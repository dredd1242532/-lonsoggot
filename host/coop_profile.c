/* Temporary saves are a restart-time mount choice, never a restore over a player's
   save. Only locally generated IDs select storage. See docs/coop.md. */
#ifndef _CRT_SECURE_NO_WARNINGS
#define _CRT_SECURE_NO_WARNINGS
#endif
#include "coop_profile.h"
#include "coop_proto.h"
#include "coop_net.h"
#include "../third_party/monocypher/monocypher.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
#endif

static char normal_root[COOP_PROFILE_PATH], selected_root[COOP_PROFILE_PATH];
static char store[COOP_PROFILE_PATH], pending[33], active_id[33], note[160];
#ifdef _WIN32
static HANDLE lease = INVALID_HANDLE_VALUE;
static const wchar_t *wide(const char *s, wchar_t w[COOP_PROFILE_PATH + 8]) {
    wchar_t raw[COOP_PROFILE_PATH], full[COOP_PROFILE_PATH];
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s, -1, raw, COOP_PROFILE_PATH)) return L"";
    for (wchar_t *p = raw; *p; p++) if (*p == L'/') *p = L'\\';
    DWORD n = GetFullPathNameW(raw, COOP_PROFILE_PATH, full, NULL);
    if (!n || n >= COOP_PROFILE_PATH) return L"";
    if (!wcsncmp(full, L"\\\\?\\", 4)) wcscpy(w, full);
    else if (!wcsncmp(full, L"\\\\", 2)) swprintf(w, COOP_PROFILE_PATH + 8, L"\\\\?\\UNC\\%ls", full + 2);
    else swprintf(w, COOP_PROFILE_PATH + 8, L"\\\\?\\%ls", full);
    return w;
}
#define W(s) wide((s), (wchar_t[COOP_PROFILE_PATH + 8]){0})
static int kind(const char *p) {
    DWORD a = GetFileAttributesW(W(p));
    if (a == INVALID_FILE_ATTRIBUTES) return GetLastError() == ERROR_FILE_NOT_FOUND || GetLastError() == ERROR_PATH_NOT_FOUND ? 0 : -1;
    if (a & FILE_ATTRIBUTE_REPARSE_POINT) return -1;
    return a & FILE_ATTRIBUTE_DIRECTORY ? 2 : 1;
}
static int mkdir_one(const char *p) { return CreateDirectoryW(W(p), NULL) != 0; }
static int move_new(const char *a, const char *b) { return MoveFileExW(W(a), W(b), MOVEFILE_WRITE_THROUGH) != 0; }
static void unlink_one(const char *p) { DeleteFileW(W(p)); }
#else
static int lease = -1;
static int kind(const char *p) {
    struct stat s;
    if (lstat(p, &s)) return errno == ENOENT ? 0 : -1;
    if (S_ISDIR(s.st_mode)) return 2;
    return S_ISREG(s.st_mode) && s.st_nlink == 1 ? 1 : -1;
}
static int mkdir_one(const char *p) { return mkdir(p, 0700) == 0; }
static int move_new(const char *a, const char *b) {
    /* Linux's non-replacing rename: no check-then-overwrite on an existing copy. */
    return syscall(SYS_renameat2, AT_FDCWD, a, AT_FDCWD, b, 1u) == 0;
}
static void unlink_one(const char *p) { unlink(p); }
#endif

static int path(char out[COOP_PROFILE_PATH], const char *a, const char *b) {
    return snprintf(out, COOP_PROFILE_PATH, "%s/%s", a, b) < COOP_PROFILE_PATH;
}
static int id_ok(const char *id) {
    if (!id || strlen(id) != 32) return 0;
    for (int i = 0; i < 32; i++) if (!((id[i] >= '0' && id[i] <= '9') || (id[i] >= 'a' && id[i] <= 'f'))) return 0;
    return 1;
}
static int random_id(char id[33]) {
    uint8_t b[16];
    if (!coop_random(b, sizeof b)) return 0;
    for (int i = 0; i < 16; i++) snprintf(id + i * 2, 3, "%02x", b[i]);
    return 1;
}
int coop_profile_path_safe(const char *s) {
    char p[COOP_PROFILE_PATH];
    if (!s || snprintf(p, sizeof p, "%s", s) >= (int)sizeof p) return 0;
#ifndef _WIN32
    for (char *c = p; *c; c++) if (*c == '\\') *c = '/';
#endif
    for (char *c = p; ; c++) {
        if (*c && *c != '/' && *c != '\\') continue;
        const char saved = *c;
        *c = 0;
        /* Drive and UNC prefixes are checked once a complete component exists. */
        if (c > p && !(c == p + 2 && p[1] == ':') && kind(p) < 0) return 0;
        *c = saved;
        if (!saved) return 1;
    }
}

