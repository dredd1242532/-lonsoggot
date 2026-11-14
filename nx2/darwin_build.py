"""macOS-only build policy. Imported by build.py on Darwin, without reading a game.

ld(1): two-level namespace is the default; -exported_symbol uses Mach-O names,
-order_file orders those names, and -undefined error is mandatory. The packager
admits exact target/minimum-OS inputs, then relocates only owned copies.
"""
import functools
import hashlib
import os
import shlex
import shutil
import subprocess

import darwin_toolchain
import toolchain


EXE = "longshot"
VK_LIB = "longshot_vk.dylib"
SDL_LIB = "libSDL2-2.0.0.dylib"
BESIDE = [EXE, VK_LIB, SDL_LIB, "libSDL3.0.dylib"]

# An image-local helper is shared only when its dependencies are image-local too.
# Unknown additions stop the build until their owner classifies them here.
SHARED_SOURCES = ("darwin_fault.c", "darwin_fiber.c", "darwin_mem.c", "darwin_process.c", "darwin_wait.c")
HOST_SOURCES = ("darwin_control_sockets.c", "darwin_cpu.c", "darwin_guest_mem.c", "darwin_prof.c",
                "darwin_vulkan.c", "darwin_wait_state.c")


def posix_sources(folder, renderer=False):
    """Keep canonical wait/guest/profiler state and SDL/logger users out of the renderer."""
    available = {n for n in os.listdir(folder) if n.startswith("darwin_") and n.endswith(".c")}
    unknown = available - set(SHARED_SOURCES) - set(HOST_SOURCES)
    if unknown:
        raise ValueError("unclassified Darwin helpers (declare host/shared dependencies): " + ", ".join(sorted(unknown)))
    names = ["win32_posix.c"]
    if not renderer:
        names += ["display_sdl.c", "linux_stubs.c"]
    selected = set(SHARED_SOURCES) | (set() if renderer else set(HOST_SOURCES))
    return [os.path.join(folder, n) for n in names + sorted(available & selected)]


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


def dependencies(names, prebuilt="", missing=None, executable_dir=""):
    """Use the same exact library files for admission, the link and packaging."""
    from darwin_package import DependencyError, Plan
    errors = []
    try:
        plan = Plan(toolchain.architecture(), toolchain.darwin_sdk(), executable_dir)
    except (DependencyError, OSError, SystemExit) as e:
        if missing is not None:
            missing.append("macOS toolchain: " + str(e))
            return None
        raise DependencyError("macOS toolchain: " + str(e)) from e
    ffmpeg, sdl = ffmpeg_flags(names, errors), sdl_flags(errors)
    if ffmpeg:
        try:
            pinned, _ = plan.pin_link_flags(ffmpeg[1])
            plan.ffmpeg = ffmpeg[0], pinned
            plan.header_key = ffmpeg_headers_key(ffmpeg[0], names)
        except (DependencyError, OSError) as e:
            errors.append("FFmpeg for macOS: " + str(e))
    if sdl:
        try:
            pinned, inputs = plan.pin_link_flags(sdl[1])
            roots = [p for p in inputs if plan.files[p]["kind"] == "dylib" and
                     (os.environ.get("LONGSHOT_SDL2") or os.path.basename(p).startswith("libSDL2"))]
            if len(roots) != 1:
                raise DependencyError("select one SDL2 dylib (not a static SDL archive)")
            plan.add(roots[0], SDL_LIB)
            plan.sdl = sdl[0], pinned
            if any("sdl3" in p.lower() for p in plan.files[roots[0]]["rpaths"]) and not os.environ.get("LONGSHOT_SDL3"):
                raise DependencyError("SDL2-compat's SDL3 search path requires explicit LONGSHOT_SDL3=<library.dylib>")
        except (DependencyError, OSError) as e:
            errors.append("SDL2 for macOS: " + str(e))
    sdl3 = os.environ.get("LONGSHOT_SDL3", "")
    if sdl3:
        try:
            key = plan.add(sdl3, "libSDL3.0.dylib")
            if plan.files[key]["kind"] != "dylib":
                raise DependencyError("SDL3 must be a dylib")
            # SDL2-compat dlopens this unversioned name; it is not an LC_LOAD_DYLIB edge.
            plan.alias("libSDL3.dylib", "libSDL3.0.dylib")
        except (DependencyError, OSError) as e:
            errors.append("SDL3 for macOS: " + str(e))
    molten = os.environ.get("LONGSHOT_MOLTENVK", "")
    if not molten:
        errors.append("LONGSHOT_MOLTENVK must name the approved libMoltenVK.dylib for this target; no runtime is downloaded")
    else:
        try:
            key = plan.add(molten, "libMoltenVK.dylib")
            if plan.files[key]["kind"] != "dylib":
                raise DependencyError("MoltenVK must be a dylib")
        except (DependencyError, OSError) as e:
            errors.append("MoltenVK for macOS: " + str(e))
    if prebuilt:
        try:
            key = plan.add(prebuilt, VK_LIB)
            if plan.files[key]["kind"] != "dylib":
                raise DependencyError("prebuilt renderer must be a dylib")
        except (DependencyError, OSError) as e:
            errors.append("Vulkan renderer for macOS: " + str(e))
    for tool in ("lipo", "install_name_tool", "codesign"):
        try:
            toolchain.need(toolchain.find(tool))
        except SystemExit as e:
            errors.append(str(e))
    if errors:
        if missing is not None:
            missing.extend(errors)
            return None
        raise DependencyError("Missing macOS build dependencies: " + "; ".join(errors))
    return plan


def ffmpeg_headers_key(cflags, names):
    """All selected codec headers, not just the major-version macros, key host objects."""
    from pathlib import Path
    from darwin_package import DependencyError, digest
    roots = []
    it = iter(cflags)
    for flag in it:
        if flag == "-I":
            path = next(it, "")
            if not path:
                raise DependencyError("missing FFmpeg include path after -I")
            roots.append(Path(path))
        elif flag.startswith("-I"):
            roots.append(Path(flag[2:]))
    result = hashlib.sha256()
    for name in names:
        root = next((p / ("lib" + name) for p in roots if (p / ("lib" + name) / (name + ".h")).is_file()), None)
        if root is None:
            raise DependencyError("missing selected FFmpeg header: lib%s/%s.h" % (name, name))
        for path in sorted(root.rglob("*.h")):
            result.update(("lib" + name + "/" + path.relative_to(root).as_posix()).encode() + b"\0")
            result.update(digest(path).encode())
    return result.digest()


@functools.lru_cache(maxsize=1)
def compiler_key():
    """Changing compiler or SDK must invalidate cached/kept objects, even at the same path."""
    from darwin_package import digest
    identity = []
    for name in ("clang", "clang++"):
        path = os.path.realpath(toolchain.need(toolchain.find(name)))
        identity += [path, darwin_toolchain.version(name), digest(path)]
    identity += toolchain.native_target()
    sdk = os.path.join(darwin_toolchain.darwin_sdk(), "SDKSettings.json")
    return hashlib.sha256("\n".join(identity).encode() + digest(sdk).encode()).hexdigest()


def post_link(path):
    from darwin_package import DependencyError, inspect
    try:
        inspect(path, toolchain.architecture())
    except DependencyError as e:
        print("macOS post-link check failed: " + str(e), flush=True)
        return False
    print("macOS: target/minimum-OS and strict two-level imports checked for %s; ELF nm/strip not used" %
          os.path.basename(path), flush=True)
    return True
