"""Narrow game-free Darwin host lifecycle/process/diagnostic checks (macOS 14.4 target).

Uses the existing arm64_host check's real, game-independent runtime-header generator and jump ABI fixture,
then real host objects for threads, migrating fibers, module/memory queries and interrupted-PC profiling.
Child spawning, same-PID restart, concurrent unflagged socket creation, and IPC use authored local processes.
The menu's three process functions are compiled verbatim; their desktop targets are redirected to an authored
substitute, never open/pbcopy/a browser/Discord. No game, Vulkan device or performance measurement.
Every compiler runs sequentially/native; only x86_64 executables use arch -x86_64.

python -I -B tests/test_darwin_host.py [--arch arm64 --arch x86_64] [--out NEW] [--source CHECKOUT]
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import sys
import tempfile

RUN_ALL_ARGS = ["--cc", "clang", "--cxx", "{cxx}"]
RUN_ALL_SKIP = {"windows": "Darwin native host APIs; Windows implementation unchanged"}
ROOT = Path(__file__).resolve().parents[1]


def load_cpu_check():
    spec = importlib.util.spec_from_file_location("darwin_cpu_check", ROOT / "tests/test_arm64_host.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the existing host CPU fixture driver")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def function(text, signature):
    start = text.index(signature)
    end = text.index("\n}\n", start) + 2
    return text[start:end] + "\n"


def check(args, out):
    source = args.source.resolve()
    cpu = load_cpu_check()
    cpu_out = out / "cpu"
    cpu_out.mkdir()
    if cpu.check(args, cpu_out):
        print("FAIL: native jump/object prerequisite; exact commands retained", flush=True)
        return 1
    generated = cpu_out / "generated"
    env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "SDKROOT", "DEVELOPER_DIR") if k in os.environ}
    env["OOT_WORK"] = str(cpu_out / "empty-work")
    helper_names = ("darwin_wait", "darwin_mem", "darwin_fault", "darwin_process", "darwin_prof", "win32_posix")
    inputs = [source / "host" / "posix" / (name + suffix)
              for name in (*helper_names, "darwin_cpu", "darwin_fiber") for suffix in (".c", ".h")
              if (source / "host" / "posix" / (name + suffix)).is_file()]
    inputs += [source / "host" / name for name in ("hos.h", "hos_setjmp.c", "hos_thread.c", "hos_main.c", "hos_relaunch.c", "hos_relaunch.h", "discord_rpc.c")]
    inputs += [source / "host/posix/windows.h", source / "host/posix/psapi.h", source / "host/posix/darwin_vulkan.h", source / "gpu/vk/vk_menu.cpp"]
    inputs += [ROOT / "tests" / name for name in ("test_darwin_host.py", "test_arm64_host.py", "darwin_host_test.c", "darwin_process_test.c", "darwin_ipc_test.c", "darwin_share_test.cpp", "arm64_host_test.c", "arm64_host_registers.S")]
    inputs.append(ROOT / "tests/darwin_fiber_registers.S")
    inputs.append(ROOT / "tests/darwin_memory_notice_test.c")
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
    (out / "source-sha256.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
    menu = (source / "gpu/vk/vk_menu.cpp").read_text(encoding="utf-8")
    excerpt = "\n".join(function(menu, "bool " + name + "(") for name in ("share_spawn", "share_clipboard", "share_open"))
    startup = (source / "host/hos_main.c").read_text(encoding="utf-8")
    memory_notice = function(startup, "static int memory_notice_text(") + function(startup, "static void memory_notice(")
    failures = 0
    for arch in args.arch:
        target = out / arch
        target.mkdir()
        flags = ["-arch", arch, "-mmacosx-version-min=14.4", "-std=gnu11", "-O" + args.opt,
                 "-fno-omit-frame-pointer", "-ffp-contract=off", "-DA64_WRITE_TRACK", "-ferror-limit=8",
                 "-Werror=implicit-function-declaration", "-Werror=unguarded-availability-new",
                 "-I" + str(generated), "-Ihost/posix", "-Ihost"]
        objects = {}
        for name in helper_names:
            obj = target / (name + ".o")
            warnings = ["-Wall", "-Wextra"] + (["-Werror"] if name in ("darwin_process", "darwin_prof") else [])
            code, _ = cpu.run([args.cc, *flags, *warnings, "-c", "host/posix/" + name + ".c", "-o", str(obj)], source, env, target, name)
            if code:
                failures += 1
            else:
                objects[name] = obj
        for name in ("hos_relaunch", "hos_main"):
            obj = target / (name + ".o")
            code, _ = cpu.run([args.cc, *flags, "-Wall", "-Wextra", "-c", "host/" + name + ".c", "-o", str(obj)], source, env, target, name)
            if code:
                failures += 1
            else:
                objects[name] = obj
        launcher = [] if arch == "arm64" else ["/usr/bin/arch", "-x86_64"]
        marker = target / "library_marker.c"
        marker.write_text("int darwin_host_library_marker(void) { return 314; }\n", encoding="utf-8")
        code, _ = cpu.run([args.cc, *flags, "-Wall", "-Wextra", "-Werror", "-dynamiclib", str(marker),
                           "-o", str(target / "libSDL2-2.0.0.dylib")], source, env, target, "marker-library")
        failures += bool(code)
        if all(name in objects for name in helper_names):
            executable = target / "darwin_host_test"
            exported = ["-Wl,-export_dynamic"]
            for name in ("sample_leaf", "sample_middle", "sample_outer", "sample_frameless", "moving_fiber"):
                exported.append("-Wl,-exported_symbol,_" + name)
            cpu_objects = [cpu_out / arch / (name + ".o") for name in ("hos_setjmp", "darwin_cpu", "darwin_fiber")]
            code, _ = cpu.run([args.cc, *flags, "-Wall", "-Wextra", "-Werror", str(ROOT / "tests/darwin_host_test.c"),
                               str(ROOT / "tests/darwin_fiber_registers.S"),
                               *(str(objects[name]) for name in helper_names), *(str(obj) for obj in cpu_objects),
                               "-Wl,-dead_strip", *exported, "-o", str(executable)], source, env, target, "host-link")
            failures += bool(code)
            if not code:
                code, _ = cpu.run([*launcher, str(executable)], source, env, target, "host-execute", timeout=30)
                failures += bool(code)
                profile_env = dict(env, HOS_PROFILE="1", HOS_THREAD_TIMES="1", HOS_SAMPLE="mkw_vk_fixture",
                                   HOS_SAMPLE_FILE=str(target / "single.samples"), HOS_SAMPLE_ALL=str(target / "all.samples"))
                (target / "all.samples.go").write_text("authored fixture\n", encoding="utf-8")
                code, _ = cpu.run([*launcher, str(executable), "--profile"], source, profile_env, target, "profile-execute", timeout=15)
                failures += bool(code)
                if not code:
                    one = (target / "single.samples").read_text(encoding="utf-8").splitlines()
                    every = (target / "all.samples").read_text(encoding="utf-8").splitlines()
                    good = bool(one) and all("+" in row and ";" in row for row in one)
                    good = good and any(" mkw_vk_fixture|" in row and ";" in row for row in every)
                    print(f"profile_files arch={arch} single_lines={len(one)} all_lines={len(every)} valid={int(good)}", flush=True)
                    failures += not good
        if "darwin_process" in objects and "hos_relaunch" in objects:
            executable = target / "darwin_process_test"
            code, _ = cpu.run([args.cc, *flags, "-Wall", "-Wextra", "-Werror", str(ROOT / "tests/darwin_process_test.c"),
                               str(objects["darwin_process"]), str(objects["hos_relaunch"]), "-Wl,-dead_strip", "-o", str(executable)],
                              source, env, target, "process-link")
            failures += bool(code)
            if not code:
                code, _ = cpu.run([*launcher, str(executable), "--test", str(target)], source, env, target, "process-execute", timeout=30)
                failures += bool(code)
        executable = target / "darwin_ipc_test"
        code, _ = cpu.run([args.cc, *flags, "-Wall", "-Wextra", '-DDARWIN_IPC_SOURCE="' + str(source / "host/discord_rpc.c") + '"',
                           str(ROOT / "tests/darwin_ipc_test.c"), "-Wl,-dead_strip", "-o", str(executable)], source, env, target, "ipc-link")
        failures += bool(code)
        if not code:
            with tempfile.TemporaryDirectory(prefix="dh-ipc-", dir="/private/tmp") as socket_dir:
                code, _ = cpu.run([*launcher, str(executable), socket_dir], source, env, target, "ipc-execute", timeout=15)
                failures += bool(code)
        if "darwin_process" in objects:
            (target / "darwin_share_functions.inc").write_text(excerpt, encoding="utf-8")
            executable = target / "darwin_share_test"
            code, _ = cpu.run([args.cxx, "-arch", arch, "-mmacosx-version-min=14.4", "-std=c++20", "-O" + args.opt,
                               "-Wall", "-Wextra", "-Werror", "-Werror=unguarded-availability-new", "-Ihost/posix", "-I" + str(target),
                               str(ROOT / "tests/darwin_share_test.cpp"), str(objects["darwin_process"]), "-o", str(executable)],
                              source, env, target, "share-link")
            failures += bool(code)
            if not code:
                code, _ = cpu.run([*launcher, str(executable), str(target / "share.receipt")], source, env, target, "share-execute", timeout=15)
                failures += bool(code)
        (target / "darwin_memory_notice.inc").write_text(memory_notice, encoding="utf-8")
        executable = target / "darwin_memory_notice_test"
        code, _ = cpu.run([args.cc, "-arch", arch, "-mmacosx-version-min=14.4", "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror",
                           "-Ihost/posix", "-I" + str(target), str(ROOT / "tests/darwin_memory_notice_test.c"), "-o", str(executable)],
                          source, env, target, "memory-notice-link")
        failures += bool(code)
        if not code:
            code, _ = cpu.run([*launcher, str(executable)], source, env, target, "memory-notice-execute", timeout=10)
            failures += bool(code)
    print(f"darwin_host_checks architectures={len(args.arch)} failed={failures}", flush=True)
    return int(failures != 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cc", default="clang")
    parser.add_argument("--cxx", default="clang++")
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--arch", action="append", choices=("arm64", "x86_64"))
    parser.add_argument("--opt", choices=("0", "1", "2"), default="2")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if sys.platform != "darwin":
        print("SKIP: Darwin host APIs; not run on Windows or Linux")
        return 0
    if args.arch is None:
        args.arch = ["arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"]
    if args.out:
        args.out.mkdir(parents=True, exist_ok=False)
        return check(args, args.out.resolve())
    with tempfile.TemporaryDirectory(prefix="dh-host-") as temp:
        return check(args, Path(temp).resolve())


if __name__ == "__main__":
    sys.exit(main())
