"""The Debug tab's Save here (host/hos_main.c oot_save_*): the game saves where Link stands, through its own pause menu
(Plus, R, A, Up, A: System, Save), the presses one at a time from the controller's read, the waits counted in presents.

The save's code is cut out of host/hos_main.c and compiled against a fake pause menu (Plus pauses and Link stops being
updated; R goes to System; A there shows the slot card with Cancel chosen; Up chooses Save; A on it writes a slot; B closes
the card, then the menu) and fake game data. Checked: nothing is pressed while the Insert menu is open; the sequence and
the slot written; the bridge's tickets (not taken yet, under way, saved, replaced, cancelled); refusals before any press
(an event, a load, no save, the free camera, the flight, a save under way); a pause menu that does not open stops it after
Plus; no slot written still closes the menu. And, from the sources: the action's number and where it is taken, the tick in
the controller's read before the host's taps, the save file hook in nn_fs.c, the bridge's command, the Debug tab's row.
No game.

python -I -B tests/test_cheat_save.py [--cc gcc]
"""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cc", "gcc"]

ROOT = Path(__file__).resolve().parents[1]


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + what)


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


PRELUDE = r'''
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); exit(1); } } while (0)
typedef int64_t LONG64;
typedef struct { int unused; } SRWLOCK;
#define SRWLOCK_INIT {0}
static void AcquireSRWLockExclusive(SRWLOCK *l) { (void)l; }
static void ReleaseSRWLockExclusive(SRWLOCK *l) { (void)l; }
static void AcquireSRWLockShared(SRWLOCK *l) { (void)l; }
static void ReleaseSRWLockShared(SRWLOCK *l) { (void)l; }
typedef struct { int unused; } Ctx;
volatile LONG64 hos_present_count;
static int64_t now_ns = 1000000000;
static int64_t hos_now_ns(void) { return now_ns; }
static char last_log[512];
static int nlogs;
__attribute__((format(printf, 1, 2))) static void hos_log(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(last_log, sizeof last_log, fmt, ap);
    va_end(ap);
    nlogs++;
}
static int64_t flight_player_seen;
/* the game: an event, a load, no save loaded; the Insert menu, the free camera, the flight */
static int event_playing, map_loading, no_save, insert_menu, camera, flying;
static int oot_event_playing(Ctx *c) { (void)c; return event_playing; }
static int oot_gamedata_bool(Ctx *c, uint32_t hash, int *value) {
    (void)c;
    CHECK(hash == 0xef2b60c2u);   /* (Sequence_IsLoadingBanc only) */
    *value = map_loading;
    return 1;
}
static int oot_pouch_count(Ctx *c, const char *name) { (void)c; (void)name; return no_save ? -1 : 0; }
static const char *const oot_item_names[] = {"Weapon_Sword_Kokiri"};
int rx_option(int id, int otherwise) {
    if (id == 1) return insert_menu;
    if (id == HOS_OPT_CHEAT_CAMERA) return camera;
    if (id == HOS_OPT_CHEAT_FLIGHT) return flying;
    return otherwise;
}
/* the pause menu: Plus pauses (Link is not updated while it is open), on Items; R: System; A on System's Save: the slot's
   card, Cancel chosen; Up: Save; A on Save: a slot written; B: the card closed, then the menu. opens 0: Plus does nothing;
   writes_ok 0: A on Save writes nothing */
static int paused, tab, card, choice, opens = 1, writes_ok = 1, stray;
static char taps[64][32];
static int ntaps;
static int writes;
static char written[64] = "slot_02";
void hos_hid_tap(const char *press) {
    CHECK(ntaps < 64);
    snprintf(taps[ntaps++], sizeof taps[0], "%s", press);
    if (!strcmp(press, "0:PLUS/150")) {
        if (opens && !paused) paused = 1, tab = 0, card = 0;
    } else if (!paused) stray++;   /* (a press in play: Save here must not make one) */
    else if (!strcmp(press, "0:R/150")) tab = 1;
    else if (!strcmp(press, "0:A/150")) {
        if (card && choice == 1) {
            if (writes_ok) writes++, snprintf(written, sizeof written, "slot_%02d", 2 + writes);
        } else if (tab == 1 && !card) card = 1, choice = 0;
    } else if (!strcmp(press, "0:UP/150")) {
        if (card) choice = 1;
    } else if (!strcmp(press, "0:B/150")) {
        if (card) card = 0;
        else paused = 0;
    }
}
int hos_fs_save_writes(char *slot, size_t cap) {
    if (slot && cap) snprintf(slot, cap, "%s", written);
    return writes;
}
'''

