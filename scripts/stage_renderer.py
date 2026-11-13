"""A renderer built since (longshot_vk.dll, longshot_vk.so on Linux) staged beside a game as <its name>.new, for
HOS_DEV_DLL_SWAP=1: the game started again (scripts/longshot_ctl.py relaunch) takes it in the old one's place
(host/rx_backend.c dev_dll_swap). A development loop's way to try a renderer change without building the whole game.

    python -I scripts/stage_renderer.py GAME_DIR [RENDERER]

RENDERER defaults to the work folder's build (OOT_WORK, else work/: sa/build/longshot_vk.dll or .so). It is staged only
when it pairs with the game's executable:
  - Linux: every symbol it leaves for the loader to supply (nm -D --undefined-only), weak ones too, unless versioned (the
    C and C++ runtime's: @GLIBC_..., @GLIBCXX_...), is one the executable exports. The renderer is opened with RTLD_NOW
    and reaches the host through weak hos_* symbols: a renderer from another tree fails at dlopen, or worse, finds a
    weak one missing and calls through NULL. build.py's check_vk_symbols cannot see this: it has no executable to pair.
  - Windows: every function it imports from the executable is one the executable exports (llvm-objdump -p); a renderer
    that imports nothing from it has nothing to pair.
One that does not pair is not staged, and the symbols are named. Standard library only (nm and llvm-objdump run).
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
LINUX = sys.platform.startswith("linux")
NAME = "longshot_vk.so" if LINUX else "longshot_vk.dll"
EXE = "longshot" if LINUX else "longshot.exe"


# (the weak names every shared object's start files leave, which the C runtime gives or leaves NULL on purpose)
TOOLCHAIN_WEAK = {"__gmon_start__", "__cxa_finalize", "_Jv_RegisterClasses", "__register_frame_info",
                  "__deregister_frame_info"}


def nm_left(text):
    """nm -D --undefined-only's lines: the names left for the loader (U, and w: weak), but the versioned ones (the
    runtime's) and the start files' own weak ones (TOOLCHAIN_WEAK, _ITM_*)"""
    out = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-2] in ("U", "w", "v") and "@" not in parts[-1]:
            name = parts[-1]
            if name not in TOOLCHAIN_WEAK and not name.startswith("_ITM_"):
                out.add(name)
    return out


def nm_defined(text):
    """nm -D --defined-only's lines: the names defined (the version dropped: a name@@VER defines name)"""
    out = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[-2] not in ("U", "w", "v"):
            out.add(parts[-1].split("@")[0])
    return out


def pe_imports(text, module):
    """llvm-objdump -p's import table: the names imported from module (its DLL Name, any case)"""
    out, current = set(), None
    for line in text.splitlines():
        m = re.match(r"\s*DLL Name:\s*(\S+)", line)
        if m:
            current = m.group(1).lower()
            continue
        if current == module.lower():
            m = re.match(r"\s*\d+\s+(\S+)\s*$", line)   # ("    1234  name": a hint and the name)
            if m:
                out.add(m.group(1))
    return out


def pe_exports(text):
    """llvm-objdump -p's export table: the names exported"""
    out, inside = set(), False
    for line in text.splitlines():
        if line.startswith("Export Table:"):
            inside = True
            continue
        if inside:
            m = re.match(r"\s*\d+\s+0x[0-9a-f]+\s+(\S+)", line)
            if m:
                out.add(m.group(1))
            elif line and not line[0].isspace():
                inside = False
    return out


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def missing(renderer, exe):
    """the names the renderer needs from the executable and the executable does not give (an empty set: they pair)"""
    if LINUX:
        nm = shutil.which("nm") or shutil.which("llvm-nm")
        if not nm:
            raise SystemExit("stage_renderer: no nm to read the symbols with: not staged")
        return nm_left(run([nm, "-D", "--undefined-only", renderer])) - nm_defined(run([nm, "-D", "--defined-only", exe]))
    objdump = shutil.which("llvm-objdump") or shutil.which("objdump")
    if not objdump:
        raise SystemExit("stage_renderer: no llvm-objdump to read the imports with: not staged")
    wanted = pe_imports(run([objdump, "-p", renderer]), os.path.basename(exe))
    return wanted - pe_exports(run([objdump, "-p", exe])) if wanted else set()


def game_files(game):
    """(the executable, the folder the renderer sits in): bin/ when the game has one (a folder with a launcher)"""
    for d in (os.path.join(game, "bin"), game):
        if os.path.isfile(os.path.join(d, EXE)):
            return os.path.join(d, EXE), d
    raise SystemExit("stage_renderer: no %s in %s or its bin/" % (EXE, game))


def stage(game, renderer):
    exe, where = game_files(game)
    gone = missing(renderer, exe)
    if gone:
        names = sorted(gone)
        raise SystemExit("stage_renderer: %s needs %d symbol(s) %s does not give, so it was not staged (build the game's "
                         "executable from the same tree): %s" % (renderer, len(names), exe, ", ".join(names[:20])))
    dest = os.path.join(where, NAME + ".new")
    tmp = dest + ".%d.tmp" % os.getpid()
    shutil.copyfile(renderer, tmp)
    os.replace(tmp, dest)   # (whole: the game never takes half of one)
    return dest


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("game")
    p.add_argument("renderer", nargs="?")
    a = p.parse_args(argv)
    work = os.environ.get("OOT_WORK") or os.path.join(ROOT, "work")
    renderer = a.renderer or os.path.join(work, "sa", "build", NAME)
    if not os.path.isfile(renderer):
        raise SystemExit("stage_renderer: no renderer at %s" % renderer)
    dest = stage(a.game, renderer)
    print("staged %s: the game takes it at its next start with HOS_DEV_DLL_SWAP=1 (longshot_ctl.py relaunch)" % dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
