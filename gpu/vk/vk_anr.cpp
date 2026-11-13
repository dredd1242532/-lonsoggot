// Optional ANR provider, bound directly to its Vulkan exports, independently of NVIDIA's NGX core.
// ABI facts: ANR 20a5e773's capture forwarder and NVIDIA DLSS SDK 9981c635's public interface.
#include "vk_anr.h"

#include <atomic>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstring>
#include <mutex>
#include <limits>
#include <string>
#include <thread>

#include "vk_menu.h"
#include "vk_draw.h"
#include "vk_temporal.h"

#if defined(_WIN32) && defined(_WIN64)
#include <filesystem>
#include "anr_parameters.h"
#include "dlss_math.h"
#include "vk_texture.h"
#endif
#include "vk_work.h"   // Provider commands must also invalidate the replay's barrier/descriptor caches.

namespace mkwvk {
extern std::atomic<bool> present_native_pq;
namespace {
enum Reason { Off, AutoRate, NativeHdr, Platform, Package, Library, Exports, Initialization, Parameters, Inputs, History, Count,
              Creation, Evaluation, Ready, Reasons };
std::atomic<int> reason{Inputs};
const char* const reasons[] = {
    "ANR is selected; frame generation is Off. Its Vulkan context is inactive.",
    "ANR needs a fixed 2x or higher multiplier. Auto uses the built-in GPU generator.",
    "ANR currently takes SDR frames. Native HDR uses the built-in GPU generator.",
    "ANR's optional provider is supported on Windows x64 here. The built-in GPU generator is used.",
    "ANR's complete package is missing from the game's anr folder. The built-in GPU generator is used.",
    "The ANR DLL could not be loaded. The built-in GPU generator is used; see the Windows error in the log.",
    "The ANR DLL lacks its Vulkan provider exports. The built-in GPU generator is used.",
    "ANR did not initialize on this Vulkan device. The built-in GPU generator is used; see the log.",
    "ANR did not accept the host parameters. The built-in GPU generator is used; see the log.",
    "No current temporal Upscaler's depth, motion and camera for ANR. The built-in GPU generator is used.",
    "ANR is starting a new history after a cut, load or resize. No old scene is interpolated.",
    "The multiplier exceeds this ANR provider's reported limit. The built-in GPU generator is used.",
    "ANR could not create its feature. The built-in GPU generator is used; see the log.",
    "ANR refused a generated frame. The built-in GPU generator is used until ANR is selected again; see the log.",
    "ANR is recording the generated frames with this scene's depth, motion and camera. Experimental: artifacts may remain.",
};
static_assert(sizeof reasons / sizeof *reasons == Reasons);

bool requested() { return opts.framegen_by == 2 && opts.framegen >= 2; }
void explain(Reason value) {
    if (reason.exchange(value, std::memory_order_relaxed) != value) logf("vk: ANR FG: %s", reasons[value]);
}

#if defined(_WIN32) && defined(_WIN64)
using Result = anr_abi::Result;
bool failed(Result r) { return (r & 0xfff00000u) == anr_abi::fail; }

struct Resource {
    VkImageView view;
    VkImage image;
    VkImageSubresourceRange range;
    VkFormat format;
    uint32_t width, height;
    uint32_t type = 0;   // Published NGX resource type: image view.
    bool writable = false;
};
static_assert(sizeof(Resource) == 56 && alignof(Resource) == 8);
static_assert(offsetof(Resource, type) == 48 && offsetof(Resource, writable) == 52);
using Init = Result (*)(unsigned long long, const wchar_t*, VkInstance, VkPhysicalDevice, VkDevice,
                       PFN_vkGetInstanceProcAddr, PFN_vkGetDeviceProcAddr, unsigned, const void*);
using Populate = Result (*)(void*);
using Create = Result (*)(VkDevice, VkCommandBuffer, unsigned, void*, void**);
using Evaluate = Result (*)(VkCommandBuffer, const void*, const void*, void*);
using Release = Result (*)(void*);
using Shutdown = Result (*)(VkDevice);

struct Image {
    VkImage image = VK_NULL_HANDLE;
    VkImageView view = VK_NULL_HANDLE;
    VmaAllocation allocation = nullptr;
    uint32_t width = 0, height = 0;
};
struct State {
    std::mutex mutex;
    HMODULE module = nullptr;
    Init init = nullptr;
    Populate populate = nullptr;
    Create create = nullptr;
    Evaluate evaluate = nullptr;
    Release release = nullptr;
    Shutdown shutdown = nullptr;
    bool tried = false, initialized = false, refused = false, closed = false;
    std::atomic<bool> held{false};
    void* feature = nullptr;
    anr_abi::Parameters parameters;
    unsigned most = 1;
    Image depth, motion;
    uint32_t width = 0, height = 0, render_width = 0, render_height = 0;
    float matrices[5][16]{}, vectors[4][3]{};
    float previous[16]{}, jitter[2]{}, near_z = .1f, far_z = 1000, fov = 1, aspect = 16.f / 9;
    bool have_previous = false, reset = true, reversed = false;
    uint64_t inputs = 0, used = 0, sequence = 0, made = 0;
    std::chrono::steady_clock::time_point at{};
};
State& state() { static State value; return value; }

std::filesystem::path package_dir() {
    std::wstring executable(32768, L'\0');
    const DWORD count = GetModuleFileNameW(nullptr, executable.data(), (DWORD)executable.size());
    if (!count || count >= executable.size()) return {};
    executable.resize(count);
    auto directory = std::filesystem::path(executable).parent_path();
    if (!_wcsicmp(directory.filename().c_str(), L"bin") &&
        GetFileAttributesW((directory.parent_path() / L"image.bin").c_str()) != INVALID_FILE_ATTRIBUTES)
        directory = directory.parent_path();
    return directory / L"anr";
}

bool start(State& s) {
    if (s.tried) return s.initialized && !s.refused;
    s.tried = true;
    s.held = true;   // Also lets switching away retry a missing or refused optional package.
    const auto directory = package_dir();
    if (directory.empty()) return explain(Package), false;
    for (const wchar_t* file : {L"nvngx_dlssg.dll", L"path_table.json", L"size_table.json", L"nr_fg.ini"})
        if (GetFileAttributesW((directory / file).c_str()) == INVALID_FILE_ATTRIBUTES) return explain(Package), false;
    if (!s.module) s.module = LoadLibraryExW((directory / L"nvngx_dlssg.dll").c_str(), nullptr,
                                           LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
    if (!s.module) {
        logf("vk: ANR FG: LoadLibraryExW failed (%lu)", (unsigned long)GetLastError());
        return explain(Library), false;
    }
    s.init = reinterpret_cast<Init>(GetProcAddress(s.module, "NVSDK_NGX_VULKAN_Init_Ext2"));
    s.populate = reinterpret_cast<Populate>(GetProcAddress(s.module, "NVSDK_NGX_VULKAN_PopulateParameters_Impl"));
    s.create = reinterpret_cast<Create>(GetProcAddress(s.module, "NVSDK_NGX_VULKAN_CreateFeature1"));
    s.evaluate = reinterpret_cast<Evaluate>(GetProcAddress(s.module, "NVSDK_NGX_VULKAN_EvaluateFeature"));
    s.release = reinterpret_cast<Release>(GetProcAddress(s.module, "NVSDK_NGX_VULKAN_ReleaseFeature"));
    s.shutdown = reinterpret_cast<Shutdown>(GetProcAddress(s.module, "NVSDK_NGX_VULKAN_Shutdown1"));
    if (!s.init || !s.populate || !s.create || !s.evaluate || !s.release || !s.shutdown) return explain(Exports), false;
    // The provider's last argument has a different pointee contract from NGX's core; its published example passes null.
    const Result init = s.init(1, directory.c_str(), dev.instance, dev.physical, dev.device, instance_proc(),
                               vkGetDeviceProcAddr, 0x15, nullptr);
    if (failed(init)) {
        logf("vk: ANR FG: Init_Ext2 refused (%#x)", init);
        return explain(Initialization), false;
    }
    s.initialized = true;
    s.held = true;
    s.parameters.clear();
    const Result populated = s.populate(s.parameters.raw());
    if (failed(populated) || !s.parameters.healthy()) {
        s.refused = true;
        return explain(Parameters), false;
    }
    unsigned most = 0;
    if (!failed(s.parameters.get("DLSSG.MultiFrameCountMax", &most)) && most) s.most = std::min(most, 7u);
    int available = 1;
    if (!failed(s.parameters.get("FrameGeneration.Available", &available)) && !available) {
        s.refused = true;
        return explain(Initialization), false;
    }
    logf("vk: ANR FG: Vulkan provider initialized; up to %u generated frames per real frame", s.most);
    return true;
}

void destroy(Image image) {
    if (image.view) vkDestroyImageView(dev.device, image.view, nullptr);
    if (image.image) destroy_image(image.image, image.allocation);
}
bool image(VkCommandBuffer cmd, Image& destination, VkFormat format, uint32_t width, uint32_t height) {
    if (destination.image && destination.width == width && destination.height == height) return true;
    const Image old = destination;
    if (old.image) temporal_later([old] { destroy(old); });
    destination = {};
    VkImageCreateInfo info{VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
    info.imageType = VK_IMAGE_TYPE_2D, info.format = format, info.extent = {width, height, 1};
    info.mipLevels = info.arrayLayers = 1;
    info.samples = VK_SAMPLE_COUNT_1_BIT;
    info.usage = VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    if (!create_image(info, destination.image, destination.allocation)) return false;
    VkImageViewCreateInfo view{VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
    view.image = destination.image, view.viewType = VK_IMAGE_VIEW_TYPE_2D, view.format = format;
    view.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    if (vkCreateImageView(dev.device, &view, nullptr, &destination.view) != VK_SUCCESS) {
        destroy(destination), destination = {};
        return false;
    }
    destination.width = width, destination.height = height;
    VkImageMemoryBarrier2 barrier{VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2};
    barrier.srcStageMask = barrier.dstStageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT;
    barrier.dstAccessMask = VK_ACCESS_2_MEMORY_READ_BIT | VK_ACCESS_2_MEMORY_WRITE_BIT;
    barrier.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED, barrier.newLayout = VK_IMAGE_LAYOUT_GENERAL;
    barrier.srcQueueFamilyIndex = barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    barrier.image = destination.image, barrier.subresourceRange = view.subresourceRange;
    VkDependencyInfo dependency{VK_STRUCTURE_TYPE_DEPENDENCY_INFO};
    dependency.imageMemoryBarrierCount = 1, dependency.pImageMemoryBarriers = &barrier;
    vkCmdPipelineBarrier2(cmd, &dependency);
    return true;
}

void transpose(const float* from, float* to) {
    for (int row = 0; row < 4; row++)
        for (int column = 0; column < 4; column++) to[row * 4 + column] = from[column * 4 + row];
}
void release_feature(State& s) {
    if (s.feature) s.release(s.feature), s.feature = nullptr;
    s.width = s.height = 0;
    s.render_width = s.render_height = 0;
    s.sequence = 0;
}
bool queue_idle(std::unique_lock<std::mutex>& lock) {
    const auto until = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    while (!lock.try_lock() && std::chrono::steady_clock::now() < until)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    if (!lock.owns_lock()) return false;
    device_wait_idle_locked();
    return true;
}
#endif
} // namespace

bool anr_fg_selected() { return opts.framegen_by == 2; }
const char* anr_fg_reason(int variant) {
    if (variant >= 0) return variant < Reasons ? reasons[variant] : nullptr;
    if (opts.framegen < 1) return reasons[Off];
    if (opts.framegen == 1) return reasons[AutoRate];
#if !defined(_WIN32) || !defined(_WIN64)
    return reasons[Platform];
#else
    return reasons[std::clamp(reason.load(std::memory_order_relaxed), 0, (int)Reasons - 1)];
#endif
}

void anr_fg_inputs(VkCommandBuffer cmd, const TemporalFrame& f, const float projection[16], const float vp[16],
                   const float clip_jitter[2], bool motion) {
    if (!requested()) return;
    if (present_native_pq.load(std::memory_order_relaxed)) return explain(NativeHdr);
#if defined(_WIN32) && defined(_WIN64)
    State& s = state();
    std::lock_guard lock(s.mutex);
    if (s.closed || s.refused || !f.depth.image || !f.motion.image || !f.render_w || !f.render_h) return;
    float p[16], current[16], inverse_p[16], inverse_vp[16], camera[16], to_previous[16], from_previous[16];
    std::memcpy(p, projection, sizeof p), std::memcpy(current, vp, sizeof current);
    dlss_math::unjitter(p, clip_jitter), dlss_math::unjitter(current, clip_jitter);
    static const float identity[16]{1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};
    if (!dlss_math::reprojection(p, identity, inverse_p) || !dlss_math::reprojection(current, identity, inverse_vp)) return;
    for (int row = 0; row < 4; row++)
        for (int column = 0; column < 4; column++) {
            double value = 0;
            for (int k = 0; k < 4; k++) value += (double)inverse_vp[row * 4 + k] * p[k * 4 + column];
            camera[row * 4 + column] = (float)value;
        }
    const bool history = s.have_previous && motion && !f.reset;
    if (!history || !dlss_math::reprojection(current, s.previous, to_previous))
        std::memcpy(to_previous, identity, sizeof identity);
    if (!dlss_math::reprojection(to_previous, identity, from_previous)) return;
    if (!start(s)) return;
    if ((unsigned)(opts.framegen - 1) > s.most) return explain(Count);
    transpose(p, s.matrices[0]), transpose(inverse_p, s.matrices[1]);
    transpose(identity, s.matrices[2]), transpose(to_previous, s.matrices[3]), transpose(from_previous, s.matrices[4]);
    for (int i = 0; i < 3; i++) {
        s.vectors[0][i] = camera[i * 4 + 3], s.vectors[1][i] = camera[i * 4 + 1];
        s.vectors[2][i] = camera[i * 4], s.vectors[3][i] = -camera[i * 4 + 2];
    }
    s.near_z = f.near_z, s.far_z = f.far_z, s.fov = f.fov_y;
    s.aspect = p[0] ? std::fabs(p[5] / p[0]) : 16.f / 9;
    s.jitter[0] = f.jitter[0], s.jitter[1] = f.jitter[1], s.reversed = f.depth_reversed;
    s.reset |= !history;
    if (!image(cmd, s.depth, VK_FORMAT_R32_SFLOAT, f.render_w, f.render_h) ||
        !image(cmd, s.motion, VK_FORMAT_R16G16_SFLOAT, f.render_w, f.render_h)) return explain(Inputs);
    full_barrier(cmd);
    const VkImageCopy copy{{VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1}, {0, 0, 0}, {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1},
                           {0, 0, 0}, {f.render_w, f.render_h, 1}};
    vkCmdCopyImage(cmd, f.depth.image, VK_IMAGE_LAYOUT_GENERAL, s.depth.image, VK_IMAGE_LAYOUT_GENERAL, 1, &copy);
    vkCmdCopyImage(cmd, f.motion.image, VK_IMAGE_LAYOUT_GENERAL, s.motion.image, VK_IMAGE_LAYOUT_GENERAL, 1, &copy);
    full_barrier(cmd);
    std::memcpy(s.previous, current, sizeof current), s.have_previous = true;
    s.inputs++;
#else
    (void)cmd, (void)f, (void)projection, (void)vp, (void)clip_jitter, (void)motion;
#endif
}

int anr_fg_frames(VkCommandBuffer cmd, VkImage color, VkImageView view, uint32_t width, uint32_t height, int count,
                  const VkImage* outputs, const VkImageView* views, bool blend, uint64_t sequence) {
    if (!requested()) return 0;
    if (present_native_pq.load(std::memory_order_relaxed)) return explain(NativeHdr), 0;
#if defined(_WIN32) && defined(_WIN64)
    State& s = state();
    std::lock_guard lock(s.mutex);
    if (s.closed || s.refused) return 0;
    if (s.tried && !s.initialized) return 0;   // Keep the package/export/start failure as the reason, not "no inputs".
    if (!blend) return explain(History), 0;
    if (s.inputs == s.used || !s.depth.image || !s.motion.image) return explain(Inputs), 0;
    if (!s.initialized || count < 1 || (unsigned)count > s.most) return explain(Count), 0;
    s.used = s.inputs;
    auto& p = s.parameters;
    if (s.feature && (s.width != width || s.height != height ||
                      s.render_width != s.depth.width || s.render_height != s.depth.height)) {
        std::unique_lock queue(dev.present_mu, std::defer_lock);
        if (!queue_idle(queue)) return explain(Creation), 0;
        release_feature(s);
    }
    if (!s.feature) {
        p.set("CreationNodeMask", 1u), p.set("VisibilityNodeMask", 1u);
        p.set("Width", width), p.set("Height", height);
        p.set("DLSSG.Width", width), p.set("DLSSG.Height", height);
        p.set("DLSSG.InternalWidth", s.depth.width), p.set("DLSSG.InternalHeight", s.depth.height);
        p.set("DLSSG.BackbufferFormat", (unsigned)VK_FORMAT_R8G8B8A8_UNORM), p.set("DLSSG.DynamicResolution", 0u);
        p.set("DLSSG.AsyncCreateEnabled", 0u);   // The host parameter object is serialized by this recording thread.
        if (!p.healthy()) return s.refused = true, explain(Parameters), 0;
        rec::boundary(rec::B_DIRECT);
        const Result result = s.create(dev.device, cmd, 11, p.raw(), &s.feature);
        note_work(), descriptors_disturbed();
        if (failed(result) || !s.feature) {
            logf("vk: ANR FG: CreateFeature1 refused (%#x)", result);
            s.refused = true;
            return explain(Creation), 0;
        }
        s.width = width, s.height = height, s.reset = true;
        s.render_width = s.depth.width, s.render_height = s.depth.height;
    }
    const VkImageSubresourceRange one{VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    Resource back{view, color, one, VK_FORMAT_R8G8B8A8_UNORM, width, height};
    Resource depth{s.depth.view, s.depth.image, one, VK_FORMAT_R32_SFLOAT, s.depth.width, s.depth.height};
    Resource motion{s.motion.view, s.motion.image, one, VK_FORMAT_R16G16_SFLOAT, s.motion.width, s.motion.height};
    const char* const matrices[]{"DLSSG.CameraViewToClip", "DLSSG.ClipToCameraView", "DLSSG.ClipToLensClip",
                                 "DLSSG.ClipToPrevClip", "DLSSG.PrevClipToClip"};
    for (int i = 0; i < 5; i++) p.set(matrices[i], (void*)s.matrices[i]);
    const char* const vectors[4][3]{{"DLSSG.CameraPosX", "DLSSG.CameraPosY", "DLSSG.CameraPosZ"},
                                   {"DLSSG.CameraUpX", "DLSSG.CameraUpY", "DLSSG.CameraUpZ"},
                                   {"DLSSG.CameraRightX", "DLSSG.CameraRightY", "DLSSG.CameraRightZ"},
                                   {"DLSSG.CameraFwdX", "DLSSG.CameraFwdY", "DLSSG.CameraFwdZ"}};
    for (int i = 0; i < 4; i++)
        for (int axis = 0; axis < 3; axis++) p.set(vectors[i][axis], s.vectors[i][axis]);
    p.set("DLSSG.JitterOffsetX", s.jitter[0]), p.set("DLSSG.JitterOffsetY", s.jitter[1]);
    p.set("DLSSG.MvecScaleX", 1.f), p.set("DLSSG.MvecScaleY", 1.f);
    p.set("DLSSG.CameraPinholeOffsetX", 0.f), p.set("DLSSG.CameraPinholeOffsetY", 0.f);
    p.set("DLSSG.CameraNear", s.near_z), p.set("DLSSG.CameraFar", s.far_z);
    p.set("DLSSG.CameraFOV", s.fov), p.set("DLSSG.CameraAspectRatio", s.aspect);
    p.set("DLSSG.ColorBuffersHDR", 0u), p.set("DLSSG.DepthInverted", (unsigned)s.reversed);
    p.set("DLSSG.CameraMotionIncluded", 1u), p.set("DLSSG.MvecDilated", 0u), p.set("DLSSG.MvecJittered", 0u);
    p.set("DLSSG.MvecInvalidValue", std::numeric_limits<float>::max());
    const bool prime = s.reset || !s.sequence || sequence != s.sequence + 1;
    p.set("DLSSG.Reset", (unsigned)prime);
    p.set("DLSSG.AutomodeOverrideReset", 0u), p.set("DLSSG.NotRenderingGameFrames", 0u);
    p.set("DLSSG.OrthoProjection", 0u), p.set("DLSSG.MenuDetectionEnabled", 0u);
    p.set("DLSSG.BackbufferFrameID", (unsigned long long)sequence), p.set("DLSSG.MultiFrameCount", (unsigned)count);
    p.set("DLSSG.Backbuffer", (void*)&back), p.set("DLSSG.Depth", (void*)&depth), p.set("DLSSG.MVecs", (void*)&motion);
    for (const char* name : {"DLSSG.HUDLess", "DLSSG.UI", "DLSSG.UIAlpha", "DLSSG.BidirectionalDistortionField",
                             "DLSSG.OutputReal", "DLSSG.OutputDisableInterpolation"})
        p.set(name, (void*)nullptr);
    full_barrier(cmd);
    for (int k = 0; k < count; k++) {
        Resource output{views[k], outputs[k], one, VK_FORMAT_R8G8B8A8_UNORM, width, height, 0, true};
        p.set("DLSSG.MultiFrameIndex", (unsigned)k + 1), p.set("DLSSG.OutputInterpolated", (void*)&output);
        if (!p.healthy()) return s.refused = true, explain(Parameters), 0;
        rec::boundary(rec::B_DIRECT);
        const Result result = s.evaluate(cmd, s.feature, p.raw(), nullptr);
        note_work(), descriptors_disturbed();
        if (failed(result)) {
            logf("vk: ANR FG: EvaluateFeature frame %d/%d refused (%#x)", k + 1, count, result);
            s.refused = true;
            full_barrier(cmd);   // Finish any partial provider writes before the built-in generator overwrites all outputs.
            return explain(Evaluation), 0;
        }
    }
    full_barrier(cmd);
    s.reset = false, s.sequence = sequence;
    if (prime) return explain(History), 0;   // Prime the provider's history; do not count its reset output as an interpolation.
    if (++s.made == 1 || s.made % 600 == 0)
        logf("vk: ANR FG: %llu sets recorded by the Vulkan provider (%dx, %ux%u)",
             (unsigned long long)s.made, count + 1, width, height);
    explain(Ready);
    return count;
#else
    (void)cmd, (void)color, (void)view, (void)width, (void)height, (void)count;
    (void)outputs, (void)views, (void)blend, (void)sequence;
    explain(Platform);
    return 0;
#endif
}

void anr_fg_idle() {
#if defined(_WIN32) && defined(_WIN64)
    State& s = state();
    if ((requested() && !present_native_pq.load(std::memory_order_relaxed)) ||
        !s.held.load(std::memory_order_relaxed)) return;
    std::lock_guard lock(s.mutex);
    std::unique_lock queue(dev.present_mu, std::defer_lock);
    if ((s.initialized || s.feature || s.depth.image || s.motion.image) && !queue_idle(queue)) return;
    release_feature(s);
    destroy(s.depth), destroy(s.motion), s.depth = {}, s.motion = {};
    if (s.initialized) s.shutdown(dev.device);
    s.initialized = false, s.tried = false, s.refused = false, s.have_previous = false, s.reset = true;
    s.inputs = s.used = 0;
    s.held = false;
    // Keep the module mapped: the provider may own threads, even after its Vulkan context is shut down.
#endif
}

void anr_fg_close() {
#if defined(_WIN32) && defined(_WIN64)
    State& s = state();
    std::lock_guard lock(s.mutex);
    s.closed = true;
    if (!s.held.load(std::memory_order_relaxed)) return;
    std::unique_lock queue(dev.present_mu, std::defer_lock);
    if ((s.initialized || s.feature || s.depth.image || s.motion.image) && !queue_idle(queue))
        return void(logf("vk: ANR FG: queue stayed busy at exit; resources left to process shutdown"));
    release_feature(s);
    destroy(s.depth), destroy(s.motion), s.depth = {}, s.motion = {};
    if (s.initialized) s.shutdown(dev.device);
    s.held = false;
#endif
}
} // namespace mkwvk