/* One exclusive, no-link file, checked after open as well. Flush errors are errors.
   This is intentionally not stdio: Windows share=0 refuses an active writer. */
static int io(const char *p, int write, int create, uint32_t off, void *buf, size_t n) {
    if (n > COOP_PROFILE_BYTES || !coop_profile_path_safe(p)) return 0;
#ifdef _WIN32
    HANDLE h = CreateFileW(W(p), write ? GENERIC_WRITE : GENERIC_READ, 0, NULL,
                          create ? CREATE_NEW : OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    BY_HANDLE_FILE_INFORMATION info;
    int ok = GetFileInformationByHandle(h, &info) && !(info.dwFileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY)) && info.nNumberOfLinks == 1;
    LARGE_INTEGER pos; pos.QuadPart = off;
    DWORD done = 0;
    ok = ok && SetFilePointerEx(h, pos, NULL, FILE_BEGIN) &&
         (write ? WriteFile(h, buf, (DWORD)n, &done, NULL) : ReadFile(h, buf, (DWORD)n, &done, NULL)) && done == n;
    if (write && ok) ok = FlushFileBuffers(h) != 0;
    CloseHandle(h);
#else
    int h = open(p, (write ? O_WRONLY : O_RDONLY) | O_NOFOLLOW | O_CLOEXEC | (create ? O_CREAT | O_EXCL : 0), 0600);
    if (h < 0) return 0;
    struct stat st;
    int ok = !fstat(h, &st) && S_ISREG(st.st_mode) && st.st_nlink == 1;
    size_t done = 0;
    while (ok && done < n) {
        ssize_t k = write ? pwrite(h, (char *)buf + done, n - done, off + done) : pread(h, (char *)buf + done, n - done, off + done);
        if (k < 0 && errno == EINTR) continue;
        if (k <= 0) ok = 0;
        else done += (size_t)k;
    }
    if (write && ok) ok = !fsync(h);
    if (close(h)) ok = 0;
#endif
    return ok;
}

/* A bounded, sorted inventory makes the checkpoint digest independent of directory
   enumeration order. Only local names enter it; no remote name is ever accepted. */
typedef struct { char rel[256]; uint32_t size; } Entry;
typedef struct { Entry e[4096]; unsigned n; uint64_t bytes; } Inventory;
static int inventory(Inventory *v, const char *base, const char *rel, unsigned depth) {
    char dir[COOP_PROFILE_PATH];
    if (depth > 8 || !path(dir, base, rel) || !coop_profile_path_safe(dir) || kind(dir) != 2) return 0;
#ifdef _WIN32
    char pat[COOP_PROFILE_PATH];
    if (!path(pat, dir, "*")) return 0;
    WIN32_FIND_DATAW data;
    HANDLE f = FindFirstFileW(W(pat), &data);
    if (f == INVALID_HANDLE_VALUE) return GetLastError() == ERROR_FILE_NOT_FOUND;
#else
    DIR *f = opendir(dir);
    if (!f) return 0;
    struct dirent *data;
#endif
    int ok = 1;
    for (;;) {
        char name[256];
        uint64_t size = 0;
#ifdef _WIN32
        if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, data.cFileName, -1, name, sizeof name, NULL, NULL)) { ok = 0; break; }
        size = (uint64_t)data.nFileSizeHigh << 32 | data.nFileSizeLow;
#else
        errno = 0;
        data = readdir(f);
        if (!data) { if (errno) ok = 0; break; }
        if (snprintf(name, sizeof name, "%s", data->d_name) >= (int)sizeof name) { ok = 0; break; }
#endif
        if (strcmp(name, ".") && strcmp(name, "..")) {
            char next[256], full[COOP_PROFILE_PATH];
            if (strchr(name, '\\') || strchr(name, ':') || snprintf(next, sizeof next, "%s%s%s", rel, *rel ? "/" : "", name) >= (int)sizeof next || !path(full, base, next)) { ok = 0; break; }
            int k = kind(full);
            if (k == 2) {
                if (v->n == 4096) ok = 0;
                else {
                    Entry *e = &v->e[v->n++]; strcpy(e->rel, next); e->size = UINT32_MAX;
                    ok = inventory(v, base, next, depth + 1);
                }
            }
            else if (k != 1) ok = 0;
            else {
#ifndef _WIN32
                struct stat st;
                if (lstat(full, &st) || st.st_size < 0) { ok = 0; break; }
                size = (uint64_t)st.st_size;
#endif
                if (v->n == 4096 || size > COOP_PROFILE_BYTES || v->bytes + size > COOP_PROFILE_BYTES) ok = 0;
                else { Entry *e = &v->e[v->n++]; strcpy(e->rel, next); e->size = (uint32_t)size; v->bytes += size; }
            }
            if (!ok) break;
        }
