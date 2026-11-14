"""Achievements (docs/achievements.md): the list, its wiring, and host/oot_achievements.c compiled with stubs, without a game.

The tracker is fed made-up inventory counts: it must unlock each achievement once (all-of and any-of conditions), never
without a save loaded or while the Debug tools pause it, keep the unlock file whole (the old one's unknown ids kept,
torn lines skipped) and still tell the menu when the file cannot be written. No item names or flags come from a dump:
they are the Inventory page's own (gpu/vk/oot_cheat_items.h).

python -I -B tests/test_oot_achievements.py [--cc COMPILER]
"""
import argparse
import re
import subprocess
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cc", "{cxx}"]
ROOT = Path(__file__).resolve().parents[1]


def check(cond, what):
    if not cond:
        raise AssertionError(what)


def achievements(header):
    """(id, title, mode, items) for each X(...) row of OOT_ACHIEVEMENTS"""
    body = header.split("#define OOT_ACHIEVEMENTS(X)", 1)[1].split("\n\n", 1)[0]
    rows = re.findall(r'X\(\s*"([^"]*)",\s*"([^"]*)",\s*"([^"]*)",\s*(OOT_ACH_ALL|OOT_ACH_ANY),\s*"([^"]*)",\s*"([^"]*)",\s*"([^"]*)"\)',
                      body.replace("\\\n", " "))
    return [(r[0], r[1], r[3], [n for n in r[4:] if n]) for r in rows]


def wiring():
    header = (ROOT / "gpu/vk/oot_achievements.h").read_text(encoding="utf-8")
    items = (ROOT / "gpu/vk/oot_cheat_items.h").read_text(encoding="utf-8")
    api = (ROOT / "gpu/vk/vk_api.h").read_text(encoding="utf-8")
    menu = (ROOT / "gpu/vk/vk_menu.cpp").read_text(encoding="utf-8")
    menu_h = (ROOT / "gpu/vk/vk_menu.h").read_text(encoding="utf-8")
    replay = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    main_c = (ROOT / "host/hos_main.c").read_text(encoding="utf-8")
    rows = achievements(header)
    check(len(rows) == header.count('    X("'), "a row of OOT_ACHIEVEMENTS did not parse")
    check(len(rows) >= 29, "the list lost achievements: %d" % len(rows))
    ids = [r[0] for r in rows]
    check(len(set(ids)) == len(ids), "two achievements share an id")
    check(all(re.fullmatch(r"[a-z0-9_]{1,63}", i) for i in ids), "an id is not lower-case letters, digits and _")
    known = set(re.findall(r'OOT_ITEM\("[^"]*", "([^"]+)"', items))
    for i, title, mode, names in rows:
        check(names, "%s asks for no item" % i)
        for n in names:
            check(n in known, "%s asks for %s, which the Inventory page does not count" % (i, n))
    # (ids clear of the Inventory page's: OOT_CHEAT_ITEM_BASE + every item stays below the toast's)
    base = int(re.search(r"#define OOT_CHEAT_ITEM_BASE (\d+)", items).group(1))
    toast = int(re.search(r"#define OOT_ACHIEVEMENT_TOAST_ID (\d+)", header).group(1))
    first = int(re.search(r"#define OOT_ACHIEVEMENT_BASE (\d+)", header).group(1))
    check(base + len(known) < toast < first, "the achievements' option ids overlap the Inventory page's")
    # (the pause: a free option id, answered by the menu while the tools have been on)
    used = [int(v) for v in re.findall(r"HOS_OPT_\w+ = (\d+)", api)]
    check("HOS_OPT_ACHIEVEMENTS_PAUSED = 58" in api and used.count(58) == 1, "HOS_OPT_ACHIEVEMENTS_PAUSED is not 58 alone")
    check("if (id == HOS_OPT_ACHIEVEMENTS_PAUSED) return menu_achievements_paused();" in replay, "the pause is not answered")
    check("if (cheats_on()) seen.store(true" in menu, "the pause does not follow the Debug tools")
    check("int achievement_popups = 1;" in menu_h, "Achievement pop-ups is not on by default")
    check('{TAB_GAMEPLAY, "Achievement alerts", &opts.achievement_popups, 0, 1, 1, tx_onoff' in menu, "the row is missing")
    check("achievement_popups" not in menu[menu.index("const char* ini_key("):menu.index("// (longshot) the controls, kept")],
          "ini_key needs no case for the row: its name is its key, achievement_alerts")
    check("achievement_toast_ask(value);" in menu and "if (toast) draw_achievement_toast(c, toast_now);" in menu,
          "the pop-up is not asked for or not drawn")
    check(menu.count("achievement_toast_due(toast_now)") == 2, "menu_record and menu_record_image must both draw the pop-up")
    refresh = main_c[main_c.index("static void oot_inventory_refresh("):]
    refresh = refresh[:refresh.index("\n}\n")]
    check("hos_oot_achievements_check(oot_item_names, counts, (int)OOT_ITEM_COUNT, loaded);" in refresh,
          "oot_inventory_refresh does not hand its counts to the achievements")
    return rows


