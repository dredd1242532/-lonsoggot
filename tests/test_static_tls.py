"""The Linux build's static TLS for the Vulkan back end (nx2/build.py vk_tls_flags, nx2/elf_tls.py, host/hos_main.c static_tls_room,
gpu/vk/vk_main.cpp tls_place, gpu/vk/vk_draw.cpp sampler_for's table).

longshot_vk.so is dlopen'ed: its thread_local data is reached through TLS descriptors (-mtls-dialect=gnu2 on x86-64; RX_VK_TLS=gd:
the default model), and the game starts itself again once with room for that data in the static TLS block
(glibc.rtld.optional_static_tls), so the descriptors resolve to constant offsets. Without the room glibc places the data in a block
of its own, silently, and every access takes its slower dynamic path. Checked here:
- nx2/build.py builds with descriptors only on Linux x86-64 without RX_VK_TLS=gd, and checks at its link that the module's PT_TLS
  fits HOS_STATIC_TLS_ROOM (nx2/elf_tls.py, run on ELF files made here);
- static_tls_room, cut from the source and run with execv stubbed: the tunable added to what GLIBC_TUNABLES held, the restart tried
  once (with the executable's own path, so the process keeps its name); HOS_STATIC_TLS off by the switches' rule, from the
  environment or longshot_env.txt (read before the file is loaded), a tunable already named, a failed exec, and the game started
  again (GLIBC_TUNABLES put back as it was, the markers gone) each leave a note for the log;
- the room is made first in main, the note logged after the log opens, and the back end's big per-thread table is a TLS_OBJECT;
- tls_place (the "vk: TLS" line), cut from the source into a module with a 48 KB TLS segment and dlopen'ed: in the static block
  with the room, not without it.
python -I -B tests/test_static_tls.py [--cc gcc] [--cxx g++]
"""
import argparse
import ast
import os
import platform
import re
import struct
import subprocess
import sys
import tempfile
import types
from pathlib import Path

RUN_ALL_ARGS = ["--cc", "gcc", "--cxx", "{cxx}"]
ROOT = Path(__file__).resolve().parents[1]
ENV_H = ROOT / "gpu" / "vk" / "compat" / "longshot_env.h"
sys.path.insert(0, str(ROOT / "nx2"))
import elf_tls  # noqa: E402


def block(text, marker):
    if text.count(marker) != 1:
        raise SystemExit("marker not found once: " + marker)
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


FIXTURE = r"""
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
typedef unsigned DWORD;
#define MAX_PATH 260
static DWORD hos_game_path_a(char *out, DWORD n) {   /* (the game's own path: in the fixture's folder) */
    snprintf(out, n, "%s/longshot", getenv("FIXTURE_DIR"));
    return (DWORD)strlen(out);
}
static int execs = 0;
static char seen[16384];   /* (room for four strings of up to 4 KB each: GCC checks snprintf's bound) */
static int fake_execv(const char *path, char *const argv[]) {
    (void)argv;
    execs++;
    const char *t = getenv("GLIBC_TUNABLES"), *w = getenv("HOS_STATIC_TLS_WAS");
    snprintf(seen, sizeof seen, "%s|%s|%s|%s", path, t ? t : "(unset)", getenv("HOS_STATIC_TLS_DONE") ? "done" : "-",
             w ? w : "(unset)");
    errno = ENOENT;
    return -1;   /* (as a failed exec: the caller goes on) */
}
#define execv fake_execv
/* NOTE */
/* PEEK */
/* DEFINE */
/* FN */
int main(void) {
    char *argv[] = {"longshot", NULL};
    static_tls_room(argv);
    const char *after = getenv("GLIBC_TUNABLES");
    printf("%d\n%s\n%s\n%s\n%s\n", execs, seen[0] ? seen : "-", after ? after : "(unset)",
           getenv("HOS_STATIC_TLS_DONE") || getenv("HOS_STATIC_TLS_WAS") ? "markers" : "clean", static_tls_note);
    return 0;
}
"""

PLACE_SO = r"""
#include <cstddef>
#include <cstdint>
#include <link.h>
/* PLACE */
thread_local char big[48 << 10];   // (as the back end's: far more than glibc's default static TLS surplus)
extern "C" int placement(size_t* size) {
    *static_cast<volatile char*>(&big[0]) = 1;
    const tls_place::Where w = tls_place::where();
    *size = w.size;
    return w.in_static;
}
"""

PLACE_HOST = r"""
#include <dlfcn.h>
#include <stdio.h>
int main(int argc, char **argv) {
    (void)argc;
    void *h = dlopen(argv[1], RTLD_NOW);
    if (!h) { printf("dlopen: %s\n", dlerror()); return 1; }
    int (*f)(size_t *) = (int (*)(size_t *))dlsym(h, "placement");
    size_t size = 0;
    const int in_static = f(&size);
    printf("%s %zu\n", in_static ? "static" : "dynamic", size);
    return 0;
}
"""


