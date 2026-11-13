// Optional ANR frame generation provider: the renderer still owns and presents every output image.
#pragma once

#include "vk_internal.h"

namespace mkwvk {
struct TemporalFrame;

bool anr_fg_selected();
// The current reason, or each possible help line for the menu's translation/height walk.
const char* anr_fg_reason(int variant = -1);
void anr_fg_inputs(VkCommandBuffer cmd, const TemporalFrame& frame, const float projection[16],
                   const float view_projection[16], const float clip_jitter[2], bool motion);
int anr_fg_frames(VkCommandBuffer cmd, VkImage color, VkImageView view, uint32_t width, uint32_t height, int count,
                  const VkImage* outputs, const VkImageView* views, bool blend, uint64_t sequence);
void anr_fg_idle();
void anr_fg_close();
} // namespace mkwvk
