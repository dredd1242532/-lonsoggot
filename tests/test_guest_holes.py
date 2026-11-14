"""The all-zero check of a big texture reads only what the game ever wrote (gpu/vk/guest_holes.h; no game/GPU).

On Linux the guest window is a memfd's, and a part of it never written, or given back since, is a hole of the file:
it reads as zero, and reading it through the mapping makes its pages. The game's shadow map (12288 x 12288 D16,
288 MB, fresh at each arrival in an area) was read whole to find it zero: 117 to 153 ms of the replay thread on a
Steam Deck, and 288 MB of pages made for it. upload_all_zero now asks the file what holds data and reads that.

Compiles tests/test_guest_holes.cpp: the question against a real memfd (Linux; elsewhere it answers "not known"), and
the read on plain memory.

python -I -B tests/test_guest_holes.py [--cxx g++]
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def check_source():
    """The check still asks for the written parts, reads only them, and the switch back is still read"""
    tex = (ROOT / "gpu/vk/vk_texture.cpp").read_text(encoding="utf-8")
    body = block(tex, "bool upload_all_zero(Texture& tex, uint64_t total, const Ranges* dirty, bool widened_format) {")
    for needle in ("memory_guest_data_parts(p, total, parts)", "guest_holes::all_zero(p, parts, ", '"RX_VK_ZERO_HOLES"',
                   "parts.assign(1, {0, total});"):
        if needle not in body:
            raise SystemExit("upload_all_zero no longer has: " + needle)
    if "std::memcpy(&w, p + k, 8)" in body or "std::vector<std::thread>" in body:
        raise SystemExit("upload_all_zero reads the storage itself again, past the written parts")
    if body.index("memory_guest_data_parts(p, total, parts)") > body.index("guest_holes::all_zero(p, parts, "):
        raise SystemExit("upload_all_zero reads before it asks what was written")
    mem = (ROOT / "gpu/vk/vk_memory.cpp").read_text(encoding="utf-8")
    parts = block(mem, "bool memory_guest_data_parts(const uint8_t* p, uint64_t n, std::vector<std::pair<uint64_t, uint64_t>>& parts) {")
    for needle in ('"hos_posix_guest_memfd"', "guest_holes::data_parts(", "host->guest_base()", "host->guest_size()"):
        if needle not in parts:
            raise SystemExit("memory_guest_data_parts no longer has: " + needle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="guest-holes-") as tmp:
        exe = Path(tmp) / "guest-holes"
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-pthread",
                        str(ROOT / "tests/test_guest_holes.cpp"), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
