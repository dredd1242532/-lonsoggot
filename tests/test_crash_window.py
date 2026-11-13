"""The game's side of the crash window (host/hos_crash_window.c, docs/crash-report-window.md), compiled on its own with a
stand-in builder, no game:

  - with builder= in longshot_version.txt, a crash leaves crash_pending.txt (the log's name and the time) and starts
    <builder> --crash <the game folder>, with its output kept out of the log;
  - once per process: a second crash starts no second window;
  - nothing at all with crash_reports=0, LONGSHOT_NO_CRASH_WINDOW on (env_on: 0, false, off and no are off) or no builder= line (no marker either: the next
    launch has nothing to offer from a window that was turned off);
  - the window starts with no signal blocked (the crash's is, in the handler) and none of the game's files;
  - the wiring: report_crash opens it after the log is flushed, and the game's start makes the command once the log is
    open.

python -I -B tests/test_crash_window.py [--cc gcc]
"""
import argparse
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RUN_ALL_ARGS = ["--cc", "gcc"]
RUN_ALL_SKIP = {"windows": "the Linux spawn (vfork, execve): the Windows one is CreateProcessW, checked by building"}

ROOT = Path(__file__).resolve().parents[1]

DRIVER = r"""
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include "hos_crash_window.h"
#include <windows.h>
DWORD GetModuleFileNameA(HMODULE m, LPSTR out, DWORD cap) {   /* (hos_paths.h's: only init(), not init_in(), asks) */
    (void)m, (void)out, (void)cap;
    return 0;
}
void hos_log(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    putchar('\n');
}
int main(int argc, char **argv) {
    hos_crash_window_init_in(argv[1]);
    (void)argc;
    /* (as in the game's crash handler: the crash's signal blocked (on_driver_fault has no SA_NODEFER), and a file of the
       game's open without O_CLOEXEC) */
    sigset_t segv;
    sigemptyset(&segv);
    sigaddset(&segv, SIGSEGV);
    sigprocmask(SIG_BLOCK, &segv, NULL);
    char leak[4096];
    snprintf(leak, sizeof leak, "%s/the game's file", argv[1]);
    if (open(leak, O_WRONLY | O_CREAT, 0644) < 0) return 2;
    for (int i = 0; i < atoi(argv[2]); i++) hos_crash_window_open();
    return 0;
}
"""

# (the stand-in builder: what it was started with, and whether its output reached anyone)
BUILDER = """#!/bin/sh
d="$(dirname "$0")"
grep '^SigBlk:' /proc/$$/status > "$d/mask.txt"
ls -l /proc/$$/fd | grep -c "the game's file" > "$d/files.txt"
printf '%s\\n' "$@" > "$d/started.txt"
echo "builder output"
"""


def wait_for(path, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        if path.exists() and path.stat().st_size:
            return True
        time.sleep(0.05)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default=None)
    args = parser.parse_args()
    if os.name == "nt":
        print("SKIP: Linux only")
        return 0
    compiler = args.cc or shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not compiler:
        print("SKIP: no C compiler")
        return 0

    failures = []

    def check(ok, what):
        print(("ok   " if ok else "FAIL ") + what)
        if not ok:
            failures.append(what)

    glue = (ROOT / "host" / "mkw_glue.c").read_text(encoding="utf-8")
    report = glue[glue.index("report_crash("):]
    report = report[:report.index("\n}\n")]
    check(re.search(r"hos_log_flush\(\);\s*\n\s*hos_crash_window_open\(\);", report) is not None,
          "report_crash opens the window right after the log is flushed")
    main_c = (ROOT / "host" / "hos_main.c").read_text(encoding="utf-8")
    check(re.search(r'hos_log\("longshot %s", LONGSHOT_VERSION\);[^\n]*\n\s*hos_crash_window_init\(\);', main_c) is not None,
          "the game's start makes the command after its first log line")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "driver.c").write_text(DRIVER, encoding="utf-8")
        exe = tmp / "driver"
        subprocess.run([compiler, "-std=gnu11", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                        "-I", str(ROOT / "host" / "posix"), "-I", str(ROOT / "host"),
                        str(ROOT / "host" / "hos_crash_window.c"), str(tmp / "driver.c"), "-o", str(exe)], check=True)

        def folder(name, version=True, options=None):
            f = tmp / name
            (f / "tools").mkdir(parents=True)
            builder = f / "tools" / "longshot builder"   # (a space: the argument stays one)
            builder.write_text(BUILDER, encoding="utf-8")
            builder.chmod(builder.stat().st_mode | stat.S_IXUSR)
            if version:
                (f / "longshot_version.txt").write_text("version=3.4.1\nbuilder=%s\n" % builder, encoding="utf-8")
            if options is not None:
                (f / "longshot_options.ini").write_text(options, encoding="utf-8")
            return f

        def run(f, crashes=1, env=None):
            e = dict(os.environ)
            e.pop("LONGSHOT_NO_CRASH_WINDOW", None)
            e.update(env or {})
            return subprocess.run([str(exe), str(f), str(crashes)], env=e, capture_output=True, text=True,
                                  timeout=20, check=True).stdout

        f = folder("game folder")
        before = time.time()
        out = run(f, crashes=2)
        started = f / "tools" / "started.txt"
        check(wait_for(started), "a crash starts the builder")
        check(started.read_text().splitlines() == ["--crash", str(f)], "with --crash and the game folder as one argument")
        marker = (f / "crash_pending.txt").read_text()
        m = re.fullmatch(r"log=longshot_log\.txt\ntime=(\d+)\n", marker)
        check(m is not None and before - 2 <= int(m.group(1)) <= time.time() + 2, "crash_pending.txt: the log and the time")
        check("builder output" not in out, "the builder's output is not the game's")
        mask = (f / "tools" / "mask.txt").read_text().split()
        # (SIGSEGV's bit, 1 << (11 - 1): the stand-in's shell blocks SIGCHLD itself while it runs grep)
        check(len(mask) == 2 and not int(mask[1], 16) & 1 << (signal.SIGSEGV - 1),
              "the window starts with the crash's signal unblocked (%s)" % " ".join(mask))
        check((f / "tools" / "files.txt").read_text().strip() == "0", "the game's files aren't passed to the window")
        check("crash window: on a crash" in out, "the game's log says where the window comes from")
        time.sleep(0.5)
        check(len(list(f.glob("tools/started*"))) == 1, "once per process")

        for name, kw, env, why in (
                ("off in settings", {"options": "vsync=1\ncrash_reports=0\n"}, None, "crash_reports=0"),
                ("off for tests", {}, {"LONGSHOT_NO_CRASH_WINDOW": "1"}, "LONGSHOT_NO_CRASH_WINDOW"),
                ("no builder", {"version": False}, None, "no builder=")):
            f = folder(name, **kw)
            out = run(f, env=env)
            time.sleep(0.5)
            check(not (f / "tools" / "started.txt").exists() and not (f / "crash_pending.txt").exists(),
                  "%s: no window, no marker" % why)
        f = folder("on in settings", options="crash_reports=1\n")
        run(f)
        check(wait_for(f / "tools" / "started.txt"), "crash_reports=1: the window starts")
        for value in ("0", "false", "OFF", "no"):   # (the switches' one rule, longshot_env.h env_on: these are off)
            f = folder("switch " + value)
            run(f, env={"LONGSHOT_NO_CRASH_WINDOW": value})
            check(wait_for(f / "tools" / "started.txt"), "LONGSHOT_NO_CRASH_WINDOW=%s: the window still starts" % value)

    print("%d failed" % len(failures) if failures else "all passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
