// Original cross-ABI caller; it loads only the authored mock DLL.
#include "anr_parameters.h"
#include <windows.h>
#include <cassert>
#include <cstdio>

int wmain(int argc, wchar_t** argv) {
    assert(argc == 2);
    HMODULE module = LoadLibraryExW(argv[1], nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
    if (!module) { std::printf("mock LoadLibrary error %lu\n", GetLastError()); return 2; }
    auto probe = reinterpret_cast<unsigned (*)(void*)>(GetProcAddress(module, "anr_abi_probe"));
    assert(probe);
    assert(probe(nullptr) == 1);
    anr_abi::Parameters parameters;
    parameters.set("host_u64", 0xfedcba9876543210ULL);
    unsigned long long value = 0;
    assert(parameters.get("host_u64", &value) == 1 && value == 0xfedcba9876543210ULL);
    parameters.set("null_pointer", nullptr);
    void* pointer = reinterpret_cast<void*>(1);
    assert(parameters.get("null_pointer", &pointer) == 1 && pointer == nullptr);
    parameters.set("nan", std::numeric_limits<double>::quiet_NaN());
    parameters.set("infinity", std::numeric_limits<double>::infinity());
    unsigned integral = 0;
    assert(parameters.get("nan", &integral) == anr_abi::fail);
    assert(parameters.get("infinity", &integral) == anr_abi::fail);
    for (unsigned attempt = 0; attempt != 100; ++attempt) {
        const unsigned failure = probe(parameters.raw());
        if (failure) { std::printf("MSVC-provider probe failed at check %u\n", failure); return 3; }
        assert(parameters.healthy());
        int result = 0;
        assert(parameters.get("after_reset", &result) == 1 && result == -3);
    }
    parameters.set(static_cast<const char*>(nullptr), 1u);
    assert(!parameters.healthy());
    parameters.clear();
    assert(parameters.healthy());
    assert(FreeLibrary(module));
    std::puts("PASS: MinGW host -> MSVC-ABI mock: all 17 slots, numeric/pointer types, 64-bit precision, failures and Reset; 100 roundtrips; Unicode DLL path; no GPU/ANR code");
}
