"""Shared profile consumers keep Windows maps, ELF symbols and native Mach-O offsets/classification correct.

The Darwin regression compiles authored arm64/x86_64 executables and renderer dylibs for macOS 14.4, but
never executes them. Public nm/otool metadata independently supplies expected image-relative offsets;
prof.py, crit.py, hotlist.py and chain.py consume synthetic samples at those offsets. Complete C++ names,
overloads, busy renderer/libc work and a real named wait on the sampled stack stay distinguishable.
The existing Windows-map and ELF regression remain; ELF execution is Linux-only.
python -I -B tests/test_prof_symbols.py [--cc cc] [--cxx clang++] [--arch arm64 --arch x86_64] [--out NEW]
"""
import argparse
import contextlib
import hashlib
import io
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
_log_dir = None
_command_number = 0

PROBE = r'''
#define _GNU_SOURCE
#include <link.h>
#include <stdio.h>
#include <stdint.h>
__attribute__((noinline)) int f_80001000(int x) { return x * 3 + 1; }
__attribute__((noinline)) int prof_symbol_probe(int x) { return f_80001000(x) + 2; }
__attribute__((noinline)) int WaitForSingleObject(int x) { volatile int value = x; return value; }
static int first(struct dl_phdr_info *i, size_t n, void *out) { (void)n; *(uintptr_t *)out = i->dlpi_addr; return 1; }
int main(void) {
    uintptr_t base = 0;
    dl_iterate_phdr(first, &base);
    printf("%lx %lx %lx\n", (unsigned long)((uintptr_t)&f_80001000 - base), (unsigned long)((uintptr_t)&prof_symbol_probe - base),
           (unsigned long)((uintptr_t)&WaitForSingleObject - base));
    return prof_symbol_probe(1) == 6 ? 0 : 1;
}
'''

MACHO_PROBE = r'''
extern "C" __attribute__((noinline, used)) int f_80001000(int x) { return x * 3 + 1; }
namespace mkwvk {
int profile_data = 42;
__attribute__((noinline, used)) int frame_work(int x) { return x * 7 + 3; }
__attribute__((noinline, used)) int frame_work(float x) { return (int)x + 11; }
__attribute__((noinline, used)) int recording_wait(int x) { volatile int value = x; return value; }
namespace { __attribute__((noinline, used)) int hidden_work(double x) { return (int)x + 19; } }
}
int main() { return f_80001000(2) + mkwvk::frame_work(3) + mkwvk::frame_work(4.0f) + mkwvk::hidden_work(5.0); }
'''


def command(args, *, env=None, input=None):
    global _command_number
    args = [str(arg) for arg in args]
    line = "$ " + shlex.join(args)
    print(line, flush=True)
    r = subprocess.run(args, input=input, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=60)
    print("exit=%d" % r.returncode, flush=True)
    if _log_dir is not None:
        _command_number += 1
        (_log_dir / ("%03d.log" % _command_number)).write_text(
            line + ("\nSTDIN\n" + input if input is not None else "") + "\nSTDOUT\n" + r.stdout +
            "\nSTDERR\n" + r.stderr + "\nEXIT_CODE=%d\n" % r.returncode, encoding="utf-8")
    if r.returncode:
        raise SystemExit("FAIL command: %s\n%s%s" % (line, r.stdout, r.stderr))
    return r


def run(script, args, maps):
    env = dict(os.environ, RX_MAPS=str(maps))
    r = command([sys.executable, "-I", str(SCRIPTS / script)] + args, env=env)
    if r.returncode:
        raise SystemExit("FAIL: %s exited %d:\n%s%s" % (script, r.returncode, r.stdout, r.stderr))
    return r.stdout


def check(cond, what):
    if not cond:
        raise SystemExit("FAIL: " + what)


def windows_map(tmp):
    """the linker-map path, unchanged: a frame in longshot.exe named from longshot.map"""
    maps = tmp / "win"
    maps.mkdir()
    (maps / "longshot.map").write_text(
        " Address         Publics by Value              Rva+Base               Lib:Object\n\n"
        " 0001:00000000       f_80002000                 0000000140001000 f   code.obj\n"
        " 0001:00000100       f_80002400                 0000000140001100 f   code.obj\n")
    samples = tmp / "win_all.txt"
    samples.write_text("".join("%d sead::MainThread|longshot.exe+1010;longshot.exe+1110\n" % (2 * i) for i in range(1, 6)))
    out = run("crit.py", [str(samples)], maps)
    check("100.0%  sead::MainThread" in out, "crit.py: the Windows main thread runs in every sample\n" + out)
    hot = tmp / "win_hot.txt"
    run("hotlist.py", [str(samples), str(hot)], maps)
    check(hot.read_text().split()[0] == "f_80002000", "hotlist.py: the innermost lifted function from the map, first")


