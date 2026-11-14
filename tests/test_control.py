"""The control bridge (host/control.c) against a fake game, over its real transport: tests/control_test.c is built with
host/control.c (HOS_CONTROL_NO_GAME: no game behind it) and drives it as a client. Valid commands, malformed JSON, unknown
commands, oversized lines, clients that disconnect mid-command or never read, too many clients, the socket's mode, a file
in the way (Linux) or the pipe's name and DACL (Windows), quit, and guest memory at each HOS_CONTROL_MEM level. Linux
drives the Unix socket, Windows the named pipe. macOS runs those same cases and control_darwin_test.c: path bounds,
kernel peer credentials, descriptor flags, exec inheritance and EPIPE without SIGPIPE, with real Unix sockets.

python -I -B tests/test_control.py [--cc gcc] [--source-root OTHER_CHECKOUT]
On macOS, --arch arm64 or --arch x86_64 chooses the fixture's target and runs it through arch (Rosetta for x86_64).
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cc", "gcc"]

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cc", default="gcc")
    p.add_argument("--source-root", type=Path, default=ROOT, help="compile the bridge and its header from this checkout")
    if sys.platform == "darwin":
        p.add_argument("--arch", choices=("arm64", "x86_64"))
    a = p.parse_args()
    windows = os.name == "nt"
    # (Linux: a short folder, a Unix socket's path is at most 107 bytes: the temporary folder ($TMPDIR) when it is short
    # enough, else /tmp. Windows: the pipes are named after the process)
    short = windows or len(tempfile.gettempdir()) <= 48 or not os.path.isdir("/tmp")
    with tempfile.TemporaryDirectory(prefix="ctl-", dir=None if short else "/tmp") as tmp:
        exe = os.path.join(tmp, "control_test.exe" if windows else "control_test")
        libs = ["-ladvapi32"] if windows else ["-lpthread", "-lm"]
        if sys.platform == "darwin":
            flags = ["-std=c11", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-Wno-missing-field-initializers",
                     "-Wno-format-truncation", "-D_GNU_SOURCE", "-DHOS_CONTROL_NO_GAME", "-mmacosx-version-min=14.4",
                     "-I", str(a.source_root / "host")]
            if a.arch:
                flags += ["-arch", a.arch]
            run_prefix = ["/usr/bin/arch", "-" + a.arch] if a.arch else []
            darwin_exe = os.path.join(tmp, "control_darwin_test")
            for binary, sources in (
                (exe, [ROOT / "tests/control_test.c", a.source_root / "host/control.c",
                       a.source_root / "host/posix/darwin_control_sockets.c"]),
                (darwin_exe, [ROOT / "tests/control_darwin_test.c"]),
            ):
                command = [a.cc] + flags + [str(source) for source in sources] + ["-o", binary] + libs
                print("compile argv:", command, flush=True)
                subprocess.run(command, check=True)
            subprocess.run(run_prefix + [darwin_exe, tempfile.mkdtemp(dir=tmp)], check=True, timeout=30)
        else:
            warnings = ["-Wno-format-truncation"] if "clang" not in Path(a.cc).name else []
            subprocess.run([a.cc, "-std=c11", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-Wno-missing-field-initializers",
                            "-D_GNU_SOURCE", "-D_CRT_SECURE_NO_WARNINGS", "-DHOS_CONTROL_NO_GAME", "-I", str(a.source_root / "host"),
                            str(ROOT / "tests" / "control_test.c"), str(a.source_root / "host" / "control.c"), "-o", exe]
                           + warnings + libs, check=True)
        # (the general cases, quit, the level unit, and the memory cases at each HOS_CONTROL_MEM level)
        runs = [([], None), (["quit"], None), (["level"], None),
                (["mem"], None), (["mem"], "ro"), (["mem"], "rw"), (["mem"], "bogus")]
        for case, mem in runs:
            folder = tempfile.mkdtemp(dir=tmp)
            env = dict(os.environ)
            env.pop("HOS_CONTROL_MEM", None)
            if mem is not None:
                env["HOS_CONTROL_MEM"] = mem
            if sys.platform == "darwin":
                r = subprocess.run(run_prefix + [exe, folder] + case, capture_output=True, text=True, timeout=180, env=env)
            else:
                r = subprocess.run([exe, folder] + case, capture_output=True, text=True, timeout=180, env=env)
            print(r.stdout, end="")
            print(r.stderr, end="", file=sys.stderr)
            if r.returncode:
                label = " ".join(case) or "(the cases)"
                print("FAIL: control_test %s%s exited %d" % (label, " HOS_CONTROL_MEM=%s" % mem if mem else "", r.returncode),
                      file=sys.stderr)
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