#ifdef _WIN32
        if (!FindNextFileW(f, &data)) { if (GetLastError() != ERROR_NO_MORE_FILES) ok = 0; break; }
#endif
    }
#ifdef _WIN32
    FindClose(f);
#else
    closedir(f);
#endif
    return ok;
}
static int compare_entry(const void *a, const void *b) { return strcmp(((const Entry *)a)->rel, ((const Entry *)b)->rel); }
static int mkdir_parents(char *p, const char *base) {
    for (char *c = p + strlen(base) + 1; *c; c++) if (*c == '/') {
        *c = 0;
        int ok = coop_profile_path_safe(p) && (kind(p) == 2 || mkdir_one(p));
        *c = '/';
        if (!ok) return 0;
    }
    return 1;
}
static int tree(const char *src, const char *dst, uint8_t digest[32]) {
    Inventory *v = calloc(1, sizeof *v);
    if (!v) return 0;
    int ok = inventory(v, src, "", 0);
    qsort(v->e, v->n, sizeof v->e[0], compare_entry);
    crypto_blake2b_ctx hash;
    crypto_blake2b_init(&hash, 32);
    uint8_t buf[8192];
    for (unsigned i = 0; ok && i < v->n; i++) {
        Entry *e = &v->e[i];
        char a[COOP_PROFILE_PATH], b[COOP_PROFILE_PATH];
        uint8_t len[4];
        for (unsigned j = 0; j < 4; j++) len[j] = (uint8_t)(e->size >> (j * 8));
        crypto_blake2b_update(&hash, (const uint8_t *)e->rel, strlen(e->rel) + 1);
        crypto_blake2b_update(&hash, len, 4);
        ok = path(a, src, e->rel);
        if (e->size == UINT32_MAX) {
            if (dst) ok = ok && path(b, dst, e->rel) && mkdir_parents(b, dst) && mkdir_one(b);
            continue;
        }
        if (dst) ok = ok && path(b, dst, e->rel) && mkdir_parents(b, dst) && io(b, 1, 1, 0, buf, 0);
        for (uint32_t off = 0; ok && off < e->size;) {
            size_t n = e->size - off; if (n > sizeof buf) n = sizeof buf;
            ok = io(a, 0, 0, off, buf, n) && (!dst || io(b, 1, 0, off, buf, n));
            if (ok) crypto_blake2b_update(&hash, buf, n);
            off += (uint32_t)n;
        }
    }
    crypto_blake2b_final(&hash, digest);
    free(v);
    return ok;
}

static int store_ready(void) {
    return store[0] && coop_profile_path_safe(store) && (kind(store) == 2 || mkdir_one(store));
}
static int quota(uint64_t extra) {
    /* Includes abandoned partials: disk pressure never silently deletes a copy. */
    Inventory *v = calloc(1, sizeof *v);
    if (!v) return 0;
    int ok = inventory(v, store, "", 0);
    unsigned count = 0;
    char prior[33] = "";
    qsort(v->e, v->n, sizeof v->e[0], compare_entry);
    for (unsigned i = 0; i < v->n; i++) if (strlen(v->e[i].rel) >= 32 && (v->e[i].rel[32] == '/' || !v->e[i].rel[32]) && strncmp(prior, v->e[i].rel, 32)) {
        memcpy(prior, v->e[i].rel, 32); prior[32] = 0; count++;
    }
    ok = ok && count < COOP_PROFILE_LIMIT && v->bytes + extra <= COOP_PROFILE_BYTES;
    free(v);
    return ok;
}
const char *coop_profile_root(void) { return selected_root[0] ? selected_root : normal_root; }
int coop_profile_active(void) { return active_id[0] != 0; }
const char *coop_profile_note(void) { return note; }
const char *coop_profile_restart_token(void) { return pending; }

