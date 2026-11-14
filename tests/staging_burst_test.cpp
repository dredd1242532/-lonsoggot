// tests/test_staging_burst.py puts vk_texture.cpp's staging() and texture_frame's give-back loop where ACTUAL is and
// runs the cases below: frames that stage, then present, on a clock the fixture moves.
#include <vulkan/vulkan.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

namespace fixture {
void require(bool ok, const char* why) {
    if (!ok) {
        fprintf(stderr, "FAIL: %s\n", why);
        std::exit(1);
    }
}
int64_t now_ns = 1'000'000'000;
struct Clock {   // (std::chrono::steady_clock in the code cut out)
    using duration = std::chrono::nanoseconds;
    using rep = duration::rep;
    using period = duration::period;
    using time_point = std::chrono::time_point<Clock, duration>;
    static constexpr bool is_steady = true;
    static time_point now() { return time_point(duration(now_ns)); }
};
struct VmaAllocation_T {};
using VmaAllocation = VmaAllocation_T*;
unsigned makes = 0, destroys = 0;
uint8_t mapped_stand_in;   // (never written: the cases only count arenas)
uint64_t rec_value = 1, completed_value = 0;
struct { VkPhysicalDeviceProperties props{}; } dev;
bool create_buffer(uint64_t, VkBufferUsageFlags, bool, VkBuffer& b, VmaAllocation& a, void** map) {
    a = new VmaAllocation_T;
    b = reinterpret_cast<VkBuffer>(a);
    *map = &mapped_stand_in;
    makes++;
    return true;
}
void destroy_buffer(VkBuffer b, VmaAllocation a) {
    require(a && b == reinterpret_cast<VkBuffer>(a), "an arena is destroyed with its own allocation");
    delete a;
    destroys++;
}
void logf(const char*, ...) {}
enum LogLevel : int { log_error, log_warn, log_info, log_debug, log_trace };
void logf_at(LogLevel, const char*, ...) {}

/* ACTUAL */

constexpr uint64_t MB = 1ull << 20;
uint64_t keep = 64 * MB;   // (what a card of 6.5 GB or less keeps between loads; 256 MB above)
// One frame: a submission that stages the pieces, then the present. The GPU finishes a frame's submission between that
// frame's present and the next one (one frame in flight)
void frame(const std::vector<uint64_t>& pieces, int64_t ms = 33) {
    rec_value++;
    for (uint64_t n : pieces) require(staging(n).buffer != VK_NULL_HANDLE, "a piece is staged");
    completed_value = rec_value - 1;
    give_back(completed_value, keep);
    now_ns += ms * 1'000'000;
}
uint64_t held() {
    uint64_t n = 0;
    for (const Arena& a : arenas) n += a.size;
    return n;
}

int run(int argc, char** argv) {
    require(argc == 2, "a case");
    const std::string test = argv[1];
    const bool off = getenv("RX_VK_STAGING_BURST_S") && atoll(getenv("RX_VK_STAGING_BURST_S")) == 0;
    dev.props.limits.nonCoherentAtomSize = 64;
    if (test == "consecutive") {
        // a little staged in each of 20 frames in a row: the frame before's arena is not the GPU's to give yet, so two take
        // turns. Kept to 64 MB, the idle one of the two went at every second present and was made again a frame later
        for (int i = 0; i < 20; i++) frame({1 * MB});
        if (off) require(makes >= 8, "RX_VK_STAGING_BURST_S=0: an arena made every second frame, as before");
        else require(makes == 2 && destroys == 0, "two arenas made for 20 frames in a row, none given back between");
    } else if (test == "settles") {
        for (int i = 0; i < 20; i++) frame({1 * MB});
        for (int i = 0; i < 70; i++) frame({});   // (2.3 s with nothing staged)
        require(arenas.size() == 1 && held() == 64 * MB, "the load over, what is kept between loads is left");
        for (int i = 0; i < 31; i++) frame({}, 1000);
        require(arenas.empty() && destroys == makes, "nothing staged for 30 s: none kept");
    } else if (test == "cap") {
        frame({60 * MB, 60 * MB, 60 * MB, 60 * MB, 60 * MB, 60 * MB});   // (60 + 60 MB do not fit one arena: six)
        require(arenas.size() == 6, "six arenas for six pieces of 60 MB");
        frame({});
        if (off) require(held() == 64 * MB, "RX_VK_STAGING_BURST_S=0: 64 MB kept, as before");
        else require(held() == 256 * MB, "at most 256 MB of the arenas just used are kept");
    } else if (test == "big") {
        frame({100 * MB});
        require(arenas.size() == 1 && arenas[0].size == 100 * MB, "a piece over 64 MB has an arena of its size");
        frame({});
        require(arenas.empty(), "an arena over 64 MB goes once the GPU is done with it, used a moment ago or not");
    } else if (test == "keep256") {
        keep = 256 * MB;   // (a card over 6.5 GB: the rule adds nothing to what it keeps)
        for (int i = 0; i < 20; i++) frame({1 * MB});
        require(makes == 2 && destroys == 0, "256 MB kept between loads: two arenas for 20 frames, as before");
        frame({60 * MB, 60 * MB, 60 * MB, 60 * MB, 60 * MB, 60 * MB});
        frame({});
        require(held() == 256 * MB, "256 MB kept between loads: no more than that after a load");
    } else {
        require(false, "a known case");
    }
    return 0;
}
} // namespace fixture
int main(int argc, char** argv) { return fixture::run(argc, argv); }
