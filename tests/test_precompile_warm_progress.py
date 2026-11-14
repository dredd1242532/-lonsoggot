# SPDX-License-Identifier: GPL-3.0-or-later
"""The create-mode warm's compute pipelines count as progress (gpu/vk/vk_draw.cpp precompile_warm).

On a GPU with mesh shaders (the Steam Deck, RADV) programs load at create calls, and the preparation's noted pipelines are
built by precompile_warm: the noted graphics pipelines, then every kept compute program's pipeline. Compiled from the real
source, with stand-ins for the driver and the files:

- the progress it reports reaches the compute pipelines too: their count is in the total, and the count done moves as each
  is built (the start-up screen's bar and the builder's @@progress lines come from it);
- the --prepare-shaders run's watchdog (gpu/vk/vk_main.cpp prepare_watchdog), fed as that run feeds it, does not stop a
  compute pass that is slow but moving: one that stopped it ended the build with "the GPU driver stopped responding" and
  left longshot_prepare_skipped, so every start of the game after it prepared nothing.

python -I -B tests/test_precompile_warm_progress.py --cxx clang++
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUN_ALL_ARGS = ["--cxx", "{cxx}"]

NOTES = 4
COMPUTES = 80
COMPILE_MS = 60   # (on one worker: the compute pass takes about 5 s, past the watchdog's 2 s below)


def block(text, marker):
    """The function or block that starts at marker, through its closing brace"""
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


HEAD = r'''
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#else
static void Sleep(unsigned ms) { std::this_thread::sleep_for(std::chrono::milliseconds(ms)); }
#endif
#include "warm_bounded.h"
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "CHECK failed line %d: %s\n", __LINE__, #c); std::_Exit(7); } } while (0)
// (the watchdog's surroundings, as tests/test_prepare_stall.py has them)
struct MkwVkHost { void (*log)(const char*); void (*log_flush)(void); };
static void host_log(const char* s) { std::printf("[log] %s\n", s); std::fflush(stdout); }
static void host_flush() { std::fflush(stdout); }
static const MkwVkHost host_v{host_log, host_flush};
const MkwVkHost* host = &host_v;
int driver_faults() { return 0; }
bool prepare_left_workers() { return false; }
std::string skipped_path() { return std::getenv("FIXTURE_SKIPPED"); }
// (precompile_warm's surroundings: NOTES noted pipelines of two programs, COMPUTES kept compute programs, each compute
// pipeline COMPILE_MS in the "driver")
using VkPipeline = uintptr_t;
using VkResult = int;
using VkPrimitiveTopology = int;
constexpr VkResult VK_SUCCESS = 0;
constexpr VkPipeline VK_NULL_HANDLE = 0;
struct VkComputePipelineCreateInfo { int unused = 0; };
struct NvnState { int unused = 0; };
struct Targets {};
struct PipeNote { NvnState s; uint32_t topo = 3; };
struct NoteRef { uint64_t key = 0, prog = 0, variant = 0; int64_t at = 0; };
struct Stage { uintptr_t module = 1; };
struct Program { uint64_t key = 0, variant = 0; bool compute = false, failed = false; std::vector<Stage> stages{Stage{}}; };
struct { uintptr_t device = 1; } dev;
static int notes_n, computes_n, compile_ms;
static std::atomic<int> graphics_built{0}, computes_built{0};
static bool env_on(const char* n) { const char* v = std::getenv(n); return v && *v && std::strcmp(v, "0"); }
unsigned usable_cpus() { return 1; }
bool prepare_stopped() { return false; }
void ub_thread_off() {}
std::string pipe_notes_path() { return std::string(std::getenv("FIXTURE_DIR")) + "/longshot_pipelines.bin"; }
std::string precompile_built_path() { return pipe_notes_path() + ".built"; }
void precompile_built_write(size_t) {}
void pcache_init() {}
void pipeline_cache_save(bool) {}
std::vector<NoteRef> pipe_notes_refs() {
    std::vector<NoteRef> refs;
    for (int i = 0; i < notes_n; i++) refs.push_back({(uint64_t)(100 + i), (uint64_t)(1 + i % 2), 0, 16});
    return refs;
}
std::vector<uint64_t> program_cache_compute_keys() {
    std::vector<uint64_t> keys;
    for (int i = 0; i < computes_n; i++) keys.push_back(1000 + (uint64_t)i);
    return keys;
}
void program_cache_borrow(uint64_t key, const std::function<void(Program&)>& fn) {
    Program p;
    p.key = key, p.compute = key >= 1000;
    fn(p);
}
void ensure_layout(Program&) {}
bool pipe_note_at(FILE*, int64_t, PipeNote* out) { *out = PipeNote{}; return true; }
Targets note_targets(const PipeNote&) { return {}; }
VkPipeline build_graphics(const Program&, const NvnState&, const Targets&, VkPrimitiveTopology) { graphics_built++; return 0x10; }
void vkDestroyPipeline(uintptr_t, VkPipeline, const void*) {}
void prepare_progress_log(const char*, size_t, size_t, std::chrono::steady_clock::time_point, std::chrono::steady_clock::time_point&) {}
bool taau_key(uint64_t) { return false; }
VkComputePipelineCreateInfo compute_pipeline_info(const Program&, bool) { return {}; }
VkResult cached_compute(const VkComputePipelineCreateInfo*, VkPipeline* made) {
    Sleep((unsigned)compile_ms);
    computes_built++;
    *made = 0x20;
    return VK_SUCCESS;
}
'''

MAIN = r'''
static std::mutex seen_mu;
static std::vector<std::pair<size_t, size_t>> seen;   // (every progress call: done, total)
static bool watched;
static void progress(size_t done, size_t total) {
    CHECK(done <= total);
    {
        std::lock_guard lk(seen_mu);
        seen.push_back({done, total});
    }
    if (watched && total) prepare_moved(2, done, total);   // (as vk_main.cpp's --prepare-shaders progress feeds the watchdog)
}
int main(int argc, char** argv) {
    CHECK(argc == 5);
    watched = !std::strcmp(argv[1], "watch");
    notes_n = std::atoi(argv[2]), computes_n = std::atoi(argv[3]), compile_ms = std::atoi(argv[4]);
    if (watched) prepare_watchdog();
    precompile_warm(progress);
    CHECK(graphics_built == notes_n && computes_built == computes_n);   // (the stand-ins were reached: both passes ran whole)
    size_t moves_in_computes = 0, last_done = 0, last_total = 0;
    for (const auto& [done, total] : seen) {
        if (done >= (size_t)notes_n && done != last_done) moves_in_computes++;
        last_done = done, last_total = total;
    }
    std::printf("progress calls %zu, the last %zu of %zu, moves past the notes %zu\n", seen.size(), last_done, last_total,
                moves_in_computes);
    if (watched) return 0;
    CHECK(last_total == (size_t)(notes_n + computes_n));   // (the compute pipelines are in the total)
    CHECK(last_done == last_total);
    CHECK(moves_in_computes >= (size_t)computes_n / 4);   // (and the count moves while they are built)
    return 0;
}
'''


def build(cxx, tmp):
    main = (ROOT / "gpu/vk/vk_main.cpp").read_text(encoding="utf-8")
    watch = main[main.index("struct PrepareSeen {"):]
    watch = watch[:watch.index(block(watch, "void prepare_watchdog() {")) + len(block(watch, "void prepare_watchdog() {"))]
    internal = (ROOT / "gpu/vk/vk_internal.h").read_text(encoding="utf-8")
    draw = (ROOT / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    pieces = [block(main, "int64_t steady_ms() {"), "std::atomic<int64_t> last_log_ms{0};\n",
              internal[internal.index("enum LogLevel"):internal.index(";", internal.index("enum LogLevel")) + 1],
              block(main, "static void vlogf("), block(main, "void logf(const char* fmt, ...) {"), watch,
              block(draw, "void precompile_warm(void (*progress)(size_t done, size_t total)) {")]
    src = Path(tmp) / "warm_progress.cpp"
    exe = Path(tmp) / ("warm_progress" + (".exe" if os.name == "nt" else ""))
    src.write_text(HEAD + "\n".join(pieces) + MAIN, encoding="utf-8")
    cmd = [cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-D_CRT_SECURE_NO_WARNINGS",
           "-I", str(ROOT / "gpu/vk"), str(src), "-o", str(exe)]
    if os.name != "nt":
        cmd.insert(1, "-pthread")
    subprocess.run(cmd, check=True, timeout=180)
    return exe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="clang++")
    args = parser.parse_args()
    if not shutil.which(args.cxx):
        raise SystemExit("C++ compiler not found: " + args.cxx)
    with tempfile.TemporaryDirectory(prefix="warm-progress-") as tmp:
        exe = build(args.cxx, tmp)
        counts = [str(NOTES), str(COMPUTES), str(COMPILE_MS)]
        env = dict(os.environ, FIXTURE_DIR=tmp, FIXTURE_SKIPPED=str(Path(tmp) / "skipped"))
        r = subprocess.run([str(exe), "plain"] + counts, env=env, capture_output=True, text=True, timeout=120)
        if r.returncode:
            raise SystemExit("precompile_warm: the compute pipelines are not counted as progress (exit %d)\n%s%s"
                             % (r.returncode, r.stdout, r.stderr))
        print("PASS precompile_warm: the compute pipelines move the progress", flush=True)
        skipped = Path(tmp) / "skipped-watch"
        env = dict(env, RX_VK_PREPARE_STALL_S="2", RX_VK_PREPARE_STATUS_S="1", FIXTURE_SKIPPED=str(skipped))
        r = subprocess.run([str(exe), "watch"] + counts, env=env, capture_output=True, text=True, timeout=120)
        if r.returncode or skipped.exists() or "STOPPED" in r.stdout:
            raise SystemExit("precompile_warm: the watchdog stopped a compute pass that was moving (exit %d, "
                             "longshot_prepare_skipped %s)\n%s%s" % (r.returncode, "left" if skipped.exists() else "not left",
                                                                    r.stdout, r.stderr))
        print("PASS precompile_warm: the watchdog lets a slow, moving compute pass finish", flush=True)
    print("PASS: precompile_warm's progress")


if __name__ == "__main__":
    main()
