"""Vulkan admission without a GPU, SDL, a window or game data.

Compile the actual loader/instance code and device-extension selection against authored call recorders, plus the pure
Darwin decisions. Windows' System32 loader and Linux's names/surface list are pinned to main 514591a3. A fixed semantic
contract pins the non-Apple API version, host size and named member offsets; Apple appends two callbacks at version 22.
SDL2/SDL3's loader and extension bridge is exercised with ABI-correct stand-ins, not a real video driver.

    python -I -B tests/test_vk_darwin_admission.py --cxx clang++ --headers <Vulkan-Headers/include>
    # On macOS only, also --arch arm64 or --arch x86_64 (the latter runs through arch -x86_64).
    # --source <main tree> is the negative control. Device-level evidence is a separate, explicitly gated local probe.
"""
import argparse
from pathlib import Path
import re
import subprocess
import sys
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}", "--headers", "{vulkan_headers}"]
ROOT = Path(__file__).resolve().parents[1]
# The semantic host ABI at main 514591a3 on its 64-bit platforms. Expected names and offsets are deliberately literal,
# not inferred from the current header: reordering or renaming a callback must fail even when the total size is unchanged.
HOST_ABI_BASE = (
    ("version", 0), ("log", 8), ("guest_base", 16), ("guest_size", 24), ("gpu_ptr", 32), ("gpu_guest", 40),
    ("gpu_read", 48), ("gpu_write", 56), ("collect_written", 64), ("free_pages", 72), ("mark_written", 80),
    ("ticks", 88), ("now_ns", 96), ("tex_layout", 104), ("tex_level_offset", 112), ("fmt_bpb", 120),
    ("tex_level_tiles", 128), ("tex_linear_stride", 136), ("depth_minus_w", 144), ("gpu_pools", 152),
    ("maps_generation", 160), ("vmap_generation", 168), ("reserved_cpus", 176), ("collect_written_in", 184),
    ("guest_alloc", 192), ("watch_guest_writes", 200), ("pad_raw", 208), ("binding", 216), ("bind", 224),
    ("aspect_ratio", 232), ("take_key_press", 240), ("pointer", 248), ("mic_count", 256), ("mic_name", 264),
    ("mic_select", 272), ("gyro_status", 280), ("log_flush", 288), ("prompt_face", 296), ("romfs_dir", 304),
    ("wide_menus", 312), ("slept_ns", 320), ("fps_timing", 328), ("prompts_live", 336), ("screen_size", 344),
)


def block(text, marker):
    start = text.index(marker)
    at = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[at] == "{") - (text[at] == "}")
        at += 1
    return text[start:at]


def select(text, apple, windows=None):
    """Select only the two platform guards in source excerpts, never change compiler/system-header macros."""
    out, stack, active = [], [], True
    for line in text.splitlines(keepends=True):
        directive = line.strip().split("//", 1)[0].split("/*", 1)[0].strip()
        if re.match(r"#\s*(if|ifdef|ifndef)\b", directive):
            value = {"#if defined(__APPLE__)": apple}.get(directive)
            if windows is not None:
                value = {"#ifdef _WIN32": windows, "#ifndef _WIN32": not windows}.get(directive, value)
            stack.append((active, value))
            if value is None:
                if active:
                    out.append(line)
            else:
                active = active and value
        elif directive == "#else" and stack:
            outer, value = stack[-1]
            if value is None:
                if active:
                    out.append(line)
            else:
                active = outer and not value
        elif directive == "#endif" and stack:
            outer, value = stack.pop()
            if value is None and active:
                out.append(line)
            active = outer
        elif active:
            out.append(line)
    if stack:
        raise ValueError("unbalanced platform guards in source excerpt")
    return "".join(out)