HEAD = r'''
#define _POSIX_C_SOURCE 200809L
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); exit(1); } } while (0)
enum { HOS_OPT_ACHIEVEMENTS_PAUSED = 58 };
const char *hos_user_dir;
static int paused, logs, unlocked_sent[64], toasts[64], toast_n;
static char last_log[256];
void hos_log(const char *fmt, ...) {
    va_list a;
    va_start(a, fmt);
    vsnprintf(last_log, sizeof last_log, fmt, a);
    va_end(a);
    fprintf(stderr, "log: %s\n", last_log);
    logs++;
}
int rx_option(int id, int otherwise) { CHECK(id == HOS_OPT_ACHIEVEMENTS_PAUSED && otherwise == 0); return paused; }
'''

MAIN = r'''
void rx_option_set(int id, int value) {
    if (id == OOT_ACHIEVEMENT_TOAST_ID) { CHECK(toast_n < 64); toasts[toast_n++] = value; return; }
    CHECK(id >= OOT_ACHIEVEMENT_BASE && id < OOT_ACHIEVEMENT_BASE + ACH_N && value == 1);
    unlocked_sent[id - OOT_ACHIEVEMENT_BASE]++;
}
static const char *const names[] = {"Weapon_Sword_Kokiri", "Weapon_Shield_Deku", "Item_BombBag_Medium", "Item_BombBag_Large",
                                    "Item_Melody_ZeldaLullaby"};
static int counts[5];
static void tick(int loaded) { hos_oot_achievements_check(names, counts, 5, loaded); }
static int at(const char *id) { int i = ach_index(id); CHECK(i >= 0); return i; }
int main(int argc, char **argv) {
    CHECK(argc == 3);
    hos_user_dir = argv[2];
    if (!strcmp(argv[1], "fresh")) {
        char when[ACH_WHEN];   /* the UTC stamp: the epoch, two leap days, a day's last second */
        ach_stamp(when, sizeof when, 0);
        CHECK(!strcmp(when, "1970-01-01T00:00:00Z"));
        ach_stamp(when, sizeof when, 951782400);
        CHECK(!strcmp(when, "2000-02-29T00:00:00Z"));
        ach_stamp(when, sizeof when, 1709164800);
        CHECK(!strcmp(when, "2024-02-29T00:00:00Z"));
        ach_stamp(when, sizeof when, 1709164799);
        CHECK(!strcmp(when, "2024-02-28T23:59:59Z"));
        for (int i = 0; i < 5; i++) counts[i] = -1;
        tick(0);   /* no save: nothing, but the (missing) file was read */
        CHECK(toast_n == 0);
        counts[0] = 1, counts[1] = 0, counts[2] = 0, counts[3] = 0, counts[4] = 0;
        tick(1);   /* the sword alone: not the pair */
        CHECK(toast_n == 0);
        counts[1] = 1;
        tick(1);
        CHECK(toast_n == 1 && toasts[0] == at("sword_and_shield") && unlocked_sent[at("sword_and_shield")] == 1);
        tick(1);   /* once only */
        CHECK(toast_n == 1);
        counts[3] = 1;   /* the biggest bag alone: both tiers (any-of) */
        tick(1);
        CHECK(toast_n == 3 && unlocked_sent[at("bomb_bag_big")] == 1 && unlocked_sent[at("bomb_bag_biggest")] == 1);
        paused = 1, counts[4] = 1;   /* the Debug tools: nothing new, said once */
        tick(1), tick(1);
        CHECK(toast_n == 3 && !unlocked_sent[at("song_zelda")] && strstr(last_log, "paused"));
        const int logged = logs;
        tick(1);
        CHECK(logs == logged);
        puts("fresh ok");
    } else if (!strcmp(argv[1], "kept")) {
        for (int i = 0; i < 5; i++) counts[i] = -1;
        tick(0);   /* the file's unlocks reach the menu before anything else, with no pop-up */
        CHECK(unlocked_sent[at("song_zelda")] == 1 && toast_n == 0);
        counts[0] = 1, counts[1] = 1, counts[2] = 0, counts[3] = 0, counts[4] = 1;
        tick(1);   /* the song again: no second unlock; the pair: new */
        CHECK(unlocked_sent[at("song_zelda")] == 1 && toast_n == 1 && toasts[0] == at("sword_and_shield"));
        puts("kept ok");
    } else if (!strcmp(argv[1], "unwritable")) {
        counts[0] = 1, counts[1] = 1;
        tick(1);
        CHECK(toast_n == 1 && strstr(last_log, "could not write"));
        const int logged = logs;
        counts[4] = 1;
        tick(1);   /* still unlocks; the failure is said once */
        CHECK(toast_n == 2 && logs == logged + 1 && strstr(last_log, "unlocked song_zelda"));
        puts("unwritable ok");
    } else CHECK(!"unknown scenario");
    return 0;
}
'''

STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")


def compiled(cc, folder):
    source = (ROOT / "host/oot_achievements.c").read_text(encoding="utf-8")
    source = "\n".join(line for line in source.splitlines() if not line.startswith("#include"))
    header = (ROOT / "gpu/vk/oot_achievements.h").read_text(encoding="utf-8")
    c = folder / "achievements.c"
    exe = folder / "achievements.exe"
    c.write_text(HEAD + header + source + MAIN, encoding="utf-8")
    # (-x c: the runner hands over its C++ compiler driver, {cxx})
    subprocess.run([cc, "-x", "c", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O1", str(c), "-o", str(exe)], check=True)
    return exe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc")
    args = parser.parse_args()
    rows = wiring()
    print("PASS: %d achievements, their items, option ids, the menu row, the pause and the refresh's call" % len(rows))
    with tempfile.TemporaryDirectory(prefix="oot-achievements-") as temporary:
        folder = Path(temporary)
        exe = compiled(args.cc, folder)

        fresh = folder / "fresh"
        fresh.mkdir()
        subprocess.run([str(exe), "fresh", str(fresh)], check=True)
        lines = (fresh / "achievements.txt").read_text(encoding="utf-8").splitlines()
        check([l.split()[0] for l in lines] == ["sword_and_shield", "bomb_bag_big", "bomb_bag_biggest"],
              "the unlock file's lines: %r" % lines)
        check(all(STAMP.fullmatch(l.split()[1]) for l in lines), "a time is not UTC ISO 8601: %r" % lines)
        check(not (fresh / "achievements.txt.tmp").exists(), "the temporary file was left behind")

        kept = folder / "kept"
        kept.mkdir()
        (kept / "achievements.txt").write_text(
            "from_a_newer_build 2030-01-01T00:00:00Z\nsong_zelda 2026-01-01T00:00:00Z\ntorn\n\n", encoding="utf-8")
        (kept / "achievements.txt.tmp").write_text("half a write", encoding="utf-8")   # (a crash's: overwritten)
        subprocess.run([str(exe), "kept", str(kept)], check=True)
        lines = (kept / "achievements.txt").read_text(encoding="utf-8").splitlines()
        check("song_zelda 2026-01-01T00:00:00Z" in lines, "an earlier unlock's time changed: %r" % lines)
        check("from_a_newer_build 2030-01-01T00:00:00Z" in lines, "an unknown id was dropped: %r" % lines)
        check("torn" not in lines and "" not in lines, "a torn line was kept: %r" % lines)
        check(any(l.startswith("sword_and_shield ") for l in lines), "the new unlock is not in the file: %r" % lines)

        subprocess.run([str(exe), "unwritable", str(folder / "no" / "such" / "folder")], check=True)
    print("PASS: unlocks once, all-of and any-of, no save, the Debug tools' pause, the file kept whole, a failed write")


if __name__ == "__main__":
    main()
