"""Render the actual Co-op header: a save-copy note cannot hide a refusal.

No GPU: text calls and box bounds are captured at three UI scales. --source-root
allows the same regression to exercise the old rendering expression.
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
struct Draw { void PushClipRect(ImVec2, ImVec2, bool) {} };
using ImFont = int;
struct Text { float y; std::string text; } texts[12];
static int count;
static float box_bottom;
namespace ui {
enum { kSoft, kGoldHi, kInk };
struct Ctx { float s; Draw* d; struct { ImFont *body = nullptr, *strong = nullptr; } f; };
void box(Ctx&, ImVec2, ImVec2 end) { box_bottom = end.y; }
void text_mid(Ctx&, ImFont*, float, float, float y, int, const char* text) {
    CHECK(count < 12); texts[count++] = {y, text};
}
void text_mid_right(Ctx& c, ImFont* f, float size, float x, float y, int color, const char* text) {
    text_mid(c, f, size, x, y, color, text);
}
}
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
int main() {
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    parser.add_argument("--source-root", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.source_root.resolve()
    menu = (root / "gpu/vk/vk_menu.cpp").read_text(encoding="utf-8")
    api = (root / "gpu/vk/vk_api.h").read_text(encoding="utf-8")
    api = api[api.index("enum { HOS_COOP_STATE_OFF"):api.index("/* what the executable gives")]
    start = menu.index("{", menu.index("void draw_coop_rows(")) + 1
    header = menu[start:menu.index("    const int count =", start)]
    with tempfile.TemporaryDirectory(prefix="coop-status-") as folder:
        folder = Path(folder)
        source, binary = folder / "fixture.cpp", folder / "fixture"
        source.write_text(FIXTURE.replace("@@HEADER@@", header).replace("@@API@@", api), encoding="utf-8")
        subprocess.run([args.cxx, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I" + str(root),
                        str(source), "-o", str(binary)], check=True)
        print("COMPILED banner fixture; executing rendering assertions", flush=True)
        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    main()
