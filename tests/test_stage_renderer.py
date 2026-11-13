"""A renderer built since, staged beside the game for HOS_DEV_DLL_SWAP (scripts/stage_renderer.py, host/rx_backend.c
dev_dll_swap; the dev-loop proposal's B1).

stage_renderer.py's readers of nm and llvm-objdump output, on the lines those print: the names a renderer leaves for the
loader (weak ones too, not the runtime's versioned ones), the names an executable defines, a PE file's imports from one
module and its exports. A renderer that needs a name the executable does not give is not staged, and the name is said;
one that pairs is copied whole to <name>.new beside the executable (bin/ when the game has one). On Linux a real pair is
built (an executable exporting some host functions, a shared object wanting them, weak and strong): the one missing is
named. dev_dll_swap is cut out of host/rx_backend.c and run (Windows, and Linux over host/posix/win32_posix.c, where it
swaps the .so): with the switch, a staged renderer takes the old one's place; without it, or with none staged, nothing
changes.

    python -I -B tests/test_stage_renderer.py [--cc gcc]
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest import mock

RUN_ALL_ARGS = ["--cc", "gcc"]

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import stage_renderer as sr  # noqa: E402

NM_UNDEFINED = """                 U hos_log
                 w hos_present_count
                 U memcpy@GLIBC_2.14
                 U _ZSt4cout@GLIBCXX_3.4
                 w __gmon_start__
                 w _ITM_deregisterTMCloneTable
                 w __cxa_finalize
                 U vkGetInstanceProcAddr
"""
NM_DEFINED = """0000000000401000 T hos_log
0000000000402000 B hos_present_count
0000000000403000 T main@@SOME_1.0
"""
OBJDUMP_DLL = """The Import Tables:
  lookup 009f8a40 time 00000000 fwd 00000000 name 009fb1b0 addr 009f9468

    DLL Name: USER32.dll
    Hint/Ord  Name
           0  CloseClipboard
           0  DispatchMessageA

    DLL Name: longshot.exe
    Hint/Ord  Name
           0  hos_log
           0  hos_gone
"""
OBJDUMP_EXE = """Export Table:
 DLL name: longshot.exe
 Ordinal base: 1
 Ordinal      RVA  Name
       1 0x199500dc  AmdPowerXpressRequestHighPerformance
       2 0x199500d8  hos_log

