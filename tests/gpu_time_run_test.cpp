// The replay's GPU-time collection (vk_replay.cpp gpu_time_collect, compiled from the real source by test_gpu_time_run.py)
// against a fake query pool: every timed recording counted once, none read before its queries are available, and a run of
// finished slots read in one vkGetQueryPoolResults. No Vulkan, GPU or game data.
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <string>

typedef int VkResult;
typedef uint32_t VkFlags;
typedef struct VkDevice_T* VkDevice;
typedef struct VkQueryPool_T* VkQueryPool;
#define VK_NULL_HANDLE nullptr
constexpr VkResult VK_SUCCESS = 0, VK_NOT_READY = 1;
constexpr VkFlags VK_QUERY_RESULT_64_BIT = 1, VK_QUERY_RESULT_WITH_AVAILABILITY_BIT = 4;

constexpr int kRecordings = 48;
struct Recording {
    int slot = 0;
};
struct {
    VkDevice device = nullptr;
    struct {
        struct {
            float timestampPeriod = 1.0f;
        } limits;
    } props;
} dev;

// the fake pool: two queries a slot, each with a value and whether the GPU has written it
static uint64_t values[2 * kRecordings];
static bool available[2 * kRecordings];
static int calls;
static VkResult vkGetQueryPoolResults(VkDevice, VkQueryPool, uint32_t first, uint32_t count, size_t size, void* data,
                                      uint64_t stride, VkFlags flags) {
    calls++;
    if (first + count > 2 * kRecordings) throw std::runtime_error("query range past the pool");
    const size_t words = (flags & VK_QUERY_RESULT_WITH_AVAILABILITY_BIT) ? 2 : 1;
    if (!(flags & VK_QUERY_RESULT_64_BIT) || stride < words * 8 || size < (count - 1) * stride + words * 8)
        throw std::runtime_error("result layout");
    VkResult res = VK_SUCCESS;
    for (uint32_t q = 0; q < count; q++) {
        uint64_t* o = (uint64_t*)((char*)data + q * stride);
        if (available[first + q]) o[0] = values[first + q];
        else res = VK_NOT_READY;
        if (words == 2) o[1] = available[first + q];
    }
    return res;
}
static void vkResetQueryPool(VkDevice, VkQueryPool, uint32_t first, uint32_t count) {
    if (first + count > 2 * kRecordings) throw std::runtime_error("reset past the pool");
    for (uint32_t q = first; q < first + count; q++) available[q] = false;
}
static bool each_switch;
static bool env_on(const char*) { return each_switch; }

static VkQueryPool ts_pool = (VkQueryPool)1;
static bool ts_pending[kRecordings];
static int64_t st_gpu_ns, st_gpu_max_ns;
#include "gpu_time_actual.inc"

static void require(bool c, const char* what) {
    if (!c) throw std::runtime_error(what);
}
static void reset() {
    std::memset(values, 0, sizeof values), std::memset(available, 0, sizeof available), std::memset(ts_pending, 0, sizeof ts_pending);
    calls = 0, st_gpu_ns = 0, st_gpu_max_ns = 0;
}
// slot i timed for ns nanoseconds, its queries written (done) or not yet
static void timed(int i, uint64_t ns, bool done) {
    values[2 * i] = 1000 + 7 * i, values[2 * i + 1] = 1000 + 7 * i + ns;
    available[2 * i] = available[2 * i + 1] = done;
    ts_pending[i] = true;
}
// the slot comes round again (begin_recording: after its own wait, so its own queries are available)
static void reuse(int i) {
    Recording r;
    r.slot = i;
    gpu_time_collect(r);
}

static void run(const std::string& c) {
    reset();
    if (c == "run") {   // 20 finished recordings, reused in ring order: all counted once, in fewer calls than one a slot
        uint64_t want = 0;
        for (int i = 0; i < 20; i++) timed(i, 100 + i, true), want += 100 + i;
        for (int i = 0; i < 20; i++) reuse(i);
        require(st_gpu_ns == (int64_t)want, "sum of the recordings' times");
        require(st_gpu_max_ns == 119, "longest recording");
        require(calls <= 2, "a run of finished slots read in one call");
    } else if (c == "behind") {   // the GPU behind on slots 1-3: they wait for their turn, slot 4 is read with slot 0
        timed(0, 10, true), timed(1, 20, false), timed(2, 30, false), timed(3, 40, false), timed(4, 50, true);
        reuse(0);
        require(st_gpu_ns == 60, "slots 0 and 4 read, 1-3 not yet");
        require(ts_pending[1] && ts_pending[2] && ts_pending[3] && !ts_pending[4], "unfinished slots stay pending");
        for (int i = 1; i <= 3; i++) available[2 * i] = available[2 * i + 1] = true;
        for (int i = 1; i <= 4; i++) reuse(i);
        require(st_gpu_ns == 150, "every recording counted once");
        for (int i = 0; i < 5; i++) require(!ts_pending[i], "all read");
    } else if (c == "own_unavailable") {   // the slot's own queries not written (a failed submit): dropped, as before
        timed(5, 10, false);
        reuse(5);
        require(st_gpu_ns == 0 && !ts_pending[5], "dropped, not pending");
    } else if (c == "half_written") {   // only the start written: not read
        timed(0, 10, true), timed(1, 20, true);
        available[3] = false;
        reuse(0);
        require(st_gpu_ns == 10 && ts_pending[1], "a slot with its end unwritten is not read");
    } else if (c == "ring_end") {   // the run stops at the pool's end: no query past it
        timed(46, 1, true), timed(47, 2, true), timed(0, 4, true);
        reuse(46);
        require(st_gpu_ns == 3 && ts_pending[0], "slots 46 and 47 read, slot 0 in its turn");
    } else if (c == "not_pending") {   // a slot never timed (a parallel segment's) is not read
        reuse(3);
        require(calls == 0 && st_gpu_ns == 0, "nothing read");
    } else if (c == "stale") {   // a slot used again, its queries' reset (in its new command buffer) not run yet: its old times
        timed(0, 10, true), timed(1, 20, true);   // still show as available, and must not be counted again
        reuse(0);   // (slot 1's 20 read with slot 0's)
        reuse(1);   // (slot 1 comes round: done)
        timed(0, 30, true);
        ts_pending[1] = true;   // (slot 1 recorded and submitted again; the GPU has not reached it)
        reuse(0);
        require(st_gpu_ns == 60, "slot 1's old times not counted again");
        require(ts_pending[1], "slot 1's new use still pending");
    } else if (c == "each") {   // RX_VK_TS_EACH=1: one call a slot, as before
        each_switch = true;
        for (int i = 0; i < 6; i++) timed(i, 5, true);
        for (int i = 0; i < 6; i++) reuse(i);
        require(st_gpu_ns == 30 && calls == 6, "one slot a call");
    } else {
        throw std::runtime_error("unknown case");
    }
}

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    try {
        run(argv[1]);
    } catch (const std::exception& e) {
        std::printf("%s: FAIL (%s)\n", argv[1], e.what());
        return 1;
    }
    std::printf("%s: ok\n", argv[1]);
    return 0;
}
