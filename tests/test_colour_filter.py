"""Correction keeps grey neutral and Off exact; only an actual PQ picture bypasses it."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]
ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default="clang++")
    a = p.parse_args()
    header = (ROOT / "gpu/vk/vk_menu.h").read_text(encoding="utf-8")
    menu = (ROOT / "gpu/vk/vk_menu.cpp").read_text(encoding="utf-8")
    frame = (ROOT / "gpu/vk/vk_framegen.cpp").read_text(encoding="utf-8")
    shader = (ROOT / "gpu/vk/shaders/fg_scale.comp").read_text(encoding="utf-8")
    replay = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
    assert "int colour_filter = 0;" in header
    assert '{"Off", "Protanopia", "Deuteranopia", "Tritanopia"}' in menu
    assert '{TAB_GRAPHICS, "Colour filter", &opts.colour_filter, 0, 3, 1, tx_colour_filter,' in menu
    assert "&& !colour) return VK_NULL_HANDLE;" in frame
    assert "static_assert(sizeof pc == 56)" in frame
    assert "float colour[9]; } pc;" in shader
    assert "vec4(accessible(c), 1.0)" in shader
    assert shader.index("c = sharpen(") < shader.index("vec4(accessible(c), 1.0)")

    # Compile the actual gates and shader identity predicate, not copies of their logic.
    colour = re.search(r"const int colour = ([^;]+);", frame).group(1)
    view = replay.split("direct_view = texture_view(", 1)[0].rsplit("if (tex && !dev.offscreen && (", 1)[1]
    view = view.split(")) {", 1)[0]
    identity = re.search(r"if \((.*?)\) return c;", shader.split("vec3 accessible(vec3 c)", 1)[1], re.S).group(1)
    code = r'''
#include "gpu/vk/colour_filter.h"
#include <cassert>
#include <cmath>
struct { int hdr, colour_filter, upscale, sharpness; } opts{};
struct { bool hdr; } dev{};
struct { float colour[9]; } pc{};
bool wide_sides_possible() { return false; }
int correction(bool pq) { (void)pq; return COLOUR; }
bool sampled_view() { return VIEW; }
bool identity() { return IDENTITY; }
int main() {
    for (int hdr = 0; hdr <= 2; ++hdr) {
        opts.hdr = hdr;
        for (int display = 0; display <= 1; ++display) {
            dev.hdr = display != 0;
            const bool pq = dev.hdr && hdr == 2;
            for (int mode = 0; mode <= 3; ++mode) {
                opts.colour_filter = mode;
                assert(correction(pq) == (pq ? 0 : mode));
                // Correction alone at 1:1 needs a sampled view, even with Native selected on SDR.
                assert(sampled_view() == (dev.hdr || mode != 0));
                float expected[9], actual[9];
                colour_filter_matrix(pq ? 0 : mode, expected);
                colour_filter_matrix(correction(pq), actual);
                for (int i = 0; i < 9; ++i) assert(actual[i] == expected[i]);
            }
        }
    }
    colour_filter_matrix(0, pc.colour);
    assert(identity());
    // A unit diagonal alone is not identity. Exercise every entry independently.
    for (int i = 0; i < 9; ++i) {
        pc.colour[i] += 0.25f;
        assert(!identity());
        pc.colour[i] -= 0.25f;
    }
    for (int mode = -1; mode <= 4; ++mode) {
        float m[9];
        colour_filter_matrix(mode, m);
        for (int r = 0; r < 3; ++r) {
            for (int v = 0; v <= 100; ++v) {
                float grey = v / 100.0f;
                assert(std::fabs(grey * (m[r * 3] + m[r * 3 + 1] + m[r * 3 + 2]) - grey) < 1e-6f);
            }
            if (mode < 1 || mode > 3)
                for (int c = 0; c < 3; ++c) assert(m[r * 3 + c] == (r == c ? 1 : 0));
        }
        // Correction adds missing information to other channels rather than simulating loss.
        if (mode == 1 || mode == 2) assert(m[0] == 1 && m[1] == 0 && m[2] == 0 && m[3] > 0);
        if (mode == 3) assert(m[8] == 1 && m[2] > 0 && m[5] > 0);
    }
}
'''.replace("COLOUR", colour).replace("VIEW", view).replace("IDENTITY", identity)
    with tempfile.TemporaryDirectory(prefix="colour-filter-") as d:
        cpp = Path(d) / "colour.cpp"
        cpp.write_text(code, encoding="utf-8")
        exe = Path(d) / "colour"
        subprocess.run([a.cxx, "-Wall", "-Wextra", "-Werror", "-I" + str(ROOT), str(cpp), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)
    print("PASS: correction, grey axis, exact Off, all-nine identity, SDR/PQ and sampled-view gates")


if __name__ == "__main__":
    main()
