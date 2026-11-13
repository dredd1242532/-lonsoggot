"""Compile the production overlay renderer with a recording draw-list, no Vulkan device.
Checks callback-off, stale camera refusal, actual glyph/triangle bounds, and eight bounded marks.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
RUN_ALL_ARGS=['--cxx','{cxx}']
FIXTURE=r'''
#define _CRT_SECURE_NO_WARNINGS
#include <algorithm>
#include <atomic>
#include <cassert>
#define _Static_assert static_assert
#include <cmath>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <cstdio>
#include "vk_api.h"
#include "dlss_math.h"
#include "coop_overlay_math.h"
struct ImVec2 {
    float x,y; ImVec2(float a=0,float b=0):x(a),y(b){}
    ImVec2 operator+(ImVec2 b) const { return {x+b.x,y+b.y}; }
    ImVec2 operator-(ImVec2 b) const { return {x-b.x,y-b.y}; }
};
struct ImFont {};
struct Draw {
    int texts=0,triangles=0,clips=0;
    ImVec2 lo,hi;
    void point(ImVec2 p) { assert(std::isfinite(p.x)&&std::isfinite(p.y)&&p.x>=lo.x&&p.x<=hi.x&&p.y>=lo.y&&p.y<=hi.y); }
    void PushClipRect(ImVec2 a,ImVec2 b,bool) { lo=a;hi=b;clips++; }
    void PopClipRect() { clips--; }
    void AddRectFilled(ImVec2 a,ImVec2 b,unsigned,float) { point(a);point(b); }
    void AddText(ImFont*,float px,ImVec2 at,unsigned,const char* text) { texts++;point(at);point(at+ImVec2(float(strlen(text))*px*.5f,px)); }
    void AddTriangleFilled(ImVec2 a,ImVec2 b,ImVec2 c,unsigned) { triangles++;point(a);point(b);point(c); }
};
namespace mkwvk {
static const MkwVkHost* host;
static uint64_t frame=1;
uint64_t texture_frame_no() { return frame; }
namespace ui {
constexpr unsigned kBox=0,kInkBright=1,kGoldHi=2;
struct Ctx { float s=1,w=1280,h=720; struct { ImFont* body=nullptr; } f; Draw* d; };
ImVec2 size_of(ImFont*,float px,const char* text) { return {float(strlen(text))*px*.5f,px}; }
}
}
@@SOURCE@@
static int reads;
static int snapshot(MkwVkCoopOverlay* out,int labels) {
    assert(labels==1||labels==2); reads++; out->nmarks=1;
    out->mark[0]={{0,0,-2},HOS_COOP_MARK_NAME|HOS_COOP_MARK_ARROW,"Literal %s name"}; return 1;
}
int main() {
    using namespace mkwvk;
    MkwVkHost h{};h.coop_overlay=snapshot;host=&h;
    MkwVkCoopOverlay v{};
    assert(!coop_overlay_read(v,0)&&reads==0&&!coop_overlay_wants_camera());
    assert(coop_overlay_read(v,2)&&reads==1&&coop_overlay_wants_camera());
    Draw draw;ui::Ctx c; c.d=&draw;
    coop_overlay_viewport(20,40,1280,720,0,0,1,1);
    coop_overlay_draw(c,v);assert(!draw.texts&&!draw.triangles);
    const float m[]={1,0,0,0,0,1,0,0,0,0,-1,-.2f,0,0,-1,0}, jitter[]={0,0};
    coop_overlay_camera(m,jitter,frame);
    coop_overlay_draw(c,v); assert(draw.texts==1&&!draw.triangles&&!draw.clips);
    v.mark[0].position[2]=2;
    coop_overlay_draw(c,v);assert(draw.texts==2&&draw.triangles==1&&!draw.clips);
    v.mark[0].flags=HOS_COOP_MARK_NAME;
    coop_overlay_draw(c,v);assert(draw.texts==2&&draw.triangles==1);
    v.mark[0].position[2]=-2;v.mark[0].flags=HOS_COOP_MARK_ARROW;
    coop_overlay_draw(c,v);assert(draw.texts==2);
    frame+=2;coop_overlay_draw(c,v);assert(draw.texts==2);
    coop_overlay_camera(m,jitter,frame);
    v.nmarks=HOS_COOP_MEMBERS+1;
    for(auto& mark:v.mark) mark={{20,20,-2},HOS_COOP_MARK_ARROW,"A long bounded friend"};
    coop_overlay_draw(c,v);assert(draw.texts==2+HOS_COOP_MEMBERS&&draw.triangles==1+HOS_COOP_MEMBERS&&!draw.clips);
    puts("PASS HUD renderer: off/no callback, fresh submitted camera, literal names, front/behind visibility, bounded text/triangles and member count");
}
'''

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--cxx',default='g++');a=p.parse_args()
    source=(ROOT/'gpu/vk/vk_coop_overlay.cpp').read_text();source=source[source.index('namespace mkwvk {'):]
    with tempfile.TemporaryDirectory(prefix='coop-hud-renderer-') as tmp:
        d=Path(tmp);(d/'test.cpp').write_text(FIXTURE.replace('@@SOURCE@@',source))
        subprocess.run([a.cxx,'-std=c++17','-Wall','-Wextra','-Werror','-I'+str(ROOT/'gpu/vk'),str(d/'test.cpp'),'-o',str(d/'test')],check=True)
        subprocess.run([str(d/'test')],check=True)

if __name__=='__main__':main()
