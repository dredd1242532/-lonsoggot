"""Restart/resume idle hooks perform only one demand load per entry.

Compile the real host state machines and the input/player hook bodies with
clock, option, atomic and call counters. --source-root also exercises the
pre-guard checkout: it must fail the counters, not fail to compile. Existing
restart/save/resume fixtures remain separate and unchanged.
"""
import argparse
import os
import subprocess
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cc", "gcc"]
ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#ifndef _WIN32
#define _POSIX_C_SOURCE 200809L
#endif
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "oot_autoload.h"
#include "oot_restart.h"
#include "vk_api.h"
#define HAS_DEMAND @HAS_DEMAND@

struct Counts { unsigned clocks, options, players, atomics, autoload, restart, requests, positions, flag_reads, flag_writes; } counts;
static const volatile void *demand_address;
static void atomic_touch(const volatile void *p, int write) {
    if (p == demand_address) {
        if (write) counts.flag_writes++; else counts.flag_reads++;
    } else counts.atomics++;
}
#define __atomic_load_n(p, order) (atomic_touch((p), 0), __atomic_load_n((p), (order)))
#define __atomic_store_n(p, value, order) (atomic_touch((p), 1), __atomic_store_n((p), (value), (order)))
#define __atomic_exchange_n(p, value, order) (atomic_touch((p), 1), __atomic_exchange_n((p), (value), (order)))
#define __atomic_compare_exchange_n(p, expected, value, weak, success, failure) \
    (atomic_touch((p), 1), __atomic_compare_exchange_n((p), (expected), (value), (weak), (success), (failure)))

static int64_t now = 1000000000;
static int want, skip, player, told, rearm_during_query;
const char *hos_user_dir;
int64_t hos_now_ns(void) { counts.clocks++; return now; }
int hos_oot_player_seen(void) { counts.players++; return player; }
int rx_option(int id, int otherwise) {
    counts.options++;
    if (id == HOS_OPT_RESTART_PLACE) {
#if HAS_DEMAND
        if (rearm_during_query) {
            rearm_during_query = 0;
            want = HOS_RESTART_SAVE;
            hos_oot_menu_work_arm();
            return 0;  /* a new menu request after this poll took the old answer */
        }
#endif
        return want;
    }
    if (id == HOS_OPT_SKIP_TITLE) return skip;
    if (id == 1) return 0;
    return otherwise;
}
void rx_option_set(int id, int value) {
    if (id == HOS_OPT_RESTART_PLACE) {
        told = value;
        if (value == HOS_RESTART_GO || value == HOS_RESTART_REFUSED) want = 0;
    }
}
void hos_hid_tap(const char *presses) { (void)presses; }
void hos_log(const char *fmt, ...) { (void)fmt; }
#define hos_oot_autoload_input real_autoload_input
#include "@AUTOLOAD@"
#undef hos_oot_autoload_input
#define hos_oot_restart_input real_restart_input
#include "@RESTART@"
#undef hos_oot_restart_input
void hos_oot_autoload_input(void) { counts.autoload++; real_autoload_input(); }
void hos_oot_restart_input(void) { counts.restart++; real_restart_input(); }

#if HAS_DEMAND
#ifndef WM_APP
#define WM_APP 0x8000
#define WM_CLOSE 0x0010
#endif
static int fullscreen_asked = -1, menu_asked = -1, restart_asked, close_asked;
@SDL_WAKE@
static int windows_wake(unsigned msg) {
    switch (msg) {
@WINDOWS_WAKE@
    default: return -1;
    }
}
#endif

