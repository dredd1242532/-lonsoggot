"""Darwin Vulkan bridge: the same loader in SDL2/SDL3 and the renderer; cached main-thread extension discovery.

The real bridge functions are cut from darwin_vulkan.c and compiled as C against ABI-correct, windowless SDL stand-ins.
No library is loaded, no SDL API runs and no Vulkan/Metal device or surface is created. The actual surface callback is
syntax-checked in the same fixture only, never executed. --source supports a main-source negative control.
"""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]
ROOT = Path(__file__).resolve().parents[1]

PRELUDE = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
static int library_token, sdl_token, window_token, failures, version, mode, loads, unloads, queries;
static char logged[4096];
static const char library_path[] = "/chosen/libMoltenVK.dylib";
static const char *const metal[] = {"VK_KHR_surface", "VK_EXT_metal_surface"};
static void check(int ok, const char *why) { if (!ok) { printf("FAIL: %s\n", why); failures++; } }
static void hos_log(const char *fmt, ...) { va_list a; va_start(a, fmt); vsnprintf(logged, sizeof logged, fmt, a); va_end(a); }
static void *hos_darwin_vulkan_library(void) { return &library_token; }
static void gipa(void) {}
static void other_gipa(void) {}
static const char *error(void) { return "authored SDL failure"; }
static bool load3(const char *path) { loads++; check(!strcmp(path, library_path), "SDL receives chosen loader path"); return mode != 1; }
static int load2(const char *path) { return load3(path) ? 0 : -1; }
static void unload(void) { unloads++; }
static void (*get3(void))(void) { return mode == 2 ? other_gipa : gipa; }
static void *get2(void) { return (void *)get3(); }
static const char *const *query3(uint32_t *count) { queries++; *count = 2; return mode == 4 ? NULL : metal; }
static int query2(void *window, unsigned *count, const char **names) {
    queries++; check(window == &window_token, "SDL2 receives actual window token");
    if (mode == 4) return 0;
    if (names) { check(*count >= 2, "SDL2 array has requested capacity"); names[0] = metal[0]; names[1] = metal[1]; }
    *count = 2; return 1;
}
static void *dlsym(void *module, const char *name) {
    if (module == &library_token && !strcmp(name, "vkGetInstanceProcAddr")) return (void *)gipa;
    if (module != &sdl_token) return NULL;
    if (!strcmp(name, "SDL_GetError")) return (void *)error;
    if (!strcmp(name, "SDL_Vulkan_LoadLibrary")) return mode == 5 ? NULL : version == 3 ? (void *)load3 : (void *)load2;
    if (!strcmp(name, "SDL_Vulkan_GetVkGetInstanceProcAddr")) return mode == 6 ? NULL : version == 3 ? (void *)get3 : (void *)get2;
    if (!strcmp(name, "SDL_Vulkan_UnloadLibrary")) return mode == 7 ? NULL : (void *)unload;
    if (!strcmp(name, "SDL_Vulkan_GetInstanceExtensions")) return mode == 3 ? NULL : version == 3 ? (void *)query3 : (void *)query2;
    return NULL;
}
/* hos_sdl.c's existing surface bridge is compiled, not called: no window/surface permission inferred. */
static struct { void *Vulkan_CreateSurface; } f;
#define CALL(type, fn) ((type)f.fn)
'''

CASES = r'''
static void reset(void) {
    if (sdl_version == 2) free((void *)extension_names);
    extension_count = 0; extension_names = NULL; sdl_vulkan_loaded = 0;
    loads = unloads = queries = 0; logged[0] = 0;
    hos_darwin_vulkan_sdl(&sdl_token, version);
}
int main(void) {
    for (version = 2; version <= 3; version++) {
        for (mode = 0; mode <= 7; mode++) {
            reset();
            uint32_t n = 99; const char *const *names = metal;
            check(hos_darwin_vulkan_extensions(&n, &names) && n == 0 && names == NULL, "headless extensions need no SDL calls");
            check(queries == 0 && loads == 0, "headless query never loads Vulkan or SDL");
            const int prepared = hos_darwin_vulkan_prepare();
            if (mode == 1 || mode == 2 || mode >= 5) {
                check(!prepared, "SDL loader refusal propagates");
                check(unloads == (mode == 2), "only successful mismatched load is unloaded");
                check(strstr(logged, mode == 2 ? "different Vulkan loaders" : mode == 1 ? "cannot load Vulkan library" : "missing Vulkan loader entrypoints") != NULL,
                      "loader refusal names missing requirement");
                continue;
            }
            check(prepared, "SDL prepare succeeds for matching loader");
            check(hos_darwin_vulkan_prepare() && loads == 1, "SDL library reference taken once");
            const int captured = hos_darwin_vulkan_capture(&window_token);
            if (mode == 3 || mode == 4) {
                check(!captured, "SDL extension refusal propagates");
                check(strstr(logged, mode == 3 ? "missing SDL_Vulkan_GetInstanceExtensions" : "Vulkan instance extensions unavailable") != NULL,
                      "SDL extension refusal named");
                continue;
            }
            check(captured, "SDL extensions captured after window creation");
            const int count_before = queries;
            check(hos_darwin_vulkan_extensions(&n, &names) && n == 2 && names && !strcmp(names[0], metal[0]) && !strcmp(names[1], metal[1]),
                  "renderer receives SDL's extension names");
            check(queries == count_before && queries == (version == 3 ? 1 : 2), "renderer reads cached names, never asks SDL on its thread");
        }
    }
    reset();
    if (!failures) puts("PASS: SDL2/SDL3 bridge 16 cases; loader identity, named failures, cached extensions; surface path compiled only");
    return failures != 0;
}
'''


def source(root):
    path = root / "host/posix/darwin_vulkan.c"
    if not path.is_file():
        raise SystemExit("FAIL: main has no Darwin SDL Vulkan loader/extension bridge")
    text = path.read_text(encoding="utf-8")
    bridge = text[text.index("static void *sdl;"):text.rindex("#endif")]
    adapter = (root / "host/hos_sdl.c").read_text(encoding="utf-8")
    start = adapter.index("int hos_sdl_vulkan_surface(")
    surface = adapter[start:adapter.index("\n}", start) + 2]
    # Wiring assertions do not replace the compiled bridge cases above.
    display = (root / "host/posix/display_sdl.c").read_text(encoding="utf-8")
    backend = (root / "host/rx_backend.c").read_text(encoding="utf-8")
    for text, required in ((adapter, "hos_darwin_vulkan_sdl(m, version)"), (adapter, "hos_darwin_vulkan_prepare()"),
                           (display, "hos_darwin_vulkan_capture(window)"), (display, '"cocoa"'),
                           (backend, "hos_darwin_vulkan_library, hos_darwin_vulkan_extensions,")):
        if required not in text:
            raise SystemExit("FAIL: missing Darwin bridge wiring: " + required)
    if display.index("hos_darwin_vulkan_capture(window)") > display.index("CloseHandle(CreateThread(NULL, 0, renderer_main"):
        raise SystemExit("FAIL: SDL extension discovery occurs after renderer thread creation")
    return PRELUDE + bridge + surface + CASES


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=ROOT)
    ap.add_argument("--cxx", default="clang++")
    ap.add_argument("--arch", choices=("arm64", "x86_64"))
    args = ap.parse_args()
    if args.arch and sys.platform != "darwin":
        ap.error("--arch is macOS-only")
    with tempfile.TemporaryDirectory(prefix="vk-sdl-") as directory:
        tmp = Path(directory)
        c, exe = tmp / "sdl.c", tmp / "sdl.exe"
        c.write_text(source(args.source), encoding="utf-8")
        command = [args.cxx, "-x", "c", "-std=c11", "-Wall", "-Wextra", "-Werror", "-D_CRT_SECURE_NO_WARNINGS", str(c), "-o", str(exe)]
        if sys.platform == "darwin":
            command += ["-mmacosx-version-min=14.4"]
            if args.arch:
                command += ["-arch", args.arch]
        print("COMPILE:", " ".join(command), flush=True)
        subprocess.run(command, check=True, timeout=120, cwd=tmp)
        run = (["arch", "-" + args.arch] if args.arch else []) + [str(exe)]
        print("RUN:", " ".join(run), flush=True)
        return subprocess.run(run, timeout=30, cwd=tmp).returncode


if __name__ == "__main__":
    raise SystemExit(main())
