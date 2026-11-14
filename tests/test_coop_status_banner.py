"""Render the actual Co-op header: persistent Experimental label and separate save/refusal notes.

No GPU: text calls and box bounds are captured at three UI scales. The label uses
the real column/scale functions with a one-em-per-letter width bound at 640x360 and
1280x720, all menu sizes and room states. This is layout evidence, not a game-font
or pixel test. --source-root exercises the same regression against an old checkout.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]
ROOT = Path(__file__).resolve().parents[1]

FIXTURE = r'''#define _CRT_SECURE_NO_WARNINGS
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <cstdint>
@@API@@
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); std::exit(1); } } while (0)
struct ImVec2 { float x, y; ImVec2(float a, float b) : x(a), y(b) {} };
struct Draw {
    float clip_top = 0;
    void PushClipRect(ImVec2 start, ImVec2, bool) { clip_top = start.y; }
};
using ImFont = int;
struct Text { float y, x, px, clip_top; std::string text; } texts[12];
static int count;
static float box_bottom;
namespace ui {
enum { kSoft, kGoldHi, kInk };
struct Ctx {
    float s;
    Draw* d;
    struct { ImFont *body = nullptr, *strong = nullptr; } f;
    float w = 1280, h = 720, safe = 0;
};
@@UNIT@@
void box(Ctx&, ImVec2, ImVec2 end) { box_bottom = end.y; }
void text_mid(Ctx& c, ImFont*, float px, float x, float y, int, const char* text) {
    CHECK(count < 12); texts[count++] = {y, x, px, c.d->clip_top, text};
}
void text_mid_right(Ctx& c, ImFont* f, float size, float x, float y, int color, const char* text) {
    text_mid(c, f, size, x, y, color, text);
}
}
@@BANNER@@
@@COLUMNS@@
static std::string fit(ui::Ctx&, ImFont*, float, float, const char* text, bool) { return text; }
static MkwVkCoopView coop_now{};
static const char* coop_accept_notice;
static bool coop_in_room(const MkwVkCoopView& v) { return v.state == HOS_COOP_STATE_JOINED; }
static void draw_header(ui::Ctx& c, float cx0, float cx1, float top, float bottom) {
@@HEADER@@
    (void)pitch; (void)rh; (void)split;
}
static float text_y(const char* text) {
    for (int i = 0; i < count; i++) if (texts[i].text == text) return texts[i].y;
    return -1;
}
static void check_experimental() {
    Draw d;
    for (const ImVec2 size : {ImVec2(640, 360), ImVec2(1280, 720)}) for (int menu_size : {1, 2, 3}) {
        ui::Ctx c{1, &d, {}};
        c.w = size.x, c.h = size.y;
        c.s = ui::unit(c.h, menu_size, &c.safe);
        float cx0, cx1, hx0, hx1;
        bool narrow;
        columns(c, &cx0, &cx1, &hx0, &hx1, &narrow);
        const float banner = banner_h(c), top = banner + 24 * c.s;
        for (int state = -1; state <= HOS_COOP_STATE_RECONNECTING + 1; state++)
            for (bool titled : {false, true}) for (int visit = 0; visit < 2; visit++) {
                coop_now = MkwVkCoopView{};
                coop_now.state = state;
                coop_now.n = HOS_COOP_MEMBERS, coop_now.ping_ms = 9999;
                if (titled) {
                    std::memset(coop_now.room_title, 'W', sizeof coop_now.room_title - 1);
                    coop_now.temporary_save = 1, coop_now.duel_diagnostic = 1;
                    std::snprintf(coop_now.save_note, sizeof coop_now.save_note, "Temporary save");
                }
                d.clip_top = 0, count = 0;
                draw_header(c, cx0, cx1, top, c.h);
                CHECK(count > 1 && texts[0].text == "Experimental");
                const Text& label = texts[0];
                CHECK(label.px == 16 * c.s);
                CHECK(label.y - label.px * 0.5f >= banner);
                CHECK(label.y + label.px * 0.5f <= top);
                CHECK(label.y - label.px * 0.5f >= label.clip_top);
                CHECK(label.x >= cx0 + 44 * c.s);
                // A deliberately broad font stand-in: every ASCII letter one em wide.
                CHECK(label.x + label.text.size() * label.px <= cx1 - 64 * c.s);
                CHECK(texts[1].y == top + 22 * c.s);
                CHECK(box_bottom == top + (titled ? 166 : 70) * c.s);
            }
    }
    std::puts("PASS: Experimental persists across visits and all room states; fits the existing gap at 640x360 and 1280x720, Small/Medium/Large (one-em font bound)");
}
int main() {
    check_experimental();
    const char* refusal = "A room with this secret is already open";
    const char* note = "Playing a temporary copy; normal saves are unchanged";
    Draw d;
    for (float scale : {0.75f, 1.0f, 1.5f}) {
        ui::Ctx c{scale, &d, {}};
        for (bool temporary : {false, true}) for (bool diagnostic : {false, true}) {
            coop_now = MkwVkCoopView{};
            coop_now.state = HOS_COOP_STATE_LEFT;
            coop_now.temporary_save = temporary;
            coop_now.duel_diagnostic = diagnostic;
            std::snprintf(coop_now.duel_terms, sizeof coop_now.duel_terms, "Diagnostic terms");
            std::snprintf(coop_now.duel_score, sizeof coop_now.duel_score, "Diagnostic score");
            std::snprintf(coop_now.duel_note, sizeof coop_now.duel_note, "No native fight");
            std::snprintf(coop_now.status, sizeof coop_now.status, "%s", refusal);
            if (temporary) std::snprintf(coop_now.save_note, sizeof coop_now.save_note, "%s", note);
            count = 0; draw_header(c, 0, 1000 * scale, 100 * scale, 700 * scale);
            CHECK(text_y(refusal) >= 0);
            if (temporary) {
                CHECK(text_y(note) - text_y(refusal) >= 24 * scale);
                CHECK(box_bottom - text_y(note) >= 16 * scale);
            } else CHECK(text_y(note) == -1);
            CHECK(box_bottom - text_y(refusal) >= 16 * scale);
            if (diagnostic) {
                CHECK(text_y("Diagnostic terms") >= 0 && text_y("Diagnostic score") >= 0 && text_y("No native fight") >= 0);
                for (int i = 1; i < count; i++) CHECK(texts[i].y - texts[i - 1].y >= 24 * scale);
                CHECK(box_bottom - texts[count - 1].y >= 16 * scale);
            }
            coop_now.status[0] = 0;
            count = 0; draw_header(c, 0, 1000 * scale, 100 * scale, 700 * scale);
            CHECK(text_y("Make a room or join one.") >= 0);
            CHECK(!temporary || text_y(note) >= 0);
            coop_accept_notice = "Review changed members before accepting";
            count = 0; draw_header(c, 0, 1000 * scale, 100 * scale, 700 * scale);
            CHECK(text_y(coop_accept_notice) >= 0);
            CHECK(!temporary || text_y(note) - text_y(coop_accept_notice) >= 24 * scale);
            coop_accept_notice = nullptr;
        }
    }
    std::puts("PASS: refusal and temporary banner both rendered, separate and inside the box; normal fallback and consent notice retained");
}
'''


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    parser.add_argument("--source-root", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.source_root.resolve()
    menu = (root / "gpu/vk/vk_menu.cpp").read_text(encoding="utf-8")
    api = (root / "gpu/vk/vk_api.h").read_text(encoding="utf-8")
    ui = (root / "gpu/vk/vk_ui.h").read_text(encoding="utf-8")
    api = api[api.index("enum { HOS_COOP_STATE_OFF"):api.index("/* what the executable gives")]
    start = menu.index("{", menu.index("void draw_coop_rows(")) + 1
    header = menu[start:menu.index("    const int count =", start)]
    with tempfile.TemporaryDirectory(prefix="coop-status-") as folder:
        folder = Path(folder)
        source, binary = folder / "fixture.cpp", folder / "fixture"
        fixture = FIXTURE.replace("@@HEADER@@", header).replace("@@API@@", api)
        fixture = fixture.replace("@@UNIT@@", block(ui, "inline float unit("))
        fixture = fixture.replace("@@BANNER@@", block(menu, "float banner_h("))
        fixture = fixture.replace("@@COLUMNS@@", block(menu, "void columns("))
        source.write_text(fixture, encoding="utf-8")
        subprocess.run([args.cxx, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I" + str(root),
                        str(source), "-o", str(binary)], check=True)
        print("COMPILED banner fixture; executing rendering assertions", flush=True)
        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    main()
