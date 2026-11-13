"""The co-op camera observes the default game's upscale without replacing or invalidating it.

Compile the production dispatch gate, GAME-mode entry and output-size publisher with authored textures;
no Vulkan device or game. --source-root selects the uncorrected source for the negative regression.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUN_ALL_ARGS = ['--cxx', '{cxx}']


def function(text, marker):
    start = text.index(marker)
    at = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[at] == '{') - (text[at] == '}')
        at += 1
    return text[start:at]


FIXTURE = r'''
#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <vector>
#include "dlss_checks.h"
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr,"FAIL:%d %s\n",__LINE__,#x); std::exit(1); } } while (0)
using VkCommandBuffer = int;
using VkImageView = int;
constexpr int VK_FORMAT_UNDEFINED = 0, VK_FORMAT_B10G11R11_UFLOAT_PACK32 = 1;
constexpr int VK_IMAGE_VIEW_TYPE_2D = 2, VK_NULL_HANDLE = 0;
namespace ViewUse { enum Value { Sample, Storage }; }
namespace ur { enum { GAME, OTHER }; }
int mode = ur::GAME;
bool off = false, labels = false;
int temporal_mode() { return mode; }
bool temporal_wanted() { return mode != ur::GAME; }
bool temporal_off() { return off; }
bool coop_overlay_wants_camera() { return labels; }
struct Texture { int format; uint32_t width, height; };
Texture input{VK_FORMAT_B10G11R11_UFLOAT_PACK32,1536,864}, output{VK_FORMAT_B10G11R11_UFLOAT_PACK32,2048,1152};
struct State { std::atomic<uint32_t> out_w{0}, out_h{0}; } state;
State& st() { return state; }
@@NOTE@@
int stretches = 0, lookups = 0;
bool stretch(VkCommandBuffer, uint64_t, bool, Texture*, VkImageView, Texture*, VkImageView, uint32_t) {
    ++stretches; return true;
}
@@UPSCALE@@
struct Desc { uint32_t count = 1, handle = 0; int format = 1; };
struct Info { bool uses_cooperative_matrix = true; std::vector<Desc> texture_descriptors, image_descriptors; };
struct Stage { Info info; };
struct Program { uint64_t key; std::vector<Stage> stages; };
struct NvnState {};
struct Image { VkImageView imageView = 1; };
struct Draw { std::vector<Image> imgs{{1},{2}}; } draw;
Draw& dw() { return draw; }
bool gameplay_network(uint64_t key) { return key == 42; }
uint32_t handle_of(const NvnState&, const Stage&, const Desc& d, int) { return d.handle; }
int image_format(int x) { return x; }
void texture_view_of(uint32_t handle, int, ViewUse::Value, int, Texture** out, uint32_t* level = nullptr) {
    ++lookups; *out = handle == 1 ? &input : &output; if (level) *level = 0;
}
bool last_dyn_valid = true, last_desc_valid = true, last_vb_valid = true;
int last_pipeline = 7;
struct { bool on = true; } vertex_set, last_ib;
@@GATE@@
int main() {
    Program p{42,{{{true,{{1,1,1}},{{1,2,1}}}}}};
    NvnState s;
    // Off: no texture lookup, no publication, no replacement or GPU state invalidation.
    CHECK(!temporal_stand_in(1,s,p));
    CHECK(lookups == 0 && state.out_w == 0 && stretches == 0 && last_pipeline == 7);
    // Default game upscaler + names: discover the known scene's output, keep the real dispatch.
    labels = true;
    CHECK(!temporal_stand_in(1,s,p));
    CHECK(state.out_w == 2048 && state.out_h == 1152);
    CHECK(mkwvk::dlss_checks::scene_size_fits(1536,864,state.out_w,state.out_h));
    CHECK(lookups == 2 && stretches == 0);
    CHECK(last_dyn_valid && last_desc_valid && last_vb_valid && last_pipeline == 7 && vertex_set.on && last_ib.on);
    // A non-gameplay network must not supply a guessed size in observation-only mode.
    p.key = 43; state.out_w = state.out_h = 0; lookups = 0;
    CHECK(!temporal_stand_in(1,s,p) && state.out_w == 0 && lookups == 0);
    // A missing/wrong-format output is not a camera-size source.
    CHECK(!temporal_upscale(1,42,true,&input,1,nullptr,2,0) && state.out_w == 0);
    output.format = 9;
    CHECK(!temporal_upscale(1,42,true,&input,1,&output,2,0) && state.out_w == 0);
    output.format = 1;
    // Existing explicit Upscaler Off remains a replacement and invalidates its touched GPU state.
    p.key = 42; off = true;
    CHECK(temporal_stand_in(1,s,p) && stretches == 1);
    CHECK(!last_dyn_valid && !last_desc_valid && !last_vb_valid && last_pipeline == 0 && !vertex_set.on && !last_ib.on);
    std::puts("PASS: default-mode name camera discovers size; overlays off cost no lookups; game dispatch/state untouched; Off still replaces");
}
'''


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--cxx', default='g++')
    ap.add_argument('--source-root', type=Path, default=ROOT)
    a = ap.parse_args()
    root = a.source_root.resolve()
    temporal = (root/'gpu/vk/vk_temporal.cpp').read_text()
    draw = (root/'gpu/vk/vk_draw.cpp').read_text()
    begin = temporal.index('bool temporal_upscale(')
    end = temporal.index('    State& s = st();', begin)
    # Only the GAME-mode entry is exercised here. Other engines are outside this regression's scope.
    entry = temporal[begin:end] + '    return false;\n}\n'
    source = FIXTURE.replace('@@NOTE@@',function(temporal,'void note_output('))
    source = source.replace('@@UPSCALE@@',entry).replace('@@GATE@@',function(draw,'bool temporal_stand_in('))
    with tempfile.TemporaryDirectory(prefix='coop-overlay-camera-') as d:
        p = Path(d); cpp,exe = p/'fixture.cpp',p/'fixture'
        cpp.write_text(source)
        subprocess.run([a.cxx,'-std=c++20','-Wall','-Wextra','-Werror','-I'+str(root/'gpu/vk'),str(cpp),'-o',str(exe)],check=True)
        subprocess.run([str(exe)],check=True)


if __name__ == '__main__':
    main()