def linux_elf(tmp, cc):
    if not sys.platform.startswith("linux") or not shutil.which(cc) or not shutil.which("nm"):
        print("skip: the ELF execution part needs Linux, %s and nm" % cc)
        return
    maps = tmp / "linux"
    maps.mkdir()
    src = tmp / "probe.c"
    src.write_text(PROBE)
    exe = maps / "longshot"
    command([cc, "-O1", "-fPIE", "-pie", "-o", str(exe), str(src)])
    r = command([str(exe)])
    check(r.returncode == 0, "the probe runs")
    lifted, probe, wait = (int(x, 16) for x in r.stdout.split())
    # Separate actual wait-name attribution from libc work beneath a non-wait runtime frame.
    lines = []
    for i in range(1, 9):
        lines.append("%d sead::MainThread|longshot+%x;longshot+%x;libc.so.6+2a1ca\n" % (2 * i, lifted + 2, probe + 4))
        lines.append("%d Presentation_Thread|libc.so.6+98d61;longshot+%x\n" % (2 * i, wait))
        lines.append("%d mkw_vk_copy|libc.so.6+98d61;longshot+%x\n" % (2 * i, probe))
    samples = tmp / "linux_all.txt"
    samples.write_text("".join(lines))
    out = run("crit.py", [str(samples)], maps)
    check("100.0%  sead::MainThread" in out, "crit.py: a Linux thread in longshot is running\n" + out)
    check("0.0%  Presentation_Thread" in out, "crit.py: a Linux thread in libc is blocked\n" + out)
    check("100.0%  mkw_vk_copy" in out, "crit.py: libc work under a non-wait runtime frame is running\n" + out)
    hot = tmp / "linux_hot.txt"
    run("hotlist.py", [str(samples), str(hot)], maps)
    check(hot.read_text().split()[0] == "f_80001000", "hotlist.py: the lifted function named from the ELF symbols, first")
    sys.path.insert(0, str(SCRIPTS))
    import prof
    addrs, names = prof.load_symbols(str(maps))["longshot"]
    check("prof_symbol_probe" in names and addrs[names.index("prof_symbol_probe")] == probe,
          "prof.load_symbols: the symbol's offset is the one a sample gives")


def macho_reference(path):
    """Independent public-tool reference, not the production Mach-O parser or its helpers."""
    layout = command(["otool", "-l", path]).stdout
    bases = []
    for block in re.split(r"Load command \d+\n", layout):
        segment = block.split("\nSection", 1)[0]
        if not re.search(r"cmd\s+LC_SEGMENT_64\b", segment):
            continue
        address = re.search(r"vmaddr\s+(0x[0-9a-fA-F]+)", segment)
        offset = re.search(r"fileoff\s+(\d+)", segment)
        size = re.search(r"filesize\s+(\d+)", segment)
        if address and offset and size and int(offset[1]) == 0 and int(size[1]):
            bases.append(int(address[1], 16))
    check(len(bases) == 1, "otool identifies one file-backed preferred header address")
    minimum = re.search(r"cmd LC_BUILD_VERSION\s+cmdsize \d+\s+platform \S+\s+minos (\S+)", layout)
    check(minimum is not None and minimum[1] == "14.4", "fixture deployment target is 14.4")
    symbols = []
    for line in command(["nm", "-n", "-U", path]).stdout.splitlines():
        match = re.match(r"^([0-9a-fA-F]+)\s+[tTwW]\s+(.+)$", line)
        if match:
            raw = match[2]
            symbols.append((int(match[1], 16), raw[1:] if raw.startswith("_") else raw))
    check(bool(symbols), "nm lists authored function symbols")
    names = command(["c++filt", "-n"], input="\n".join(name for _, name in symbols) + "\n").stdout.splitlines()
    check(len(names) == len(symbols), "reference demangler returns every symbol")
    return bases[0], {name: value - bases[0] for (value, _), name in zip(symbols, names)}


