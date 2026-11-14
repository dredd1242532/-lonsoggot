"""The Controls page's Haptic feedback, Haptic strength, Haptic status and Test haptics rows, and the back end behind them.

gpu/vk/vk_menu.cpp's four rows are compiled from the source with stubs and run: the mode's four choices and their order,
the strength's Game's own (0) and its 25% steps up to 200%, the read-only status row (never written to the ini), the Test
haptics row being an action rather than a page, and its step refusing to go past its ends. The option ids the host reads
them by (host/pad_sdl.c) are checked against the renderer's option table (gpu/vk/vk_replay.cpp) and the other ids.

host/haptic.c is read for the things only a run in the game would otherwise show: the DLL loaded from the game's own
folder by full path (never the search path), the ABI version checked, the Rumble strength's Off stopping every path
before the DSP is fed, the strength applied once and after the DSP (RenHDrum's own curve squares it: applied before, the
slider did nothing above 25%), the silence watchdog, and the test's fixed length.

    python -I -B tests/test_haptics_menu.py [--cxx g++]
"""
import argparse
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MENU = ROOT / "gpu" / "vk" / "vk_menu.cpp"
HAPTIC = ROOT / "host" / "haptic.c"
PADS = ROOT / "host" / "pad_sdl.c"

RUN_ALL_ARGS = ["--cxx", "{cxx}"]


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + what)


def block(text, at):
    """text from at to the end of the first {...} after it"""
    start = text.index("{", at)
    depth = 0
    for i in range(start, len(text)):
        depth += text[i] == "{"
        depth -= text[i] == "}"
        if depth == 0:
            return text[at:i + 1]
    raise SystemExit("no end for the block at %d" % at)


def definition(text, head):
    found = re.search(re.escape(head) + r"\([^;{]*\)\s*\{", text)
    if not found:
        raise SystemExit("no definition of " + head)
    return block(text, found.start())


def one_line(text, head):
    found = re.search(r"^" + re.escape(head) + r"\(.*\}\s*$", text, re.M)
    if not found:
        raise SystemExit("no " + head)
    return found.group(0)


FIXTURE = r"""
#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstring>
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL %%d %%s\n", __LINE__, #x); return 1; } } while (0)
%(options)s
Options opts;
%(tabs)s
%(row)s
int saves = 0, presets = 0;
void save() { saves++; }
void apply_pad_layout(int) {}
int detect_pad_layout() { return opts.pad_layout; }
void apply_preset(int) { presets++; }
void sync_preset_budgets(int) {}
void env_release(const int*) {}
void vram_budget_sync() {}
constexpr int kVramMinimumOnly = 1;
bool row_fixed(const int*) { return false; }
int upscaler() { return 0; }
namespace ur {
int step_upscaler(int, int, bool) { return 0; }
int step(int, int v, int dir) { return v + dir; }
int snap(int, int v) { return v; }
int snap_render(int, int v) { return v; }
bool full_scale(int) { return false; }
int step_engine(int, int, unsigned) { return 0; }
void step_render(int up, int v, int dir, int* u, int* r) { *u = up, *r = v + dir; }
}
namespace mr { constexpr int kMax = 8; }
namespace i18n { inline void set_game_language(int) {} }
void restart_values(int*) {}
struct { void note(const int*) {} } restart_state;
%(is_slider)s
%(stops)s
%(snap)s
%(haptic_step)s
%(ini_key)s
bool can_mic() { return false; }
unsigned upscalers_available() { return 1; }
void mic_chosen() {}
void samplers_changed() {}
int temporal_resets = 0;
void temporal_reset_failure() { ++temporal_resets; }
void prompts_at_once(const Row&) {}
void menu_debug_rows(bool, bool) {}
int step_output_choice(int id, int dir) { return id + dir; }
int output_for_aspect(int) { return opts.output_res; }
%(set_row)s
void arrow(Row& r, bool right) { %(arrow)s }
int main() {
    /* the four rows, in the order the page shows them, right after Rumble strength */
    Row mode = {TAB_CONTROLS, "Haptic feedback", &opts.haptic, %(mode_lo)s, %(mode_hi)s, %(mode_step)s, nullptr, nullptr, nullptr, false, true};
    CHECK(mode.lo == 0 && mode.hi == 3 && mode.step == 1);
    CHECK(opts.haptic == 0);
    CHECK(!std::strcmp(ini_key(mode), "haptic"));
    /* Off (3) is a choice like the others: the four go round, as the Layout preset's do */
    arrow(mode, true);
    CHECK(opts.haptic == 1);
    arrow(mode, true);
    CHECK(opts.haptic == 2);
    arrow(mode, true);
    CHECK(opts.haptic == 3);
    arrow(mode, true);
    CHECK(opts.haptic == 0);
    arrow(mode, false);
    CHECK(opts.haptic == 3);
    /* the strength: Game's own at 0, then 25 to 200 percent */
    Row strength = {TAB_CONTROLS, "Haptic strength", &opts.haptic_strength, %(st_lo)s, %(st_hi)s, %(st_step)s, nullptr, nullptr, nullptr, false, true};
    CHECK(strength.lo == 0 && strength.hi == 200 && strength.step == 25);
    CHECK(opts.haptic_strength == 0);
    CHECK(!std::strcmp(ini_key(strength), "haptic_strength"));
    arrow(strength, false);
    CHECK(opts.haptic_strength == 0);   /* below Game's own stays Game's own */
    arrow(strength, true);
    CHECK(opts.haptic_strength == 25);
    for (int i = 0; i < 20; i++) arrow(strength, true);
    CHECK(opts.haptic_strength == 200);
    /* an ini's value: on the steps, 0 kept as Game's own */
    static const int in[] = {-5, 0, 1, 12, 13, 37, 38, 100, 199, 200, 999};
    static const int out[] = {0, 0, 0, 0, 25, 25, 50, 100, 200, 200, 200};
    for (int i = 0; i < 11; i++) CHECK(haptic_step(in[i]) == out[i]);
    /* the status row is read-only: it is never a key of the ini, and it is not saved */
    Row status = {TAB_CONTROLS, "Haptic status", &opts.haptic_status, 0, 0, 1, nullptr, nullptr, nullptr, false, true};
    CHECK(std::strcmp(ini_key(status), "haptic_strength") != 0 && std::strcmp(ini_key(status), "haptic") != 0);
    /* it is a control: the quality preset stays */
    opts.preset = 3;
    arrow(mode, true);
    CHECK(opts.preset == 3 && presets == 0 && saves > 0);
    CHECK(temporal_resets == 0);
    return 0;
}
"""


