"""A texture kept as BC1 (RX_VK_ASTC_BC1) that comes to hold alpha, and GPU copies between a BC1 and a BC3 texture.

Runs the real bc1_to_bc3, upload_astc_gpu, astc_layer_opaque, texture_copy_into_bc and bc_blit_to_scratch of
gpu/vk/vk_texture.cpp and the real texture_view_of of gpu/vk/vk_draw.cpp against modelled Vulkan
(tests/astc_bc1_remake_test.cpp), no game and no GPU: the image is made again as BC3 with its texels encoded over, the BC1
image and its views go to the dead list, the textures' count swaps one image's bytes for the other's, a failure changes
nothing, a present's conversions are capped, and a copy between the two formats is re-encoded, never copied block for block
and never encoded into a BC1 image. An upload reads the game's memory once a layer and judges what it stages: memory the
game writes while it is read is staged as it was judged, and blocks with alpha are never encoded into a BC1 image. A draw
whose touch made the image again is given a view of the new image, not the handle cache's retired one. Then, from the
source's text: the BC1 choice is made before the image is created and counted, the upload judges the one copy it stages
before anything is recorded, and vk_xfer.cpp sends a pair of two formats to the re-encode.

    python -I -B tests/test_astc_bc1_remake.py --headers <Vulkan-Headers/include> [--cxx g++] [--source <tree>]
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

# (env_on, the switches' rule, before the code cut out of the sources: gpu/vk/compat/longshot_env.h)
ENV_ON_H = str(Path(__file__).resolve().parents[1] / "gpu" / "vk" / "compat" / "longshot_env.h")

RUN_ALL_ARGS = ["--cxx", "{cxx}", "--headers", "{vulkan_headers}"]

ROOT = Path(__file__).resolve().parents[1]


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def in_order(text, where, *needles):
    """Each needle is in the text, after the one before it"""
    at = 0
    for needle in needles:
        found = text.find(needle, at)
        if found < 0:
            raise SystemExit("%s: not found, or not after what must come before it: %s" % (where, needle))
        at = found + len(needle)


def check_source(tex, xfer, draw):
    create = block(tex, "Texture* create(const Key& key")
    # The image is BC1 from the start: chosen before its create info takes the format, before it is made, and before its
    # bytes enter the textures' count at 8 bytes a block. Nothing sets the format again between the choice and the image.
    in_order(create, "create()",
             "if (x.bc && !as_target && astc_bc1_on() && astc_blocks_opaque(x, x.guest_size)) {",
             "x.bc1 = true;", "x.format = VK_FORMAT_BC1_RGB_UNORM_BLOCK;", "ci.format = x.format;", "create_image(ci, x.image",
             "// what it costs:", "x.bc1 ? 8 : x.bc ? 16 :", "total_bytes += x.bytes + x.pad;")
    chosen = create.index("x.format = VK_FORMAT_BC1_RGB_UNORM_BLOCK;") + 1
    if "x.format =" in create[chosen:create.index("ci.format = x.format;")]:
        raise SystemExit("create(): the format is set again after the BC1 choice, before the image is made")
    # An upload into a BC1 image reads the game's memory once into one copy, judges that copy and makes the image BC3 before
    # any staging is taken (an upload that must wait takes none), then stages from the copy: the only other read of the
    # memory is the one for an image not made as BC1, which has no copy (the game writes between two reads)
    upload = block(tex, "bool upload_astc_gpu(")
    in_order(upload, "upload_astc_gpu()", "if (tex.bc1) {",
             "std::memcpy(held.data() + k * tail, guest_bytes(tex.gpu + at, n, scratch), n);",
             "opaque = opaque && astc_layer_opaque(tex, held.data() + k * tail, n);",
             "if (!opaque && !bc1_to_bc3(tex, upload_cmd(), true)) {", "return false;",
             "const Staging s = staging(",
             "std::memcpy(s.data + k * tail, held.empty() ? guest_bytes(tex.gpu + at, n, scratch) : held.data() + k * tail, n);",
             "flush_mapped_range(", "bc_encode_into(")
    if upload.count("guest_bytes(") != 2:
        raise SystemExit("upload_astc_gpu(): the game's memory is read elsewhere too (what is judged may not be what is staged)")
    if "astc_blocks_opaque(" in block(tex, "bool upload_inner(Texture& tex, const Ranges* dirty) {"):
        raise SystemExit("upload_inner(): the blocks are judged in a read of the game's memory apart from the upload's")
    # A handle-cache hit whose touch made a texture's image again is resolved again, not answered with the view kept
    view = block(draw, "VkImageView texture_view_of(")
    in_order(view, "vk_draw.cpp texture_view_of()", "texture_touch(it->second.tex, it->second.sampling);",
             "stale = texture_destroy_generation() != dgen;", "if (!stale) {", "return it->second.view;", "texture_get(&t,")
    # The encoder writes colour blocks alone into a BC1 image, with the BC3 encoder (never the BC7 one)
    encode = block(tex, "void bc_encode_into(")
    in_order(encode, "bc_encode_into()", "!dst.bc1 && bc7_enabled() ? bc7_pipe : bc_pipe", "dst.bc1 ? 0u : 1u")
    # The switch, read once by the switches' rule; a BC1 image's views are BC1's
    in_order(tex, "vk_texture.cpp", "VK_FORMAT_BC1_RGB_SRGB_BLOCK : VK_FORMAT_BC1_RGB_UNORM_BLOCK", "bool astc_bc1_on() {",
             'env_on("RX_VK_ASTC_BC1")')
    # Two textures kept as BC that are not of one format go to texture_copy_into_bc, not to the block-for-block copy
    copy = block(xfer, "void copy_t2t(")
    in_order(copy, "vk_xfer.cpp copy_t2t()",
             "if (s.tex->bc != d.tex->bc || (s.tex->bc && (!same_size || s.tex->format != d.tex->format))) {",
             "if (d.tex->bc && same_size) {", "done = texture_copy_into_bc(cmd, s.tex,")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", type=Path, default=ROOT)
    p.add_argument("--headers", type=Path, required=True)
    p.add_argument("--cxx", default="g++")
    p.add_argument("--sanitize", action="store_true")
    a = p.parse_args()
    tex = (a.source / "gpu/vk/vk_texture.cpp").read_text(encoding="utf-8")
    xfer = (a.source / "gpu/vk/vk_xfer.cpp").read_text(encoding="utf-8")
    draw = (a.source / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    check_source(tex, xfer, draw)
    actual = "\n".join(block(tex, marker) for marker in (
        "void bc_blit_to_scratch(", "bool bc1_to_bc3(Texture& tex, VkCommandBuffer cmd, bool may_wait) {",
        "bool astc_layer_opaque(", "bool upload_astc_gpu(", "bool texture_copy_into_bc("))
    types = "\n".join(block(tex, marker) + ";" for marker in ("struct LevelGeo {", "struct AstcPush {", "struct Staging {"))
    fixture = (ROOT / "tests/astc_bc1_remake_test.cpp").read_text(encoding="utf-8")
    fixture = fixture.replace("/* DEAD */", block(tex, "struct Dead {") + ";").replace("/* TYPES */", types)
    fixture = fixture.replace("/* ACTUAL */", actual).replace("/* HANDLE */", block(draw, "struct HandleHit {") + ";")
    fixture = fixture.replace("/* DRAW */", block(draw, "VkImageView texture_view_of("))
    env = dict(os.environ)
    env.pop("RX_VK_COUNT_BY_ESTIMATE", None)   # (the count by allocation, the default: what the fixture's numbers are)
    env.pop("RX_VK_BC_LAYERS", None)           # (one layer a run, the default: what the fixture counts)
    env.pop("RX_VK_LOG_HITS", None)
    with tempfile.TemporaryDirectory(prefix="astc-bc1-remake-") as temp:
        cpp, exe = Path(temp) / "fixture.cpp", Path(temp) / "fixture.exe"
        cpp.write_text(fixture, encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++20", "-include", ENV_ON_H, "-O1", "-Wall", "-Wextra", "-Werror",
                        "-Wno-missing-field-initializers", "-I" + str(a.headers), "-I" + str(a.source / "gpu" / "vk"),
                        *(["-fsanitize=address,undefined", "-fno-omit-frame-pointer"] if a.sanitize else []),
                        str(cpp), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True, timeout=120, env=env)


if __name__ == "__main__":
    main()
