// Authored game-free driver for the exact startup/rollback/host-table bodies. Vulkan, VMA and SDL are call recorders.
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <thread>
#include <vector>
#include "apple_api.h"

namespace mkwvk {
static unsigned checks;
static void check(bool ok, const char* message) {
    ++checks;
    if (!ok) { std::fprintf(stderr, "FAIL: %s\n", message); std::exit(1); }
}
template<class T> static T handle(uintptr_t x) { return reinterpret_cast<T>(x); }
struct {
    VkDevice device{}; VkPhysicalDevice physical{}; VkInstance instance{};
    VkPhysicalDeviceProperties props{}; VkPhysicalDeviceMemoryProperties memory{};
    uint32_t queue_family = 2, max_push_descriptors = 32;
    bool memory_budget = true, offscreen = true, push_descriptor = true, eds3_blend = true, custom_border_color = true;
    bool mesh_capable = true, mesh_shader = true;
} dev;
using VmaAllocator = void*;
constexpr uint32_t VMA_ALLOCATOR_CREATE_BUFFER_DEVICE_ADDRESS_BIT = 0x20, VMA_ALLOCATOR_CREATE_EXT_MEMORY_BUDGET_BIT = 8;
struct VmaVulkanFunctions { PFN_vkGetInstanceProcAddr vkGetInstanceProcAddr; PFN_vkGetDeviceProcAddr vkGetDeviceProcAddr; };
struct VmaAllocatorCreateInfo {
    uint32_t flags{}; VkPhysicalDevice physicalDevice{}; VkDevice device{}; VkInstance instance{};
    uint32_t vulkanApiVersion{}; const VmaVulkanFunctions* pVulkanFunctions{}; uint64_t preferredLargeHeapBlockSize{};
};
static PFN_vkGetDeviceProcAddr vkGetDeviceProcAddr;
static PFN_vkGetInstanceProcAddr instance_proc() { return nullptr; }
static VmaAllocator vma;
static unsigned call_count, fail_at, next_id = 1, parallel = 3;
static bool memory_allowed = true;
static std::map<uintptr_t, char> objects;
static std::map<uintptr_t, uintptr_t> command_buffers;
static bool fail() { return ++call_count == fail_at; }
template<class T> static VkResult make(T* out, char kind) {
    *out = handle<T>(0xdead);
    if (fail()) return VK_ERROR_OUT_OF_HOST_MEMORY;
    *out = handle<T>(++next_id); objects.emplace(next_id, kind); return VK_SUCCESS;
}
template<class T> static void drop(T object, char kind) {
    const uintptr_t id = (uintptr_t)object;
    check(objects.count(id) && objects[id] == kind, "rollback destroyed undefined output, wrong resource, or resource twice");
    objects.erase(id);
}
static VkResult vmaCreateAllocator(const VmaAllocatorCreateInfo* ci, VmaAllocator* out) {
    check(ci->vulkanApiVersion == VK_API_VERSION_1_3 && ci->preferredLargeHeapBlockSize == (64ull << 20), "VMA startup inputs changed");
    check(ci->flags == (VMA_ALLOCATOR_CREATE_BUFFER_DEVICE_ADDRESS_BIT | VMA_ALLOCATOR_CREATE_EXT_MEMORY_BUDGET_BIT), "VMA device-address/budget flags");
    return make(out, 'A');
}
static void vmaDestroyAllocator(VmaAllocator a) { check(objects.size() == 1, "allocator destroyed before startup resources"); drop(a, 'A'); }
static VkResult vkCreateSemaphore(VkDevice, const VkSemaphoreCreateInfo*, const VkAllocationCallbacks*, VkSemaphore* out) { return make(out, 'S'); }
static VkResult vkCreateFence(VkDevice, const VkFenceCreateInfo*, const VkAllocationCallbacks*, VkFence* out) { return make(out, 'F'); }
static VkResult vkCreateCommandPool(VkDevice, const VkCommandPoolCreateInfo* ci, const VkAllocationCallbacks*, VkCommandPool* out) {
    check(ci->queueFamilyIndex == dev.queue_family, "wrong startup queue family"); return make(out, 'P');
}
static VkResult vkAllocateCommandBuffers(VkDevice, const VkCommandBufferAllocateInfo* ci, VkCommandBuffer* out) {
    check(ci->commandBufferCount == 1 && ci->level == VK_COMMAND_BUFFER_LEVEL_PRIMARY, "unexpected startup command allocation");
    check(objects.count((uintptr_t)ci->commandPool) && objects[(uintptr_t)ci->commandPool] == 'P', "allocation through an unowned command pool");
    *out = handle<VkCommandBuffer>(0xbad);
    if (fail()) return VK_ERROR_OUT_OF_DEVICE_MEMORY;
    *out = handle<VkCommandBuffer>(++next_id); command_buffers.emplace(next_id, (uintptr_t)ci->commandPool); return VK_SUCCESS;
}
static void vkDestroyCommandPool(VkDevice, VkCommandPool p, const VkAllocationCallbacks*) {
    for (auto it = command_buffers.begin(); it != command_buffers.end();) {
        if (it->second == (uintptr_t)p) it = command_buffers.erase(it); else ++it;
    }
    drop(p, 'P');
}
static void vkDestroySemaphore(VkDevice, VkSemaphore s, const VkAllocationCallbacks*) { drop(s, 'S'); }
static void vkDestroyFence(VkDevice, VkFence f, const VkAllocationCallbacks*) { drop(f, 'F'); }
static bool memory_init() { return memory_allowed; }
static void menu_init() {}
static int par_threads() { return (int)parallel; }
static void logf(const char*, ...) {}
static bool vma_init(PFN_vkGetInstanceProcAddr);
static void vma_startup_rollback();
#include "startup_types.inc"
static Recording recordings[48];
static Frame frames[3];
static VkSemaphore timeline, batch_tl, rec_timeline;
#include "startup_actual.inc"

static uint64_t wired_lo, wired_hi;
static unsigned wired_pins, wired_unpins;
static int hos_posix_guest_import_pin(uint64_t lo, uint64_t hi) { wired_lo = lo; wired_hi = hi; ++wired_pins; return 1; }
static void hos_posix_guest_import_unpin(uint64_t lo, uint64_t hi) {
    check(lo == wired_lo && hi == wired_hi, "host table unpin changed 64-bit range"); ++wired_unpins;
}
#include "host_wiring.inc"

static void no_resources() {
    check(objects.empty() && command_buffers.empty() && !vma && !timeline && !batch_tl && !rec_timeline, "failed startup leaked a resource");
    for (const Recording& r : recordings) check(!r.pool && !r.upload_pool && !r.init_pool && !r.cmd && !r.upload && !r.init && !r.fence,
                                               "failed startup retained recording handles");
    for (const Frame& f : frames) check(!f.pool && !f.cmd && !f.done && !f.acquired, "failed startup retained frame handles");
}
static void run_startup() {
    check(vk_host.version == 23 && sizeof(MkwVkHost) == 384, "Apple host table not at exact API23 layout");
    check(vk_host.guest_import_pin(0x7f12340000ull, 0x7f12348000ull) == 1, "host table did not call its real pin provider slot");
    vk_host.guest_import_unpin(0x7f12340000ull, 0x7f12348000ull);
    check(wired_pins == 1 && wired_unpins == 1, "pin/unpin wiring duplicated or skipped a call");
    memory_allowed = false; check(!replay_start(), "startup ignored memory admission failure"); no_resources(); memory_allowed = true;
    fail_at = 0; call_count = 0; check(replay_start(), "valid startup failed");
    const unsigned sites = call_count;
    check(command_buffers.size() == 147, "startup did not create main/upload/init buffers and frame buffers");
    replay_start_rollback(); no_resources();
    unsigned injected = 0;
    for (unsigned site = 1; site <= sites; ++site) {
        fail_at = site; call_count = 0;
        const bool ok = replay_start();
        // The two optional recording/batch timelines keep their existing fallback; no required object may be missing.
        check(ok == (site == 3 || site == 4), "startup success/failure contract at injected resource allocation");
        if (ok) replay_start_rollback();
        no_resources(); ++injected;
    }
    fail_at = 0; call_count = 0; parallel = 0;
    check(replay_start() && command_buffers.size() == 99, "serial startup changed or made parallel-only pools");
    replay_start_rollback(); no_resources();
    std::printf("PASS: %u exact startup allocation failures, Apple23 typed host callback wiring, serial/parallel resources and rollback; no Vulkan driver\n", injected);
}
} // namespace mkwvk