def check_build():
    build = (ROOT / "nx2" / "build.py").read_text(encoding="utf-8")
    tree = ast.parse(build)
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "vk_tls_flags"), None)
    if not fn:
        raise SystemExit("FAIL: nx2/build.py: no vk_tls_flags")
    code = ast.get_source_segment(build, fn)

    def flags(linux, machine, env):
        ns = {"LINUX": linux, "os": types.SimpleNamespace(environ=env), "platform": types.SimpleNamespace(machine=lambda: machine),
              "print": lambda *a, **k: None}
        exec(code, ns)
        return ns["vk_tls_flags"]()
    for linux, machine, env, want in [(True, "x86_64", {}, ["-mtls-dialect=gnu2"]), (True, "AMD64", {"RX_VK_TLS": "gnu2"}, ["-mtls-dialect=gnu2"]),
                                      (True, "x86_64", {"RX_VK_TLS": "gd"}, []), (True, "aarch64", {}, []), (False, "x86_64", {}, [])]:
        got = flags(linux, machine, env)
        if got != want:
            raise SystemExit("FAIL: vk_tls_flags on %s (Linux %s, %r): %r, want %r" % (machine, linux, env, got, want))
    if "vk_tls = vk_tls_flags()" not in build or \
            not re.search(r'vflags = target\(\) \+ CPU \+ \(\[[^\n]*\] if LINUX else \[\]\) \+ (vk_lto \+ )?vk_tls \+ \[', build):
        raise SystemExit("FAIL: nx2/build.py: vk_tls_flags is in the back end's flags")
    link = build[build.index("if LINUX and not check_vk_symbols(vdll):"):]
    link = link[:link.index('print("linked %s')]
    if "if LINUX and vk_tls_flags():" not in link or "elf_tls.check(vdll, elf_tls.room(" not in link or "if not fits:" not in link:
        raise SystemExit("FAIL: nx2/build.py: the link does not check the back end's TLS against the static TLS room")


def elf(phdrs, good=True):
    head = bytearray(64)
    head[:4] = b"\x7fELF" if good else b"MZ\0\0"
    head[4], head[5] = 2, 1
    struct.pack_into("<Q", head, 32, 64)
    struct.pack_into("<HH", head, 54, 56, len(phdrs))
    return bytes(head) + b"".join(struct.pack("<IIQQQQQQ", t, 0, 0, 0, 0, 0, memsz, align) for t, memsz, align in phdrs)


def check_elf_tls(temp, room):
    f = Path(temp) / "x.so"
    cases = [
        (elf([(1, 0x10000, 0x1000), (7, 49152, 64), (2, 0, 8)]), (49152, 64)),
        (elf([(1, 0x10000, 0x1000)]), (0, 1)),
        (elf([(7, 100, 0)]), (100, 1)),
        (elf([(7, 100, 64)], good=False), None),
    ]
    for data, want in cases:
        f.write_bytes(data)
        if elf_tls.pt_tls(str(f)) != want:
            raise SystemExit("FAIL: elf_tls.pt_tls: %r, want %r" % (elf_tls.pt_tls(str(f)), want))
    for memsz, align, fits in [(49152, 64, True), (room - elf_tls.MARGIN, 64, True), (room - elf_tls.MARGIN + 1, 64, False),
                               (room - elf_tls.MARGIN - 4095, 65536, False), (131072, 64, False), (0, 1, True)]:
        f.write_bytes(elf([(7, memsz, align)]))
        ok, line = elf_tls.check(str(f), room)
        if ok != fits or str(memsz) not in line:
            raise SystemExit("FAIL: elf_tls.check(%d bytes, align %d) in %d: %s %s" % (memsz, align, room, ok, line))
    f.write_bytes(elf([(7, 100, 8)]))
    if elf_tls.check(str(f), None)[0]:
        raise SystemExit("FAIL: elf_tls.check passes without HOS_STATIC_TLS_ROOM")


