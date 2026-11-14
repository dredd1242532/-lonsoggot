/* (longshot) fopen for the files the game writes by itself in its folder and the user folder (longshot_options.ini, the
   pipeline caches and lists, the program cache and its index, the markers, the button-prompt textures, mouse_sens.txt,
   language.txt, longshot_log.txt and the like), without following a symbolic link at the file's name. A game folder
   unpacked from an archive someone shared can hold such a link (tar and unzip keep them on Linux): a plain fopen("w") then
   emptied and rewrote the file it named, anywhere the player can write.
   Linux: O_NOFOLLOW, and only a regular file (not a FIFO or a device). Windows: the name is opened as itself
   (FILE_FLAG_OPEN_REPARSE_POINT) and refused when it is a name surrogate (a symbolic link, a junction, WSL's and the
   container links). A file with no reparse point (the usual case) is then opened for writing as itself in one step, and
   refused if what that handle finds is a reparse point after all. One with a reparse point that is not a link (OneDrive's
   files, deduplicated or compressed ones) is opened again without the flag, so OneDrive fills it first, and kept only
   when it is the same file (volume and file index).
   mode: "w", "wb", "a", "ab", "r+" or "r+b" ("r+": an existing file only, as fopen). Used by the host (C) and the renderer
   (C++). tests/test_game_writes_no_links.py fails on a write in host/ or gpu/vk/ that does not come through here.
   hos_fopen_read_nofollow reads by the same rule: the bug report's copies of the logs, which the player sends.
   Not covered, on purpose: a link at a folder on the way to the file (the game folder, user/, bug-reports, prompts) is
   followed, as a linked game or user folder and OneDrive need; only the file's own name is checked. A hard link is not
   told apart from the file itself (an archive can only hard-link within itself, and refusing them would refuse
   deduplicated or hard-link-copied folders). */
#pragma once
#include <stdio.h>
#include <string.h>
#include "hos_fopen_read.h"   /* (and the reads: hos_fopen_read, hos_fopen_read_binary) */
#include <errno.h>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <fcntl.h>
#include <io.h>
#include <stdlib.h>
#ifndef IsReparseTagNameSurrogate
#define IsReparseTagNameSurrogate(tag) (((tag) & 0x20000000UL) != 0)
#endif
#else
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

/* (a refusal is said once per file that includes this, on stderr (the game's log): a setting that "does not stick" has a reason) */
static inline void hos_fopen_write_refused(const char *path) {
    static int said;
    if (!said++) fprintf(stderr, "files: %s is a link or not a plain file: the game does not write through it\n", path);
}
#ifdef _WIN32
/* (the game's char paths are UTF-8, its manifest's code page (tests/test_utf8_paths.py)) */
static inline wchar_t *hos_fopen_wide(const char *path) {
    const int n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, NULL, 0);
    wchar_t *w = n > 0 ? (wchar_t *)malloc((size_t)n * sizeof *w) : NULL;
    if (w && MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, w, n) != n) free(w), w = NULL;
    return w;
}
/* (what is at a handle: -1 not a plain file (a folder, a device, a name surrogate, or not known), 0 a file with no reparse
   point, 1 a file with a reparse point that is not a link (OneDrive's, deduplicated, compressed)) */