def darwin_macho(tmp, cxx, arches):
    if sys.platform != "darwin":
        print("skip: Mach-O compiler/metadata regression needs Darwin; no fixture execution is required")
        return
    from unittest.mock import patch
    sys.path.insert(0, str(SCRIPTS))
    import prof
    import chain
    for tool in (cxx, "nm", "otool", "c++filt"):
        check(shutil.which(tool) is not None, "Darwin symbol tool is available: " + tool)
    wanted = ("f_80001000", "mkwvk::frame_work(int)", "mkwvk::frame_work(float)",
              "mkwvk::recording_wait(int)", "mkwvk::(anonymous namespace)::hidden_work(double)")
    for arch in arches:
        maps = tmp / arch
        maps.mkdir()
        source = maps / "probe.cpp"
        source.write_text(MACHO_PROBE, encoding="utf-8")
        reference = {}
        bases = {}
        for module in ("longshot", "longshot_vk.dylib"):
            binary = maps / module
            flags = ["-dynamiclib"] if module.endswith(".dylib") else []
            command([cxx, "-arch", arch, "-mmacosx-version-min=14.4", "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror",
                     *flags, source, "-o", binary])
            bases[module], reference[module] = macho_reference(binary)
        check(bases["longshot"] == 0x100000000 and bases["longshot_vk.dylib"] == 0,
              "fixtures cover nonzero executable and zero dylib preferred bases")
        # Apple linker maps are not MSVC maps. An empty Windows alias would mask longshot in hotlist.
        (maps / "longshot.map").write_text("# Symbols:\n# Address Size File Name\n0x100001000 0x10 [1] _main\n", encoding="utf-8")
        with patch.object(prof, "load_elf", side_effect=AssertionError("Mach-O must never use the ELF/GNU-nm path")):
            loaded = prof.load_symbols(str(maps))
        check("longshot.exe" not in loaded and set(loaded) == {"longshot", "longshot_vk.dylib"}, "native module dispatch")
        for module in reference:
            addresses, names = loaded[module]
            check(addresses == sorted(addresses), "Mach-O offsets are sorted")
            for name in wanted:
                check(name in names and name in reference[module], "complete native symbol name: " + name)
                check(addresses[names.index(name)] == reference[module][name], "image-relative symbol offset: " + name)
            check(not any("profile_data" in name for name in names), "Mach-O data symbols are not function frames")
        game = reference["longshot"]["f_80001000"]
        work = reference["longshot_vk.dylib"]["mkwvk::frame_work(int)"]
        other = reference["longshot_vk.dylib"]["mkwvk::frame_work(float)"]
        wait = reference["longshot_vk.dylib"]["mkwvk::recording_wait(int)"]
        hidden = reference["longshot_vk.dylib"][wanted[-1]]
        samples = maps / "all.samples"
        lines = ["# t0_ns 1000000\n"]
        for tick in range(1, 9):
            lines += ["%d sead::MainThread|longshot+%x\n" % (2 * tick, game),
                      "%d mkw_vk_busy|longshot_vk.dylib+%x\n" % (2 * tick, work),
                      "%d mkw_vk_copy|libsystem_platform.dylib+123;longshot_vk.dylib+%x\n" % (2 * tick, work),
                      "%d mkw_vk_wait|libsystem_kernel.dylib+123;longshot_vk.dylib+%x\n" % (2 * tick, wait)]
        samples.write_text("".join(lines), encoding="utf-8")
        out = run("crit.py", [str(samples)], maps)
        for thread in ("sead::MainThread", "mkw_vk_busy", "mkw_vk_copy"):
            check("100.0%  " + thread in out, "crit.py running attribution for " + thread + "\n" + out)
        check(re.search(r"\s0\.0%  mkw_vk_wait", out), "crit.py recognizes a demangled native wait\n" + out)
        stacks = maps / "single.samples"
        stacks.write_text("longshot_vk.dylib+%x\nlongshot_vk.dylib+%x\nlongshot_vk.dylib+%x\n" % (work, other, hidden), encoding="utf-8")
        out = run("prof.py", [str(stacks)], maps)
        for name in (wanted[1], wanted[2], wanted[-1]):
            check("longshot_vk:" + name in out, "prof.py retains C++ overload/anonymous names: " + name)
        hot = maps / "hot.txt"
        run("hotlist.py", [str(samples), str(hot)], maps)
        check(hot.read_text().split()[0] == "f_80001000", "hotlist.py selects the actual native executable")
        output = io.StringIO()
        labels = {1: "mkw_vk_busy", 2: "mkw_vk_copy", 3: "mkw_vk_wait"}
        with patch.dict(os.environ, {"RX_MAPS": str(maps)}), contextlib.redirect_stdout(output):
            chain.samples_on_path(str(samples), labels, [(key, 1000000, 21000000) for key in labels], 20)
        chain_output = output.getvalue()
        (maps / "chain-output.txt").write_text(chain_output, encoding="utf-8")
        for thread in ("mkw_vk_busy", "mkw_vk_copy"):
            check(re.search(thread + r"\s+100\.0% working,\s+0\.0% waiting", chain_output), "chain.py running attribution: " + thread)
        check(re.search(r"mkw_vk_wait\s+0\.0% working,\s+100\.0% waiting", chain_output), "chain.py native wait attribution")
        (maps / "reference.json").write_text(json.dumps({"arch": arch, "preferred_bases": bases, "symbol_offsets": reference,
            "binary_sha256": {module: hashlib.sha256((maps / module).read_bytes()).hexdigest() for module in reference}}, indent=2) + "\n", encoding="utf-8")
        print("Mach-O arch=%s target=14.4 offsets=preferred-base-relative names=complete running=busy+copy waiting=native-wait consumers=prof+crit+chain+hotlist executed_fixtures=0" % arch)


