// (longshot) Quick pipelines (RX_VK_QUICK_MS) and the brief pipeline wait (RX_VK_PIPE_WAIT_MS), for a draw whose full
// pipeline build is not done (vk_draw.cpp, draw and quick_pipeline). Ported from Mako, by sharkiix (8292d363): AMD's
// optimizer took 5-13 s a pipeline for Volvagia's intro, whose colour draws were left out (a black silhouette) or, waited
// for, froze it. Only pipelines: a shader still translating is waited for as before (#1573, RX_VK_TRANSLATE_WAIT_MS).
#pragma once
#include <atomic>
#include <cstdint>

#include "translate_wait.h"   // (the same strict reading of a number of milliseconds)

namespace quick_pipe {

/// RX_VK_QUICK_MS unset: this budget on AMD's proprietary driver, where the slow optimizer was measured; none elsewhere
constexpr double kQuickDefaultMs = 1000;
/// How long a draw that cannot be left out waits for a quick one another thread is making (a few ms as a rule)
constexpr int kOtherQuickPolls = 5000;   // (1 ms each)

/// A switch's milliseconds: its value when it reads as a number (0 or less: off), def when unset; a value that is not a
/// number ("8ms", "off", "") is def too, with *bad set so that the caller says so in the log
inline double read_ms(const char* env, double def, bool* bad) {
    if (bad) *bad = false;
    if (!env) return def;
    double ms = 0;
    if (!translate_wait::parse(env, ms)) {
        if (bad) *bad = true;
        return def;
    }
    return ms > 0 ? ms : 0;
}
/// RX_VK_QUICK_MS: each recording thread's quick builds a frame, in ms (0: none, the draw left out or waited for as before)
inline double quick_ms(const char* env, bool amd_proprietary, bool* bad) {
    return read_ms(env, amd_proprietary ? kQuickDefaultMs : 0, bad);
}
/// RX_VK_PIPE_WAIT_MS: each recording thread's waits a frame for skippable draws' pipelines, in ms (off unless set)
inline double wait_ms(const char* env, bool* bad) { return read_ms(env, 0, bad); }
/// RX_VK_TEST_SLOW_MS: a test that holds each full build that long, as AMD's optimizer on a cold cache (off unless set; at
/// most a minute)
inline int slow_ms(const char* env, bool* bad) {
    const double ms = read_ms(env, 0, bad);
    return ms > 60000 ? 60000 : (int)ms;
}

/// What is left of a budget in this frame, on one recording thread (a thread_local each)
struct FrameBudget {
    uint64_t frame = ~0ull;
    double used = 0;
    double left(uint64_t now, double total) {
        if (now != frame) frame = now, used = 0;
        return total - used;
    }
    void spend(double ms) { used += ms; }
};

/// The quick state of an entry: none tried, one being made, one tried (made or not)
enum : uint8_t { kNone = 0, kMaking = 1, kTried = 2 };
enum class Got { kNothing, kCached, kQuick, kOther };

/// The steps for a draw whose full pipeline is not ready and that has none to draw with (Entry: ready, pipeline and quick,
/// atomics). Once an entry: the full one asked of the caches without compiling (ask), else one built now without the
/// driver's optimizer and not into our pipeline cache (build); either set as the entry's pipeline, not ready, so the worker's
/// full build replaces it. A handle that loses to the full one, which came first, is destroyed (no draw has seen it).
/// must: a draw that cannot be left out: not held to the budget, and waits (poll) for a quick one another thread is making.
/// The time taken comes off the budget either way. kOther: another thread's (or the full build) is there to draw with
template <class Entry, class Ask, class Build, class Destroy, class Now, class Poll>
Got take(Entry& e, bool must, double budget_ms, FrameBudget& budget, uint64_t frame, Ask&& ask, Build&& build, Destroy&& destroy,
         Now&& now_ms, Poll&& poll) {
    using P = decltype(e.pipeline.load());
    const auto there = [&] { return e.pipeline.load(std::memory_order_acquire) != P{}; };
    if (budget_ms <= 0) return Got::kNothing;
    if (!must && budget.left(frame, budget_ms) <= 0) return there() ? Got::kOther : Got::kNothing;
    uint8_t none = kNone;
    if (!e.quick.compare_exchange_strong(none, kMaking, std::memory_order_acq_rel)) {
        for (int i = 0; must && i < kOtherQuickPolls && e.quick.load(std::memory_order_acquire) == kMaking && !there() &&
                        !e.ready.load(std::memory_order_acquire);
             i++)
            poll();
        return there() ? Got::kOther : Got::kNothing;
    }
    const double t0 = now_ms();
    Got got = Got::kCached;
    P pl = ask();
    if (pl == P{}) got = Got::kQuick, pl = build();
    budget.left(frame, budget_ms);
    budget.spend(now_ms() - t0);
    if (pl != P{}) {
        P empty{};
        if (!e.pipeline.compare_exchange_strong(empty, pl, std::memory_order_acq_rel)) destroy(pl), got = Got::kOther;
    } else {
        got = there() ? Got::kOther : Got::kNothing;
    }
    e.quick.store(kTried, std::memory_order_release);
    return got;
}

/// The brief wait: a skippable draw's queued full build moved to the queue's front (hurry) and waited for (wait, at most
/// the ms it is given) while this frame's budget lasts. The build is not run on this thread: one started cannot be stopped,
/// and on AMD's optimizer it took seconds. true: ready, draw it
template <class Hurry, class Wait, class Now>
bool brief(const std::atomic<bool>& ready, double budget_ms, FrameBudget& budget, uint64_t frame, Hurry&& hurry, Wait&& wait,
           Now&& now_ms) {
    if (ready.load(std::memory_order_acquire)) return true;
    if (budget_ms <= 0) return false;
    const double left = budget.left(frame, budget_ms);
    if (left <= 0) return false;
    const double t0 = now_ms();
    hurry();
    const bool ok = wait(left);
    budget.spend(now_ms() - t0);
    return ok;
}

}  // namespace quick_pipe
