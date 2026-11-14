// SPDX-License-Identifier: GPL-3.0-or-later
// (longshot, issue 388) Whether the pipeline cache's file (cache/longshot_vk_pipelines.bin, 1.2 GB after a preparation) is read
// at the start (vk_draw.cpp pcache_init), where RX_VK_PCACHE_LOAD does not say. Nothing of Vulkan here:
// tests/test_pcache_load.py.
//
// Read, the file is handed whole to vkCreatePipelineCache and the driver keeps its copy in the process's private memory for
// the session. On Linux the drivers keep their own shader caches too, with room for the preparation's pipelines
// (hos_main.c driver_cache_room: 10 GiB), and a program loaded at its create call finds its pipelines there as well. On a
// 16 GB PC that copy is what pushes the game into swap: measured on an RTX 3070 Ti, 16 GB, Linux, an eight-area tour with
// programs loaded at create, the game's private memory and its swap at the end 5.1 GB with the file and 4.3-4.4 GB
// without (two runs each), the game's peak (resident and swapped) 12.3-12.6 GB against 11.8-11.9 GB; fps and frames over
// 100 ms the same. What it costs: a load at create takes its pipelines from the driver's cache instead (median 1.8 ms
// against 4.9 ms, on the load workers, not the frame).
//
// So the file is not read, by default, only where all of these hold, and read as before everywhere else:
// - programs are loaded at their create calls (in a start that loads them all before the title, the precompile builds its
//   pipelines into the cache object anyway, which then holds about what the file did: no saving, and the precompile took
//   twice as long, 29 s against 15 s);
// - Linux and NVIDIA's driver (the one measured; Mesa keeps a disk cache too, untested);
// - 20 GB of RAM or less (16 GB PCs report about 15.7 GB);
// - the driver's own cache holds at least kWarmMb (a cache emptied, or never filled, would leave every pipeline to be
//   compiled in play: there the file is what answers);
// - not a --prepare-shaders run, which builds into the file and saves it.
// RX_VK_PCACHE_LOAD=0 or 1 (or off/on): not read or read, whatever these say.
#pragma once
#include <cstdint>

namespace pcache_load {

constexpr uint32_t kSmallRamMb = 20480;
constexpr uint64_t kWarmMb = 512;

enum class Why { kSwitch, kPreparing, kStart, kNotLinuxNvidia, kRam, kColdDriverCache, kSmallPc };
struct Choice {
    bool load;
    Why why;
};
/// sw: RX_VK_PCACHE_LOAD (-1 unset, 0 off, 1 on). ram_mb: the PC's RAM (0: not known). driver_cache_mb: what the driver's own
/// shader cache folders hold
constexpr Choice choose(int sw, bool preparing, bool load_at_create, bool linux_nvidia, uint32_t ram_mb,
                        uint64_t driver_cache_mb) {
    if (sw >= 0) return {sw == 1, Why::kSwitch};
    if (preparing) return {true, Why::kPreparing};
    if (!load_at_create) return {true, Why::kStart};
    if (!linux_nvidia) return {true, Why::kNotLinuxNvidia};
    if (!ram_mb || ram_mb > kSmallRamMb) return {true, Why::kRam};
    if (driver_cache_mb < kWarmMb) return {true, Why::kColdDriverCache};
    return {false, Why::kSmallPc};
}

/// For the log line: why the file was or was not read
constexpr const char* why_text(Why w) {
    switch (w) {
        case Why::kSwitch: return "RX_VK_PCACHE_LOAD";
        case Why::kPreparing: return "a --prepare-shaders run builds into it";
        case Why::kStart: return "programs are loaded before the title";
        case Why::kNotLinuxNvidia: return "not Linux with NVIDIA's driver";
        case Why::kRam: return "more than 20 GB of RAM";
        case Why::kColdDriverCache: return "the driver's own cache is nearly empty";
        case Why::kSmallPc: return "20 GB of RAM or less, programs loaded at create: the driver's own cache answers them";
    }
    return "";
}

} // namespace pcache_load
