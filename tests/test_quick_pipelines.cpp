// Quick pipelines (gpu/vk/quick_pipe.h, vk_draw.cpp quick_pipeline): the decision without a GPU or the game. quick_pipe.h's
// rules with fake handles, then vk_draw.cpp's own quick_budget_ms, pipe_wait_budget_ms and quick_pipeline, cut out by
// tests/test_quick_pipelines.py into quick_cut.inc and built against the stand-ins below.
// argv[1]: "amd" or "nvidia" (the device's driver); argv[2]: what RX_VK_QUICK_MS is expected to come to.
#include <atomic>
#include <chrono>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

#include "quick_pipe.h"

namespace {
int failures = 0;
void check(bool ok, const char* what) {
    if (!ok) std::printf("FAIL: %s\n", what), failures++;
}
}  // namespace

// ---- stand-ins for what the cut code uses ----
using VkPipeline = uintptr_t;
constexpr VkPipeline VK_NULL_HANDLE = 0;
using VkPrimitiveTopology = int;
enum VkDriverId { VK_DRIVER_ID_AMD_PROPRIETARY = 1, VK_DRIVER_ID_NVIDIA_PROPRIETARY = 4 };
struct {
    VkDriverId driver_id = VK_DRIVER_ID_NVIDIA_PROPRIETARY;
    struct { bool pipelineCreationCacheControl = true; } vk13;
    void* device = nullptr;
} dev;
struct Program { uint64_t key = 0x1234; };
struct NvnState {};
struct Targets {};
std::vector<std::string> logs;
void logf(const char* f, ...) {
    char line[512];
    va_list a;
    va_start(a, f);
    std::vsnprintf(line, sizeof line, f, a);
    va_end(a);
    logs.push_back(line);
}
uint64_t frame_no = 1;
uint64_t texture_frame_no() { return frame_no; }
thread_local bool ask_start_first = false;
thread_local bool quick_build = false;
// (build_graphics: what each call was, and what it answers: the caches' full one, or a quick one)
struct Call { bool asked, quick, start_first; };
std::vector<Call> calls;
bool cache_has = false;
VkPipeline next_handle = 100;
VkPipeline build_graphics(const Program&, const NvnState&, const Targets&, VkPrimitiveTopology, int part = -1, bool* cold = nullptr) {
    calls.push_back({cold != nullptr, quick_build, ask_start_first});
    check(part == -1, "a whole pipeline is asked for");
    if (cold) return cache_has ? next_handle++ : (*cold = true, VK_NULL_HANDLE);
    return next_handle++;
}
std::vector<VkPipeline> destroyed;
void vkDestroyPipeline(void*, VkPipeline pl, void*) { destroyed.push_back(pl); }

#include "quick_cut.inc"   // (PipeEntry, quick_budget_ms, pipe_wait_budget_ms, quick_pipeline: vk_draw.cpp's own)

