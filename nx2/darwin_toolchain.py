"""(macOS) a64-kit's tools/toolchain.py and lift/build_emit.py, made to form Mach-O commands from outside third_party/.

CONTRIBUTING keeps third_party/ as its upstream copies, so the macOS parts live here. install() replaces four of
toolchain's functions (find, need, native_target, native_link) and build_emit's output name and build, as nx2/insn.py
replaces a64emit.translate. kit.py and make_game.py call it on macOS only: on Windows and Linux nothing imports this
file, and the commands are a64-kit's own (tests/test_macos_build_flags.py compares them with main's).

On macOS:
- the output system and architecture are separate from the Python process: RX_ARCH chooses the game's (arm64 or
  x86_64), never the helpers' that Python loads (a64emit, zbic), which follow the process;
- tools come from an explicit EMSDK prefix (bin/, or emsdk's upstream/bin/), then PATH, then xcrun;
- compiles take -arch, a 14.4 deployment target and Apple's SDK (SDKROOT, else xcrun's); links take a Mach-O linker
  (ld64.lld when there is one, else Apple's ld), never ld.lld's ELF driver;
- a64emit is liba64emit-<process arch>.dylib, rebuilt when its compiler, that compiler's version or its flags change.
Written from Apple's ld(1) and xcrun(1) and Clang's documented driver options.
"""
import functools
import hashlib
import os
import platform
import shutil
import subprocess
import sys

import toolchain

_A64 = {}   # a64-kit's own native_target and native_link, for a Windows target (llvm-mingw) built on a Mac
_EMITTERS = []   # the build_emit modules given their macOS output and build


def system(host=False):
    """The output system; host tools run on this PC, not a cross build's target"""
    if toolchain.windows() and not (host and os.name != "nt"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def architecture(host=False):
    """RX_ARCH selects a macOS game, never the Python process's helpers"""
    if system(host) != "macos":
        return "x86_64"
    arch = ("" if host else os.environ.get("RX_ARCH", "")) or platform.machine().lower()
    if not host and not os.environ.get("RX_ARCH") and arch == "x86_64":
        # A translated Python still defaults to the Apple Silicon target. Helpers follow its process ABI.
        if _apple_silicon():
            arch = "arm64"
    arch = {"aarch64": "arm64", "amd64": "x86_64"}.get(arch, arch)
    if arch not in ("arm64", "x86_64"):
        raise SystemExit("RX_ARCH must be arm64 or x86_64 for macOS (got %s)" % arch)
    return arch


@functools.lru_cache(maxsize=1)
def _apple_silicon():
    try:
        result = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.optional.arm64"], capture_output=True,
                                text=True, check=True, timeout=15)
        return result.stdout.strip() == "1"
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


@functools.lru_cache(maxsize=None)
def _xcrun(*args):
    try:
        return subprocess.run(["xcrun", "--sdk", "macosx", *args], capture_output=True, text=True,
                              check=True, timeout=15).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""


def darwin_sdk():
    """Apple's SDK, also needed by a standalone LLVM distribution"""
    sdk = os.environ.get("SDKROOT") or _xcrun("--show-sdk-path")
    if not sdk or not os.path.isdir(sdk):
        raise SystemExit("macOS SDK not found: set SDKROOT or select Command Line Tools with xcrun available")
    return sdk


def _darwin_find(tool):
    # EMSDK is already the build's compiler prefix. Accept a normal LLVM prefix as well as its old layout.
    root = os.environ.get("EMSDK", "")
    if root:
        for sub in ("bin", os.path.join("upstream", "bin")):
            path = os.path.join(root, sub, tool)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
    return shutil.which(tool, path=os.environ.get("PATH")) or _xcrun("--find", tool) or tool


def version(tool, host=False):
    """The tool's actual first version line, not an inferred tools.lock version"""
    path = toolchain.need(toolchain.find(tool, host))
    result = subprocess.run([path, "--version"], capture_output=True, text=True, check=True, timeout=15)
    return next((line.strip() for line in result.stdout.splitlines() if "version" in line.lower()),
                result.stdout.strip())