def main():
    from unittest.mock import patch
    from types import SimpleNamespace
    sys.path.insert(0, str(SCRIPTS))
    import prof
    symbols = [
        "mkwvk::(anonymous namespace)::build_descriptors(int)",
        "mkwvk::(anonymous namespace)::texture_view_of(unsigned int)",
        "mkwvk::Recorder::operator()(int)",
        "mkwvk::Recorder::operator()(float)",
    ]
    output = "\n".join("%016x t %s" % (0x1000 + i * 16, name)
                       for i, name in enumerate(symbols))
    with patch.object(prof.subprocess, "run", return_value=SimpleNamespace(stdout=output)):
        addresses, names = prof.load_elf("synthetic.so")
    check(names == symbols and addresses == [0x1000, 0x1010, 0x1020, 0x1030],
          "anonymous namespace functions and overloaded operators keep distinct names")
    for module, names, expected in (
        ("longshot.exe", ["longshot:WaitForSingleObject"], False),
        ("longshot", ["longshot:prof_symbol_probe"], False),
        ("longshot_vk.so", ["longshot_vk:frame_work"], False),
        ("ntdll.dll", ["longshot:WaitForSingleObject"], True),
        ("libc.so.6", ["longshot_vk:recording_wait"], True),
        ("libc.so.6", ["longshot_vk:frame_work"], False),
        ("longshot_vk.dylib", ["longshot_vk.dylib"], False),
        ("libsystem_kernel.dylib", ["longshot_vk:mkwvk::recording_wait(int)"], True),
        ("libsystem_platform.dylib", ["longshot_vk:mkwvk::frame_work(int)"], False),
    ):
        check(prof.waiting(module, names) == expected, "shared running/waiting classification: " + module)
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--cc", default="cc")
    p.add_argument("--cxx", default="clang++")
    p.add_argument("--arch", action="append", choices=("arm64", "x86_64"))
    p.add_argument("--out", type=Path)
    a = p.parse_args()
    arches = a.arch or ["arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"]

    def exercise(tmp):
        global _log_dir
        _log_dir = tmp / "commands"
        _log_dir.mkdir()
        inputs = (SCRIPTS / "prof.py", SCRIPTS / "crit.py", SCRIPTS / "chain.py", SCRIPTS / "hotlist.py", Path(__file__))
        (tmp / "source-sha256.json").write_text(json.dumps({str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}, indent=2) + "\n")
        windows_map(tmp)
        linux_elf(tmp, a.cc)
        darwin_macho(tmp, a.cxx, arches)

    if a.out:
        a.out.mkdir(parents=True, exist_ok=False)
        exercise(a.out.resolve())
    else:
        with tempfile.TemporaryDirectory() as d:
            exercise(Path(d))
    print("ok")


if __name__ == "__main__":
    main()