def check_wiring(main_c, draw):
    linux_main = main_c[main_c.index("static void static_tls_room(char **argv) {"):]
    linux_main = block(linux_main, "int main(int argc, char **argv) {")
    body = [l.strip() for l in linux_main.splitlines()[1:] if l.strip() and not l.strip().startswith(("int ", "void "))]
    if not body or not body[0].startswith("static_tls_room(argv);"):
        raise SystemExit("FAIL: host/hos_main.c: the static TLS room is the first thing main does")
    log = main_c[main_c.index("removed_switch_notes();\n"):]
    if not re.match(r"removed_switch_notes\(\);\n#ifndef _WIN32\n\s*if \(static_tls_note\[0\]\) hos_log\(\"%s\", static_tls_note\);", log):
        raise SystemExit("FAIL: host/hos_main.c: static_tls_room's note is not logged after the log opens")
    if not re.search(r"^struct SamplerTable \{ SamplerEntry e\[2\]\[4096\]; \};", draw, re.M) or \
            not re.search(r"^TLS_OBJECT\(sampler_table, SamplerTable\)$", draw, re.M):
        raise SystemExit("FAIL: vk_draw.cpp: sampler_for's table is a TLS_OBJECT (on the heap), not 128 KB of the TLS segment")
    sf = block(draw, "VkSampler sampler_for(int id, bool compare) {")
    if "sampler_table().e[" not in sf or "thread_local" in sf:
        raise SystemExit("FAIL: vk_draw.cpp sampler_for: the thread's table is sampler_table()")
    vk_main = (ROOT / "gpu" / "vk" / "vk_main.cpp").read_text(encoding="utf-8")
    pick = block(vk_main, "bool pick_device() {")
    if "tls_place::where()" not in pick or '"vk: TLS: ' not in pick:
        raise SystemExit("FAIL: vk_main.cpp: pick_device does not log where the back end's TLS landed")


def run(exe, env_over, temp, env_file):
    env = {k: v for k, v in os.environ.items()
           if k not in ("GLIBC_TUNABLES", "HOS_STATIC_TLS", "HOS_STATIC_TLS_DONE", "HOS_STATIC_TLS_WAS", "HOS_ENV_FILE")}
    env["FIXTURE_DIR"] = temp
    env.update(env_over)
    beside = Path(temp) / "longshot_env.txt"
    if env_file is None:
        beside.unlink(missing_ok=True)
    else:
        beside.write_bytes(env_file.encode("utf-8"))
    r = subprocess.run([str(exe)], env=env, text=True, capture_output=True, timeout=20)
    if r.returncode:
        raise SystemExit("FAIL: the fixture exited %d: %s" % (r.returncode, r.stderr))
    return r.stdout.split("\n")[:5]


def check_restart(cc, temp, main_c, room):
    fn = block(main_c, "static void static_tls_room(char **argv) {")
    peek = block(main_c, "static const char *env_file_peek(const char *name, char *out, size_t size) {")
    note = next(l for l in main_c.splitlines() if l.startswith("static char static_tls_note["))
    define = next(l for l in main_c.splitlines() if l.startswith("#define HOS_STATIC_TLS_ROOM "))
    src, exe = Path(temp) / "tls.c", Path(temp) / "tls"
    src.write_text(FIXTURE.replace("/* NOTE */", note).replace("/* PEEK */", peek).replace("/* DEFINE */", define)
                   .replace("/* FN */", fn), encoding="utf-8")
    subprocess.run([cc, "-std=gnu11", "-D_GNU_SOURCE", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", "-include", str(ENV_H), str(src),
                    "-o", str(exe)], check=True)
    want = "glibc.rtld.optional_static_tls=%d" % room
    self_path = os.path.realpath(str(exe))
    other = str(Path(temp) / "other_env.txt")
    Path(other).write_text("HOS_STATIC_TLS=false\n", encoding="utf-8")
    failed = "starting again for room failed (%s: " % self_path
    # (environment, longshot_env.txt beside the game or None, then: execs, what the exec was given (path|tunables|done|was),
    # GLIBC_TUNABLES after, the markers left, a part of the note)
    cases = [
        ({}, None, "1", "SELF|%s|done|(unset)" % want, "(unset)", "clean", failed),
        ({"GLIBC_TUNABLES": "glibc.malloc.arena_max=2"}, None, "1",
         "SELF|glibc.malloc.arena_max=2:%s|done|glibc.malloc.arena_max=2" % want, "glibc.malloc.arena_max=2", "clean", failed),
        ({"GLIBC_TUNABLES": ""}, None, "1", "SELF|%s|done|" % want, "", "clean", failed),
        ({"HOS_STATIC_TLS": "0"}, None, "0", "-", "(unset)", "clean", "HOS_STATIC_TLS=0: not started again"),
        ({"HOS_STATIC_TLS": "Off"}, None, "0", "-", "(unset)", "clean", "HOS_STATIC_TLS=Off: not started again"),
        ({"HOS_STATIC_TLS": ""}, None, "0", "-", "(unset)", "clean", "HOS_STATIC_TLS=: not started again"),
        ({"HOS_STATIC_TLS": "1"}, None, "1", "SELF|%s|done|(unset)" % want, "(unset)", "clean", failed),
        ({}, "# a comment\n  HOS_STATIC_TLS=no  \r\n", "0", "-", "(unset)", "clean", "HOS_STATIC_TLS=no: not started again"),
        ({}, "\ufeffHOS_STATIC_TLS=0\nHOS_STATIC_TLS=1\n", "1", "SELF|%s|done|(unset)" % want, "(unset)", "clean", failed),
        ({}, "HOS_STATIC_TLS_X=0\nXHOS_STATIC_TLS=0\n", "1", "SELF|%s|done|(unset)" % want, "(unset)", "clean", failed),
        ({"HOS_STATIC_TLS": "1"}, "HOS_STATIC_TLS=0\n", "1", "SELF|%s|done|(unset)" % want, "(unset)", "clean", failed),
        ({"HOS_ENV_FILE": other}, "HOS_STATIC_TLS=1\n", "0", "-", "(unset)", "clean", "HOS_STATIC_TLS=false: not started again"),
        ({"GLIBC_TUNABLES": "glibc.rtld.optional_static_tls=4096"}, None, "0", "-", "glibc.rtld.optional_static_tls=4096", "clean",
         "sets glibc.rtld.optional_static_tls already"),
        ({"HOS_STATIC_TLS_DONE": "1", "GLIBC_TUNABLES": "glibc.malloc.arena_max=2:" + want, "HOS_STATIC_TLS_WAS": "glibc.malloc.arena_max=2"},
         None, "0", "-", "glibc.malloc.arena_max=2", "clean", "started again with " + want),
        ({"HOS_STATIC_TLS_DONE": "1", "GLIBC_TUNABLES": want}, None, "0", "-", "(unset)", "clean", "started again with " + want),
        ({"HOS_STATIC_TLS_DONE": "0"}, None, "1", "SELF|%s|done|(unset)" % want, "(unset)", "clean", failed),
    ]
    for env, env_file, *expect in cases:
        expect[1] = expect[1].replace("SELF", self_path)
        got = run(exe, env, temp, env_file)
        if got[:4] != expect[:4] or expect[4] not in got[4]:
            raise SystemExit("FAIL: %r, file %r: got %r, want %r" % (env, env_file, got, expect))


