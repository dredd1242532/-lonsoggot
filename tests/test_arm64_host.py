"""Compile and exercise Darwin's host jumps and pause hints, without a game or a graphics device.

The real nx2/rtgen.py and a64-kit's generators supply the runtime headers from checked-in source. A native emitter
library provides runtime text only: no instructions are translated and no game is read. Object compilation (not
syntax-only) catches accidental x86 assembly and headers. The C fixture links hos_setjmp.c and Darwin's arm64 assembly;
its .S companion controls every callee-saved register, SP, FP and floating-point state.
Windows and Linux execution is deliberately unchanged: this new check is Darwin-only.

    python -I -B tests/test_arm64_host.py [--cc clang] [--cxx clang++] [--arch arm64 --arch x86_64]
        [--source <checkout>] [--out <new directory>] [--opt 0|1|2]

By default only the native architecture runs. An explicit x86_64 run on Apple Silicon needs Rosetta, which this
check never installs. Every compiler stays native; only the x86_64 executable is launched with arch -x86_64.
--source lets this test fail against an unchanged main checkout without writing there. --out retains all commands,
outputs, generated headers, objects and executables; otherwise a temporary directory is removed after the check.
"""
import argparse
import os
from pathlib import Path
import platform
import shlex
import subprocess
import sys
import tempfile

RUN_ALL_ARGS = ["--cc", "clang", "--cxx", "{cxx}"]
RUN_ALL_SKIP = {"windows": "Darwin host CPU ABI; no Windows implementation changed"}

ROOT = Path(__file__).resolve().parents[1]


def run(cmd, source, env, out, name, timeout=60):
    line = "$ " + shlex.join(str(part) for part in cmd)
    print(line, flush=True)
    try:
        p = subprocess.run(cmd, cwd=source, env=env, text=True, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout, check=False)
        text, code = p.stdout, p.returncode
    except subprocess.TimeoutExpired as exc:
        text = exc.stdout or b""
        if isinstance(text, bytes):
            text = text.decode("utf-8", "replace")
        text += "\nTIMEOUT\n"
        code = 124
    (out / (name + ".log")).write_text(line + "\n" + text + f"\nEXIT_CODE={code}\n", encoding="utf-8")
    print(text, end="" if text.endswith("\n") else "\n", flush=True)
    print(f"{name} exit={code}", flush=True)
    return code, text


def check(args, out):
    source = args.source.resolve()
    env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "SDKROOT", "DEVELOPER_DIR") if k in os.environ}
    # kit.py consults OOT_WORK/dump.txt at import time. A new empty directory rules out reading a user's dump path.
    empty = out / "empty-work"
    empty.mkdir()
    env["OOT_WORK"] = str(empty)
    generated = out / "generated"
    generated.mkdir()
    emitter = generated / "liba64emit.dylib"
    native = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
    code, _ = run([args.cxx, "-arch", native, "-mmacosx-version-min=14.4", "-std=c++20", "-O0", "-shared",
                   "-fPIC", "-Wno-everything", "third_party/a64-kit/lift/a64emit.cpp", "-o", str(emitter)],
                  source, env, out, "build-runtime-emitter")
    if code:
        return 1
    generator = ("import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
                 "import rtgen, build_emit; build_emit.build = lambda force=False: sys.argv[3]; "
                 "import a64_lift, a64_rtgen, suyu_rt_fixes; suyu_rt_fixes.install(); "
                 "a64_rtgen.write(sys.argv[2]); rtgen.write(sys.argv[2]); "
                 "Path(sys.argv[2], 'a64_fpx.h').write_text(a64_lift.fpx_header(set()), encoding='utf-8')")
    code, _ = run([sys.executable, "-I", "-B", "-c", generator, str(source / "nx2"), str(generated), str(emitter)],
                  source, env, out, "generate-runtime")
    if code:
        return 1
    yield_source = out / "windows_yield.c"
    yield_source.write_text('#include <windows.h>\nvoid test_yield(void) { YieldProcessor(); }\n', encoding="utf-8")
    failed = 0
    for arch in args.arch:
        target = out / arch
        target.mkdir()
        flags = ["-arch", arch, "-mmacosx-version-min=14.4", "-std=gnu11", "-O" + args.opt,
                 "-fno-omit-frame-pointer", "-ffp-contract=off", "-DA64_WRITE_TRACK", "-ferror-limit=4",
                 "-I" + str(generated), "-Ihost/posix", "-Ihost"]
        objects = {}
        for name, path in (("hos_setjmp", "host/hos_setjmp.c"), ("hos_thread", "host/hos_thread.c"),
                           ("windows_yield", str(yield_source))):
            obj = target / (name + ".o")
            code, _ = run([args.cc, *flags, "-c", path, "-o", str(obj)], source, env, target, name)
            if code == 0:
                objects[name] = obj
        print(f"host_objects arch={arch} passed={len(objects)}/3", flush=True)
        if len(objects) != 3:
            failed += 1
            continue
        jump_objects = [str(objects["hos_setjmp"])]
        darwin = source / "host/posix/darwin_cpu.c"
        if darwin.is_file():
            obj = target / "darwin_cpu.o"
            code, _ = run([args.cc, *flags, "-Wall", "-Wextra", "-Werror", "-c", "host/posix/darwin_cpu.c",
                           "-o", str(obj)], source, env, target, "darwin_cpu")
            if code:
                failed += 1
                continue
            jump_objects.append(str(obj))
        executable = target / "host_cpu_test"
        code, _ = run([args.cc, *flags, "-Wall", "-Wextra", "-Werror", str(ROOT / "tests/arm64_host_test.c"),
                       str(ROOT / "tests/arm64_host_registers.S"), *jump_objects, "-o", str(executable)],
                      source, env, target, "link")
        if code:
            failed += 1
            continue
        command = [str(executable)] if arch == "arm64" else ["/usr/bin/arch", "-x86_64", str(executable)]
        code, text = run(command, source, env, target, "execute", timeout=15)
        if code or "host_cpu failures=0\n" not in text:
            failed += 1
        if arch == "arm64" and darwin.is_file():
            # arm64e is not an alias for this raw arm64 context. Require a clear refusal, not silent PAC bypass.
            code, text = run([args.cc, "-arch", "arm64e", "-mmacosx-version-min=14.4", "-c",
                              "host/posix/darwin_cpu.c", "-o", str(target / "unsupported-arm64e.o")],
                             source, env, target, "arm64e-refusal")
            refused = code != 0 and "not arm64e authenticated contexts" in text
            print(f"arm64e rejected={int(refused)}", flush=True)
            if not refused:
                failed += 1
    print(f"darwin_cpu architectures={len(args.arch)} failed={failed}", flush=True)
    return int(failed != 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cc", default="clang")
    ap.add_argument("--cxx", default="clang++")
    ap.add_argument("--source", type=Path, default=ROOT)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--arch", action="append", choices=("arm64", "x86_64"))
    ap.add_argument("--opt", choices=("0", "1", "2"), default="2")
    args = ap.parse_args()
    if sys.platform != "darwin":
        print("SKIP: Darwin host CPU ABI; not run on Windows or Linux")
        return 0
    if args.arch is None:
        machine = platform.machine().lower()
        args.arch = ["arm64" if machine in ("arm64", "aarch64") else "x86_64"]
    if args.out:
        args.out.mkdir(parents=True, exist_ok=False)
        return check(args, args.out.resolve())
    with tempfile.TemporaryDirectory(prefix="host-cpu-") as tmp:
        return check(args, Path(tmp).resolve())


if __name__ == "__main__":
    sys.exit(main())