The Import Tables:
"""


def check_readers():
    # (not the runtime's versioned names, nor the start files' own weak ones)
    assert sr.nm_left(NM_UNDEFINED) == {"hos_log", "hos_present_count", "vkGetInstanceProcAddr"}
    assert sr.nm_defined(NM_DEFINED) == {"hos_log", "hos_present_count", "main"}
    assert sr.pe_imports(OBJDUMP_DLL, "longshot.exe") == {"hos_log", "hos_gone"}
    assert sr.pe_imports(OBJDUMP_DLL, "LONGSHOT.EXE") == {"hos_log", "hos_gone"}
    assert sr.pe_imports(OBJDUMP_DLL, "other.exe") == set()
    assert sr.pe_exports(OBJDUMP_EXE) == {"AmdPowerXpressRequestHighPerformance", "hos_log"}


def check_stage(tmp):
    game = Path(tmp) / "game"
    (game / "bin").mkdir(parents=True)
    (game / "bin" / sr.EXE).write_bytes(b"exe")
    renderer = Path(tmp) / sr.NAME
    renderer.write_bytes(b"the renderer built since")
    with mock.patch.object(sr, "missing", return_value={"hos_gone", "hos_older"}):
        try:
            sr.stage(str(game), str(renderer))
        except SystemExit as e:
            assert "hos_gone, hos_older" in str(e) and "not staged" in str(e), e
        else:
            raise AssertionError("a renderer that does not pair was staged")
    assert not list((game / "bin").glob("*.new"))
    with mock.patch.object(sr, "missing", return_value=set()):
        dest = sr.stage(str(game), str(renderer))
    assert dest == str(game / "bin" / (sr.NAME + ".new")) and Path(dest).read_bytes() == b"the renderer built since"
    assert sorted(p.name for p in (game / "bin").iterdir()) == sorted([sr.EXE, sr.NAME + ".new"])   # (no temporary left)
    flat = Path(tmp) / "flat"   # (a game with no bin/: beside the executable)
    flat.mkdir()
    (flat / sr.EXE).write_bytes(b"exe")
    with mock.patch.object(sr, "missing", return_value=set()):
        assert sr.stage(str(flat), str(renderer)) == str(flat / (sr.NAME + ".new"))


def check_linux_pair(cc, tmp):
    """a real executable and shared object: the weak and strong names the exe does not export are the ones named"""
    d = Path(tmp) / "pair"
    d.mkdir()
    (d / "exe.c").write_text("int hos_log(void) { return 1; }\nint hos_present_count;\nint main(void) { return 0; }\n")
    (d / "so.c").write_text("extern int hos_log(void);\nextern int hos_gone(void) __attribute__((weak));\n"
                            "extern int hos_present_count;\nextern int hos_older;\n"
                            "int f(void) { return hos_log() + (hos_gone ? hos_gone() : 0) + hos_present_count + hos_older; }\n")
    subprocess.run([cc, "-rdynamic", str(d / "exe.c"), "-o", str(d / sr.EXE)], check=True)
    subprocess.run([cc, "-shared", "-fPIC", str(d / "so.c"), "-o", str(d / sr.NAME)], check=True)
    assert sr.missing(str(d / sr.NAME), str(d / sr.EXE)) == {"hos_gone", "hos_older"}
    (d / "exe.c").write_text("int hos_log(void) { return 1; }\nint hos_present_count, hos_older;\n"
                             "int hos_gone(void) { return 2; }\nint main(void) { return 0; }\n")
    subprocess.run([cc, "-rdynamic", str(d / "exe.c"), "-o", str(d / sr.EXE)], check=True)
    assert sr.missing(str(d / sr.NAME), str(d / sr.EXE)) == set()


SWAP = r"""
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include "longshot_env.h"
static void hos_log(const char *f, ...) { (void)f; }
%SWAP%
int main(int argc, char **argv) { (void)argc; dev_dll_swap(argv[1]); return 0; }
"""


def check_swap(cc, tmp):
    """dev_dll_swap itself. Windows: the Win32 calls it makes are the real ones. Linux: over host/posix/win32_posix.c,
    as the game builds it; given the .dll's name, as load_vulkan is, it swaps the .so"""
    src = (ROOT / "host" / "rx_backend.c").read_text(encoding="utf-8")
    start = src.index("static void dev_dll_swap(const char *path) {")
    fn = src[start:src.index("\n}\n", start) + 3]
    c, exe = Path(tmp) / "swap.c", Path(tmp) / "swap.exe"
    c.write_text(SWAP.replace("%SWAP%", fn), encoding="utf-8")
    compat = str(ROOT / "gpu" / "vk" / "compat")
    if os.name == "nt":
        cmd = [cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-I", compat, str(c)]
    else:   # (the shim is built with the game's own warnings, not these: -w, as the other checks over it do)
        cmd = [cc, "-std=gnu11", "-D_GNU_SOURCE", "-w", "-I", str(ROOT / "host" / "posix"), "-I", compat, str(c),
               str(ROOT / "host" / "posix" / "win32_posix.c"), "-lpthread", "-ldl"]
    subprocess.run(cmd + ["-o", str(exe)], check=True)
    game = Path(tmp) / "swapgame"
    game.mkdir()
    given = game / "longshot_vk.dll"
    dll = given if os.name == "nt" else game / "longshot_vk.so"
    new = dll.with_name(dll.name + ".new")
    env = {k: v for k, v in os.environ.items() if k != "HOS_DEV_DLL_SWAP"}
    for switch, staged, want in ((None, True, "old"), ("0", True, "old"), ("1", False, "old"), ("1", True, "new")):
        dll.write_bytes(b"old")
        if staged:
            new.write_bytes(b"new")
        elif new.exists():
            new.unlink()
        e = dict(env, **({"HOS_DEV_DLL_SWAP": switch} if switch is not None else {}))
        subprocess.run([str(exe), str(given)], env=e, check=True)
        assert dll.read_bytes().decode() == want, (switch, staged, dll.read_bytes())
        assert new.exists() == (staged and want == "old"), (switch, staged)


def check_source():
    src = (ROOT / "host" / "rx_backend.c").read_text(encoding="utf-8")
    load = src[src.index("static const MkwVkRenderer *load_vulkan(const char *dll) {"):]
    assert load.index("dev_dll_swap(path);") < load.index("LoadLibraryA(path)")
    fn = src[src.index("static void dev_dll_swap("):]
    assert fn.index('if (!env_on("HOS_DEV_DLL_SWAP")) return;') < fn.index("GetFileAttributesA(staged)")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--cc", default="gcc")
    a = p.parse_args()
    check_source()
    check_readers()
    with tempfile.TemporaryDirectory(prefix="stage-renderer-") as tmp:
        check_stage(tmp)
        if sr.LINUX and shutil.which("nm"):
            check_linux_pair(a.cc, tmp)
            print("linux: a real executable and shared object paired")
        if os.name == "nt" or sr.LINUX:
            check_swap(a.cc, tmp)
            print("%s: dev_dll_swap run" % ("windows" if os.name == "nt" else "linux, over the POSIX shim"))
    print("stage_renderer: readers, staging, the swap under its switch only")


if __name__ == "__main__":
    main()
