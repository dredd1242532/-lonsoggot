// tests/test_guest_holes.py compiles and runs this: gpu/vk/guest_holes.h against a real memfd (Linux) and plain memory.
#include "../gpu/vk/guest_holes.h"

#include <cstdio>
#include <cstring>
#if defined(__linux__)
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>
#endif

#define CHECK(c)                                                           \
    do {                                                                   \
        if (!(c)) {                                                        \
            std::fprintf(stderr, "%s:%d: not true: %s\n", __FILE__, __LINE__, #c); \
            return 1;                                                      \
        }                                                                  \
    } while (0)

using Parts = std::vector<std::pair<uint64_t, uint64_t>>;

int main() {
    {   // all_zero, on any platform: what it reads and what it leaves alone
        // (4 MB and 3 bytes: with 4 threads a share of exactly 1 MB each would leave the last 3 bytes unread)
        std::vector<uint8_t> m((4 << 20) + 3, 0);
        const Parts whole{{0, m.size()}};
        CHECK(guest_holes::all_zero(m.data(), whole, 1));
        CHECK(guest_holes::all_zero(m.data(), whole, 4));
        m[m.size() - 1] = 1;
        CHECK(!guest_holes::all_zero(m.data(), whole, 1));
        CHECK(!guest_holes::all_zero(m.data(), whole, 4));
        m[m.size() - 1] = 0;
        const uint64_t end = 3 << 20;
        const Parts two{{0, 4096}, {8192, end}};   // (4 threads: the first one's share runs over both parts)
        m[5000] = 7;   // (between the parts: not read)
        CHECK(guest_holes::all_zero(m.data(), two, 1));
        CHECK(guest_holes::all_zero(m.data(), two, 4));
        m[4095] = 7;   // (a part's last byte)
        CHECK(!guest_holes::all_zero(m.data(), two, 4));
        m[4095] = 0, m[8192] = 7;   // (a part's first byte)
        CHECK(!guest_holes::all_zero(m.data(), two, 4));
        m[8192] = 0, m[end - 1] = 7;   // (the last part's last byte)
        CHECK(!guest_holes::all_zero(m.data(), two, 4));
        CHECK(!guest_holes::all_zero(m.data(), two, 3));
        m[end - 1] = 0, m[end] = 7;   // (one byte past the last part)
        CHECK(guest_holes::all_zero(m.data(), two, 4));
        CHECK(guest_holes::all_zero(m.data(), two, 3));
        CHECK(guest_holes::all_zero(m.data(), Parts{}, 4));   // (nothing to read)
        CHECK(!guest_holes::all_zero(m.data(), whole, 0));   // (no thread count: read all the same, the 7s found)
    }
    Parts parts{{1, 2}};
    CHECK(!guest_holes::data_parts(-1, 0, 4096, parts) && parts.empty());   // (no file: not known)
#if defined(__linux__)
    const uint64_t size = 64ull << 20, page = (uint64_t)sysconf(_SC_PAGESIZE);
    const int fd = (int)syscall(SYS_memfd_create, "guest-holes", 1u);
    CHECK(fd >= 0 && ftruncate(fd, (off_t)size) == 0);
    uint8_t* w = (uint8_t*)mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    CHECK(w != MAP_FAILED);

    // never written: no data, the check reads nothing and makes no page
    CHECK(guest_holes::data_parts(fd, 0, size, parts) && parts.empty());
    CHECK(guest_holes::all_zero(w, parts, 4));
    CHECK(guest_holes::data_parts(fd, 0, size, parts) && parts.empty());

    // two written runs, one of them written with zeros (data all the same)
    std::memset(w + 16 * page, 0, 3 * page);
    w[40 * page + 5] = 9;
    CHECK(guest_holes::data_parts(fd, 0, size, parts));
    CHECK(parts == (Parts{{16 * page, 19 * page}, {40 * page, 41 * page}}));
    CHECK(!guest_holes::all_zero(w, parts, 4));

    // a range inside the file, not page aligned: offsets from its start, clipped to it
    CHECK(guest_holes::data_parts(fd, 17 * page + 100, 40 * page + 5, parts));
    CHECK(parts == (Parts{{0, 2 * page - 100}, {23 * page - 100, 23 * page - 95}}));
    CHECK(guest_holes::all_zero(w + 17 * page + 100, parts, 2));   // (the 9 is the first byte past it)
    CHECK(guest_holes::data_parts(fd, 17 * page + 100, 40 * page + 6, parts));
    CHECK(!guest_holes::all_zero(w + 17 * page + 100, parts, 2));

    // given back, as the host frees guest pages (MADV_REMOVE): a hole again
    CHECK(madvise(w + 40 * page, page, MADV_REMOVE) == 0);
    CHECK(guest_holes::data_parts(fd, 0, size, parts) && parts == (Parts{{16 * page, 19 * page}}));
    CHECK(guest_holes::all_zero(w, parts, 4));

    // the last page, and past the file's end
    w[size - 1] = 3;
    CHECK(guest_holes::data_parts(fd, 0, size, parts) && parts.size() == 2 && parts[1] == std::make_pair(size - page, size));
    CHECK(guest_holes::data_parts(fd, size, size + page, parts) && parts.empty());

    // (what the question saves, for the record: a read of a hole through the mapping makes its page on this kernel or not)
    volatile uint8_t sink = w[50 * page];
    (void)sink;
    CHECK(guest_holes::data_parts(fd, 50 * page, 51 * page, parts));
    std::printf("guest holes: a read of a hole through the mapping %s its page here\n", parts.empty() ? "does not make" : "makes");
#else
    CHECK(!guest_holes::data_parts(0, 0, 4096, parts) && parts.empty());   // (not Linux: not known, all of it is read)
#endif
    std::puts("guest holes: ok");
    return 0;
}
