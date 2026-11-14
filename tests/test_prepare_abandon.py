# SPDX-License-Identifier: GPL-3.0-or-later
"""A shader preparation that goes on from a pipeline the driver never finishes frees what it no longer needs.

precompile_run and predict_run (gpu/vk/vk_draw.cpp) leave their workers in the driver once nothing has finished for their
stall limit. Their state, the noted and predicted pipelines' notes (each a whole NvnState, up to 67,106 of them), was let go
then and never freed. Compiled from the real source, with a driver that never returns from one pipeline until the check
lets it, and notes that count themselves:

- a run that went on has freed at once every note no worker is building (the stragglers', and those of the programs not
  begun when the driver faulted early); the stuck worker's own note is still alive, and still valid when the driver returns;
- the state is freed once the last worker returns, and a run that got to its end frees it once;
- program_cache_borrow (gpu/vk/vk_progcache.cpp) lets go of every module and layout it made, also when its callback or the
  loader throws, and none twice.

No GPU or game. python -I -B tests/test_prepare_abandon.py --cxx clang++
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUN_ALL_ARGS = ["--cxx", "{cxx}"]


def block(text, marker):
    """The function or block that starts at marker, through its closing brace"""
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


RUN_HEAD = r'''
#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <memory>
#include <mutex>
#include <new>
#include <set>
#include <string>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "CHECK failed line %d: %s\n", __LINE__, #c); std::_Exit(7); } } while (0)
using VkPipeline = uintptr_t;
using VkPrimitiveTopology = int;
using VkResult = int;
constexpr VkPipeline VK_NULL_HANDLE = 0;
constexpr VkResult VK_SUCCESS = 0;
// (the run's state is freed when the buffer of its notes' list is: the list is moved into it and lives as long as it does)
static std::atomic<void*> watched{nullptr};
static std::atomic<int> watched_frees{0};
void* operator new(std::size_t n) {
    if (void* p = std::malloc(n ? n : 1)) return p;
    throw std::bad_alloc();
}
void operator delete(void* p) noexcept {
    void* w = p;
    if (p && watched.compare_exchange_strong(w, nullptr)) watched_frees++;
    std::free(p);
}
void operator delete(void* p, std::size_t) noexcept { operator delete(p); }
// (each note's state counts itself: alive holds every one not yet destroyed, and a second destroy fails)
static std::mutex alive_mu;
static std::set<const void*> alive;
struct NvnState {
    NvnState() { std::lock_guard lk(alive_mu); alive.insert(this); }
    NvnState(const NvnState&) : NvnState() {}
    NvnState& operator=(const NvnState&) { return *this; }
    ~NvnState() { std::lock_guard lk(alive_mu); CHECK(alive.erase(this) == 1); }
};
static size_t alive_n() { std::lock_guard lk(alive_mu); return alive.size(); }
static bool is_alive(const void* s) { std::lock_guard lk(alive_mu); return alive.count(s) != 0; }
struct Targets {};
struct PipeNote { uint64_t key, prog, variant; uint32_t topo; NvnState s; };
struct Program {
    uint64_t key = 0, variant = 0;
    bool compute = false, fallback = false, mesh = false;
    std::atomic<int> compute_state{0};
    VkPipeline compute_pipeline = VK_NULL_HANDLE;
};
struct VkComputePipelineCreateInfo { int unused = 0; };
struct PredictCounts { size_t vertex_types = 0, output_types = 0, variants_unfit = 0, notes_unfit = 0, refit = 0; };
struct ComputePlan {
    std::unordered_set<uint64_t> keys;
    bool all = false, none = true, learned_only = false;
    size_t shipped = 0, learned = 0, fresh = 0;
};
struct { uintptr_t device = 1; } dev;
thread_local bool driver_cache_only = false;
static bool env_on(const char* n) { const char* v = std::getenv(n); return v && *v && std::strcmp(v, "0"); }
static std::string logged;
static std::mutex log_mu;
void logf(const char* f, ...) {
    char b[2048];
    va_list a;
    va_start(a, f);
    std::vsnprintf(b, sizeof b, f, a);
    va_end(a);
    std::lock_guard lk(log_mu);
    logged += b;
    logged += "\n";
}
static unsigned cpus = 4;
unsigned usable_cpus() { return cpus; }
void Sleep(unsigned ms) { std::this_thread::sleep_for(std::chrono::milliseconds(ms)); }
bool prepare_stopped() { return false; }
static std::atomic<int> faults{0};
int driver_faults() { return faults.load(); }
void prepare_left_workers_note() {}
void prepare_progress_log(const char*, size_t, size_t, std::chrono::steady_clock::time_point, std::chrono::steady_clock::time_point&) {}
void ub_thread_off() {}
inline uint64_t mix(uint64_t h, uint64_t v) { return (h ^ v) * 0x100000001b3ull; }
std::string pipe_notes_path() { return std::string(std::getenv("FIXTURE_DIR")) + "/notes.bin"; }
std::string predict_pending_path() { return pipe_notes_path() + ".predicting"; }
// "stuck": the third pipeline built never returns until released (the last stragglers); "fault": every one after the first
// faults in the driver and never returns until released (stuck early, the bulk not begun)
static std::string mode;
static bool precompiling = false;
static int notes_n = 20, programs_n = 40, per_program = 3;
static std::atomic<int> builds{0};
static std::atomic<bool> released{false};
static std::mutex gate_mu;
static std::condition_variable gate;
static std::vector<const void*> stuck_states;
static std::set<uint64_t> stuck_programs;
static std::atomic<int> stuck_returned{0};
std::vector<std::unique_ptr<PipeNote>> pipe_notes_read() {
    std::vector<std::unique_ptr<PipeNote>> v;
    const int n = precompiling ? notes_n : 1;
    v.reserve(n);
    for (int i = 0; i < n; i++) v.push_back(std::make_unique<PipeNote>(PipeNote{(uint64_t)i + 1, (uint64_t)i + 1, 0, 3, {}}));
    if (precompiling) watched = v.data();
    return v;
}
std::vector<std::unique_ptr<PipeNote>> predict_pipelines(const std::vector<std::unique_ptr<PipeNote>>&, size_t* programs,
                                                         PredictCounts*) {
    std::vector<std::unique_ptr<PipeNote>> v;
    v.reserve((size_t)programs_n * per_program);
    for (int p = 1; p <= programs_n; p++)
        for (int k = 0; k < per_program; k++) v.push_back(std::make_unique<PipeNote>(PipeNote{0, (uint64_t)p, 0, 3, {}}));
    *programs = (size_t)programs_n;
    watched = v.data();
    return v;
}
VkPipeline build_graphics(const Program& p, const NvnState& s, const Targets&, VkPrimitiveTopology) {
    const int k = ++builds;
    if ((mode == "stuck" && k == 3) || (mode == "fault" && k > 1)) {
        if (mode == "fault") faults++;
        std::unique_lock lk(gate_mu);
        stuck_states.push_back(&s);
        stuck_programs.insert(p.key);
        gate.wait(lk, [] { return released.load(); });
        CHECK(is_alive(&s));   // (its note still there when the driver returns)
        stuck_returned++;
    }
    return (VkPipeline)(0x100 + k);
}
void vkDestroyPipeline(uintptr_t, VkPipeline, const void*) {}
Targets note_targets(const PipeNote&) { return {}; }
// predict_run's
void predicted_sample_write(const std::vector<std::unique_ptr<PipeNote>>& predicted) {
    for (const auto& n : predicted) CHECK(n && is_alive(&n->s));   // (read after the join: every note there)
}
void predicted_keys_write(std::vector<uint64_t>) {}
void driver_cache_log(const char*) {}
void program_cache_borrow(uint64_t key, const std::function<void(Program&)>& fn) {
    Program p;
    p.key = key;
    fn(p);
}
void ensure_layout(Program&) {}
uint64_t pipeline_key(const Program&, const NvnState& s, const Targets&, VkPrimitiveTopology) { return (uint64_t)(uintptr_t)&s; }
VkPrimitiveTopology pipeline_topology(const Program&, VkPrimitiveTopology t) { return t; }
// precompile_run's
bool programs_load_at_create() { return false; }
bool pcache_drop_stale() { return false; }
std::string precompile_built_path() { return pipe_notes_path() + ".built"; }
bool precompile_covers_notes() { return false; }
void precompile_warm(void (*)(size_t, size_t)) {}
void precompile_at_create() {}
void pcache_init() {}
std::mutex note_mu, state_mu;
FILE* note_file = nullptr;
FILE* state_file = nullptr;
void notes_open_locked(const std::vector<std::unique_ptr<PipeNote>>*) {}
void state_open_locked() {}
ComputePlan computes_plan(const char*, bool) { return {}; }
size_t program_cache_load_some(const std::unordered_set<uint64_t>&, const std::function<void(Program&)>&, bool, bool) { return 0; }
VkComputePipelineCreateInfo compute_pipeline_info(const Program&, bool) { return {}; }
VkPipeline compute_pipeline_cached(const Program&, bool) { return VK_NULL_HANDLE; }
VkResult cached_compute(const VkComputePipelineCreateInfo*, VkPipeline* pl) { return *pl = VK_NULL_HANDLE, -1; }
void fallback_compute_pipelines(Program&) {}
const Program* program_find(uint64_t key, uint64_t) {
    static Program programs[64];
    Program& p = programs[key % 64];
    p.key = key;
    return &p;
}
void precompile_built_write(size_t) {}
void predicted_check() {}
'''

RUN_MAIN = r'''
int main(int argc, char** argv) {
    CHECK(argc == 3);
    const std::string which = argv[1];
    mode = argv[2];
    precompiling = which == "precompile";
    if (mode == "fault") cpus = 2, programs_n = 100;   // (two workers stuck early: most programs not begun)
    if (precompiling) precompile_run(nullptr);
    else predict_run(nullptr);
    std::printf("%s", logged.c_str());
    if (mode == "complete") {
        CHECK(logged.find("nothing finished") == std::string::npos);
        CHECK(watched_frees == 1 && alive_n() == 0);   // (the state freed once, with every note)
        std::fflush(stdout);
        std::_Exit(0);
    }
    CHECK(logged.find("nothing finished for 1 s") != std::string::npos);
    std::vector<const void*> stuck;
    size_t programs = 0;
    {
        std::lock_guard lk(gate_mu);
        stuck = stuck_states, programs = stuck_programs.size();
    }
    CHECK(!stuck.empty() && (mode == "fault" || stuck.size() == 1));
    // (gone on: the state is still the stuck workers', and it keeps only what they are building; the rest is freed)
    CHECK(watched_frees == 0);
    CHECK(alive_n() == (precompiling ? stuck.size() : programs * (size_t)per_program));
    for (const void* s : stuck) CHECK(is_alive(s));
    {
        std::lock_guard lk(gate_mu);
        released = true;
    }
    gate.notify_all();
    // (the last worker to return frees the state)
    for (int i = 0; i < 1000 && (watched_frees == 0 || alive_n() != 0); i++) std::this_thread::sleep_for(std::chrono::milliseconds(10));
    CHECK(stuck_returned == (int)stuck.size());
    CHECK(watched_frees == 1 && alive_n() == 0);
    std::fflush(stdout);
    std::_Exit(0);
}
'''

BORROW_HEAD = r'''
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "CHECK failed line %d: %s\n", __LINE__, #c); std::_Exit(7); } } while (0)
using VkShaderModule = uintptr_t;
using VkPipelineLayout = uintptr_t;
using VkDescriptorSetLayout = uintptr_t;
constexpr uintptr_t VK_NULL_HANDLE = 0;
struct { uintptr_t device = 1; } dev;
struct Stage { VkShaderModule module = VK_NULL_HANDLE; };
struct Program {
    std::vector<Stage> stages;
    VkDescriptorSetLayout set_layout = VK_NULL_HANDLE;
    VkPipelineLayout layout = VK_NULL_HANDLE;
};
// (every handle made is live until destroyed; destroying one not live, or twice, fails)
static std::set<uintptr_t> live;
static uintptr_t next_handle = 0x100;
static uintptr_t make() { live.insert(++next_handle); return next_handle; }
static void destroy(uintptr_t h) { CHECK(h && live.erase(h) == 1); }
void vkDestroyShaderModule(uintptr_t, VkShaderModule m, const void*) { destroy(m); }
void vkDestroyPipelineLayout(uintptr_t, VkPipelineLayout l, const void*) { destroy(l); }
void vkDestroyDescriptorSetLayout(uintptr_t, VkDescriptorSetLayout l, const void*) { destroy(l); }
static std::string mode;
void program_cache_load(uint64_t, std::vector<std::unique_ptr<Program>>& variants, bool keep) {
    CHECK(keep);
    for (int v = 0; v < 3; v++) {
        if (mode == "load-throws" && v == 2) throw std::runtime_error("the loader failed partway");
        auto p = std::make_unique<Program>();
        p->stages = {{make()}, {make()}};
        p->set_layout = make(), p->layout = make();
        variants.push_back(std::move(p));
    }
}
'''

BORROW_MAIN = r'''
int main(int argc, char** argv) {
    CHECK(argc == 2);
    mode = argv[1];
    int called = 0;
    bool threw = false;
    try {
        program_cache_borrow(7, [&](Program& p) {
            CHECK(p.layout && live.count(p.layout));   // (each variant whole while fn has it)
            if (mode == "fn-throws" && ++called == 2) throw std::runtime_error("the callback failed");
            if (mode == "ok") called++;
        });
    } catch (const std::runtime_error&) {
        threw = true;
    }
    CHECK(threw == (mode != "ok"));
    CHECK(mode != "ok" || called == 3);
    CHECK(next_handle > 0x100 && live.empty());   // (every module and layout made was let go, none twice)
    return 0;
}
'''


def compile_exe(cxx, name, code, tmp):
    src = Path(tmp) / (name + ".cpp")
    exe = Path(tmp) / (name + (".exe" if os.name == "nt" else ""))
    src.write_text(code, encoding="utf-8")
    # (-D_CRT_SECURE_NO_WARNINGS: a clang that targets MSVC's C library calls fopen and getenv deprecated: test_prepare_stall.py)
    cmd = [cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-D_CRT_SECURE_NO_WARNINGS", str(src),
           "-o", str(exe)]
    if os.name != "nt":
        cmd.insert(1, "-pthread")
    subprocess.run(cmd, check=True, timeout=180)
    return exe


def check_runs(cxx, tmp):
    draw = (ROOT / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    start = draw.index("struct PipeEntry {")
    entry = draw[start:draw.index("\n};", start) + 3]
    globals_ = "std::unordered_map<uint64_t, std::unique_ptr<PipeEntry>> pipelines;\n"
    code = (RUN_HEAD + entry + "\n" + globals_ + block(draw, "void precompile_run(") + "\n" + block(draw, "void predict_run(") +
            RUN_MAIN)
    exe = compile_exe(cxx, "runs", code, tmp)
    env = dict(os.environ, RX_VK_PREDICT_STALL_S="1", RX_VK_PRECOMPILE_STALL_S="1", FIXTURE_DIR=tmp)
    for which, mode, what in (("predict", "complete", "a run that got to its end frees its state once"),
                              ("predict", "stuck", "gone on from the last stragglers: only the stuck program's notes stay"),
                              ("predict", "fault", "gone on after a fault early: the programs not begun are freed"),
                              ("precompile", "complete", "a run that got to its end frees its state once"),
                              ("precompile", "stuck", "gone on from a straggler: only its note stays")):
        r = subprocess.run([str(exe), which, mode], env=env, capture_output=True, text=True, timeout=60)
        if r.returncode:
            raise SystemExit("%s_run, %s: exit %d\n%s%s" % (which, what, r.returncode, r.stdout, r.stderr))
        print("PASS %s_run: %s" % (which, what), flush=True)


def check_borrow(cxx, tmp):
    progcache = (ROOT / "gpu/vk/vk_progcache.cpp").read_text(encoding="utf-8")
    exe = compile_exe(cxx, "borrow", BORROW_HEAD + block(progcache, "void program_cache_borrow(") + BORROW_MAIN, tmp)
    for mode, what in (("ok", "every variant's modules and layouts let go"),
                       ("fn-throws", "the callback throws: the current and the remaining variants let go"),
                       ("load-throws", "the loader throws partway: the variants it made let go")):
        r = subprocess.run([str(exe), mode], capture_output=True, text=True, timeout=60)
        if r.returncode:
            raise SystemExit("program_cache_borrow, %s: exit %d\n%s%s" % (what, r.returncode, r.stdout, r.stderr))
        print("PASS program_cache_borrow:", what, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="clang++")
    args = parser.parse_args()
    if not shutil.which(args.cxx):
        raise SystemExit("C++ compiler not found: " + args.cxx)
    with tempfile.TemporaryDirectory(prefix="prepare-abandon-") as tmp:
        check_borrow(args.cxx, tmp)
        check_runs(args.cxx, tmp)
    print("PASS: an abandoned preparation frees what no worker holds, and a borrow lets go of its handles")


if __name__ == "__main__":
    main()
