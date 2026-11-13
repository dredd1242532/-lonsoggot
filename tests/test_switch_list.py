"""One list of the game's switches (host/switches.def, the dev-loop proposal's E1), kept in step with the reads.

Every switch host/, gpu/vk, gpu/render and gpu/compat read (scripts/switch_list.py: getenv, env_on, env_int, env_float,
menu_env_on, env_override with its name written out, or a table's string literal) is listed, and every listed one is
still read; each line is well formed, in order, and names a file that reads it as its area. build.py counts the list
among the host's headers. longshot_env.txt's allow-list (host/hos_main.c env_line_allowed) is cut out, compiled with the
list and run: every LOCAL switch is refused, the names it refused before the list are LOCAL, a name ending in _FILE or
_DIR is refused though not listed, and the rest behaves as before (RX_VK_DLSS_DIR, paths in values, the system's names).
scripts/switch_list.py --update is run on a copy: a missing switch gets its line, one read nowhere goes, an area that
no longer reads its switch moves, the file's head stays as it was. No game or GPU is needed.

    python -I -B tests/test_switch_list.py [--cxx clang++]
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import switch_list  # noqa: E402

# (env_line_allowed's own table and its two names of other shapes, before host/switches.def: these stay LOCAL)
REFUSED_BEFORE = {"HOS_AUDIO_DUMP", "HOS_AUDIO_WB_DUMP", "HOS_SHADER_DUMP", "HOS_FS_OVERLAY", "HOS_MODS",
                  "HOS_INPUT_LIVE", "HOS_INPUT_SCRIPT", "HOS_SAMPLE_ALL", "HOS_CHAIN", "HOS_WATCH",
                  "LONGSHOT_BUILDER_HOME", "RX_VK_SHOTS", "RX_VK_SHOT_GO", "RX_VK_FRAMELOG", "RX_VK_HANG_DUMP",
                  "RX_VK_DUMP", "RX_VK_DUMP_SPV", "RX_VK_DUMP_MESH_SPV", "RX_VK_DUMP_STAGES", "RX_VK_DUMP_FAILED",
                  "RX_VK_IR", "RX_VK_MENU_SCRIPT", "RX_DUMP_WGSL", "RX_DUMP_SLOW_WGSL", "RX_DUMP_TICS", "RX_WGSL_OVERRIDE",
                  "RX_VK_FRAME_LOG", "HOS_PRESENT_TRACE"}
CASES = [   # (name, value, taken from longshot_env.txt)
    ("RX_VK_NOOPT", "1", 1), ("RX_VK_PACE", "1", 1), ("HOS_WINDOW_SIZE", "1280x720", 1),
    ("HOS_AUDIO_DUMP", "image.bin", 0), ("HOS_INPUT_SCRIPT", "0:A", 0), ("RX_VK_FRAME_LOG", "f.csv", 0),
    ("HOS_SAMPLE_FILE", "longshot_vk.dll", 0), ("RX_NOT_LISTED_YET_DIR", "x", 0), ("RX_NOT_LISTED_YET_FILE", "x", 0),
    ("RX_VK_DLSS_DIR", "C:\\dlss", 1), ("RX_VK_DLSS_DIR", "\\\\server\\x", 0),
    ("RX_VK_SOMETHING", "a/b", 0), ("RX_VK_SOMETHING", "C:x", 0), ("RX_VK_SOMETHING", "..", 0),
    ("PATH", "x", 0), ("HOS_ENV_FILE", "x", 0), ("SDL_VULKAN_LIBRARY", "x", 0), ("rx_vk_lower", "1", 0)]

FIXTURE = r"""
#include <stdio.h>
#include <string.h>
%FUNCTION%
int main(int argc, char **argv) {
    if (argc > 1 && !strcmp(argv[1], "local")) {   /* every LOCAL switch, and what the allow-list says of it */
#define SWITCH(name, type, scope, area, what) if (!strcmp(#scope, "LOCAL")) printf("%s %d\n", #name, env_line_allowed(#name, "x"));
#include "switches.def"
#undef SWITCH
        return 0;
    }
    for (int i = 1; i + 1 < argc; i += 2) printf("%d\n", env_line_allowed(argv[i], argv[i + 1]));
    return 0;
}
"""


def function(text, marker):
    start = text.index(marker)
    end = text.index("\n}\n", start) + 3
    return text[start:end]


def check_build_reads_it():
    """build.py counts the list among the host's headers: an edit to it compiles host/hos_main.c again"""
    build = (ROOT / "nx2" / "build.py").read_text(encoding="utf-8")
    deps = build[build.index("host_deps = deps + "):]
    assert 'f.endswith((".h", ".def"))' in deps[:deps.index("\n    if LINUX:")], "build.py: host/switches.def is no dependency"


