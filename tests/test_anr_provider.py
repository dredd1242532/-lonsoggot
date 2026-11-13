"""Exercise ANR's actual request gate and loader against authored Win32/provider spies; no DLL or GPU is used."""
import argparse
from pathlib import Path
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}", "--headers", "{vulkan_headers}"]
ROOT = Path(__file__).resolve().parents[1]


def block(source, marker):
    start = source.index(marker)
    at = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[at] == "{") - (source[at] == "}")
        at += 1
    return source[start:at]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    parser.add_argument("--headers", type=Path, required=True)
    args = parser.parse_args()
    source = (ROOT / "gpu/vk/vk_anr.cpp").read_text(encoding="utf-8")
    for marker in ("void anr_fg_inputs(", "int anr_fg_frames("):
        body = block(source, marker).split("{", 1)[1].lstrip()
        if not body.startswith("if (!requested()) return"):
            raise SystemExit(marker + " must reject Off/Auto/Built-in before touching provider state")
    reason = source[source.index("enum Reason {"):source.index("#if defined(_WIN32) && defined(_WIN64)", source.index("enum Reason {"))]
    state = source[source.index("using Result = anr_abi::Result;"):source.index("State& state()")]
    fixture = r'''#define VK_NO_PROTOTYPES
#include <vulkan/vulkan_core.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include "anr_parameters.h"
using HMODULE = void*;
using VmaAllocation = void*;
constexpr unsigned INVALID_FILE_ATTRIBUTES = ~0u;
constexpr unsigned LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR = 0x100, LOAD_LIBRARY_SEARCH_DEFAULT_DIRS = 0x1000;
struct { int framegen = 0, framegen_by = 0; } opts;
template<class... T> void logf(const char*, T...) {}
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); std::exit(1); } } while (0)
''' + reason + state + r'''
struct Path {
    std::wstring value;
    bool empty() const { return value.empty(); }
    const wchar_t* c_str() const { return value.c_str(); }
    Path operator/(const wchar_t* name) const { return {value + L"/" + name}; }
};
Path package_dir() { return {L"authored-anr"}; }
int loads = 0, initializations = 0, populations = 0;
bool have_module = true, corrupt_parameters = false;
std::wstring missing_file;
std::string missing_export;
Result init_result = anr_abi::success, populate_result = anr_abi::success;
int provider_available = 1;
unsigned provider_most = 3;
State* current = nullptr;
struct { VkInstance instance = VK_NULL_HANDLE; VkPhysicalDevice physical = VK_NULL_HANDLE; VkDevice device = VK_NULL_HANDLE; } dev;
PFN_vkGetInstanceProcAddr instance_proc() { return nullptr; }
PFN_vkGetDeviceProcAddr vkGetDeviceProcAddr = nullptr;
unsigned GetFileAttributesW(const wchar_t* file) {
    return !missing_file.empty() && std::wstring(file).ends_with(missing_file) ? INVALID_FILE_ATTRIBUTES : 0;
}
unsigned GetLastError() { return 126; }
HMODULE LoadLibraryExW(const wchar_t* file, void* reserved, unsigned flags) {
    CHECK(std::wstring(file).ends_with(L"/nvngx_dlssg.dll") && reserved == nullptr);
    CHECK(flags == (LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS));
    ++loads; return have_module ? reinterpret_cast<HMODULE>(1) : nullptr;
}
Result init(unsigned long long app, const wchar_t* data, VkInstance, VkPhysicalDevice, VkDevice,
            PFN_vkGetInstanceProcAddr, PFN_vkGetDeviceProcAddr, unsigned version, const void* extra) {
    CHECK(app != 0 && data && *data && version == 0x15 && extra == nullptr);
    ++initializations; return init_result;
}
Result populate(void* params) {
    CHECK(current && params == current->parameters.raw());
    ++populations;
    current->parameters.set("FrameGeneration.Available", provider_available);
    current->parameters.set("DLSSG.MultiFrameCountMax", provider_most);
    if (corrupt_parameters) current->parameters.set(nullptr, 1u);
    return populate_result;
}
Result create(VkDevice, VkCommandBuffer, unsigned, void*, void**) { std::abort(); }
Result evaluate(VkCommandBuffer, const void*, const void*, void*) { std::abort(); }
Result release(void*) { std::abort(); }
Result shutdown(VkDevice) { std::abort(); }
void* GetProcAddress(HMODULE module, const char* name) {
    CHECK(module && name);
    if (missing_export == name) return nullptr;
    if (!std::strcmp(name, "NVSDK_NGX_VULKAN_Init_Ext2")) return reinterpret_cast<void*>(&init);
    if (!std::strcmp(name, "NVSDK_NGX_VULKAN_PopulateParameters_Impl")) return reinterpret_cast<void*>(&populate);
    if (!std::strcmp(name, "NVSDK_NGX_VULKAN_CreateFeature1")) return reinterpret_cast<void*>(&create);
    if (!std::strcmp(name, "NVSDK_NGX_VULKAN_EvaluateFeature")) return reinterpret_cast<void*>(&evaluate);
    if (!std::strcmp(name, "NVSDK_NGX_VULKAN_ReleaseFeature")) return reinterpret_cast<void*>(&release);
    if (!std::strcmp(name, "NVSDK_NGX_VULKAN_Shutdown1")) return reinterpret_cast<void*>(&shutdown);
    std::abort();
}
''' + block(source, "bool start(State& s) {") + r'''
void clean() {
    loads = initializations = populations = 0;
    have_module = true; corrupt_parameters = false;
    missing_file.clear(); missing_export.clear();
    init_result = populate_result = anr_abi::success;
    provider_available = 1; provider_most = 3;
}
void twice_rejected(State& state, Reason expected) {
    current = &state;
    CHECK(!start(state) && reason.load() == expected);
    const int load_count = loads, init_count = initializations, population_count = populations;
    CHECK(!start(state));
    CHECK(loads == load_count && initializations == init_count && populations == population_count);
}
int main() {
    for (int provider = 0; provider <= 2; provider++)
        for (int multiplier = 0; multiplier <= 8; multiplier++) {
            opts.framegen_by = provider; opts.framegen = multiplier;
            CHECK(requested() == (provider == 2 && multiplier >= 2));
            if (provider != 2 || multiplier < 2) {
                State s; current = &s;
                if (requested()) start(s);
                CHECK(loads == 0 && initializations == 0 && populations == 0);
            }
        }
    { clean(); State s; missing_file = L"path_table.json"; twice_rejected(s, Package); CHECK(loads == 0); }
    { clean(); State s; have_module = false; twice_rejected(s, Library); CHECK(loads == 1 && initializations == 0); }
    { clean(); State s; missing_export = "NVSDK_NGX_VULKAN_EvaluateFeature"; twice_rejected(s, Exports); CHECK(initializations == 0); }
    { clean(); State s; init_result = anr_abi::fail; twice_rejected(s, Initialization); CHECK(!s.initialized && populations == 0); }
    { clean(); State s; populate_result = anr_abi::fail; twice_rejected(s, Parameters); CHECK(s.initialized); }
    { clean(); State s; corrupt_parameters = true; twice_rejected(s, Parameters); CHECK(s.initialized); }
    { clean(); State s; provider_available = 0; twice_rejected(s, Initialization); CHECK(s.initialized); }
    { clean(); State s; current = &s; provider_most = 99;
      CHECK(start(s) && s.initialized && s.held && s.most == 7);
      CHECK(start(s) && loads == 1 && initializations == 1 && populations == 1); }
    std::puts("PASS: actual ANR request/loader: Off/Auto/Built-in do not load; package/exports/init/parameters failures stay refused");
}
'''
    with tempfile.TemporaryDirectory(prefix="anr-provider-") as folder:
        cpp, exe = Path(folder) / "fixture.cpp", Path(folder) / "fixture.exe"
        cpp.write_text(fixture, encoding="utf-8")
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-pthread",
                        "-I" + str(args.headers), "-I" + str(ROOT / "gpu/vk"), str(cpp), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True, timeout=15)


if __name__ == "__main__":
    main()
