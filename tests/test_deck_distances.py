"""Draw-distance caps, override persistence and Deck wiring without game/GPU."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUN_ALL_ARGS = ["--cxx", "{cxx}"]


def definition(text, marker):
    start = text.index(marker)
    cursor = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[cursor] == "{") - (text[cursor] == "}")
        cursor += 1
    return text[start:cursor]


class Wiring(unittest.TestCase):
    def test_env_and_saved_precedence(self):
        menu = (ROOT / "gpu/vk/vk_menu.cpp").read_text(encoding="utf-8")
        auto = definition(menu, "void apply_auto(bool keep_own) {")
        init = definition(menu, "void gfx_env_init() {")
        names = ("FOLIAGE", "OBJECTS", "GRASS", "SHADOWS", "CONTACT_SHADOWS", "BASE_QUALITY")
        aliases = ("FOLIAGE_DISTANCE", "OBJECT_DETAIL", "GRASS_DENSITY", "SHADOW_DISTANCE")
        fields = ("foliage", "objects", "grass", "shadows", "contact_shadows", "base_quality")
        choices = ("foliage_distance", "object_detail", "grass_density", "shadow_distance", "contact_shadows", "base_quality")
        for i, (name, field) in enumerate(zip(names, fields)):
            expected = (f'!gfx_env("RX_OOT_{name}", "RX_OOT_{aliases[i]}")' if i < 4
                        else f'!getenv("RX_OOT_{name}")')
            self.assertIn(expected, auto)
            self.assertIn("if (deck.apply && c." + choices[i] + " >= 0", auto)
            self.assertIn(f"(!keep_own || !(gfx_saved_mask & {1 << i}u))", auto)
            self.assertIn("RX_OOT_" + name, init)
            self.assertIn("env_override(&opts.gfx_" + field, init)
        self.assertIn("gfx_saved_mask |= gfx_bit(r.value);", menu)
        self.assertIn("(!hw.deck || gfx_saved_mask == 63)", menu)
        for field in fields[:4]:
            self.assertIn("&opts.gfx_" + field, definition(menu, "bool is_slider("))
            self.assertIn("&opts.gfx_" + field, definition(menu, "bool stops_at_ends("))

    def test_option_ids_avoid_existing_reservations(self):
        api = (ROOT / "gpu/vk/vk_api.h").read_text(encoding="utf-8")
        self.assertIn("HOS_OPT_FPS_CAP = 53", api)
        self.assertIn("HOS_OPT_DAMAGE_TAKEN = 55", api)
        self.assertIn("HOS_OPT_GFX_FOLIAGE = 63", api)
        self.assertIn("HOS_OPT_GFX_UNCAPPED = 69", api)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(Wiring))
    if not result.wasSuccessful():
        raise SystemExit(1)
    menu = (ROOT / "gpu/vk/vk_menu.cpp").read_text(encoding="utf-8")
    cap_line = re.search(r"constexpr int kCapFoliage[^\n]+", menu).group(0)
    source = r'''
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "gpu/vk/auto_settings.h"
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL: %d %s\n", __LINE__, #x); std::exit(1); } } while (0)
struct Options { int gfx_foliage, gfx_objects, gfx_grass, gfx_shadows, gfx_uncapped, gfx_contact_shadows, gfx_base_quality; } opts;
struct Row { int* value; int hi; };
Row rows[] = {{&opts.gfx_foliage, 400}, {&opts.gfx_objects, 400},
              {&opts.gfx_grass, 300}, {&opts.gfx_shadows, 400}};
struct EnvOverride { int* value; int saved, forced; };
EnvOverride env_overrides[32];
int env_override_count = 0;
bool env_value_off(const char* e) { return !*e || !std::strcmp(e, "0"); }
void set_env(const char* key, const char* value) {
#ifdef _WIN32
    CHECK(_putenv_s(key, value ? value : "") == 0);
#else
    CHECK((value ? setenv(key, value, 1) : unsetenv(key)) == 0);
#endif
}
'''
    source += cap_line + "\n"
    for marker in ("void gfx_limits() {", "void env_override(int* value, int v) {",
                   "int saved_value(int* value) {", "const char* gfx_env(",
                   "void gfx_env_init() {"):
        source += definition(menu, marker) + "\n"
    auto = definition(menu, "void apply_auto(bool keep_own) {")
    fields = ("foliage", "objects", "grass", "shadows", "contact_shadows", "base_quality")
    assignments = []
    for field in fields:
        lines = [line for line in auto.splitlines() if
                 re.search(r"\bopts\.gfx_" + field + r"\s*=\s*c\.", line)]
        if len(lines) != 1:
            raise SystemExit("expected one production default assignment for " + field)
        assignments.append(lines[0])
    source += "static unsigned gfx_saved_mask = 0;\n"
    source += "void apply_draw_defaults(bool apply, bool keep_own) {\n"
    source += "    const struct { bool apply; } deck = {apply};\n"
    source += "    auto_settings::Hardware hardware; hardware.deck = true;\n"
    source += "    const auto_settings::Choice c = auto_settings::choose(hardware);\n"
    source += "\n".join(assignments) + "\n}\n"
    source += r'''
void reset() {
    const char* keys[] = {"RX_OOT_FOLIAGE", "RX_OOT_OBJECTS", "RX_OOT_GRASS", "RX_OOT_SHADOWS",
        "RX_OOT_FOLIAGE_DISTANCE", "RX_OOT_OBJECT_DETAIL", "RX_OOT_GRASS_DENSITY", "RX_OOT_SHADOW_DISTANCE",
        "RX_OOT_CONTACT_SHADOWS", "RX_OOT_BASE_QUALITY", "RX_OOT_UNCAP_DETAIL_SLIDERS"};
    for (const char* key : keys) set_env(key, nullptr);
    env_override_count = 0;
    gfx_saved_mask = 0;
}
void check_distances(const Options& want) {
    CHECK(opts.gfx_foliage == want.gfx_foliage && opts.gfx_objects == want.gfx_objects &&
          opts.gfx_grass == want.gfx_grass && opts.gfx_shadows == want.gfx_shadows &&
          opts.gfx_contact_shadows == want.gfx_contact_shadows && opts.gfx_base_quality == want.gfx_base_quality);
}
int main() {
    reset();
    opts = {400, 400, 300, 400, 0, 1, 0};
    gfx_limits();
    CHECK(opts.gfx_foliage == 150 && opts.gfx_objects == 250 &&
          opts.gfx_grass == 200 && opts.gfx_shadows == 200);
    CHECK(rows[0].hi == 150 && rows[1].hi == 250 && rows[2].hi == 200 && rows[3].hi == 200);
    opts = {400, 400, 300, 400, 1, 1, 0};
    gfx_limits();
    CHECK(opts.gfx_foliage == 400 && opts.gfx_objects == 400 &&
          opts.gfx_grass == 300 && opts.gfx_shadows == 400);
    opts = {25, 100, 0, 25, 0, 0, 0};
    gfx_limits();
    CHECK(opts.gfx_foliage == 25 && opts.gfx_objects == 100 &&
          opts.gfx_grass == 0 && opts.gfx_shadows == 25);

    reset();
    opts = {75, 100, 100, 100, 0, 1, 0};
    set_env("RX_OOT_FOLIAGE", "400");
    gfx_env_init();
    CHECK(opts.gfx_foliage == 150 && saved_value(&opts.gfx_foliage) == 75);
    CHECK(env_overrides[0].forced == 150);
    set_env("RX_OOT_UNCAP_DETAIL_SLIDERS", "1");
    gfx_env_init();
    CHECK(opts.gfx_foliage == 400 && saved_value(&opts.gfx_foliage) == 75);

    reset();
    opts = {400, 400, 300, 400, 1, 1, 0};
    set_env("RX_OOT_UNCAP_DETAIL_SLIDERS", "0");
    gfx_env_init();
    CHECK(opts.gfx_foliage == 150 && saved_value(&opts.gfx_foliage) == 400);
    CHECK(opts.gfx_objects == 250 && saved_value(&opts.gfx_objects) == 400);
    CHECK(opts.gfx_grass == 200 && saved_value(&opts.gfx_grass) == 300);
    CHECK(opts.gfx_shadows == 200 && saved_value(&opts.gfx_shadows) == 400);
    CHECK(saved_value(&opts.gfx_uncapped) == 1);

    reset();
    opts = {75, 100, 100, 100, 0, 1, 0};
    set_env("RX_OOT_FOLIAGE_DISTANCE", "25");
    gfx_env_init();
    CHECK(opts.gfx_foliage == 25 && saved_value(&opts.gfx_foliage) == 75);
    set_env("RX_OOT_FOLIAGE", "100");
    gfx_env_init();
    CHECK(opts.gfx_foliage == 100 && saved_value(&opts.gfx_foliage) == 75);
    reset();
    /* Rechoosing Auto must not reapply a previously settled Deck profile. */
    const Options player = {75, 175, 50, 125, 0, 1, 1};
    const Options low = {25, 100, 0, 25, 0, 0, 0};
    reset(); opts = player;
    apply_draw_defaults(false, false); check_distances(player);
    apply_draw_defaults(false, true); check_distances(player);
    apply_draw_defaults(true, false); check_distances(low);

    /* Each saved-row bit is independent, and explicit selection ignores it. */
    for (unsigned bit = 0; bit < 6; ++bit) {
        reset(); opts = player; gfx_saved_mask = 1u << bit;
        Options want = low;
        switch (bit) {
        case 0: want.gfx_foliage = player.gfx_foliage; break;
        case 1: want.gfx_objects = player.gfx_objects; break;
        case 2: want.gfx_grass = player.gfx_grass; break;
        case 3: want.gfx_shadows = player.gfx_shadows; break;
        case 4: want.gfx_contact_shadows = player.gfx_contact_shadows; break;
        case 5: want.gfx_base_quality = player.gfx_base_quality; break;
        }
        apply_draw_defaults(true, true); check_distances(want);
        opts = player; apply_draw_defaults(true, false); check_distances(low);
    }
    reset(); opts = player; gfx_saved_mask = 63;
    apply_draw_defaults(true, true); check_distances(player);

    /* All canonical switches win even over an explicit profile selection. */
    reset(); opts = player;
    const char* canonical[] = {"RX_OOT_FOLIAGE", "RX_OOT_OBJECTS", "RX_OOT_GRASS",
        "RX_OOT_SHADOWS", "RX_OOT_CONTACT_SHADOWS", "RX_OOT_BASE_QUALITY"};
    for (const char* key : canonical) set_env(key, "1");
    apply_draw_defaults(true, false); check_distances(player);
    reset(); opts = player;
    const char* aliases[] = {"RX_OOT_FOLIAGE_DISTANCE", "RX_OOT_OBJECT_DETAIL",
        "RX_OOT_GRASS_DENSITY", "RX_OOT_SHADOW_DISTANCE",
        "RX_OOT_CONTACT_SHADOWS", "RX_OOT_BASE_QUALITY"};
    for (const char* key : aliases) set_env(key, "1");
    apply_draw_defaults(true, true); check_distances(player);
    reset();
    std::puts("PASS: distance caps, aliases, saved values and Deck rechoose");
}
'''
    with tempfile.TemporaryDirectory(prefix="deck-distances-") as temp:
        cpp = Path(temp) / "caps.cpp"
        exe = Path(temp) / "caps.exe"
        cpp.write_text(source, encoding="utf-8")
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT), str(cpp), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
