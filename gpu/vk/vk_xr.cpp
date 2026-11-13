// (hookshot-quest) OpenXR output and Touch controllers: vk_xr.h says what and why.
//
// Two ways to show the game. The screen: every presented picture copied into one swapchain, shown as a quad in front of the
// player. Stereo (stereo=1 in hookshot_vr.ini): the game's own camera moved to the headset's eyes, one eye a frame (alternate
// eye rendering), through host/hos_main.c hos_oot_free_view, which asks mkwvk_xr_view below for the view each frame. That is
// the camera the game culls and draws with, so the picture is the world as seen from that eye. Each frame's scene camera is read
// back where the draws read it (xr_note_scene_draw: the uniform buffer the temporal upscalers read, vk_temporal.cpp) and matched
// to the view handed out, which tells which eye the frame is, the pose it was drawn for and its field of view (the game's
// projection). The frame goes into that eye's swapchain, and the headset gets a projection layer of the two eyes' latest, each
// with its own pose: the compositor turns each to the head as it is when shown. The world is fixed to the game's camera; the
// head's motion from where it was at the last recentring moves the eyes from it (world_scale: game metres for a real one).
// Whenever there is no stereo frame (the Insert menu open, the start-up screen, a frame not matched), the screen shows instead.
//
// Threads and locks. The present path (present_image, one thread at a time) acquires an image of an OpenXR swapchain, records
// its copy, and releases it after the submit. The XR thread polls the runtime's events, syncs the controllers, locates the
// eyes, waits for the headset's frame and submits the layer. OpenXR may use the Vulkan queue in xrBeginFrame, xrEndFrame,
// xrAcquireSwapchainImage and xrReleaseSwapchainImage (XR_KHR_vulkan_enable2, "Concurrency"): each runs with dev.present_mu
// held, the lock the replay's submits and the presents take. xr.mu (the swapchains and what they show) is taken after
// dev.present_mu, never before it; xr.in_mu (the controllers, the eyes, the views handed out) and xr.tag_mu (the last frame's
// match) are taken alone.
#include "vk_xr.h"

#ifndef _WIN32
// Not on Linux yet: this file's threads, clocks, keys and paths are Win32's. The renderer is built without a headset there: every
// call says so, and the VR tab shows one row that says why (vk_menu.cpp vr_row_shown)
namespace mkwvk {
int xr_opt[XO_N];
bool xr_built() { return false; }
bool xr_wanted() { return false; }
bool xr_create_instance(const VkInstanceCreateInfo*, VkInstance*) { return false; }
VkPhysicalDevice xr_physical_device() { return VK_NULL_HANDLE; }
bool xr_create_device(VkPhysicalDevice, const VkDeviceCreateInfo*, VkDevice*) { return false; }
void xr_device_ready() {}
bool xr_running() { return false; }
void xr_shutdown() {}
bool xr_enabled() { return false; }
bool xr_stereo_on() { return false; }
bool xr_no_temporal() { return false; }
int xr_temporal_slot() { return 0; }
XrCopy xr_copy_acquire(uint32_t, uint32_t, bool, uint32_t, uint32_t, VkFormat) { return {}; }
void xr_copy_record(VkCommandBuffer, const XrCopy&, VkImage, VkImageLayout, uint32_t, uint32_t) {}
void xr_copy_release(const XrCopy&) {}
bool xr_mirror(VkCommandBuffer, VkImage, uint32_t, uint32_t, uint32_t, uint32_t, VkImage, uint32_t, uint32_t) { return false; }
void xr_note_window_draw(const Texture*, uint32_t, uint32_t, uint64_t, int, int, bool, uint32_t, const float*, bool,
                         const struct NvnState*) {}
void xr_note_window(const struct NvnState&) {}
bool xr_hud_draw(VkCommandBuffer, struct NvnState&, const struct DrawArgs&, Texture*) { return false; }
XrCopy xr_hud_acquire() { return {}; }
void xr_hud_record(VkCommandBuffer, const XrCopy&) {}
bool xr_hud_watched(uint64_t) { return false; }
void xr_note_scene_draw(const Texture*, uint32_t, uint32_t, uint64_t, uint64_t) {}
void xr_opts_load() {}
void xr_opt_changed(int) {}
void xr_recenter() {}
const char* xr_status() { return "Not in the Linux build yet"; }
} // namespace mkwvk
#else

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "vk_menu.h"
#include "vk_paths.h"
#include "vk_draw.h"
#include "vk_state.h"
#include "vk_temporal.h"
#include "vk_texture.h"

#define XR_NO_PROTOTYPES
#define XR_USE_GRAPHICS_API_VULKAN
#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>

#include "vk_work.h"

namespace mkwvk {

extern std::atomic<uint32_t> present_kind;   // (vk_replay.cpp: 1 while a made frame goes out)

namespace {

void image_barrier(VkCommandBuffer cmd, VkImage image, VkImageLayout from, VkImageLayout to) {   // (as vk_replay.cpp's)
    VkImageMemoryBarrier2 b{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2};
    b.srcStageMask = b.dstStageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT;
    b.srcAccessMask = VK_ACCESS_2_MEMORY_WRITE_BIT;
    b.dstAccessMask = VK_ACCESS_2_MEMORY_READ_BIT | VK_ACCESS_2_MEMORY_WRITE_BIT;
    b.oldLayout = from;
    b.newLayout = to;
    b.srcQueueFamilyIndex = b.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    b.image = image;
    b.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    VkDependencyInfo d{VK_STRUCTURE_TYPE_DEPENDENCY_INFO};
    d.imageMemoryBarrierCount = 1;
    d.pImageMemoryBarriers = &b;
    vkCmdPipelineBarrier2(cmd, &d);
}

// ---------------------------------------------------------------------------------------------------------------------------
// settings: hookshot_vr.ini in the game folder (quest/build_vk.ps1 -Install puts one there), RX_VR over its vr= line
struct Settings {
    int vr = 0;
    float distance = 2.6f;   // (the screen: metres from the head)
    float width = 3.6f;      // (its width in metres; its height follows the picture)
    float height = 0.0f;     // (its middle above (+) or below (-) the eyes, metres)
    int gyro = 1;            // (the right controller's motion as the pad's gyroscope)
    int swap_sticks = 0;     // (1: move with the right stick, the camera on the left)
    int stereo = 0;          // (1: the game's camera at the headset's eyes, one eye a frame)
    float world_scale = 1.0f;   // (stereo: game metres for one real metre; above 1 the world looks smaller, a diorama)
    int horizon_lock = 1;    // (stereo: the game camera's pitch left out, the horizon level; the head's own pitch stays)
    int head_motion = 1;     // (stereo: the head's movement moves the eyes; 0: only its turning)
    int hud = 1;             // (stereo: the HUD on a panel fixed to the view, not drawn into the eyes)
    float separation = 1.0f;   // (stereo: the eyes' distance apart, times the real one; 0: both eyes drawn from between them)
    float test_yaw = 0, test_pitch = 0;   // (RX_VR_TEST: the pretended head's turn to the left and up, degrees)
    int dlss = 1;            // (stereo: DLSS / FSR allowed (their history is the other eye's frame, moved by the camera's step); 0: the game's own upscaler)
    int sync = 1;            // (stereo: the game's time in pairs of frames, both eyes of a pair one moment, shown together)
    int sync_us = 4167;      // (the second frame's step, microseconds: 1/240 s, the least the game takes. 300 and 1500 crashed it at once)
    int touch = 1;           // (the Touch controllers as a gamepad; 0: at rest for the game, whatever is pressed on them)
    int smooth = 0;          // (stereo: between the game's frames the pictures go on turning as its camera was: stereo_smooth.
                             // Off: with stereo_pace the runtime's own reprojection and ASW do that, as they are made to)
    int lead = 0;            // (stereo: the head predicted further ahead by how late pairs have been: stereo_lead. Off: with
                             // stereo_pace a pair is made for the frame it is shown in, and its eyes are located for that frame)
    int pace = 1;            // (stereo: one frame given to the headset for each pair of eyes the game drew, as OpenXR means it:
                             // xrWaitFrame, xrBeginFrame, the pair, xrEndFrame. The runtime then knows the game's real rate
                             // and fills the frames between with its own reprojection (ASW). 0: the pair given again at each
                             // of the headset's refreshes, as before, which told the runtime the game ran at the full rate)
    int sync_flip = 1;       // (the host's first and second frame of a pair taken the other way round)
    int mirror = 1;          // (stereo, the window: 1 both eyes side by side, 2 the left eye alone, 0 each frame as the game drew it)
    int fill = 1;            // (stereo: the game's cameras take the headset's field of view, the picture filling the view)
    float hud_distance = 1.6f, hud_width = 1.9f, hud_height = -0.05f;   // (that panel: metres in front, wide, above the eyes)
    // (the HUD's middle, where the game draws its aiming reticles, is not the view's: it is where the game's camera points, the
    // aim. That part is shown there, far off, and the rest stays on the panel. hud_aim=0: all of it on the panel)
    // (first person: the eyes where the game's camera looks, which is Link, not where it looks from. The game has no such view
    // (its stick press is "Reset Camera"): this is the headset's own. first_person: on at the start; a tap of the left stick or
    // F9 turns it on and off. fp_height, fp_forward: metres above and ahead of the point the camera looks at, so the eyes are at
    // his face and not inside his head)
    int first_person = 0;
    float fp_height = 0.25f, fp_forward = 0.2f;
    int hud_aim = 1;
    float hud_center = 0.3f;         // (the part of the HUD's width and height that is the middle)
    float hud_aim_distance = 25.0f;  // (metres to it: far, so a reticle lies on what it aims at for both eyes)
};
// (the VR tab's numbers, vk_xr.h XrOpt: each one's key in the file, its field, and the menu's units to one of the file's)
struct OptMap {
    const char* key;
    int Settings::*i;
    float Settings::*f;
    float scale;
};
const OptMap opt_map[XO_N] = {
    {"vr", &Settings::vr, nullptr, 1},
    {"stereo", &Settings::stereo, nullptr, 1},
    {"first_person", &Settings::first_person, nullptr, 1},
    {"fp_height", nullptr, &Settings::fp_height, 100},
    {"fp_forward", nullptr, &Settings::fp_forward, 100},
    {"world_scale", nullptr, &Settings::world_scale, 100},
    {"stereo_separation", nullptr, &Settings::separation, 100},
    {"horizon_lock", &Settings::horizon_lock, nullptr, 1},
    {"head_motion", &Settings::head_motion, nullptr, 1},
    {"stereo_fill", &Settings::fill, nullptr, 1},
    {"stereo_dlss", &Settings::dlss, nullptr, 1},
    {"mirror", &Settings::mirror, nullptr, 1},
    {"hud", &Settings::hud, nullptr, 1},
    {"hud_distance", nullptr, &Settings::hud_distance, 100},
    {"hud_width", nullptr, &Settings::hud_width, 100},
    {"hud_height", nullptr, &Settings::hud_height, 100},
    {"hud_aim", &Settings::hud_aim, nullptr, 1},
    {"hud_center", nullptr, &Settings::hud_center, 100},
    {"hud_aim_distance", nullptr, &Settings::hud_aim_distance, 1},
    {"stereo_sync", &Settings::sync, nullptr, 1},
    {"stereo_pace", &Settings::pace, nullptr, 1},
    {"stereo_smooth", &Settings::smooth, nullptr, 1},
    {"screen_distance", nullptr, &Settings::distance, 100},
    {"screen_width", nullptr, &Settings::width, 100},
    {"screen_height", nullptr, &Settings::height, 100},
    {"touch_controllers", &Settings::touch, nullptr, 1},
    {"gyro", &Settings::gyro, nullptr, 1},
    {"swap_sticks", &Settings::swap_sticks, nullptr, 1},
};
void opts_from(const Settings& s) {
    for (int k = 0; k < XO_N; k++) {
        const OptMap& m = opt_map[k];
        xr_opt[k] = m.i ? s.*m.i : (int)std::lround(s.*m.f * m.scale);
    }
}
std::string vr_ini_path() {
    char exe[MAX_PATH];
    const DWORD len = game_module_path(exe, MAX_PATH);
    std::string ini(exe, len);
    return ini.substr(0, ini.find_last_of("\\/") + 1) + "hookshot_vr.ini";
}
std::atomic<uint64_t> ini_seen{0};   // (the file's time as last read or written here: reload_settings reads it again when it differs)
uint64_t ini_time() {
    WIN32_FILE_ATTRIBUTE_DATA a;
    if (!GetFileAttributesExA(vr_ini_path().c_str(), GetFileExInfoStandard, &a)) return 0;
    return ((uint64_t)a.ftLastWriteTime.dwHighDateTime << 32) | a.ftLastWriteTime.dwLowDateTime;
}
/// One setting's line in hookshot_vr.ini set (added at the end when the file has none): the other lines as they are
void ini_write(const char* key, const char* value) {
    const std::string path = vr_ini_path();
    std::vector<std::string> lines;
    if (FILE* f = fopen(path.c_str(), "r")) {
        char line[512];
        while (fgets(line, sizeof line, f)) {
            std::string l(line);
            while (!l.empty() && (l.back() == '\n' || l.back() == '\r')) l.pop_back();
            lines.push_back(l);
        }
        fclose(f);
    }
    bool found = false;
    for (std::string& l : lines) {
        char k[64];
        float v;
        if (sscanf(l.c_str(), " %63[a-z_] = %f", k, &v) == 2 && !strcmp(k, key)) l = std::string(key) + "=" + value, found = true;
    }
    if (!found) lines.push_back(std::string(key) + "=" + value);
    const std::string tmp = path + ".tmp";
    FILE* f = fopen(tmp.c_str(), "w");
    if (!f) {
        logf("xr: hookshot_vr.ini could not be written (%s)", tmp.c_str());
        return;
    }
    for (const std::string& l : lines) fprintf(f, "%s\n", l.c_str());
    fclose(f);
    if (!MoveFileExA(tmp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING)) {
        logf("xr: hookshot_vr.ini could not be replaced (error %lu)", GetLastError());
        DeleteFileA(tmp.c_str());
        return;
    }
    ini_seen.store(ini_time(), std::memory_order_relaxed);   // (this write is not a change to read again)
}
Settings load_settings() {
    Settings s;
    char exe[MAX_PATH];
    const DWORD len = game_module_path(exe, MAX_PATH);
    std::string ini(exe, len);
    ini = ini.substr(0, ini.find_last_of("\\/") + 1) + "hookshot_vr.ini";
    if (FILE* f = fopen(ini.c_str(), "r")) {
        char line[160];
        while (fgets(line, sizeof line, f)) {
            char key[64];
            float v;
            if (sscanf(line, " %63[a-z_] = %f", key, &v) != 2) continue;
            if (!strcmp(key, "vr")) s.vr = (int)v;
            else if (!strcmp(key, "screen_distance")) s.distance = v;
            else if (!strcmp(key, "screen_width")) s.width = v;
            else if (!strcmp(key, "screen_height")) s.height = v;
            else if (!strcmp(key, "gyro")) s.gyro = (int)v;
            else if (!strcmp(key, "swap_sticks")) s.swap_sticks = (int)v;
            else if (!strcmp(key, "stereo")) s.stereo = (int)v;
            else if (!strcmp(key, "world_scale")) s.world_scale = v;
            else if (!strcmp(key, "horizon_lock")) s.horizon_lock = (int)v;
            else if (!strcmp(key, "head_motion")) s.head_motion = (int)v;
            else if (!strcmp(key, "hud")) s.hud = (int)v;
            else if (!strcmp(key, "stereo_separation")) s.separation = v;
            else if (!strcmp(key, "test_yaw")) s.test_yaw = v;
            else if (!strcmp(key, "stereo_fill")) s.fill = (int)v;
            else if (!strcmp(key, "mirror")) s.mirror = (int)v;
            else if (!strcmp(key, "stereo_sync")) s.sync = (int)v;
            else if (!strcmp(key, "sync_step_us")) s.sync_us = (int)v;
            else if (!strcmp(key, "sync_flip")) s.sync_flip = (int)v;
            else if (!strcmp(key, "stereo_smooth")) s.smooth = (int)v;
            else if (!strcmp(key, "stereo_pace")) s.pace = (int)v;
            else if (!strcmp(key, "stereo_lead")) s.lead = (int)v;
            else if (!strcmp(key, "touch_controllers")) s.touch = (int)v;
            else if (!strcmp(key, "stereo_dlss")) s.dlss = (int)v;
            else if (!strcmp(key, "test_pitch")) s.test_pitch = v;
            else if (!strcmp(key, "hud_distance")) s.hud_distance = v;
            else if (!strcmp(key, "hud_width")) s.hud_width = v;
            else if (!strcmp(key, "hud_height")) s.hud_height = v;
            else if (!strcmp(key, "hud_aim")) s.hud_aim = (int)v;
            else if (!strcmp(key, "first_person")) s.first_person = (int)v;
            else if (!strcmp(key, "fp_height")) s.fp_height = v;
            else if (!strcmp(key, "fp_forward")) s.fp_forward = v;
            else if (!strcmp(key, "hud_center")) s.hud_center = v;
            else if (!strcmp(key, "hud_aim_distance")) s.hud_aim_distance = v;
        }
        fclose(f);
    }
    if (const char* e = getenv("RX_VR")) s.vr = atoi(e);
    if (const char* e = getenv("RX_VR_STEREO")) s.stereo = atoi(e);
    if (s.distance < 0.5f) s.distance = 0.5f;
    if (s.width < 0.3f) s.width = 0.3f;
    if (!(s.world_scale > 0.01f)) s.world_scale = 1.0f;
    return s;
}

// ---------------------------------------------------------------------------------------------------------------------------
// OpenXR's functions, from openxr_loader.dll (beside this DLL, else the system's)
#define XR_FUNCS(X)                                                                                                            \
    X(xrDestroyInstance) X(xrGetInstanceProperties) X(xrGetSystem) X(xrGetSystemProperties) X(xrPollEvent)                    \
    X(xrResultToString) X(xrGetVulkanGraphicsRequirements2KHR) X(xrCreateVulkanInstanceKHR)                                  \
    X(xrGetVulkanGraphicsDevice2KHR) X(xrCreateVulkanDeviceKHR) X(xrCreateSession) X(xrDestroySession) X(xrBeginSession)     \
    X(xrEndSession) X(xrRequestExitSession) X(xrCreateReferenceSpace) X(xrLocateSpace) X(xrLocateViews)                       \
    X(xrEnumerateSwapchainFormats) X(xrCreateSwapchain) X(xrDestroySwapchain) X(xrEnumerateSwapchainImages)                  \
    X(xrAcquireSwapchainImage) X(xrWaitSwapchainImage) X(xrReleaseSwapchainImage) X(xrWaitFrame) X(xrBeginFrame)             \
    X(xrEndFrame) X(xrStringToPath) X(xrCreateActionSet) X(xrCreateAction) X(xrSuggestInteractionProfileBindings)            \
    X(xrAttachSessionActionSets) X(xrSyncActions) X(xrGetActionStateBoolean) X(xrGetActionStateFloat)                        \
    X(xrGetActionStateVector2f) X(xrGetActionStatePose) X(xrCreateActionSpace) X(xrApplyHapticFeedback)                      \
    X(xrStopHapticFeedback)
#define XR_DECLARE(f) PFN_##f f = nullptr;
XR_FUNCS(XR_DECLARE)
#undef XR_DECLARE
PFN_xrGetInstanceProcAddr xrGetInstanceProcAddr_ = nullptr;

HMODULE load_loader() {
    HMODULE self = nullptr;
    char path[MAX_PATH];
    if (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           (LPCSTR)(void*)&load_loader, &self)) {
        DWORD n = GetModuleFileNameA(self, path, MAX_PATH);
        while (n && path[n - 1] != '\\' && path[n - 1] != '/') n--;
        snprintf(path + n, MAX_PATH - n, "openxr_loader.dll");
        if (HMODULE m = LoadLibraryExA(path, nullptr, LOAD_WITH_ALTERED_SEARCH_PATH)) return m;
    }
    return LoadLibraryExA("openxr_loader.dll", nullptr, LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
}

// ---------------------------------------------------------------------------------------------------------------------------
// small vector maths (OpenXR's frames, and the game's cameras: +X right, +Y up, -Z forward)
struct V3 { float x, y, z; };
V3 add(V3 a, V3 b) { return {a.x + b.x, a.y + b.y, a.z + b.z}; }
V3 sub(V3 a, V3 b) { return {a.x - b.x, a.y - b.y, a.z - b.z}; }
V3 mul(V3 a, float s) { return {a.x * s, a.y * s, a.z * s}; }
float dot(V3 a, V3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
V3 cross(V3 a, V3 b) { return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x}; }
V3 norm(V3 a) {
    const float l = std::sqrt(dot(a, a));
    return l > 1e-9f ? mul(a, 1.0f / l) : a;
}
V3 v3(XrVector3f v) { return {v.x, v.y, v.z}; }
XrQuaternionf qmul(XrQuaternionf a, XrQuaternionf b) {
    return {a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y, a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
            a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w, a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z};
}
XrQuaternionf qconj(XrQuaternionf q) { return {-q.x, -q.y, -q.z, q.w}; }
V3 rotate(XrQuaternionf q, V3 v) {
    const XrQuaternionf p{v.x, v.y, v.z, 0};
    const XrQuaternionf r = qmul(qmul(q, p), qconj(q));
    return {r.x, r.y, r.z};
}
XrQuaternionf yaw_of(XrQuaternionf q) {   // (the turn about the vertical alone)
    const V3 f = rotate(q, {0, 0, -1});
    const float yaw = std::atan2(-f.x, -f.z);
    return {0, std::sin(yaw / 2), 0, std::cos(yaw / 2)};
}

// ---------------------------------------------------------------------------------------------------------------------------
// the Touch controllers as they were read last (the XR thread writes, SDL's update of the virtual pad reads)
struct Touch {
    float lx = 0, ly = 0, rx = 0, ry = 0;   // (sticks, -1..1, up positive)
    float lt = 0, rt = 0, lg = 0, rg = 0;   // (triggers and grips, 0..1)
    bool a = false, b = false, x = false, y = false, menu = false, lclick = false, rclick = false;
    bool aim_valid = false;
    V3 gyro{};    // (rad/s, in the controller's own frame)
    V3 accel{};   // (m/s^2, gravity's reaction in the controller's frame)
};

// an OpenXR swapchain the presents copy into: the screen's, or an eye's
struct Chain {
    XrSwapchain sc = XR_NULL_HANDLE;
    std::vector<VkImage> images;
    uint32_t w = 0, h = 0;
    bool released_once = false;   // (an image to show)
    XrPosef pose{{0, 0, 0, 1}, {0, 0, 0}};   // (an eye's: where its picture was drawn from, and its field of view)
    XrFovf fov{};
    float cam[12]{};    // (an eye's: the game camera of its frame)
    uint64_t seq = 0;   // (an eye's: its frame's view; the newer eye is the other's reference)
    uint64_t updates = 0;   // (an eye's: pictures it got, for the log's "eye" lines)
    bool held = false;      // (an eye's: an image acquired and copied into, not released: the first of a pair, waiting for the second)
    VkImage held_image = VK_NULL_HANDLE;   // (that image: a first whose second never came is drawn over by the next first)
    bool acquired = false;   // (an image acquired whose wait has not come through yet: waited for again, not acquired again)
    uint32_t acquired_index = 0;
    XrPosef held_pose{};
    int64_t held_time = 0, time = 0;   // (an eye's: the moment its picture's pose was located for)
    XrFovf held_fov{};
    float held_cam[12]{};
    uint64_t held_seq = 0;
    int64_t at_ns = 0;      // (an eye's: when its last picture came)
};

// the HUD's own picture: an RGBA16F target of the HUD picture's size, at guest memory of the renderer's own (host->guest_alloc)
struct Hud {
    std::mutex mu;
    std::atomic<uint64_t> picture_gpu{0};   // (the picture the game draws its HUD over: the window draw's texture 0)
    nvn_tex picture{};                      // (its texture, as last seen)
    nvn_tex own{};                          // (the HUD's own)
    bool made = false, failed = false;
    uint64_t cleared_frame = ~0ull, serial = 1ull << 62;
    std::atomic<uint64_t> frame{~0ull};     // (the frame a HUD draw went into it last)
    std::atomic<VkImage> image{VK_NULL_HANDLE};
    uint32_t w = 0, h = 0;
};
Hud& hud() {
    static Hud h;
    return h;
}

// a view handed to the game (mkwvk_xr_view), to know a frame by
struct Issued {
    uint64_t seq = 0;
    int eye = 0;
    XrPosef pose{};
    float v[12]{};
    float cam[12]{};   // (the game's upright camera it was made from: right, up, back axes and its place, game units)
    int phase = 0;     // (1 the first frame of a pair of one moment, 2 the second, 0 no pairs)
    XrTime time = 0;   // (the moment its pose was located for)
};
// a frame's scene camera matched to one (xr_note_scene_draw), for its present
struct Tag {
    uint64_t seq = 0, frame = 0;
    int eye = -1;
    XrPosef pose{};
    XrFovf fov{};
    float cam[12]{};
    int phase = 0;
    XrTime time = 0;
};

struct Xr {
    Settings set;
    bool wanted = false;   // (instance and system there: the Vulkan objects go through the runtime)
    XrInstance instance = XR_NULL_HANDLE;
    XrSystemId system = XR_NULL_SYSTEM_ID;
    XrSession session = XR_NULL_HANDLE;
    XrSpace local = XR_NULL_HANDLE, view = XR_NULL_HANDLE;
    std::atomic<bool> running{false}, dead{false}, thread_done{true};
    XrSessionState state = XR_SESSION_STATE_UNKNOWN;