PRELUDE = r'''
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
#include <vulkan/vulkan_beta.h>
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdarg>
#include <mutex>
#include <string>
#include <type_traits>
#include <vector>
#include "gpu/vk/vk_api.h"
#include "gpu/vk/mesh_mode.h"
#ifndef VK_KHR_WIN32_SURFACE_EXTENSION_NAME
#define VK_KHR_WIN32_SURFACE_EXTENSION_NAME "VK_KHR_win32_surface"
#endif
using HMODULE = void*;
using LPCSTR = const char*;
using DWORD = unsigned long;
constexpr unsigned MAX_PATH = 1024, ERROR_FILE_NOT_FOUND = 2;
#ifndef PATH_MAX
#define PATH_MAX 4096
#endif
constexpr unsigned LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x800, RTLD_NOW = 2, RTLD_LOCAL = 4;
static int failures;
static void check(bool ok, const char* why) { if (!ok) { std::printf("FAIL: %s\n", why); failures++; } }
static bool same(const char* a, const char* b) { return a && b ? !std::strcmp(a, b) : a == b; }
static int fixture_casecmp(const char* a, const char* b) {  // ASCII DLL names, on any fixture host
    while (*a && *b) {
        const unsigned x = (*a >= 'A' && *a <= 'Z') ? *a + 32 : (unsigned char)*a;
        const unsigned y = (*b >= 'A' && *b <= 'Z') ? *b + 32 : (unsigned char)*b;
        if (x != y) return x < y ? -1 : 1;
        ++a; ++b;
    }
    return (unsigned char)*a - (unsigned char)*b;
}
#define strcasecmp fixture_casecmp
static std::vector<VkExtensionProperties> properties(std::initializer_list<const char*> names) {
    std::vector<VkExtensionProperties> out;
    for (const char* name : names) { VkExtensionProperties e{}; std::snprintf(e.extensionName, sizeof e.extensionName, "%s", name); out.push_back(e); }
    return out;
}
static std::vector<std::string> strings(const char* const* names, uint32_t count) {
    std::vector<std::string> out;
    for (uint32_t i = 0; i < count; ++i) out.emplace_back(names[i]);
    return out;
}
static void names_equal(const std::vector<std::string>& actual, const std::vector<std::string>& expected, const char* why) {
    if (actual == expected) return;
    check(false, why);
    std::printf("  actual:"); for (const auto& n : actual) std::printf(" %s", n.c_str());
    std::printf("\n  expected:"); for (const auto& n : expected) std::printf(" %s", n.c_str());
    std::puts("");
}
'''

RECORDER = r'''
static std::vector<VkExtensionProperties> advertised;
static std::vector<std::string> requested, loaded, logs;
static VkInstanceCreateFlags instance_flags;
static unsigned loader_flags;
static bool validation;
static VkResult create_result = VK_SUCCESS;
static const char* const metal[] = {"VK_KHR_surface", "VK_EXT_metal_surface"};
static const char* const* sdl_names;
static uint32_t sdl_count;
static void logf(const char* fmt, ...) {
    char b[1024]; va_list a; va_start(a, fmt); std::vsnprintf(b, sizeof b, fmt, a); va_end(a); logs.emplace_back(b);
}
static bool env_on(const char* name) { return !strcmp(name, "RX_VK_VALIDATION") && validation; }
static bool logged(const char* text) { return std::find(logs.begin(), logs.end(), text) != logs.end(); }
static VkResult VKAPI_PTR enumerate_version(uint32_t* v) { *v = VK_API_VERSION_1_3; return VK_SUCCESS; }
static VkResult VKAPI_PTR enumerate_extensions(const char*, uint32_t* n, VkExtensionProperties* p) {
    if (p) std::copy(advertised.begin(), advertised.end(), p); *n = (uint32_t)advertised.size(); return VK_SUCCESS;
}
static VkResult VKAPI_PTR make_instance(const VkInstanceCreateInfo* ci, const VkAllocationCallbacks*, VkInstance*) {
    requested = strings(ci->ppEnabledExtensionNames, ci->enabledExtensionCount); instance_flags = ci->flags;
    return create_result;
}
static PFN_vkEnumerateInstanceVersion vkEnumerateInstanceVersion = enumerate_version;
static PFN_vkEnumerateInstanceExtensionProperties vkEnumerateInstanceExtensionProperties = enumerate_extensions;
static PFN_vkCreateInstance vkCreateInstance = make_instance;
static PFN_vkGetInstanceProcAddr get_instance_proc;
static void load_instance(VkInstance) {}
static void dlss_instance_extensions(std::vector<const char*>&, const std::vector<VkExtensionProperties>&) {}
static void* dlopen(const char* path, int) { loaded.emplace_back(path); return nullptr; }
static void* GetProcAddress(void*, const char*) { return nullptr; }
static void* LoadLibraryExA(const char* path, void*, unsigned flags) { loaded.emplace_back(path); loader_flags = flags; return nullptr; }
static unsigned last_error;
static int cached_extensions(uint32_t* n, const char* const** names) { *n = sdl_count; *names = sdl_names; return 1; }
static struct { int (*vulkan_extensions)(uint32_t*, const char* const**); } host_data{cached_extensions}, *host = &host_data;
static struct { VkInstance instance{}; bool swapchain_colorspace{}; } dev;
#define VKX_GLOBAL(X) X(vkCreateInstance) X(vkEnumerateInstanceExtensionProperties) X(vkEnumerateInstanceVersion)
'''

