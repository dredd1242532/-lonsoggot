"""The images' pool (DLSS's, the frame interpolation's and the scratch images) has blocks of 32 MB, not the textures' 128.

The textures and the render targets have pools of their own; the images' pool held 11-17 MB in its one 128 MB block in every
report of eight 9-minute routes on an 8 GB RTX 5060 Ti, the rest kept empty by VMA and counted against the textures' budget.
RX_VK_IMAGES_BLOCK_MB sizes it (128: as before). Where the pool holds the textures (RX_VK_COMPACT=0) or the render targets
(RX_VK_TARGET_BLOCKS=0) it keeps RX_VK_IMAGE_BLOCK_MB's size. block_mb() is cut out of gpu/vk/vk_vma.cpp, compiled and run
with a switch set and unset; the pools are checked to use those sizes. No GPU or game.

python -I -B tests/test_image_block_sizes.py [--cxx g++]
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


def need(where, text, needles):
    for needle in needles:
        if needle not in text:
            raise SystemExit(where + " no longer has: " + needle)


def check_source(vma):
    need("vk_vma.cpp", vma, ['const uint64_t kImageBlock = block_mb("RX_VK_IMAGE_BLOCK_MB", 128ull << 20);',
                             'const uint64_t kImagesBlock = block_mb("RX_VK_IMAGES_BLOCK_MB", 32ull << 20);'])
    need("vk_vma.cpp: target_images_pool()", block(vma, "VmaPool target_images_pool() {"), ["pi.blockSize = kImageBlock;"])
    need("vk_vma.cpp: textures_pool()", block(vma, "VmaPool textures_pool() {"), ["pi.blockSize = kImageBlock;"])
    # (the images' pool keeps the large blocks where it holds the textures or the render targets)
    need("vk_vma.cpp: images_pool()", block(vma, "VmaPool images_pool() {"),
         ['const bool holds_textures = !vram_compact::enabled(getenv("RX_VK_COMPACT"));',
          'const bool holds_targets = getenv("RX_VK_TARGET_BLOCKS") && !atoi(getenv("RX_VK_TARGET_BLOCKS"));',
          "pi.blockSize = holds_textures || holds_targets ? kImageBlock : kImagesBlock;",
          "make_pool(pi, blocks_at_start(holds_textures), image_pool)"])
    # (and the switch that turns the targets' pool off reads the same way, so the two agree)
    need("vk_vma.cpp: target_images_pool()", block(vma, "VmaPool target_images_pool() {"),
         ['static const bool off = getenv("RX_VK_TARGET_BLOCKS") && !atoi(getenv("RX_VK_TARGET_BLOCKS"));'])


PROGRAM = r"""
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#ifdef _WIN32
static void setenv(const char* name, const char* value, int) { _putenv_s(name, value); }
#endif
%s
static int failed = 0;
static void expect(const char* what, uint64_t got, uint64_t want) {
    if (got != want) { std::printf("%%s: %%llu, not %%llu\n", what, (unsigned long long)got, (unsigned long long)want); failed = 1; }
}
int main() {
    const uint64_t def = 32ull << 20;
    expect("unset", block_mb("TEST_BLOCK_MB", def), def);
    setenv("TEST_BLOCK_MB", "64", 1);
    expect("64", block_mb("TEST_BLOCK_MB", def), 64ull << 20);
    setenv("TEST_BLOCK_MB", "8", 1);
    expect("below the floor", block_mb("TEST_BLOCK_MB", def), 16ull << 20);
    setenv("TEST_BLOCK_MB", "0", 1);
    expect("0", block_mb("TEST_BLOCK_MB", def), 16ull << 20);
    setenv("TEST_BLOCK_MB", "-5", 1);
    expect("negative", block_mb("TEST_BLOCK_MB", def), 16ull << 20);
    setenv("TEST_BLOCK_MB", "4096", 1);
    expect("4096", block_mb("TEST_BLOCK_MB", def), 4096ull << 20);
    return failed;
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    vma = (ROOT / "gpu/vk/vk_vma.cpp").read_text(encoding="utf-8")
    check_source(vma)
    with tempfile.TemporaryDirectory(prefix="image-block-sizes-") as tmp:
        src = Path(tmp) / "image_block_sizes.cpp"
        src.write_text(PROGRAM % block(vma, "static uint64_t block_mb("), encoding="utf-8")
        exe = Path(tmp) / "image-block-sizes"
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", str(src), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
