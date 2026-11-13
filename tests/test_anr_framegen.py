"""Run the production frame-maker accounting and provider menu without a GPU or provider DLL."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]
ROOT = Path(__file__).resolve().parents[1]


def block(source, marker):
    start = source.index(marker)
    at = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[at] == "{") - (source[at] == "}")
        at += 1
    return source[start:at]


def provider_gates(dlss, fsr):
    return r'''#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <initializer_list>
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); std::exit(1); } } while (0)
struct { int framegen_by = 0; } opts;
namespace dlss_spy {
int force = -1, dlss_allowed = 0, fg_env_calls = 0, dlss_env_calls = 0, possible_calls = 0;
int env_switch(const char* name) {
    if (!std::strcmp(name, "RX_VK_DLSS_FG")) { ++fg_env_calls; return force; }
    CHECK(!std::strcmp(name, "RX_VK_DLSS")); ++dlss_env_calls; return dlss_allowed;
}
''' + block(dlss, "bool fg_possible() {").replace("bool fg_possible()", "bool underlying_possible()") + r'''
bool fg_possible() { ++possible_calls; return underlying_possible(); }
''' + block(dlss, "bool fg_asked() {") + r'''
}
namespace fsr_spy {
unsigned asked_value = 0;
int asked_calls = 0, capable_calls = 0;
bool capable_value = false;
unsigned asked() { ++asked_calls; return asked_value; }
bool capable() { ++capable_calls; return capable_value; }
''' + block(fsr, "uint32_t frames_wanted() {") + r'''
}
int main(int argc, char** argv) {
    CHECK(argc == 3);
    const int force = std::atoi(argv[1]);
    const bool capable = std::atoi(argv[2]) != 0;
    const bool possible = force != 0 && capable;
    CHECK(force >= -1 && force <= 1);
    dlss_spy::force = force;
    dlss_spy::dlss_allowed = int(capable);
    fsr_spy::asked_value = force > 0 ? 1u : 0u;
    for (int repeat = 0; repeat < 3; repeat++)
        for (int provider = 0; provider <= 2; provider++) {
            opts.framegen_by = provider;
            dlss_spy::possible_calls = 0;
            CHECK(dlss_spy::fg_asked() == (provider != 2 && possible && (force > 0 || provider == 0)));
            CHECK(dlss_spy::possible_calls == int(provider != 2));
            fsr_spy::capable_value = capable;
            fsr_spy::asked_calls = fsr_spy::capable_calls = 0;
            CHECK(fsr_spy::frames_wanted() == unsigned(provider != 2 && force > 0 && capable));
            CHECK(fsr_spy::asked_calls == int(provider != 2));
            CHECK(fsr_spy::capable_calls == int(provider != 2 && force > 0));
        }
    CHECK(dlss_spy::fg_env_calls == 2 && dlss_spy::dlss_env_calls == int(force != 0));
    std::printf("PASS: ANR excludes forced DLSS/FSR before probing; prior overrides preserved (force=%d capable=%d)\n", force, capable);
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    menu = (ROOT / "gpu/vk/vk_menu.cpp").read_text(encoding="utf-8")
    framegen = (ROOT / "gpu/vk/vk_framegen.cpp").read_text(encoding="utf-8")
    options = block((ROOT / "gpu/vk/vk_menu.h").read_text(encoding="utf-8"), "struct Options {") + ";"
    maker = block((ROOT / "gpu/vk/vk_framegen.h").read_text(encoding="utf-8"), "enum class FgMaker {") + ";"
    skips = block((ROOT / "gpu/vk/vk_dlss.h").read_text(encoding="utf-8"), "enum FgSkip {") + ";"
    dlss = (ROOT / "gpu/vk/vk_dlss.cpp").read_text(encoding="utf-8")
    fsr = (ROOT / "gpu/vk/vk_fsr3fi.cpp").read_text(encoding="utf-8")
    replay = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    if not re.search(r'if\s*\(swapchain_framegen\(\)\s*<\s*2\s*\|\|\s*anr_fg_selected\(\)\)\s*dlss_fg_idle\(\);', replay):
        raise SystemExit("selecting ANR must let go of a previously active DLSS-G feature")
    if not re.search(r'"Frame generation by", &opts\.framegen_by, 0, 2, 1, tx_framegen_by,', menu):
        raise SystemExit("the existing provider row must retain Auto=0, Built-in=1 and add ANR=2")
    accounting = framegen[framegen.index("constexpr int kMakerSets ="):].split("\n} // namespace", 1)[0]
    functions = "\n".join(block(menu, marker) for marker in (
        "const char* framegen_maker_name() {", "const char* tx_framegen_by(int v) {",
        "const char* framegen_skip_text(int why, unsigned most) {", "const char* hn_framegen_by(int v, int variant) {",
        "const char* ini_key(const Row& r) {"))
    fixture = r'''#include <atomic>
#include <algorithm>
#include <cstdint>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); std::exit(1); } } while (0)
''' + options + "\nOptions opts;\n" + maker + "\n" + skips + r'''
int64_t clock_ns = 1000000;
int64_t now_ns() { return clock_ns; }
int hint_variant = -9;
const char* anr_fg_reason(int variant) {
    hint_variant = variant;
    return variant > 0 ? nullptr : "ANR unavailable: authored provider stub";
}
int dlss_fg_skip(unsigned* most) { *most = 1; return FG_SKIP_DEVICE; }
struct Row { const char* name; int* value; };
''' + accounting + "\n" + block(framegen, "FgMaker framegen_maker() {") + "\n" + functions + r'''
void fill(FgMaker maker) { for (int i = 0; i < kMakerSets; ++i) note_maker(maker); }
int main() {
    CHECK(opts.framegen == 0 && opts.framegen_by == 0);
    const Row provider{"Frame generation by", &opts.framegen_by};
    CHECK(!std::strcmp(ini_key(provider), "frame_generation_by"));
    CHECK(!std::strcmp(tx_framegen_by(0), "Auto"));
    CHECK(!std::strcmp(tx_framegen_by(1), "Built-in FG"));
    CHECK(!std::strcmp(tx_framegen_by(2), "ANR (experimental)"));
    CHECK(framegen_maker_name() == nullptr); // Selecting a provider never proves that it made a frame.
    opts.framegen = 2; opts.framegen_by = 2;
    fill(FgMaker::BuiltIn);
    CHECK(framegen_maker() == FgMaker::BuiltIn);
    CHECK(!std::strcmp(framegen_maker_name(), "Built-in FG"));
    CHECK(!std::strcmp(tx_framegen_by(2), "ANR: Built-in FG"));
    CHECK(std::strstr(hn_framegen_by(2, 0), "Experimental ANR") && hint_variant == -9);
    CHECK(std::strstr(hn_framegen_by(2, -1), "ANR unavailable") && hint_variant == -1);
    CHECK(hn_framegen_by(2, 1) != nullptr && hint_variant == 0);
    CHECK(hn_framegen_by(2, 2) == nullptr && hint_variant == 1);
    fill(FgMaker::Anr);
    CHECK(framegen_maker() == FgMaker::Anr);
    CHECK(!std::strcmp(framegen_maker_name(), "ANR FG") && !std::strcmp(tx_framegen_by(2), "ANR FG"));
    clock_ns += 500000001;
    CHECK(framegen_maker() == FgMaker::None && framegen_maker_name() == nullptr);
    CHECK(!std::strcmp(tx_framegen_by(2), "ANR (experimental)"));
    fill(FgMaker::Dlss); opts.framegen_by = 0;
    CHECK(!std::strcmp(tx_framegen_by(0), "Auto: DLSS FG"));
    fill(FgMaker::Fsr3);
    CHECK(!std::strcmp(framegen_maker_name(), "FSR 3.1 FG"));
    fill(FgMaker::None);
    CHECK(framegen_maker() == FgMaker::None && framegen_maker_name() == nullptr);
    opts.framegen = 0; fill(FgMaker::Anr);
    CHECK(framegen_maker_name() == nullptr && !std::strcmp(tx_framegen_by(2), "ANR (experimental)"));
    std::puts("PASS: provider defaults/key and Auto/Built-in persist; ANR labels only actual frames and explains fallback");
}
'''
    with tempfile.TemporaryDirectory(prefix="anr-framegen-menu-") as folder:
        cpp, exe = Path(folder) / "fixture.cpp", Path(folder) / "fixture.exe"
        cpp.write_text(fixture, encoding="utf-8")
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", str(cpp), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True, timeout=15)
        cpp.write_text(provider_gates(dlss, fsr), encoding="utf-8")
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", str(cpp), "-o", str(exe)], check=True)
        for force in (-1, 0, 1):
            for capable in (0, 1):
                subprocess.run([str(exe), str(force), str(capable)], check=True, timeout=15)


if __name__ == "__main__":
    main()
