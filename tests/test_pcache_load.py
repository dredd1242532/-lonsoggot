"""Whether the pipeline cache's file is read at the start (gpu/vk/pcache_load.h, used by vk_draw.cpp's pcache_load_off), issue
388: not read by default on a Linux PC with NVIDIA's driver, 20 GB of RAM or less and programs loaded at their create calls,
where the driver's own shader cache is not nearly empty; read as before everywhere else; RX_VK_PCACHE_LOAD says either way.
Compile and run the rule, and check vk_draw.cpp decides through it. No game or GPU is needed.
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]

TEST = r'''
#include "pcache_load.h"
#include <cstdio>
#include <initializer_list>
using namespace pcache_load;
static int fails;
#define NEED(c, what) do { if (!(c)) { fails++; std::printf("FAIL: %s\n", what); } } while (0)
// (the rule is constexpr: the table holds at build time too)
static_assert(!choose(-1, false, true, true, 15757, 1200).load, "a 16 GB Linux NVIDIA PC with a warm driver cache reads the file");
static_assert(choose(-1, false, true, true, 32000, 1200).load, "a 32 GB PC does not read the file");
int main() {
    const uint32_t pc16 = 15757;   // (a 16 GB PC: what it reports)
    // the small PC: not read
    Choice c = choose(-1, false, true, true, pc16, 1200);
    NEED(!c.load && c.why == Why::kSmallPc, "16 GB, Linux NVIDIA, create, warm driver cache: not read");
    NEED(!choose(-1, false, true, true, kSmallRamMb, kWarmMb).load, "at the limits: not read");
    // read as before everywhere else
    NEED(choose(-1, false, true, true, kSmallRamMb + 1, 1200).why == Why::kRam, "over 20 GB: read");
    NEED(choose(-1, false, true, true, 0, 1200).why == Why::kRam, "RAM not known: read");
    NEED(choose(-1, false, true, false, pc16, 1200).why == Why::kNotLinuxNvidia, "Windows, AMD, Intel: read");
    NEED(choose(-1, false, false, true, pc16, 1200).why == Why::kStart, "programs loaded before the title: read");
    NEED(choose(-1, true, true, true, pc16, 1200).why == Why::kPreparing, "a --prepare-shaders run: read");
    NEED(choose(-1, false, true, true, pc16, kWarmMb - 1).why == Why::kColdDriverCache, "a nearly empty driver cache: read");
    // the switch, whatever the rest says
    NEED(choose(1, false, true, true, pc16, 1200).load, "RX_VK_PCACHE_LOAD=1 reads it on the small PC");
    NEED(!choose(0, false, false, false, 64000, 0).load, "RX_VK_PCACHE_LOAD=0 does not read it anywhere");
    NEED(!choose(0, true, true, true, pc16, 1200).load, "RX_VK_PCACHE_LOAD=0 holds in a preparing run too, as before");
    for (Why w : {Why::kSwitch, Why::kPreparing, Why::kStart, Why::kNotLinuxNvidia, Why::kRam, Why::kColdDriverCache, Why::kSmallPc})
        NEED(why_text(w)[0] != 0, "every reason has its words for the log");
    if (!fails) std::printf("ok\n");
    return fails != 0;
}
'''


def check_source():
    src = (ROOT / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    fn = src[src.index("bool pcache_load_off() {"):]
    fn = fn[:fn.index("\n}\n")]
    for needle, why in (
            ('#include "pcache_load.h"', "vk_draw.cpp does not include pcache_load.h"),
            ('getenv("RX_VK_PCACHE_LOAD")', "pcache_load_off no longer reads RX_VK_PCACHE_LOAD"),
            ("env_value_off(e)", "RX_VK_PCACHE_LOAD is not read by the switches' one rule"),
            ('env_on("LONGSHOT_PREPARE_ONLY")', "a --prepare-shaders run is not told apart"),
            ("programs_load_at_create()", "the load mode is not asked"),
            ("dev.props.vendorID == 0x10DE", "NVIDIA is not told apart"),
            ("pcache_load::choose(sw, preparing, at_create, linux_nvidia, ram_mb, cache_mb)", "pcache_load_off does not decide through the rule"),
            ("return !c.load;", "pcache_load_off does not return the rule's answer"),
            ("static const bool off", "the answer is not decided once a run")):
        if needle not in (src if needle.startswith("#include") else fn):
            raise SystemExit(why + " (looked for: " + needle + ")")
    # (the driver's folders are walked only where the rest of the rule would leave the file unread: a walk of a big cache at
    # every start of every PC is not wanted)
    if "const uint64_t cache_mb = small ? driver_cache_bytes(nullptr) >> 20 : 0;" not in fn:
        raise SystemExit("pcache_load_off walks the driver's cache folders where the rule does not need them")
    # (the rest of the pipeline cache still goes by the one answer: not saved when not read)
    init = src[src.index("void pcache_init() {"):]
    init = init[:init.index("\n}\n")]
    if "const bool off = pcache_load_off();" not in init or "pcache_no_save = off;" not in init:
        raise SystemExit("pcache_init no longer takes pcache_load_off's answer for reading and saving")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="pcache-load-") as tmp:
        cpp, exe = Path(tmp) / "pcache_load_test.cpp", Path(tmp) / "pcache_load_test"
        cpp.write_text(TEST, encoding="utf-8")
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "gpu/vk"), str(cpp), "-o", str(exe)],
                       check=True)
        subprocess.run([str(exe)], check=True)
    print("pipeline cache file: not read on a small Linux NVIDIA PC with programs loaded at create; read as before elsewhere")


if __name__ == "__main__":
    main()
