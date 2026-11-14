/* SPDX-License-Identifier: GPL-3.0-or-later */
/* macOS Vulkan library ownership and SDL's main-thread startup. */
#pragma once
#if defined(__APPLE__)
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>

/* Pure name translation: no search or dlopen. Kept inline because win32_posix.c is also in the renderer. */
static inline int hos_darwin_library_path(const char *name, char *out, size_t cap) {
    if (!name || strlen(name) >= cap) return 0;
    memcpy(out, name, strlen(name) + 1);
    for (char *p = out; *p; p++) if (*p == '\\') *p = '/';
    char *slash = strrchr(out, '/');
    char *base = slash ? slash + 1 : out;
    const char *mapped = NULL;
    static const char *const map[][2] = {{"sdl3.dll", "libSDL3.0.dylib"}, {"sdl2.dll", "libSDL2-2.0.0.dylib"},
                                       {"vulkan-1.dll", "libvulkan.1.dylib"}};
    for (size_t i = 0; i < sizeof map / sizeof map[0]; i++)
        if (!strcasecmp(base, map[i][0])) { mapped = map[i][1]; break; }
    if (mapped) {
        const size_t left = cap - (size_t)(base - out);
        if (strlen(mapped) >= left) return 0;
        memcpy(base, mapped, strlen(mapped) + 1);
    } else {
        const size_t n = strlen(out);
        if (n > 4 && !strcasecmp(out + n - 4, ".dll")) {
            if (!slash || n + 3 > cap) return 0;   /* no Windows system DLLs; .dylib needs two more bytes */
            memcpy(out + n - 4, ".dylib", 7);
        }
    }
    return 1;
}

#ifdef __cplusplus
extern "C" {
#endif
/* Pure candidate order, using an executable path supplied by the caller. 1: candidate, 0: end, -1: too long. */
int hos_darwin_vulkan_candidate(unsigned index, const char *configured, const char *exe, char *out, size_t cap);
/* One library, retained for the process lifetime: SDL and the renderer must never use different loaders. */
void *hos_darwin_vulkan_library(void);
void hos_darwin_vulkan_sdl(void *module, int version);
int hos_darwin_vulkan_prepare(void);               /* main thread, after video init and before a Vulkan window */
int hos_darwin_vulkan_capture(void *window);        /* main thread, after the window; no surface is created */
int hos_darwin_vulkan_extensions(uint32_t *count, const char *const **names); /* cached only; 0 names when headless */
#ifdef __cplusplus
}
#endif
#endif
