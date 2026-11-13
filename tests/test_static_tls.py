"""The Linux build's static TLS for the Vulkan back end (nx2/build.py vk_tls, host/hos_main.c static_tls_room, gpu/vk/vk_draw.cpp
sampler_for's table).

hookshot_vk.so is dlopen'ed: its thread_local data is reached through TLS descriptors (-mtls-dialect=gnu2, RX_VK_TLS=gd: the default
model), and the game starts itself again once with room for that data in the static TLS block (glibc.rtld.optional_static_tls), so
the descriptors resolve to constant offsets. static_tls_room is cut from the source and run with execv stubbed: the tunable is added
to what GLIBC_TUNABLES held, the restart is tried once (with the executable's own path, so the process keeps its name), and HOS_STATIC_TLS=0, a tunable already named or a restart already made leave
everything as it was; a failed exec puts GLIBC_TUNABLES back. The wiring is checked: the room is made first in main, and the back
end's big per-thread table is on the heap, not in the TLS segment.
python -I -B tests/test_static_tls.py [--cc gcc]
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cc", "gcc"]
ROOT = Path(__file__).resolve().parents[1]


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
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
static int execs = 0;
static char seen[16384];   /* (room for three strings of up to 4 KB each: GCC checks snprintf's bound) */
static int fake_execv(const char *path, char *const argv[]) {
    (void)argv;
    execs++;
    const char *t = getenv("GLIBC_TUNABLES");
    snprintf(seen, sizeof seen, "%s|%s|%s", path, t ? t : "(unset)", getenv("HOS_STATIC_TLS_DONE") ? "done" : "-");
    return -1;   /* (as a failed exec: the caller goes on) */
}
#define execv fake_execv
/* DEFINE */
/* FN */
int main(void) {
    char *argv[] = {"hookshot", NULL};
    static_tls_room(argv);
    const char *after = getenv("GLIBC_TUNABLES");
    printf("%d %s %s\n", execs, seen[0] ? seen : "-", after ? after : "(unset)");
    return 0;
}
"""


def run(exe, env_over):
    env = {k: v for k, v in os.environ.items() if k not in ("GLIBC_TUNABLES", "HOS_STATIC_TLS", "HOS_STATIC_TLS_DONE")}
    env.update(env_over)
    r = subprocess.run([str(exe)], env=env, text=True, capture_output=True, timeout=20)
    if r.returncode:
        raise SystemExit("FAIL: the fixture exited %d: %s" % (r.returncode, r.stderr))
    return r.stdout.strip()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cc", default="gcc")
    a = p.parse_args()
    build = (ROOT / "nx2" / "build.py").read_text(encoding="utf-8")
    if 'vk_tls = ["-mtls-dialect=gnu2"] if LINUX and os.environ.get("RX_VK_TLS", "gnu2") != "gd" else []' not in build:
        raise SystemExit("FAIL: nx2/build.py: the Linux back end is built with TLS descriptors unless RX_VK_TLS=gd")
    if not re.search(r'vflags = target\(\) \+ CPU \+ \(\[[^\n]*\] if LINUX else \[\]\) \+ (vk_lto \+ )?vk_tls \+ \[', build):
        raise SystemExit("FAIL: nx2/build.py: vk_tls is in the back end's flags")
    main_c = (ROOT / "host" / "hos_main.c").read_text(encoding="utf-8")
    fn = block(main_c, "static void static_tls_room(char **argv) {")
    define = next(l for l in main_c.splitlines() if l.startswith("#define HOS_STATIC_TLS_ROOM "))
    linux_main = main_c[main_c.index("static void static_tls_room(char **argv) {"):]
    linux_main = block(linux_main, "int main(int argc, char **argv) {")
    body = [l.strip() for l in linux_main.splitlines()[1:] if l.strip() and not l.strip().startswith(("int ", "void "))]
    if not body or not body[0].startswith("static_tls_room(argv);"):
        raise SystemExit("FAIL: host/hos_main.c: the static TLS room is the first thing main does")
    draw = (ROOT / "gpu" / "vk" / "vk_draw.cpp").read_text(encoding="utf-8")
    sf = block(draw, "VkSampler sampler_for(int id, bool compare) {")
    if "static thread_local std::unique_ptr<Table> table;" not in sf or "thread_local Entry table[" in sf:
        raise SystemExit("FAIL: vk_draw.cpp sampler_for: the thread's table is on the heap, not 128 KB of the TLS segment")
    if sys.platform == "win32":
        print("PASS (wiring only: the restart is Linux's)")
        return
    room = int(define.split()[2])
    want = "glibc.rtld.optional_static_tls=%d" % room
    with tempfile.TemporaryDirectory(prefix="static-tls-") as temp:
        src, exe = Path(temp) / "tls.c", Path(temp) / "tls"
        src.write_text(FIXTURE.replace("/* DEFINE */", define).replace("/* FN */", fn), encoding="utf-8")
        subprocess.run([a.cc, "-std=gnu11", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", str(src), "-o", str(exe)],
                       check=True)
        cases = [
            ({}, "1 /proc/self/exe|%s|done (unset)" % want),
            ({"GLIBC_TUNABLES": "glibc.malloc.arena_max=2"},
             "1 /proc/self/exe|glibc.malloc.arena_max=2:%s|done glibc.malloc.arena_max=2" % want),
            ({"GLIBC_TUNABLES": ""}, "1 /proc/self/exe|%s|done " % want),
            ({"HOS_STATIC_TLS": "0"}, "0 - (unset)"),
            ({"HOS_STATIC_TLS_DONE": "1"}, "0 - (unset)"),
            ({"GLIBC_TUNABLES": "glibc.rtld.optional_static_tls=4096"}, "0 - glibc.rtld.optional_static_tls=4096"),
            ({"HOS_STATIC_TLS": "1"}, "1 /proc/self/exe|%s|done (unset)" % want),
        ]
        self_path = os.path.realpath(str(exe))
        for env, expect in cases:
            expect = expect.replace("/proc/self/exe", self_path)
            got = run(exe, env)
            if got != expect.rstrip():
                raise SystemExit("FAIL: %r: got %r, want %r" % (env, got, expect))
    print("PASS: the back end's TLS through descriptors in the static block, the game started again once to make room for it")


if __name__ == "__main__":
    main()
