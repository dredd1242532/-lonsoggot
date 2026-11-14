"""The x86-64 level this PC's CPU runs (nx2/build.py's -march; issue 363). The game is built on the PC it runs on, so a
CPU without x86-64-v3's features (AVX2, FMA, BMI2, MOVBE) gets x86-64-v2, or the x86-64 baseline. RX_MARCH, read by
build.py, still chooses."""
import os
import sys

if sys.platform == "darwin":
    sys.path[:0] = [os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "third_party", "a64-kit", "tools"),
                    os.path.dirname(os.path.abspath(__file__))]
    import darwin_toolchain


def host_march():
    if sys.platform == "darwin" and os.name != "nt":
        # Rosetta advertises v2. Native arm64 needs no x86 -march (RX_MARCH still overrides in build.py).
        return "" if darwin_toolchain.architecture() == "arm64" else "x86-64-v2"
    want = ("avx2", "fma", "bmi2", "movbe")
    try:
        if os.name == "nt":
            import ctypes
            pf = ctypes.windll.kernel32.IsProcessorFeaturePresent
            # (PF_AVX2_INSTRUCTIONS_AVAILABLE 40; PF_SSE4_2_INSTRUCTIONS_AVAILABLE 38. Windows reports no FMA/BMI2 bit, but
            # every AVX2 CPU has them, as x86-64-v3 counts them together)
            if pf(40):
                return "x86-64-v3"
            return "x86-64-v2" if pf(38) else "x86-64"
        with open("/proc/cpuinfo") as f:
            flags = f.read().split("flags", 1)[1].split(chr(10), 1)[0].split()
        if all(f in flags for f in want):
            return "x86-64-v3"
        return "x86-64-v2" if all(f in flags for f in ("sse4_2", "popcnt", "ssse3")) else "x86-64"
    except Exception:
        return "x86-64-v3"


def host_mtune():
    """Tuning target for the host CPU (e.g. znver2 for Steam Deck APU 0405/0932)"""
    if sys.platform == "darwin" and os.name != "nt":
        return ""
    if os.environ.get("RX_STEAMDECK") == "1":
        return "znver2"
    if os.name != "nt":
        try:
            with open("/proc/cpuinfo") as f:
                info = f.read()
            if "Custom APU 0405" in info or "Custom APU 0932" in info:
                return "znver2"
        except Exception:
            pass
    return ""