    // what is shown (mu): the screen and the two eyes
    std::mutex mu;
    Chain screen_chain, eyes[2], hud_chain;
    float game_tan_x = 0.83f, game_tan_y = 0.47f;   // (the game's picture: the tangents of half its field of view, as last drawn)
    std::atomic<int> first_person{-1};   // (1 on, 0 off; -1: as hookshot_vr.ini says, until it is toggled)
    std::atomic<float> game_pitch{0};               // (the game camera's tilt left out of the eyes' views by horizon_lock, radians up)
    std::atomic<int64_t> hud_ns{0};   // (when the HUD's picture came last: the panel shows while it is fresh)
    VkFormat format = VK_FORMAT_UNDEFINED;
    bool copy_raw = false;   // (vkCmdCopyImage: the same bytes; otherwise a blit converts)
    XrPosef screen{{0, 0, 0, 1}, {0, 0, -2.6f}};
    std::atomic<bool> recenter{true};
    std::atomic<int64_t> stereo_ns{0};   // (when an eye's picture came last)
    // (a pair of eyes shown: counted, for the frame loop that gives each to the headset once: xr_thread)
    std::mutex pair_mu;
    std::condition_variable pair_cv;
    uint64_t pair_gen = 0;
    // (the frame loop as OpenXR means it, across the two threads: the runtime says when a frame starts (xrWaitFrame), the frame is
    // begun and the eyes located for when it will be shown (open_gen counts them), the game then makes its pair for that frame
    // with those eyes (the camera hook waits here before a pair's first frame), and the frame ends with that pair. The game
    // runs at the headset's pace, two frames to each of its frames, and no pair is made that is not shown)
    std::mutex open_mu;
    std::condition_variable open_cv;
    uint64_t open_gen = 0, open_used = 0;
    std::atomic<uint64_t> alone{0};   // (an eye's held picture shown without its pair's second: xr_copy_acquire)
    // (pairs kept whole: a second frame whose first was not the view before it is left out, and a first whose second never came is
    // drawn over by the next first; the headset keeps the last whole pair meanwhile)
    std::atomic<uint64_t> orphans{0}, redrawn{0};
    std::atomic<uint64_t> not_ready{0};   // (copies left out: the compositor had no image free within 3 ms)
    // (the game's camera of the last two pictures shown and when each came: its motion, which the pictures go on with between
    // the game's frames, xr_thread)
    float cam_cur[12]{}, cam_prev[12]{};
    int64_t cam_cur_ns = 0, cam_prev_ns = 0;
    std::atomic<uint64_t> matched{0};    // (frames whose camera matched a view handed out)
    std::atomic<bool> stereo_off{false};   // (given up: no frame ever matched)

    // the controllers, the eyes, the views handed out (in_mu)
    XrActionSet set_ = XR_NULL_HANDLE;
    XrAction a_move = XR_NULL_HANDLE, a_look = XR_NULL_HANDLE, a_a = XR_NULL_HANDLE, a_b = XR_NULL_HANDLE,
             a_x = XR_NULL_HANDLE, a_y = XR_NULL_HANDLE, a_menu = XR_NULL_HANDLE, a_lt = XR_NULL_HANDLE,
             a_rt = XR_NULL_HANDLE, a_lg = XR_NULL_HANDLE, a_rg = XR_NULL_HANDLE, a_lclick = XR_NULL_HANDLE,
             a_rclick = XR_NULL_HANDLE, a_aim = XR_NULL_HANDLE, a_haptic_l = XR_NULL_HANDLE, a_haptic_r = XR_NULL_HANDLE;
    XrSpace aim_space = XR_NULL_HANDLE;
    std::mutex in_mu;
    Touch touch;
    std::atomic<uint32_t> rumble{0};   // (the game's motors: low << 16 | high)
    XrPosef eye_pose[2]{};   // (the eyes as located last, in the local space)
    XrFovf eye_fov[2]{};     // (and the field of view the runtime draws each with: the eyes' pictures are laid out in it)
    // (a view handed to the game is shown some frames later: the eyes are located that much ahead (lead_ns, learnt from the
    // pictures' own times against the frames they were shown in), so a picture is drawn for where the head will be when it is
    // seen. Drawn for an older moment, the compositor had far to turn it while the head moved, and its edge came into view)
    XrTime eye_time = 0;     // (the moment the eyes above are located for)
    std::atomic<int64_t> lead_ns{0};
    bool eyes_valid = false;
    XrPosef anchor{{0, 0, 0, 1}, {0, 0, 0}};   // (where the game's camera is: the head at the last recentring, upright)
    bool anchor_valid = false;
    Issued issued[16];
    uint64_t issued_n = 0;
    int next_eye = 0;
    int64_t last_issue_ns = 0;

