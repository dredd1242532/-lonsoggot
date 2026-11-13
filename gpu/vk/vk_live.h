// (longshot) Switches that can change while the game runs, for an A/B in one session: the same scene, the same warm caches and
// no restart between the halves (scripts/longshot_ctl.py ab).
//
// Each starts as its environment variable says (gpu/vk/compat/longshot_env.h's rule). The control bridge asks for another
// value (longshot_ctl.py option set RX_VK_OCC_QUERIES 1): the Insert menu's settings list for the bridge (menu_setting) ends
// with these, tab "live", key env.<NAME>. They are not kept in longshot_options.ini and not shown in the menu. The present
// takes an ask at its next frame (live_tick, vk_replay.cpp's status_tick) and first logs the time since the last one: its
// frames, frame rate, 1% low, GPU time and draws. An ask for the value a switch has changes nothing and still ends that time,
// as a mark does (live_marks).
//
// A switch is listed here only once its reader takes it where a change is safe: where a piece of work begins (a command
// buffer, a frame), never inside one, with nothing made or sized at the start depending on it. Its reader says where.
#pragma once
#include <atomic>
#include <cstring>
#include "longshot_env.h"

enum LiveId { LIVE_OCC_QUERIES, LIVE_COUNT };
struct LiveSwitch {
    const char* env;
    const char* what;            // (for the bridge's listing: what on does)
    std::atomic<int> value;      // (what the readers take: written by the present's thread alone, live_tick)
    std::atomic<int> want;       // (what the bridge asked for)
    std::atomic<unsigned> asks;  // (the bridge's asks so far, each one ending the time measured)
};
inline LiveSwitch* live_switches() {
    static LiveSwitch s[LIVE_COUNT] = {
        // (vk_replay.cpp occ_queries_on: taken once a command buffer, before its dry pass)
        {"RX_VK_OCC_QUERIES", "the GPU's counts in samples-passed reports", (int)env_on("RX_VK_OCC_QUERIES"),
         (int)env_on("RX_VK_OCC_QUERIES"), 0u},
    };
    return s;
}
// (the marks: an ask that only ends the time measured, for a switch that needs a restart (longshot_ctl.py ab --restart).
// The bridge lists it after the switches, key live.mark)
inline std::atomic<unsigned> live_marks{0};
inline bool live_on(LiveId id) { return live_switches()[id].value.load(std::memory_order_relaxed) != 0; }
/// The switch whose variable is env; null for none
inline LiveSwitch* live_find(const char* env) {
    for (int i = 0; i < LIVE_COUNT; i++)
        if (!strcmp(live_switches()[i].env, env)) return &live_switches()[i];
    return nullptr;
}
