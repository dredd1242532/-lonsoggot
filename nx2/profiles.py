"""The build's environment in one place (the dev-loop proposal's A9, #2636): what nx2/make_game.py gives the lift and nx2/build.py.
A build started by hand (nx2/dev.py) takes the same, so its object keys and lift key are the builders' and nothing is compiled or
translated again because a variable was missing or extra.

Two profiles:
  release   what the builders make (LONGSHOT_PROFILE unset): RX_RELEASE=1, no console window, the shaders prepared before
            the title
  dev       LONGSHOT_PROFILE=dev: no RX_RELEASE. The exe keeps its console and the shaders are prepared as the game meets
            them; the renderer's objects are their own (-DLONGSHOT_RELEASE is a flag), the lifted code's are the release's, and
            the lift is the same (RX_RELEASE is not in its key)

Standard library only."""
import os

PROFILES = ("release", "dev")
TOOLCHAIN_ENV = ("LONGSHOT_MINGW", "LONGSHOT_MSVC", "EMSDK", "RX_CPM", "WGPU_NATIVE", "LONGSHOT_NGX_SDK", "LONGSHOT_FSR",
                 "LONGSHOT_JOBS", "RX_MARCH", "RX_TARGET")   # (where the tools are and what they make: dev.py build passes them on)


def profile(environ=None):
    """The profile this build is (LONGSHOT_PROFILE, release unless it says dev)"""
    name = (os.environ if environ is None else environ).get("LONGSHOT_PROFILE", "") or "release"
    if name not in PROFILES:
        raise SystemExit("LONGSHOT_PROFILE=%s: the profiles are %s" % (name, ", ".join(PROFILES)))
    return name


def build_env(work, exefs, romfs, out, profile_ok=lambda prof: True, name="release"):
    """The variables the lift and build.py run with: the folders, the hot pieces' switch, the hot list and the play profile when
    the work folder has them (profile_ok: whether this compiler can read the profile), and RX_RELEASE in the release profile"""
    env = {"OOT_WORK": work, "OOT_EXEFS": exefs, "OOT_ROMFS": romfs, "OOT_DUMP": out, "A64_HOT_CHUNK": "1"}
    if name == "release":
        env["RX_RELEASE"] = "1"
    if os.path.exists(os.path.join(work, "hot_funcs.txt")):
        env["A64_HOT_FUNCS"] = os.path.join(work, "hot_funcs.txt")
    prof = os.path.join(work, "longshot_pieces.prof")
    if os.path.exists(prof) and profile_ok(prof):
        env["RX_PGO"] = prof
    return env
