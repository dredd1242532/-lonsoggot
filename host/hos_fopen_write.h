/* (longshot) fopen for the files the game writes by itself in its folder and the user folder (longshot_options.ini, the
   pipeline caches and lists, the program cache and its index, the markers, the button-prompt textures, mouse_sens.txt,
   language.txt, longshot_log.txt and the like), without following a symbolic link at the file's name. A game folder
   unpacked from an archive someone shared can hold such a link (tar and unzip keep them on Linux): a plain fopen("w") then
   emptied and rewrote the file it named, anywhere the player can write.
   Linux: O_NOFOLLOW, and only a regular file (not a FIFO or a device). Windows: the name is opened as itself
   (FILE_FLAG_OPEN_REPARSE_POINT) and refused when it is a name surrogate (a symbolic link, a junction, WSL's and the
   container links), then opened for writing and kept only when that is the same file, so nothing can be swapped in
   between; OneDrive's own reparse points are not surrogates and are written.
   mode: "w", "wb", "a", "ab", "r+" or "r+b" ("r+": an existing file only, as fopen). Used by the host (C) and the renderer
   (C++). tests/test_game_writes_no_links.py fails on a write in host/ or gpu/vk/ that does not come through here. */
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
/* (the handle to write w through, or INVALID_HANDLE_VALUE; *refused: the name is a link or not a plain file) */
static inline HANDLE hos_fopen_write_handle(const wchar_t *w, DWORD access, int existing, int *refused) {
    const DWORD share = FILE_SHARE_READ | FILE_SHARE_WRITE;   /* (as fopen's) */
    const HANDLE look = CreateFileW(w, FILE_READ_ATTRIBUTES, share | FILE_SHARE_DELETE, NULL, OPEN_EXISTING,
                                    FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS, NULL);
    if (look == INVALID_HANDLE_VALUE) {
        if (existing || GetLastError() != ERROR_FILE_NOT_FOUND) return INVALID_HANDLE_VALUE;
        /* (nothing at the name: made new, which fails if something, a link too, was put there since) */
        return CreateFileW(w, access, share, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
    }
    FILE_ATTRIBUTE_TAG_INFO tag;
    BY_HANDLE_FILE_INFORMATION was, is;
    const int plain = GetFileType(look) == FILE_TYPE_DISK &&
                      GetFileInformationByHandleEx(look, FileAttributeTagInfo, &tag, sizeof tag) &&
                      !(tag.FileAttributes & FILE_ATTRIBUTE_DIRECTORY) &&
                      !((tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) && IsReparseTagNameSurrogate(tag.ReparseTag)) &&
                      GetFileInformationByHandle(look, &was);
    /* (opened again without FILE_FLAG_OPEN_REPARSE_POINT, so that OneDrive fills a file kept only online before it is
       written, and kept only if it is the file looked at) */
    HANDLE h = plain ? CreateFileW(w, access, share, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL) : INVALID_HANDLE_VALUE;
    const int opened = h != INVALID_HANDLE_VALUE;
    const int same = opened && GetFileInformationByHandle(h, &is) && is.dwVolumeSerialNumber == was.dwVolumeSerialNumber &&
                     is.nFileIndexHigh == was.nFileIndexHigh && is.nFileIndexLow == was.nFileIndexLow;
    CloseHandle(look);
    if (opened && !same) CloseHandle(h), h = INVALID_HANDLE_VALUE;
    *refused = !plain || (opened && !same);
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