static inline int hos_fopen_kind(HANDLE h) {
    FILE_ATTRIBUTE_TAG_INFO tag;
    if (GetFileType(h) != FILE_TYPE_DISK || !GetFileInformationByHandleEx(h, FileAttributeTagInfo, &tag, sizeof tag) ||
        (tag.FileAttributes & FILE_ATTRIBUTE_DIRECTORY))
        return -1;
    if (!(tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) return 0;
    return IsReparseTagNameSurrogate(tag.ReparseTag) ? -1 : 1;
}
/* (the handle to write or read w through, for access, or INVALID_HANDLE_VALUE; existing: only a file that is there, else
   one is made new where nothing is; *refused: the name is a link or not a plain file) */
static inline HANDLE hos_fopen_write_handle(const wchar_t *w, DWORD access, int existing, int *refused) {
    const DWORD share = FILE_SHARE_READ | FILE_SHARE_WRITE;   /* (as fopen's) */
    *refused = 0;
    const HANDLE look = CreateFileW(w, FILE_READ_ATTRIBUTES, share | FILE_SHARE_DELETE, NULL, OPEN_EXISTING,
                                    FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS, NULL);
    if (look == INVALID_HANDLE_VALUE) {
        if (existing || GetLastError() != ERROR_FILE_NOT_FOUND) return INVALID_HANDLE_VALUE;
        /* (nothing at the name: made new, which fails if something, a link too, was put there since) */
        return CreateFileW(w, access, share, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
    }
    const int kind = hos_fopen_kind(look);
    BY_HANDLE_FILE_INFORMATION was, is;
    HANDLE h = INVALID_HANDLE_VALUE;
    int same = 0, refuse = kind < 0;
    if (kind == 0) {
        /* (no reparse point: opened as itself once more, now for access. The flag changes nothing for such a file, and
           whatever was put at the name since is opened as itself too, not followed, and refused below. No file IDs are
           compared, so a file system that makes them up for each open (some network redirectors, FUSE mounts, a virtual
           machine's shared folders) still saves) */
        h = CreateFileW(w, access | FILE_READ_ATTRIBUTES, share, NULL, OPEN_EXISTING,
                        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, NULL);
        same = h != INVALID_HANDLE_VALUE && hos_fopen_kind(h) == 0;
    } else if (kind == 1) {
        /* (a reparse point that is not a link: opened again without the flag, so that OneDrive fills a file kept only
           online and the others are read and written as their data, and kept only if it is the file looked at. A file
           index of 0 proves nothing: refused) */
        if (GetFileInformationByHandle(look, &was) && (was.nFileIndexHigh | was.nFileIndexLow)) {
            h = CreateFileW(w, access | FILE_READ_ATTRIBUTES, share, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
            same = h != INVALID_HANDLE_VALUE && GetFileInformationByHandle(h, &is) &&
                   is.dwVolumeSerialNumber == was.dwVolumeSerialNumber && is.nFileIndexHigh == was.nFileIndexHigh &&
                   is.nFileIndexLow == was.nFileIndexLow;
        } else {
            refuse = 1;
        }
    }
    CloseHandle(look);
    if (h != INVALID_HANDLE_VALUE && !same) CloseHandle(h), h = INVALID_HANDLE_VALUE, refuse = 1;
    *refused = refuse;
    return h;
}
#endif
static inline FILE *hos_fopen_write(const char *path, const char *mode) {
    const int append = mode[0] == 'a', update = mode[0] == 'r' && strchr(mode, '+') != NULL;
    if (!append && !update && mode[0] != 'w') return NULL;
#ifdef _WIN32
    wchar_t *w = hos_fopen_wide(path);
    if (!w) return NULL;
    int refused = 0;
    HANDLE h = hos_fopen_write_handle(w, GENERIC_WRITE | (update ? GENERIC_READ : 0), update, &refused);
    free(w);
    if (h == INVALID_HANDLE_VALUE) {
        if (refused) hos_fopen_write_refused(path);
        return NULL;
    }
    if (mode[0] == 'w' && !SetEndOfFile(h)) {   /* (emptied once it is known to be the file of its own name; at its start) */
        CloseHandle(h);
        return NULL;
    }
    const int fd = _open_osfhandle((intptr_t)h, (append ? _O_APPEND : 0) | (strchr(mode, 'b') ? 0 : _O_TEXT));
    if (fd < 0) {
        CloseHandle(h);
        return NULL;
    }
    FILE *f = _fdopen(fd, mode);
    if (!f) _close(fd);
    return f;
#else
    const int fd = open(path, (update ? O_RDWR : O_WRONLY | O_CREAT) | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK | (append ? O_APPEND : 0), 0666);
    if (fd < 0) {
        if (errno == ELOOP || errno == ENXIO) hos_fopen_write_refused(path);   /* (a link; a FIFO with no reader) */
        return NULL;
    }
    struct stat st;
    /* (truncated only once it is known to be a regular file of its own name) */
    const int plain = fstat(fd, &st) == 0 && S_ISREG(st.st_mode);
    if (!plain || (mode[0] == 'w' && ftruncate(fd, 0) != 0)) {
        if (!plain) hos_fopen_write_refused(path);
        close(fd);
        return NULL;
    }
    const int flags = fcntl(fd, F_GETFL);
    if (flags != -1) fcntl(fd, F_SETFL, flags & ~O_NONBLOCK);
    FILE *f = fdopen(fd, mode);
    if (!f) close(fd);
    return f;
#endif
}
/* freopen(path, mode, stream) as hos_fopen_write opens it: longshot_log.txt as stderr. False: stream is left as it was */
static inline int hos_freopen_write(const char *path, const char *mode, FILE *stream) {
    FILE *f = hos_fopen_write(path, mode);
    if (!f) return 0;
#ifdef _WIN32
    /* (a game started from its icon has no console, and its stderr no descriptor to dup2 onto: freopen gives it one. The
       name was just checked and emptied, so this opens the same file; only something swapping it in this moment is not
       caught, which a folder unpacked from an archive cannot) */
    fclose(f);
    return freopen(path, mode, stream) != NULL;
#else
    fflush(stream);
    const int ok = dup2(fileno(f), fileno(stream)) >= 0;
    fclose(f);
    return ok;
#endif
}
/* (a refused read, said once per file that includes this, as a refused write is) */
static inline void hos_fopen_read_refused(const char *path) {
    static int said;
    if (!said++) fprintf(stderr, "files: %s is a link or not a plain file: the game does not read it\n", path);
}
/* fopen(path, "rb") for a file whose bytes leave the PC (the bug report's copies of longshot_log.txt and longshot_log.1.txt,
   which the player is asked to send): by the write's rule, not through a link at its name and only a regular file, so a
   link there cannot put another file's bytes in the report, and a FIFO is not waited on. Linux: O_NOFOLLOW and
   O_NONBLOCK, then fstat. Windows: hos_fopen_write_handle, for reading an existing file */
static inline FILE *hos_fopen_read_nofollow(const char *path) {
#ifdef _WIN32
    wchar_t *w = hos_fopen_wide(path);
    if (!w) return NULL;
    int refused = 0;
    HANDLE h = hos_fopen_write_handle(w, GENERIC_READ, 1, &refused);
    free(w);
    if (h == INVALID_HANDLE_VALUE) {
        if (refused) hos_fopen_read_refused(path);
        return NULL;
    }
    const int fd = _open_osfhandle((intptr_t)h, _O_RDONLY);
    if (fd < 0) {
        CloseHandle(h);
        return NULL;
    }
    FILE *f = _fdopen(fd, "rb");
    if (!f) _close(fd);
    return f;
#else
    const int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (fd < 0) {
        if (errno == ELOOP) hos_fopen_read_refused(path);
        return NULL;
    }
    struct stat st;
    if (fstat(fd, &st) != 0 || !S_ISREG(st.st_mode)) {
        hos_fopen_read_refused(path);
        close(fd);
        return NULL;
    }
    const int flags = fcntl(fd, F_GETFL);
    if (flags != -1) fcntl(fd, F_SETFL, flags & ~O_NONBLOCK);
    FILE *f = fdopen(fd, "rb");
    if (!f) close(fd);
    return f;
#endif
}