DEVICE_RECORDER = r'''
static std::vector<VkExtensionProperties> advertised;
static std::vector<std::string> requested, logs;
static struct {
    VkPhysicalDevice physical{};
    VkPhysicalDeviceProperties props{};
    bool mesh_capable{}, mesh_shader{}, external_memory_host{}, external_memory_fd{}, dma_buf_import{}, pipeline_library{},
         graphics_pipeline_library{}, custom_border_color{}, dynamic_state3{}, depth_clip_control{}, null_descriptor{}, index_uint8{},
         push_descriptor{}, conditional_rendering{}, provoking_vertex{}, vertex_divisor{}, stencil_export{}, memory_budget{},
         view_2d_of_3d{}, checkpoints{}, buffer_marker{}, device_fault{}, coop_matrix{}, swap_mutable{};
} dev;
static void logf(const char* fmt, ...) { char b[1024]; va_list a; va_start(a, fmt); std::vsnprintf(b, sizeof b, fmt, a); va_end(a); logs.emplace_back(b); }
static bool logged(const char* text) { return std::find(logs.begin(), logs.end(), text) != logs.end(); }
static const char* fixture_env(const char*) { return nullptr; }
static unsigned game_module_path(char* path, unsigned cap) { return (unsigned)std::snprintf(path, cap, "/not-an-LS-install/longshot"); }
static VkResult vkEnumerateDeviceExtensionProperties(VkPhysicalDevice, const char*, uint32_t* n, VkExtensionProperties* p) {
    if (p) std::copy(advertised.begin(), advertised.end(), p); *n = (uint32_t)advertised.size(); return VK_SUCCESS;
}
static void vkGetPhysicalDeviceFeatures2(VkPhysicalDevice, VkPhysicalDeviceFeatures2*) {}
static void vkGetPhysicalDeviceProperties2(VkPhysicalDevice, VkPhysicalDeviceProperties2*) {}
static bool native_fadd_wanted(const char*, VkDriverId driver) { return driver == VK_DRIVER_ID_NVIDIA_PROPRIETARY; }
static void dlss_device_extensions(std::vector<const char*>&, const std::vector<VkExtensionProperties>&) {}
static void dlss_fg_device_extensions(std::vector<const char*>&, const std::vector<VkExtensionProperties>&) {}
static bool fsr4_wants_descriptor_buffer() { return false; }
'''

# main's default extension selection, in request order, with vendor SDK hooks inactive.
OPTIONAL = ["VK_EXT_mesh_shader", "VK_EXT_external_memory_host", "VK_KHR_external_memory_fd", "VK_EXT_external_memory_dma_buf",
            "VK_KHR_pipeline_library", "VK_EXT_shader_module_identifier", "VK_EXT_graphics_pipeline_library",
            "VK_EXT_custom_border_color", "VK_EXT_extended_dynamic_state3", "VK_EXT_depth_clip_control", "VK_EXT_robustness2",
            "VK_EXT_index_type_uint8", "VK_KHR_push_descriptor", "VK_EXT_conditional_rendering", "VK_EXT_provoking_vertex",
            "VK_EXT_vertex_attribute_divisor", "VK_EXT_shader_stencil_export", "VK_EXT_memory_budget", "VK_EXT_image_2d_view_of_3d",
            "VK_NV_device_diagnostic_checkpoints", "VK_EXT_device_fault", "VK_KHR_cooperative_matrix", "VK_KHR_swapchain_mutable_format",
            "VK_NV_low_latency2", "VK_KHR_present_id", "VK_AMD_anti_lag"]


