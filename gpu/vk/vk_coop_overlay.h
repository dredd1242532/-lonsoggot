// Co-op UI: copied host snapshots and the submitted scene camera.
#pragma once
#include <cstdint>
#include "vk_api.h"
namespace mkwvk {
namespace ui { struct Ctx; }
bool coop_overlay_read(MkwVkCoopOverlay& out, int labels);
bool coop_overlay_wants_camera();
void coop_overlay_camera(const float matrix[16], const float jitter[2], uint64_t frame);
void coop_overlay_viewport(float x, float y, float w, float h, float u, float v, float uw, float vh);
void coop_overlay_draw(ui::Ctx& c, const MkwVkCoopOverlay& view);
}