def check_placement(cc, cxx, temp, vk_main, room):
    place = block(vk_main, "namespace tls_place {")
    so, host = Path(temp) / "place.so", Path(temp) / "place"
    (Path(temp) / "place.cpp").write_text(PLACE_SO.replace("/* PLACE */", place), encoding="utf-8")
    (Path(temp) / "host.c").write_text(PLACE_HOST, encoding="utf-8")
    subprocess.run([cxx, "-std=c++20", "-O1", "-fPIC", "-shared", "-mtls-dialect=gnu2", "-Wall", "-Werror", str(Path(temp) / "place.cpp"),
                    "-o", str(so)], check=True)
    subprocess.run([cc, "-O1", "-Wall", "-Werror", str(Path(temp) / "host.c"), "-o", str(host), "-ldl"], check=True)
    for tunables, want in [(None, "dynamic"), ("glibc.rtld.optional_static_tls=%d" % room, "static")]:
        env = {k: v for k, v in os.environ.items() if k != "GLIBC_TUNABLES"}
        if tunables:
            env["GLIBC_TUNABLES"] = tunables
        r = subprocess.run([str(host), str(so)], env=env, text=True, capture_output=True, timeout=20)
        got = r.stdout.split()
        if r.returncode or len(got) != 2 or got[0] != want or int(got[1]) < 48 << 10:
            raise SystemExit("FAIL: tls_place with GLIBC_TUNABLES %s: %r %s, want %s and the segment's size" % (tunables, r.stdout, r.stderr, want))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cc", default="gcc")
    p.add_argument("--cxx", default="g++")
    a = p.parse_args()
    main_c = (ROOT / "host" / "hos_main.c").read_text(encoding="utf-8")
    draw = (ROOT / "gpu" / "vk" / "vk_draw.cpp").read_text(encoding="utf-8")
    vk_main = (ROOT / "gpu" / "vk" / "vk_main.cpp").read_text(encoding="utf-8")
    room = elf_tls.room(main_c)
    if not room:
        raise SystemExit("FAIL: host/hos_main.c: no HOS_STATIC_TLS_ROOM")
    check_build()
    check_wiring(main_c, draw)
    with tempfile.TemporaryDirectory(prefix="static-tls-") as temp:
        check_elf_tls(temp, room)
        if sys.platform == "win32":
            print("PASS (build, elf_tls and wiring only: the restart is Linux's)")
            return
        check_restart(a.cc, temp, main_c, room)
        if platform.machine().lower() not in ("x86_64", "amd64"):
            print("PASS (the placement check is x86-64's: TLS descriptors)")
            return
        check_placement(a.cc, a.cxx, temp, vk_main, room)
    print("PASS: the back end's TLS through descriptors in the static block, the game started again once to make room for it")


if __name__ == "__main__":
    main()