def instance_fixture(main, posix, platform):
    apple, windows = platform == "darwin", platform == "windows"
    helpers = "\n".join(block(main, "static const char* " + fn + "(") for fn in
                         ("darwin_result_name", "darwin_instance_extensions")) if "darwin_result_name(" in main and apple else ""
    code = RECORDER + block(main, "bool has_extension(") + "\n" + helpers
    if not apple:
        code += "\n" + block(posix, "static const char *linux_name(")
        code += "\n" + select(block(posix, "HMODULE LoadLibraryA("), False)
        code += "\n" + select(block(main, "bool load_global()"), False, windows)
    code += "\n" + select(block(main, "bool create_instance()"), apple, windows)
    code += '\nstatic void run() {\n    const int before = failures;\n'
    if not apple:
        library = "vulkan-1.dll" if windows else "libvulkan.so.1"
        code += f'    check(!load_global(), "{platform}: missing loader rejected");\n'
        code += f'    names_equal(loaded, {{"{library}"}}, "{platform}: loader names unchanged");\n'
        code += f'    check(loader_flags == {"LOAD_LIBRARY_SEARCH_SYSTEM32" if windows else "0"}, "{platform}: loader search flags unchanged");\n'
        for mask in range(8):
            surface = ["VK_KHR_surface", "VK_KHR_win32_surface"]
            surface += [name for bit, name in enumerate(("VK_KHR_xlib_surface", "VK_KHR_xcb_surface", "VK_KHR_wayland_surface")) if mask & (1 << bit)]
            surface += ["VK_EXT_metal_surface", "VK_KHR_portability_enumeration"]
            for hdr in (False, True):
                avail = surface + (["VK_EXT_swapchain_colorspace"] if hdr else [])
                expected = surface[:2] if windows else [surface[0]] + surface[2:-2]
                expected += ["VK_EXT_swapchain_colorspace"] if hdr else []
                code += f'    advertised = properties({{{", ".join(chr(34)+s+chr(34) for s in avail)}}}); requested.clear();\n'
                code += f'    check(create_instance(), "{platform}: instance selection accepted");\n'
                code += f'    names_equal(requested, {{{", ".join(chr(34)+s+chr(34) for s in expected)}}}, "{platform}: surface order equals main");\n'
                code += f'    check(instance_flags == 0, "{platform}: no portability flag added");\n'
        code += f'    if (before == failures) std::puts("PASS: {platform} loader and 16 instance extension cases equal main");\n'
    else:
        code += r'''
    for (bool portability : {false, true}) for (bool window : {false, true}) {
        advertised = properties({"VK_KHR_surface", "VK_EXT_metal_surface", "VK_KHR_xlib_surface"});
        if (portability) { auto p = properties({"VK_KHR_portability_enumeration"}); advertised.push_back(p[0]); }
        sdl_names = window ? metal : nullptr; sdl_count = window ? 2 : 0;
        requested.clear(); logs.clear(); check(create_instance(), "Darwin: admitted SDL or headless extensions");
        std::vector<std::string> expected{"VK_KHR_surface"};
        if (window) expected.emplace_back("VK_EXT_metal_surface");
        if (portability) expected.emplace_back("VK_KHR_portability_enumeration");
        names_equal(requested, expected, "Darwin: SDL extensions, no Linux names; portability only when advertised");
        check(instance_flags == (portability ? VK_INSTANCE_CREATE_ENUMERATE_PORTABILITY_BIT_KHR : 0), "Darwin: portability flag matches advertisement");
    }
    advertised = properties({"VK_KHR_surface"}); sdl_names = metal; sdl_count = 2; logs.clear();
    check(!create_instance(), "Darwin: missing Metal surface extension refused");
    check(logged("vk: required instance extension missing: VK_EXT_metal_surface"), "Darwin: missing Metal extension named");
    advertised.clear(); sdl_names = nullptr; sdl_count = 0; logs.clear();
    check(!create_instance(), "Darwin: generic surface dependency still required headless");
    check(logged("vk: required instance extension missing: VK_KHR_surface"), "Darwin: generic surface missing message");
    advertised = properties({"VK_KHR_surface"}); create_result = VK_ERROR_LAYER_NOT_PRESENT; validation = true; logs.clear();
    check(!create_instance(), "Darwin: missing requested validation layer refused");
    check(logged("vk: required instance layer missing: VK_LAYER_KHRONOS_validation"), "Darwin: missing validation layer named");
    if (before == failures) std::puts("PASS: Darwin SDL/headless/portability and named instance refusals");
'''
    return f'namespace {platform}_instance {{\n' + code + "}\n}\n"