MAIN = r'''
/* n presents, a controller read at each; Link updated at each while the pause menu is closed */
static void frames(int n) {
    for (int i = 0; i < n; i++) {
        hos_present_count++;
        now_ns += 16666667;
        if (!paused) flight_player_seen = now_ns;
        oot_save_tick();
    }
}
static int taps_are(const char *const *want, int n) {
    if (ntaps != n) return 0;
    for (int i = 0; i < n; i++)
        if (strcmp(taps[i], want[i])) return 0;
    return 1;
}
static int result_of(int ticket, char *why, char *slot) { return hos_oot_save_result(ticket, why, 160, slot, 64); }
/* (Link's update) the bridge's ask taken, or the Debug tab's (ticket 0) */
static void update(int from_bridge) {
    Ctx c = {0};
    int ticket = 0;
    if (from_bridge) CHECK((ticket = oot_save_take()) != 0);
    oot_save_start(&c, ticket);
}
int main(void) {
    char why[160], slot[64];
    frames(10);
    {   /* saved: nothing pressed while the Insert menu is open, then the sequence, the slot, the menu closed */
        insert_menu = 1;
        const int t = hos_oot_save_ask();
        CHECK(t > 0 && result_of(t, why, slot) == 0);   /* (not taken yet) */
        update(1);
        CHECK(strstr(last_log, "cheats: save: through the pause menu"));
        CHECK(result_of(t, why, slot) == 3);   /* (under way) */
        frames(600);
        CHECK(ntaps == 0 && result_of(t, why, slot) == 3);
        insert_menu = 0;
        frames(400);
        CHECK(ntaps >= 1 && !strcmp(taps[0], "0:PLUS/150") && paused);
        frames(3000);
        static const char *const want[] = {"0:PLUS/150", "0:R/150", "0:A/150", "0:UP/150", "0:A/150", "0:B/150", "0:B/150"};
        CHECK(taps_are(want, 7));
        CHECK(!paused && !stray && writes == 1);
        CHECK(result_of(t, why, slot) == 1 && !strcmp(slot, "slot_03") && !why[0]);
        CHECK(!strcmp(last_log, "cheats: save: saved in slot_03"));
        frames(600);
        CHECK(ntaps == 7);   /* (over: nothing more) */
    }
    {   /* refused before any press, with the reason, answered by ticket */
        static const char *const reasons[] = {"an event is playing", "a map is loading", "no save is loaded",
                                              "the free camera is on", "Link is flying"};
        for (int r = 0; r < 5; r++) {
            event_playing = r == 0, map_loading = r == 1, no_save = r == 2, camera = r == 3, flying = r == 4;
            ntaps = 0;
            const int t = hos_oot_save_ask();
            update(1);
            CHECK(result_of(t, why, slot) == -1 && strstr(why, reasons[r]) && !slot[0]);
            CHECK(!strncmp(last_log, "cheats: save refused: ", 22) && strstr(last_log, reasons[r]));
            frames(1200);
            CHECK(ntaps == 0);
        }
        event_playing = map_loading = no_save = camera = flying = 0;
    }
    {   /* one under way: a second is refused and the first goes on */
        ntaps = 0;
        update(0);
        const int t = hos_oot_save_ask();
        update(1);
        CHECK(result_of(t, why, slot) == -1 && strstr(why, "already under way"));
        frames(3000);
        CHECK(ntaps == 7 && writes == 2 && !paused && !strcmp(last_log, "cheats: save: saved in slot_04"));
    }
    {   /* the pause menu does not open: nothing after Plus, not saved */
        ntaps = 0, opens = 0;
        const int t = hos_oot_save_ask();
        update(1);
        frames(3000);
        static const char *const want[] = {"0:PLUS/150"};
        CHECK(taps_are(want, 1) && !stray);
        CHECK(result_of(t, why, slot) == -3 && strstr(why, "did not open"));
        CHECK(strstr(last_log, "cheats: save not made: the pause menu did not open"));
        opens = 1;
    }
    {   /* no slot written: said, and the menu still closed */
        ntaps = 0, writes_ok = 0;
        const int t = hos_oot_save_ask();
        update(1);
        frames(4000);
        CHECK(result_of(t, why, slot) == -3 && strstr(why, "no save file was written") && !slot[0]);
        CHECK(!paused && !stray && ntaps >= 7 && !strcmp(taps[ntaps - 1], "0:B/150"));
        writes_ok = 1;
    }
    {   /* the bridge: a later ask replaces one not taken; a cancelled one is not taken */
        const int a = hos_oot_save_ask(), b = hos_oot_save_ask();
        CHECK(result_of(a, why, slot) == -2 && result_of(b, why, slot) == 0);
        hos_oot_save_cancel(b);
        CHECK(result_of(b, why, slot) == -2 && oot_save_take() == 0);
    }
    puts("PASS: Save here: the pause menu's System, Save; its refusals, checks and the bridge's tickets");
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="cc")
    args = parser.parse_args()
    host = (ROOT / "host/hos_main.c").read_text(encoding="utf-8")
    hid = (ROOT / "host/nn_hid.c").read_text(encoding="utf-8")
    fs = (ROOT / "host/nn_fs.c").read_text(encoding="utf-8")
    control = (ROOT / "host/control.c").read_text(encoding="utf-8")
    header = (ROOT / "host/control.h").read_text(encoding="utf-8")
    menu = (ROOT / "gpu/vk/vk_menu.cpp").read_text(encoding="utf-8")
    api = (ROOT / "gpu/vk/vk_api.h").read_text(encoding="utf-8")
    docs = (ROOT / "docs/control.md").read_text(encoding="utf-8")

    # (the action: its number beside the others, taken from the bridge when nothing else is, and before the pouch's range)
    check("#define OOT_SAVE_ACTION 998 " in host, "hos_main.c: OOT_SAVE_ACTION is not 998")
    cheats = block(host, "static void oot_update_player_cheats(")
    check(re.search(r"if \(!action && \(save_ticket = oot_save_take\(\)\)\) action = OOT_SAVE_ACTION;", cheats),
          "Link's update does not take the bridge's save")
    at = cheats.index("action == OOT_SAVE_ACTION")
    check(at < cheats.index("} else if (action >= 200) {"), "the save is not taken before the pouch's actions (200 and up)")
    check("oot_save_start(c, save_ticket);" in cheats, "the save is not begun from Link's update")
    check(re.search(r"void hos_oot_save_tick\(void\) \{\n#if defined\(_WIN32\) \|\| defined\(__linux__\)[^\n]*\n    oot_save_tick\(\);\n#endif\n\}",
                    host), "hos_oot_save_tick is not defined on every platform (and the tool on Windows and Linux)")
    # (the presses: the live input's queue, from the controller's read, before the host's taps are taken)
    script = block(hid, "static void scripted_input(")
    check(script.index("hos_oot_save_tick();") < script.index("host_taps();"), "the save's tick is not before host_taps")
    check('if (!strncmp(line, "!cheat ", 7)) __atomic_store_n(&hos_oot_live_action, atoi(line + 7), __ATOMIC_RELAXED);' in hid,
          "the live input file's !cheat is gone")
    # (the slot written: nn_fs.c counts progress.sav files written into the save folder)
    check("if (fh->writable) note_save_write(fh->path);" in block(fs, "HLE(_ZN2nn2fs9CloseFileENS0_10FileHandleE)"),
          "CloseFile does not note a written save")
    check("note_save_write(gstr(ARG(1)));" in block(fs, "HLE(_ZN2nn2fs10RenameFileEPKcS2_)"),
          "RenameFile does not note a save renamed into place")
    check('_stricmp(name + 1, "progress.sav")' in block(fs, "static void note_save_write("), "the hook is not progress.sav's")
    # (the bridge)
    check('else if (!strcmp(cmd, "save")) cmd_save(ci, &d, req, id);' in control, "the bridge has no save command")
    check(".save = hos_oot_save_ask, .save_result = hos_oot_save_result, .save_cancel = hos_oot_save_cancel," in control,
          "the bridge's game does not give it the save")
    check("int (*save)(void);" in header and "int (*save_result)(" in header, "control.h has no save in HosControlGame")
    check("**save** (OoT)" in docs, "docs/control.md does not describe save")
    # (the Debug tab's row: Row::cheat 12, confirm stores 998 and closes the menu)
    check(re.search(r'\{TAB_DEBUG, "Save here", nullptr, 0, 0, 0, nullptr,\n[^{}]*nullptr, false, true, 12\}', menu),
          "the Debug tab has no Save here row (cheat 12)")
    check(re.search(r"press\[7\] && i == 12\) \{[^}]*cheat_action\.store\(998\);[^}]*open_now = false;", menu),
          "Save here's confirm does not ask for 998 and close the menu")

    save = host[host.index("enum { OOT_SAVE_NONE"):host.index("static void oot_update_player_cheats(")]
    source = block(api, "enum { HOS_OPT_CHEAT_HEARTS") + ";\n" + PRELUDE + save + MAIN
    with tempfile.TemporaryDirectory(prefix="cheat-save-") as tmp:
        c = Path(tmp) / "save.c"
        exe = Path(tmp) / "save.exe"
        c.write_text(source, encoding="utf-8")
        subprocess.run([args.cc, "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-x", "c", str(c), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