namespace Messages {
using mkwvk::check;
using HWND = void*;
using LPCSTR = const char*;
using UINT = unsigned;
constexpr int RTLD_NOW = 2, RTLD_NOLOAD = 4;
static std::string allowed;
static std::vector<std::string> opened;
static unsigned shown, closed;
static bool symbol = true;
static int show(uint32_t flags, const char* caption, const char* text, void* window) {
    check(flags == 0x10 && caption && text && !window, "SDL message values"); ++shown; return 0;
}
static void* dlopen(const char* name, int flags) {
    check(flags == (RTLD_NOW | RTLD_NOLOAD), "notice loaded an unowned SDL library");
    opened.emplace_back(name); return name == allowed ? reinterpret_cast<void*>(1) : nullptr;
}
static void* dlsym(void*, const char* name) {
    check(!std::strcmp(name, "SDL_ShowSimpleMessageBox"), "wrong SDL message entrypoint");
    return symbol ? reinterpret_cast<void*>(&show) : nullptr;
}
static int dlclose(void*) { ++closed; return 0; }
namespace Apple {
#include "message_apple.inc"
}
namespace Linux {
#include "message_linux.inc"
}
static void run() {
    allowed = "libSDL3.0.dylib";
    Apple::MessageBoxA(nullptr, "fixture", "fixture", 0);
    check(opened == std::vector<std::string>{allowed} && shown == 1 && closed == 1, "Darwin SDL3 loaded-library route");
    opened.clear(); allowed = "libSDL2-2.0.0.dylib";
    Apple::MessageBoxA(nullptr, "fixture", "fixture", 0);
    check(opened == std::vector<std::string>({"libSDL3.0.dylib", allowed}) && shown == 2 && closed == 2, "Darwin SDL2 loaded-library route");
    opened.clear(); allowed.clear(); Apple::MessageBoxA(nullptr, "fixture", "fixture", 0);
    check(shown == 2 && closed == 2, "missing SDL invented a GUI call or reference");
    opened.clear(); allowed = "libSDL2-2.0.so.0";
    Linux::MessageBoxA(nullptr, "fixture", "fixture", 0);
    check(opened == std::vector<std::string>({"libSDL3.so.0", allowed}) && shown == 3 && closed == 3, "Linux SDL names changed");
    symbol = false; Apple::MessageBoxA(nullptr, nullptr, nullptr, 0);
    check(shown == 3, "missing SDL symbol was called");
}
} // namespace Messages

int main() {
    mkwvk::run_startup(); Messages::run();
    std::printf("PASS: %u startup/ABI/resource checks; source-only fixture, no SDL window or GPU\n", mkwvk::checks);
}
