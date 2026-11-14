// Authored game-free call recorder. No Vulkan library/device, host mapping, game data or GUI is opened.
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <new>
#include <set>
#include <string>
#include <thread>
#include <utility>
#include <vector>
#include "gpu/vk/darwin_import.h"
#include "gpu/vk/import_hold.h"
#include "gpu/vk/retired_give_back.h"

namespace mkwvk {
static unsigned checks;
static void check(bool ok, const char* what) {
    ++checks;
    if (!ok) { std::fprintf(stderr, "FAIL: %s\n", what); std::exit(1); }
}
template<class T> T handle(uintptr_t n) { return reinterpret_cast<T>(n); }
constexpr uint64_t base = 0x1000000000ull, window = 1ull << 39, pointer_base = 0x100000000000ull, chunk_bytes = 65536;
using VmaAllocation = void*;
using SIZE_T = size_t;
constexpr unsigned MEM_COMMIT = 0x1000, MEM_RESERVE = 0x2000, PAGE_READWRITE = 4, PAGE_READONLY = 2, PAGE_NOACCESS = 1, PAGE_GUARD = 256;
struct MEMORY_BASIC_INFORMATION { void* BaseAddress{}; size_t RegionSize{}; unsigned State{}, Protect{}; };
struct SYSTEM_INFO { unsigned dwPageSize{}; };
struct { VkDevice device{}; VkPhysicalDevice physical{}; VkPhysicalDeviceMemoryProperties memory{}; VkPhysicalDeviceProperties props{};
         bool external_memory_host = true; } dev;
static unsigned host_page = 16384, driver_alignment = 16384;
static bool pin_failure, query_failure, commit_failure, reserved, watched, zero_address, requirements_oversize, wrong_types, wait_failure;
static VkResult create_failure = VK_SUCCESS, allocation_failure = VK_SUCCESS, bind_failure = VK_SUCCESS, properties_failure = VK_SUCCESS;
static uint32_t host_generation = 1;
static std::vector<std::array<uint64_t, 2>> host_pools;
static std::map<std::pair<uint64_t, uint64_t>, int> pins;
static std::vector<std::string> events;
static unsigned commits, allocations, frees, buffer_destroys, pin_calls, unpin_calls, overlapping_payloads, peak_payloads, waits, finishes;
static uint64_t next_handle = 1;
struct Memory { uint64_t lo{}, hi{}; std::vector<uint8_t> bytes; };
struct Buffer { uint64_t size{}, offset{}; VkDeviceMemory memory{}; };
static std::map<VkDeviceMemory, Memory> memories;
static std::map<VkBuffer, Buffer> buffers;
static std::map<uint64_t, uint8_t> host_bytes;
static std::vector<std::function<void()>> commands;
static std::map<VkBuffer, unsigned> queued_refs;
static uint8_t* guest_base() { return reinterpret_cast<uint8_t*>(pointer_base); }
static uint64_t guest_size() { return window; }
static uint32_t maps_generation() { return host_generation; }
static int gpu_pools(uint64_t (*out)[2], int max) {
    const int n = std::min(max, (int)host_pools.size());
    for (int i = 0; i < n; ++i) out[i][0] = host_pools[i][0], out[i][1] = host_pools[i][1];
    return n;
}
static uint8_t* gpu_ptr(uint64_t gpu, uint64_t* avail) { *avail = gpu < window ? window - gpu : 0; return gpu < window ? guest_base() + gpu : nullptr; }
static uint64_t gpu_guest(uint64_t gpu) { return gpu; }
static int pin(uint64_t lo, uint64_t hi) {
    events.push_back("pin"); ++pin_calls;
    if (pin_failure || lo >= hi || hi > window || lo % host_page || hi % host_page) return 0;
    ++pins[{lo, hi}]; return 1;
}
static bool pinned(uint64_t lo, uint64_t hi);
static void unpin(uint64_t lo, uint64_t hi) {
    events.push_back("unpin"); ++unpin_calls;
    auto it = pins.find({lo, hi}); check(it != pins.end() && it->second > 0, "unmatched unpin");
    if (!--it->second) pins.erase(it);
    for (const auto& [id, m] : memories) { (void)id; check(pinned(m.lo, m.hi), "unpin before final Vulkan memory destruction"); }
}
struct Host {
    uint8_t* (*guest_base)(); uint64_t (*guest_size)(); uint8_t* (*gpu_ptr)(uint64_t, uint64_t*); uint64_t (*gpu_guest)(uint64_t);
    uint32_t (*maps_generation)(); int (*gpu_pools)(uint64_t (*)[2], int); int (*guest_import_pin)(uint64_t, uint64_t);
    void (*guest_import_unpin)(uint64_t, uint64_t);
} host_value{guest_base, guest_size, gpu_ptr, gpu_guest, maps_generation, gpu_pools, pin, unpin}, *host = &host_value;
static bool pinned(uint64_t lo, uint64_t hi) {
    for (const auto& [r, n] : pins) if (n && r.first <= lo && r.second >= hi) return true;
    return false;
}
static void require_pin(uint64_t lo, uint64_t hi) {
#if HAVE_DARWIN_IMPORTS
    check(pinned(lo, hi), "driver/import preparation without full backing pin");
#else
    (void)lo; (void)hi;
#endif
}
static size_t VirtualQuery(const void* p, MEMORY_BASIC_INFORMATION* mi, size_t size) {
    events.push_back("query"); if (query_failure) return 0;
    const uint64_t lo = (uintptr_t)p - pointer_base;
    require_pin(lo, lo + 1);
    *mi = {const_cast<void*>(p), host_page, reserved ? MEM_RESERVE : MEM_COMMIT, watched ? PAGE_READONLY : PAGE_READWRITE};
    return size;
}
static void* VirtualAlloc(void* p, size_t size, unsigned, unsigned) {
    events.push_back("commit"); ++commits;
    const uint64_t lo = (uintptr_t)p - pointer_base; require_pin(lo, lo + size);
    return commit_failure ? nullptr : p;
}
static void GetSystemInfo(SYSTEM_INFO* out) { out->dwPageSize = host_page; }
static void vkGetPhysicalDeviceProperties2(VkPhysicalDevice, VkPhysicalDeviceProperties2* p) {
    for (auto* b = (VkBaseOutStructure*)p->pNext; b; b = b->pNext) {
        if (b->sType == VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_MEMORY_HOST_PROPERTIES_EXT)
            ((VkPhysicalDeviceExternalMemoryHostPropertiesEXT*)b)->minImportedHostPointerAlignment = driver_alignment;
        if (b->sType == VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_PROPERTIES)
            ((VkPhysicalDeviceVulkan11Properties*)b)->maxMemoryAllocationSize = 1ull << 30;
        if (b->sType == VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_PROPERTIES)
            ((VkPhysicalDeviceVulkan13Properties*)b)->maxBufferSize = 1ull << 30;
    }
}
static VkResult vkGetMemoryHostPointerPropertiesEXT(VkDevice, VkExternalMemoryHandleTypeFlagBits, const void* p, VkMemoryHostPointerPropertiesEXT* out) {
    events.push_back("properties"); require_pin((uintptr_t)p - pointer_base, (uintptr_t)p - pointer_base + 1);
    out->memoryTypeBits = 3; return properties_failure;
}
static VkResult vkCreateBuffer(VkDevice, const VkBufferCreateInfo* ci, const VkAllocationCallbacks*, VkBuffer* out) {
    events.push_back("buffer"); *out = handle<VkBuffer>(0xdead);
    if (create_failure != VK_SUCCESS) return create_failure;
    check(ci->size > 0 && ci->usage == 0x201ff, "real LS import buffer usage or size changed");
    check(ci->pNext && ((const VkExternalMemoryBufferCreateInfo*)ci->pNext)->handleTypes == VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT,
          "missing host payload buffer declaration");
    *out = handle<VkBuffer>(++next_handle); buffers.emplace(*out, Buffer{ci->size}); return VK_SUCCESS;
}
static void vkGetBufferMemoryRequirements(VkDevice, VkBuffer b, VkMemoryRequirements* r) {
    events.push_back("requirements"); check(buffers.count(b) == 1, "requirements of an unowned buffer");
    r->size = buffers[b].size + (requirements_oversize ? chunk_bytes * 4 : 0); r->alignment = 256;
    r->memoryTypeBits = wrong_types ? 1 : 3;
}
static VkResult vkAllocateMemory(VkDevice, const VkMemoryAllocateInfo* ai, const VkAllocationCallbacks*, VkDeviceMemory* out) {
    events.push_back("allocate"); *out = handle<VkDeviceMemory>(0xbad);
    const auto* im = (const VkImportMemoryHostPointerInfoEXT*)ai->pNext;
    check(im && im->sType == VK_STRUCTURE_TYPE_IMPORT_MEMORY_HOST_POINTER_INFO_EXT, "wrong import chain");
    check(im->handleType == VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT, "wrong host payload type");
    const auto* flags = (const VkMemoryAllocateFlagsInfo*)im->pNext;
    check(flags && flags->flags == VK_MEMORY_ALLOCATE_DEVICE_ADDRESS_BIT, "missing BDA allocation flag");
    const uint64_t lo = (uintptr_t)im->pHostPointer - pointer_base, hi = lo + ai->allocationSize;
    check(ai->memoryTypeIndex == 1, "buffer/host coherent memory type intersection");
    require_pin(lo, hi);
    if (allocation_failure != VK_SUCCESS) return allocation_failure;
    for (const auto& [id, m] : memories) { (void)id; if (lo < m.hi && m.lo < hi) ++overlapping_payloads; }
    *out = handle<VkDeviceMemory>(++next_handle);
    Memory m{lo, hi, std::vector<uint8_t>((size_t)(hi - lo))};
    for (const auto& [at, value] : host_bytes) if (at >= lo && at < hi) m.bytes[(size_t)(at - lo)] = value;
    memories.emplace(*out, std::move(m)); ++allocations; peak_payloads = std::max(peak_payloads, (unsigned)memories.size());
    return VK_SUCCESS;
}
static VkResult vkBindBufferMemory(VkDevice, VkBuffer b, VkDeviceMemory m, VkDeviceSize offset) {
    events.push_back("bind"); check(buffers.count(b) && memories.count(m), "bind of unowned handles");
    if (bind_failure != VK_SUCCESS) return bind_failure;
    check(offset + buffers[b].size <= memories[m].bytes.size(), "view exceeds full pinned backing");
    buffers[b].memory = m, buffers[b].offset = offset; return VK_SUCCESS;
}
static VkDeviceAddress vkGetBufferDeviceAddress(VkDevice, const VkBufferDeviceAddressInfo* info) {
    events.push_back("address"); check(buffers.count(info->buffer), "BDA of unowned buffer");
    const Buffer& b = buffers.at(info->buffer); check(memories.count(b.memory), "BDA before binding");
    return zero_address ? 0 : (uint64_t)(uintptr_t)b.memory * (1ull << 32) + b.offset;
}
static void release_texel_buffer_views(VkBuffer b) { check(buffers.count(b), "release views of unowned buffer"); events.push_back("views"); }
static void vkDestroyBuffer(VkDevice, VkBuffer b, const VkAllocationCallbacks*) {
    check(queued_refs[b] == 0, "destroyed a buffer still referenced by recorded GPU work");
    check(buffers.erase(b) == 1, "destroyed invalid output handle or buffer twice");
    events.push_back("destroy-buffer"); ++buffer_destroys;
}
static void vkFreeMemory(VkDevice, VkDeviceMemory id, const VkAllocationCallbacks*) {
    for (const auto& [b, v] : buffers) { (void)b; check(v.memory != id, "freed a payload before its last VkBuffer view"); }
    check(memories.count(id), "freed invalid allocation output or memory twice");
    const Memory& m = memories.at(id);
    for (size_t i = 0; i < m.bytes.size(); ++i) if (m.bytes[i]) host_bytes[m.lo + i] = m.bytes[i];
    memories.erase(id); events.push_back("free-memory"); ++frees;
}
static void destroy_buffer(VkBuffer, VmaAllocation) {}
static void logf(const char*, ...) {}
constexpr int log_debug = 0;
static void logf_at(int, const char*, ...) {}
static const char* getenv(const char*) { return nullptr; }
static bool env_on(const char*) { return false; }
static int env_int(const char*, int value) { return value; }
static bool system_heap_usage(uint64_t*, uint64_t*) { return false; }
static int64_t import_now_ms() { return 1000; }
static bool process_exit_probe;
struct TeardownGuard {
    ~TeardownGuard() {
        if (!process_exit_probe) return;
        if (frees || buffer_destroys || unpin_calls || memories.size() != 1 || pins.size() != 1) {
            std::fprintf(stderr, "FAIL: static teardown called Vulkan or dropped an active import pin\n");
            std::_Exit(1);
        }
        std::puts("PASS: published import remains process-owned; no Vulkan destruction/unpin during static teardown");
    }
} teardown_guard;
#include "import_types.inc"

static uint64_t mem_rec_value = 7, mem_completed = 0, align = 16384, max_import_size = 1ull << 30, mirror_epoch = 10;
static std::vector<GuestPool> guest_pools;
static std::atomic<uint64_t> guest_pools_gen{1}, pools_gen{1};
#if !HAVE_DARWIN_IMPORTS
static std::map<uint64_t, Pool> imported;
#endif
static Pool* last_pool;
static Pool* pool_cache[4096]{};
static std::mutex pools_mu, import_holds_mu;
static uint32_t generation = ~0u;
static ImportHolds import_holds;
static thread_local bool import_cooling;
static thread_local VkResult import_result = VK_SUCCESS;
static int failures;
static std::atomic<int64_t> memsync_lock_wait_ns{0}, memsync_part_ns[3]{};
static std::mutex bg_mu;
static std::condition_variable bg_done_cv;
static std::vector<uint64_t> bg_inflight;
#if !HAVE_DARWIN_IMPORTS
static std::vector<std::pair<uint64_t, Pool>> bg_ready;
#endif
static int64_t st_bg_waits, st_prefetch_dropped, st_prefetch_used, st_prefetch_sent;
#if !HAVE_DARWIN_IMPORTS
static std::deque<std::map<uint64_t, Pool>::node_type> retired_pools, evicted_pools;
#endif
static std::vector<Pool*> mirror_wanted;
#if !HAVE_DARWIN_IMPORTS
static std::deque<DeadImport> dead_imports;
#endif
static uint64_t st_retired_imports_freed, st_retired_import_bytes_freed, dead_import_bytes;
struct DeadMirror { VkBuffer buffer{}; VmaAllocation alloc{}; uint64_t value{}; };
static std::deque<DeadMirror> dead_mirrors;
static void shadow_free(Pool&) { events.push_back("shadow-retire"); }
static void note_in_place_read(bool = false) {}
static uint64_t import_chunk() { return chunk_bytes; }
static uint64_t import_span() { return 0; }   // The existing RX_VK_IMPORT_SPAN_MB=0 route, to exercise logical growth deterministically.
static void prefetch_chunks(uint64_t, uint64_t, uint64_t) {}
static Pool* pool_lookup(uint64_t, uint64_t);
static void retire_pool(std::map<uint64_t, Pool>::iterator);
struct TableRun { uint64_t first, count, address; };
static std::vector<uint64_t> page_storage((8ull << 30 >> 12) + (4ull << 30 >> 12));
static uint64_t* guest_pages = page_storage.data();
static bool table_defer;
static std::vector<TableRun> table_pending;
static void execute_commands() { for (auto& c : commands) c(); commands.clear(); queued_refs.clear(); }
#include "import_actual.inc"

static uint8_t& byte(VkBuffer b, uint64_t offset) {
    check(buffers.count(b), "recorded reference lost its buffer");
    const Buffer& v = buffers.at(b); check(offset < v.size, "command outside a buffer view");
    return memories.at(v.memory).bytes.at((size_t)(v.offset + offset));
}
static void fill_later(VkBuffer b, uint64_t offset, uint8_t value) {
    ++queued_refs[b]; commands.push_back([=] { byte(b, offset) = value; --queued_refs[b]; });
}
static void read_later(VkBuffer b, uint64_t offset, uint8_t expected) {
    ++queued_refs[b]; commands.push_back([=] { check(byte(b, offset) == expected, "same-submit view read did not share the payload"); --queued_refs[b]; });
}
static void release(Pool& p) {
#if HAVE_DARWIN_IMPORTS
    destroy_import(p);
#else
    if (p.buffer) destroy_import_buffer(p.buffer);
    if (p.memory) vkFreeMemory(dev.device, p.memory, nullptr);
    p.buffer = VK_NULL_HANDLE, p.memory = VK_NULL_HANDLE;
#endif
}
static void clean() {
    check(commands.empty(), "cleanup before GPU command completion");
    for (auto& [lo, p] : imported) { (void)lo; release(p); }
    for (auto& n : retired_pools) release(n.mapped());
    for (auto& n : evicted_pools) release(n.mapped());
    for (auto& [lo, p] : bg_ready) { (void)lo; release(p); }
    mem_completed = UINT64_MAX; reap_mirrors();
    imported.clear(); retired_pools.clear(); evicted_pools.clear(); bg_ready.clear();
    last_pool = nullptr; std::fill(std::begin(pool_cache), std::end(pool_cache), nullptr);
    check(memories.empty() && buffers.empty() && pins.empty(), "resource or host-pin leak");
}
static void reset() {
    clean(); mem_rec_value = 7; mem_completed = 0; generation = ~0u; ++host_generation;
    host_pools = {{base, 2 * chunk_bytes}}; guest_pools.clear();
    import_holds.rows.clear(); events.clear(); table_pending.clear(); table_defer = false;
    std::fill(page_storage.begin(), page_storage.end(), 0);
    query_failure = pin_failure = commit_failure = reserved = watched = zero_address = requirements_oversize = wrong_types = wait_failure = false;
    create_failure = allocation_failure = bind_failure = properties_failure = VK_SUCCESS;
    commits = allocations = frees = buffer_destroys = pin_calls = unpin_calls = overlapping_payloads = peak_payloads = waits = finishes = 0;
    host->guest_import_pin = pin; host->guest_import_unpin = unpin;
    check(memory_init(), "memory_init refused valid fixture capabilities");
}
static void admit() { memory_sync(); }

#if HAVE_DARWIN_IMPORTS
static void raw_lifetime_topology() {
    reset(); admit();
    Pool* first = pool_of(base + 4096, 4096); check(first != nullptr, "first real pool lookup");
    const VkBuffer a = first->buffer; const VkDeviceMemory owner = first->memory;
    first->last_value = mem_rec_value;
    fill_later(a, chunk_bytes - 4096, 0xa7);
    Pool* second = pool_of(base + chunk_bytes - 4096, 8192);
    check(second && second != first && retired_pools.size() == 1, "actual lookup did not grow and retire its old logical pool");
    check(second->memory != owner && first->memory == owner, "baseline import topology changed without alias qualification");
    check(allocations == 2 && pin_calls == 2 && overlapping_payloads == 1 && waits == 0, "raw import topology or independent pins differ");
    execute_commands();
    check(memory_guest_imported(base + 2 * chunk_bytes - 1, base + 2 * chunk_bytes) == 1, "full active backing missing from imported query");
    check(!memory_guest_imported(base, base), "empty imported query");
    mem_completed = 6; retired_imports_give_back(); reap_mirrors();
    check(buffers.count(a) && unpin_calls == 0, "retired buffer/pin freed before GPU completion");
    mem_completed = 7; mem_rec_value = 8; retired_imports_give_back(); reap_mirrors();
    check(buffers.count(a), "dead-import gate ignored the later recording value");
    mem_completed = 8; reap_mirrors();
    check(!buffers.count(a) && !memories.count(owner) && memories.count(second->memory) && unpin_calls == 1,
          "retired owner not destroyed exactly after completion");
    clean(); check(frees == 2 && unpin_calls == 2, "raw owner pin balance");
    std::puts("SOURCE TOPOLOGY: active and retired payloads overlap; pin correctness is NOT alias-visibility qualification");
}
static void shared_owner_consumer() {
    reset();
    auto owner = darwin_pin_backing({base, base + 2 * chunk_bytes}); check(owner != nullptr, "shared owner pin");
    Pool a, b;
    check(darwin_import_view(base, base + chunk_bytes, a, owner), "first explicit shared-owner view");
    check(darwin_import_view(base + 16384, base + 81920, b, owner), "overlapping explicit shared-owner view");
    check(a.memory == b.memory && allocations == 1 && pin_calls == 1, "shared ownership made another driver payload");
    check(b.address == a.address + 16384, "fixture BDA offset arithmetic");
    fill_later(a.buffer, 16384, 0x6b); read_later(b.buffer, 0, 0x6b); execute_commands();
    destroy_import(a); check(frees == 0 && unpin_calls == 0, "first view destroyed shared backing");
    destroy_import(b); check(frees == 0 && unpin_calls == 0, "final view ignored the explicit retained memory owner");
    owner.reset(); clean(); check(frees == 1 && unpin_calls == 1, "shared-owner final destruction");

    reset(); Pool old;
    check(import_pool(base, base + chunk_bytes, old), "growth old owner");
    fill_later(old.buffer, 4096, 0x4e); execute_commands();
    auto replacement = darwin_pin_backing({base, base + 2 * chunk_bytes}); check(replacement != nullptr, "growth pin before releasing old owner");
    destroy_import(old); check(memories.empty() && pins.size() == 1, "old owner not finally destroyed before reimport");
    Pool next; check(darwin_import_view(base, base + 2 * chunk_bytes, next, replacement), "replacement allocation");
    check(byte(next.buffer, 4096) == 0x4e, "fixture replacement lost completed writer state");
    destroy_import(next); replacement.reset(); clean();
    std::puts("PASS: explicit shared-owner and destroy-before-reimport consumers; driver alias visibility remains untested");
}
static void retained_sources() {
    reset(); admit();
    Pool live, retired, eviction, queued, background;
    check(import_pool(base, base + chunk_bytes, live), "direct live import");
    check(import_pool(base + 16384, base + 32768, retired), "retired view");
    check(import_pool(base + 32768, base + 49152, eviction), "evicted view");
    check(import_pool(base + 49152, base + 65536, queued), "queued-dead view");
    check(import_pool(base + 16384, base + 32768, background), "background duplicate view");
    retired.last_value = retired.retired_value = eviction.retired_value = 7;
    imported.emplace(live.lo, std::move(live));
    std::map<uint64_t, Pool> temporary;
    temporary.emplace(retired.lo, std::move(retired)); retired_pools.push_back(temporary.extract(temporary.begin()));
    temporary.emplace(eviction.lo, std::move(eviction)); evicted_pools.push_back(temporary.extract(temporary.begin()));
    dead_imports.push_back({queued.buffer, queued.memory, 7, queued.hi - queued.lo, {}, std::move(queued.import_view)});
    dead_import_bytes += queued.hi - queued.lo;
    queued.buffer = VK_NULL_HANDLE; queued.memory = VK_NULL_HANDLE;
    bg_ready.push_back({background.lo, std::move(background)});
    bg_drain(); check(bg_ready.empty() && frees == 1 && unpin_calls == 1, "redundant background owner not unwound");
    check(memory_guest_imported(base + 49152, base + 65536), "queued-dead backing missing from import query");
    mem_completed = 6; retired_imports_give_back(); reap_mirrors(); imports_give_back();
    check(frees == 1 && memories.size() == 4, "retired/evicted/dead owner freed before completion");
    mem_completed = 7; retired_imports_give_back(); reap_mirrors(); imports_give_back();
    check(frees == 4 && memories.size() == 1 && dead_imports.empty() && evicted_pools.empty(), "retirement owners not released at completion");
    clean(); check(pin_calls == 5 && pin_calls == unpin_calls, "all owner sources and duplicate ranges balance");
}
static void failures_unwind() {
    for (int mode = 0; mode < 10; ++mode) {
        reset(); admit();
        pin_failure = mode == 0; query_failure = mode == 1; reserved = mode == 2; commit_failure = mode == 2;
        create_failure = mode == 3 ? VK_ERROR_OUT_OF_HOST_MEMORY : VK_SUCCESS;
        properties_failure = mode == 4 ? VK_ERROR_INVALID_EXTERNAL_HANDLE : VK_SUCCESS;
        wrong_types = mode == 5; allocation_failure = mode == 6 ? VK_ERROR_OUT_OF_DEVICE_MEMORY : VK_SUCCESS;
        bind_failure = mode == 7 ? VK_ERROR_OUT_OF_HOST_MEMORY : VK_SUCCESS; zero_address = mode == 8; requirements_oversize = mode == 9;
        if (mode == 9) max_import_size = chunk_bytes;   // Required padding itself exceeds the allocation cap, not an unrelated domain.
        Pool p; check(!import_pool(base, base + chunk_bytes, p), "injected import error reported success");
        check(!p.buffer && !p.memory && !p.import_view.buffer && !p.import_view.backing && memories.empty() && buffers.empty() && pins.empty(),
              "failed import leaked owner/pin/invalid handle");
        check(unpin_calls == (pin_failure ? 0u : pin_calls), "failed pin was unpinned or successful pin leaked");
    }
    reset(); watched = true; admit(); Pool p;
    check(import_pool(base, base + chunk_bytes, p) && commits == 0, "existing read-only watch was recommitted"); release(p); clean();
    reset(); requirements_oversize = true; Pool padded;
    check(import_pool(base, base + chunk_bytes, padded), "required allocation padding not imported");
    check(padded.import_view.backing->hi == base + 5 * chunk_bytes && pin_calls == 2 && unpin_calls == 1,
          "full padded backing was not pinned before replacing the preparatory pin");
    imported.emplace(padded.lo, std::move(padded));
    check(memory_guest_imported(base + 4 * chunk_bytes, base + 5 * chunk_bytes), "query misses pinned allocation padding");
    clean(); check(pin_calls == unpin_calls, "padded import pin balance");
    reset(); host->guest_import_pin = nullptr; check(!memory_init(), "missing pin callback accepted");
    host->guest_import_pin = pin; host->guest_import_unpin = nullptr; check(!memory_init(), "missing unpin callback accepted");
    host->guest_import_unpin = unpin;
    reset();
    try {
        Pool unpublished;
        check(import_pool(base, base + chunk_bytes, unpublished), "unpublished owner before exception");
        throw std::bad_alloc();   // Models a caller's failed map/vector publication after successful driver import.
    } catch (const std::bad_alloc&) {}
    check(frees == 1 && buffer_destroys == 1 && unpin_calls == 1, "unpublished exception did not destroy view before memory/pin");
    clean();
    reset();
    auto one = darwin_pin_backing({base, base + chunk_bytes});
    auto two = darwin_pin_backing({base, base + chunk_bytes});
    check(one && two && pins[{base, base + chunk_bytes}] == 2, "duplicate successful pins were not counted");
    one.reset(); check(pins[{base, base + chunk_bytes}] == 1, "first duplicate release removed the other pin");
    two.reset(); check(pins.empty() && pin_calls == unpin_calls, "duplicate pin balance");
    check(!darwin_pin_backing({base + 4096, base + 8192}) && !darwin_pin_backing({window - 16384, window + 16384}),
          "unaligned or out-of-window backing accepted");
    reset(); host_page = driver_alignment = 4096; check(memory_init(), "4 KiB host route"); admit();
    Pool subpage; check(import_pool(base + 4096, base + 8192, subpage), "4 KiB view inside shared backing");
    release(subpage); clean(); host_page = driver_alignment = 16384;
}

static void valid_small_views() {
    reset(); host_pools = {{base, 32 * chunk_bytes}}; admit();
    max_import_size = chunk_bytes + chunk_bytes / 2;
    check(pool_of(base + 4096, 4096) && pool_of(base + 16 * chunk_bytes + 4096, 4096),
          "large registered pool rejected previously valid small imported spans");
    check(allocations == 2 && memories.size() == 2, "small bindings proactively imported the whole registered pool");
    clean();
    reset(); host_pools.clear(); ++host_generation; admit();
    check(!gpu_buffer(base, 4096) && allocations == 0 && pin_calls == 0, "pool-less CPU address fabricated a Vulkan import");
    clean();
}
#endif

static void run(bool leave_process_owned) {
    (void)leave_process_owned;
    dev.memory.memoryTypeCount = 2;
    dev.memory.memoryTypes[0].propertyFlags = VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT;
    dev.memory.memoryTypes[1].propertyFlags = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
#if HAVE_DARWIN_IMPORTS
    if (leave_process_owned) {
        reset(); admit();
        check(pool_of(base, 4096) != nullptr, "process-owned teardown import");
        process_exit_probe = true;
        return;   // The late global guard runs after the actual extracted storage declarations' teardown.
    }
    raw_lifetime_topology(); shared_owner_consumer(); retained_sources(); failures_unwind(); valid_small_views(); clean();
    std::printf("PASS: %u import-owner checks; full-range pins, actual retirement/error paths, explicit shared-owner consumer and no new whole-pool cap; alias GPU qualification pending\n", checks);
#else
    reset(); admit();
    Pool* a = pool_of(base + 4096, 4096); check(a, "baseline first pool lookup");
    const VkDeviceMemory first = a->memory; a->last_value = mem_rec_value;
    Pool* b = pool_of(base + chunk_bytes - 4096, 8192);
    check(b && b != a && b->memory != first && retired_pools.size() == 1, "baseline source did not expose distinct active and retired imports");
    check(overlapping_payloads == 1 && peak_payloads == 2 && memories.size() == 2 && waits == 0,
          "baseline active/retired same-recording overlap topology differs");
    std::printf("OBSERVED SOURCE FIXTURE: active=1 retired=1 live_host_payloads=2 overlapping_allocations=1 recording=7 completed=0 waits=0; NOT an observed LS frame defect\n");
    clean();
#endif
}
} // namespace mkwvk
int main(int argc, char**) { mkwvk::run(argc > 1); }