def device_fixture(root, main, platform):
    apple, windows = platform == "darwin", platform == "windows"
    helpers = "\n".join(block(main, "static const char* " + fn + "(") for fn in
                         ("darwin_result_name", "darwin_device_extensions")) if "darwin_result_name(" in main and apple else ""
    selection = block(main, "bool create_device()")
    selection = selection[:selection.index("    // the queue:")] + "    requested = strings(want.data(), (uint32_t)want.size()); return true;\n}"
    code = DEVICE_RECORDER + block(main, "bool has_extension(") + "\n" + helpers
    code += "\n#define getenv fixture_env\n"
    for filename, markers in (("vk_reflex.cpp", ("struct Reflex {", "Reflex& rx()", "void reflex_device_extensions(")),
                              ("vk_antilag.cpp", ("struct AntiLag {", "AntiLag& al()", "void antilag_device_extensions("))):
        text = (root / "gpu/vk" / filename).read_text()
        for marker in markers:
            code += block(text, marker) + (";" if marker.startswith("struct ") else "") + "\n"
    code += select(selection, apple, windows) + "\n#undef getenv\n"
    code += "\nstatic void run() {\n    const int before = failures;\n"
    groups = [OPTIONAL, [], ["VK_EXT_graphics_pipeline_library", "VK_EXT_external_memory_dma_buf"],
              ["VK_KHR_pipeline_library", "VK_EXT_graphics_pipeline_library"],
              ["VK_KHR_external_memory_fd", "VK_EXT_external_memory_dma_buf"],
              ["VK_NV_low_latency2", "VK_KHR_present_id", "VK_AMD_anti_lag"], ["VK_NV_low_latency2"]]
    for extensions in groups:
        extras = ["VK_KHR_swapchain", "VK_KHR_portability_subset", "VK_EXT_shader_atomic_float", "VK_AMD_buffer_marker", "VK_EXT_descriptor_buffer"]
        expected = ["VK_KHR_swapchain"] + (["VK_KHR_portability_subset"] if apple else [])
        expected += [name for name in OPTIONAL if name in extensions and
                     (name != "VK_EXT_graphics_pipeline_library" or "VK_KHR_pipeline_library" in extensions) and
                     (name != "VK_EXT_external_memory_dma_buf" or "VK_KHR_external_memory_fd" in extensions) and
                     (name not in ("VK_NV_low_latency2", "VK_KHR_present_id") or
                      all(n in extensions for n in ("VK_NV_low_latency2", "VK_KHR_present_id")))]
        code += f'    advertised = properties({{{", ".join(chr(34)+s+chr(34) for s in extras + extensions)}}});\n'
        code += f'    check(create_device(), "{platform}: device extension selection");\n'
        code += f'    names_equal(requested, {{{", ".join(chr(34)+s+chr(34) for s in expected)}}}, "{platform}: optional extension order and dependencies");\n'
    code += '    advertised.clear(); logs.clear();\n'
    if apple:
        code += '    check(!create_device(), "Darwin: swapchain still required headless");\n'
        code += '    check(logged("vk: required device extension missing: VK_KHR_swapchain"), "Darwin: missing swapchain named");\n'
    else:
        code += f'    check(create_device(), "{platform}: unconditional swapchain request unchanged");\n'
        code += f'    names_equal(requested, {{"VK_KHR_swapchain"}}, "{platform}: required swapchain request equals main");\n'
    code += f'    if (before == failures) std::puts("PASS: {platform} required/optional device extension decisions ({len(groups) + 1} cases)");\n'
    return f'namespace {platform}_device {{\n' + code + "}\n}\n"