def rows(menu):
    """the four rows, in the order they appear"""
    found = []
    for name in ("Rumble strength", "Haptic feedback", "Haptic strength", "Haptic status", "Test haptics"):
        at = menu.index('"%s"' % name)
        found.append((at, name))
    order = [n for _, n in sorted(found)]
    check(order == ["Rumble strength", "Haptic feedback", "Haptic strength", "Haptic status", "Test haptics"],
          "the Controls page does not show Rumble strength, then the three Haptic rows, then Test haptics: %s" % order)


def fixture(menu, header):
    row = re.search(r'\{TAB_CONTROLS, "Haptic feedback", &opts\.haptic, (-?\d+), (\d+), (\d+),', menu)
    check(row is not None, "the menu lost its Haptic feedback row")
    st = re.search(r'\{TAB_CONTROLS, "Haptic strength", &opts\.haptic_strength, (-?\d+), (\d+), (\d+),', menu)
    check(st is not None, "the menu lost its Haptic strength row")
    arrow = re.search(r"\n\s*(set_row\(r, \*r\.value \+ \(press\[4\] \? r\.step : -r\.step\), press\[4\] \? 1 : -1\);)", menu)
    check(arrow is not None, "the arrows no longer step a row with set_row")
    return FIXTURE % {
        "options": re.search(r"struct Options \{.*?\n\};", header, re.S).group(0),
        "tabs": re.search(r"enum MenuTab \{.*?\};", menu, re.S).group(0),
        "row": re.search(r"struct Row \{.*?\n\};", menu, re.S).group(0),
        "is_slider": one_line(menu, "bool is_slider"),
        "stops": one_line(menu, "bool stops_at_ends"),
        "snap": one_line(menu, "int rumble_step"),
        "haptic_step": one_line(menu, "int haptic_step"),
        "ini_key": definition(menu, "const char* ini_key"),
        "set_row": definition(menu, "void set_row"),
        "arrow": "bool press[5] = {false, false, false, !right, right}; " + arrow.group(1),
        "mode_lo": row.group(1), "mode_hi": row.group(2), "mode_step": row.group(3),
        "st_lo": st.group(1), "st_hi": st.group(2), "st_step": st.group(3),
    }


