"""Which ASTC blocks are provably opaque (gpu/vk/astc_opaque.h, RX_VK_ASTC_BC1), with no game and no GPU.

Hand-built blocks of each kind (every colour endpoint mode in one partition and in several, partitions of different modes,
void-extent blocks, reserved modes, blocks that are not legal), the walk of a level's blocks in its block-linear storage
against the GPU decoder's address arithmetic, and 420000 random blocks against the tree's own ASTC decoder
(gpu/third_party/suyu/video_core/textures/astc.cpp): a block judged opaque must decode to alpha 255 in every texel.

    python -I -B tests/test_astc_opaque.py [--cxx g++]
"""
import argparse
import os
import subprocess
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]


def check_source():
    """The texture code still asks the header, behind the switch, before it makes a BC1 image and of what each upload stages"""
    src = (ROOT / "gpu/vk/vk_texture.cpp").read_text(encoding="utf-8")
    for needle in ('#include "astc_opaque.h"', 'env_on("RX_VK_ASTC_BC1")', "astc_opaque::level(",
                   "astc_bc1_on() && astc_blocks_opaque(x, x.guest_size)",
                   "opaque = opaque && astc_layer_opaque(tex, held.data() + k * tail, n);"):
        if needle not in src:
            raise SystemExit("vk_texture.cpp no longer has: " + needle)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    gpu = ROOT / "gpu"
    with tempfile.TemporaryDirectory(prefix="astc-opaque-") as tmp:
        exe = Path(tmp) / "astc-opaque.exe"
        subprocess.run([args.cxx, "-std=c++20", "-O1", "-DNDEBUG", "-Wall", "-Wextra", "-Werror",
                        *([] if os.name == "nt" else ["-pthread"]),
                        # (the decoder's include folders, in the build's order (nx2/build.py), as system folders: its own
                        # warnings are not this test's)
                        "-isystem", str(gpu / "vk" / "compat"), "-isystem", str(gpu / "compat"),
                        "-isystem", str(gpu / "third_party" / "suyu"),
                        str(ROOT / "tests/astc_opaque_test.cpp"), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True, timeout=600)


if __name__ == "__main__":
    main()
