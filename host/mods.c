/* Mods, in the layout emulators use, so RomFS mods made for them work as they are:
     <mods>/<name>/romfs/...          files that replace (or add to) the game's RomFS
   <mods> is the "mods" folder beside the executable, or HOS_MODS=<dir>. A mod may sit deeper, as emulators keep them
   (<mods>/contents/<title id>/<name>/romfs): any folder holding a romfs or exefs folder is a mod. Folders whose names
   start with '-' or '_' are left out (off). When two mods have the same file, the one later in longshot_mod_order.ini wins;
   without a saved order, names sort alphabetically. The button prompts' entries are merged into the winning mod's ResourceSizeTable
   (prompts.c), so added assets and prompt files still fit.
   Every mod file is indexed once at the start (no disk lookups per open); nn_fs.c asks hos_mods_romfs for each rom:
   path and lists a folder's mod-only entries with the game's own (hos_mods_dir_extras). HOS_FS_OVERLAY (nn_fs.c) is
   looked at after this, so a test overlay wins over mods.
   exefs patches (.pchtxt, .ips) are not applied: the code is compiled ahead of time, and the game folder has no module map
   (image.json) to place data patches with. A mod's exefs folder is logged as left out.
   From Skybound's loader (NotRexed/SkyboundTotk a64-kit/hos/mods.c, MIT): its RomFS part. */
#include "hos.h"
#include "hos_paths.h"
#include "mods_menu.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <limits.h>
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

typedef struct { char *key; char *host; uint64_t size; int is_dir, logged; } Entry;   /* key: romfs path, lower case, '/' */
static Entry *table;
static size_t table_cap, table_n;
static uint64_t hash_str(const char *s) {
    uint64_t h = 0xcbf29ce484222325ull;
    for (; *s; s++) h = (h ^ (uint8_t)*s) * 0x100000001b3ull;
    return h;
}
static void lower_slash(char *s) {
    for (; *s; s++) *s = *s == '\\' ? '/' : (char)tolower((unsigned char)*s);
}
static const char *last_sep(const char *s) {   /* (a host path: '\' on Windows, '/' from host/posix) */
    const char *b = strrchr(s, '\\'), *f = strrchr(s, '/');
    return b > f ? b : f;
}
static Entry *find(const char *key) {
    if (!table_cap) return NULL;
    for (size_t i = hash_str(key) & (table_cap - 1);; i = (i + 1) & (table_cap - 1)) {
        if (!table[i].key) return NULL;
        if (!strcmp(table[i].key, key)) return &table[i];
    }
}
static void put(const char *key, const char *host, uint64_t size, int is_dir) {
    if ((table_n + 1) * 2 > table_cap) {   /* (grow: rehash) */
        Entry *old = table;
        const size_t old_cap = table_cap;
        table_cap = table_cap ? table_cap * 2 : 1024;
        table = (Entry *)calloc(table_cap, sizeof *table);
        table_n = 0;
        for (size_t i = 0; i < old_cap; i++)
            if (old[i].key) {
                size_t j = hash_str(old[i].key) & (table_cap - 1);
                while (table[j].key) j = (j + 1) & (table_cap - 1);
                table[j] = old[i];
                table_n++;
            }
        free(old);
    }
    size_t i = hash_str(key) & (table_cap - 1);
    while (table[i].key && strcmp(table[i].key, key)) i = (i + 1) & (table_cap - 1);
    if (!table[i].key) table[i].key = _strdup(key), table_n++;
    else free(table[i].host);
    table[i].host = _strdup(host);
    table[i].size = size;
    table[i].is_dir = is_dir;
}