    // the last frame matched (tag_mu)
    std::mutex tag_mu;
    Tag tag;                 // (the newest)
    Tag tags[8];             // (the last frames', by frame number % 8: a present takes its own frame's, not the newest)
    uint64_t tag_used = 0;   // (the seq of the tag a present took)
    uint64_t took_own = 0, took_ahead = 0, took_none = 0;   // (how the presents found their tags: the log's "eyes" lines)
    int64_t took_log_ns = 0;
    std::atomic<bool> swap{false};   // (the eyes' pictures swapped: both stick clicks held and A, a test)
    uint64_t note_frame = ~0ull, note_camera = 0;   // (the draw's camera already looked at this frame)
    int64_t match_log_ns = 0;
};
Xr& xr() {
    static Xr x;
    return x;
}

bool ok(XrResult r, const char* what) {
    if (XR_SUCCEEDED(r)) return true;
    char name[XR_MAX_RESULT_STRING_SIZE] = "?";
    if (xrResultToString && xr().instance) xrResultToString(xr().instance, r, name);
    logf("xr: %s failed: %s (%d)", what, name, (int)r);
    return false;
}

XrPath path(const char* s) {
    XrPath p = XR_NULL_PATH;
    xrStringToPath(xr().instance, s, &p);
    return p;
}

int64_t now_ns() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

// ---------------------------------------------------------------------------------------------------------------------------
// the virtual SDL3 gamepad (SDL3.dll, loaded by the game: host/hos_sdl.c). SDL 3.2's ABI, declared here
struct SdlSensorDesc { int type; float rate; };
struct SdlVirtualJoystickDesc {
    uint32_t version;
    uint16_t type, padding, vendor_id, product_id, naxes, nbuttons, nballs, nhats, ntouchpads, nsensors, padding2[2];
    uint32_t button_mask, axis_mask;
    const char* name;
    const void* touchpads;
    const SdlSensorDesc* sensors;
    void* userdata;
    void (*Update)(void*);
    void (*SetPlayerIndex)(void*, int);
    bool (*Rumble)(void*, uint16_t, uint16_t);
    bool (*RumbleTriggers)(void*, uint16_t, uint16_t);
    bool (*SetLED)(void*, uint8_t, uint8_t, uint8_t);
    bool (*SendEffect)(void*, const void*, int);
    bool (*SetSensorsEnabled)(void*, bool);
    void (*Cleanup)(void*);
};
static_assert(sizeof(XrPosef) == sizeof(float) * 7 && sizeof(XrFovf) == sizeof(float) * 4, "XrCopy's pose and fov");
static_assert(sizeof(SdlVirtualJoystickDesc) == 136, "SDL_VirtualJoystickDesc (64-bit) is 136 bytes");
enum { SDL_INIT_JOYSTICK_ = 0x200, SDL_INIT_GAMEPAD_ = 0x2000, SDL_JOYSTICK_TYPE_GAMEPAD_ = 1, SDL_SENSOR_ACCEL_ = 1,
       SDL_SENSOR_GYRO_ = 2 };
enum { B_SOUTH, B_EAST, B_WEST, B_NORTH, B_BACK, B_GUIDE, B_START, B_LSTICK, B_RSTICK, B_LSHOULDER, B_RSHOULDER, B_UP, B_DOWN,
       B_LEFT, B_RIGHT, B_COUNT };
enum { AX_LX, AX_LY, AX_RX, AX_RY, AX_LT, AX_RT, AX_COUNT };

struct Sdl {
    uint32_t (*WasInit)(uint32_t) = nullptr;
    uint32_t (*Attach)(const SdlVirtualJoystickDesc*) = nullptr;
    void* (*Open)(uint32_t) = nullptr;
    bool (*SetAxis)(void*, int, int16_t) = nullptr;
    bool (*SetButton)(void*, int, bool) = nullptr;
    bool (*SendSensor)(void*, int, uint64_t, const float*, int) = nullptr;
    const char* (*GetError)() = nullptr;
    void* joy = nullptr;
    std::atomic<bool> sensors_on{false};
    // (the combinations' state: only SDL's update reads and writes them)
    int64_t menu_down_ns = 0, start_until = 0, guide_until = 0;
    bool menu_was = false, guide_sent = false, lmod = false, rmod = false;
};
Sdl& sdl() {
    static Sdl s;
    return s;
}

int16_t stick(float v) { return (int16_t)std::lround(std::clamp(v, -1.0f, 1.0f) * 32767.0f); }
// (a virtual pad's trigger is a whole axis: -32768 let go, 32767 pressed home; SDL's gamepad reads half of it above the middle.
// 0 for "let go" was a trigger half pressed: ZL and ZR held for the game whenever the pad was there, Link targeting and his
// shield up with no hand on anything)
int16_t trigger(float v) { return (int16_t)(std::lround(std::clamp(v, 0.0f, 1.0f) * 65535.0f) - 32768); }

// The controls (quest/README.md has the table). A stick's click is that stick's button until the stick is pushed while it is
// held: then the left one is Minus (-), the right one the D-pad, until the click is let go. Menu tapped is Plus (+); held half
// a second it opens or closes the Insert menu (Home). Both sticks clicked together for a second: recentred.
void pad_update(void*) {
    Sdl& s = sdl();
    if (!s.joy) return;
    Touch t;
    {
        std::lock_guard lk(xr().in_mu);
        t = xr().touch;
    }
    const int64_t now = now_ns();
    // (the headset off, or touch_controllers=0: a pad at rest. A controller lying on its trigger or grip held ZL or L for the
    // game, whose pads all count together: Link then sidestepped and would not turn with the player's own pad)
    if (!xr().running.load(std::memory_order_relaxed) || !xr().set.touch) t = Touch{};
    if (xr().set.swap_sticks) std::swap(t.lx, t.rx), std::swap(t.ly, t.ry);
    const float push = 0.55f;
    auto pushed = [&](float x, float y) { return x * x + y * y > push * push; };
    if (!t.lclick) s.lmod = false;
    else if (pushed(t.lx, t.ly)) s.lmod = true;
    if (!t.rclick) s.rmod = false;
    else if (pushed(t.rx, t.ry)) s.rmod = true;
    if (t.menu && !s.menu_was) s.menu_down_ns = now, s.guide_sent = false;
    if (t.menu && !s.guide_sent && now - s.menu_down_ns > 500000000LL) s.guide_sent = true, s.guide_until = now + 120000000LL;
    if (!t.menu && s.menu_was && !s.guide_sent) s.start_until = now + 120000000LL;
    s.menu_was = t.menu;

    bool b[B_COUNT] = {};
    b[B_SOUTH] = t.a, b[B_EAST] = t.b, b[B_WEST] = t.x, b[B_NORTH] = t.y;
    b[B_LSHOULDER] = t.lg > 0.6f, b[B_RSHOULDER] = t.rg > 0.6f;
    b[B_START] = now < s.start_until;
    b[B_GUIDE] = now < s.guide_until;
    b[B_LSTICK] = t.lclick && !s.lmod;
    b[B_RSTICK] = t.rclick && !s.rmod;
    b[B_BACK] = s.lmod;
    if (s.rmod) b[B_UP] = t.ry > 0.4f, b[B_DOWN] = t.ry < -0.4f, b[B_LEFT] = t.rx < -0.4f, b[B_RIGHT] = t.rx > 0.4f;
    for (int i = 0; i < B_COUNT; i++) s.SetButton(s.joy, i, b[i]);
    s.SetAxis(s.joy, AX_LX, s.lmod ? 0 : stick(t.lx));
    s.SetAxis(s.joy, AX_LY, s.lmod ? 0 : stick(-t.ly));   // (SDL: down positive)
    s.SetAxis(s.joy, AX_RX, s.rmod ? 0 : stick(t.rx));
    s.SetAxis(s.joy, AX_RY, s.rmod ? 0 : stick(-t.ry));
    s.SetAxis(s.joy, AX_LT, trigger(t.lt));
    s.SetAxis(s.joy, AX_RT, trigger(t.rt));
    if (s.sensors_on.load(std::memory_order_relaxed) && xr().set.gyro && t.aim_valid) {
        // (SDL's frame for a pad held in front of the player is +X right, +Y up, +Z towards the player: the aim pose's own)
        const float g[3] = {t.gyro.x, t.gyro.y, t.gyro.z};
        const float a[3] = {t.accel.x, t.accel.y, t.accel.z};
        s.SendSensor(s.joy, SDL_SENSOR_GYRO_, (uint64_t)now, g, 3);
        s.SendSensor(s.joy, SDL_SENSOR_ACCEL_, (uint64_t)now, a, 3);
    }
}
bool pad_rumble(void*, uint16_t lo, uint16_t hi) {
    xr().rumble.store((uint32_t)lo << 16 | hi, std::memory_order_relaxed);
    return true;
}
bool pad_sensors(void*, bool on) {
    sdl().sensors_on = on;
    return true;
}

// Once the game has SDL's gamepads running: the virtual pad attached and opened (tried again each second until then)
bool pad_attach() {
    Sdl& s = sdl();
    if (s.joy) return true;
    HMODULE m = GetModuleHandleA("SDL3.dll");
    if (!m) return false;
    if (!s.WasInit) {
        s.WasInit = (uint32_t (*)(uint32_t))(void*)GetProcAddress(m, "SDL_WasInit");
        s.Attach = (uint32_t (*)(const SdlVirtualJoystickDesc*))(void*)GetProcAddress(m, "SDL_AttachVirtualJoystick");
        s.Open = (void* (*)(uint32_t))(void*)GetProcAddress(m, "SDL_OpenJoystick");
        s.SetAxis = (bool (*)(void*, int, int16_t))(void*)GetProcAddress(m, "SDL_SetJoystickVirtualAxis");
        s.SetButton = (bool (*)(void*, int, bool))(void*)GetProcAddress(m, "SDL_SetJoystickVirtualButton");
        s.SendSensor = (bool (*)(void*, int, uint64_t, const float*, int))(void*)GetProcAddress(m, "SDL_SendJoystickVirtualSensorData");
        s.GetError = (const char* (*)())(void*)GetProcAddress(m, "SDL_GetError");
        if (!s.WasInit || !s.Attach || !s.Open || !s.SetAxis || !s.SetButton || !s.SendSensor) {
            logf("xr: SDL3.dll has no virtual joysticks: the Touch controllers are not a pad");
            s.WasInit = nullptr;
            return true;   // (no more tries)
        }
    }
    if (!(s.WasInit(SDL_INIT_GAMEPAD_ | SDL_INIT_JOYSTICK_) & SDL_INIT_JOYSTICK_)) return false;
    static const SdlSensorDesc sensors[2] = {{SDL_SENSOR_ACCEL_, 0.0f}, {SDL_SENSOR_GYRO_, 0.0f}};
    static SdlVirtualJoystickDesc d{};
    d.version = sizeof d;
    d.type = SDL_JOYSTICK_TYPE_GAMEPAD_;
    d.vendor_id = 0x2833;    // (Oculus VR's USB vendor id)
    d.product_id = 0x0186;
    d.naxes = AX_COUNT;
    d.nbuttons = B_COUNT;
    d.nsensors = xr().set.gyro ? 2 : 0;
    d.button_mask = (1u << B_COUNT) - 1;
    d.axis_mask = (1u << AX_COUNT) - 1;
    d.name = "Meta Quest Touch (Hookshot VR)";
    d.sensors = sensors;
    d.Update = pad_update;
    d.Rumble = pad_rumble;
    d.SetSensorsEnabled = pad_sensors;
    const uint32_t id = s.Attach(&d);
    if (!id) {
        logf("xr: the virtual pad could not be attached: %s", s.GetError ? s.GetError() : "?");
        s.WasInit = nullptr;
        return true;
    }
    s.joy = s.Open(id);
    logf(s.joy ? "xr: the Touch controllers are a gamepad now (SDL virtual joystick %u%s)" : "xr: the virtual pad %u would not open",
         id, xr().set.gyro ? ", the right controller its gyroscope" : "");
    return true;
}

// ---------------------------------------------------------------------------------------------------------------------------
// the controllers' actions
bool make_actions() {
    Xr& x = xr();
    XrActionSetCreateInfo si{XR_TYPE_ACTION_SET_CREATE_INFO};
    strcpy(si.actionSetName, "gameplay");
    strcpy(si.localizedActionSetName, "Gameplay");
    if (!ok(xrCreateActionSet(x.instance, &si, &x.set_), "xrCreateActionSet")) return false;
    auto make = [&](XrAction& a, const char* name, XrActionType type) {
        XrActionCreateInfo ci{XR_TYPE_ACTION_CREATE_INFO};
        ci.actionType = type;
        strcpy(ci.actionName, name);
        strcpy(ci.localizedActionName, name);
        return ok(xrCreateAction(x.set_, &ci, &a), name);
    };
    bool good = make(x.a_move, "move", XR_ACTION_TYPE_VECTOR2F_INPUT) && make(x.a_look, "look", XR_ACTION_TYPE_VECTOR2F_INPUT) &&
                make(x.a_a, "button_a", XR_ACTION_TYPE_BOOLEAN_INPUT) && make(x.a_b, "button_b", XR_ACTION_TYPE_BOOLEAN_INPUT) &&
                make(x.a_x, "button_x", XR_ACTION_TYPE_BOOLEAN_INPUT) && make(x.a_y, "button_y", XR_ACTION_TYPE_BOOLEAN_INPUT) &&
                make(x.a_menu, "menu", XR_ACTION_TYPE_BOOLEAN_INPUT) &&
                make(x.a_lt, "trigger_left", XR_ACTION_TYPE_FLOAT_INPUT) && make(x.a_rt, "trigger_right", XR_ACTION_TYPE_FLOAT_INPUT) &&
                make(x.a_lg, "grip_left", XR_ACTION_TYPE_FLOAT_INPUT) && make(x.a_rg, "grip_right", XR_ACTION_TYPE_FLOAT_INPUT) &&
                make(x.a_lclick, "stick_click_left", XR_ACTION_TYPE_BOOLEAN_INPUT) &&
                make(x.a_rclick, "stick_click_right", XR_ACTION_TYPE_BOOLEAN_INPUT) &&
                make(x.a_aim, "aim_right", XR_ACTION_TYPE_POSE_INPUT) &&
                make(x.a_haptic_l, "rumble_left", XR_ACTION_TYPE_VIBRATION_OUTPUT) &&
                make(x.a_haptic_r, "rumble_right", XR_ACTION_TYPE_VIBRATION_OUTPUT);
    if (!good) return false;
    const std::pair<XrAction, const char*> touch[] = {
        {x.a_move, "/user/hand/left/input/thumbstick"}, {x.a_look, "/user/hand/right/input/thumbstick"},
        {x.a_a, "/user/hand/right/input/a/click"}, {x.a_b, "/user/hand/right/input/b/click"},
        {x.a_x, "/user/hand/left/input/x/click"}, {x.a_y, "/user/hand/left/input/y/click"},
        {x.a_menu, "/user/hand/left/input/menu/click"},
        {x.a_lt, "/user/hand/left/input/trigger/value"}, {x.a_rt, "/user/hand/right/input/trigger/value"},
        {x.a_lg, "/user/hand/left/input/squeeze/value"}, {x.a_rg, "/user/hand/right/input/squeeze/value"},
        {x.a_lclick, "/user/hand/left/input/thumbstick/click"}, {x.a_rclick, "/user/hand/right/input/thumbstick/click"},
        {x.a_aim, "/user/hand/right/input/aim/pose"},
        {x.a_haptic_l, "/user/hand/left/output/haptic"}, {x.a_haptic_r, "/user/hand/right/output/haptic"}};
    std::vector<XrActionSuggestedBinding> b;
    for (const auto& [a, p] : touch) b.push_back({a, path(p)});
    XrInteractionProfileSuggestedBinding sb{XR_TYPE_INTERACTION_PROFILE_SUGGESTED_BINDING};
    sb.interactionProfile = path("/interaction_profiles/oculus/touch_controller");
    sb.suggestedBindings = b.data();
    sb.countSuggestedBindings = (uint32_t)b.size();
    if (!ok(xrSuggestInteractionProfileBindings(x.instance, &sb), "the Touch controllers' bindings")) return false;
    XrActionSpaceCreateInfo ai{XR_TYPE_ACTION_SPACE_CREATE_INFO};
    ai.action = x.a_aim;
    ai.poseInActionSpace.orientation.w = 1;
    if (!ok(xrCreateActionSpace(x.session, &ai, &x.aim_space), "xrCreateActionSpace")) return false;
    XrSessionActionSetsAttachInfo at{XR_TYPE_SESSION_ACTION_SETS_ATTACH_INFO};
    at.countActionSets = 1;
    at.actionSets = &x.set_;
    return ok(xrAttachSessionActionSets(x.session, &at), "xrAttachSessionActionSets");
}

void read_controllers(XrTime when) {
    Xr& x = xr();
    XrActiveActionSet active{x.set_, XR_NULL_PATH};
    XrActionsSyncInfo sy{XR_TYPE_ACTIONS_SYNC_INFO};
    sy.countActiveActionSets = 1;
    sy.activeActionSets = &active;
    Touch t;
    if (x.set_ && x.state == XR_SESSION_STATE_FOCUSED && XR_SUCCEEDED(xrSyncActions(x.session, &sy))) {
        auto boolean = [&](XrAction a) {
            XrActionStateGetInfo gi{XR_TYPE_ACTION_STATE_GET_INFO};
            gi.action = a;
            XrActionStateBoolean s{XR_TYPE_ACTION_STATE_BOOLEAN};
            return XR_SUCCEEDED(xrGetActionStateBoolean(x.session, &gi, &s)) && s.isActive && s.currentState;
        };
        auto value = [&](XrAction a) {
            XrActionStateGetInfo gi{XR_TYPE_ACTION_STATE_GET_INFO};
            gi.action = a;
            XrActionStateFloat s{XR_TYPE_ACTION_STATE_FLOAT};
            return XR_SUCCEEDED(xrGetActionStateFloat(x.session, &gi, &s)) && s.isActive ? s.currentState : 0.0f;
        };
        auto vec = [&](XrAction a, float& ox, float& oy) {
            XrActionStateGetInfo gi{XR_TYPE_ACTION_STATE_GET_INFO};
            gi.action = a;
            XrActionStateVector2f s{XR_TYPE_ACTION_STATE_VECTOR2F};
            if (XR_SUCCEEDED(xrGetActionStateVector2f(x.session, &gi, &s)) && s.isActive) ox = s.currentState.x, oy = s.currentState.y;
        };
        vec(x.a_move, t.lx, t.ly);
        vec(x.a_look, t.rx, t.ry);
        t.a = boolean(x.a_a), t.b = boolean(x.a_b), t.x = boolean(x.a_x), t.y = boolean(x.a_y), t.menu = boolean(x.a_menu);
        t.lclick = boolean(x.a_lclick), t.rclick = boolean(x.a_rclick);
        t.lt = value(x.a_lt), t.rt = value(x.a_rt), t.lg = value(x.a_lg), t.rg = value(x.a_rg);
        XrSpaceVelocity vel{XR_TYPE_SPACE_VELOCITY};
        XrSpaceLocation loc{XR_TYPE_SPACE_LOCATION};
        loc.next = &vel;
        if (XR_SUCCEEDED(xrLocateSpace(x.aim_space, x.local, when, &loc)) &&
            (loc.locationFlags & XR_SPACE_LOCATION_ORIENTATION_VALID_BIT)) {
            const XrQuaternionf inv = qconj(loc.pose.orientation);
            if (vel.velocityFlags & XR_SPACE_VELOCITY_ANGULAR_VALID_BIT)
                t.gyro = rotate(inv, {vel.angularVelocity.x, vel.angularVelocity.y, vel.angularVelocity.z});
            t.accel = rotate(inv, {0.0f, 9.80665f, 0.0f});
            t.aim_valid = true;
        }
    }
    {
        std::lock_guard lk(x.in_mu);
        x.touch = t;
    }
    if (!x.set_) return;
    static bool buzzing = false;   // (the game's rumble on both hands)
    const uint32_t r = x.rumble.load(std::memory_order_relaxed);
    const float amp = std::max(r >> 16, r & 0xffff) / 65535.0f;
    for (XrAction a : {x.a_haptic_l, x.a_haptic_r}) {
        XrHapticActionInfo hi{XR_TYPE_HAPTIC_ACTION_INFO};
        hi.action = a;
        if (amp > 0.02f) {
            XrHapticVibration v{XR_TYPE_HAPTIC_VIBRATION};
            v.amplitude = amp;
            v.duration = 50000000;   // (50 ms, renewed each frame while the game rumbles)
            v.frequency = XR_FREQUENCY_UNSPECIFIED;
            xrApplyHapticFeedback(x.session, &hi, (const XrHapticBaseHeader*)&v);
        } else if (buzzing) {
            xrStopHapticFeedback(x.session, &hi);
        }
    }
    buzzing = amp > 0.02f;
}

// the eyes where they will be when this frame is shown: what the next views handed to the game are made from
void locate_eyes(XrTime when) {
    Xr& x = xr();
    XrViewLocateInfo li{XR_TYPE_VIEW_LOCATE_INFO};
    li.viewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
    const XrTime ahead = when + (x.set.lead || x.set.pace ? x.lead_ns.load(std::memory_order_relaxed) : 0);
    li.displayTime = ahead;
    li.space = x.local;
    XrViewState vs{XR_TYPE_VIEW_STATE};
    XrView v[2] = {{XR_TYPE_VIEW}, {XR_TYPE_VIEW}};
    uint32_t n = 0;
    const bool good = XR_SUCCEEDED(xrLocateViews(x.session, &li, &vs, 2, &n, v)) && n == 2 &&
                      (vs.viewStateFlags & XR_VIEW_STATE_POSITION_VALID_BIT) && (vs.viewStateFlags & XR_VIEW_STATE_ORIENTATION_VALID_BIT);
    std::lock_guard lk(x.in_mu);
    x.eyes_valid = good;
    if (good) {
        x.eye_pose[0] = v[0].pose, x.eye_pose[1] = v[1].pose;
        x.eye_time = ahead;
        static bool said = false;
        if (!said) {
            said = true;
            logf("xr: the headset's own fields of view: left %.1f..%.1f x %.1f..%.1f deg, right %.1f..%.1f x %.1f..%.1f deg",
                 v[0].fov.angleLeft * 57.2958f, v[0].fov.angleRight * 57.2958f, v[0].fov.angleDown * 57.2958f, v[0].fov.angleUp * 57.2958f,
                 v[1].fov.angleLeft * 57.2958f, v[1].fov.angleRight * 57.2958f, v[1].fov.angleDown * 57.2958f, v[1].fov.angleUp * 57.2958f);
        }
        x.eye_fov[0] = v[0].fov, x.eye_fov[1] = v[1].fov;
    }
}

// ---------------------------------------------------------------------------------------------------------------------------
// recentring: the screen in front of the head, upright, turned only about the vertical; the stereo anchor at the head
void place_screen(XrTime when) {
    Xr& x = xr();
    XrSpaceLocation loc{XR_TYPE_SPACE_LOCATION};
    if (XR_FAILED(xrLocateSpace(x.view, x.local, when, &loc)) ||
        (loc.locationFlags & (XR_SPACE_LOCATION_POSITION_VALID_BIT | XR_SPACE_LOCATION_ORIENTATION_VALID_BIT)) !=
            (XR_SPACE_LOCATION_POSITION_VALID_BIT | XR_SPACE_LOCATION_ORIENTATION_VALID_BIT))
        return;   // (tried again next frame)
    const XrQuaternionf q = yaw_of(loc.pose.orientation);
    const V3 off = rotate(q, {0, x.set.height, -x.set.distance});
    {
        std::lock_guard lk(x.mu);
        x.screen.orientation = q;
        x.screen.position = {loc.pose.position.x + off.x, loc.pose.position.y + off.y, loc.pose.position.z + off.z};
    }
    {
        std::lock_guard lk(x.in_mu);
        x.anchor.orientation = q;
        x.anchor.position = loc.pose.position;
        x.anchor_valid = true;
    }
    x.recenter = false;
    logf("xr: recentred (the screen %.1f m in front, %.1f m wide%s)", x.set.distance, x.set.width,
         x.set.stereo ? "; the game's camera at the head" : "");
}

void handle_events() {
    Xr& x = xr();
    XrEventDataBuffer e{XR_TYPE_EVENT_DATA_BUFFER};
    while (xrPollEvent(x.instance, &e) == XR_SUCCESS) {
        if (e.type == XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED) {
            const auto& s = *(const XrEventDataSessionStateChanged*)&e;
            x.state = s.state;
            logf("xr: session state %d", (int)s.state);
            if (s.state == XR_SESSION_STATE_READY) {
                XrSessionBeginInfo bi{XR_TYPE_SESSION_BEGIN_INFO};
                bi.primaryViewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
                if (ok(xrBeginSession(x.session, &bi), "xrBeginSession")) x.running = true, x.recenter = true;
            } else if (s.state == XR_SESSION_STATE_STOPPING) {
                x.running = false;
                ok(xrEndSession(x.session), "xrEndSession");
            } else if (s.state == XR_SESSION_STATE_EXITING || s.state == XR_SESSION_STATE_LOSS_PENDING) {
                x.running = false, x.dead = true;
            }
        } else if (e.type == XR_TYPE_EVENT_DATA_REFERENCE_SPACE_CHANGE_PENDING) {
            x.recenter = true;   // (the headset's own recentring: the screen and the camera follow)
        } else if (e.type == XR_TYPE_EVENT_DATA_INSTANCE_LOSS_PENDING) {
            x.running = false, x.dead = true;
        }
        e = {XR_TYPE_EVENT_DATA_BUFFER};
    }
}

XrPosef moved_by_camera(const XrPosef& eye, const float* cam_e, const float* cam_ref, const XrPosef& anchor, float scale);
XrPosef compose(const XrPosef& a, const XrPosef& b);
XrPosef inverse(const XrPosef& a);
XrQuaternionf quat_of(const V3 c[3]);

// The game's frames come slower than the headset's (a pair of eyes some 35 times a second, shown 72 to 120 times): a camera that
// turns would show each picture two or three times in one place and then jump, seen as a second image trailing the first. So
// between the game's frames the pictures go on as the camera was moving: its motion from the picture before to the last one
// (M = C_prev^-1 C_cur), carried on for the part s of a frame's time gone since (0 to 1), and the pictures' poses moved the other
// way by it: A * (M^s)^-1 * A^-1 before each. A cut or a stop of more than a tenth of a second: nothing. (x.mu held)
bool camera_carry(const XrPosef& anchor, float scale, XrPosef& out) {
    Xr& x = xr();
    if (!x.set.smooth || !x.cam_prev_ns || !x.cam_cur_ns) return false;
    const int64_t span = x.cam_cur_ns - x.cam_prev_ns, since = now_ns() - x.cam_cur_ns;
    if (span < 8000000 || span > 100000000 || since < 0 || since > 100000000) return false;
    const float s = std::min(1.0f, (float)since / (float)span);
    V3 c[4], p[4];
    std::memcpy(c, x.cam_cur, sizeof c), std::memcpy(p, x.cam_prev, sizeof p);
    V3 cols[3];
    for (int i = 0; i < 3; i++) cols[i] = {dot(p[0], c[i]), dot(p[1], c[i]), dot(p[2], c[i])};
    XrQuaternionf q = quat_of(cols);
    if (q.w < 0) q = {-q.x, -q.y, -q.z, -q.w};
    const V3 d = sub(c[3], p[3]);
    V3 t = mul(V3{dot(p[0], d), dot(p[1], d), dot(p[2], d)}, 1.0f / scale);
    const float half = std::acos(std::min(1.0f, q.w));
    if (half > 0.26f || dot(t, t) > 1.0f) return false;   // (over 30 degrees or a metre in one frame: a cut, not a motion)
    XrQuaternionf qs{0, 0, 0, 1};
    if (half > 1e-5f) {
        const float k = std::sin(s * half) / std::sin(half);
        qs = {q.x * k, q.y * k, q.z * k, std::cos(s * half)};
    }
    // (the turn only, and about each eye where it is: a picture's place told elsewhere than its eye's is a thing the headset's
    // compositor may act on, and the camera's travel in a frame is small beside what is in view)
    const XrQuaternionf turn = qmul(qmul(anchor.orientation, qconj(qs)), qconj(anchor.orientation));
    out = {turn, {0, 0, 0}};
    return true;
}

// The VR combinations, on any controller the game reads (an Xbox pad, a DualSense, a Switch 2 controller, the Touch controllers'
// virtual pad: host->pad_raw, by position): both sticks pressed in for a second, recentred; both sticks in and the bottom face
// button (A on an Xbox pad, B on a Nintendo one), the eyes swapped. F8 on the keyboard recentres too.
void combos() {
    static int64_t both_ns = 0;
    static bool south_was = false, f8_was = false;
    const uint32_t raw = host && host->pad_raw ? host->pad_raw() : 0;
    const bool l = raw >> HOS_PAD_LSTICK & 1, r = raw >> HOS_PAD_RSTICK & 1, south = raw >> HOS_PAD_SOUTH & 1;
    const int64_t now = now_ns();
    (void)south, (void)south_was;   // (the eye swap that was here, a test, is gone: pressed by accident while recentring, it
                                    // left the eyes swapped)
    if (l && r) {
        if (!both_ns) both_ns = now;
        else if (both_ns > 0 && now - both_ns > 1000000000LL) xr().recenter = true, both_ns = -1;
    } else {
        both_ns = 0;
    }
    {
        static int64_t l_down = 0;
        static bool l_was = false, f9_was = false, with_r = false;
        if (l && !l_was) l_down = now, with_r = false;
        if (l && r) with_r = true;
        bool toggle = !l && l_was && !with_r && now - l_down < 350000000LL;   // (let go within a third of a second)
        l_was = l;
        const bool f9 = (GetAsyncKeyState(VK_F9) & 0x8000) != 0;
        if (f9 && !f9_was) toggle = true;
        f9_was = f9;
        if (toggle && xr().set.stereo) {
            const int cur = xr().first_person.load();
            const int next = (cur < 0 ? xr().set.first_person != 0 : cur != 0) ? 0 : 1;
            xr().first_person = next;
            xr_opt[XO_FIRST_PERSON] = next;   // (the VR tab's row shows it; the file keeps what it has)
            logf("xr: first person %s", next ? "on: your eyes at Link's" : "off: the game's camera");
        }
    }
    const bool f8 = (GetAsyncKeyState(VK_F8) & 0x8000) != 0;
    if (f8 && !f8_was) xr().recenter = true;
    f8_was = f8;
}

// hookshot_vr.ini read again when it changes (once a second): the screen, the HUD, the separation, the scale and the rest take
// effect while the game runs; vr= and stereo= decide how it started and stay
void reload_settings() {
    static int64_t next = 0;
    if (now_ns() < next) return;
    next = now_ns() + 1000000000LL;
    const uint64_t t = ini_time();
    if (!t) return;
    const uint64_t last = ini_seen.load(std::memory_order_relaxed);
    if (!last) {
        ini_seen.store(t, std::memory_order_relaxed);
        return;
    }
    if (last == t) return;
    ini_seen.store(t, std::memory_order_relaxed);
    Settings s = load_settings();
    Xr& x = xr();
    {   // (the menu's rows follow the file; VR itself as the file has it, not as RX_VR made this run)
        const int vr_row = xr_opt[XO_VR], stereo_row = xr_opt[XO_STEREO];
        opts_from(s);
        if (getenv("RX_VR")) xr_opt[XO_VR] = vr_row;
        if (getenv("RX_VR_STEREO")) xr_opt[XO_STEREO] = stereo_row;
    }
    x.first_person = -1;   // (as the file says)
    s.vr = x.set.vr, s.stereo = x.set.stereo, s.gyro = x.set.gyro;
    x.set = s;
    if (env_on("RX_VR_TEST")) logf("xr: test: separation %.2f, yaw %.1f, pitch %.1f", s.separation, s.test_yaw, s.test_pitch);
    x.recenter = true;   // (the screen and the HUD where the new numbers put them)
    logf("xr: hookshot_vr.ini read again: separation %.2f, world scale %.2f, horizon lock %d, head motion %d, HUD %d", s.separation,
         s.world_scale, s.horizon_lock, s.head_motion, s.hud);
}

void xr_thread() {
    Xr& x = xr();
    int64_t next_pad_try = 0;
    while (!x.dead) {
        reload_settings();
        handle_events();
        // (the pad once the headset shows the game: one attached before, at rest, would be the newest pad, and the Motion
        // controls of a real controller would wait for it to be used)
        if (x.running && now_ns() >= next_pad_try) next_pad_try = pad_attach() ? INT64_MAX : now_ns() + 1000000000LL;
        if (!x.running) {
            Sleep(20);
            continue;
        }
        XrFrameState fs{XR_TYPE_FRAME_STATE};
        if (!ok(xrWaitFrame(x.session, nullptr, &fs), "xrWaitFrame")) {
            Sleep(20);
            continue;
        }
        read_controllers(fs.predictedDisplayTime);
        combos();
        if (x.recenter) place_screen(fs.predictedDisplayTime);
        {   // (the frames' own spacing, as the runtime gives them: a pair started now is shown one frame on, and its eyes are
            // located for then)
            static XrTime before = 0;
            static int64_t spacing = 0;
            const int64_t d = before ? fs.predictedDisplayTime - before : 0;
            before = fs.predictedDisplayTime;
            if (d > 5000000 && d < 120000000) spacing = spacing ? (spacing * 3 + d) / 4 : d;
            if (x.set.pace && !x.set.lead) x.lead_ns.store(spacing, std::memory_order_relaxed);
        }
        if (x.set.stereo) locate_eyes(fs.predictedDisplayTime);
        // (stereo_pace) the frame begun is the game's next pair of eyes: waited for here, a twentieth of a second at most (a
        // load, a hitch: the last pair again), so the headset is given each pair once, at the game's own rate. Nothing held
        // while waiting: the pair's own copies and releases take the queue's lock
        static uint64_t pair_seen = 0;
        static uint64_t paced = 0, late = 0;
        static int64_t pace_said = 0;
        if (x.set.pace && x.set.stereo && now_ns() - x.stereo_ns.load(std::memory_order_relaxed) < 300000000LL) {
            std::unique_lock pk(x.pair_mu);
            const bool came = x.pair_cv.wait_for(pk, std::chrono::milliseconds(40), [&] { return x.pair_gen != pair_seen; });
            pair_seen = x.pair_gen;
            (came ? paced : late)++;
            pk.unlock();
            if (now_ns() > pace_said) {
                pace_said = now_ns() + 10000000000LL;
                logf("xr: stereo: copies left out for want of a free image: %llu so far", (unsigned long long)x.not_ready.load());
                logf("xr: stereo: frames given to the headset with a new pair %llu, without one in 40 ms %llu",
                     (unsigned long long)paced, (unsigned long long)late);
            }
        }
        // (the frame begun only now, with its pair in hand, and ended at once: the runtime takes the time from begin to end for
        // what the application needs to draw a frame, and one begun before the wait for the pair looked slow enough to be
        // given half the frames)
        std::lock_guard qk(dev.present_mu);
        if (!ok(xrBeginFrame(x.session, nullptr), "xrBeginFrame")) continue;
        std::lock_guard lk(x.mu);
        XrCompositionLayerQuad quad{XR_TYPE_COMPOSITION_LAYER_QUAD};
        XrCompositionLayerProjection proj{XR_TYPE_COMPOSITION_LAYER_PROJECTION};
        XrCompositionLayerProjectionView pv[2] = {{XR_TYPE_COMPOSITION_LAYER_PROJECTION_VIEW},
                                                  {XR_TYPE_COMPOSITION_LAYER_PROJECTION_VIEW}};
        const XrCompositionLayerBaseHeader* layers[8] = {};
        XrCompositionLayerQuad hud_part[5];
        XrCompositionLayerQuad hud_quad{XR_TYPE_COMPOSITION_LAYER_QUAD};
        XrFrameEndInfo ei{XR_TYPE_FRAME_END_INFO};
        ei.displayTime = fs.predictedDisplayTime;
        ei.environmentBlendMode = XR_ENVIRONMENT_BLEND_MODE_OPAQUE;
        // (stereo while both eyes have pictures and one came in the last half second; the screen otherwise)
        const bool stereo = x.eyes[0].released_once && x.eyes[1].released_once &&
                            now_ns() - x.stereo_ns.load(std::memory_order_relaxed) < 500000000LL;
        if (fs.shouldRender && stereo) {
            XrPosef anchor;
            {
                std::lock_guard ik(x.in_mu);
                anchor = x.anchor;
            }
            const int ref = x.eyes[0].seq > x.eyes[1].seq ? 0 : 1;
            {   // (how late this pair is for the frame it is shown in: the lead for the next views follows it, a seventh of the
                // way each new pair, within 0 to 150 ms)
                static uint64_t lead_seq = 0;
                static int64_t lead_said = 0;
                const Chain& r = x.eyes[ref];
                if (r.seq != lead_seq && r.time) {
                    lead_seq = r.seq;
                    const int64_t late = fs.predictedDisplayTime - r.time;
                    int64_t lead = x.lead_ns.load(std::memory_order_relaxed);
                    if (x.set.lead && late > -200000000LL && late < 400000000LL) lead += late / 20;
                    lead = std::clamp<int64_t>(lead, 0, 150000000LL);
                    if (x.set.lead) x.lead_ns.store(lead, std::memory_order_relaxed);
                    if (now_ns() > lead_said) {
                        lead_said = now_ns() + 10000000000LL;
                        logf("xr: stereo: the head is predicted %.0f ms ahead; the last pair was %.0f ms off its frame's time",
                             lead / 1e6, late / 1e6);
                    }
                }
            }
            XrPosef carry;
            const bool carried = camera_carry(anchor, x.set.world_scale, carry);
            for (int e = 0; e < 2; e++) {
                const Chain& c = x.eyes[e];
                pv[e].pose = e == ref ? c.pose : moved_by_camera(c.pose, c.cam, x.eyes[ref].cam, anchor, x.set.world_scale);
                if (carried) pv[e].pose.orientation = qmul(carry.orientation, pv[e].pose.orientation);
                {   // (the log's "shown" line: what an odd frame was)
                    auto angle = [](const XrQuaternionf& a, const XrQuaternionf& b) {
                        const XrQuaternionf d = qmul(qconj(a), b);
                        return 2.0f * std::acos(std::min(1.0f, std::fabs(d.w))) * 57.2958f;
                    };
                    static float max_moved = 0, max_head = 0, max_roll = 0;
                    static uint64_t frames = 0, split = 0, big_moved = 0, big_head = 0;
                    static int64_t said = 0;
                    XrPosef head;
                    {
                        std::lock_guard ik(x.in_mu);
                        head = x.eye_pose[e];
                    }
                    const float moved = angle(c.pose.orientation, pv[e].pose.orientation);   // (turned for the camera's motion)
                    const float off = angle(pv[e].pose.orientation, head.orientation);        // (left for the compositor to turn)
                    const V3 up_shown = rotate(pv[e].pose.orientation, {0, 1, 0}), up_head = rotate(head.orientation, {0, 1, 0});
                    const V3 right_head = rotate(head.orientation, {1, 0, 0});
                    const float roll = std::asin(std::clamp(dot(up_shown, right_head), -1.0f, 1.0f)) * 57.2958f;
                    (void)up_head;
                    max_moved = std::max(max_moved, moved), max_head = std::max(max_head, off), max_roll = std::max(max_roll, std::fabs(roll));
                    if (moved > 2.0f) big_moved++;
                    if (off > 6.0f) big_head++;
                    if (e == 1) {
                        frames++;
                        const uint64_t a = x.eyes[0].seq, b = x.eyes[1].seq;
                        if ((a > b ? a - b : b - a) != 1) split++;
                        if (now_ns() > said) {
                            said = now_ns() + 10000000000LL;
                            logf("xr: stereo: pairs kept whole: %llu seconds without their first left out, %llu firsts drawn over, so far",
                                 (unsigned long long)x.orphans.load(), (unsigned long long)x.redrawn.load());
                            logf("xr: stereo: shown in 10 s: %llu frames, %llu with eyes of two pairs, %llu eye pictures shown alone so far; "
                                 "turned for the camera's motion up to %.1f deg (%llu over 2), off the head up to %.1f deg (%llu over 6), "
                                 "rolled against the head up to %.1f deg", (unsigned long long)frames, (unsigned long long)split,
                                 (unsigned long long)x.alone.load(), max_moved, (unsigned long long)big_moved, max_head,
                                 (unsigned long long)big_head, max_roll);
                            frames = split = big_moved = big_head = 0, max_moved = max_head = max_roll = 0;
                        }
                    }
                }
                pv[e].fov = c.fov;
                pv[e].subImage.swapchain = c.sc;
                pv[e].subImage.imageRect = {{0, 0}, {(int32_t)c.w, (int32_t)c.h}};
            }
            proj.space = x.local;
            proj.viewCount = 2;
            proj.views = pv;
            layers[0] = (const XrCompositionLayerBaseHeader*)&proj;
            ei.layerCount = 1;
            // (the HUD, pinned to the view: in front, a little low, its transparent parts showing the world)
            if (x.hud_chain.released_once && now_ns() - x.hud_ns.load(std::memory_order_relaxed) < 250000000LL) {
                const Chain& c = x.hud_chain;
                const float W = x.set.hud_width, H = x.set.hud_width * c.h / std::max(1u, c.w);
                auto part = [&](XrCompositionLayerQuad& q, XrSpace space, int32_t ix, int32_t iy, int32_t iw, int32_t ih) {
                    q = {XR_TYPE_COMPOSITION_LAYER_QUAD};
                    q.layerFlags = XR_COMPOSITION_LAYER_BLEND_TEXTURE_SOURCE_ALPHA_BIT;
                    q.space = space;
                    q.eyeVisibility = XR_EYE_VISIBILITY_BOTH;
                    q.subImage.swapchain = c.sc;
                    q.subImage.imageRect = {{ix, iy}, {iw, ih}};
                    q.pose.orientation = {0, 0, 0, 1};
                };
                const float mid = std::clamp(x.set.hud_center, 0.05f, 0.9f);
                const int32_t w = (int32_t)c.w, h = (int32_t)c.h;
                const int32_t x0 = (int32_t)(w * (1 - mid) / 2), y0 = (int32_t)(h * (1 - mid) / 2), x1 = w - x0, y1 = h - y0;
                if (!x.set.hud_aim || x0 < 1 || y0 < 1) {   // (all of it on the panel)
                    part(hud_part[0], x.view, 0, 0, w, h);
                    hud_part[0].pose.position = {0, x.set.hud_height, -x.set.hud_distance};
                    hud_part[0].size = {W, H};
                    layers[ei.layerCount++] = (const XrCompositionLayerBaseHeader*)&hud_part[0];
                } else {
                    // the panel around the middle: the bands above and below it, and the pieces at its sides
                    const float fx = (float)x0 / w, fy = (float)y0 / h;   // (a side band's share of the width, a top band's of the height)
                    const V3 at{0, x.set.hud_height, -x.set.hud_distance};
                    part(hud_part[0], x.view, 0, 0, w, y0);
                    hud_part[0].pose.position = {at.x, at.y + H / 2 - H * fy / 2, at.z};
                    hud_part[0].size = {W, H * fy};
                    part(hud_part[1], x.view, 0, y1, w, h - y1);
                    hud_part[1].pose.position = {at.x, at.y - H / 2 + H * fy / 2, at.z};
                    hud_part[1].size = {W, H * fy};
                    part(hud_part[2], x.view, 0, y0, x0, y1 - y0);
                    hud_part[2].pose.position = {at.x - W / 2 + W * fx / 2, at.y, at.z};
                    hud_part[2].size = {W * fx, H * (1 - 2 * fy)};
                    part(hud_part[3], x.view, x1, y0, w - x1, y1 - y0);
                    hud_part[3].pose.position = {at.x + W / 2 - W * fx / 2, at.y, at.z};
                    hud_part[3].size = {W * fx, H * (1 - 2 * fy)};
                    // the middle where the game aims: straight ahead of the game's camera (the anchor), tilted as the camera is
                    // (the tilt the horizon lock keeps out of the view), far off, at the size the game's own picture gives it
                    const float pitch = x.game_pitch.load(std::memory_order_relaxed);
                    const XrQuaternionf tilt{std::sin(pitch / 2), 0, 0, std::cos(pitch / 2)};
                    const XrQuaternionf aim = qmul(anchor.orientation, tilt);
                    const float D = std::max(2.0f, x.set.hud_aim_distance);
                    const V3 ahead = add(v3(anchor.position), rotate(aim, {0, 0, -D}));
                    part(hud_part[4], x.local, x0, y0, x1 - x0, y1 - y0);
                    hud_part[4].pose = {aim, {ahead.x, ahead.y, ahead.z}};
                    hud_part[4].size = {2 * D * x.game_tan_x * (float)(x1 - x0) / w, 2 * D * x.game_tan_y * (float)(y1 - y0) / h};
                    for (int k = 0; k < 5; k++) layers[ei.layerCount++] = (const XrCompositionLayerBaseHeader*)&hud_part[k];
                }
            }
        } else if (fs.shouldRender && x.screen_chain.released_once) {
            const Chain& c = x.screen_chain;
            quad.space = x.local;
            quad.eyeVisibility = XR_EYE_VISIBILITY_BOTH;
            quad.subImage.swapchain = c.sc;
            quad.subImage.imageRect = {{0, 0}, {(int32_t)c.w, (int32_t)c.h}};
            quad.pose = x.screen;
            quad.size = {x.set.width, x.set.width * c.h / std::max(1u, c.w)};
            layers[0] = (const XrCompositionLayerBaseHeader*)&quad;
            ei.layerCount = 1;
        }
        ei.layers = ei.layerCount ? layers : nullptr;
        ok(xrEndFrame(x.session, &ei), "xrEndFrame");
        {   // (this frame given: the game may start its next pair, which is drawn while the runtime waits for the next frame's
            // time, and is there when it comes. One pair to a frame, neither waiting for the other's work to end)
            std::lock_guard ok_(x.open_mu);
            x.open_gen++;
        }
        x.open_cv.notify_all();
        static int64_t diag_ns;
        static uint64_t diag_updates[2];
        if (ei.layers == (const XrCompositionLayerBaseHeader* const*)layers && layers[0] == (const XrCompositionLayerBaseHeader*)&proj &&
            now_ns() >= diag_ns) {
            diag_ns = now_ns() + 2000000000LL;
            auto yaw_pitch = [](const XrQuaternionf& q, float& yaw, float& pitch) {
                const V3 f = rotate(q, {0, 0, -1});
                yaw = std::atan2(-f.x, -f.z) * 57.2958f, pitch = std::asin(std::clamp(f.y, -1.0f, 1.0f)) * 57.2958f;
            };
            XrPosef head[2];
            {
                std::lock_guard ik(x.in_mu);
                head[0] = x.eye_pose[0], head[1] = x.eye_pose[1];
            }
            char line[512];
            int k = snprintf(line, sizeof line, "xr: eyes:");
            for (int e = 0; e < 2; e++) {
                float dy, dp, sy, sp, hy, hp;
                yaw_pitch(x.eyes[e].pose.orientation, dy, dp);
                yaw_pitch(pv[e].pose.orientation, sy, sp);
                yaw_pitch(head[e].orientation, hy, hp);
                const V3 d = sub(v3(pv[e].pose.position), v3(head[e].position));
                k += snprintf(line + k, sizeof line - k,
                              " %s %llu pictures in 2 s, %.0f ms old, drawn yaw %.1f pitch %.1f, shown yaw %.1f pitch %.1f, head yaw %.1f "
                              "pitch %.1f, shown %.3f %.3f %.3f m from the eye, fov %.1f..%.1f;",
                              e ? "right" : "left", (unsigned long long)(x.eyes[e].updates - diag_updates[e]),
                              (now_ns() - x.eyes[e].at_ns) / 1e6, dy, dp, sy, sp, hy, hp, d.x, d.y, d.z,
                              pv[e].fov.angleLeft * 57.2958f, pv[e].fov.angleRight * 57.2958f);
                diag_updates[e] = x.eyes[e].updates;
            }
            logf("%s", line);
        }
    }
    logf("xr: the session ended: the game goes on on the desktop");
    x.thread_done = true;
}

// An eye's picture drawn with another frame's game camera than the newer eye's: its pose in the headset's space moved by the
// camera's motion from its frame to the newer one, so that both eyes show one world (the game's camera at the anchor as of the
// newer frame). pose = A * (C_ref^-1 C_e, its translation / world_scale) * A^-1 * E
XrQuaternionf quat_of(const V3 c[3]) {   // (a rotation matrix given by its columns)
    const float m00 = c[0].x, m11 = c[1].y, m22 = c[2].z, tr = m00 + m11 + m22;
    if (tr > 0) {
        const float s = std::sqrt(tr + 1.0f) * 2;
        return {(c[1].z - c[2].y) / s, (c[2].x - c[0].z) / s, (c[0].y - c[1].x) / s, 0.25f * s};
    }
    if (m00 > m11 && m00 > m22) {
        const float s = std::sqrt(1.0f + m00 - m11 - m22) * 2;
        return {0.25f * s, (c[1].x + c[0].y) / s, (c[2].x + c[0].z) / s, (c[1].z - c[2].y) / s};
    }
    if (m11 > m22) {
        const float s = std::sqrt(1.0f + m11 - m00 - m22) * 2;
        return {(c[1].x + c[0].y) / s, 0.25f * s, (c[2].y + c[1].z) / s, (c[2].x - c[0].z) / s};
    }
    const float s = std::sqrt(1.0f + m22 - m00 - m11) * 2;
    return {(c[2].x + c[0].z) / s, (c[2].y + c[1].z) / s, 0.25f * s, (c[0].y - c[1].x) / s};
}
XrPosef compose(const XrPosef& a, const XrPosef& b) {
    const V3 p = add(v3(a.position), rotate(a.orientation, v3(b.position)));
    return {qmul(a.orientation, b.orientation), {p.x, p.y, p.z}};
}
XrPosef inverse(const XrPosef& a) {
    const XrQuaternionf q = qconj(a.orientation);
    const V3 p = mul(rotate(q, v3(a.position)), -1.0f);
    return {q, {p.x, p.y, p.z}};
}
XrPosef moved_by_camera(const XrPosef& eye, const float* cam_e, const float* cam_ref, const XrPosef& anchor, float scale) {
    V3 e[4], r[4];
    std::memcpy(e, cam_e, sizeof e), std::memcpy(r, cam_ref, sizeof r);
    // C_ref^-1 C_e: the eye frame's camera axes in the reference camera's frame (columns), its place there in metres
    V3 cols[3];
    for (int i = 0; i < 3; i++) cols[i] = {dot(r[0], e[i]), dot(r[1], e[i]), dot(r[2], e[i])};
    const V3 d = sub(e[3], r[3]);
    const V3 t = mul(V3{dot(r[0], d), dot(r[1], d), dot(r[2], d)}, 1.0f / scale);
    const XrPosef delta{quat_of(cols), {t.x, t.y, t.z}};
    return compose(compose(anchor, delta), compose(inverse(anchor), eye));
}

// the format the swapchains take the window's pictures in (x.mu held): the window's bytes are sRGB in an UNORM image, and the
// sRGB format of the same layout takes them as they are
bool pick_format() {
    Xr& x = xr();
    if (x.format != VK_FORMAT_UNDEFINED) return true;
    uint32_t n = 0;
    xrEnumerateSwapchainFormats(x.session, 0, &n, nullptr);
    std::vector<int64_t> formats(n);
    xrEnumerateSwapchainFormats(x.session, n, &n, formats.data());
    auto has = [&](VkFormat f) { return std::find(formats.begin(), formats.end(), (int64_t)f) != formats.end(); };
    const VkFormat src = dev.swap_format;
    const VkFormat same_srgb = src == VK_FORMAT_B8G8R8A8_UNORM ? VK_FORMAT_B8G8R8A8_SRGB
                             : src == VK_FORMAT_R8G8B8A8_UNORM ? VK_FORMAT_R8G8B8A8_SRGB : VK_FORMAT_UNDEFINED;
    if (same_srgb != VK_FORMAT_UNDEFINED && has(same_srgb)) x.format = same_srgb, x.copy_raw = true;
    else if (has(src)) x.format = src, x.copy_raw = true;
    else if (has(VK_FORMAT_R8G8B8A8_SRGB)) x.format = VK_FORMAT_R8G8B8A8_SRGB, x.copy_raw = false;
    else if (has(VK_FORMAT_B8G8R8A8_SRGB)) x.format = VK_FORMAT_B8G8R8A8_SRGB, x.copy_raw = false;
    else if (!formats.empty()) x.format = (VkFormat)formats[0], x.copy_raw = false;
    else return false;
    logf("xr: swapchains in format %d (%s; the window's is %d)", (int)x.format, x.copy_raw ? "copied as they are" : "converted by a blit",
         (int)src);
    return true;
}

// a chain of w x h (x.mu and dev.present_mu held)
bool make_chain(Chain& c, uint32_t w, uint32_t h, const char* what) {
    Xr& x = xr();
    if (c.sc) {
        device_wait_idle_locked();   // (the copies into the old images done)
        xrDestroySwapchain(c.sc);
        c.sc = XR_NULL_HANDLE, c.images.clear(), c.released_once = false, c.held = false, c.acquired = false;
    }
    if (!pick_format()) return false;
    XrSwapchainCreateInfo ci{XR_TYPE_SWAPCHAIN_CREATE_INFO};
    ci.usageFlags = XR_SWAPCHAIN_USAGE_TRANSFER_DST_BIT | XR_SWAPCHAIN_USAGE_COLOR_ATTACHMENT_BIT;
    ci.format = x.format;
    ci.sampleCount = 1;
    ci.width = w, ci.height = h;
    ci.faceCount = 1, ci.arraySize = 1, ci.mipCount = 1;
    if (!ok(xrCreateSwapchain(x.session, &ci, &c.sc), "xrCreateSwapchain")) return false;
    uint32_t n = 0;
    xrEnumerateSwapchainImages(c.sc, 0, &n, nullptr);
    std::vector<XrSwapchainImageVulkan2KHR> imgs(n, {XR_TYPE_SWAPCHAIN_IMAGE_VULKAN2_KHR});
    xrEnumerateSwapchainImages(c.sc, n, &n, (XrSwapchainImageBaseHeader*)imgs.data());
    for (const auto& i : imgs) c.images.push_back(i.image);
    c.w = w, c.h = h;
    logf("xr: %s swapchain: %ux%u, %u images", what, w, h, n);
    return true;
}

// the field of view a projection draws: OpenGL's, clip = P * view position, the picture's centre moved by P[0][2], P[1][2]
// (sead's frustum offset, and the upscalers' jitter: nx2/oot_jitter.py). rows: P row major
bool fov_of(const float* p, XrFovf& f) {
    const float p00 = p[0], p02 = p[2], p11 = p[5], p12 = p[6];
    if (!(p00 > 0.01f) || !(p11 > 0.01f) || p[14] != -1.0f) return false;
    f.angleLeft = std::atan((p02 - 1.0f) / p00);
    f.angleRight = std::atan((p02 + 1.0f) / p00);
    f.angleDown = std::atan((p12 - 1.0f) / p11);
    f.angleUp = std::atan((p12 + 1.0f) / p11);
    return true;
}

} // namespace

// ---------------------------------------------------------------------------------------------------------------------------
int xr_opt[XO_N];
bool xr_built() { return true; }
void xr_opts_load() {
    static std::once_flag once;
    std::call_once(once, [] {
        Xr& x = xr();
        x.set = load_settings();
        opts_from(x.set);
        if (!ini_seen.load()) ini_seen.store(ini_time());
    });
}
void xr_opt_changed(int id) {
    if (id < 0 || id >= XO_N) return;
    Xr& x = xr();
    const OptMap& m = opt_map[id];
    char text[32];
    if (m.i) snprintf(text, sizeof text, "%d", xr_opt[id]);
    else snprintf(text, sizeof text, "%.2f", xr_opt[id] / m.scale);
    // (what the game started with stays as it started: VR, the view, and the gyroscope the virtual pad was made with)
    if (id != XO_VR && id != XO_STEREO && id != XO_GYRO) {
        if (m.i) x.set.*m.i = xr_opt[id];
        else x.set.*m.f = xr_opt[id] / m.scale;
    }
    if (id == XO_FIRST_PERSON) x.first_person = -1;   // (as set here, whatever the stick toggled)
    if (id == XO_SCREEN_DISTANCE || id == XO_SCREEN_HEIGHT) x.recenter = true;   // (the screen put where the new numbers say)
    ini_write(m.key, text);
    logf("xr: menu: %s=%s", m.key, text);
}
void xr_recenter() { xr().recenter = true; }
const char* xr_status() {
    Xr& x = xr();
    if (env_on("RX_VR_TEST")) return "Test mode: no headset";
    if (!x.set.vr) return "Off: on the desktop";
    if (!x.wanted) return "No headset found";
    if (x.dead) return "Stopped: the link failed";
    if (!x.running.load(std::memory_order_relaxed)) return "Waiting for the headset";
    if (x.set.stereo && x.stereo_off) return "On: flat screen";
    return x.set.stereo ? "On: stereo 3D" : "On: flat screen";
}

bool xr_wanted() {
    static const bool wanted = [] {
        Xr& x = xr();
        xr_opts_load();
        if (!x.set.vr) return false;
        HMODULE m = load_loader();
        if (!m) {
            logf("xr: VR is on (hookshot_vr.ini), but there is no openxr_loader.dll: the desktop only");
            return false;
        }
        xrGetInstanceProcAddr_ = (PFN_xrGetInstanceProcAddr)(void*)GetProcAddress(m, "xrGetInstanceProcAddr");
        PFN_xrCreateInstance create = nullptr;
        if (!xrGetInstanceProcAddr_ ||
            XR_FAILED(xrGetInstanceProcAddr_(XR_NULL_HANDLE, "xrCreateInstance", (PFN_xrVoidFunction*)&create)) || !create)
            return false;
        const char* exts[] = {XR_KHR_VULKAN_ENABLE2_EXTENSION_NAME};
        XrInstanceCreateInfo ci{XR_TYPE_INSTANCE_CREATE_INFO};
        strcpy(ci.applicationInfo.applicationName, "Hookshot");
        ci.applicationInfo.applicationVersion = 1;
        strcpy(ci.applicationInfo.engineName, "Hookshot VR");
        ci.applicationInfo.apiVersion = XR_MAKE_VERSION(1, 0, 0);   // (1.0: every runtime takes it)
        ci.enabledExtensionCount = 1;
        ci.enabledExtensionNames = exts;
        const XrResult r = create(&ci, &x.instance);
        if (XR_FAILED(r)) {
            logf("xr: no OpenXR runtime (xrCreateInstance %d): is Quest Link / the Meta Horizon Link app running, and set as the "
                 "OpenXR runtime? The desktop only", (int)r);
            return false;
        }
#define XR_LOAD(f) xrGetInstanceProcAddr_(x.instance, #f, (PFN_xrVoidFunction*)&f);
        XR_FUNCS(XR_LOAD)
#undef XR_LOAD
        XrInstanceProperties ip{XR_TYPE_INSTANCE_PROPERTIES};
        xrGetInstanceProperties(x.instance, &ip);
        logf("xr: runtime %s %u.%u.%u", ip.runtimeName, XR_VERSION_MAJOR(ip.runtimeVersion), XR_VERSION_MINOR(ip.runtimeVersion),
             XR_VERSION_PATCH(ip.runtimeVersion));
        XrSystemGetInfo si{XR_TYPE_SYSTEM_GET_INFO};
        si.formFactor = XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;
        if (!ok(xrGetSystem(x.instance, &si, &x.system), "xrGetSystem (is the headset connected over Link?)")) {
            xrDestroyInstance(x.instance), x.instance = XR_NULL_HANDLE;
            return false;
        }
        XrSystemProperties sp{XR_TYPE_SYSTEM_PROPERTIES};
        xrGetSystemProperties(x.instance, x.system, &sp);
        logf("xr: headset: %s; %s", sp.systemName,
             x.set.stereo ? "stereo (the game's camera at your eyes, one eye a frame)" : "the screen");
        XrGraphicsRequirementsVulkan2KHR req{XR_TYPE_GRAPHICS_REQUIREMENTS_VULKAN2_KHR};
        if (!ok(xrGetVulkanGraphicsRequirements2KHR(x.instance, x.system, &req), "xrGetVulkanGraphicsRequirements2KHR")) {
            xrDestroyInstance(x.instance), x.instance = XR_NULL_HANDLE;
            return false;
        }
        x.wanted = true;
        return true;
    }();
    return wanted;
}

bool xr_create_instance(const VkInstanceCreateInfo* ci, VkInstance* out) {
    if (!xr_wanted()) return false;
    Xr& x = xr();
    XrVulkanInstanceCreateInfoKHR xi{XR_TYPE_VULKAN_INSTANCE_CREATE_INFO_KHR};
    xi.systemId = x.system;
    xi.pfnGetInstanceProcAddr = instance_proc();
    xi.vulkanCreateInfo = ci;
    VkResult vr = VK_SUCCESS;
    if (!ok(xrCreateVulkanInstanceKHR(x.instance, &xi, out, &vr), "xrCreateVulkanInstanceKHR") || vr != VK_SUCCESS) {
        logf("xr: the Vulkan instance through OpenXR failed (VkResult %d): the desktop only", (int)vr);
        x.wanted = false;
        return false;
    }
    logf("xr: the Vulkan instance made through OpenXR");
    return true;
}

VkPhysicalDevice xr_physical_device() {
    Xr& x = xr();
    if (!x.wanted) return VK_NULL_HANDLE;
    XrVulkanGraphicsDeviceGetInfoKHR gi{XR_TYPE_VULKAN_GRAPHICS_DEVICE_GET_INFO_KHR};
    gi.systemId = x.system;
    gi.vulkanInstance = dev.instance;
    VkPhysicalDevice p = VK_NULL_HANDLE;
    if (!ok(xrGetVulkanGraphicsDevice2KHR(x.instance, &gi, &p), "xrGetVulkanGraphicsDevice2KHR")) x.wanted = false;
    return p;
}

bool xr_create_device(VkPhysicalDevice physical, const VkDeviceCreateInfo* ci, VkDevice* out) {
    Xr& x = xr();
    if (!x.wanted) return false;
    XrVulkanDeviceCreateInfoKHR di{XR_TYPE_VULKAN_DEVICE_CREATE_INFO_KHR};
    di.systemId = x.system;
    di.pfnGetInstanceProcAddr = instance_proc();
    di.vulkanPhysicalDevice = physical;
    di.vulkanCreateInfo = ci;
    VkResult vr = VK_SUCCESS;
    if (!ok(xrCreateVulkanDeviceKHR(x.instance, &di, out, &vr), "xrCreateVulkanDeviceKHR") || vr != VK_SUCCESS) {
        logf("xr: the Vulkan device through OpenXR failed (VkResult %d): made as usual, the desktop only", (int)vr);
        x.wanted = false;
        return false;
    }
    logf("xr: the Vulkan device made through OpenXR");
    return true;
}

void xr_device_ready() {
    Xr& x = xr();
    if (!x.wanted) return;
    XrGraphicsBindingVulkan2KHR gb{XR_TYPE_GRAPHICS_BINDING_VULKAN2_KHR};
    gb.instance = dev.instance;
    gb.physicalDevice = dev.physical;
    gb.device = dev.device;
    gb.queueFamilyIndex = dev.queue_family;
    gb.queueIndex = 0;
    XrSessionCreateInfo si{XR_TYPE_SESSION_CREATE_INFO};
    si.next = &gb;
    si.systemId = x.system;
    if (!ok(xrCreateSession(x.instance, &si, &x.session), "xrCreateSession")) {
        x.wanted = false;
        return;
    }
    XrReferenceSpaceCreateInfo ri{XR_TYPE_REFERENCE_SPACE_CREATE_INFO};
    ri.poseInReferenceSpace.orientation.w = 1;
    ri.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_LOCAL;
    ok(xrCreateReferenceSpace(x.session, &ri, &x.local), "the local space");
    ri.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_VIEW;
    ok(xrCreateReferenceSpace(x.session, &ri, &x.view), "the view space");
    if (!make_actions()) {
        logf("xr: the controllers' actions could not be made: no Touch controls");
        x.set_ = XR_NULL_HANDLE;
    }
    x.thread_done = false;
    std::thread(xr_thread).detach();
    logf("xr: session made; the game shows in the headset once it is put on");
}

bool xr_running() { return xr().running.load(std::memory_order_relaxed); }

void xr_shutdown() {
    Xr& x = xr();
    if (!x.instance) return;
    x.running = false, x.dead = true;   // (the presents' copies, the camera hook and the frame loop stop)
    x.pair_cv.notify_all();
    for (int i = 0; i < 150 && !x.thread_done.load(); i++) Sleep(10);   // (its frame in hand ended: a frame's wait at most)
    std::lock_guard qk(dev.present_mu);
    std::lock_guard lk(x.mu);
    device_wait_idle_locked();   // (the last copies into the swapchains' images done)
    for (Chain* c : {&x.screen_chain, &x.eyes[0], &x.eyes[1], &x.hud_chain}) c->sc = XR_NULL_HANDLE, c->images.clear(), c->held = false;
    if (x.session) xrDestroySession(x.session), x.session = XR_NULL_HANDLE;   // (its swapchains, spaces and actions with it)
    xrDestroyInstance(x.instance), x.instance = XR_NULL_HANDLE;
    x.wanted = false;
    logf("xr: the session and the instance closed (the frame loop %s)", x.thread_done.load() ? "ended first" : "did not end in time");
}
// RX_VR_TEST=1: stereo's camera without a headset, for looking at what the game draws with it (RX_VK_SHOTS): a pretended
// head at the anchor, turned by test_yaw and test_pitch (hookshot_vr.ini, read live), the eyes 64 mm apart
static bool xr_test() {
    static const bool on = [] {
        if (!env_on("RX_VR_TEST")) return false;
        xr_opts_load();
        xr().set.stereo = 1;
        logf("xr: test mode: stereo's camera with a pretended head, no headset");
        return true;
    }();
    return on;
}
static bool xr_test_mode() { return xr_test(); }
bool xr_enabled() { return xr_test() || (xr().wanted && xr().session); }
bool xr_stereo_on() { return xr_enabled() && xr().set.stereo && !xr().stereo_off; }
bool xr_no_temporal() { return xr_stereo_on() && !xr().set.dlss; }
static std::atomic<int> frame_eye{0};   // (the eye of the last frame known by its camera: xr_note_scene_draw, at the frame's first scene draw)
int xr_temporal_slot() { return xr_stereo_on() ? frame_eye.load(std::memory_order_relaxed) & 1 : 0; }

// ---------------------------------------------------------------------------------------------------------------------------
// stereo: the game's camera at an eye (the game's thread, host/hos_main.c hos_oot_free_view, each frame)
static int xr_view(const double game_eye[3], const double game_target[3], float out[12], int phase) {
    Xr& x = xr();
    const bool test = xr_test();
    if (test) reload_settings();
    if (!x.set.stereo || x.stereo_off || (!test && !x.running.load(std::memory_order_relaxed)) || x.dead || menu_is_open()) return 0;
    std::unique_lock lk(x.in_mu);
    if (test) {   // (the pretended head)
        const float yaw = x.set.test_yaw * 0.0174533f, pitch = x.set.test_pitch * 0.0174533f;
        const XrQuaternionf qy{0, std::sin(yaw / 2), 0, std::cos(yaw / 2)}, qp{std::sin(pitch / 2), 0, 0, std::cos(pitch / 2)};
        const XrQuaternionf q = qmul(qy, qp);
        for (int i = 0; i < 2; i++) {
            const V3 at = rotate(q, {i ? 0.032f : -0.032f, 0, 0});
            x.eye_pose[i] = {q, {at.x, at.y, at.z}};
        }
        x.anchor = {{0, 0, 0, 1}, {0, 0, 0}};
        x.eyes_valid = x.anchor_valid = true;
        x.eye_fov[0] = {-0.94f, 0.70f, 0.77f, -0.89f}, x.eye_fov[1] = {-0.70f, 0.94f, 0.77f, -0.89f};   // (a Quest 3's, roughly)
    }
    // (240 views and not one frame known by its camera: the game draws with a camera made after the hook, or reads it from
    // elsewhere. The camera goes back to the game's own and the headset keeps the screen, rather than a flat picture that
    // jumps between the eyes)
    if (x.issued_n > 240 && !x.matched.load(std::memory_order_relaxed)) {
        x.stereo_off = true;
        logf("xr: stereo: no frame's camera matched the views handed out (see the 'matched' lines): the screen instead");
        return 0;
    }
    if (!x.eyes_valid || !x.anchor_valid) return 0;
    // (one eye a frame: the other one than the last; asked again within 4 ms, the same frame's view, the same eye)
    const int64_t now = now_ns();
    const bool again = now - x.last_issue_ns < 4000000;
    x.last_issue_ns = now;
    int e = again ? (x.next_eye ^ 1) : x.next_eye;
    if (!again) x.next_eye ^= 1;
    if (!x.set.sync) phase = 0;
    if (phase) {
        // (which frame of a pair this is, by what the game's camera did: the pair's first frame is made with two frames' time
        // and its second with next to none, so a moving camera goes far before a first frame and hardly at all before a second.
        // The host's word for it (the step it saw read) depends on when its threads ran: it was the other way round in a test
        // run, and nothing says it stays one way. Pairs taken wrongly are two moments a whole step apart, shown together.
        // A camera at rest: the frames go on in turn, as before)
        static double last[6];
        static double step_before = 0;
        static int second_before = 0;
        static uint64_t agree = 0, differ = 0;
        static int64_t said = 0;
        double step = 0;
        for (int i = 0; i < 3; i++) step += std::fabs(game_eye[i] - last[i]) + std::fabs(game_target[i] - last[3 + i]);
        for (int i = 0; i < 3; i++) last[i] = game_eye[i], last[3 + i] = game_target[i];
        // (in turn, unless the steps say plainly which this is: a step over three times the one before is a pair's first frame,
        // one under a third of it its second. A steady walk gives about six to one)
        int second = !second_before;
        if (step > 1e-4 && step > 3.0 * step_before) second = 0;
        else if (step_before > 1e-4 && step_before > 3.0 * step) second = 1;
        const int host_says = x.set.sync_flip ? 3 - phase : phase;
        (host_says == (second ? 2 : 1) ? agree : differ)++;
        if (now > said) {
            said = now + 10000000000LL;
            logf("xr: stereo: pairs by the camera's steps; the host's word agreed %llu times, differed %llu",
                 (unsigned long long)agree, (unsigned long long)differ);
        }
        step_before = step, second_before = second;
        phase = second ? 2 : 1;
    }
    if (phase == 1 && x.set.pace && !test) {
        // (a pair starts: when the headset's next frame has begun, and with the eyes located for it. A twentieth of a second
        // at most, should the frame loop be held up: the game goes on with the eyes as they are)
        lk.unlock();
        {
            std::unique_lock ok_(x.open_mu);
            x.open_cv.wait_for(ok_, std::chrono::milliseconds(50), [&] { return x.open_gen != x.open_used; });
            x.open_used = x.open_gen;
        }
        lk.lock();
        if (!x.eyes_valid || !x.anchor_valid) return 0;
    }
    if (phase) e = phase == 2 ? 1 : 0, x.next_eye = e ^ 1;   // (pairs: the left eye first, the right eye the same moment)
    // the eye relative to the anchor (the head at the recentring, upright), in real metres
    const XrPosef& ep = x.eye_pose[e];
    const XrQuaternionf ia = qconj(x.anchor.orientation);
    const XrQuaternionf r_rel = qmul(ia, ep.orientation);
    V3 t_rel = rotate(ia, sub(v3(ep.position), v3(x.anchor.position)));
    const V3 mid = rotate(ia, sub(mul(add(v3(x.eye_pose[0].position), v3(x.eye_pose[1].position)), 0.5f), v3(x.anchor.position)));
    t_rel = add(mid, mul(sub(t_rel, mid), x.set.separation));   // (stereo_separation, about the eyes' middle: below 0 the eyes mirrored)
    if (!x.set.head_motion) t_rel = sub(t_rel, mid);   // (only the eyes' separation: the head's position left out)
    // (the pose this view is drawn from, in the headset's space: what the compositor is told the picture is, so a separation
    // other than the real one, or no head motion, is shown as drawn)
    const XrPosef drawn = compose(x.anchor, XrPosef{r_rel, {t_rel.x, t_rel.y, t_rel.z}});
    // the game's camera, upright (no roll; no pitch with horizon_lock)
    V3 g{(float)game_eye[0], (float)game_eye[1], (float)game_eye[2]};
    V3 f = norm({(float)(game_target[0] - game_eye[0]), (float)(game_target[1] - game_eye[1]), (float)(game_target[2] - game_eye[2])});
    // (the tilt the horizon lock takes out of the view is still where the game aims: the reticle's layer has it, xr_thread)
    x.game_pitch.store(x.set.horizon_lock ? std::asin(std::clamp(f.y, -1.0f, 1.0f)) : 0.0f, std::memory_order_relaxed);
    if (x.set.horizon_lock && std::fabs(f.y) < 0.999f) f = norm({f.x, 0, f.z});
    if (std::fabs(f.y) > 0.999f) return 0;   // (straight up or down: no upright frame)
    const V3 right = norm(cross(f, {0, 1, 0}));
    const V3 up = cross(right, f);
    const V3 back = mul(f, -1.0f);
    {   // (first person: from the point the game's camera looks at, a little up and ahead, facing as the camera faces)
        const int fp = x.first_person.load(std::memory_order_relaxed);
        if (fp < 0 ? x.set.first_person != 0 : fp != 0) {
            const V3 ahead = norm({f.x, 0, f.z});
            const V3 at{(float)game_target[0], (float)game_target[1], (float)game_target[2]};
            const V3 face = add(add(at, mul(ahead, x.set.fp_forward)), V3{0, x.set.fp_height, 0});
            g = face;
        }
    }
    auto to_world = [&](V3 v) { return add(add(mul(right, v.x), mul(up, v.y)), mul(back, v.z)); };   // (camera axes: x right, y up, z back)
    // the eye in the game's world: its axes the camera's turned by the head's, its place moved by the head's, scaled
    const V3 ax[3] = {to_world(rotate(r_rel, {1, 0, 0})), to_world(rotate(r_rel, {0, 1, 0})), to_world(rotate(r_rel, {0, 0, 1}))};
    const V3 p = add(g, to_world(mul(t_rel, x.set.world_scale)));
    for (int row = 0; row < 3; row++) {
        out[row * 4 + 0] = ax[row].x, out[row * 4 + 1] = ax[row].y, out[row * 4 + 2] = ax[row].z;
        out[row * 4 + 3] = -dot(ax[row], p);
    }
    Issued& is = x.issued[x.issued_n % 16];
    is.seq = ++x.issued_n;
    is.eye = e;
    is.pose = drawn;
    is.phase = phase;
    is.time = x.eye_time;
    std::memcpy(is.v, out, sizeof is.v);
    const V3 cam[4] = {right, up, back, g};
    std::memcpy(is.cam, cam, sizeof is.cam);
    if (test)
        logf("xr: test: view %llu eye %d handed out at frame %llu; game eye %.3f %.3f %.3f target %.3f %.3f %.3f",
             (unsigned long long)is.seq, e, (unsigned long long)texture_frame_no(), game_eye[0], game_eye[1], game_eye[2], game_target[0],
             game_target[1], game_target[2]);
    return 1;
}

// a draw into the scene's target (vk_draw.cpp, as the temporal upscalers are told): its camera matched to a view handed out
void xr_note_scene_draw(const Texture* color0, uint32_t width, uint32_t height, uint64_t camera, uint64_t camera_size) {
    Xr& x = xr();
    if (!x.set.stereo || x.stereo_off || (!xr_test() && !x.running.load(std::memory_order_relaxed)) || !color0 || !camera ||
        camera_size < 0x130)
        return;
    if (color0->format != VK_FORMAT_B10G11R11_UFLOAT_PACK32) return;
    const uint64_t frame = texture_frame_no();
    {
        std::lock_guard lk(x.tag_mu);
        if (x.note_frame == frame && x.note_camera == camera) return;   // (looked at already)
        x.note_frame = frame, x.note_camera = camera;
    }
    float c[0x130 / 4];
    if (!memory_read_as_submitted(camera, c, sizeof c)) return;
    // (as vk_temporal.cpp checks: the target's size at 0x120, the projection's w row (0, 0, -1, 0) at 0xa0)
    if (c[0x120 / 4] != (float)width || c[0x124 / 4] != (float)height || c[0xa0 / 4] != 0.0f || c[0xa4 / 4] != 0.0f ||
        c[0xa8 / 4] != -1.0f || c[0xac / 4] != 0.0f)
        return;
    const float* vp = c + 0x30 / 4;   // (the view-projection, row major)
    const float* pr = c + 0x70 / 4;   // (the projection)
    XrFovf fov;
    if (!fov_of(pr, fov)) return;
    // which view handed out this is: P * V against the view-projection
    Issued best{};
    float best_err = 1e30f;
    {
        std::lock_guard lk(x.in_mu);
        for (const Issued& is : x.issued) {
            if (!is.seq) continue;
            float err = 0;
            for (int r = 0; r < 4; r++)
                for (int col = 0; col < 4; col++) {
                    float m = 0.0f;
                    for (int k = 0; k < 3; k++) m += pr[r * 4 + k] * is.v[k * 4 + col];
                    if (col == 3) m += pr[r * 4 + 3];
                    err += std::fabs(m - vp[r * 4 + col]);
                }
            if (err < best_err) best_err = err, best = is;
        }
    }
    const int64_t now = now_ns();
    std::lock_guard lk(x.tag_mu);
    if (now >= x.match_log_ns) {   // (how well the frames match, now and then: a camera the game moves after the hook shows here)
        x.match_log_ns = now + 10000000000LL;
        logf("xr: stereo: a %ux%u scene's camera matched view %llu (eye %d) to %.2g; fov %.1f x %.1f deg", width, height,
             (unsigned long long)best.seq, best.eye, best_err, (fov.angleRight - fov.angleLeft) * 57.2958f,
             (fov.angleUp - fov.angleDown) * 57.2958f);
    }
    if (!best.seq || best_err > 0.05f || (x.tag.frame == frame && x.tag.seq == best.seq)) return;
    x.tag.seq = best.seq, x.tag.frame = frame, x.tag.eye = best.eye, x.tag.pose = best.pose, x.tag.fov = fov;
    std::memcpy(x.tag.cam, best.cam, sizeof x.tag.cam);
    x.tag.phase = best.phase;
    x.tag.time = best.time;
    frame_eye.store(best.eye & 1, std::memory_order_relaxed);
    x.tags[frame % 8] = x.tag;
    if (!x.matched++) logf("xr: stereo: the first frame known by its camera (view %llu, eye %d)", (unsigned long long)best.seq, best.eye);
    if (xr_test()) logf("xr: test: frame %llu is view %llu, eye %d (error %.3g)", (unsigned long long)frame, (unsigned long long)best.seq, best.eye, best_err);
}

static std::mutex hud_watch_mu;
static std::vector<uint64_t> hud_watch;
static void hud_watch_add(uint64_t gpu) {
    std::lock_guard lk(hud_watch_mu);
    if (std::find(hud_watch.begin(), hud_watch.end(), gpu) == hud_watch.end() && hud_watch.size() < 16) hud_watch.push_back(gpu);
}
bool xr_hud_watched(uint64_t gpu) {
    std::lock_guard lk(hud_watch_mu);
    return std::find(hud_watch.begin(), hud_watch.end(), gpu) != hud_watch.end();
}

void xr_note_window_draw(const Texture* color0, uint32_t width, uint32_t height, uint64_t prog, int count, int instances, bool mesh,
                         uint32_t blend_en, const float* viewport, bool depth, const NvnState* window_state) {
    static std::mutex mu;
    static uint64_t frame = 0;
    static int64_t next_ns = 0;
    static int n = 0;
    static const bool on = env_on("RX_VR_TRACE");
    if (!on) return;
    std::lock_guard lk(mu);
    const uint64_t f = texture_frame_no();
    if (f != frame) {   // (a frame: traced when it is time)
        const int64_t now = now_ns();
        if (now < next_ns) return;
        if (frame && n) next_ns = now + 10000000000LL;   // (the one traced ended: the next in 10 s)
        frame = f, n = 0;
        if (now < next_ns) return;
    }
    if (n >= 300) return;
    logf("xr: hud trace: frame %llu draw %d into %#llx%s %ux%u fmt %d: program %llu, %d x %d%s, blend %#x, viewport %.0f %.0f %.0f %.0f%s",
         (unsigned long long)f, n, (unsigned long long)color0->gpu, window_state ? " (window)" : "", width, height, (int)color0->format,
         (unsigned long long)prog, count, instances, mesh ? " mesh" : "", blend_en, viewport[0], viewport[1], viewport[2], viewport[3],
         depth ? ", depth" : "");
    n++;
    if (window_state) {   // (what the window's draw samples: their draws are traced from the next traced frame on)
        for (int i = 0; i < 32; i++) {
            const uint64_t h = window_state->tex[1][i];
            nvn_tex t;
            nvn_view v;
            bool hv = false;
            if (!h || !texture_of((int)(h & 0xFFFFF), &t, &v, &hv)) continue;
            logf("xr: hud trace:   samples slot %d: %#llx %ux%u nvn format %d", i, (unsigned long long)t.gpu, t.d.width, t.d.height,
                 t.d.format);
            hud_watch_add(t.gpu);
        }
    }
}

bool xr_mirror(VkCommandBuffer cmd, VkImage src, uint32_t sx, uint32_t sy, uint32_t sw, uint32_t sh, VkImage dst, uint32_t w,
               uint32_t h) {
    Xr& x = xr();
    if (!x.set.mirror || !xr_stereo_on() || !x.running.load(std::memory_order_relaxed) || !sw || !sh || w < 64 || h < 64) return false;
    Tag tag;
    {   // (the frame being shown, as xr_copy_acquire finds it; not taken here)
        const uint64_t own = texture_frame_no() - 1;
        std::lock_guard lk(x.tag_mu);
        const Tag& t = x.tags[own % 8];
        if (!t.seq || t.frame != own) return false;
        tag = t;
    }
    // an eye's picture in its half of the window, at its field of view's shape (the picture itself is not: its pixels are not
    // square once the cameras have the headset's field of view)
    const float shape = (std::tan(tag.fov.angleRight) - std::tan(tag.fov.angleLeft)) /
                        std::max(0.01f, std::tan(tag.fov.angleUp) - std::tan(tag.fov.angleDown));
    const bool one = x.set.mirror == 2;   // (2: the left eye alone, the whole window: a steady picture to record)
    const uint32_t cell = one ? w : w / 2;
    uint32_t mw = cell, mh = (uint32_t)(mw / shape);
    if (mh > h) mh = h, mw = (uint32_t)(mh * shape);
    if (mw < 16 || mh < 16) return false;
    static struct {
        VkImage image[2]{};
        VmaAllocation memory[2]{};
        uint32_t w = 0, h = 0;
        VkFormat format = VK_FORMAT_UNDEFINED;
        bool has[2]{};
    } m;
    if (m.w != mw || m.h != mh || m.format != dev.swap_format) {
        for (int e = 0; e < 2; e++) {
            if (m.image[e]) {
                const VkImage image = m.image[e];
                const VmaAllocation memory = m.memory[e];
                temporal_later([image, memory] { destroy_image(image, memory); });   // (after the frames that may still read it)
            }
            m.image[e] = VK_NULL_HANDLE, m.memory[e] = nullptr, m.has[e] = false;
            VkImageCreateInfo ic{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
            ic.imageType = VK_IMAGE_TYPE_2D, ic.format = dev.swap_format, ic.extent = {mw, mh, 1}, ic.mipLevels = 1, ic.arrayLayers = 1;
            ic.samples = VK_SAMPLE_COUNT_1_BIT, ic.tiling = VK_IMAGE_TILING_OPTIMAL;
            ic.usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
            if (!create_image(ic, m.image[e], m.memory[e])) {
                m.w = m.h = 0;
                return false;
            }
        }
        m.w = mw, m.h = mh, m.format = dev.swap_format;
    }
    const int e = tag.eye & 1;
    {   // the frame's picture into its eye's
        image_barrier(cmd, m.image[e], VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
        VkImageBlit b{};
        b.srcSubresource = b.dstSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
        b.srcOffsets[0] = {(int32_t)sx, (int32_t)sy, 0};
        b.srcOffsets[1] = {(int32_t)(sx + sw), (int32_t)(sy + sh), 1};
        b.dstOffsets[1] = {(int32_t)mw, (int32_t)mh, 1};
        vkCmdBlitImage(cmd, src, VK_IMAGE_LAYOUT_GENERAL, m.image[e], VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &b, VK_FILTER_LINEAR);
        image_barrier(cmd, m.image[e], VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL);
        m.has[e] = true;
    }
    // the window: black, the left eye's in its left half, the right eye's in its right
    const VkClearColorValue black{{0.0f, 0.0f, 0.0f, 1.0f}};
    const VkImageSubresourceRange range{VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    vkCmdClearColorImage(cmd, dst, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &black, 1, &range);
    image_barrier(cmd, dst, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
    for (int k = 0; k < (one ? 1 : 2); k++) {
        if (!m.has[k]) continue;
        VkImageCopy c{};
        c.srcSubresource = c.dstSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
        c.dstOffset = {(int32_t)(k * cell + (cell - mw) / 2), (int32_t)((h - mh) / 2), 0};
        c.extent = {mw, mh, 1};
        // (the other eye's was left readable when it was written, a frame ago)
        vkCmdCopyImage(cmd, m.image[k], VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, dst, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &c);
    }
    return true;
}

void xr_note_window(const NvnState& s) {
    const uint64_t h = s.tex[1][0];
    nvn_tex t;
    nvn_view v;
    bool hv = false;
    if (!h || !texture_of((int)(h & 0xFFFFF), &t, &v, &hv) || t.d.format != NVN_FORMAT_R11G11B10F) return;
    Hud& u = hud();
    if (u.picture_gpu.load(std::memory_order_relaxed) == t.gpu) return;
    std::lock_guard lk(u.mu);
    u.picture = t;
    u.picture_gpu = t.gpu;
}

bool xr_hud_draw(VkCommandBuffer cmd, NvnState& s, const DrawArgs& d, Texture* color0) {
    Xr& x = xr();
    Hud& u = hud();
    if (!x.set.hud || !xr_stereo_on() || !x.running.load(std::memory_order_relaxed)) return false;
    const uint64_t pic = u.picture_gpu.load(std::memory_order_relaxed);
    // (why HUD draws were not taken, every 10 s while some are not: the log's "hud:" lines)
    static std::atomic<uint64_t> no_pic{0}, other{0}, multi{0}, not_stereo{0}, taken{0};
    static std::atomic<int64_t> said{0};
    if (now_ns() > said.load()) {
        said = now_ns() + 10000000000LL;
        logf("xr: hud: picture %#llx; draws taken %llu, into other targets %llu, with more than one target %llu, in frames not "
             "stereo %llu, before the picture was known %llu", (unsigned long long)pic, (unsigned long long)taken.load(),
             (unsigned long long)other.load(), (unsigned long long)multi.load(), (unsigned long long)not_stereo.load(),
             (unsigned long long)no_pic.load());
    }
    if (!pic) return no_pic++, false;
    if (color0->gpu != pic) return other++, false;
    if (s.rt.n != 1) return multi++, false;
    const uint64_t f = texture_frame_no();
    {   // (only a stereo frame's HUD: a frame known by its camera. The menus' and the title's stay in their picture, the screen's)
        std::lock_guard lk(x.tag_mu);
        const Tag& t = x.tags[f % 8];
        if (!t.seq || t.frame != f) return not_stereo++, false;
    }
    taken++;
    std::lock_guard lk(u.mu);
    if (u.failed) return false;
    if (!u.made) {
        u.own = u.picture;
        u.own.d.format = NVN_FORMAT_RGBA16F;
        u.own.d.levels = 1;
        nvn_texlayout L{};
        host->tex_layout(&u.own.d, &L);
        u.own.L = L;
        const uint64_t at = host->guest_alloc(L.size ? L.size : (uint64_t)u.own.d.width * u.own.d.height * 8);
        if (!at) {
            u.failed = true;
            logf("xr: no guest memory for the HUD's picture: the HUD stays in the eyes' pictures");
            return false;
        }
        u.own.gpu = at, u.own.pool = nullptr, u.own.off = 0, u.own.serial = 0x48554400ull;   // ("HUD")
        u.w = u.own.d.width, u.h = u.own.d.height;
        u.made = true;
        logf("xr: the HUD's own picture: %ux%u RGBA16F at %#llx (%llu bytes)", u.w, u.h, (unsigned long long)at,
             (unsigned long long)L.size);
    }
    static thread_local std::unique_ptr<NvnState> s2p;
    if (!s2p) s2p = std::make_unique<NvnState>();
    NvnState& s2 = *s2p;
    if (u.cleared_frame != f) {   // (the frame's first HUD draw: the picture transparent again)
        s2 = NvnState{};
        s2.rt.n = 1, s2.rt.color[0] = &u.own, s2.rt_serial = ++u.serial;
        static const float clear[4] = {0, 0, 0, 0};
        clear_color_target(cmd, s2, 0, clear, 0xF);
        u.cleared_frame = f;
    }
    s2 = s;
    s2.rt = {};
    s2.rt.n = 1, s2.rt.color[0] = &u.own, s2.rt_serial = ++u.serial;
    draw(cmd, s2, d);
    if (Texture* t = texture_get(&u.own, false)) u.image = t->image;
    u.frame = f;
    return true;
}

static bool chain_image(Chain& ch, const char* what);   // (below)

XrCopy xr_hud_acquire() {
    Xr& x = xr();
    Hud& u = hud();
    XrCopy c;
    // (the frame being shown drew a HUD: the one before the frame number now, as for the eyes)
    if (!x.wanted || !x.running || x.dead || !x.set.hud || u.frame.load() != texture_frame_no() - 1 || !u.image.load()) return c;
    std::lock_guard qk(dev.present_mu);
    std::lock_guard lk(x.mu);
    Chain& ch = x.hud_chain;
    if ((ch.w != u.w || ch.h != u.h || !ch.sc) && !make_chain(ch, u.w, u.h, "the HUD's")) return c;
    if (!chain_image(ch, "the HUD's image")) return c;
    c.image = ch.images[ch.acquired_index], c.w = u.w, c.h = u.h, c.hud = true;
    return c;
}

void xr_hud_record(VkCommandBuffer cmd, const XrCopy& c) {
    if (!c.image) return;
    const VkImage src = hud().image.load();
    image_barrier(cmd, c.image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
    VkImageBlit b{};   // (RGBA16F's linear values into the sRGB swapchain: the blit encodes them)
    b.srcSubresource = b.dstSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
    b.srcOffsets[1] = {(int32_t)c.w, (int32_t)c.h, 1};
    b.dstOffsets[1] = {(int32_t)c.w, (int32_t)c.h, 1};
    vkCmdBlitImage(cmd, src, VK_IMAGE_LAYOUT_GENERAL, c.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &b, VK_FILTER_LINEAR);
    image_barrier(cmd, c.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL);
}

// An eye's image is the headset's own field of view for that eye, black, with the game's picture where its field of view puts it
// (pictures are linear in the tangents of their angles). The runtime is not asked to show a picture of another field of view than
// its own: Meta's, over Link, stretched such a picture over the eye's whole view, each eye's lopsided the other way (seen: the
// world twice, the HUD's edges at the view's). The picture's pixels are kept one to one while the image fits in 4096.
struct EyeLayout {
    uint32_t full_w, full_h, ox, oy, crop_x, crop_y, src_w, src_h, dst_w, dst_h;
    bool scaled;
};
static bool eye_layout(const XrFovf& game, const XrFovf& own, uint32_t w, uint32_t h, EyeLayout& L) {
    const double gx0 = std::tan(game.angleLeft), gx1 = std::tan(game.angleRight), gy0 = std::tan(game.angleDown), gy1 = std::tan(game.angleUp);
    const double rx0 = std::tan(own.angleLeft), rx1 = std::tan(own.angleRight), ry0 = std::tan(own.angleDown), ry1 = std::tan(own.angleUp);
    if (!(gx1 > gx0) || !(gy1 > gy0) || !(rx1 > rx0) || !(ry1 > ry0)) return false;
    double dx = w / (gx1 - gx0), dy = h / (gy1 - gy0);   // (pixels a unit of tangent)
    double scale = 1.0;
    const double fw = (rx1 - rx0) * dx, fh = (ry1 - ry0) * dy;
    if (fw > 4096 || fh > 4096) scale = std::min(4096 / fw, 4096 / fh);
    L.scaled = scale != 1.0;
    L.full_w = (uint32_t)std::lround(fw * scale), L.full_h = (uint32_t)std::lround(fh * scale);
    // the picture's rectangle in the image, in the picture's own pixels first; what falls outside is cropped
    double x0 = (gx0 - rx0) * dx, y0 = (ry1 - gy1) * dy;   // (image rows go down from the top angle)
    double cx = 0, cy = 0, sw = w, sh = h;
    if (x0 < 0) cx = -x0, sw -= cx, x0 = 0;
    if (y0 < 0) cy = -y0, sh -= cy, y0 = 0;
    if (x0 + sw > fw) sw = fw - x0;
    if (y0 + sh > fh) sh = fh - y0;
    if (sw < 1 || sh < 1) return false;
    L.crop_x = (uint32_t)std::lround(cx), L.crop_y = (uint32_t)std::lround(cy);
    L.src_w = (uint32_t)sw, L.src_h = (uint32_t)sh;
    L.ox = (uint32_t)std::lround(x0 * scale), L.oy = (uint32_t)std::lround(y0 * scale);
    L.dst_w = L.scaled ? (uint32_t)(sw * scale) : L.src_w, L.dst_h = L.scaled ? (uint32_t)(sh * scale) : L.src_h;
    L.dst_w = std::min(L.dst_w, L.full_w - L.ox), L.dst_h = std::min(L.dst_h, L.full_h - L.oy);
    if (!L.scaled) L.src_w = L.dst_w, L.src_h = L.dst_h;
    return L.dst_w && L.dst_h;
}

// A chain's next image to copy into (x.mu and dev.present_mu held): acquired, and waited for 3 ms at most. The wait was a
// second: when the headset was put back on, the compositor had no image free for a while, and each of the game's frames then
// stood a second with the queue's lock held, which also kept the frame loop from giving the compositor the frames that
// would have freed them: the game crawled at a frame a second. Not ready: this copy is left out and the image, still acquired,
// is waited for again at the next
static bool chain_image(Chain& ch, const char* what) {
    Xr& x = xr();
    if (!ch.acquired) {
        uint32_t index = 0;
        XrSwapchainImageAcquireInfo ai{XR_TYPE_SWAPCHAIN_IMAGE_ACQUIRE_INFO};
        if (!ok(xrAcquireSwapchainImage(ch.sc, &ai, &index), what)) return false;
        ch.acquired = true, ch.acquired_index = index;
    }
    XrSwapchainImageWaitInfo wi{XR_TYPE_SWAPCHAIN_IMAGE_WAIT_INFO};
    wi.timeout = 3000000;
    const XrResult r = xrWaitSwapchainImage(ch.sc, &wi);
    if (r == XR_TIMEOUT_EXPIRED) {
        x.not_ready++;
        return false;
    }
    if (XR_FAILED(r)) {
        ok(r, what);
        x.dead = true;   // (the swapchain cannot go on; the desktop stays)
        return false;
    }
    ch.acquired = false;
    return true;
}

// An eye's held picture shown: its image released, and where it was drawn from told (x.mu and dev.present_mu held)
static void publish_held(Chain& ch) {
    if (!ch.held) return;
    ch.held = false;
    XrSwapchainImageReleaseInfo ri{XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO};
    if (!ok(xrReleaseSwapchainImage(ch.sc, &ri), "xrReleaseSwapchainImage")) return;
    ch.released_once = true;
    ch.pose = ch.held_pose, ch.fov = ch.held_fov, ch.seq = ch.held_seq, ch.time = ch.held_time;
    std::memcpy(ch.cam, ch.held_cam, sizeof ch.cam);
    xr().stereo_ns = now_ns();
    ch.updates++, ch.at_ns = now_ns();
    Xr& x = xr();
    const int64_t now = now_ns();
    if (now - x.cam_cur_ns > 5000000) {
        std::memcpy(x.cam_prev, x.cam_cur, sizeof x.cam_prev), x.cam_prev_ns = x.cam_cur_ns;
        x.cam_cur_ns = now;
    }
    std::memcpy(x.cam_cur, ch.cam, sizeof x.cam_cur);
    {
        std::lock_guard pk(x.pair_mu);
        x.pair_gen++;
    }
    x.pair_cv.notify_one();
}

XrCopy xr_copy_acquire(uint32_t w, uint32_t h, bool game_frame, uint32_t game_w, uint32_t game_h, VkFormat game_format) {
    Xr& x = xr();
    XrCopy c;
    if (!x.wanted || !x.running || x.dead || !w || !h) return c;
    // a game frame with a new matched camera: its eye's; otherwise the screen's
    Tag tag;
    if (game_frame && x.set.stereo) {
        // (the frame being shown is the one before the frame number now: do_present moves it on (texture_frame) before its
        // present. Its own tag, never a newer frame's: the replay may have drawn the next frame's scene already, and that one
        // is the other eye)
        const uint64_t own = texture_frame_no() - 1;
        std::lock_guard lk(x.tag_mu);
        const Tag& t = x.tags[own % 8];
        if (t.seq && t.frame == own && t.seq != x.tag_used) tag = t, x.tag_used = t.seq, x.took_own++;
        else if (x.tag.seq && x.tag.frame > own) x.took_ahead++;
        else x.took_none++;
        const int64_t now = now_ns();
        if (now >= x.took_log_ns) {
            x.took_log_ns = now + 10000000000LL;
            logf("xr: stereo: eyes by frame: %llu frames took their own camera, %llu found only a newer frame's (left out), %llu "
                 "none%s", (unsigned long long)x.took_own, (unsigned long long)x.took_ahead, (unsigned long long)x.took_none,
                 x.swap ? "; the eyes swapped (both stick clicks + A)" : "");
        }
        if (tag.eye >= 0 && x.swap) tag.eye ^= 1;
    }
    // (a frame with no eye of its own (the logos, the movies, the title and pause screens, the Insert menu): the screen's. The
    // headset shows the screen once no eye has had a picture for half a second, xr_thread)
    EyeLayout L{};
    XrFovf own{};
    // (an eye's picture from the game's own when it is laid out as the window's images are: the same bytes, copied as they are)
    const auto same_layout = [](VkFormat a, VkFormat b) {
        const auto rgba = [](VkFormat f) { return f == VK_FORMAT_R8G8B8A8_UNORM || f == VK_FORMAT_R8G8B8A8_SRGB; };
        const auto bgra = [](VkFormat f) { return f == VK_FORMAT_B8G8R8A8_UNORM || f == VK_FORMAT_B8G8R8A8_SRGB; };
        return (rgba(a) && rgba(b)) || (bgra(a) && bgra(b));
    };
    const bool from_game = tag.eye >= 0 && game_w && game_h && same_layout(game_format, dev.swap_format) && x.copy_raw;
    if (from_game) w = game_w, h = game_h;
    if (tag.eye >= 0) {
        {
            std::lock_guard ik(x.in_mu);
            own = x.eye_fov[tag.eye];
        }
        if (!eye_layout(tag.fov, own, w, h, L)) return c;
    }
    std::lock_guard qk(dev.present_mu);
    std::lock_guard lk(x.mu);
    Chain& ch = tag.eye >= 0 ? x.eyes[tag.eye] : x.screen_chain;
    const uint32_t cw = tag.eye >= 0 ? L.full_w : w, chh = tag.eye >= 0 ? L.full_h : h;
    // (an eye's image made again only when its size is off by more than a pixel: the layout's rounding)
    const bool fits = ch.sc && (tag.eye >= 0 ? (ch.w + 1 >= cw && cw + 1 >= ch.w && ch.h + 1 >= chh && chh + 1 >= ch.h) : (ch.w == cw && ch.h == chh));
    if (!fits && !make_chain(ch, cw, chh, tag.eye == 0 ? "the left eye's" : tag.eye == 1 ? "the right eye's" : "the screen's")) return c;
    if (tag.eye >= 0) {
        static bool said[2];
        if (!said[tag.eye]) {
            said[tag.eye] = true;
            logf("xr: the %s eye's image: %ux%u for the headset's %.1f..%.1f x %.1f..%.1f deg; the game's %ux%u picture (%.1f..%.1f x "
                 "%.1f..%.1f deg) at %u,%u%s", tag.eye ? "right" : "left", ch.w, ch.h, own.angleLeft * 57.2958f, own.angleRight * 57.2958f,
                 own.angleDown * 57.2958f, own.angleUp * 57.2958f, w, h, tag.fov.angleLeft * 57.2958f, tag.fov.angleRight * 57.2958f,
                 tag.fov.angleDown * 57.2958f, tag.fov.angleUp * 57.2958f, L.ox, L.oy, L.scaled ? ", scaled down to fit" : "");
        }
        // (the layout within the image as it is: a pixel of rounding either way)
        L.dst_w = std::min(L.dst_w, ch.w - std::min(L.ox, ch.w)), L.dst_h = std::min(L.dst_h, ch.h - std::min(L.oy, ch.h));
        if (!L.scaled) L.src_w = L.dst_w, L.src_h = L.dst_h;
        if (!L.dst_w || !L.dst_h) return c;
    }
    bool reuse = false;
    if (tag.eye >= 0 && tag.phase) {
        // (frames in pairs of one moment. The game does not draw every frame it steps: a pair that lost a frame is not shown.
        // Its second with another first was a left eye of one moment beside a right eye of another, one frame in ten: seen as
        // a flicker)
        Chain& other = x.eyes[tag.eye ^ 1];
        if (tag.phase == 2 && !(other.held && other.held_seq + 1 == tag.seq)) {
            x.orphans++;
            return c;
        }
        if (tag.phase == 2) {
            // (and the two of one moment: the game's camera where it was for the first, within a degree and 15 cm. Two frames
            // in a row a whole step apart are the end of one pair and the start of the next, taken the wrong way round)
            V3 a[4], b[4];
            std::memcpy(a, other.held_cam, sizeof a), std::memcpy(b, tag.cam, sizeof b);
            const V3 d = sub(a[3], b[3]);
            if (dot(a[2], b[2]) < 0.99985f || dot(d, d) > 0.15f * 0.15f * x.set.world_scale * x.set.world_scale) {
                x.orphans++;
                return c;
            }
        }
        if (ch.held) reuse = true, x.redrawn++;   // (a first whose second never came: this one over it, in the same image)
    } else if (ch.held) {
        publish_held(ch), x.alone++;   // (no pairs: shown before this eye's next)
    }
    if (reuse) {
        c.image = ch.held_image;
    } else {
        if (!chain_image(ch, "an eye's or the screen's image")) return c;
        c.image = ch.images[ch.acquired_index];
        if (tag.eye >= 0) ch.held_image = c.image;
    }
    c.w = w, c.h = h, c.eye = tag.eye;
    if (tag.eye >= 0) {   // (the picture's part of the eye's image; the field of view told is the headset's own)
        c.w = L.src_w, c.h = L.src_h;
        c.full_w = ch.w, c.full_h = ch.h, c.ox = L.ox, c.oy = L.oy, c.crop_x = L.crop_x, c.crop_y = L.crop_y;
        c.dst_w = L.dst_w, c.dst_h = L.dst_h, c.scaled = L.scaled;
        c.from_game = from_game;
        x.game_tan_x = (std::tan(tag.fov.angleRight) - std::tan(tag.fov.angleLeft)) / 2;
        x.game_tan_y = (std::tan(tag.fov.angleUp) - std::tan(tag.fov.angleDown)) / 2;
        tag.fov = own;
    }
    std::memcpy(c.pose, &tag.pose, sizeof c.pose), std::memcpy(c.fov, &tag.fov, sizeof c.fov);
    std::memcpy(c.cam, tag.cam, sizeof c.cam), c.seq = tag.seq;
    c.phase = tag.phase;
    c.pose_time = tag.time;
    return c;
}

void xr_copy_record(VkCommandBuffer cmd, const XrCopy& c, VkImage src, VkImageLayout src_layout, uint32_t sx, uint32_t sy) {
    if (!c.image) return;
    image_barrier(cmd, c.image, VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
    uint32_t dw = c.w, dh = c.h;
    if (c.eye >= 0) {   // (an eye's image: black around the picture)
        const VkClearColorValue black{{0.0f, 0.0f, 0.0f, 1.0f}};
        const VkImageSubresourceRange range{VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
        vkCmdClearColorImage(cmd, c.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &black, 1, &range);
        image_barrier(cmd, c.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL);
        sx += c.crop_x, sy += c.crop_y;
        dw = c.dst_w, dh = c.dst_h;
    }
    if (xr().copy_raw && !c.scaled) {
        VkImageCopy r{};
        r.srcSubresource = r.dstSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
        r.srcOffset = {(int32_t)sx, (int32_t)sy, 0};
        r.dstOffset = {(int32_t)c.ox, (int32_t)c.oy, 0};
        r.extent = {c.w, c.h, 1};
        vkCmdCopyImage(cmd, src, src_layout, c.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &r);
    } else {
        VkImageBlit b{};
        b.srcSubresource = b.dstSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
        b.srcOffsets[0] = {(int32_t)sx, (int32_t)sy, 0};
        b.srcOffsets[1] = {(int32_t)(sx + c.w), (int32_t)(sy + c.h), 1};
        b.dstOffsets[0] = {(int32_t)c.ox, (int32_t)c.oy, 0};
        b.dstOffsets[1] = {(int32_t)(c.ox + dw), (int32_t)(c.oy + dh), 1};
        vkCmdBlitImage(cmd, src, src_layout, c.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &b,
                       c.scaled ? VK_FILTER_LINEAR : VK_FILTER_NEAREST);
    }
    image_barrier(cmd, c.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL);
}

void xr_copy_release(const XrCopy& c) {   // (dev.present_mu held: after the submit with the copy)
    if (!c.image) return;
    Xr& x = xr();
    std::lock_guard lk(x.mu);
    Chain& ch = c.hud ? x.hud_chain : c.eye >= 0 ? x.eyes[c.eye] : x.screen_chain;
    if (c.eye >= 0 && !c.hud) {
        // (an eye's picture is held, copied and not released, until it is shown. Frames in pairs of one moment (c.phase): the
        // first waits for the second and both are shown at once, so the eyes never show two moments; otherwise at once)
        ch.held = true;
        std::memcpy(&ch.held_pose, c.pose, sizeof c.pose), std::memcpy(&ch.held_fov, c.fov, sizeof c.fov);
        std::memcpy(ch.held_cam, c.cam, sizeof ch.held_cam), ch.held_seq = c.seq;
        ch.held_time = c.pose_time;
        Chain& other = x.eyes[c.eye ^ 1];
        if (c.phase == 1) {
            if (other.held) publish_held(other);   // (a picture left from before the pairs)
            return;
        }
        if (other.held) publish_held(other);
        publish_held(ch);
        return;
    }
    XrSwapchainImageReleaseInfo ri{XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO};
    if (!ok(xrReleaseSwapchainImage(ch.sc, &ri), "xrReleaseSwapchainImage")) return;
    ch.released_once = true;
    if (c.hud) {
        x.hud_ns = now_ns();
        return;
    }
    if (c.eye >= 0) {
        std::memcpy(&ch.pose, c.pose, sizeof c.pose), std::memcpy(&ch.fov, c.fov, sizeof c.fov);
        std::memcpy(ch.cam, c.cam, sizeof ch.cam), ch.seq = c.seq;
        x.stereo_ns = now_ns();
        ch.updates++, ch.at_ns = now_ns();
    }
}

} // namespace mkwvk

// (hookshot-quest) the field of view for the game's cameras while stereo runs (host/hos_main.c hos_oot_free_view, each frame after a
// view was handed out): one frustum both eyes' views fit in, upright and centred, as sead's projection is: the tangent of half its
// height, and its width over its height. The eyes' images take the part of the picture each eye sees (eye_layout)
extern "C" __declspec(dllexport) int mkwvk_xr_projection(float out[2]) {
    using namespace mkwvk;
    Xr& x = xr();
    if (!x.set.fill || !x.set.stereo || x.stereo_off) return 0;
    std::lock_guard lk(x.in_mu);
    float tx = 0, ty = 0;
    for (const XrFovf& f : x.eye_fov) {
        tx = std::max({tx, std::fabs(std::tan(f.angleLeft)), std::fabs(std::tan(f.angleRight))});
        ty = std::max({ty, std::fabs(std::tan(f.angleUp)), std::fabs(std::tan(f.angleDown))});
    }
    if (!(tx > 0.1f) || !(ty > 0.1f) || tx > 4.0f || ty > 4.0f) return 0;
    out[0] = ty, out[1] = tx / ty;
    return 1;
}

// (hookshot-quest) the export host/rx_backend.c rx_xr_view looks for: an older game does not ask, an older back end has none
extern "C" __declspec(dllexport) int mkwvk_xr_view(const double eye[3], const double target[3], float out[12]) {
    return mkwvk::xr_view(eye, target, out, 0);
}
// (a game that gives its frames' time in pairs says which frame of the pair this is: host/nn_vi.c realtime_step)
extern "C" __declspec(dllexport) int mkwvk_xr_view2(const double eye[3], const double target[3], float out[12], int phase) {
    return mkwvk::xr_view(eye, target, out, phase);
}
// (the step of a pair's second frame, microseconds, while stereo's pictures are coming and stereo_sync is on; 0: no pairs)
extern "C" __declspec(dllexport) int mkwvk_xr_sync() {
    using namespace mkwvk;
    Xr& x = xr();
    if (!x.set.sync || !xr_stereo_on()) return 0;
    const bool flowing = xr_test_mode() || now_ns() - x.stereo_ns.load(std::memory_order_relaxed) < 500000000LL;
    return flowing ? std::clamp(x.set.sync_us, 4167, 8000) : 0;
}
#endif   // _WIN32
