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
    # (the pause: an id beside the achievements' own, answered by the menu while the tools have been on; never in vk_api.h,
    # which stamps the shader caches: a change there makes every player prepare the shaders again)
    paused = int(re.search(r"#define OOT_ACHIEVEMENTS_PAUSED_ID (\d+)", header).group(1))
    check(base + len(known) < paused < toast, "OOT_ACHIEVEMENTS_PAUSED_ID overlaps the Inventory page's ids")
    check("ACHIEVEMENT" not in api, "the achievements touch vk_api.h, which is in the shader caches' stamp")
    check("ACHIEVEMENT" not in replay, "the achievements add to the option callback, whose ids are vk_api.h's")
    cheats = main_c[main_c.index("static void oot_update_player_cheats("):]
    took, paused_at = cheats.index("int action = rx_option(HOS_OPT_CHEAT_ACTION, 0);"), cheats.index("hos_oot_achievements_cheated();")
    check(took < paused_at < cheats.index("oot_inventory_refresh(c, now);"),
          "a debug tool's action does not pause the achievements before the next refresh")
    check("if (id == OOT_ACHIEVEMENTS_PAUSED_ID) {" in menu, "the menu is not told of the pause")
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
    # (the Achievements page, page 6: its row opens it before the row-without-a-value fallback that opens the Controls page)
    check('{TAB_GAMEPLAY, "Achievements", nullptr, 0, 0, 0, nullptr,' in menu, "the Achievements row is missing")
    opens = menu.index("else if (press[7] && is_achievements_row(r)) page = 6")
    check(opens < menu.index("else if (press[7] && !r.value && can_bind()) page = 1"),
          "the Achievements row would open the Controls page")
    check("else if (page == 6) draw_achievements(k);" in menu and "} else if (open_now.load() && page == 6) {" in menu,
          "page 6 is not drawn or takes no input")
    check("achievement_scroll_px = saved_ach_px[0]" in menu, "prebake does not give the page's scroll back")
    check("menu_refresh = true;   // (the page open: drawn again with it)" in menu, "an unlock does not redraw the open page")
    imgui = (ROOT / "gpu/vk/vk_imgui.cpp").read_text(encoding="utf-8")
    build = (ROOT / "nx2/build.py").read_text(encoding="utf-8")
    check('#include "longshot_achievement_icons.h"' in imgui and "ImTextureData* achievement_icon(const char* id, bool locked, " in imgui,
          "vk_imgui.cpp does not load the icons")
    check('achievement_icons.write(os.path.join(gen_dir, "longshot_achievement_icons.h"))' in build,
          "nx2/build.py does not generate the icons' header")
    # (one texture for every icon: the overlays' backend pools few, and a texture it cannot make holds back every overlay, the
    # menu and the FPS counter too: 58 textures of their own left the Insert menu blank in the game, 2026-11-14)
    check(imgui.count("ImGui::RegisterUserTexture(") == 2 and "ImGui::RegisterUserTexture(achievement_atlas_)" in imgui,
          "the icons register more than their one atlas texture")
    return rows


def icon_checks(folder):
    """nx2/achievement_icons.py on the repository's icons and on made-up folders"""
    import sys
    sys.path.insert(0, str(ROOT / "nx2"))
    sys.path.insert(0, str(ROOT / "scripts"))
    import achievement_icons
    import make_achievement_icons as art
    ids = achievement_icons.ids()
    got = achievement_icons.icons()
    have = {(i, locked) for i, locked, *_ in got}
    for i in ids:   # (the default set is whole: an unlocked and a locked icon for every achievement)
        check((i, False) in have and (i, True) in have, "assets/achievements lacks an icon for %s" % i)
    check(all(w <= achievement_icons.MAX_SIDE and h <= achievement_icons.MAX_SIDE and len(px) == w * h * 4
              for _, _, w, h, px in got), "an icon's size or pixels are wrong")
    text = achievement_icons.header()
    check("#define LONGSHOT_ACHIEVEMENT_ICONS %d" % len(got) in text and text.count('{"') == len(got), "the icons' header")
    check(text.count("longshot_achievement_atlas_rgba[") == 1, "the icons are not one atlas")
    # (the atlas: each icon in its own cell of the largest one's size, its pixels where its place says)
    a = [("song_zelda", False, 2, 1, bytes([1, 2, 3, 4, 5, 6, 7, 8])), ("song_zelda", True, 3, 3, bytes(range(36)))]
    aw, ah, px, places = achievement_icons.atlas(a)
    check((aw, ah) == (6, 3) and places == [("song_zelda", False, 0, 0, 2, 1), ("song_zelda", True, 3, 0, 3, 3)], "atlas layout")
    check(px[0:8] == bytes([1, 2, 3, 4, 5, 6, 7, 8]) and px[(2 * aw + 3) * 4:(2 * aw + 6) * 4] == bytes(range(24, 36)),
          "atlas pixels")
    tiny = art.png(2, 2, bytes([255, 0, 0, 255] * 4))
    with tempfile.TemporaryDirectory(prefix="achievement-icons-") as t:
        folder = Path(t)
        check(achievement_icons.icons(str(folder / "none")) == [], "no folder: no icons")
        check("LONGSHOT_ACHIEVEMENT_ICONS" not in achievement_icons.header(str(folder / "none")), "no icons: no define")
        (folder / "song_zelda_locked.png").write_bytes(tiny)
        only = achievement_icons.icons(str(folder))
        check([(i, locked, w, h) for i, locked, w, h, _ in only] == [("song_zelda", True, 2, 2)], "a lone locked icon")
        (folder / "song_zeldaa.png").write_bytes(tiny)
        try:
            achievement_icons.icons(str(folder))
            check(False, "a misspelled icon was not refused")
        except ValueError as e:
            check("song_zeldaa" in str(e), "the refusal does not name the file")
        (folder / "song_zeldaa.png").unlink()
        (folder / "song_zelda.png").write_bytes(art.png(257, 1, bytes(257 * 4)))
        try:
            achievement_icons.icons(str(folder))
            check(False, "an icon over 256 x 256 was not refused")
        except ValueError:
            pass
    return len(got)


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
const char *hos_user_dir;
static int paused_sent, logs, unlocked_sent[64], toasts[64], toast_n;
static char last_log[256];
void hos_log(const char *fmt, ...) {
    va_list a;
    va_start(a, fmt);
    vsnprintf(last_log, sizeof last_log, fmt, a);
    va_end(a);
    fprintf(stderr, "log: %s\n", last_log);
    logs++;
}
'''

MAIN = r'''
void rx_option_set(int id, int value) {
    if (id == OOT_ACHIEVEMENT_TOAST_ID) { CHECK(toast_n < 64); toasts[toast_n++] = value; return; }
    if (id == OOT_ACHIEVEMENTS_PAUSED_ID) { CHECK(value == 1); paused_sent++; return; }
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
        hos_oot_achievements_cheated();   /* a debug tool used: nothing new for the run, said and told once */
        CHECK(strstr(last_log, "paused") && paused_sent == 1);
        const int logged = logs;
        hos_oot_achievements_cheated();
        counts[4] = 1;
        tick(1), tick(1);
        CHECK(toast_n == 3 && !unlocked_sent[at("song_zelda")] && logs == logged && paused_sent == 1);
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
    print("PASS: %d achievements, their items, option ids, the menu rows, the page, the pause and the refresh's call" % len(rows))
    print("PASS: %d icons, the build's header, a lone locked icon, a misspelled and an oversized one refused" % icon_checks(None))
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