typedef struct Ctx { int unused; } Ctx;
static float oot_load_float(Ctx *c, uint64_t address) { (void)c; (void)address; counts.positions++; return 0; }
static void oot_restart_request(Ctx *c, uint64_t actor) { (void)c; (void)actor; counts.requests++; }
static void input_hook(void) {
@INPUT@
}
static void player_hook(void) {
    Ctx context = {0}, *c = &context;
    uint64_t actor = 0;
@PLAYER@
}
static void direct_input(void) {
    hos_oot_autoload_input();
    hos_oot_restart_input();
    if (hos_oot_autoload_alone()) return;
}
static void direct_player(void) {
    Ctx context = {0}, *c = &context;
    uint64_t actor = 0;
    if (hos_oot_restart_continuing()) {
        float pos[3];
        for (unsigned i = 0; i < 3; i++) pos[i] = oot_load_float(c, actor + 796 + i * 4);
        hos_oot_restart_player(pos);
    }
    if (hos_oot_restart_asking()) oot_restart_request(c, actor);
}
static int failures;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL %d: %s\n", __LINE__, #x); failures++; } } while (0)
static void reset(void) {
    hos_oot_restart_reset();
    hos_oot_autoload_reset();
    want = skip = player = told = rearm_during_query = 0;
    now = 1000000000;
#ifdef _WIN32
    SetEnvironmentVariableA(HOS_RESTART_NOTE, NULL);
#else
    unsetenv(HOS_RESTART_NOTE);
#endif
#if HAS_DEMAND
    hos_oot_autoload_start();
#endif
    memset(&counts, 0, sizeof counts);
}
static void idle(const char *label) {
    memset(&counts, 0, sizeof counts);
    for (int i = 0; i < 1000; i++) input_hook(), player_hook();
    printf("%s: clocks=%u options=%u player-polls=%u state-atomics=%u autoload=%u restart=%u demand-loads=%u demand-writes=%u\n",
           label, counts.clocks, counts.options, counts.players, counts.atomics, counts.autoload, counts.restart,
           counts.flag_reads, counts.flag_writes);
    CHECK(counts.clocks == 0 && counts.options == 0 && counts.players == 0 && counts.atomics == 0);
    CHECK(counts.autoload == 0 && counts.restart == 0 && counts.requests == 0 && counts.positions == 0);
    CHECK(counts.flag_writes == 0);
#if HAS_DEMAND
    CHECK(counts.flag_reads == 2000);
#endif
}
static struct Counts work_counts(void) {
    struct Counts c = counts;
    c.flag_reads = c.flag_writes = 0;
    return c;
}
int main(int argc, char **argv) {
    CHECK(argc == 2);
    if (argc != 2) return 1;
    hos_user_dir = argv[1];
#if HAS_DEMAND
    demand_address = &hos_oot_menu_work;
#endif
    reset();
    idle("idle");
#if HAS_DEMAND
    /* Both actual message handlers wake without asking to close/relaunch. */
    reset(); CHECK(windows_wake(WM_APP + 13) == 0 && hos_oot_menu_work);
    reset(); hos_posix_post_message(WM_APP + 13, 0, 0);
    CHECK(hos_oot_menu_work && !restart_asked && !close_asked);
    reset(); CHECK(windows_wake(WM_APP + 12) == -1 && !hos_oot_menu_work);
    hos_posix_post_message(WM_APP + 12, 0, 0);   /* main's Paste is not a restart wake */
    CHECK(!hos_oot_menu_work && !restart_asked && !close_asked);
    reset(); hos_posix_post_message(WM_APP + 9, 0, 0);
    CHECK(!hos_oot_menu_work && restart_asked && !close_asked);
    puts("wake transport: Windows and windowless Linux arm without relaunching");

    /* Requested work has the same calls/state as the original unguarded hook. */
    reset(); player = 1; want = HOS_RESTART_SAVE; hos_oot_menu_work_arm();
    memset(&counts, 0, sizeof counts); input_hook(); player_hook();
    const struct Counts gated = work_counts();
    CHECK(r_state == R_ASK && counts.requests == 1 && counts.autoload == 1 && counts.restart == 1);
    CHECK(counts.clocks > 0 && counts.options > 0 && counts.atomics > 0);
    reset(); player = 1; want = HOS_RESTART_SAVE; hos_oot_menu_work_arm();
    memset(&counts, 0, sizeof counts); direct_input(); direct_player();
    const struct Counts direct = work_counts();
    CHECK(!memcmp(&gated, &direct, sizeof gated) && r_state == R_ASK);
    puts("armed request: same clock/option/atomic/call counters and request state as direct hooks");
    want = 0; input_hook(); CHECK(!hos_oot_menu_work && r_state == R_OFF);
    idle("cancelled");

    /* A new wake during completion cannot be erased by that older poll. */
    reset(); player = 1; hos_oot_menu_work_arm(); rearm_during_query = 1;
    input_hook(); CHECK(hos_oot_menu_work && want == HOS_RESTART_SAVE);
    input_hook(); CHECK(r_state == R_ASK);
    puts("concurrent rearm: the new request survives the older poll");

    /* The existing player clock remembers play without idle input polling. */
    reset(); int64_t last_player = 1;
    hos_oot_restart_watch_player(&last_player);
    now = 3000000000; want = HOS_RESTART_SAVE; hos_oot_menu_work_arm();
    input_hook(); CHECK(told == HOS_RESTART_REFUSED && !hos_oot_menu_work);
    idle("loading refusal");

    /* A cancelled request cannot leave later idle play's history stale. */
    reset(); last_player = now; hos_oot_restart_watch_player(&last_player);
    player = 1; want = HOS_RESTART_SAVE; hos_oot_menu_work_arm(); input_hook();
    want = 0; input_hook(); CHECK(!hos_oot_menu_work);
    last_player = 4000000000; now = 4500000000; player = 0;
    want = HOS_RESTART_SAVE; hos_oot_menu_work_arm(); input_hook();
    CHECK(r_state == R_ASK && told != HOS_RESTART_REFUSED);
    puts("loading history: a later player's existing timestamp is not stale after cancellation");

    /* No game has played: the title restart still goes ahead once. */
    reset(); want = HOS_RESTART_SAVE; hos_oot_menu_work_arm();
    input_hook(); CHECK(told == HOS_RESTART_GO && !hos_oot_menu_work);
    idle("title completion");

    /* Full note/slot validation belongs to the unchanged restart fixture; here
       compare an armed continuation's player/input hooks, then their disarm. */
    reset(); c_state = 1; active = restarted = started = 1; next_ms = 20000;
    memset(c_pos, 0, sizeof c_pos); hos_oot_menu_work_arm();
    memset(&counts, 0, sizeof counts); player_hook();
    const struct Counts resumed = work_counts();
    CHECK(c_state == 2 && counts.positions == 3);
    reset(); c_state = 1; active = restarted = started = 1; next_ms = 20000;
    memset(c_pos, 0, sizeof c_pos); hos_oot_menu_work_arm();
    memset(&counts, 0, sizeof counts); direct_player();
    const struct Counts resumed_direct = work_counts();
    CHECK(!memcmp(&resumed, &resumed_direct, sizeof resumed) && c_state == 2);
    player = 1; input_hook(); CHECK(!hos_oot_menu_work && !active);
    idle("resumed");
    puts("armed resume: same player hook work; demand clears after play returns");
#else
    /* Keep the same executable counter failure on the old source, not an
       undefined-symbol failure from interfaces it did not yet have. */
    (void)direct_input; (void)direct_player; (void)work_counts;
#endif
    return failures ? 1 : 0;
}
'''


def between(text, start, end):
    at = text.index(start) + len(start)
    return text[at:text.index(end, at)]


def brace_block(text, start):
    at = text.index(start)
    opening = text.index("{", at)
    depth = 1
    end = opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[at:end]


def check_wiring(root):
    menu = (root / "gpu/vk/vk_menu.cpp").read_text(encoding="utf-8")
    request = between(menu, "auto restart_in_place = [&] {", "\n    };")
    assert request.index("place_state = 1") < request.index("PostMessageA(app_window(), WM_APP + 13, 0, 0)"), "publish request before wake"
    for filename in ("host/display.c", "host/posix/display_sdl.c"):
        source = (root / filename).read_text(encoding="utf-8")
        assert "WM_APP + 13" in source and "hos_oot_menu_work_arm();" in source, filename + ": missing wake receiver"
    backend = (root / "host/rx_backend.c").read_text(encoding="utf-8")
    assert backend.count("hos_oot_autoload_start();") == 2, "arm startup on both renderer paths"
    main = (root / "host/hos_main.c").read_text(encoding="utf-8")
    assert "hos_oot_restart_watch_player(&flight_player_seen);" in main, "use the existing player clock"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default=os.environ.get("CC", "gcc"))
    parser.add_argument("--source-root", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.source_root.resolve()
    hid = (root / "host/nn_hid.c").read_text(encoding="utf-8")
    game = (root / "host/hos_main.c").read_text(encoding="utf-8")
    input_hook = between(hid, "    scripted_input(buttons, stick);", "    /* (longshot) HOS_CONTROL:")
    player_hook = between(game, "    last_player_tick = now;", "    int action = rx_option(HOS_OPT_CHEAT_ACTION, 0);")
    demand = "hos_oot_menu_work" in (root / "host/oot_autoload.h").read_text(encoding="utf-8")
    source = HARNESS.replace("@HAS_DEMAND@", str(int(demand))).replace("@INPUT@", input_hook).replace("@PLAYER@", player_hook)
    sdl_wake = windows_wake = ""
    if demand:
        sdl_wake = brace_block((root / "host/posix/display_sdl.c").read_text(encoding="utf-8"),
                               "void hos_posix_post_message(")
        windows_wake = brace_block((root / "host/display.c").read_text(encoding="utf-8"), "case WM_APP + 13:")
    source = source.replace("@SDL_WAKE@", sdl_wake).replace("@WINDOWS_WAKE@", windows_wake)
    for token, filename in (("@AUTOLOAD@", "oot_autoload.c"), ("@RESTART@", "oot_restart.c")):
        source = source.replace(token, (root / "host" / filename).as_posix())
    with tempfile.TemporaryDirectory(prefix="restart-idle-") as directory:
        tmp = Path(directory)
        src, exe = tmp / "counter.c", tmp / "counter"
        src.write_text(source, encoding="utf-8")
        subprocess.run([args.cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-D_CRT_SECURE_NO_WARNINGS",
                        "-I", str(root / "host"), "-I", str(root / "gpu/vk"), str(src), "-lm", "-o", str(exe)], check=True)
        subprocess.run([str(exe), str(tmp)], check=True)
    check_wiring(root)
    print("PASS: idle demand-only hooks, unchanged armed work, cancellation, rearm, loading history and resume disarm")


if __name__ == "__main__":
    main()
