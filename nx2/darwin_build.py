"""macOS-only build policy. Imported by build.py on Darwin, without reading a game.

ld(1): two-level namespace is the default; -exported_symbol uses Mach-O names,
-order_file orders those names, and -undefined error is the default for dylibs.
This is not an ELF -Bsymbolic emulation or a portable application bundle builder.
"""
import functools
import hashlib
import os
import shlex
import shutil
import subprocess

import toolchain


EXE = "longshot"
VK_LIB = "longshot_vk.dylib"
SDL_LIB = "libSDL2-2.0.0.dylib"
BESIDE = [EXE, VK_LIB, SDL_LIB, "libSDL3.0.dylib"]


def posix_sources(folder, renderer=False):
    """Keep the shared Win32 layer; Linux's /proc sampler cannot be compiled here."""
    names = ["win32_posix.c"]
    if not renderer:
        # These contain no Linux system APIs: SDL display and absent Windows/WebGPU features.
        names += ["display_sdl.c", "linux_stubs.c"]
    names += sorted(n for n in os.listdir(folder) if n.startswith("darwin_") and n.endswith(".c")
                    and not (renderer and n in ("darwin_cpu.c", "darwin_vulkan.c")))
    return [os.path.join(folder, n) for n in names]


def order_file(source):
    """Convert LS's undecorated C symbol names to Mach-O names, never alter the input."""
    with open(source, encoding="utf-8") as f:
        names = [line.strip() for line in f if line.strip() and not line.lstrip().startswith("#")]
    # Even an ELF name starting '_' needs Darwin's additional leading underscore.
    text = "".join("_" + name + "\n" for name in names)
    path = source + ".macos"
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return path
    except FileNotFoundError:
        pass
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def pkg_config(names, prefix=None, static=False):
    """Use an explicit package's .pc files, else PATH/Homebrew. Preserve shell-escaped paths."""
    pc = shutil.which("pkg-config")
    if not pc and os.path.isfile("/opt/homebrew/bin/pkg-config"):
        pc = "/opt/homebrew/bin/pkg-config"
    if not pc:
        raise FileNotFoundError("pkg-config not found on PATH or in /opt/homebrew/bin")
    env = os.environ.copy()
    if prefix:
        # A chosen x86_64 prefix must not quietly borrow an arm64 system package.
        dirs = [os.path.join(prefix, sub, "pkgconfig") for sub in ("lib", "share")]
        env["PKG_CONFIG_LIBDIR"] = env["PKG_CONFIG_PATH"] = os.pathsep.join(dirs)
    else:
        env["PKG_CONFIG_PATH"] = os.pathsep.join(filter(None, [env.get("PKG_CONFIG_PATH", ""),
            "/opt/homebrew/lib/pkgconfig", "/opt/homebrew/share/pkgconfig"]))
    common = [pc] + (["--static"] if static else [])
    def query(option):
        return subprocess.run(common + [option] + list(names), env=env, capture_output=True, text=True,
                              check=True, timeout=15).stdout.strip()
    return shlex.split(query("--cflags")), shlex.split(query("--libs"))


def ffmpeg_flags(names, missing=None):
    prefix = os.environ.get("LONGSHOT_FFMPEG")
    static = bool(prefix and not os.path.isfile(os.path.join(prefix, "lib", "libavcodec.dylib")))
    try:
        return pkg_config(["lib" + n for n in names], prefix, static)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        if missing is not None:
            missing.append("FFmpeg for macOS: pkg-config for %s; LONGSHOT_FFMPEG needs lib/pkgconfig (and this target's "
                           "libraries), otherwise PATH or /opt/homebrew. %s" % (", ".join(names), e))
        return None


def sdl_flags(missing=None):
    selected = os.environ.get("LONGSHOT_SDL2", "")
    try:
        if selected and not os.path.isfile(selected):
            raise FileNotFoundError("LONGSHOT_SDL2 must name an SDL2 .dylib")
        if selected:
            return [], [selected]
        return pkg_config(["sdl2"])
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        if missing is not None:
            missing.append("SDL2 for macOS: LONGSHOT_SDL2=<library.dylib> or pkg-config sdl2 through PATH/"
                           "/opt/homebrew. %s" % e)
        return None


def copy_sdl(out):
    """Only copy an explicitly selected library. Homebrew dependencies remain installed, not bundled."""
    selected = os.environ.get("LONGSHOT_SDL2", "")
    if selected:
        shutil.copyfile(selected, os.path.join(out, SDL_LIB))
    else:
        print("macOS SDL2/FFmpeg use their installed dylibs; not bundling Homebrew's dependency closure", flush=True)


@functools.lru_cache(maxsize=1)
def compiler_key():
    """Changing compiler or SDK must invalidate cached/kept objects, even at the same path."""
    identity = []
    for name in ("clang", "clang++"):
        identity += [toolchain.need(toolchain.find(name)), toolchain.version(name)]
    identity += toolchain.native_target()
    sdk = os.path.join(toolchain.darwin_sdk(), "SDKSettings.json")
    with open(sdk, "rb") as f:
        body = f.read()
    return hashlib.sha256("\n".join(identity).encode() + body).hexdigest()


def post_link(path):
    # Unlike ELF's shared link, Mach-O already rejects unresolved non-weak symbols.
    # Never use -undefined dynamic_lookup to bypass that check.
    print("macOS: nm -D ELF check skipped for %s; Mach-O -undefined error checked the link; "
          "GNU strip --strip-unneeded skipped (not a Mach-O option)" % os.path.basename(path), flush=True)
    return True
