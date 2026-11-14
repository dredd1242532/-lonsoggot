// The render targets' image pool's bound and its share of the textures' budget, and texture_frame's two looks at the
// driver's count around the dead list: vk_texture.cpp's own code, cut out by tests/test_target_pool_cap.py and run with
// stand-ins for Vulkan and the allocator. The test sets RX_VK_TARGET_POOL_MB=64.
#include "vram_compact.h"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <iterator>
#include <mutex>
#include <unordered_map>
#include <vector>

namespace mkwvk {

// ---- the stand-ins: Vulkan's handles, the allocator, the renderer's globals ----
struct VkImage_T;
struct VkImageView_T;
struct VkDeviceMemory_T;
struct VkBuffer_T;
using VkImage = VkImage_T*;
using VkImageView = VkImageView_T*;
using VkDeviceMemory = VkDeviceMemory_T*;
using VkBuffer = VkBuffer_T*;
using VmaAllocation = void*;
#define VK_NULL_HANDLE nullptr
struct Device {
    int device = 0;
};
Device dev;
uint64_t frame_no = 1;
int destroyed_images = 0, destroyed_views = 0;
// (the driver's count as vram_usage gives it: an image destroyed leaves its bytes free in VMA's blocks, which stay)
bool vram_known = true;
uint64_t vram_used = 0, vram_allowed = 0, vram_block_free = 0, an_image_frees = 0;
int looks = 0;
bool vram_usage(uint64_t* used, uint64_t* budget, uint64_t* block_free = nullptr) {
    looks++;
    *used = vram_used, *budget = vram_allowed;
    if (block_free) *block_free = vram_block_free;
    return vram_known;
}
bool compacted = false;   // (RX_VK_COMPACT: the textures' pool's own figures)
uint64_t textures_blocks = 0, textures_free = 0, textures_kept = 0;
bool compact_on() {
    return compacted;
}
bool textures_pool_usage(uint64_t* block_bytes, uint64_t* free_bytes, uint64_t* kept_bytes = nullptr) {
    *block_bytes = textures_blocks, *free_bytes = textures_free;
    if (kept_bytes) *kept_bytes = textures_kept;
    return true;
}
bool env_on(const char*) {
    return false;
}
struct Compact {
    bool in_pass = false;
};
Compact compact;
void destroy_image(VkImage, VmaAllocation) {
    destroyed_images++;
    vram_block_free += an_image_frees;
}
void vkDestroyImageView(int, VkImageView, std::nullptr_t) {
    destroyed_views++;
}
void destroy_buffer(VkBuffer, VmaAllocation) {}
void vkFreeMemory(int, VkDeviceMemory, std::nullptr_t) {}

/* DEAD */
std::deque<Dead> dead;

/* POOL */

/// texture_frame from its first look at the driver's count to where its budget's rules begin, and what those rules are
/// handed; reached is false on the presents that stop before them
struct Seen {
    bool reached, known, kept_only;
    uint64_t used, allowed, block_free, reclaim, pressure;
};
static Seen seen;
static void frame(uint64_t completed) {
    seen = {};
    /* FRAME */
    (void)own;
    seen = {true, known, kept_only, used, allowed, block_free, reclaim, pressure};
}

// ---- the checks ----
static int fails = 0;
static void fail(const char* what, int line) {
    std::printf("FAIL line %d: %s\n", line, what);
    fails++;
}
#define CHECK(c)                       \
    do {                               \
        if (!(c)) fail(#c, __LINE__);  \
    } while (0)

const uint64_t MB = 1ull << 20;
static uintptr_t handles = 0x1000;
template <class T>
static T handle() {
    return reinterpret_cast<T>(handles += 16);
}
/// A gone render target's image as the dead list holds it
static Dead gone(uint64_t key, uint64_t bytes) {
    Dead d{0, handle<VkImage>(), handle<VmaAllocation>(), {}, {}};
    d.pool_key = key, d.bytes = bytes;
    d.view_map[1] = handle<VkImageView>();
    return d;
}

static void pool_checks() {
    const uint64_t cap = pool_cap();
    CHECK(cap == 64 * MB);
    const uint64_t key = 0x51, other = 0x53;
    VkImage image = nullptr;
    VmaAllocation allocation = nullptr;
    std::unordered_map<uint64_t, VkImageView> views;

    // a put that would take the pool past its bound is refused, and what was refused is left whole for its caller
    Dead a = gone(key, 24 * MB), b = gone(key, 24 * MB), c = gone(other, 24 * MB);
    CHECK(pool_offer(a) && pool_offer(b));
    CHECK(pool_bytes == 48 * MB && pool_n == 2);
    const VkImage c_image = c.image;
    CHECK(!pool_offer(c));
    CHECK(pool_bytes == 48 * MB && pool_n == 2);
    CHECK(c.image == c_image && c.view_map.size() == 1 && c.pool_key == other);
    Dead d = gone(other, 16 * MB);
    const VkImage d_image = d.image;
    CHECK(pool_offer(d) && pool_bytes == cap);   // (up to the bound, not past it)
    Dead e = gone(other, 1);
    CHECK(!pool_offer(e) && pool_bytes == cap && pool_n == 3);

    // what the pool holds comes off the textures' budget: the textures within what is left, and the pool, are within it
    CHECK(budget_less_pool(1000 * MB) == 1000 * MB - 64 * MB);
    CHECK(budget_less_pool(1000 * MB) + pool_bytes == 1000 * MB);
    CHECK(budget_less_pool(10 * MB) == 0);   // (not a wrapped difference)

    // taken by the next texture of its key, views and all: out of the pool's count, and back in the budget
    CHECK(pool_take(other, image, allocation, views));
    CHECK(image == d_image && views.size() == 1 && pool_bytes == 48 * MB && pool_n == 2);
    CHECK(budget_less_pool(1000 * MB) == 952 * MB);
    CHECK(!pool_take(0x99, image, allocation, views));

    // VRAM short: none taken whatever room there is, and the trim gives all of it back, 64 a present at most
    pool_pressed = true;
    Dead p = gone(key, MB);
    CHECK(!pool_offer(p) && pool_n == 2);
    const int images_before = destroyed_images, views_before = destroyed_views;
    CHECK(pool_trim() == 2 && pool_trim() == 0);
    CHECK(pool_n == 0 && pool_bytes == 0 && budget_less_pool(1000 * MB) == 1000 * MB);
    CHECK(destroyed_images == images_before + 2 && destroyed_views == views_before + 2);
    pool_pressed = false;

    // one larger than the whole bound never, one of exactly the bound into an empty pool
    Dead big = gone(key, cap + 1), whole = gone(key, cap);
    CHECK(!pool_offer(big) && pool_n == 0);
    CHECK(pool_offer(whole) && pool_bytes == cap);
    CHECK(pool_take(key, image, allocation, views) && pool_bytes == 0);

    // the age rule alone is left to the trim: 120 presents, 16 a present
    frame_no = 1000;
    for (int i = 0; i < 20; i++) {
        Dead s = gone(key + 2 * (uint64_t)(i % 3), MB);
        CHECK(pool_offer(s));
    }
    frame_no = 1120;
    CHECK(pool_trim() == 0);
    frame_no = 1121;
    CHECK(pool_trim() == 16);
    CHECK(pool_trim() == 4 && pool_n == 0 && pool_bytes == 0);

    // offers, takes and trims in any order: never past the bound, and every image taken in leaves once, by a take or a trim
    uint64_t seed = 7;
    uint64_t in = 0, out = 0;
    const int destroyed_at = destroyed_images;
    for (int i = 0; i < 4000; i++) {
        seed = seed * 6364136223846793005ull + 1442695040888963407ull;
        Dead x = gone(0x60 + (seed >> 61), ((seed >> 33) % 20 + 1) * MB);
        const uint64_t before = pool_bytes;
        const bool took = pool_offer(x);
        in += took;
        if (pool_bytes > cap || pool_bytes != before + (took ? x.bytes : 0)) {
            fail("the pool went past its bound, or its count is not what it holds", __LINE__);
            break;
        }
        if (i % 3 == 0) out += pool_take(0x60 + ((seed >> 40) & 7), image, allocation, views);
        if (i % 40 == 0) frame_no += 50, pool_trim();
    }
    pool_pressed = true;
    while (pool_trim()) {}
    pool_pressed = false;
    CHECK(in > 100 && out > 0);   // (the loop did put and take)
    CHECK(pool_n == 0 && pool_bytes == 0 && in == out + (uint64_t)(destroyed_images - destroyed_at));
}

/// A dead image that is no pooled target's
static Dead plain(uint64_t value) {
    return Dead{value, handle<VkImage>(), handle<VmaAllocation>(), {}, {}};
}

static void frame_checks() {
    VkImage image = nullptr;
    VmaAllocation allocation = nullptr;
    std::unordered_map<uint64_t, VkImageView> views;
    CHECK(pool_n == 0 && dead.empty() && !pool_pressed);
    vram_allowed = 10000 * MB, an_image_frees = 200 * MB;

    // a tenth present, VRAM short by the first look (9400 of 10000 MB held): the pool takes nothing, and the dead images'
    // 800 MB, destroyed, are free in the blocks by the time the budget's rules look: 8600 MB held, under their 90% line
    frame_no = 20, looks = 0;
    vram_used = 9500 * MB, vram_block_free = 100 * MB;
    for (int i = 0; i < 3; i++) dead.push_back(plain(5));
    Dead target = gone(0x81, 8 * MB);
    target.value = 5;
    dead.push_back(std::move(target));
    dead.push_back(plain(99));   // (the GPU not done with it: it waits)
    const int destroyed_at = destroyed_images;
    frame(10);
    CHECK(pool_pressed && pool_n == 0 && st_pool_refused == 1);   // (the pool's admission: by the look before the frees)
    CHECK(destroyed_images == destroyed_at + 4 && dead.size() == 1 && dead.front().value == 99);
    CHECK(seen.reached && seen.known && seen.used == 9500 * MB && seen.allowed == 10000 * MB);
    CHECK(seen.block_free == 900 * MB && seen.reclaim == 900 * MB && seen.pressure == 8600 * MB);
    CHECK(seen.pressure <= seen.allowed / 10 * 9);   // (nothing live goes for what was just freed)
    CHECK(looks == 2);

    // the presents between: one look, for the pool, and the dead list and the trim as on any present
    frame_no = 21, looks = 0;
    dead.push_back(plain(5));
    frame(10);
    CHECK(!seen.reached && looks == 1 && dead.size() == 1 && destroyed_images == destroyed_at + 5);

    // VRAM not short: the pool takes the target's image, nothing is destroyed, and both looks see the same
    frame_no = 30, looks = 0;
    vram_used = 5000 * MB, vram_block_free = 0;
    Dead kept = gone(0x81, 8 * MB);
    kept.value = 5;
    dead.push_back(std::move(kept));
    frame(10);
    CHECK(!pool_pressed && pool_n == 1 && pool_bytes == 8 * MB && destroyed_images == destroyed_at + 5);
    CHECK(seen.reached && seen.pressure == 5000 * MB && seen.block_free == 0 && looks == 2);

    // what the trim destroys is seen as free too: the pooled image, 130 presents on
    frame_no = 160, looks = 0;
    frame(10);
    CHECK(pool_n == 0 && pool_bytes == 0 && destroyed_images == destroyed_at + 6);
    CHECK(seen.reached && seen.block_free == 200 * MB && seen.pressure == 4800 * MB);

    // compacted, only the textures' pool's free space comes off the count, and the look after the frees has its figures too
    compacted = true, textures_blocks = textures_kept = 1152 * MB, textures_free = 300 * MB;
    frame_no = 170;
    vram_used = 9500 * MB, vram_block_free = 100 * MB;
    for (int i = 0; i < 3; i++) dead.push_back(plain(5));
    frame(10);
    CHECK(pool_pressed);   // (9400 MB held at the first look)
    CHECK(seen.reached && seen.block_free == 700 * MB && seen.reclaim == 300 * MB && seen.pressure == 9200 * MB && seen.kept_only);
    textures_blocks = 1280 * MB;
    frame_no = 180;
    frame(10);
    CHECK(seen.reached && !seen.kept_only);
    compacted = false;

    // the driver gives no count: not known at either look, and the pool is not held back
    vram_known = false;
    frame_no = 190;
    frame(10);
    CHECK(seen.reached && !seen.known && !pool_pressed);
    vram_known = true;

    CHECK(dead.size() == 1 && !pool_take(0x81, image, allocation, views));
    dead.clear();
    an_image_frees = 0;
}

int run() {
    pool_checks();
    frame_checks();
    if (!fails) std::printf("target pool cap: all checks passed\n");
    return fails != 0;
}

} // namespace mkwvk

int main() {
    return mkwvk::run();
}
