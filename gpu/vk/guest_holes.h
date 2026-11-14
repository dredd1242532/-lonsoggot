// What of a range of guest memory was ever written, and whether that is all zero (vk_texture.cpp, upload_all_zero).
// On Linux the guest window is a memfd's (host/hos_main.c): a part of it never written, or given back since (MADV_REMOVE),
// is a hole of the file. A hole reads as zero, and reading it through the mapping makes its pages.
// tests/test_guest_holes.py
#pragma once
#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <thread>
#include <utility>
#include <vector>
#if defined(__linux__)
#include <cerrno>
#include <unistd.h>
#endif

namespace guest_holes {

// The data in the file's bytes lo..hi, as [first, second) offsets from lo, in order. false: not known (not Linux, no file,
// a seek the file refused), and the caller reads all of it.
inline bool data_parts(int fd, uint64_t lo, uint64_t hi, std::vector<std::pair<uint64_t, uint64_t>>& parts) {
    parts.clear();
#if defined(__linux__) && defined(SEEK_DATA)
    if (fd < 0 || hi < lo) return false;
    for (uint64_t at = lo; at < hi;) {
        const off_t d = lseek(fd, (off_t)at, SEEK_DATA);
        if (d < 0) {
            if (errno == ENXIO) return true;   // (no data from here to the file's end)
            parts.clear();
            return false;
        }
        if ((uint64_t)d >= hi) return true;
        const off_t h = lseek(fd, d, SEEK_HOLE);
        if (h <= d) {
            parts.clear();
            return false;
        }
        parts.emplace_back((uint64_t)d - lo, std::min((uint64_t)h, hi) - lo);
        at = (uint64_t)h;
    }
    return true;
#else
    (void)fd, (void)lo, (void)hi;
    return false;
#endif
}

// Whether every byte of p's parts is zero. Read once, on up to `threads` threads (one for each MB at most), each stopping
// at the first byte that is not zero.
inline bool all_zero(const uint8_t* p, const std::vector<std::pair<uint64_t, uint64_t>>& parts, uint32_t threads) {
    uint64_t data = 0;
    for (const auto& r : parts) data += r.second - r.first;
    if (!data) return true;
    threads = (uint32_t)std::clamp<uint64_t>(threads, 1, (data + 0xFFFFF) >> 20);
    const uint64_t per = (((data + threads - 1) / threads) + 4095) & ~4095ull;
    std::atomic<bool> found{false};
    const auto share = [&](uint32_t t) {   // (thread t's: bytes t * per .. + per of the data, the parts taken end to end)
        uint64_t skip = (uint64_t)t * per, left = per;
        for (const auto& r : parts) {
            const uint64_t len = r.second - r.first;
            if (skip >= len) {
                skip -= len;
                continue;
            }
            const uint64_t a = r.first + skip, b = std::min(r.second, a + left);
            skip = 0, left -= b - a;
            for (uint64_t at = a; at < b && !found.load(std::memory_order_relaxed); at += 65536) {
                const uint64_t end = std::min(b, at + 65536);
                uint64_t acc = 0, k = at;
                for (; k + 8 <= end; k += 8) {
                    uint64_t w;
                    std::memcpy(&w, p + k, 8);
                    acc |= w;
                }
                for (; k < end; k++) acc |= p[k];
                if (acc) found.store(true, std::memory_order_relaxed);
            }
            if (!left || found.load(std::memory_order_relaxed)) return;
        }
    };
    std::vector<std::thread> th;
    for (uint32_t t = 1; t < threads; t++) th.emplace_back(share, t);
    share(0);
    for (auto& x : th) x.join();
    return !found.load();
}

} // namespace guest_holes