/* a mod's romfs folder, recursively: rel is the path inside the RomFS so far ("" at its root) */
static int index_romfs(const char *host_dir, const char *rel, int depth) {
    if (depth > 32) return 0;   /* (longshot: no RomFS is this deep) */
    if (!depth) {   /* (longshot) the romfs folder itself a link (to / in a mod's archive): the walk covered the whole disk */
        const DWORD at = GetFileAttributesA(host_dir);
        if (at != INVALID_FILE_ATTRIBUTES && (at & FILE_ATTRIBUTE_REPARSE_POINT)) {
            hos_log("mods: %s is a link to a folder: left out", host_dir);
            return 0;
        }
    }
    char pat[MAX_PATH];
    snprintf(pat, sizeof pat, "%s\\*", host_dir);
    WIN32_FIND_DATAA fd;
    HANDLE f = FindFirstFileA(pat, &fd);
    if (f == INVALID_HANDLE_VALUE) return 0;
    int files = 0;
    do {
        if (!strcmp(fd.cFileName, ".") || !strcmp(fd.cFileName, "..")) continue;
        char host[MAX_PATH], key[MAX_PATH];
        snprintf(host, sizeof host, "%s\\%s", host_dir, fd.cFileName);
        /* (longshot) a link to a folder is not followed (two links back up made the walk endless); links to files are
           (mod managers place files as links) */
        if ((fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) && (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) {
            hos_log("mods: %s is a link to a folder: left out", host);
            continue;
        }
        snprintf(key, sizeof key, "%s%s%s", rel, *rel ? "/" : "", fd.cFileName);
        lower_slash(key);
        const int is_dir = (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
        put(key, host, ((uint64_t)fd.nFileSizeHigh << 32) | fd.nFileSizeLow, is_dir);
        if (is_dir) {
            char sub_rel[MAX_PATH];
            snprintf(sub_rel, sizeof sub_rel, "%s%s%s", rel, *rel ? "/" : "", fd.cFileName);
            files += index_romfs(host, sub_rel, depth + 1);
        } else {
            files++;
        }
    } while (FindNextFileA(f, &fd));
    FindClose(f);
    return files;
}

/* the mod folders under root, sorted (a mod: a folder holding romfs or exefs) */
typedef struct {
    char path[MAX_PATH];
    char name[MAX_PATH];
    int has_romfs, has_exefs;
    int romfs_enabled, romfs_started;
    int load_order, started_order;
} Mod;
static Mod *mods;
static int nmods, cap_mods;
static char mod_options_path[MAX_PATH];
static char mod_order_path[MAX_PATH];

static int mod_config_path(char *path, size_t cap) {
    DWORD n = hos_game_path_a(path, (DWORD)cap);
    if (!n || n >= cap) return 0;
    char *slash = (char *)last_sep(path);
    if (!slash) return 0;
    slash[1] = 0;
    const size_t used = strlen(path);
    if (used + sizeof("longshot_mods.ini") > cap) return 0;
    memcpy(path + used, "longshot_mods.ini", sizeof("longshot_mods.ini"));
    if (GetFileAttributesA(path) == INVALID_FILE_ATTRIBUTES) {
        char legacy[MAX_PATH];
        if (used + sizeof("hookshot_mods.ini") > sizeof legacy) return 0;
        memcpy(legacy, path, used);
        memcpy(legacy + used, "hookshot_mods.ini", sizeof("hookshot_mods.ini"));
        if (GetFileAttributesA(legacy) != INVALID_FILE_ATTRIBUTES)
            memcpy(path + used, "hookshot_mods.ini", sizeof("hookshot_mods.ini"));
    }
    return 1;
}

static int mod_order_config_path(char *path, size_t cap) {
    DWORD n = hos_game_path_a(path, (DWORD)cap);
    if (!n || n >= cap) return 0;
    char *slash = (char *)last_sep(path);
    if (!slash) return 0;
    slash[1] = 0;
    const size_t used = strlen(path);
    if (used + sizeof("longshot_mod_order.ini") > cap) return 0;
    memcpy(path + used, "longshot_mod_order.ini", sizeof("longshot_mod_order.ini"));
    return 1;
}

static void load_mod_order(void) {
    if (!*mod_order_path) return;
    FILE *f = fopen(mod_order_path, "rb");
    if (!f) return;
    char line[MAX_PATH];
    int order = 0;
    while (fgets(line, sizeof line, f)) {
        char *end = line + strlen(line);
        while (end > line && (end[-1] == '\r' || end[-1] == '\n')) *--end = 0;
        if (!*line) continue;
        for (int i = 0; i < nmods; i++) {
            if (mods[i].load_order == INT_MAX && !_stricmp(mods[i].name, line)) {
                mods[i].load_order = order++;
                break;
            }
        }
    }
    fclose(f);
}

static int write_mod_order(void) {
    if (!*mod_order_path) return 0;
    char tmp[MAX_PATH];
    if (snprintf(tmp, sizeof tmp, "%s.tmp", mod_order_path) >= (int)sizeof tmp) return 0;
    FILE *f = fopen(tmp, "wb");
    if (!f) return 0;
    int ok = 1;
    for (int i = 0; i < nmods; i++)
        if (fprintf(f, "%s\n", mods[i].name) < 0) ok = 0;
    if (fclose(f)) ok = 0;
    if (!ok || !MoveFileExA(tmp, mod_order_path, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        DeleteFileA(tmp);
        return 0;
    }
    return 1;
}

static int mod_enabled_from_file(const char *name) {
    if (!*mod_options_path) return 1;
    FILE *f = fopen(mod_options_path, "rb");
    if (!f) return 1;
    char line[MAX_PATH + 32];
    int enabled = 1;
    while (fgets(line, sizeof line, f)) {
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq++ = 0;
        char *end = line + strlen(line);
        while (end > line && (end[-1] == '\r' || end[-1] == '\n' || end[-1] == ' ' || end[-1] == '\t')) *--end = 0;
        char *value = eq;
        while (*value == ' ' || *value == '\t') value++;
        if (!_stricmp(line, name)) {
            enabled = atoi(value) == 1;
            break;
        }
    }
    fclose(f);
    return enabled;
}

static int write_mod_options(void) {
    if (!*mod_options_path) return 0;
    char tmp[MAX_PATH];
    if (snprintf(tmp, sizeof tmp, "%s.tmp", mod_options_path) >= (int)sizeof tmp) return 0;
    FILE *f = fopen(tmp, "wb");
    if (!f) return 0;
    int ok = 1;
    for (int i = 0; i < nmods; i++) {
        if (!mods[i].has_romfs || mods[i].has_exefs) continue;
        if (fprintf(f, "%s=%d\n", mods[i].name, mods[i].romfs_enabled ? 1 : 0) < 0) ok = 0;
    }
    if (fclose(f)) ok = 0;
    if (!ok || !MoveFileExA(tmp, mod_options_path, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        DeleteFileA(tmp);
        return 0;
    }
    return 1;
}

static void add_mod(const char *dir, const char *name, int has_romfs, int has_exefs, int enabled) {
    if (nmods == cap_mods) mods = (Mod *)realloc(mods, sizeof *mods * (size_t)(cap_mods = cap_mods ? cap_mods * 2 : 16));
    snprintf(mods[nmods].path, MAX_PATH, "%s", dir);
    snprintf(mods[nmods].name, MAX_PATH, "%s", name);
    mods[nmods].has_romfs = has_romfs;
    mods[nmods].has_exefs = has_exefs;
    mods[nmods].romfs_enabled = enabled;
    mods[nmods].romfs_started = enabled;
    mods[nmods].load_order = INT_MAX;
    mods[nmods].started_order = INT_MAX;
    nmods++;
}

static void find_mods(const char *dir, const char *name, int depth) {
    char probe[MAX_PATH];
    snprintf(probe, sizeof probe, "%s\\romfs", dir);
    const int has_romfs = GetFileAttributesA(probe) != INVALID_FILE_ATTRIBUTES;
    snprintf(probe, sizeof probe, "%s\\exefs", dir);
    const int has_exefs = GetFileAttributesA(probe) != INVALID_FILE_ATTRIBUTES;
    if ((has_romfs || has_exefs) && depth > 0) {
        const int enabled = !has_romfs || has_exefs ? 1 : mod_enabled_from_file(name);
        add_mod(dir, name, has_romfs, has_exefs, enabled);
        return;
    }
    if (depth >= 4) return;
    char pat[MAX_PATH];
    snprintf(pat, sizeof pat, "%s\\*", dir);
    WIN32_FIND_DATAA fd;
    HANDLE f = FindFirstFileA(pat, &fd);
    if (f == INVALID_HANDLE_VALUE) return;
    do {
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || fd.cFileName[0] == '.') continue;
        if (fd.cFileName[0] == '-' || fd.cFileName[0] == '_') {
            hos_log("mods: %s\\%s is off (its name starts with %c)", dir, fd.cFileName, fd.cFileName[0]);
            continue;
        }
        char sub[MAX_PATH], sub_name[MAX_PATH];
        snprintf(sub, sizeof sub, "%s\\%s", dir, fd.cFileName);
        snprintf(sub_name, sizeof sub_name, "%s%s%s", name, *name ? "/" : "", fd.cFileName);
        find_mods(sub, sub_name, depth + 1);
    } while (FindNextFileA(f, &fd));
    FindClose(f);
}
static int mod_cmp(const void *a, const void *b) {
    const Mod *left = (const Mod *)a, *right = (const Mod *)b;
    if (left->load_order != right->load_order) return left->load_order < right->load_order ? -1 : 1;
    return _stricmp(left->name, right->name);
}

/* hos_main.c, once the image is loaded */
void hos_mods_init(const char *image_path) {
    (void)image_path;
    /* Keep disabled settings beside the portable executable, never in AppData. */
    mod_config_path(mod_options_path, sizeof mod_options_path);
    mod_order_config_path(mod_order_path, sizeof mod_order_path);
    char root[MAX_PATH];
    if (getenv("HOS_MODS")) {
        snprintf(root, sizeof root, "%s", getenv("HOS_MODS"));
    } else {
        hos_game_path_a(root, sizeof root);
        char *slash = (char *)last_sep(root);
        if (!slash) return;
        snprintf(slash + 1, sizeof root - (size_t)(slash + 1 - root), "mods");
    }
    if (GetFileAttributesA(root) == INVALID_FILE_ATTRIBUTES) return;
    find_mods(root, "", 0);
    if (!nmods) {
        hos_log("mods: none in %s (a mod: <name>\\romfs)", root);
        return;
    }
    load_mod_order();
    qsort(mods, (size_t)nmods, sizeof *mods, mod_cmp);
    for (int i = 0; i < nmods; i++) mods[i].started_order = mods[i].load_order;
    for (int i = 0; i < nmods; i++) {
        char dir[MAX_PATH];
        snprintf(dir, sizeof dir, "%s\\romfs", mods[i].path);
        const int files = mods[i].has_romfs && mods[i].romfs_enabled ? index_romfs(dir, "", 0) : 0;
        snprintf(dir, sizeof dir, "%s\\exefs", mods[i].path);
        if (GetFileAttributesA(dir) != INVALID_FILE_ATTRIBUTES)
            hos_log("mods: %s: exefs patches are left out (the code is compiled ahead of time)", mods[i].name);
        hos_log("mods: %s: %d RomFS files", mods[i].name, files);
    }
    hos_log("mods: %d mods from %s: %zu RomFS entries", nmods, root, table_n);
}

/* nn_fs.c: the mod file for a RomFS path ("UI/x.zs", no mount, no leading slash), or 0 */
int hos_mods_romfs(const char *rel, char *out, size_t cap) {
    char key[MAX_PATH];
    snprintf(key, sizeof key, "%s", rel);
    lower_slash(key);
    int hos_prompts_romfs(const char *key, const char *mod, char *out, size_t cap);   /* prompts.c: the button prompts */
    Entry *e = table_n ? find(key) : NULL;
    if (!e || e->is_dir) return hos_prompts_romfs(key, NULL, out, cap);
    const int merged = hos_prompts_romfs(key, e->host, out, cap);   /* (the size table: the prompts' entries merged in) */
    if (!merged) snprintf(out, cap, "%s", e->host);
    static volatile LONG logged;
    if (!e->logged && InterlockedIncrement(&logged) <= 200)
        hos_log("mods: %s <- %s%s", rel, e->host, merged ? " (with the button prompts' sizes merged in)" : "");
    e->logged = 1;
    return 1;
}

/* prompts.c: the mod file for this exact RomFS path, before the prompts' replacements */
int hos_mods_file(const char *rel, char *out, size_t cap) {
    char key[MAX_PATH];
    snprintf(key, sizeof key, "%s", rel);
    lower_slash(key);
    const Entry *e = table_n ? find(key) : NULL;
    if (!e || e->is_dir) return 0;
    snprintf(out, cap, "%s", e->host);
    return 1;
}

/* nn_fs.c, listing a RomFS folder: fn(arg, name, is_dir, size) for each mod entry directly in it that the game's own
   folder (base_dir, a host path; may not exist) does not have. Returns how many. */
int hos_mods_dir_extras(const char *rel, const char *base_dir, void (*fn)(void *, const char *, int, uint64_t),
                        void *arg) {
    if (!table_n) return 0;
    char prefix[MAX_PATH];
    snprintf(prefix, sizeof prefix, "%s", rel);
    lower_slash(prefix);
    size_t pn = strlen(prefix);
    while (pn && prefix[pn - 1] == '/') prefix[--pn] = 0;
    int n = 0;
    for (size_t i = 0; i < table_cap; i++) {
        const Entry *e = &table[i];
        if (!e->key) continue;
        const char *k = e->key;
        if (pn && (strncmp(k, prefix, pn) || k[pn] != '/')) continue;
        const char *name = pn ? k + pn + 1 : k;
        if (!*name || strchr(name, '/')) continue;   /* (deeper) */
        const char *real = last_sep(e->host);   /* (the name as the mod spells it) */
        real = real ? real + 1 : e->host;
        char base[MAX_PATH];
        snprintf(base, sizeof base, "%s\\%s", base_dir, real);
        if (GetFileAttributesA(base) != INVALID_FILE_ATTRIBUTES) continue;
        fn(arg, real, e->is_dir, e->size);
        n++;
    }
    return n;
}

/* The F1 Mods tab lists all discovered user mods. ExeFS-bearing mods are locked because their code is baked into the
   executable; a mixed mod's RomFS therefore stays enabled with its compiled ExeFS patches. */
static Mod *menu_mod_at(int index) {
    if (index < 0) return NULL;
    /* Keep the alphabetical load/overlay order intact; only group ExeFS mods last in the menu. */
    for (int exefs = 0; exefs <= 1; exefs++) {
        for (int i = 0; i < nmods; i++) {
            if ((!mods[i].has_romfs && !mods[i].has_exefs) || mods[i].has_exefs != exefs) continue;
            if (!index--) return &mods[i];
        }
    }
    return NULL;
}

int hos_mods_romfs_menu_count(void) {
    int count = 0;
    for (int i = 0; i < nmods; i++)
        if (mods[i].has_romfs || mods[i].has_exefs) count++;
    return count;
}

int hos_mods_romfs_menu_get(int index, char *name, size_t cap, int *enabled, int *has_exefs) {
    Mod *mod = menu_mod_at(index);
    if (!mod) return 0;
    if (name && cap) snprintf(name, cap, "%s", mod->name);
    if (enabled) *enabled = mod->romfs_enabled;
    if (has_exefs) *has_exefs = mod->has_exefs;
    return 1;
}

int hos_mods_romfs_menu_reorder(int from, int to) {
    if (from == to) return menu_mod_at(from) != NULL;
    Mod *source = menu_mod_at(from), *target = menu_mod_at(to);
    if (!source || !target || source->has_exefs || target->has_exefs || nmods < 2) return 0;
    const int visible_count = hos_mods_romfs_menu_count();
    Mod **display = (Mod **)malloc((size_t)visible_count * sizeof *display);
    Mod *original = (Mod *)malloc((size_t)nmods * sizeof *original);
    if (!display || !original) { free(display); free(original); return 0; }
    memcpy(original, mods, (size_t)nmods * sizeof *original);
    for (int i = 0; i < visible_count; i++)
        display[i] = menu_mod_at(i);
    Mod *moving = display[from];
    if (from < to) memmove(&display[from], &display[from + 1], (size_t)(to - from) * sizeof *display);
    else memmove(&display[to + 1], &display[to], (size_t)(from - to) * sizeof *display);
    display[to] = moving;
    for (int i = 0; i < visible_count; i++) display[i]->load_order = i;
    qsort(mods, (size_t)nmods, sizeof *mods, mod_cmp);
    const int saved = write_mod_order();
    if (!saved) {
        memcpy(mods, original, (size_t)nmods * sizeof *mods);
        qsort(mods, (size_t)nmods, sizeof *mods, mod_cmp);
    }
    free(display); free(original);
    return saved;
}

int hos_mods_romfs_menu_set(int index, int enabled) {
    Mod *mod = menu_mod_at(index);
    if (!mod || mod->has_exefs || !mod->has_romfs) return 0;
    enabled = !!enabled;
    if (mod->romfs_enabled == enabled) return 1;
    const int was = mod->romfs_enabled;
    mod->romfs_enabled = enabled;
    if (!write_mod_options()) {
        mod->romfs_enabled = was;
        return 0;
    }
    hos_log("mods: %s RomFS %s (restart to apply)", mod->name, enabled ? "enabled" : "disabled");
    return 1;
}

int hos_mods_romfs_menu_changed(void) {
    for (int i = 0; i < nmods; i++)
        if ((mods[i].has_romfs && !mods[i].has_exefs && mods[i].romfs_enabled != mods[i].romfs_started) ||
            mods[i].load_order != mods[i].started_order) return 1;
    return 0;
}