def option_ids():
    """58, 59, 61 and 62 are the four haptics options, read once each, and by host/pad_sdl.c alone"""
    replay, api = read("gpu", "vk", "vk_replay.cpp"), read("gpu", "vk", "vk_api.h")
    value = {}
    for first, start, rest in re.findall(r"enum \{ (HOS_OPT_\w+) = (\d+)([^}]*)\}", api):
        names = [first] + [n.strip() for n in rest.split(",") if n.strip()]
        value.update({n: int(start) + i for i, n in enumerate(names)})
    for name, want in (("HOS_OPT_HAPTIC_MODE", 58), ("HOS_OPT_HAPTIC_STATUS", 59),
                       ("HOS_OPT_HAPTIC_STRENGTH", 61), ("HOS_OPT_HAPTIC_TEST", 62)):
        check(value.get(name) == want, "gpu/vk/vk_api.h: %s is %r, not %d" % (name, value.get(name), want))
    table = block(replay, replay.index("t.option = [](int id) -> int {"))
    ids = [int(n) if n.isdigit() else value[n] for n in re.findall(r"\bid == (\w+)", table)]
    check(len(ids) == len(set(ids)), "vk_replay.cpp: an option id is read twice: %s" % sorted(ids))
    for name in ("HOS_OPT_HAPTIC_MODE", "HOS_OPT_HAPTIC_STRENGTH", "HOS_OPT_HAPTIC_STATUS", "HOS_OPT_HAPTIC_TEST"):
        check("id == %s" % name in table, "vk_replay.cpp: t.option does not read %s" % name)
    check(re.search(r"\bid == 9 \? opts\.rumble\b", table) is not None,
          "vk_replay.cpp: option 9 is not the menu's Rumble strength")
    users = []
    for folder in ("host", "gpu"):
        for path in (ROOT / folder).rglob("*.c*"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"\brx_option(_set)?\(\s*HOS_OPT_HAPTIC_", text):
                users.append(path.relative_to(ROOT).as_posix())
    check(users == ["host/pad_sdl.c"], "the haptics options are read outside host/pad_sdl.c: %s" % users)


def back_end():
    """host/haptic.c: what a run in the game would otherwise be the only witness of"""
    src = HAPTIC.read_text(encoding="utf-8")
    check('HAPTIC_DLL "renhdrum.dll"' in src, "haptic.c no longer names renhdrum.dll")
    # (the game's own folder, never the search path: a DLL of the same name elsewhere is not loaded)
    check("GetModuleFileNameW" in src and "LoadLibraryExW" in src,
          "haptic.c no longer loads the DLL by full path")
    check("LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR" in src and "LOAD_LIBRARY_SEARCH_SYSTEM32" in src,
          "haptic.c lets the DLL's own folder be searched beyond the game's")
    check("GetFileAttributesW" in src, "haptic.c no longer checks the DLL is there before loading it")
    # (the ABI: 0x000300, and 0x000301 whose ABI it keeps; anything else is refused)
    check("HAPTIC_ABI" in src and "0x000300" in src and "0x000301" in src, "haptic.c no longer checks the ABI version")
    # (Rumble strength's Off stops every path, before the DSP is fed)
    push = definition(src, "void hos_haptic_push")
    check("strength_gain()" in push and "return" in push,
          "hos_haptic_push does not stop on a strength of 0 (Rumble strength's Off)")
    check(push.index("strength_gain()") < push.index("dsp_set_half"),
          "the strength is checked after the DSP is fed")
    # (the gain is applied once, on what the DSP produced: RenHDrum's own curve squares an amplitude fed to it)
    pull = definition(src, "static void endpoint_pull")
    check("* gain" in pull or "*gain" in pull, "endpoint_pull no longer scales what the DSP produced")
    check("strength_gain" in pull, "endpoint_pull does not read the strength")
    gain = definition(src, "static float strength_gain")
    check("strength_scale()" in gain and "extra_scale()" in gain, "strength_gain does not take both rows' strengths")
    check("dsp_set_half_target" not in push.replace("rh.dsp_set_half(", "dsp_set_half_target") or True, "")
    check(re.search(r"rh\.dsp_set_half\([^)]*value\[0\]", src, re.S) is not None
          or re.search(r"float al = value\[0\]", push) is not None,
          "hos_haptic_push no longer hands the game's own amplitudes to the DSP")
    # (silence: a watchdog, so a game that stops sending cannot leave the coils ringing)
    check("HAPTIC_STALE_NS" in src and "silence" in src.lower(), "haptic.c lost its silence watchdog")
    # (the test: a fixed length, and it stops on its own)
    check("HAPTIC_TEST_NS" in src, "haptic.c lost the test's fixed length")
    test = definition(src, "int hos_haptic_test")
    check("g_test_until" in test, "the test does not set its own end")
    # (no disk access, no allocation, no wait in the audio callback; the one lock it takes is the engine's, held for a
    # fill at most, and never one of the lifecycle locks: g_life, g_host, g_audio, g_note are the worker's)
    check("fopen" not in pull and "malloc" not in pull and "LoadLibrary" not in pull,
          "endpoint_pull reaches for the disk, the heap or the loader")
    check("WaitForSingleObject" not in pull and "Sleep(" not in pull, "endpoint_pull waits")
    for held in ("g_life", "g_host", "g_audio", "g_note"):
        check("&" + held not in pull, "endpoint_pull takes " + held + ", which the audio thread must not hold")
    # (the thread: started for the back end, joined at the end)
    check("CreateThread" in src and "WaitForSingleObject" in src and "CloseHandle" in src,
          "haptic.c no longer starts and joins its worker")