namespace {

struct FakeEntry {
    std::atomic<bool> ready{false};
    std::atomic<int> pipeline{0};
    std::atomic<uint8_t> quick{quick_pipe::kNone};
};

void header_rules() {
    bool bad = false;
    check(quick_pipe::quick_ms(nullptr, true, &bad) == quick_pipe::kQuickDefaultMs && !bad, "unset: on for AMD's driver");
    check(quick_pipe::quick_ms(nullptr, false, &bad) == 0 && !bad, "unset: off elsewhere");
    check(quick_pipe::quick_ms("250", false, &bad) == 250 && !bad, "a number sets it anywhere");
    check(quick_pipe::quick_ms(" 0 ", true, &bad) == 0 && !bad, "0 turns it off on AMD");
    check(quick_pipe::quick_ms("-5", true, &bad) == 0 && !bad, "a negative number is off");
    for (const char* v : {"", "8ms", "off", "nan", "inf", "1e999"}) {
        check(quick_pipe::quick_ms(v, true, &bad) == quick_pipe::kQuickDefaultMs && bad, "a non-number is the default, said");
        check(quick_pipe::wait_ms(v, &bad) == 0 && bad, "a non-number wait is off, said");
    }
    check(quick_pipe::wait_ms(nullptr, &bad) == 0 && !bad, "the brief wait is off unless set");
    check(quick_pipe::wait_ms("100", &bad) == 100 && !bad, "the brief wait's budget");
    check(quick_pipe::slow_ms("8000", &bad) == 8000 && !bad, "the slow-build test");
    check(quick_pipe::slow_ms("999999", &bad) == 60000, "the slow-build test is at most a minute");
    check(quick_pipe::slow_ms("8s", &bad) == 0 && bad, "the slow-build test is strict");

    const auto none = [] { return 0; };
    int built = 0, asked = 0;
    std::vector<int> gone;
    double clock = 0;
    const auto ask_none = [&] { return asked++, 0; };
    const auto build_slow = [&] { return clock += 400, ++built, 50 + built; };   // (each quick build: 400 ms)
    const auto destroy = [&](int pl) { gone.push_back(pl); };
    const auto now = [&] { return clock; };
    const auto poll = [&] { clock += 1; };
    quick_pipe::FrameBudget budget;
    // (budget per frame: 1000 ms, 400 ms a build. Three skippable draws get one, the fourth in the frame is left out)
    FakeEntry e[6];
    int got = 0;
    for (int i = 0; i < 4; i++)
        got += quick_pipe::take(e[i], false, 1000, budget, 7, ask_none, build_slow, destroy, now, poll) == quick_pipe::Got::kQuick;
    check(got == 3 && built == 3, "a frame's quick builds stop once its budget is spent");
    check(e[3].quick.load() == quick_pipe::kNone, "an entry left out for the budget can be given one in a later frame");
    check(quick_pipe::take(e[4], true, 1000, budget, 7, ask_none, build_slow, destroy, now, poll) == quick_pipe::Got::kQuick,
          "a draw that cannot be left out is not held to the budget");
    check(quick_pipe::take(e[3], false, 1000, budget, 8, ask_none, build_slow, destroy, now, poll) == quick_pipe::Got::kQuick,
          "the next frame has its budget again");
    check(quick_pipe::take(e[5], false, 0, budget, 9, ask_none, build_slow, destroy, now, poll) == quick_pipe::Got::kNothing,
          "a budget of 0 is off");
    // (cache first: a full one the caches have is taken, and nothing is built)
    FakeEntry c;
    const int before = built;
    check(quick_pipe::take(c, false, 1000, budget, 10, [] { return 9; }, build_slow, destroy, now, poll) == quick_pipe::Got::kCached &&
              c.pipeline.load() == 9 && built == before,
          "the caches' full pipeline first, without a quick build");
    // (once an entry: a second draw neither asks nor builds again)
    asked = 0;
    check(quick_pipe::take(c, false, 1000, budget, 10, ask_none, build_slow, destroy, now, poll) == quick_pipe::Got::kOther &&
              asked == 0 && built == before,
          "an entry is given one once");
    // (the full one came first: kept, the quick one destroyed, no draw has seen it)
    FakeEntry f;
    const auto build_racing = [&] { f.pipeline.store(1); f.ready.store(true); return 77; };
    check(quick_pipe::take(f, false, 1000, budget, 11, ask_none, build_racing, destroy, now, poll) == quick_pipe::Got::kOther &&
              f.pipeline.load() == 1 && gone == std::vector<int>{77},
          "a quick one that loses to the full build is destroyed, the full one kept");
    // (the full build replaces the quick one: the worker's store is unconditional; vk_draw.cpp is checked to do so)
    FakeEntry r;
    quick_pipe::take(r, false, 1000, budget, 12, ask_none, build_slow, destroy, now, poll);
    r.pipeline.store(1), r.ready.store(true);
    check(r.pipeline.load() == 1, "the full build's pipeline replaces the quick one");
    // (another thread making one: a draw that may be left out does not wait; one that may not waits for it, and not when
    // the other attempt is over without one)
    FakeEntry m;
    m.quick.store(quick_pipe::kMaking);
    double t0 = clock;
    check(quick_pipe::take(m, false, 1000, budget, 13, none, none, destroy, now, poll) == quick_pipe::Got::kNothing && clock == t0,
          "a skippable draw does not wait for another thread's quick one");
    int polls = 0;
    const auto poll_then_made = [&] { if (++polls == 3) m.pipeline.store(5); };
    check(quick_pipe::take(m, true, 1000, budget, 13, none, none, destroy, now, poll_then_made) == quick_pipe::Got::kOther &&
              polls == 3,
          "a draw that must wait takes another thread's quick one once made");
    FakeEntry t;
    t.quick.store(quick_pipe::kTried);
    polls = 0;
    check(quick_pipe::take(t, true, 1000, budget, 13, none, none, destroy, now, [&] { polls++; }) == quick_pipe::Got::kNothing &&
              polls == 0,
          "an attempt that is over without one is not waited for");

    // (the brief wait: hurried, waited at most what is left of the frame's budget, the time taken off it)
    std::atomic<bool> ready{false};
    quick_pipe::FrameBudget wb;
    int hurried = 0;
    double asked_ms = -1;
    const auto wait_none = [&](double ms) { asked_ms = ms, clock += ms; return false; };
    check(!quick_pipe::brief(ready, 0, wb, 1, [&] { hurried++; }, wait_none, now) && hurried == 0, "the brief wait is off at 0");
    check(!quick_pipe::brief(ready, 100, wb, 1, [&] { hurried++; }, wait_none, now) && hurried == 1 && asked_ms == 100,
          "the brief wait hurries the build and waits for the frame's budget");
    asked_ms = -1;
    check(!quick_pipe::brief(ready, 100, wb, 1, [&] { hurried++; }, wait_none, now) && hurried == 1 && asked_ms == -1,
          "a frame's brief waits stop once its budget is spent");
    check(quick_pipe::brief(ready, 100, wb, 2, [&] { hurried++; }, [&](double) { clock += 3; return true; }, now) && hurried == 2,
          "the next frame waits again, and a build done in time is drawn");
    check(wb.used == 3, "only the time waited comes off the budget");
}

// vk_draw.cpp's own: the caches asked first without compiling and without the quick flag; a quick build only after, with it,
// and the flag cleared after; nothing asked where the driver cannot be asked without compiling
void cut_code(double want_ms) {
    PipeEntry e;
    Program p;
    NvnState s;
    Targets at;
    check(quick_budget_ms() == want_ms, "RX_VK_QUICK_MS comes to what it should on this driver");
    if (want_ms <= 0) {
        check(!quick_pipeline(e, p, s, at, 0, true) && calls.empty(), "off: nothing asked, nothing built");
        return;
    }
    cache_has = true;
    check(quick_pipeline(e, p, s, at, 0, false) && e.pipeline.load() == 100 && !e.ready.load(), "from the caches, not ready");
    check(calls.size() == 1 && calls[0].asked && !calls[0].quick && calls[0].start_first, "asked of the caches without compiling");
    check(!ask_start_first && !quick_build, "the thread's flags are left as they were");
    PipeEntry q;
    cache_has = false, calls.clear();
    check(quick_pipeline(q, p, s, at, 0, false) && q.pipeline.load() != 0 && !q.ready.load(), "a quick one, not ready");
    check(calls.size() == 2 && calls[0].asked && !calls[0].quick && !calls[1].asked && calls[1].quick && !calls[1].start_first,
          "the caches first, then a quick build (no optimizer, no cache: build_graphics's quick_build)");
    check(!quick_build, "quick_build is cleared after the quick build");
    bool logged = false;
    for (const std::string& l : logs) logged |= l.find("quick pipeline (no optimizer)") != std::string::npos;
    check(logged, "a quick build is logged");
    PipeEntry n;
    dev.vk13.pipelineCreationCacheControl = false, calls.clear();
    check(quick_pipeline(n, p, s, at, 0, false) && calls.size() == 1 && calls[0].quick,
          "without pipelineCreationCacheControl the caches are not asked (the flag needs it)");
    dev.vk13.pipelineCreationCacheControl = true;
    check(pipe_wait_budget_ms() == quick_pipe::wait_ms(std::getenv("RX_VK_PIPE_WAIT_MS"), nullptr), "RX_VK_PIPE_WAIT_MS as read");
}

}  // namespace

int main(int argc, char** argv) {
    dev.driver_id = argc > 1 && !std::strcmp(argv[1], "amd") ? VK_DRIVER_ID_AMD_PROPRIETARY : VK_DRIVER_ID_NVIDIA_PROPRIETARY;
    const double want = argc > 2 ? std::atof(argv[2]) : 0;
    header_rules();
    cut_code(want);
    if (failures) return EXIT_FAILURE;
    std::printf("PASS (%s, RX_VK_QUICK_MS %s, budget %.0f ms): quick pipelines' decision\n", argc > 1 ? argv[1] : "nvidia",
                std::getenv("RX_VK_QUICK_MS") ? std::getenv("RX_VK_QUICK_MS") : "unset", want);
    return EXIT_SUCCESS;
}
