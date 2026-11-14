"""The render targets' image pool never holds more than its bound and is inside the textures' budget.

The pool took every dead target's image and was trimmed to its bound afterwards, 16 a present: a sweep's 300-700
targets waited in it for presents, GBs that no budget counted, and whether VRAM was short was looked at every tenth
present only. Now an image that would take the pool past its bound (RX_VK_TARGET_POOL_MB), or any while VRAM is short,
is refused and destroyed as before the pool, and what the pool holds comes off the textures' budget.

This runs vk_texture.cpp's own pool, and texture_frame from its look at the driver's count to where its budget's rules
begin, with stand-ins for Vulkan (tests/test_target_pool_cap.cpp):
- a put past the bound is refused and left whole; over thousands of puts, takes and trims the pool never passes it;
- the pool's bytes come off the budget, and are back in it when the images are taken or destroyed;
- while VRAM is short nothing is pooled and the trim gives all of it back; otherwise the trim is the 120 presents' alone;
- the pool goes by a look at the driver's count before the dead list's images are destroyed, each present; the budget's
  rules, each tenth, by a look after that and the trim: what was just freed is not held any more, and counted as held
  it had live textures let go for it.

python -I -B tests/test_target_pool_cap.py [--cxx g++]
"""
import argparse
import os
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


def check_frame(frame):
    """What the fixture cannot run: where texture_frame calls the code it does run"""
    drain = frame.index("for (auto it = dead.begin(); it != dead.end();) {")
    pressed = frame.index("pool_pressed = known && pressure > allowed / 10 * 9;")
    tenth = frame.index("if (frame_no % 10) return;")
    if not pressed < drain < frame.index("pool_trim();") < tenth:
        raise SystemExit("texture_frame: whether VRAM is short is no longer worked out each present, before the dead "
                         "list's offers to the pool and the pool's trim")
    loop = block(frame, "for (auto it = dead.begin(); it != dead.end();) {")
    offer = loop.index("if (it->pool_key && pool_offer(*it)) {")
    views = loop.index("for (auto& kv : it->view_map)")
    if not offer < views < loop.index("destroy_image(it->image, it->allocation);"):
        raise SystemExit("texture_frame: an image the pool refused is no longer destroyed there, its views with it")
    if not frame.index("budget = budget_less_pool(budget);") < frame.index("const uint64_t size_budget = budget;"):
        raise SystemExit("texture_frame: what the pool holds no longer comes off the textures' budget as a size")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default="g++")
    a = p.parse_args()
    tex = (ROOT / "gpu/vk/vk_texture.cpp").read_text(encoding="utf-8")
    frame = block(tex, "void texture_frame(uint64_t completed)")
    check_frame(frame)
    start = tex.index("struct Pooled {")
    end = tex.index("bool pool_pressed;", start) + len("bool pool_pressed;")
    pool = "\n".join([tex[start:end]] + [block(tex, m) for m in (
        "uint64_t pool_cap()", "bool pool_take(", "void pool_destroy(", "bool pool_offer(", "uint64_t budget_less_pool(",
        "size_t pool_trim()")])
    # (texture_frame's looks, its dead list and the trim, up to where the budget's rules begin)
    at = frame.index("uint64_t used = 0, allowed = 0, block_free = 0;")
    looks = frame[at:frame.index("if (!own)", at)]
    fixture = (ROOT / "tests/test_target_pool_cap.cpp").read_text(encoding="utf-8")
    fixture = (fixture.replace("/* DEAD */", block(tex, "struct Dead {") + ";").replace("/* POOL */", pool)
               .replace("/* FRAME */", looks))
    with tempfile.TemporaryDirectory(prefix="target-pool-cap-") as temp:
        cpp, exe = Path(temp) / "fixture.cpp", Path(temp) / "fixture.exe"
        cpp.write_text(fixture, encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror", "-Wno-missing-field-initializers",
                        *([] if os.name == "nt" else ["-pthread"]), "-I" + str(ROOT / "gpu/vk"),
                        str(cpp), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True, env=dict(os.environ, RX_VK_TARGET_POOL_MB="64"))


if __name__ == "__main__":
    main()