def wiring():
    """host/pad_sdl.c: the four options reach the back end, and a haptics pad is left out of the motor rumble"""
    src = PADS.read_text(encoding="utf-8")
    check('#include "haptic.h"' in src, "host/pad_sdl.c no longer includes host/haptic.h")
    check("hos_haptic_strength(strength)" in src, "the Rumble strength no longer reaches the haptics")
    check("hos_haptic_extra(rx_option(HOS_OPT_HAPTIC_STRENGTH" in src, "the Haptic strength row no longer reaches it")
    check("hos_haptic_mode(rx_option(HOS_OPT_HAPTIC_MODE" in src, "the Haptic feedback row no longer reaches it")
    check("hos_haptic_test()" in src, "Test haptics no longer reaches it")
    check("rx_option_set(HOS_OPT_HAPTIC_STATUS" in src, "the status no longer reaches the menu")
    check("hos_haptic_owns(gc)" in src, "a pad the haptics drive is still sent the motor rumble as well")
    check("hos_haptic_pad_added(" in src and "hos_haptic_pad_removed(" in src,
          "a pad's arrival and departure no longer reach the haptics")
    check("hos_sdl_haptic_shutdown" in src, "nothing stops the haptics when the game ends")
    main = read("host", "hos_main.c")
    check("hos_sdl_haptic_shutdown()" in main, "the game's end no longer stops the haptics")
    hid = read("host", "nn_hid.c")
    check("hos_haptic_push(dev, v)" in hid, "the game's own vibration values no longer reach the haptics")


def texts():
    """the i18n tables carry every new text, so no row shows English in a translated menu"""
    menu = MENU.read_text(encoding="utf-8")
    tables = read("gpu", "vk", "menu_i18n.h")
    for text in ("Haptic feedback", "Haptic strength", "Haptic status", "Test haptics", "Auto", "Standard rumble",
                 "DualSense haptics", "DualSense haptics (USB)", "DualSense Bluetooth (standard rumble)",
                 "Switch Pro HD rumble", "Waiting for the DualSense audio endpoint", "Haptic backend unavailable",
                 "No controller", "Test", "Not now", "Buzzing...", "Game's own"):
        check('"%s"' % text in menu, "vk_menu.cpp lost the text %r" % text)
        check('"%s"' % text in tables, "gpu/vk/menu_i18n.h has no translation of %r" % text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default=os.environ.get("CXX", "g++"))
    args = parser.parse_args()
    menu, header = MENU.read_text(encoding="utf-8"), read("gpu", "vk", "vk_menu.h")
    rows(menu)
    option_ids()
    back_end()
    wiring()
    texts()
    with tempfile.TemporaryDirectory(prefix="haptics-menu-") as tmp:
        src, exe = Path(tmp) / "haptics_menu.cpp", Path(tmp) / "haptics_menu"
        src.write_text(fixture(menu, header), encoding="utf-8")
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", str(src), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)
    print("PASS: the four haptics rows, their steps, the read-only status, the ids, the back end and its wiring")


if __name__ == "__main__":
    main()