def find(tool, host=False):
    """toolchain.find on macOS: llvm-mingw's tools for a Windows target as before; else EMSDK, PATH, then xcrun"""
    mingw = toolchain.mingw(host)
    if mingw and (tool in ("clang", "clang++", "lld") or tool.startswith("llvm-")):
        path = os.path.join(mingw, "bin", ("ld.lld" if tool == "lld" else tool) + (".exe" if os.name == "nt" else ""))
        if os.path.isfile(path):
            return path
    return _darwin_find(tool)


def need(path):
    """toolchain.need on macOS, naming the three places a tool is looked for here"""
    if not os.path.isfile(path):
        raise SystemExit("%s not found: set EMSDK to an LLVM prefix (bin/ or upstream/bin/), put the tool on PATH, "
                         "or make it available through xcrun" % path)
    return path


def native_target(host=False):
    """toolchain.native_target on macOS: this Mac's architecture, 14.4 and Apple's SDK for a macOS output"""
    if system(host) == "macos":
        return ["-arch", architecture(host), "-mmacosx-version-min=14.4", "-isysroot", darwin_sdk()]
    return _A64["native_target"](host)


def native_link(host=False):
    """toolchain.native_link on macOS: a Mach-O linker, never ld.lld's ELF driver. The path stays one compiler argument,
    commas included"""
    if system(host) == "macos":
        lld = toolchain.find("ld64.lld", host)
        ld = lld if os.path.isfile(lld) else toolchain.need(toolchain.find("ld", host))
        return ["--ld-path=" + ld]
    return _A64["native_link"](host)


def emit_command(emit):
    """build_emit's compile for this Python process's architecture, callable without building the helper"""
    return [toolchain.need(toolchain.find("clang++", host=True)), "-std=c++20", "-O2", "-dynamiclib", "-o", emit.OUT,
            emit.SRC[0], "-Wno-everything"] + toolchain.native_target(host=True) + toolchain.native_link(host=True) + ["-fPIC"]


def _emit_build(emit):
    def build(force=False):
        """build_emit.build on macOS: rebuilt when the sources, the compiler, its version or the flags change"""
        cmd = emit_command(emit)
        body = b""
        for f in emit.SRC:
            with open(f, "rb") as src:
                body += src.read()
        key = hashlib.sha1(body + ("\n".join(cmd) + "\n" + version("clang++", host=True)).encode()).hexdigest()
        stamp = emit.OUT + ".sha1"
        if not force and os.path.exists(emit.OUT) and os.path.exists(stamp):
            with open(stamp) as f:
                if f.read() == key:
                    return emit.OUT
        print("building", os.path.basename(emit.OUT))
        subprocess.run(cmd, check=True)
        with open(stamp, "w") as f:
            f.write(key)
        return emit.OUT
    return build


def install_toolchain():
    """toolchain's find, need, native_target and native_link become this file's (once)"""
    if not _A64:
        _A64.update(native_target=toolchain.native_target, native_link=toolchain.native_link)
        toolchain.find, toolchain.need = find, need
        toolchain.native_target, toolchain.native_link = native_target, native_link


def install_emit(emit):
    """build_emit's output becomes liba64emit-<process arch>.dylib, and its build this file's (once)"""
    if not any(e is emit for e in _EMITTERS):
        _EMITTERS.append(emit)
        emit.OUT = os.path.join(emit.HERE, "liba64emit-%s.dylib" % architecture(host=True))
        emit.build = _emit_build(emit)


def install():
    """kit.py and make_game.py, on macOS: both of the above, with build_emit from a64-kit's lift folder"""
    install_toolchain()
    lift = os.path.join(os.path.dirname(toolchain.HERE), "lift")   # (make_game.py has only tools/ on its path)
    if lift not in sys.path:
        sys.path.insert(0, lift)
    import build_emit   # (a plain import: make_game.py's lift_sources follows it, as it follows every import of the lift's)
    install_emit(build_emit)