def check_list():
    missing, gone, _, have = switch_list.compare()
    assert not missing and not gone, ("read, not listed: %s; listed, read nowhere: %s (python -I scripts/switch_list.py "
                                      "--update)" % (missing, gone))
    assert list(have) == sorted(have), "host/switches.def is not in order"
    for name in REFUSED_BEFORE:
        assert have[name][1] == "LOCAL", "%s was refused from longshot_env.txt and is %s now" % (name, have[name][1])
    found = switch_list.reads()
    for name, (typ, scope, area, what) in have.items():
        assert area in found[name]["files"], "%s: %s does not read it (it is read in %s)" % (name, area, sorted(found[name]["files"]))
        assert scope == "SYSTEM" or re.match(r"(RX|HOS|LONGSHOT|EDEN|NVN)_", name), "%s: not the game's, and %s" % (name, scope)
    return have


def check_allowed(cxx, have, tmp):
    src = (ROOT / "host" / "hos_main.c").read_text(encoding="utf-8")
    code = FIXTURE.replace("%FUNCTION%", function(src, "static int env_line_allowed(const char *name, const char *value) {"))
    c, exe = Path(tmp) / "allowed.c", Path(tmp) / "allowed.exe"
    c.write_text(code, encoding="utf-8")
    r = subprocess.run([cxx, "-x", "c", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "host"), str(c), "-o",
                        str(exe)], capture_output=True, text=True)
    assert r.returncode == 0, "the allow-list did not compile:\n" + r.stdout + r.stderr
    out = subprocess.run([str(exe)] + [x for n, v, _ in CASES for x in (n, v)], capture_output=True, text=True,
                         check=True).stdout.split()
    for (name, value, want), got in zip(CASES, out):
        assert int(got) == want, "%s=%s: taken %s, should be %s" % (name, value, got, want)
    local = dict(l.split() for l in subprocess.run([str(exe), "local"], capture_output=True, text=True,
                                                   check=True).stdout.splitlines())
    assert set(local) == {n for n, v in have.items() if v[1] == "LOCAL"}
    assert all(v == "0" for v in local.values()), [n for n, v in local.items() if v != "0"]


def check_update(tmp):
    """--update on a copy of the tree's list: a switch read and not listed gets its line, one read nowhere goes, the head
    (whose comment shows a SWITCH( line itself) stays"""
    root = Path(tmp) / "tree"
    (root / "host").mkdir(parents=True)
    (root / "gpu" / "vk").mkdir(parents=True)
    head = "/* the list\n\n     SWITCH(NAME, TYPE, SCOPE, \"area\", \"what\")\n*/\n"
    (root / "host" / "switches.def").write_text(head + 'SWITCH(HOS_GONE, FLAG, GAME, "host/a.c", "no longer read")\n'
                                                'SWITCH(HOS_KEPT, FLAG, GAME, "host/a.c", "kept as it was")\n'
                                                'SWITCH(HOS_MOVED, FLAG, GAME, "host/old.c", "read elsewhere now")\n',
                                                encoding="utf-8")
    (root / "host" / "a.c").write_text('int a(void) { return env_on("HOS_KEPT") + env_int("HOS_NEW_LEVEL", 2) + '
                                       'env_on("HOS_MOVED"); }\n'
                                       'const char *b(void) { return getenv("RX_NEW_DUMP_DIR"); }\n'
                                       'static const char *const names[] = {"RX_VK_FROM_A_TABLE"};\n', encoding="utf-8")
    (root / "gpu" / "vk" / "b.cpp").write_text('bool f() { return getenv("HOME") != nullptr; }\n', encoding="utf-8")
    path = str(root / "host" / "switches.def")
    missing, gone = switch_list.update(str(root), path)
    assert missing == ["HOME", "HOS_NEW_LEVEL", "RX_NEW_DUMP_DIR", "RX_VK_FROM_A_TABLE"] and gone == ["HOS_GONE"], (missing, gone)
    text = open(path, encoding="utf-8").read()
    assert text.startswith(head), text[:200]
    rows = [l for l in text.splitlines() if l.startswith("SWITCH(")]
    assert rows == ['SWITCH(HOME, ANY, SYSTEM, "gpu/vk/b.cpp", "")',
                    'SWITCH(HOS_KEPT, FLAG, GAME, "host/a.c", "kept as it was")',
                    'SWITCH(HOS_MOVED, FLAG, GAME, "host/a.c", "read elsewhere now")',
                    'SWITCH(HOS_NEW_LEVEL, INT, GAME, "host/a.c", "")',
                    'SWITCH(RX_NEW_DUMP_DIR, PATH, LOCAL, "host/a.c", "")',
                    'SWITCH(RX_VK_FROM_A_TABLE, ANY, GAME, "host/a.c", "")'], rows
    assert switch_list.compare(str(root), path)[:2] == ([], [])


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--cxx", default=shutil.which("clang") or shutil.which("gcc") or "cc")
    a = p.parse_args()
    check_build_reads_it()
    have = check_list()
    with tempfile.TemporaryDirectory(prefix="switch-list-") as tmp:
        check_update(tmp)
        check_allowed(a.cxx, have, tmp)
    print("switch list: %d switches, in step with the reads; the allow-list refuses the %d LOCAL ones" % (
        len(have), sum(1 for v in have.values() if v[1] == "LOCAL")))


if __name__ == "__main__":
    main()
