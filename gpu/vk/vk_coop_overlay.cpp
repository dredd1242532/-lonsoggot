// Reuses coop-players' submitted-camera overlay in the existing ImGui pass; no input or guest reads.
#include "vk_coop_overlay.h"
#include "vk_menu.h"
#include "vk_texture.h"
#include "vk_ui.h"
#include "coop_overlay_math.h"
#include "dlss_math.h"
#include <atomic>
#include <cstring>
#include <mutex>

namespace mkwvk {
namespace {
std::atomic<bool> wants_camera{false};
std::mutex camera_lock;
float scene_matrix[16]{};
uint64_t scene_frame=UINT64_MAX;
float destination[4]{}, source_crop[4]{0,0,1,1};
}
bool coop_overlay_read(MkwVkCoopOverlay& out, int labels) {
    out.nmarks=0;
    const bool any=labels>0 && host && host->coop_overlay && host->coop_overlay(&out,labels);
    wants_camera.store(any && out.nmarks>0,std::memory_order_relaxed);
    return any;
}
bool coop_overlay_wants_camera() { return wants_camera.load(std::memory_order_relaxed); }
void coop_overlay_camera(const float matrix[16], const float jitter[2], uint64_t frame) {
    if (!coop_overlay_wants_camera()) return;
    std::lock_guard lock(camera_lock);
    std::memcpy(scene_matrix,matrix,sizeof scene_matrix);
    dlss_math::unjitter(scene_matrix,jitter);
    scene_frame=frame;
}
void coop_overlay_viewport(float x,float y,float w,float h,float u,float v,float uw,float vh) {
    if (!coop_overlay_wants_camera()) return;
    destination[0]=x; destination[1]=y; destination[2]=w; destination[3]=h;
    source_crop[0]=u; source_crop[1]=v; source_crop[2]=uw; source_crop[3]=vh;
}
void coop_overlay_draw(ui::Ctx& c,const MkwVkCoopOverlay& view) {
    float matrix[16];
    {
        std::lock_guard lock(camera_lock);
        const uint64_t frame=texture_frame_no();
        if (scene_frame==UINT64_MAX || frame<scene_frame || frame-scene_frame>1) return;
        std::memcpy(matrix,scene_matrix,sizeof matrix);
    }
    const float px=18*c.s, pad=8*c.s;
    if (!(destination[2]>0 && destination[3]>0)) return;
    c.d->PushClipRect(ImVec2(destination[0],destination[1]),ImVec2(destination[0]+destination[2],destination[1]+destination[3]),true);
    for (int i=0;i<view.nmarks && i<HOS_COOP_MEMBERS;i++) {
        const auto& mark=view.mark[i];
        char name[sizeof mark.name]; std::memcpy(name,mark.name,sizeof name); name[sizeof name-1]=0;
        const ImVec2 size=ui::size_of(c.f.body,px,name);
        coop_overlay_math::Marker p{};
        if (!coop_overlay_math::project(matrix,mark.position,destination,source_crop,size.x,px,pad,
                                        (mark.flags&HOS_COOP_MARK_ARROW)!=0,p)) continue;
        if (!p.arrow && !(mark.flags&HOS_COOP_MARK_NAME)) continue;
        const ImVec2 a(p.text_x,p.text_y), b(p.text_x+size.x,p.text_y+px);
        if (name[0]) {
            c.d->AddRectFilled(a-ImVec2(3*c.s,3*c.s),b+ImVec2(3*c.s,3*c.s),ui::kBox,3*c.s);
            // Names are literal data, never localization or format patterns.
            c.d->AddText(c.f.body,px,a,ui::kInkBright,name);
        }
        if (p.arrow) {
            const ImVec2 tip(p.x+p.dx*pad,p.y+p.dy*pad), base(p.x-p.dx*pad,p.y-p.dy*pad);
            const ImVec2 side(-p.dy*pad*.6f,p.dx*pad*.6f);
            c.d->AddTriangleFilled(tip,base+side,base-side,ui::kGoldHi);
        }
    }
    c.d->PopClipRect();
}
}
