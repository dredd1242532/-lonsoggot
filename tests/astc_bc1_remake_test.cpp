// (longshot, RX_VK_ASTC_BC1) A texture kept as BC1 made again as BC3 (bc1_to_bc3), the upload that judges the blocks it stages
// (upload_astc_gpu), the GPU copies between textures kept as BC of two formats (texture_copy_into_bc), as gpu/vk/vk_texture.cpp
// has them, and the draw's handle cache (vk_draw.cpp's texture_view_of): tests/test_astc_bc1_remake.py pastes the real functions
// and types over the markers below. Vulkan and the rest of the cache are modelled: every command is recorded.
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <memory>
#include <unordered_map>
#include <utility>
#include <vector>

#include "astc_opaque.h"   // (gpu/vk: the rule the upload judges by, and this file's own check of what was staged)

namespace fixture {
void require(bool ok, const char* why) { if (!ok) { fprintf(stderr, "FAIL: %s\n", why); std::exit(1); } }
template<class T> T handle(uintptr_t n) { return reinterpret_cast<T>(n); }

struct VmaAllocation_T { uint64_t size; };
using VmaAllocation = VmaAllocation_T*;
std::vector<std::unique_ptr<VmaAllocation_T>> allocations;
VmaAllocation allocate(uint64_t size) {
    allocations.push_back(std::make_unique<VmaAllocation_T>(VmaAllocation_T{size}));
    return allocations.back().get();
}
uint64_t allocation_size(VmaAllocation a) { return a ? a->size : 0; }

/* DEAD */
std::deque<Dead> dead;

struct Texture {
    uint32_t id = 7;
    uint64_t gpu = 0x1000;
    VkImage image = VK_NULL_HANDLE;
    VmaAllocation allocation = nullptr;
    VkFormat format = VK_FORMAT_R8G8B8A8_UNORM;
    VkImageUsageFlags usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_SAMPLED_BIT;
    VkImageCreateFlags create_flags = VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT | VK_IMAGE_CREATE_BLOCK_TEXEL_VIEW_COMPATIBLE_BIT;
    VkImageType type = VK_IMAGE_TYPE_2D;
    VkSampleCountFlagBits samples = VK_SAMPLE_COUNT_1_BIT;
    VkImageAspectFlags aspect = VK_IMAGE_ASPECT_COLOR_BIT;
    uint32_t width = 64, height = 32, depth = 1, levels = 1, layers = 1, skip = 0;
    struct { uint32_t width = 64, height = 32; } d;
    int block_w = 4, block_h = 4;
    struct { uint64_t layer_size = 0; } L;
    bool astc = true, bc = false, bc1 = false, bc_resized = false, uploaded = false;
    uint64_t bytes = 0, pad = 0, last_value = 0;
    std::unordered_map<uint64_t, VkImageView> views;
};

uint64_t frame_no = 1, rec_value = 1, total_bytes = 0, destroy_gen = 1, st_bc1_back = 0;
constexpr uint32_t kBcDim = 2048, kBcLayers = 8;
const VkImage bc_scratch = handle<VkImage>(0x5C);
const VkCommandBuffer the_cmd = handle<VkCommandBuffer>(0xC0), the_upload_cmd = handle<VkCommandBuffer>(0xC1);
VkCommandBuffer want_cmd = the_cmd;   // (where the code under test records: an upload's commands go to upload_cmd())

// What was recorded, in order. S: the scratch's layout asked for. L: an image put into GENERAL from UNDEFINED. B: a blit into
// the scratch. C: a copy into the scratch. E: the scratch encoded into a texture's image (R: resized first). F: the staged
// ASTC blocks flushed. D: the ASTC decoder run over n layers of them (x: where in the staging they start)
struct Op {
    char kind;
    VkImage image;     // (B and C: the source; E, R and L: the destination)
    VkFormat format;   // (E and R: the destination texture's format when it was encoded into, and whether it was BC1 still)
    bool bc1;
    uint32_t level, layer, n, w, h;
    int x, y;          // (B and C: the source's corner; E: the destination's)
};
std::vector<Op> ops;
size_t count(char kind) { return (size_t)std::count_if(ops.begin(), ops.end(), [=](const Op& o) { return o.kind == kind; }); }

bool ready = true, bc7 = false, compact = true, fail_scratch = false, fail_image = false, fail_pool = false, no_blit_src = false;
int create_calls = 0;
bool made_in_pool = false;
VkImageCreateInfo made_ci{};
uint64_t next_allocation = 0;   // (the size the next image's allocation reports)
uintptr_t next_image = 0x100;

bool bc_ready() { return ready; }
bool bc7_enabled() { return bc7; }
bool compact_on() { return compact; }
bool bc_scratch_layout(VkCommandBuffer cmd, bool resized = false) {
    require(cmd == want_cmd, "the scratch is laid out in the recording's command buffer");
    if (fail_scratch) return false;
    ops.push_back({'S', bc_scratch, VK_FORMAT_UNDEFINED, false, 0, 0, resized ? 2u : 1u, 0, 0, 0, 0});
    return true;
}
bool create_image(const VkImageCreateInfo& ci, VkImage& image, VmaAllocation& allocation, bool dedicated = false,
                  bool target = false, bool textures = false) {
    require(!dedicated && !target, "the BC3 image is no dedicated allocation and no render target's");
    create_calls++;
    if (fail_image || (textures && fail_pool)) return false;
    made_ci = ci, made_in_pool = textures;
    allocation = allocate(next_allocation);
    image = handle<VkImage>(next_image++);
    return true;
}
struct InitRedirect { void* to = nullptr; };   // (no parallel replay here: the recording's own command buffer)
VkCommandBuffer upload_cmd() { return the_upload_cmd; }
VkFormatProperties format_props(VkFormat) {
    VkFormatProperties p{};
    p.optimalTilingFeatures = VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT;
    if (!no_blit_src) p.optimalTilingFeatures |= VK_FORMAT_FEATURE_BLIT_SRC_BIT;
    return p;
}
void logf(const char*, ...) {}
void full_barrier(VkCommandBuffer cmd, const char* = "") { require(cmd == want_cmd, "barriers in the recording's command buffer"); }
void vkCmdPipelineBarrier2(VkCommandBuffer cmd, const VkDependencyInfo* di) {
    require(cmd == want_cmd, "with no parallel replay the new image's layout is set in the recording's command buffer");
    require(di->imageMemoryBarrierCount == 1, "one image barrier");
    const VkImageMemoryBarrier2& b = di->pImageMemoryBarriers[0];
    require(b.oldLayout == VK_IMAGE_LAYOUT_UNDEFINED && b.newLayout == VK_IMAGE_LAYOUT_GENERAL, "the new image: UNDEFINED to GENERAL");
    require(b.subresourceRange.levelCount == VK_REMAINING_MIP_LEVELS && b.subresourceRange.layerCount == VK_REMAINING_ARRAY_LAYERS,
            "every level and layer of the new image");
    ops.push_back({'L', b.image, VK_FORMAT_UNDEFINED, false, 0, 0, 0, 0, 0, 0, 0});
}
void vkCmdBlitImage(VkCommandBuffer cmd, VkImage src, VkImageLayout, VkImage dst, VkImageLayout, uint32_t regions,
                    const VkImageBlit* r, VkFilter filter) {
    require(cmd == want_cmd && dst == bc_scratch && regions == 1 && filter == VK_FILTER_NEAREST, "a blit: one region into the scratch, nearest");
    require(r->dstSubresource.mipLevel == 0 && r->dstSubresource.baseArrayLayer == 0 &&
            r->dstSubresource.layerCount == r->srcSubresource.layerCount, "a blit: into the scratch's first layers");
    require(r->dstOffsets[0].x == 0 && r->dstOffsets[0].y == 0 && r->dstOffsets[0].z == 0 && r->dstOffsets[1].z == 1 &&
            r->srcOffsets[0].z == 0 && r->srcOffsets[1].z == 1, "a blit: into the scratch's corner");
    const int w = r->srcOffsets[1].x - r->srcOffsets[0].x, h = r->srcOffsets[1].y - r->srcOffsets[0].y;
    require(w > 0 && h > 0 && r->dstOffsets[1].x == w && r->dstOffsets[1].y == h, "a blit: texel for texel, not scaled");
    ops.push_back({'B', src, VK_FORMAT_UNDEFINED, false, r->srcSubresource.mipLevel, r->srcSubresource.baseArrayLayer,
                   r->srcSubresource.layerCount, (uint32_t)w, (uint32_t)h, r->srcOffsets[0].x, r->srcOffsets[0].y});
}
void vkCmdCopyImage(VkCommandBuffer cmd, VkImage src, VkImageLayout, VkImage dst, VkImageLayout, uint32_t regions,
                    const VkImageCopy* r) {
    require(cmd == want_cmd && dst == bc_scratch && regions == 1, "a copy: one region into the scratch");
    ops.push_back({'C', src, VK_FORMAT_UNDEFINED, false, r->srcSubresource.mipLevel, r->srcSubresource.baseArrayLayer,
                   r->srcSubresource.layerCount, r->extent.width, r->extent.height, r->srcOffset.x, r->srcOffset.y});
}
void bc_encode_into(VkCommandBuffer cmd, uint32_t w, uint32_t h, uint32_t n, Texture& dst, uint32_t level, uint32_t layer, int dx,
                    int dy, VkImageView = VK_NULL_HANDLE) {
    require(cmd == want_cmd, "encodes in the recording's command buffer");
    ops.push_back({'E', dst.image, dst.format, dst.bc1, level, layer, n, w, h, dx, dy});
}
void bc_encode_resized(VkCommandBuffer cmd, uint32_t w, uint32_t h, uint32_t, uint32_t, uint32_t n, Texture& dst, uint32_t level,
                       uint32_t layer) {
    require(cmd == want_cmd, "encodes in the recording's command buffer");
    ops.push_back({'R', dst.image, dst.format, dst.bc1, level, layer, n, w, h, 0, 0});
}

// ---- what upload_astc_gpu takes besides: the game's memory, the staging, the ASTC decoder ----
/* TYPES */
struct { struct { struct { uint64_t minStorageBufferOffsetAlignment = 16; } limits; } props; } dev;
using Ranges = std::vector<std::pair<uint64_t, uint64_t>>;
bool overlaps(const Ranges* r, uint64_t a, uint64_t b) {
    return !r || std::any_of(r->begin(), r->end(), [=](auto x) { return x.first < b && a < x.second; });
}
thread_local const char* astc_fail = nullptr;
int64_t st_bytes = 0, st_astc = 0;
bool astc_ready() { return true; }
const VkPipeline astc_pipe = handle<VkPipeline>(0xA5);
const VkPipelineLayout astc_layout = handle<VkPipelineLayout>(0xA6);
const VkImageView bc_scratch_view = handle<VkImageView>(0x5D);
VkImageView astc_level_view(Texture*, uint32_t, uint32_t) { return handle<VkImageView>(0xA7); }

// The game's memory, the texture's storage at address 0: a layer is 1024 bytes, level 0 its first 512 (2 x 2 blocks of 4 x 4
// texels, block linear: at 0, 64, 16 and 80) and level 1 the next (one block)
constexpr uint64_t kLayer = 1024;
const uint8_t kOpaque[16] = {0x42, 0x00, 0x01};   // (a 4 x 4 weight grid, one partition, RGB endpoints: astc_opaque_test.cpp)
const uint8_t kAlpha[16] = {0x42, 0x80, 0x01};    // (the same with RGBA endpoints: not opaque)
std::vector<uint8_t> guest, staging_bytes;
int guest_reads = 0;
bool guest_private = false, fail_staging = false;   // (guest_private: read as a copy, as where the capture kept the storage)
struct { bool armed = false; uint32_t layer = 0; } game_writes;   // (the game writes alpha into this layer during an upload)
void put(uint32_t layer, uint32_t level, uint32_t i, const uint8_t* block) {
    static const uint64_t at[4] = {0, 64, 16, 80};
    std::memcpy(guest.data() + layer * kLayer + (level ? 512 : at[i]), block, 16);
}
LevelGeo level_geo(const Texture& tex, uint32_t level) {
    // (the game's write lands while a layer's last level is looked up: after its memory was first read, and in an upload that
    // judged the memory where it lies, after its first level was judged and before any of it is staged)
    if (game_writes.armed && guest_reads && level + 1 == tex.levels) {
        game_writes.armed = false;
        put(game_writes.layer, 0, 0, kAlpha);
    }
    LevelGeo g{};
    g.offset = level * 512ull;
    g.w = std::max(1u, tex.d.width >> level), g.h = std::max(1u, tex.d.height >> level), g.d = 1;
    g.tw = (g.w + 3) / 4, g.th = (g.h + 3) / 4, g.td = 1;
    return g;
}
const uint8_t* guest_bytes(uint64_t gpu, uint64_t n, std::vector<uint8_t>& scratch) {
    require(gpu <= guest.size() && n <= guest.size() - gpu, "the upload reads inside the texture's storage");
    guest_reads++;
    if (!guest_private) return guest.data() + gpu;   // (in place: the memory the game goes on writing)
    scratch.assign(guest.data() + gpu, guest.data() + gpu + n);
    return scratch.data();
}
uint64_t staging_taken = 0;   // (the bytes uploads took of the staging: an arena keeps them until its submission is done)
Staging staging(uint64_t n, uint64_t = 256) {
    if (fail_staging) return {};
    staging_taken += n;
    staging_bytes.assign(n, 0);   // (zeros: what a layer the storage does not hold leaves there is no opaque block)
    return {handle<VkBuffer>(0x5B), 0, staging_bytes.data(), allocate(n)};
}
void flush_mapped_range(VmaAllocation, uint64_t, uint64_t) {
    ops.push_back({'F', VK_NULL_HANDLE, VK_FORMAT_UNDEFINED, false, 0, 0, 0, 0, 0, 0, 0});
}
uint64_t decode_from = 0;
void vkCmdBindPipeline(VkCommandBuffer cmd, VkPipelineBindPoint, VkPipeline) {
    require(cmd == want_cmd, "pipelines bound in the recording's command buffer");
}
void vkCmdPushDescriptorSetKHR(VkCommandBuffer, VkPipelineBindPoint, VkPipelineLayout, uint32_t, uint32_t,
                               const VkWriteDescriptorSet* w) {
    decode_from = w[0].pBufferInfo->offset;
}
void vkCmdPushConstants(VkCommandBuffer, VkPipelineLayout, VkShaderStageFlags, uint32_t, uint32_t, const void*) {}
void vkCmdDispatch(VkCommandBuffer cmd, uint32_t, uint32_t, uint32_t n) {
    require(cmd == want_cmd, "the decoder runs in the recording's command buffer");
    ops.push_back({'D', VK_NULL_HANDLE, VK_FORMAT_UNDEFINED, false, 0, 0, n, 0, 0, (int)decode_from, 0});
}

/* ACTUAL */

// ---- what vk_draw.cpp's texture_view_of takes: the slots, the cache's lookups, the once-a-submission touch ----
enum class ViewUse { Sample, Attachment, Storage };
struct nvn_tex { struct { int target; uint32_t format, width, height, depth, levels; } d; uint64_t gpu; };
struct nvn_view { bool set_format, set_target; int format, target; };
struct ParLock { ParLock() {} ~ParLock() {} };
thread_local VkFormat last_view_format = VK_FORMAT_UNDEFINED;
Texture* bound = nullptr;   // (the texture the slot names)
uint32_t epoch = 1;
int gets = 0, touches = 0;
bool touch_uploads = false;
std::unordered_map<VkImageView, VkImage> view_image;   // (each view made, and the image it is of)
uintptr_t next_view = 0xB00;
uint32_t texture_slot_version(int) { return 1; }
uint64_t texture_destroy_generation() { return destroy_gen; }
uint32_t texture_epoch() { return epoch; }
bool texture_of(int, nvn_tex* t, nvn_view* v, bool* has_view) {
    *t = {}, *v = {}, *has_view = false;
    return bound != nullptr;
}
Texture* texture_get(const nvn_tex*, bool) { gets++; return bound; }
bool texture_touch_quick(Texture*, bool) { return false; }   // (its memory may have been written: the full touch)
Texture* texture_touch(Texture* tex, bool) {
    touches++;
    // (as texture_touch does for a texture whose memory the game wrote: the upload, which judges what it stages)
    if (touch_uploads) require(upload_astc_gpu(*tex, tex->layers * kLayer, nullptr), "the touch's upload is done");
    return tex;
}
VkImageView texture_view(Texture* tex, const nvn_tex*, const nvn_view*, VkImageViewType, ViewUse, VkFormat* = nullptr,
                         VkFormat = VK_FORMAT_UNDEFINED) {
    VkImageView& view = tex->views[0x51E];   // (the texture's own cache of views, emptied when its image goes)
    if (!view) view = handle<VkImageView>(next_view++), view_image[view] = tex->image;
    return view;
}
unsigned view_level(const nvn_view*) { return 0; }
unsigned image_level(const Texture*, unsigned level) { return level; }
/* HANDLE */
std::unordered_map<uint64_t, HandleHit>& handle_hits() { static std::unordered_map<uint64_t, HandleHit> h; return h; }
/* DRAW */

const VkImageView view_a = handle<VkImageView>(0xA1), view_b = handle<VkImageView>(0xA2);

/// The bytes of a w x h texture's levels and layers in 4 x 4 blocks of `block` bytes
uint64_t chain(uint32_t w, uint32_t h, uint32_t levels, uint32_t layers, uint32_t block) {
    uint64_t blocks = 0;
    for (uint32_t l = 0; l < levels; l++) blocks += (uint64_t)((std::max(1u, w >> l) + 3) / 4) * ((std::max(1u, h >> l) + 3) / 4);
    return blocks * layers * block;
}
/// A decoded ASTC texture as the cache counts it: kept as BC1 (block 8), as BC3 (16), or RGBA8 (0)
Texture texture(uint32_t block, uint32_t w = 64, uint32_t h = 32, uint32_t levels = 3, uint32_t layers = 2, bool uploaded = true) {
    Texture t;
    t.width = t.d.width = w, t.height = t.d.height = h, t.levels = levels, t.layers = layers, t.uploaded = uploaded;
    t.bc = block != 0, t.bc1 = block == 8;
    t.format = block == 8 ? VK_FORMAT_BC1_RGB_UNORM_BLOCK : block == 16 ? VK_FORMAT_BC3_UNORM_BLOCK : VK_FORMAT_R8G8B8A8_UNORM;
    if (block) t.usage |= VK_IMAGE_USAGE_STORAGE_BIT;   // (create() adds it where the driver lets a compressed image have it)
    t.bytes = block ? chain(w, h, levels, layers, block) : (uint64_t)w * h * 4 * layers, t.pad = 4096;
    t.image = handle<VkImage>(next_image++);
    t.allocation = allocate(t.bytes + t.pad);
    t.views = {{1, view_a}, {2, view_b}, {3, VK_NULL_HANDLE}};   // (a view that could not be made is a null one)
    t.last_value = 3;
    total_bytes += t.bytes + t.pad;
    return t;
}

struct Snapshot {
    VkImage image; VmaAllocation allocation; VkFormat format; VkImageUsageFlags usage; bool bc1; uint64_t bytes, pad, last_value;
    size_t views, dead_n, ops_n; uint64_t total, gen, back;
    explicit Snapshot(const Texture& t)
        : image(t.image), allocation(t.allocation), format(t.format), usage(t.usage), bc1(t.bc1), bytes(t.bytes), pad(t.pad),
          last_value(t.last_value), views(t.views.size()), dead_n(dead.size()), ops_n(ops.size()), total(total_bytes),
          gen(destroy_gen), back(st_bc1_back) {}
    bool same(const Texture& t) const {
        return image == t.image && allocation == t.allocation && format == t.format && usage == t.usage && bc1 == t.bc1 &&
               bytes == t.bytes && pad == t.pad && last_value == t.last_value && views == t.views.size() &&
               dead_n == dead.size() && ops_n == ops.size() && total == total_bytes && gen == destroy_gen && back == st_bc1_back;
    }
};

void fresh() {   // (a new present: the conversions' cap starts again)
    frame_no++;
    ops.clear(), dead.clear();
    create_calls = 0;
    ready = true, bc7 = false, compact = true, fail_scratch = fail_image = fail_pool = no_blit_src = false;
    next_allocation = 0;
    want_cmd = the_cmd;
}

/// tex's image made again: the checks every successful conversion passes. first_op: where its commands start in ops
void check_converted(const Texture& tex, const Snapshot& was, VkFormat want, size_t first_op) {
    require(tex.format == want && !tex.bc1 && tex.bc, "the texture is BC3 (or BC7) now, kept as BC still");
    require(tex.image != was.image && tex.image && tex.allocation != was.allocation && tex.allocation, "it has a new image");
    require(made_ci.format == want && made_ci.extent.width == tex.width && made_ci.extent.height == tex.height &&
            made_ci.extent.depth == tex.depth && made_ci.mipLevels == tex.levels && made_ci.arrayLayers == tex.layers &&
            made_ci.imageType == tex.type && made_ci.samples == tex.samples && made_ci.flags == tex.create_flags &&
            made_ci.tiling == VK_IMAGE_TILING_OPTIMAL && made_ci.initialLayout == VK_IMAGE_LAYOUT_UNDEFINED,
            "the new image is the old one's size, levels, layers and flags in the new format");
    const VkImageUsageFlags usage = was.usage & ~(VkImageUsageFlags)(VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT);
    require(made_ci.usage == usage && tex.usage == usage, "the new image's usage is the old one's without the raw block views'");
    // the count: the BC1 image's bytes out, the BC3 image's in (twice the blocks' bytes, and what its allocation is over that)
    require(tex.bytes == was.bytes * 2, "its blocks are counted at 16 bytes, twice BC1's");
    require(tex.pad == std::max(tex.bytes, next_allocation) - tex.bytes, "what the new allocation is over its blocks is counted");
    require(total_bytes == was.total - (was.bytes + was.pad) + tex.bytes + tex.pad, "the textures' count: the BC1 image out, the BC3 image in");
    // the old image: to the dead list with its views, freed once the GPU is done with what is recorded now
    require(dead.size() == was.dead_n + 1, "the BC1 image goes to the dead list, once");
    const Dead& d = dead.back();
    require(d.image == was.image && d.allocation == was.allocation, "the dead entry is the BC1 image and its allocation");
    require(d.value == std::max(was.last_value, rec_value), "the BC1 image waits for the recording that still reads it");
    require(d.views.size() == 2 && std::count(d.views.begin(), d.views.end(), view_a) == 1 &&
            std::count(d.views.begin(), d.views.end(), view_b) == 1, "its views go with it, the null one left out");
    require(d.memory.empty() && !d.pool_key && !d.buffer, "the dead entry is an image's, nothing else");
    require(tex.views.empty(), "the texture's views are made again when next asked for");
    require(destroy_gen == was.gen + 1, "what was resolved to the old image's views is stale");
    require(tex.last_value == std::max(was.last_value, rec_value), "the new image is in use by the recording");
    require(st_bc1_back == was.back + 1, "the conversion is counted");
    // the commands: the scratch laid out if the old texels are needed, the new image into GENERAL, then every level and layer
    // blitted from the BC1 image into the scratch and encoded into the new one
    size_t at = first_op;
    if (tex.uploaded) require(ops.size() > at && ops[at++].kind == 'S', "the scratch is laid out before the image is made");
    require(ops.size() > at && ops[at].kind == 'L' && ops[at].image == tex.image, "the new image is put into GENERAL");
    at++;
    if (tex.uploaded)
        for (uint32_t level = 0; level < tex.levels; level++)
            for (uint32_t layer = 0; layer < tex.layers; layer++) {
                const uint32_t w = std::max(1u, tex.width >> level), h = std::max(1u, tex.height >> level);
                require(ops.size() >= at + 2, "every level and layer the BC1 image held is encoded into the new one");
                const Op& b = ops[at++];
                require(b.kind == 'B' && b.image == was.image && b.level == level && b.layer == layer && b.n == 1 && b.w == w &&
                        b.h == h && !b.x && !b.y, "a level's layer is blitted whole from the BC1 image");
                const Op& e = ops[at++];
                require(e.kind == 'E' && e.image == tex.image && e.format == want && !e.bc1 && e.level == level &&
                        e.layer == layer && e.n == 1 && e.w == w && e.h == h && !e.x && !e.y,
                        "and encoded into the new image, as BC3, at the same level and layer");
            }
    require(ops.size() == at || ops[at].kind == 'S' || ops[at].kind == 'F',
            "a texture that was never uploaded has no texels to encode");
}

void remake() {
    {   // an uploaded texture: its texels are kept
        fresh();
        total_bytes = 1000, rec_value = 9;
        Texture tex = texture(8);
        const Snapshot was(tex);
        next_allocation = tex.bytes * 2 + 8192;
        require(bc1_to_bc3(tex, the_cmd, true), "a BC1 texture's image is made again as BC3");
        check_converted(tex, was, VK_FORMAT_BC3_UNORM_BLOCK, 0);
        require(ops.size() == 2 + 2 * 3 * 2 && count('C') == 0, "three levels of two layers: six blits and six encodes");
        require(made_in_pool && create_calls == 1, "with the textures' pool on the image comes from it");
        require(tex.pad == 8192 && total_bytes == 1000 + tex.bytes + 8192, "the count holds the new image alone");
    }
    {   // one never uploaded: no texels, and no scratch
        fresh();
        Texture tex = texture(8, 128, 128, 4, 1, false);
        tex.last_value = 30, rec_value = 9;
        const Snapshot was(tex);
        fail_scratch = true;
        require(bc1_to_bc3(tex, the_cmd, true), "a BC1 texture not uploaded yet needs no scratch");
        check_converted(tex, was, VK_FORMAT_BC3_UNORM_BLOCK, 0);
        require(ops.size() == 1 && dead.back().value == 30, "only its layout is set; the old image waits for its own last use");
    }
    {   // BC7 where the BC3 textures are BC7
        fresh();
        bc7 = true;
        Texture tex = texture(8);
        const Snapshot was(tex);
        require(bc1_to_bc3(tex, the_cmd, false), "a BC1 texture's image is made again as BC7");
        check_converted(tex, was, VK_FORMAT_BC7_UNORM_BLOCK, 0);
    }
    {   // the textures' pool refuses: a plain image; no pool: a plain image at once
        fresh();
        fail_pool = true;
        Texture tex = texture(8);
        const Snapshot was(tex);
        require(bc1_to_bc3(tex, the_cmd, true) && create_calls == 2 && !made_in_pool, "refused by the pool, the image is made outside it");
        check_converted(tex, was, VK_FORMAT_BC3_UNORM_BLOCK, 0);
        fresh();
        compact = false;
        Texture other = texture(8);
        const Snapshot other_was(other);
        require(bc1_to_bc3(other, the_cmd, true) && create_calls == 1 && !made_in_pool, "with no textures' pool the image is a plain one");
        check_converted(other, other_was, VK_FORMAT_BC3_UNORM_BLOCK, 0);
    }
    {   // what cannot be done changes nothing: the caller tries again, the texture is BC1 still and whole
        fresh();
        Texture tex = texture(8);
        const Snapshot was(tex);
        fail_image = true;
        require(!bc1_to_bc3(tex, the_cmd, true) && create_calls == 2, "no image: the pool and a plain one were both tried");
        require(count('L') + count('B') + count('E') == 0 && dead.empty(), "no image: nothing recorded but the scratch's layout");
        ops.clear();
        require(Snapshot(tex).image == was.image && was.same(tex), "no image: the texture, the count and the dead list are as they were");
        fresh();
        fail_scratch = true;
        const Snapshot again(tex);
        require(!bc1_to_bc3(tex, the_cmd, false) && create_calls == 0, "no scratch: no image is made");
        require(again.same(tex), "no scratch: the texture, the count and the dead list are as they were");
    }
    {   // the cap: an upload's conversions leave at most 256 MB of BC1 images waiting in a present, the first always let through
        fresh();
        Texture a = texture(8), b = texture(8), c = texture(8), big = texture(8);
        a.bytes = b.bytes = c.bytes = 200ull << 20, big.bytes = 300ull << 20;
        require(bc1_to_bc3(a, the_cmd, true), "the cap: a present's first conversion is done");
        const Snapshot b_was(b);
        require(!bc1_to_bc3(b, the_cmd, true) && b_was.same(b), "the cap: 200 MB waiting, another 200 MB waits for the next present");
        require(bc1_to_bc3(b, the_cmd, false), "the cap: a GPU copy's conversion cannot wait and is done");
        const Snapshot c_was(c);
        require(!bc1_to_bc3(c, the_cmd, true) && c_was.same(c), "the cap: the copy's conversion counts toward it");
        frame_no++;
        require(bc1_to_bc3(c, the_cmd, true), "the cap: the next present starts again");
        frame_no++;
        require(bc1_to_bc3(big, the_cmd, true), "the cap: one image over 256 MB is still converted, alone");
    }
}

void copies() {
    {   // decoded ASTC (RGBA8) into BC3: as before, a copy into the scratch and an encode, 8 layers a run
        fresh();
        Texture src = texture(0, 64, 32, 1, 10), dst = texture(16, 64, 32, 1, 10);
        const Snapshot was(dst);
        require(texture_copy_into_bc(the_cmd, &src, 0, 0, 4, 8, &dst, 0, 0, 12, 16, 16, 8, 10), "RGBA8 into BC3 is done");
        require(ops.size() == 5 && ops[0].kind == 'S' && count('B') == 0, "RGBA8 into BC3: the scratch, then two runs of copy and encode");
        require(ops[1].kind == 'C' && ops[1].image == src.image && ops[1].layer == 0 && ops[1].n == 8 && ops[1].w == 16 &&
                ops[1].h == 8 && ops[1].x == 4 && ops[1].y == 8, "RGBA8 into BC3: the first eight layers copied from the source's region");
        require(ops[2].kind == 'E' && ops[2].image == dst.image && ops[2].layer == 0 && ops[2].n == 8 && ops[2].x == 12 &&
                ops[2].y == 16, "RGBA8 into BC3: encoded at the destination's corner");
        require(ops[3].kind == 'C' && ops[3].layer == 8 && ops[3].n == 2 && ops[4].kind == 'E' && ops[4].layer == 8 && ops[4].n == 2,
                "RGBA8 into BC3: then the last two");
        require(was.image == dst.image && dead.empty(), "RGBA8 into BC3: the destination's image stays");
    }
    {   // BC1 into BC3 (the mixed pair vk_xfer.cpp's copy_t2t sends here): decompressed by a blit, encoded as BC3
        fresh();
        Texture src = texture(8, 64, 32, 3, 4), dst = texture(16, 64, 32, 3, 4);
        const Snapshot src_was(src), dst_was(dst);
        require(texture_copy_into_bc(the_cmd, &src, 1, 1, 0, 0, &dst, 1, 2, 0, 0, 32, 16, 2), "BC1 into BC3 is done");
        require(ops.size() == 3 && ops[0].kind == 'S' && count('C') == 0, "BC1 into BC3: no block-for-block copy");
        require(ops[1].kind == 'B' && ops[1].image == src.image && ops[1].level == 1 && ops[1].layer == 1 && ops[1].n == 2 &&
                ops[1].w == 32 && ops[1].h == 16, "BC1 into BC3: the source's level and layers blitted into the scratch");
        require(ops[2].kind == 'E' && ops[2].image == dst.image && ops[2].format == VK_FORMAT_BC3_UNORM_BLOCK && !ops[2].bc1 &&
                ops[2].level == 1 && ops[2].layer == 2 && ops[2].n == 2, "BC1 into BC3: encoded into the BC3 image");
        ops.clear();
        require(src_was.same(src) && dst_was.same(dst), "BC1 into BC3: neither texture's image changes");
    }
    {   // BC3 into BC1 (the other mixed pair): the destination is made BC3 first, its texels kept, then the copy
        fresh();
        Texture src = texture(16, 64, 32, 3, 2), dst = texture(8, 64, 32, 3, 2);
        const Snapshot was(dst);
        require(texture_copy_into_bc(the_cmd, &src, 0, 0, 0, 0, &dst, 0, 1, 0, 0, 64, 32, 1), "BC3 into BC1 is done");
        check_converted(dst, was, VK_FORMAT_BC3_UNORM_BLOCK, 0);
        const size_t n = ops.size();
        require(n == 2 + 12 + 3 && ops[n - 3].kind == 'S', "BC3 into BC1: after the conversion, the copy's own scratch, blit and encode");
        require(ops[n - 2].kind == 'B' && ops[n - 2].image == src.image && ops[n - 2].n == 1, "BC3 into BC1: the source blitted");
        require(ops[n - 1].kind == 'E' && ops[n - 1].image == dst.image && ops[n - 1].image != was.image &&
                ops[n - 1].format == VK_FORMAT_BC3_UNORM_BLOCK && !ops[n - 1].bc1 && ops[n - 1].layer == 1,
                "BC3 into BC1: encoded as BC3 into the new image, never into the BC1 one");
        for (const Op& o : ops) require(o.kind != 'E' || !o.bc1, "BC3 into BC1: nothing is encoded into a BC1 image");
    }
    {   // decoded ASTC (RGBA8, which may hold alpha) into BC1: the same, with a copy
        fresh();
        Texture src = texture(0, 64, 32, 1, 1), dst = texture(8, 64, 32, 1, 1, false);
        const Snapshot was(dst);
        require(texture_copy_into_bc(the_cmd, &src, 0, 0, 0, 0, &dst, 0, 0, 0, 0, 64, 32, 1), "RGBA8 into BC1 is done");
        check_converted(dst, was, VK_FORMAT_BC3_UNORM_BLOCK, 0);
        const size_t n = ops.size();
        require(n == 4 && ops[1].kind == 'S' && ops[2].kind == 'C' && ops[2].image == src.image && ops[3].kind == 'E' &&
                ops[3].image == dst.image && !ops[3].bc1, "RGBA8 into BC1: made BC3, then copied and encoded as BC3");
    }
    {   // a copy that is left out leaves a BC1 destination BC1: nothing is converted for it
        const auto left_out = [](Texture& src, Texture& dst, int dx, const char* why) {
            const Snapshot src_was(src), dst_was(dst);
            require(!texture_copy_into_bc(the_cmd, &src, 0, 0, 0, 0, &dst, 0, 0, dx, 0, 64, 32, 1), why);
            require(src_was.same(src) && dst_was.same(dst) && ops.empty() && create_calls == 0, why);
        };
        fresh();
        Texture rgba = texture(0), bc1_a = texture(8), bc1_b = texture(8), bc3_a = texture(16), bc3_b = texture(16);
        left_out(bc1_a, bc1_b, 0, "BC1 into BC1 is not this function's: one format is copied block for block (vk_xfer.cpp)");
        left_out(bc3_a, bc3_b, 0, "BC3 into BC3 is not this function's either");
        left_out(rgba, bc1_a, 2, "a copy to a corner that is not a block's is left out, the BC1 destination not converted");
        no_blit_src = true;
        left_out(bc3_a, bc1_a, 0, "where the GPU cannot blit from the source's format the copy is left out, nothing converted");
        left_out(bc1_a, bc3_a, 0, "and so is BC1 into BC3");
        no_blit_src = false;
        bc3_a.bc_resized = true;
        left_out(bc3_a, bc1_a, 0, "a resized BC3 source's texels are not the game's: left out");
        bc3_a.bc_resized = false;
        ready = false;
        left_out(bc3_a, bc1_a, 0, "with no encoder the copy is left out");
        ready = true;
        rgba.astc = false;
        left_out(rgba, bc1_a, 0, "a source that is not decoded ASTC is left out");
        rgba.astc = true;
        fail_image = true;   // (the conversion fails: the copy is left out, never written into the BC1 image)
        const Snapshot was(bc1_a);
        require(!texture_copy_into_bc(the_cmd, &bc3_a, 0, 0, 0, 0, &bc1_a, 0, 0, 0, 0, 64, 32, 1), "no BC3 image: the copy is left out");
        require(count('E') + count('B') + count('C') + count('L') == 0, "no BC3 image: nothing is copied or encoded");
        ops.clear();
        require(was.same(bc1_a), "no BC3 image: the destination is BC1 still, its image and count as they were");
    }
}

/// A texture kept as BC1 as an upload takes it: 8 x 8 texels of ASTC 4 x 4, two levels, every block in the game's memory opaque
Texture upload_texture(uint32_t layers, bool uploaded, uint32_t block = 8) {
    Texture t = texture(block, 8, 8, 2, layers, uploaded);
    t.gpu = 0, t.L.layer_size = kLayer;
    guest.assign(layers * kLayer, 0);
    for (uint32_t layer = 0; layer < layers; layer++) {
        for (uint32_t i = 0; i < 4; i++) put(layer, 0, i, kOpaque);
        put(layer, 1, 0, kOpaque);
    }
    guest_reads = 0, guest_private = false, fail_staging = false, game_writes.armed = false, staging_taken = 0;
    want_cmd = the_upload_cmd;
    return t;
}
/// Whether the n layers staged are all opaque, by the header's rule over the staged bytes themselves
bool staged_opaque(size_t n) {
    require(staging_bytes.size() == n * kLayer, "the upload stages the layers it takes, whole");
    for (size_t k = 0; k < n; k++) {
        const uint8_t* p = staging_bytes.data() + k * kLayer;
        if (!astc_opaque::level(p, 512, 2, 2, 0, 4, 4) || !astc_opaque::level(p + 512, 512, 1, 1, 0, 4, 4)) return false;
    }
    return true;
}
/// What every upload must leave: each level of each staged layer decoded and encoded once, after the flush, into the texture's
/// image as it is now; and into a BC1 image only blocks that are opaque as staged (the guarantee: alpha never goes into BC1)
void check_upload(const Texture& tex, size_t n, bool want_bc1) {
    const bool opaque = staged_opaque(n);
    require(tex.bc1 == want_bc1 && (opaque || !tex.bc1), "the texture stays BC1 only when what was staged is opaque");
    const auto flush = std::find_if(ops.begin(), ops.end(), [](const Op& o) { return o.kind == 'F'; });
    require(flush != ops.end() && count('F') == 1, "the staged blocks are flushed once");
    size_t decodes = 0, encodes = 0;
    for (auto o = flush + 1; o != ops.end(); ++o) {
        if (o->kind == 'S') continue;
        require(o->kind == (decodes == encodes ? 'D' : 'E'), "after the flush: a decode of staged blocks, then its encode, in turn");
        if (o->kind == 'D') decodes++;
        else {
            encodes++;
            require(o->image == tex.image && o->bc1 == tex.bc1 && (opaque || !o->bc1),
                    "staged blocks are encoded into the texture's image as it is now, and never into a BC1 image when they hold alpha");
        }
    }
    require(decodes == tex.levels * n && encodes == decodes, "each level of each staged layer is decoded and encoded once");
    for (auto o = ops.begin(); o != flush; ++o)
        require(o->kind != 'D' && (o->kind != 'E' || !o->bc1), "before the flush only a conversion's encodes, into the BC3 image");
}

void uploads() {
    for (const bool priv : {false, true}) {   // opaque memory: read once a layer, staged as read, encoded into the BC1 image
        fresh();
        Texture tex = upload_texture(2, false);
        guest_private = priv;
        const Snapshot was(tex);
        require(upload_astc_gpu(tex, 2 * kLayer, nullptr), "opaque blocks are uploaded into a BC1 image");
        require(guest_reads == 2, "the game's memory is read once a layer: what is judged is what is staged");
        require(staging_bytes == guest, "the layers are staged as the game's memory has them");
        check_upload(tex, 2, true);
        require(tex.image == was.image && dead.empty() && create_calls == 0, "opaque blocks: the BC1 image stays");
    }
    for (const bool uploaded : {false, true}) {   // alpha in the memory: the image is made again as BC3 before anything is encoded
        fresh();
        Texture tex = upload_texture(2, uploaded);
        put(1, 1, 0, kAlpha);
        const Snapshot was(tex);
        require(upload_astc_gpu(tex, 2 * kLayer, nullptr), "blocks with alpha are uploaded, into a BC3 image");
        require(guest_reads == 2 && staging_bytes == guest, "alpha: the memory is read once a layer and staged as read");
        check_converted(tex, was, VK_FORMAT_BC3_UNORM_BLOCK, 0);
        check_upload(tex, 2, false);
        require(count('E') == (uploaded ? 8u : 4u), "alpha: what the BC1 image held is encoded over first, then what was staged");
    }
    for (const bool priv : {false, true}) {   // the game writes alpha into a layer while it is judged: staged is what was judged
        fresh();
        Texture tex = upload_texture(1, true);
        guest_private = priv;
        game_writes.armed = true, game_writes.layer = 0;
        const std::vector<uint8_t> before = guest;
        const Snapshot was(tex);
        require(upload_astc_gpu(tex, kLayer, nullptr), "the upload during which the game writes is done");
        require(!game_writes.armed && guest != before && guest_reads == 1, "the game wrote alpha into the layer during the upload");
        require(staging_bytes == before, "what is staged is the copy that was judged, not the memory the game wrote after");
        check_upload(tex, 1, true);
        require(tex.image == was.image && dead.empty(), "judged and staged opaque: the BC1 image stays");
        ops.clear();   // the next upload reads the alpha, and makes the image BC3
        const Snapshot then(tex);
        require(upload_astc_gpu(tex, kLayer, nullptr), "the upload after the game's write is done");
        check_converted(tex, then, VK_FORMAT_BC3_UNORM_BLOCK, 0);
        check_upload(tex, 1, false);
    }
    {   // the game writes alpha into a layer not read yet, while another is judged: it is read with the alpha, and judged so
        fresh();
        Texture tex = upload_texture(2, false);
        game_writes.armed = true, game_writes.layer = 1;
        const Snapshot was(tex);
        require(upload_astc_gpu(tex, 2 * kLayer, nullptr), "the upload during which the game writes a later layer is done");
        require(!game_writes.armed && guest_reads == 2 && staging_bytes == guest, "the later layer is staged with the alpha");
        check_converted(tex, was, VK_FORMAT_BC3_UNORM_BLOCK, 0);
        check_upload(tex, 2, false);
    }
    {   // only the layers staged are judged: alpha in a layer the game did not write since is not uploaded, the image stays
        fresh();
        Texture tex = upload_texture(2, true);
        put(0, 0, 2, kAlpha);
        const Ranges dirty{{kLayer + 16, kLayer + 32}};
        require(upload_astc_gpu(tex, 2 * kLayer, &dirty) && guest_reads == 1, "a written layer alone is read and staged");
        check_upload(tex, 1, true);
        require(dead.empty(), "the layer staged is opaque: the BC1 image stays");
    }
    {   // a layer the storage does not hold stages nothing: not opaque, so no BC1 image is encoded from it
        fresh();
        Texture tex = upload_texture(2, false);
        const Snapshot was(tex);
        require(upload_astc_gpu(tex, kLayer, nullptr) && guest_reads == 1, "a storage that ends before a layer: the rest is uploaded");
        check_converted(tex, was, VK_FORMAT_BC3_UNORM_BLOCK, 0);
        check_upload(tex, 2, false);
    }
    {   // the image cannot be made again now: nothing is recorded, the upload says why, the texture is BC1 still for the next try
        fresh();
        Texture tex = upload_texture(2, false);
        put(0, 0, 1, kAlpha);
        fail_image = true;
        const Snapshot was(tex);
        require(!upload_astc_gpu(tex, 2 * kLayer, nullptr) && astc_fail, "alpha and no BC3 image: the upload fails and says why");
        require(ops.empty() && was.same(tex), "alpha and no BC3 image: nothing recorded, the texture as it was");
        require(!staging_taken, "alpha and no BC3 image: no staging is taken for an upload that is not done");
        fail_image = false;
        require(upload_astc_gpu(tex, 2 * kLayer, nullptr) && !astc_fail, "tried again, the upload is done");
        require(staging_taken == 2 * kLayer, "tried again: the layers' staging is taken once");
        check_converted(tex, was, VK_FORMAT_BC3_UNORM_BLOCK, 0);
        check_upload(tex, 2, false);
    }
    {   // the present's conversions are at their cap: every try in it reads and judges, and takes no staging, until the next present
        fresh();
        Texture first = texture(8, 8, 8, 1, 1, false);
        first.bytes = 200ull << 20;
        require(bc1_to_bc3(first, the_cmd, true), "the cap: the present's first conversion is done");
        ops.clear();
        Texture tex = upload_texture(2, true);
        tex.bytes = 200ull << 20;
        put(1, 0, 1, kAlpha);
        const Snapshot was(tex);
        for (int tries = 1; tries <= 3; tries++) {   // (an uploaded texture's upload is tried again in each submission)
            require(!upload_astc_gpu(tex, 2 * kLayer, nullptr) && astc_fail, "the cap: the upload waits and says why");
            require(guest_reads == 2 * tries && ops.empty() && was.same(tex), "the cap: read and judged, nothing recorded or changed");
            require(!staging_taken, "the cap: an upload that waits takes no staging, however often it is tried in the present");
        }
        frame_no++;
        require(upload_astc_gpu(tex, 2 * kLayer, nullptr) && !astc_fail && !tex.bc1, "the cap: the next present, the upload is done");
        require(staging_taken == 2 * kLayer && staging_bytes == guest, "the cap: the staging is taken once, for the upload done");
        check_upload(tex, 2, false);
    }
    {   // a BC3 texture is not judged: its memory staged from where it lies, as before, alpha and all
        fresh();
        Texture tex = upload_texture(2, true, 16);
        put(1, 0, 0, kAlpha);
        const Snapshot was(tex);
        require(upload_astc_gpu(tex, 2 * kLayer, nullptr) && guest_reads == 2, "a BC3 texture's upload is done");
        require(staging_bytes == guest && tex.image == was.image && dead.empty() && create_calls == 0, "a BC3 texture's image stays");
        check_upload(tex, 2, false);
    }
    want_cmd = the_cmd;
}

constexpr uint32_t kHandle = 5;
VkImageView view_of(Texture** out = nullptr) {
    return texture_view_of(kHandle, VK_IMAGE_VIEW_TYPE_2D, ViewUse::Sample, VK_FORMAT_UNDEFINED, out, nullptr);
}
void draws() {   // the handle cache of a recording, over the submissions in which a BC1 texture's memory comes to hold alpha
    fresh();
    Texture tex = upload_texture(2, true);
    bound = &tex, epoch = 1, gets = touches = 0, touch_uploads = false;
    Texture* out = nullptr;
    const VkImageView first = view_of(&out);
    require(first && out == &tex && view_image[first] == tex.image && gets == 1, "a handle resolves to a view of the BC1 image");
    require(view_of() == first && gets == 1 && touches == 0, "bound again in the submission: the view kept for the handle");
    epoch++, touch_uploads = true;   // the next submission: the game wrote its memory, opaque blocks still
    require(view_of() == first && touches == 1 && gets == 1 && tex.bc1, "an opaque upload: the kept view, not resolved again");
    epoch++;                         // the next: the game wrote alpha, and the touch's upload makes the image again as BC3
    put(1, 0, 3, kAlpha);
    const VkImage old = tex.image;
    out = nullptr;
    const VkImageView now = view_of(&out);
    require(touches == 2 && !tex.bc1 && tex.image != old && dead.size() == 1, "the touch's upload made the BC1 image again as BC3");
    require(std::count(dead.back().views.begin(), dead.back().views.end(), first) == 1, "the BC1 image's view is retired with it");
    require(now != first, "the draw that touched it is not given the retired view of the BC1 image");
    require(now && view_image[now] == tex.image && out == &tex && gets == 2, "it is given a view of the new BC3 image");
    require(view_of() == now && touches == 2, "bound again in the submission: the new image's view, touched no more");
    epoch++;                         // the submission after: nothing made again, the kept view of the BC3 image
    const int resolved = gets;
    require(view_of() == now && touches == 3 && gets == resolved && dead.size() == 1, "the next submission: the new view, kept");
    bound = nullptr, touch_uploads = false, want_cmd = the_cmd;
    handle_hits().clear();
}

} // namespace fixture

int main() {
    fixture::remake();
    fixture::copies();
    fixture::uploads();
    fixture::draws();
    std::printf("astc bc1 remake: all checks passed\n");
    return 0;
}
