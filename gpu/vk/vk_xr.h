// (hookshot-quest) The game on a Meta Quest (or any OpenXR headset) over Quest Link / Air Link / Virtual Desktop: vk_xr.cpp.
//
// The renderer's Vulkan instance and device are made through OpenXR (XR_KHR_vulkan_enable2) when VR is on, every presented
// picture (the game's, the start-up screen, the Insert menu over them) is copied into an OpenXR swapchain, and a thread of its
// own shows it as a big screen in front of the player (a quad layer, world-locked: the headset's compositor keeps it still
// while the head moves, at the headset's rate whatever the game's). The Touch controllers are a virtual SDL3 gamepad, with
// the right controller's motion as its gyroscope (the game's Motion controls), so host/pad_sdl.c plays them as any pad.
//
// On when hookshot_vr.ini beside the game says vr=1 (RX_VR=0 / 1 over it). Anything missing (no openxr_loader.dll,
// no runtime, the headset not connected) leaves the game on the desktop as it was, said in the log ("xr: ...").
#pragma once

#include <cstdint>

#include "vk_internal.h"

namespace mkwvk {

/// This build has the headset's code (Windows; not Linux yet)
bool xr_built();
/// VR wanted and an OpenXR system (a headset) found: the instance and device are to be made through it
bool xr_wanted();
/// vkCreateInstance through the runtime (its extensions added). False: not handled, make it as usual
bool xr_create_instance(const VkInstanceCreateInfo* ci, VkInstance* out);
/// The physical device the headset is on (VK_NULL_HANDLE: no say)
VkPhysicalDevice xr_physical_device();
/// vkCreateDevice through the runtime. False: not handled, make it as usual
bool xr_create_device(VkPhysicalDevice physical, const VkDeviceCreateInfo* ci, VkDevice* out);
/// The device and its queue made: the session, the controllers, and the thread that shows the screen
void xr_device_ready();
/// The session is running (the headset shows the game): the pads count with the window behind others, and the Insert menu
/// hears them
bool xr_running();
/// The window is being closed: the frame loop stopped, the session and the instance destroyed. A process that left with them
/// alive died inside Meta's runtime library (RuntimeIPCServiceClient_64.dll, 0xc0000409, at every exit), and the runtime's
/// service was left believing the application still ran: Meta Horizon Link would not start again ("QLH is already running")
void xr_shutdown();
/// VR is on and the device was made through OpenXR (the headset may not be worn yet): frame generation stays off
bool xr_enabled();
/// Stereo is on (stereo=1, the device made through OpenXR, not given up): the temporal upscalers stay off
bool xr_stereo_on();
/// Stereo runs and stereo_dlss=0: the temporal upscalers (DLSS, FSR) stay off, their history being the other eye's frame
bool xr_no_temporal();
/// The temporal upscalers' chain for the frame being drawn: its eye's (0 left, 1 right) in stereo, 0 otherwise
int xr_temporal_slot();

/// One presented picture's copy into the headset's screen (present_image): acquire before recording, record, release after
/// the submit (dev.present_mu held for the release, not for the acquire)
struct XrCopy {
    VkImage image = VK_NULL_HANDLE;   // (the OpenXR swapchain's image, waited for: VK_NULL_HANDLE when nothing is copied)
    uint32_t w = 0, h = 0;
    int eye = -1;                     // (stereo: the eye whose picture this is; -1 the screen's)
    float pose[7]{}, fov[4]{};        // (an eye's XrPosef and XrFovf: where it was drawn from)
    float cam[12]{};                  // (an eye's: the game's camera of its frame)
    uint64_t seq = 0;
    bool hud = false;                 // (the HUD's swapchain)
    // (an eye's: its image is the headset's whole field of view, the picture a part of it. The image's size; the picture's
    // place in it; what is left out of the picture's left and top where it is wider than the headset's view; 1: scaled)
    uint32_t full_w = 0, full_h = 0, ox = 0, oy = 0, crop_x = 0, crop_y = 0, dst_w = 0, dst_h = 0;
    bool scaled = false;
    int64_t pose_time = 0;            // (an eye's: the moment its pose was located for, XrTime)
    int phase = 0;                    // (an eye's: 1 the first of a pair of one moment (held for the second), 2 the second)
    bool from_game = false;           // (an eye's: copied from the game's own picture, not the window's)
};
/// game_frame: one of the game's own frames (not a made one, not the start-up screen): in stereo, an eye's
/// game_w, game_h, game_format: the game's own picture (its crop), which an eye's copy is taken from when its format allows
XrCopy xr_copy_acquire(uint32_t w, uint32_t h, bool game_frame, uint32_t game_w, uint32_t game_h, VkFormat game_format);
/// Records the copy of [x, y, w, h) of src (TRANSFER_SRC_OPTIMAL) into c.image
void xr_copy_record(VkCommandBuffer cmd, const XrCopy& c, VkImage src, VkImageLayout src_layout, uint32_t x, uint32_t y);
void xr_copy_release(const XrCopy& c);

/// The window in stereo: the frame's eye's picture (src, GENERAL, its rectangle) kept, and both eyes' drawn side by side into the
/// window's image (dst, TRANSFER_DST_OPTIMAL, w x h), each at the shape its field of view has. False: not a stereo frame, the
/// window is drawn as usual
bool xr_mirror(VkCommandBuffer cmd, VkImage src, uint32_t sx, uint32_t sy, uint32_t sw, uint32_t sh, VkImage dst, uint32_t w,
               uint32_t h);

// The Insert menu's VR tab (vk_menu.cpp): hookshot_vr.ini's settings as the menu's whole numbers (a length in centimetres, a
// scale in percent, a switch 0 or 1). A row changes xr_opt[] and tells xr_opt_changed: the setting applies at once (those read
// at the start after a restart: XO_VR, XO_STEREO, XO_GYRO) and its line in hookshot_vr.ini is written, the file's other lines
// and comments kept
enum XrOpt {
    XO_VR, XO_STEREO,
    // (the stereo view's: XO_STEREO_FIRST to XO_STEREO_LAST)
    XO_FIRST_PERSON, XO_FP_HEIGHT, XO_FP_FORWARD, XO_WORLD_SCALE, XO_SEPARATION, XO_HORIZON, XO_HEAD_MOTION, XO_FILL, XO_DLSS,
    XO_MIRROR, XO_HUD, XO_HUD_DISTANCE, XO_HUD_WIDTH, XO_HUD_HEIGHT, XO_HUD_AIM, XO_HUD_CENTER, XO_HUD_AIM_DISTANCE, XO_SYNC,
    XO_PACE, XO_SMOOTH,
    // (the flat screen's)
    XO_SCREEN_DISTANCE, XO_SCREEN_WIDTH, XO_SCREEN_HEIGHT,
    XO_TOUCH, XO_GYRO, XO_SWAP,
    XO_N,
    XO_STEREO_FIRST = XO_FIRST_PERSON, XO_STEREO_LAST = XO_SMOOTH, XO_SCREEN_FIRST = XO_SCREEN_DISTANCE,
    XO_SCREEN_LAST = XO_SCREEN_HEIGHT
};
extern int xr_opt[XO_N];
/// hookshot_vr.ini read into xr_opt[] (once; the menu's start, before it notes what the game started with)
void xr_opts_load();
void xr_opt_changed(int id);
/// The screen, the HUD and the game's camera put in front of the head as it is now (as F8, or both sticks held in)
void xr_recenter();
/// What the headset is doing, for the VR tab's first row
const char* xr_status();

struct Texture;
/// Stereo: a draw into a scene-sized target (vk_draw.cpp, where the temporal upscalers are told of it): its camera read back and
/// matched to the views handed to the game, so the frame's present knows its eye
/// (the HUD's draws: the draws into a window texture, traced for one frame every 10 s while VR runs)
void xr_note_window_draw(const Texture* color0, uint32_t width, uint32_t height, uint64_t prog, int count, int instances, bool mesh,
                         uint32_t blend_en, const float* viewport, bool depth, const struct NvnState* window_state);
/// The HUD: the window's draw samples the picture the HUD is drawn over (its texture 0), noted each frame
void xr_note_window(const struct NvnState& s);
/// A draw into that picture in a stereo frame: drawn into the HUD's own picture instead (true: done, the draw is not made)
bool xr_hud_draw(VkCommandBuffer cmd, struct NvnState& s, const struct DrawArgs& d, Texture* color0);
/// The HUD's picture into its swapchain at the present (the frame drew a HUD): acquire, record, release as the eyes' copy
XrCopy xr_hud_acquire();
void xr_hud_record(VkCommandBuffer cmd, const XrCopy& c);
/// (the trace: a texture the window's draw sampled, whose own draws are traced too)
bool xr_hud_watched(uint64_t gpu);
void xr_note_scene_draw(const Texture* color0, uint32_t width, uint32_t height, uint64_t camera, uint64_t camera_size);

} // namespace mkwvk
