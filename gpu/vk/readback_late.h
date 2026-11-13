// (longshot) RX_VK_READBACK_LATE=<rule>[,<rule>...]: the CPU reads of GPU results that do not wait for their write-back
// (vk_replay.cpp invalidate). Off unless set: every such read waits, as before. The CPU then reads what guest memory holds,
// the write-back of the frame before. Two rules, either is enough:
//   newest       the range's writer is the newest submission handed out: the game reads it right after submitting the work
//                that fills it, so it cannot count on that work being done;
//   <hex size>   every range of exactly that many bytes, whoever wrote it.
// OoT on a Steam Deck: one pair of 0x3980-byte ranges is 94% of the readbacks' wait (camilobt's finding, Hookshot issue
// 1000); RX_VK_LOG_READBACK_RANGES=1 logs the ranges, to find them on other hardware. Not a rule for every range: the
// exposure meter read late is the white flashes the wait is there for (invalidate's comment).
#pragma once
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace readback_late {

struct Rules {
    bool newest = false;
    std::vector<uint64_t> sizes;
    bool any() const { return newest || !sizes.empty(); }
};

inline Rules parse(const char* text) {
    Rules r;
    for (const char* p = text; p && *p;) {
        if (!strncmp(p, "newest", 6)) r.newest = true;
        else if (const uint64_t n = strtoull(p, nullptr, 16)) r.sizes.push_back(n);
        p = strchr(p, ',');
        if (p) p++;
    }
    return r;
}

inline bool wanted(const Rules& r, uint64_t size, bool newest) {
    if (newest && r.newest) return true;
    for (const uint64_t s : r.sizes)
        if (s == size) return true;
    return false;
}

} // namespace readback_late