def darwin_pure(root, main):
    cpath, hpath = root / "host/posix/darwin_vulkan.c", root / "host/posix/darwin_vulkan.h"
    if not cpath.exists() or "darwin_required_feature(" not in main:
        return 'namespace darwin_pure { static void run() { check(false, "Darwin library and feature admission absent on main"); } }\n'
    c, h = cpath.read_text(), hpath.read_text()
    code = block(c, "int hos_darwin_vulkan_candidate(") + "\n" + block(h, "static inline int hos_darwin_library_path(")
    code += "\n" + block(main, "static const char* darwin_required_feature(") + "\n" + block(main, "static const char* darwin_result_name(")
    code += r'''
static void run() {
    const int before = failures;
    for (const char* value : {static_cast<const char*>(nullptr), "", "/chosen/driver.dylib", "0", "relative.dylib"}) {
        char path[256]; std::vector<std::string> got;
        for (unsigned i = 0; hos_darwin_vulkan_candidate(i, value, "/game/bin/longshot", path, sizeof path) == 1; ++i) got.emplace_back(path);
        std::vector<std::string> expected;
        if (value && *value) expected.emplace_back(value);
        expected.insert(expected.end(), {"/game/bin/libvulkan.1.dylib", "/game/bin/libMoltenVK.dylib", "libvulkan.1.dylib"});
        names_equal(got, expected, "Darwin: explicit, beside executable, usual loader name");
    }
    char path[256];
    check(hos_darwin_vulkan_candidate(0, nullptr, nullptr, path, sizeof path) == 1 && same(path, "libvulkan.1.dylib"), "Darwin: no executable path does not search current directory");
    check(hos_darwin_vulkan_candidate(1, nullptr, "relative-executable", path, sizeof path) == 0, "Darwin: no extra fallback without executable path");
    check(hos_darwin_vulkan_candidate(0, "/a/long/explicit/path", "/game/longshot", path, 4) == -1, "Darwin: no truncated library candidate");
    struct Map { const char* in; const char* out; };
    for (const Map& m : {Map{"SDL3.dll", "libSDL3.0.dylib"}, Map{"sDl2.DlL", "libSDL2-2.0.0.dylib"},
                        Map{"vulkan-1.dll", "libvulkan.1.dylib"}, Map{"/game/bin/longshot_vk.dll", "/game/bin/longshot_vk.dylib"},
                        Map{"/game\\SDL3.dll", "/game/libSDL3.0.dylib"}, Map{"/driver/custom.dylib", "/driver/custom.dylib"}}) {
        check(hos_darwin_library_path(m.in, path, sizeof path) && same(path, m.out), "Darwin: translated library name");
    }
    check(!hos_darwin_library_path("kernel32.dll", path, sizeof path), "Darwin: no Windows system DLL search");
    check(!hos_darwin_library_path(nullptr, path, sizeof path), "Darwin: null library rejected");
    check(!hos_darwin_library_path("/d/x.dll", path, 9), "Darwin: suffix expansion cannot overflow");
    check(hos_darwin_library_path("/d/x.dll", path, 11) && same(path, "/d/x.dylib"), "Darwin: exact suffix capacity works");
    for (unsigned mask = 0; mask < 8; ++mask) {
        VkPhysicalDeviceVulkan12Features f12{}; VkPhysicalDeviceVulkan13Features f13{};
        f13.dynamicRendering = (mask & 1) != 0; f13.synchronization2 = (mask & 2) != 0; f12.timelineSemaphore = (mask & 4) != 0;
        const char* expected = !(mask & 1) ? "VkPhysicalDeviceVulkan13Features.dynamicRendering" :
                               !(mask & 2) ? "VkPhysicalDeviceVulkan13Features.synchronization2" :
                               !(mask & 4) ? "VkPhysicalDeviceVulkan12Features.timelineSemaphore" : nullptr;
        check(same(darwin_required_feature(f12, f13), expected), "Darwin: first missing core feature named exactly");
    }
    check(same(darwin_result_name(VK_ERROR_FEATURE_NOT_PRESENT), "VK_ERROR_FEATURE_NOT_PRESENT"), "Darwin: feature VkResult named");
    check(same(darwin_result_name(VK_ERROR_EXTENSION_NOT_PRESENT), "VK_ERROR_EXTENSION_NOT_PRESENT"), "Darwin: extension VkResult named");
    check(same(darwin_result_name(VK_ERROR_LAYER_NOT_PRESENT), "VK_ERROR_LAYER_NOT_PRESENT"), "Darwin: layer VkResult named");
    check(same(darwin_result_name(VK_SUCCESS), "VK_SUCCESS"), "Darwin: success VkResult named");
    if (before == failures) std::puts("PASS: Darwin library candidates, UTF-8-safe name mapping and all 8 required-feature masks");
}
'''
    return "namespace darwin_pure {\n" + code + "}\n"