int coop_profile_init(const char *normal, const char *token) {
    pending[0] = active_id[0] = selected_root[0] = note[0] = 0;
    if (!normal || snprintf(normal_root, sizeof normal_root, "%s", normal) >= (int)sizeof normal_root ||
        !path(store, normal_root, "coop-sessions")) return 0;
    if (!token || !*token) {
        if (kind(store) == 2) snprintf(note, sizeof note, "Normal saves selected. Temporary copies, including interrupted sessions, are kept separately.");
        return 1;
    }
    if (!id_ok(token) || !coop_profile_path_safe(store)) return 0;
    char dir[COOP_PROFILE_PATH], req[COOP_PROFILE_PATH], used[COOP_PROFILE_PATH], root[COOP_PROFILE_PATH], saves[COOP_PROFILE_PATH], lock[COOP_PROFILE_PATH];
    uint8_t want[32], got[32];
    if (!path(dir, store, token) || !path(req, dir, "restart.ticket") || !path(used, dir, "restart.used") ||
        !path(root, dir, "user") || !path(saves, root, "save") || !path(lock, dir, "active.lock") ||
        !io(req, 0, 0, 0, want, sizeof want) || !move_new(req, used) || !tree(saves, NULL, got) || memcmp(want, got, 32)) return 0;
#ifdef _WIN32
    lease = CreateFileW(W(lock), GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_ALWAYS, FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (lease == INVALID_HANDLE_VALUE) return 0;
#else
    lease = open(lock, O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (lease < 0 || flock(lease, LOCK_EX | LOCK_NB)) { if (lease >= 0) close(lease); lease = -1; return 0; }
#endif
    strcpy(selected_root, root); strcpy(active_id, token);
    snprintf(note, sizeof note, "Temporary copy active. Your normal saves are untouched; no progress is returned.");
    return 1;
}

int coop_profile_prepare(void) {
    if (coop_profile_active() || pending[0] || !store_ready()) return 0;
    char id[33], dir[COOP_PROFILE_PATH], src[COOP_PROFILE_PATH], original[COOP_PROFILE_PATH], user[COOP_PROFILE_PATH], dst[COOP_PROFILE_PATH], req[COOP_PROFILE_PATH];
    uint8_t digest[32], verify[32];
    Inventory *v = calloc(1, sizeof *v);
    if (!v) return 0;
    int room = path(src, normal_root, "save") && inventory(v, src, "", 0) && quota(v->bytes * 2 + 32);
    free(v);
    if (!room) return 0;
    if (!random_id(id) || !path(dir, store, id) || !path(src, normal_root, "save") || !path(original, dir, "original") ||
        !path(user, dir, "user") || !path(dst, user, "save") || !path(req, dir, "restart.ticket") ||
        !coop_profile_path_safe(src) || kind(src) != 2 || !mkdir_one(dir) || !mkdir_one(original) ||
        !tree(src, original, digest) || !mkdir_one(user) || !mkdir_one(dst) || !tree(original, dst, verify) ||
        memcmp(digest, verify, 32) || !io(req, 1, 1, 0, digest, sizeof digest)) {
        snprintf(note, sizeof note, "Copy failed; normal saves remain selected and unchanged. Incomplete copies are not mountable.");
        return 0;
    }
    strcpy(pending, id);
    snprintf(note, sizeof note, "Copy ready. Restarting selects it; the normal save folder is not replaced.");
    return 1;
}
int coop_profile_return(void) {
    if (!coop_profile_active()) return 0;
    pending[0] = 0; /* No token: restart/ordinary launch selects the unchanged normal root. */
    snprintf(note, sizeof note, "Returning to normal saves after restart. This temporary copy is kept.");
    return 1;
}
void coop_profile_restart_failed(void) {
    if (pending[0]) {
        char dir[COOP_PROFILE_PATH], req[COOP_PROFILE_PATH];
        if (path(dir, store, pending) && path(req, dir, "restart.ticket") && coop_profile_path_safe(req)) unlink_one(req);
    }
    pending[0] = 0;
}

static int transfer_path(char out[COOP_PROFILE_PATH], const char *id, const char *name) {
    char dir[COOP_PROFILE_PATH];
    return id_ok(id) && path(dir, store, id) && path(out, dir, name) && coop_profile_path_safe(out);
}
int coop_profile_stage(char id[33]) {
    char dir[COOP_PROFILE_PATH], p[COOP_PROFILE_PATH];
    if (!store_ready() || !quota(1048576) || !random_id(id) || !path(dir, store, id) || !mkdir_one(dir) ||
        !transfer_path(p, id, ".partial") || !mkdir_one(p)) return 0;
    return 1;
}
static int part_io(const char *id, unsigned file, uint32_t off, void *data, size_t n, int write) {
    char p[COOP_PROFILE_PATH];
    if (file > 1 || off > 1048576 || n > 1048576 - off || !transfer_path(p, id, file ? ".partial/caption.sav" : ".partial/progress.sav")) return 0;
    return io(p, write, write && !off, off, data, n);
}
int coop_profile_chunk(const char *id, unsigned f, uint32_t off, const void *data, size_t n) { return part_io(id, f, off, (void *)data, n, 1); }
int coop_profile_read(const char *id, unsigned f, uint32_t off, void *data, size_t n) { return part_io(id, f, off, data, n, 0); }
int coop_profile_publish(const char *id) {
    char a[COOP_PROFILE_PATH], b[COOP_PROFILE_PATH];
    return transfer_path(a, id, ".partial") && transfer_path(b, id, "verified") && kind(a) == 2 && move_new(a, b);
}
void coop_profile_abandon(const char *id) {
    char p[COOP_PROFILE_PATH];
    if (transfer_path(p, id, ".partial/progress.sav")) unlink_one(p);
    if (transfer_path(p, id, ".partial/caption.sav")) unlink_one(p);
    /* Keep the directory as a recovery record. It has no restart ticket. */
}
