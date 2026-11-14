"""The Debug tab's Recorder thread row: RX_VK_RECORDER=thread with RX_VK_RECORDER_OWN, chosen in the menu. No game, no GPU.

1. The mode (gpu/vk/vk_recorder.h, from enum Mode to mode()) compiled with gpu/vk/vk_draw.cpp's real menu_env_on and the line
   that hands it to the recorder (rec::g_row_on), over a stub game folder (the fixture's own) holding a longshot_options.ini
   or none. Every case of RX_VK_RECORDER {unset, thread, sync, off, 0} x the row {no file, 0, 1} x RX_VK_PARALLEL {unset, 1,
   4} x RX_VK_RECORDER_OWN {unset, 0, 1}: the mode, g_own, the one start or refusal line and which source it names
   (RX_VK_RECORDER set to anything decides alone; unset, the row; the row's own on unless RX_VK_RECORDER_OWN is set, then by
   its value; refused with RX_VK_PARALLEL>=2). mode() is first called from four threads at once: one mode, one line.
2. The row's line as menu_env_on reads it, RX_VK_RECORDER unset: no file, no line, recorder_thread=0, =1, and a longer key with
   the same start (recorder_thread_x=1 is not the row's).
3. The menu (gpu/vk/vk_menu.cpp): the row is in the table once, on the Debug tab, Off/On, a restart row with a slot in
   menu_restart.h's State, preset-exempt, default 0 (gpu/vk/vk_menu.h); the real ini_key gives it recorder_thread, the key
   step 1 reads; the real hn_recorder_thread says the recorder is not running only when the row was On at the start and the
   recorder is not in thread mode (or could not start), and lists its one line for the menu's height.
Each part has broken variants of the code it cuts that must fail it.

python -I -B tests/test_recorder_thread_row.py [--cxx clang++] [--root <checkout>]
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]
OFF, SYNC, THREAD = 0, 1, 3
ROW_LINE = "LOG: vk: the Recorder thread row (recorder_thread=1 in longshot_options.ini"
ENV_LINE = "LOG: vk: RX_VK_RECORDER=thread: vkCmd* packed"
ROW_REFUSED = "LOG: vk: the Recorder thread row (recorder_thread=1 in longshot_options.ini) refused: RX_VK_PARALLEL=4"
ENV_REFUSED = "LOG: vk: RX_VK_RECORDER=thread refused: RX_VK_PARALLEL=4"


def find(text, marker, where):
    at = text.find(marker)
    if at < 0:
        raise SystemExit("FAIL: %s: anchor not found: %r" % (where, marker))
    return at


def block(text, marker, where):
    """From marker to the end of the brace block it opens"""
    start = find(text, marker, where)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


MODE_FIXTURE = r"""
#include <algorithm>
#include <atomic>
#include <cstdarg>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include "longshot_env.h"
using DWORD = unsigned long;
constexpr int MAX_PATH = 260;
static DWORD game_module_path(char* path, DWORD size) { return (DWORD)snprintf(path, size, "./longshot.exe"); }
namespace mkwvk {
std::mutex log_mu;
void logf(const char* fmt, ...) {
    std::lock_guard lk(log_mu);
    va_list a;
    va_start(a, fmt);
    std::printf("LOG: ");
    std::vprintf(fmt, a);
    std::printf("\n");
    va_end(a);
}
namespace rec {
@@RECORDER@@
} // namespace rec
namespace {
@@MENU_ENV_ON@@
@@HOOK@@
} // namespace
} // namespace mkwvk
int main() {
    namespace rec = mkwvk::rec;
    std::atomic<int> seen[4];
    std::atomic<bool> go{false};
    std::thread t[4];
    for (int i = 0; i < 4; i++)
        t[i] = std::thread([&, i] {
            while (!go.load()) {}
            seen[i] = rec::mode();
        });
    go = true;
    for (auto& th : t) th.join();
    for (int i = 1; i < 4; i++)
        if (seen[i] != seen[0]) {
            std::printf("FAIL: the threads read modes %d and %d\n", seen[0].load(), seen[i].load());
            return 1;
        }
    std::printf("mode %d own %d\n", rec::mode(), (int)rec::g_own);
    return 0;
}
"""

MENU_FIXTURE = r"""
#include <atomic>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
namespace mkwvk {
namespace rec {
enum Mode : int { OFF = 0, SYNC = 1, DEFER = 2, THREAD = 3 };
int stub_mode = OFF;
int mode() { return stub_mode; }
struct Recorder { std::atomic<bool> broken{false}; };
Recorder g_rec;
} // namespace rec
struct Options { @@FIELDS@@ };
Options opts;
struct Row { const char* name; int* value; };
Row rows[] = {{"Sampler cache", &opts.sampler_cache}, {"Recorder thread", &opts.recorder_thread}};
constexpr int kRows = 2;
int restart_slot[kRows] = {-1, 0};
struct { int start[16]; } restart_state;
namespace {
@@INI_KEY@@
@@HINT@@
} // namespace
} // namespace mkwvk
int main() {
    using namespace mkwvk;
    int bad = 0;
    if (strcmp(ini_key(rows[1]), "recorder_thread")) std::printf("FAIL: ini_key %s\n", ini_key(rows[1])), bad++;
    // (the row's start value, the recorder's mode, its thread could not start: whether the line shows)
    struct { int start, mode; bool broken, shows; } cases[] = {
        {1, rec::THREAD, false, false}, {1, rec::OFF, false, true}, {1, rec::SYNC, false, true}, {1, rec::THREAD, true, true},
        {0, rec::OFF, false, false}, {0, rec::THREAD, false, false}, {0, rec::THREAD, true, false},
    };
    for (auto& c : cases)
        for (int v = 0; v <= 1; v++) {
            restart_state.start[0] = c.start, rec::stub_mode = c.mode, rec::g_rec.broken = c.broken, opts.recorder_thread = v;
            const char* h = hn_recorder_thread(v, -1);
            if ((h != nullptr) != c.shows)
                std::printf("FAIL: started %d, mode %d, broken %d, row now %d: line %s\n", c.start, c.mode, c.broken, v,
                            h ? h : "(none)"), bad++;
            if (!hn_recorder_thread(v, 0) || hn_recorder_thread(v, 1))
                std::printf("FAIL: hint(%d, 0..) does not list exactly one line\n", v), bad++;
        }
    // (no restart slot: the start value is unknown, no line)
    restart_slot[1] = -1, restart_state.start[0] = 1, rec::stub_mode = rec::OFF;
    if (hn_recorder_thread(1, -1)) std::printf("FAIL: a line with no restart slot\n"), bad++;
    if (!bad) std::printf("menu ok\n");
    return bad != 0;
}
"""


def build(cxx, work, name, source, root):
    src, exe = work / (name + ".cpp"), work / (name + ".exe")
    src.write_text(source, encoding="utf-8")
    r = subprocess.run([cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-Wno-unused-variable",
                        "-D_CRT_SECURE_NO_WARNINGS", "-I", str(root / "gpu/vk/compat"), str(src), "-o", str(exe)],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if r.returncode:
        return None, r.stdout
    return exe, ""


def write_ini(folder, text):
    ini = folder / "longshot_options.ini"
    if ini.exists():
        ini.unlink()
    if text is not None:
        ini.write_text(text, encoding="ascii")


def run_mode(exe, folder, env_rec, par, own):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("RX_VK_RECORDER", "RX_VK_PARALLEL"))}
    for k, v in (("RX_VK_RECORDER", env_rec), ("RX_VK_PARALLEL", par), ("RX_VK_RECORDER_OWN", own)):
        if v is not None:
            env[k] = v
    r = subprocess.run([str(exe)], cwd=folder, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                       timeout=60)
    lines = r.stdout.splitlines()
    m = re.search(r"^mode (\d+) own (\d+)$", r.stdout, re.M)
    if r.returncode or not m:
        return None, None, lines
    return int(m.group(1)), int(m.group(2)), [x for x in lines if x.startswith("LOG: ")]


def expect(env_rec, row, par, own):
    """(mode, own, the one line's start or None) as the decisions say"""
    par_on = par is not None and int(par) >= 2
    own_env = own is not None and own != "0"
    if env_rec is None:
        if row != "1":
            return OFF, 0, None
        if par_on:
            return OFF, 0, ROW_REFUSED
        return THREAD, 1 if own is None else int(own_env), ROW_LINE
    if env_rec == "sync":
        return SYNC, 0, None
    if env_rec == "thread":
        if par_on:
            return OFF, 0, ENV_REFUSED
        return THREAD, int(own_env), ENV_LINE
    return OFF, 0, None


ROWS = {None: None, "0": "options_version=5\nrecorder_thread=0\n", "1": "options_version=5\nrecorder_thread=1\n"}


def check_modes(exe, folder, first_only=False):
    """Part 1's matrix: the first failure, or None"""
    for row, text in ROWS.items():
        write_ini(folder, text)
        for env_rec in (None, "thread", "sync", "off", "0"):
            for par in (None, "1", "4"):
                for own in (None, "0", "1"):
                    mode, got_own, logs = run_mode(exe, folder, env_rec, par, own)
                    want_mode, want_own, want_line = expect(env_rec, row, par, own)
                    case = "RX_VK_RECORDER=%s row=%s RX_VK_PARALLEL=%s RX_VK_RECORDER_OWN=%s" % (env_rec, row, par, own)
                    if mode is None:
                        return "%s: the fixture failed: %s" % (case, " | ".join(logs))
                    if (mode, got_own) != (want_mode, want_own):
                        return "%s: mode %d own %d, want mode %d own %d" % (case, mode, got_own, want_mode, want_own)
                    if want_line is None and logs:
                        return "%s: a line where none is due: %s" % (case, logs)
                    if want_line is not None and (len(logs) != 1 or not logs[0].startswith(want_line)):
                        return "%s: lines %s, want one starting %r" % (case, logs, want_line)
                    if want_mode == THREAD and want_own and want_line == ROW_LINE and own is None and "(with the row)" not in logs[0]:
                        return "%s: the start line does not say the row turned the own resets on: %s" % (case, logs[0])
    return None


# (part 2: the options file's text, RX_VK_RECORDER, RX_VK_PARALLEL and RX_VK_RECORDER_OWN unset -> the mode)
INI_CASES = (
    (None, OFF),
    ("options_version=5\nsampler_cache=1\n", OFF),
    ("recorder_thread=0\n", OFF),
    ("recorder_thread=1\n", THREAD),
    ("recorder_thread_x=1\n", OFF),
    ("recorder_thread_x=1\nrecorder_thread=0\n", OFF),
    ("recorder_thread_x=0\noptions_version=5\nrecorder_thread=1\n", THREAD),
)


def check_ini(exe, folder):
    for text, want in INI_CASES:
        write_ini(folder, text)
        mode, _, logs = run_mode(exe, folder, None, None, None)
        if mode != want:
            return "options file %r: mode %s, want %d (%s)" % (text, mode, want, logs)
    return None


def menu_parts(root):
    menu = (root / "gpu/vk/vk_menu.cpp").read_text(encoding="utf-8")
    header = (root / "gpu/vk/vk_menu.h").read_text(encoding="utf-8")
    restart_h = (root / "gpu/vk/menu_restart.h").read_text(encoding="utf-8")
    table = menu[find(menu, "Row rows[] = {", "vk_menu.cpp"):]
    table = table[:table.index("\n};")]
    entries = re.split(r"\n    \{TAB_", table)[1:]
    mine = [e for e in entries if re.match(r'\w+, "Recorder thread"', e)]
    if len(mine) != 1:
        raise SystemExit("FAIL: vk_menu.cpp: %d rows named Recorder thread, want 1" % len(mine))
    e = mine[0]
    want = (r'DEBUG, "Recorder thread", &opts\.recorder_thread, 0, 1, 1, tx_onoff,\s+"[^{}]*Applies after a restart\.",'
            r'\s*hn_recorder_thread, true, true\}')
    if not re.match(want, e):
        raise SystemExit("FAIL: vk_menu.cpp: the Recorder thread row is not Debug, Off/On, hn_recorder_thread, restart, "
                         "preset-exempt, help ending 'Applies after a restart.': %r" % e[:400])
    restarts = [re.match(r'\w+, "([^"]+)"', x).group(1) for x in entries if (re.findall(r"\b(true|false)\b", x) or [""])[0] == "true"]
    kmax = int(re.search(r"constexpr int kMax = (\d+);", restart_h).group(1))
    if "Recorder thread" not in restarts or restarts.index("Recorder thread") >= kmax:
        raise SystemExit("FAIL: the Recorder thread row has no restart slot (%d restart rows, kMax %d)" % (len(restarts), kmax))
    if not re.search(r"^    int recorder_thread = 0;", header, re.M):
        raise SystemExit("FAIL: vk_menu.h: Options::recorder_thread is not 0 by default")
    ini_key = block(menu, "const char* ini_key(const Row& r) {", "vk_menu.cpp")
    hint = block(menu, "const char* hn_recorder_thread(int, int variant) {", "vk_menu.cpp")
    fields = sorted(set(re.findall(r"&opts\.(\w+)", ini_key)) | {"sampler_cache", "recorder_thread"})
    return ini_key, hint, fields


BROKEN_RECORDER = (
    ("the row not read", 'if (!g_row_on || !g_row_on("RX_VK_RECORDER", "recorder_thread", false, 0)) return OFF;', "return OFF;"),
    ("the row read over RX_VK_RECORDER", 'const char* e = getenv("RX_VK_RECORDER");',
     'const char* e = g_row_on && g_row_on("RX_VK_RECORDER_UNSET", "recorder_thread", false, 0) ? nullptr : getenv("RX_VK_RECORDER");'),
    ("the row not refused with RX_VK_PARALLEL", "*refused = parallel_configured();", "*refused = false;"),
    ("the row's own resets not on by default", "g_own = own ? !env_value_off(own) : row;", "g_own = own ? !env_value_off(own) : false;"),
    ("RX_VK_RECORDER_OWN=0 not heard with the row", "g_own = own ? !env_value_off(own) : row;", "g_own = row || (own && !env_value_off(own));"),
    ("the start line names the environment for the row", 'row ? "the Recorder thread row (', 'false ? "the Recorder thread row ('),
    ("the refusal names the variable for the row", "if (refused && row)", "if (false)"),
)
BROKEN_DRAW = (
    ("the row's line matched by its start", "if (!strncmp(line, ini_key_name, klen) && line[klen] == '=') v = atoi(line + klen + 1);",
     "if (!strncmp(line, ini_key_name, klen) && strchr(line, '=')) v = atoi(strchr(line, '=') + 1);"),
)
BROKEN_HINT = (
    ("the line whatever the start value", "return started_on && (", "return ("),
    ("a thread that could not start not said", " || rec::g_rec.broken.load(std::memory_order_relaxed)", ""),
    ("not listed for the menu's height", "if (variant >= 0) return variant == 0 ? off : nullptr;",
     "if (variant >= 0) return nullptr;"),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default=os.environ.get("CXX", "clang++"))
    parser.add_argument("--root", type=Path, default=ROOT, help="the checkout to check (default: this one)")
    args = parser.parse_args()
    cxx = shutil.which(args.cxx)
    if not cxx:
        raise SystemExit("C++ compiler not found: " + args.cxx)
    root = args.root.resolve()
    rec_h = (root / "gpu/vk/vk_recorder.h").read_text(encoding="utf-8")
    draw = (root / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    start = find(rec_h, "enum Mode : int", "vk_recorder.h")
    recorder = rec_h[start:rec_h.index(block(rec_h, "inline int mode() {", "vk_recorder.h"))] + block(rec_h, "inline int mode() {", "vk_recorder.h")
    menu_env_on = block(draw, "bool menu_env_on(const char* env_name, const char* ini_key_name, bool def = false, int since = 0) {",
                        "vk_draw.cpp")
    hooks = [x for x in draw.splitlines() if "rec::g_row_on" in x]
    if len(hooks) != 1:
        raise SystemExit("FAIL: vk_draw.cpp: %d lines set rec::g_row_on, want 1 (the recorder would not read the row)" % len(hooks))
    hook = hooks[0]
    ini_key, hint, fields = menu_parts(root)

    def mode_source(recorder_text, menu_env_on_text, hook_text):
        return (MODE_FIXTURE.replace("@@HOOK@@", hook_text).replace("@@MENU_ENV_ON@@", menu_env_on_text)
                .replace("@@RECORDER@@", recorder_text))

    def menu_source(hint_text):
        return (MENU_FIXTURE.replace("@@FIELDS@@", " ".join("int %s = 0;" % f for f in fields)).replace("@@INI_KEY@@", ini_key)
                .replace("@@HINT@@", hint_text))

    with tempfile.TemporaryDirectory(prefix="recorder-row-") as tmp:
        work = Path(tmp)
        folder = work / "game"
        folder.mkdir()
        exe, err = build(cxx, work, "mode", mode_source(recorder, menu_env_on, hook), root)
        if not exe:
            raise SystemExit("FAIL: the mode fixture does not build:\n" + err)
        bad = check_modes(exe, folder)
        if bad:
            raise SystemExit("FAIL: " + bad)
        bad = check_ini(exe, folder)
        if bad:
            raise SystemExit("FAIL: " + bad)
        menu_exe, err = build(cxx, work, "menu", menu_source(hint), root)
        if not menu_exe:
            raise SystemExit("FAIL: the menu fixture does not build:\n" + err)
        r = subprocess.run([str(menu_exe)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)
        if r.returncode or "menu ok" not in r.stdout:
            raise SystemExit("FAIL: the menu: " + r.stdout)
        print("PASS: 135 cases of RX_VK_RECORDER x the row x RX_VK_PARALLEL x RX_VK_RECORDER_OWN (mode, own resets, the line and "
              "its source; first read on 4 threads); the options file's line (%d cases); the menu's row, key and line" % len(INI_CASES))

        # (the broken variants: each must fail its part)
        caught = 0
        variants = [(n, mode_source(recorder.replace(a, b), menu_env_on, hook), a, recorder) for n, a, b in BROKEN_RECORDER]
        variants += [(n, mode_source(recorder, menu_env_on.replace(a, b), hook), a, menu_env_on) for n, a, b in BROKEN_DRAW]
        variants.append(("no line hands menu_env_on to the recorder", mode_source(recorder, menu_env_on, ""), hook, hook))
        for i, (name, source, anchor, text) in enumerate(variants):
            if anchor not in text:
                raise SystemExit("FAIL: broken variant %r: anchor not found: %r" % (name, anchor))
            exe_b, err = build(cxx, work, "broken%d" % i, source, root)
            if not exe_b:
                raise SystemExit("FAIL: broken variant %r does not build:\n%s" % (name, err))
            why = check_ini(exe_b, folder) or check_modes(exe_b, folder)
            if not why:
                raise SystemExit("FAIL: broken variant passes: " + name)
            print("  caught, %s: %s" % (name, why))
            caught += 1
        for i, (name, a, b) in enumerate(BROKEN_HINT):
            if a not in hint:
                raise SystemExit("FAIL: broken variant %r: anchor not found: %r" % (name, a))
            exe_b, err = build(cxx, work, "broken_hint%d" % i, menu_source(hint.replace(a, b)), root)
            if not exe_b:
                raise SystemExit("FAIL: broken variant %r does not build:\n%s" % (name, err))
            r = subprocess.run([str(exe_b)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)
            if r.returncode == 0:
                raise SystemExit("FAIL: broken variant passes: " + name)
            print("  caught, %s: %s" % (name, r.stdout.splitlines()[0] if r.stdout else r.returncode))
            caught += 1
        print("PASS: %d broken variants caught" % caught)


if __name__ == "__main__":
    main()
