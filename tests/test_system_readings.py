"""System counter and CSV: real parser, CPU deltas, unknowns, off path and exclusive UTF-8 output.

--source selects the production checkout, keeping this fixture. Pinned main first fails the compiled
level check (System is absent); it cannot supply the later reader because that feature is new.
"""
import argparse
import csv
import subprocess
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "{cxx}"]
ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    parser.add_argument("--source", type=Path, default=ROOT)
    args = parser.parse_args()
    vk = args.source.resolve() / "gpu" / "vk"
    with tempfile.TemporaryDirectory(prefix="system-readings-") as tmp:
        out = Path(tmp)
        level = out / "level.cpp"
        level.write_text('''#define _CRT_SECURE_NO_WARNINGS
#include "fps_stats.h"
#include <cstdio>
#include <cstring>
int main() {
    if (std::strcmp(mkwvk::fps_stats::level_name(6), "System")) {
        std::puts("FAIL: FPS counter value 6 is not System"); return 1;
    }
    std::puts("PASS: System follows the existing Dev level");
}
''', encoding="utf-8")
        flags = [args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-I", str(vk)]
        level_exe = out / "level.exe"
        subprocess.run([*flags, str(level), "-o", str(level_exe)], check=True)
        if not (vk / "system_readings.cpp").exists():
            subprocess.run([str(level_exe)], check=True)
        exe = out / "readings.exe"
        subprocess.run([*flags, str(ROOT / "tests/test_system_readings.cpp"), str(vk / "system_readings.cpp"),
                        "-o", str(exe)], check=True)
        menu = (vk / "vk_menu.cpp").read_text(encoding="utf-8")
        integration = menu[menu.index("namespace sr = system_readings;"):menu.index("/// The FPS counter's target frame time")]
        fixture = out / "integration.cpp"
        fixture.write_text(r'''#define _CRT_SECURE_NO_WARNINGS
#include "system_readings.h"
#include "fps_stats.h"
#include <cstdlib>
#include <cstring>
using namespace mkwvk;
static int clocks = 0, reads = 0, budgets = 0;
static uint64_t now = 1000000000;
namespace mkwvk::system_readings {
uint64_t tracked_clock() { ++clocks; return now; }
Sample tracked_read() { ++reads; Sample s; s.cpu_ns = now; return s; }
}
#define monotonic_ns tracked_clock
#define read_process tracked_read
static struct { int show_fps = 0; } opts;
static struct { bool memory_budget = false; } dev;
static bool vram_usage(uint64_t* used, uint64_t* budget) { ++budgets; *used = 1048576; *budget = 2097152; return true; }
static void logf(const char*, ...) {}
''' + integration + r'''
#undef monotonic_ns
#undef read_process
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL integration %d: %s\n", __LINE__, #x); return 1; } } while (0)
int main(int argc, char** argv) {
    CHECK(argc == 2);
    for (int level = 0; level <= fps_stats::kDev; ++level) {
        opts.show_fps = level;
        for (int frame = 0; frame < 100; ++frame) system_tick();
    }
    CHECK(clocks == 0 && reads == 0 && budgets == 0 && !system_file);
    opts.show_fps = fps_stats::kSystem;
    system_tick();
    CHECK(clocks == 1 && reads == 1 && budgets == 0);
    CHECK(!std::strcmp(system_text[0], "n/a") && !std::strcmp(system_text[3], "n/a") && !std::strcmp(system_text[4], "n/a"));
    now += 1000000000; dev.memory_budget = true; system_tick();
    CHECK(budgets == 1 && !std::strcmp(system_text[0], "100.00%"));
    CHECK(!std::strcmp(system_text[3], "1 MiB") && !std::strcmp(system_text[4], "2 MiB"));
    opts.show_fps = fps_stats::kOff;
    const int old_clocks = clocks;
    system_tick();
    CHECK(clocks == old_clocks);
    system_file = sr::create_csv(argv[1]);
    CHECK(system_file); sr::csv_header(system_file);
    system_tick();
    CHECK(reads == 3 && budgets == 2); // File-only capture with FPS Off.
    CHECK(std::fclose(system_file) == 0); system_file = nullptr;
    std::puts("PASS: production menu integration, six existing levels do no monitor work; file-only capture works");
}
''', encoding="utf-8")
        integration_exe = out / "integration.exe"
        subprocess.run([*flags, str(fixture), str(vk / "system_readings.cpp"), "-o", str(integration_exe)], check=True)
        subprocess.run([str(level_exe)], check=True)
        subprocess.run([str(integration_exe), str(out / "file-only.csv")], check=True)
        path = out / "système.csv"
        subprocess.run([str(exe), str(path)], check=True)
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "# longshot_system,1"
        rows = list(csv.DictReader(lines[1:]))
        assert list(rows[0]) == ["monotonic_ns", "cpu_process_pct", "rss_bytes", "ram_available_bytes",
                                 "vram_used_bytes", "vram_budget_bytes"]
        assert rows[0] == dict.fromkeys(list(rows[0])[1:], "na") | {"monotonic_ns": "1234567890"}
        assert list(rows[1].values()) == ["1234567890", "250.01", "3145728", "0", "123", "456"]
        assert len(rows) == 2, "exclusive creation changed the completed capture"
    print("PASS: CSV units, unknowns, decimal dot, UTF-8 path and no overwrite")


if __name__ == "__main__":
    main()
