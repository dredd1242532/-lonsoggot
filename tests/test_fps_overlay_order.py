"""The overlays (start-up, menu, FPS, co-op names and chat) share one Dear ImGui frame and recording.
Start-up stays under the menu, the counter over both, and independently enabled names/chat show only in play.
Nothing draws while none shows (except one glyph bake after a resize), and no "0 FPS" shows over start-up.

python -I -B tests/test_fps_overlay_order.py [--cxx g++] [--source path/to/vk_menu.cpp]
Executes the real menu_record with recording stubs; no GPU or game data.
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default="g++")
    p.add_argument("--source", type=Path, default=ROOT / "gpu/vk/vk_menu.cpp")
    a = p.parse_args()
    menu_source = a.source.read_text(encoding="utf-8")
    fn = block(menu_source, "void menu_record(") + "\n" + block(menu_source, "void menu_record_image(")
    fixture = r'''#include "vk_api.h"
#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); std::exit(1); } } while (0)
using VkCommandBuffer = int;
using VkImage = int;
using VkImageView = int;
struct { bool show_fps = false; int coop_labels = 1; } opts;
std::atomic<bool> open_now{false}, startup_on{false};
std::string calls;
bool can_draw = true;
bool names_ready = false, chat_visible = false;
int labels_seen = -1;
bool coop_overlay_read(MkwVkCoopOverlay& overlay, int labels) {
    labels_seen = labels;
    overlay.nmarks = names_ready && labels ? 1 : 0;
    return overlay.nmarks != 0;
}
bool chat_drawn() { return chat_visible; }
float ease_step = 0;
int64_t prof_now() { return 1000; }
namespace ui {
struct Ctx {};
bool begin(uint32_t, uint32_t) { calls += "b"; return can_draw; }
void idle() { calls += "i"; }
void record(VkCommandBuffer cmd, uint32_t index, uint32_t w, uint32_t h) { CHECK(cmd == 1 && index == 2 && w == 640 && h == 360); calls += "r"; }
void record_to(VkCommandBuffer cmd, VkImage image, VkImageView view, uint32_t w, uint32_t h) {
    CHECK(cmd == 1 && image == 3 && view == 4 && w == 640 && h == 360); calls += "t";
}
}
namespace mkwvk::rec {   // (RX_VK_RECORDER: the boundary before each Dear ImGui recording, logged as "d")
enum Bound { B_DIRECT };
void boundary(Bound) { calls += "d"; }
}
ui::Ctx ui_ctx(uint32_t, uint32_t) { return {}; }
void prebake(const ui::Ctx&) { calls += "p"; }
bool resized = false;
bool prebake_needed(uint32_t, uint32_t) { return resized; }
void draw_startup(ui::Ctx&) { calls += "s"; }
void draw_menu(ui::Ctx&) { calls += "m"; }
void draw_fps(ui::Ctx&, bool under_banner) { calls += under_banner ? "F" : "f"; }
void coop_overlay_draw(ui::Ctx&, const MkwVkCoopOverlay& overlay) { CHECK(overlay.nmarks == 1); calls += "n"; }
void draw_chat(ui::Ctx&) { calls += "c"; }
''' + fn + r'''
int main() {
    for (bool startup : {false, true}) for (bool fps : {false, true}) for (bool menu : {false, true}) for (bool can : {true, false}) {
        opts.show_fps = fps;
        open_now = menu;
        startup_on = startup;
        can_draw = can;
        calls.clear();
        menu_record(1, 2, 640, 360);
        if (!startup && !fps && !menu) {   // (nothing shows: no ImGui frame at all, only the reaping; after a resize, one bake)
            CHECK(calls == "i");
            calls.clear(), resized = true;
            menu_record(1, 2, 640, 360);
            CHECK(calls == (can ? "bpdr" : "bi"));
            resized = false;
            continue;
        }
        if (!can) { CHECK(calls == "b"); continue; }                           // (no ImGui here: nothing recorded)
        std::string want = "bp";
        if (startup) want += "s";
        if (menu) want += "m";
        if (fps && !startup) want += menu ? "F" : "f";
        want += "dr";   // (the recorder boundary right before the recording)
        CHECK(calls == want);
    }
    // Names and chat are independent overlays, including when neither FPS nor the menu is on.
    for (bool startup : {false, true}) for (bool menu : {false, true}) for (bool fps : {false, true})
        for (bool names : {false, true}) for (bool chat : {false, true}) for (int labels : {0, 1, 2})
            for (bool can : {false, true}) {
                startup_on = startup, open_now = menu, opts.show_fps = fps, opts.coop_labels = labels;
                names_ready = names, chat_visible = chat, can_draw = can;
                const bool show_names = !startup && !menu && names && labels;
                const bool show_chat = !startup && !menu && chat;
                std::string drawn;
                if (show_names) drawn += "n";
                if (menu) drawn += "m";
                if (fps && !startup) drawn += menu ? "F" : "f";
                if (show_chat) drawn += "c";
                calls.clear();
                menu_record(1, 2, 640, 360);
                CHECK(labels_seen == (startup || menu ? 0 : labels));
                const std::string frame = (startup ? "s" : "") + drawn;
                CHECK(calls == (frame.empty() ? "i" : can ? "bp" + frame + "dr" : "b"));
                calls.clear();
                menu_record_image(1, 3, 4, 640, 360);
                CHECK(labels_seen == (startup || menu ? 0 : labels));
                CHECK(calls == (drawn.empty() ? "" : can ? "b" + drawn + "dt" : "b"));
            }
    std::printf("PASS: one ImGui frame and recording for whatever shows, in order, nothing while nothing shows\n");
}
'''
    with tempfile.TemporaryDirectory(prefix="fps-overlay-") as tmp:
        cpp = Path(tmp) / "fixture.cpp"
        exe = Path(tmp) / "fixture"
        cpp.write_text(fixture, encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-I" + str(a.source.parent),
                        str(cpp), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