def abi_fixture(api):
    off, on = select(api, False), select(api, True)
    original = block(off, "typedef struct MkwVkHost")
    extended = block(on, "typedef struct MkwVkHost")
    code = original.replace("MkwVkHost", "NonAppleHost") + " NonAppleHost;\n"
    code += extended.replace("MkwVkHost", "AppleHost") + " AppleHost;\n"
    # Compile the header's version definitions as code; comments/formatting are not the assertion's subject.
    for variant, name in ((off, "FIXTURE_NONAPPLE_API_VERSION"), (on, "FIXTURE_APPLE_API_VERSION")):
        definition = re.search(r"^[ \t]*#[ \t]*define[ \t]+MKWVK_API_VERSION\b[^\n]*", variant, re.M)
        if definition is None:
            raise ValueError("cannot extract the host API version definition")
        code += definition.group().replace("MKWVK_API_VERSION", name, 1) + "\n"
    code += 'static_assert(FIXTURE_NONAPPLE_API_VERSION == 21);\n'
    code += 'static_assert(sizeof(NonAppleHost) == 352);\n'
    for field, offset in HOST_ABI_BASE:
        code += f'static_assert(offsetof(NonAppleHost, {field}) == {offset});\n'
        code += f'static_assert(offsetof(AppleHost, {field}) == {offset});\n'
        code += f'static_assert(offsetof(MkwVkHost, {field}) == {offset});\n'
    code += r'''
template<class T> static void apple_abi() {
    if constexpr (requires(T host) { host.vulkan_library; host.vulkan_extensions; }) {
        static_assert(offsetof(T, vulkan_library) == 352);
        static_assert(offsetof(T, vulkan_extensions) == 360);
        static_assert(sizeof(T) == 368);
        static_assert(std::is_same_v<decltype(T::vulkan_library), void* (*)(void)>);
        static_assert(std::is_same_v<decltype(T::vulkan_extensions), int (*)(uint32_t*, const char* const**)>);
    } else {
        check(false, "Apple host has no Vulkan library/extension callbacks");
    }
}
#if defined(__APPLE__)
static_assert(sizeof(MkwVkHost) == sizeof(AppleHost) && MKWVK_API_VERSION == FIXTURE_APPLE_API_VERSION);
#else
static_assert(sizeof(MkwVkHost) == 352 && MKWVK_API_VERSION == 21);
#endif
static void abi_run() {
    const int before = failures;
    check(FIXTURE_APPLE_API_VERSION == 22, "Apple host API version is 22");
    apple_abi<AppleHost>();
    if (before == failures)
        std::printf("PASS: fixed non-Apple ABI: version 21, size %zu, all 44 named offsets; Apple version 22, size %zu, typed callbacks at 352/360\n",
                    sizeof(NonAppleHost), sizeof(AppleHost));
}
'''
    return code


def fixture(root):
    main = (root / "gpu/vk/vk_main.cpp").read_text()
    posix = (root / "host/posix/win32_posix.c").read_text()
    api = (root / "gpu/vk/vk_api.h").read_text()
    loader = (root / "gpu/vk/vk_loader.h").read_text()
    prefix = loader[loader.index("#if defined(__APPLE__)"):loader.index("#include <vulkan/vulkan.h>")] if "#if defined(__APPLE__)" in loader else ""
    code = select(prefix, True) + PRELUDE + abi_fixture(api)
    if "darwin_required_feature(" in main:
        code += "static_assert(VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PORTABILITY_SUBSET_FEATURES_KHR == 1000163000);\n"
    for platform in ("windows", "linux", "darwin"):
        code += instance_fixture(main, posix, platform)
        code += device_fixture(root, main, platform)
    code += darwin_pure(root, main)
    code += r'''
int main() {
    abi_run(); windows_instance::run(); linux_instance::run();
    windows_device::run(); linux_device::run();
    darwin_instance::run(); darwin_device::run(); darwin_pure::run();
    if (!failures) std::puts("PASS: Vulkan admission decisions; no library, SDL, device, window or surface opened");
    return failures != 0;
}
'''
    return code


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=ROOT)
    ap.add_argument("--cxx", default="clang++")
    ap.add_argument("--headers", type=Path, required=True)
    ap.add_argument("--arch", choices=("arm64", "x86_64"))
    args = ap.parse_args()
    if args.arch and sys.platform != "darwin":
        ap.error("--arch is a macOS-only cross-check")
    with tempfile.TemporaryDirectory(prefix="vk-admission-") as folder:
        tmp = Path(folder)
        src, exe = tmp / "admission.cpp", tmp / "admission.exe"
        src.write_text(fixture(args.source), encoding="utf-8")
        command = [args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-Wno-unused-variable",
                   "-Wno-missing-field-initializers", "-D_CRT_SECURE_NO_WARNINGS", "-I" + str(args.headers),
                   "-I" + str(args.source), str(src), "-o", str(exe)]
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
