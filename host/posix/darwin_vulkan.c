/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Public contracts: dyld(3), dlopen(3), and wiki.libsdl.org/SDL2 (or SDL3)/SDL_Vulkan_LoadLibrary,
   SDL_Vulkan_GetVkGetInstanceProcAddr, SDL_Vulkan_GetInstanceExtensions. No SDL call on the renderer thread. */
#if defined(__APPLE__)
#include "darwin_vulkan.h"
#include <dlfcn.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdlib.h>

void hos_log(const char *format, ...);

int hos_darwin_vulkan_candidate(unsigned index, const char *configured, const char *exe, char *out, size_t cap) {
    const char *name = NULL;
    if (configured && *configured) {
        if (!index) name = configured;
        else index--;
    }
    int n;
    if (name) n = snprintf(out, cap, "%s", name);
    else {
        const char *slash = exe && exe[0] == '/' ? strrchr(exe, '/') : NULL;
        if (slash && index < 2) {
            name = index ? "libMoltenVK.dylib" : "libvulkan.1.dylib";
            n = snprintf(out, cap, "%.*s%s", (int)(slash + 1 - exe), exe, name);
        } else {
            if (slash) index -= 2;
            if (index) return 0;
            n = snprintf(out, cap, "%s", "libvulkan.1.dylib");
        }
    }
    return n >= 0 && (size_t)n < cap ? 1 : -1;
}

static pthread_once_t library_once = PTHREAD_ONCE_INIT;
static void *library;
static char library_path[PATH_MAX];
static void choose_library(void) {
    char exe[PATH_MAX] = "", resolved[PATH_MAX];
    uint32_t size = sizeof exe;
    if (_NSGetExecutablePath(exe, &size) != 0) exe[0] = 0;
    else if (realpath(exe, resolved)) memcpy(exe, resolved, strlen(resolved) + 1);
    const char *configured = getenv("RX_VK_LIBRARY");
    for (unsigned i = 0;; i++) {
        char path[PATH_MAX];
        const int candidate = hos_darwin_vulkan_candidate(i, configured, exe, path, sizeof path);
        if (!candidate) break;
        if (candidate < 0) { hos_log("vk: Vulkan library candidate %u is too long", i); continue; }
        void *m = dlopen(path, RTLD_NOW | RTLD_LOCAL);
        if (!m) { hos_log("vk: cannot load Vulkan library %s: %s", path, dlerror()); continue; }
        void *proc = dlsym(m, "vkGetInstanceProcAddr");
        if (!proc) {
            hos_log("vk: Vulkan library %s is missing vkGetInstanceProcAddr", path);
            dlclose(m);
            continue;
        }
        Dl_info info;
        const char *loaded = dladdr(proc, &info) && info.dli_fname ? info.dli_fname : path;
        if (strlen(loaded) >= sizeof library_path) {
            hos_log("vk: loaded Vulkan library path is too long");
            dlclose(m);
            continue;
        }
        memcpy(library_path, loaded, strlen(loaded) + 1);
        library = m;
        hos_log("vk: Vulkan library: %s", library_path);
        return;
    }
    hos_log("vk: no Vulkan library: set RX_VK_LIBRARY to a loader or MoltenVK dylib for this architecture");
}
void *hos_darwin_vulkan_library(void) {
    pthread_once(&library_once, choose_library);
    return library;
}

static void *sdl;
static int sdl_version, sdl_vulkan_loaded;
static uint32_t extension_count;
static const char *const *extension_names;
void hos_darwin_vulkan_sdl(void *module, int version) {
    sdl = module;
    sdl_version = version;
}
static const char *sdl_error(void) {
    const char *(*error)(void) = sdl ? (const char *(*)(void))dlsym(sdl, "SDL_GetError") : NULL;
    return error ? error() : "SDL is not loaded";
}
int hos_darwin_vulkan_prepare(void) {
    if (sdl_vulkan_loaded) return 1;
    void *m = hos_darwin_vulkan_library();
    if (!m || !sdl) return 0;
    void *load = dlsym(sdl, "SDL_Vulkan_LoadLibrary");
    void *get_proc = dlsym(sdl, "SDL_Vulkan_GetVkGetInstanceProcAddr");
    void *unload = dlsym(sdl, "SDL_Vulkan_UnloadLibrary");
    if (!load || !get_proc || !unload) {
        hos_log("vk: SDL%d is missing Vulkan loader entrypoints", sdl_version);
        return 0;
    }
    const int ok = sdl_version == 3 ? ((bool (*)(const char *))load)(library_path)
                                    : ((int (*)(const char *))load)(library_path) == 0;
    if (!ok) {
        hos_log("vk: SDL%d cannot load Vulkan library %s: %s", sdl_version, library_path, sdl_error());
        return 0;
    }
    typedef void (*Proc)(void);
    void *proc = sdl_version == 3 ? (void *)((Proc (*)(void))get_proc)() : ((void *(*)(void))get_proc)();
    if (proc != dlsym(m, "vkGetInstanceProcAddr")) {
        hos_log("vk: SDL%d and the renderer selected different Vulkan loaders", sdl_version);
        ((void (*)(void))unload)();
        return 0;
    }
    sdl_vulkan_loaded = 1;   /* paired process-lifetime ownership with hos_sdl.c's SDL module */
    return 1;
}
int hos_darwin_vulkan_capture(void *window) {
    if (!window || !sdl_vulkan_loaded) return 0;
    void *query = dlsym(sdl, "SDL_Vulkan_GetInstanceExtensions");
    if (!query) {
        hos_log("vk: SDL%d is missing SDL_Vulkan_GetInstanceExtensions", sdl_version);
        return 0;
    }
    uint32_t n = 0;
    const char *const *names = NULL;
    const char **allocated = NULL;
    if (sdl_version == 3) names = ((const char *const *(*)(uint32_t *))query)(&n);
    else {
        int (*get)(void *, unsigned *, const char **) = (int (*)(void *, unsigned *, const char **))query;
        if (get(window, &n, NULL) && n) {
            allocated = calloc(n, sizeof *allocated);
            if (allocated && get(window, &n, allocated)) names = allocated;
        }
    }
    if (!names || !n) {
        free(allocated);
        hos_log("vk: SDL%d Vulkan instance extensions unavailable: %s", sdl_version, sdl_error());
        return 0;
    }
    /* SDL owns the strings (and SDL3's array); its module is never unloaded. Publish before the renderer thread. */
    extension_names = names;
    extension_count = n;
    return 1;
}
int hos_darwin_vulkan_extensions(uint32_t *count, const char *const **names) {
    *count = extension_count;
    *names = extension_names;
    return 1;
}
#endif
