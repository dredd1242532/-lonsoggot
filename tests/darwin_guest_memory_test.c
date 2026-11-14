/* Included after text-extracted production functions by test_darwin_guest_memory.py.
   All addresses/data below are synthetic, and every VM operation is real. */
#include <sys/wait.h>

const uint64_t hos_image_base = 0x80000000, hos_image_end = 0x80100000;

static void expect_bytes(const unsigned char *p, size_t n, unsigned char value) {
    for (size_t i = 0; i < n; i++) {
        if (p[i] != value) {
            fprintf(stderr, "FAIL: byte %zu expected=%u actual=%u\n", i, value, p[i]);
            exit(1);
        }
    }
}
static void memory_call(void (*fn)(Ctx *), uint64_t addr, uint64_t size, uint64_t result) {
    Ctx c = {0}; c.x[0] = addr; c.x[1] = size;
    fn(&c);
    CHECK(c.x[0] == result);
}
static uint64_t resident_bytes(void) {
    mach_task_basic_info_data_t info;
    mach_msg_type_number_t count = MACH_TASK_BASIC_INFO_COUNT;
    CHECK(task_info(mach_task_self(), MACH_TASK_BASIC_INFO, (task_info_t)&info, &count) == KERN_SUCCESS);
    return info.resident_size;
}
static vm_prot_t protection(void *p) {
    mach_vm_address_t at = (mach_vm_address_t)p;
    mach_vm_size_t n = 0;
    vm_region_basic_info_data_64_t info;
    mach_msg_type_number_t count = VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t object = MACH_PORT_NULL;
    const kern_return_t kr = mach_vm_region(mach_task_self(), &at, &n, VM_REGION_BASIC_INFO_64,
                                           (vm_region_info_t)&info, &count, &object);
    if (object != MACH_PORT_NULL) CHECK(mach_port_deallocate(mach_task_self(), object) == KERN_SUCCESS);
    CHECK(kr == KERN_SUCCESS && at <= (mach_vm_address_t)p && (mach_vm_address_t)p - at < n);
    return info.protection;
}
static void adjacent_pages(uint64_t addr, unsigned rounds, int check_live_commit) {
    const size_t bytes = 65536, guest_page = 4096;
    unsigned char expected[65536];
    unsigned char *p = GP(addr);
    memset(p, 0x31, bytes); memset(expected, 0x31, bytes);
    for (unsigned round = 0; round < rounds; round++) {
        const size_t off = ((round * 3) % 4) * guest_page;
        if (check_live_commit) {
            memory_call(imp__ZN2nn2os19AllocateMemoryPagesEmm, addr + off, guest_page, 0);
            CHECK(!memcmp(p, expected, bytes));
        }
        const unsigned char value = (unsigned char)(1 + round % 254);
        memset(p + off, value, guest_page); memset(expected + off, value, guest_page);
        CHECK(!memcmp(p, expected, bytes));
        memory_call(imp__ZN2nn2os15FreeMemoryPagesEmm, addr + off, guest_page, 0);
        memset(expected + off, 0, guest_page);
        CHECK(!memcmp(p, expected, bytes));
        memory_call(imp__ZN2nn2os19AllocateMemoryPagesEmm, addr + off, guest_page, 0);
        CHECK(!memcmp(p, expected, bytes));
        memory_call(imp__ZN2nn2os15FreeMemoryPagesEmm, addr + off, guest_page, 0);
        CHECK(!memcmp(p, expected, bytes));
    }
    printf("ADJACENT guest=%" PRIu64 " rounds=%u guest_page=4096 live_commit=%d neighbors=preserved zero_on_recommit=1\n",
           addr, rounds, check_live_commit);
}
static void byte_ranges(uint64_t addr, size_t page) {
    const size_t bytes = 6 * page;
    unsigned char *p = GP(addr), *expected = malloc(bytes);
    CHECK(expected);
    const size_t ranges[][2] = {{7, 13}, {page - 7, 13}, {1, 3 * page + 19},
                                {page, 3 * page}, {4096, 4096}, {bytes - 19, 19}};
    unsigned count = 0;
    for (int op = 0; op < 4; op++) {
        for (size_t i = 0; i < sizeof ranges / sizeof ranges[0]; i++) {
            memset(p, 0x6e, bytes); memset(expected, 0x6e, bytes);
            const size_t off = ranges[i][0], n = ranges[i][1];
            if (op == 0) CHECK(!hos_posix_guest_discard(p + off, n));
            if (op == 1) CHECK(VirtualFree(p + off, n, MEM_DECOMMIT));
            if (op == 2) CHECK(VirtualAlloc(p + off, n, MEM_RESET, PAGE_READWRITE) == p + off);
            if (op == 3) {
                Ctx c = {0}; c.x[0] = addr + off; c.x[1] = n;
                physical_svc(&c, 0x2D); CHECK(c.x[0] == 0);
            }
            memset(expected + off, 0, n);
            CHECK(!memcmp(p, expected, bytes));
            hos_commit(addr + off, n);
            CHECK(!memcmp(p, expected, bytes));
            count++;
        }
    }
    CHECK(!hos_posix_guest_discard(p + 3, 0));
    CHECK(!memcmp(p, expected, bytes));
    free(expected);
    printf("BYTE_RANGES cases=%u host_page=%zu outside_unchanged=1 zero_on_recommit=1\n", count, page);
}
static void host_allocations(size_t page) {
    unsigned char *p = VirtualAlloc(NULL, 4 * page, MEM_RESERVE, PAGE_NOACCESS);
    CHECK(p && !((uintptr_t)p % page));
    CHECK(protection(p) == VM_PROT_NONE);
    CHECK(!VirtualFree(p + 1, page, MEM_DECOMMIT));
    CHECK(GetLastError() == ERROR_ACCESS_DENIED);
    CHECK(!VirtualAlloc(p + 1, page, MEM_RESET, PAGE_READWRITE));
    CHECK(GetLastError() == ERROR_ACCESS_DENIED);
    CHECK(VirtualAlloc(p + 4096, 4096, MEM_COMMIT, PAGE_READWRITE) == p + 4096);
    CHECK(protection(p + 4096) == (VM_PROT_READ | VM_PROT_WRITE));
    CHECK(protection(p + 3 * page) == VM_PROT_NONE);
    if (page == 16384) CHECK(protection(p) == (VM_PROT_READ | VM_PROT_WRITE));
    if (page == 4096) CHECK(protection(p) == VM_PROT_NONE);
    DWORD old;
    CHECK(VirtualProtect(p + 4097, 1, PAGE_READONLY, &old));
    CHECK(protection(p + 4096) == VM_PROT_READ);
    CHECK(protection(p + 3 * page) == VM_PROT_NONE);
    CHECK(VirtualAlloc(p, 4 * page, MEM_COMMIT, PAGE_READWRITE) == p);
    memset(p, 0x83, 4 * page);
    CHECK(!VirtualAlloc(p, 4 * page, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE));
    CHECK(GetLastError() == ERROR_ALREADY_EXISTS);
    expect_bytes(p, 4 * page, 0x83);
    CHECK(VirtualFree(p + 3, 2 * page + 17, MEM_DECOMMIT));
    expect_bytes(p, 3, 0x83); expect_bytes(p + 3, 2 * page + 17, 0);
    expect_bytes(p + 2 * page + 20, 2 * page - 20, 0x83);
    CHECK(VirtualFree(p, 0, MEM_RELEASE));
    CHECK(VirtualAlloc(p, 4 * page, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE) == p);
    expect_bytes(p, 4 * page, 0);
    CHECK(VirtualFree(p, 0, MEM_RELEASE));
    printf("HOST_ALLOC page=%zu reserve_commit_protect_release=1 duplicate_preserved=1 decommit_exact=1\n", page);
}
static void region_lifecycle(void) {
    uint64_t addr[8];
    for (int i = 0; i < 8; i++) {
        addr[i] = hos_region_alloc(4096, 4096);
        expect_bytes(GP(addr[i]), 4096, 0);
        memset(GP(addr[i]), 0x40 + i, 4096);
    }
    const uint64_t freed = addr[3];
    hos_region_free(freed);
    for (int i = 0; i < 8; i++) expect_bytes(GP(addr[i]), 4096, i == 3 ? 0 : (unsigned char)(0x40 + i));
    addr[3] = hos_region_alloc(4096, 4096);
    CHECK(addr[3] == freed);
    expect_bytes(GP(addr[3]), 4096, 0);
    for (int i = 0; i < 8; i++) hos_region_free(addr[i]);
    CHECK(nregions == 0);
    puts("REGIONS adjacent=8 middle_reused=1 neighbors=preserved zero_on_recommit=1");
}
static void failure_paths(uint64_t addr, size_t page) {
    unsigned char *p = GP(addr);
    memset(p, 0x93, 4 * page);
    CHECK(!hos_posix_guest_discard(p + 7, 0));
    CHECK(hos_posix_guest_discard((void *)(UINTPTR_MAX - 15), 32) == -1 && errno == EINVAL);
    CHECK(!VirtualAlloc((void *)(UINTPTR_MAX - 15), 32, MEM_COMMIT, PAGE_READWRITE));
    CHECK(!VirtualAlloc(p + 1, page, MEM_RESERVE, PAGE_READWRITE));
    DWORD old;
    CHECK(VirtualProtect(p + page, page, PAGE_NOACCESS, &old));
    CHECK(hos_posix_guest_discard(p + 1, 2 * page) == -1 && errno == EACCES);
    expect_bytes(p, page, 0x93); expect_bytes(p + 2 * page, 2 * page, 0x93);
    memory_call(imp__ZN2nn2os15FreeMemoryPagesEmm, addr + page, page, 0xCA01);
    Ctx c = {0}; c.x[0] = addr + page; c.x[1] = page;
    physical_svc(&c, 0x2D); CHECK(c.x[0] == 0xCA01);
    CHECK(VirtualProtect(p + page, page, PAGE_READWRITE, &old));
    expect_bytes(p, 4 * page, 0x93);
    CHECK(!munmap(p + page, page));
    CHECK(hos_posix_guest_discard(p + 1, 2 * page) == -1);
    expect_bytes(p, page, 0x93); expect_bytes(p + 2 * page, 2 * page, 0x93);
    CHECK(VirtualAlloc(p + page, page, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE) == p + page);
    /* The hole was created by this fixture only. Release that separately tracked
       reservation now; no later case uses these guest addresses. */
    CHECK(VirtualFree(p + page, 0, MEM_RELEASE));
    const pid_t child = fork(); CHECK(child >= 0);
    if (!child) {
        const uint64_t a = hos_region_alloc(page, page);
        CHECK(VirtualProtect(GP(a), page, PAGE_NOACCESS, &old));
        hos_region_free(a);
        _exit(24); /* A failed region discard must reach hos_fatal (exit 23 here). */
    }
    int status = 0; CHECK(waitpid(child, &status, 0) == child);
    CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 23);
    puts("FAILURE_PATHS overflow_unaligned_hole_readonly=refused free_svc_result=0xca01 region_fatal_exit=23");
}
static void reclaim_large(uint64_t addr, size_t page) {
    const size_t bytes = 128u << 20;
    unsigned char *p = GP(addr);
    hos_commit(addr, bytes);
    memset(p - page, 0x4d, page); memset(p + bytes, 0x8b, page);
    uint64_t value = UINT64_C(0x784688a835a295e9);
    for (size_t i = 0; i < bytes / sizeof(uint64_t); i++) {
        value ^= value << 13; value ^= value >> 7; value ^= value << 17;
        ((uint64_t *)p)[i] = value;
    }
    const uint64_t before = resident_bytes();
    const int64_t t0 = hos_now_ns();
    memory_call(imp__ZN2nn2os15FreeMemoryPagesEmm, addr, bytes, 0);
    const int64_t elapsed = hos_now_ns() - t0;
    const uint64_t after = resident_bytes();
    printf("RECLAIM bytes=%zu rss_before=%" PRIu64 " rss_after=%" PRIu64 " drop=%" PRId64 " cost_us=%.3f\n",
           bytes, before, after, (int64_t)before - (int64_t)after, elapsed / 1000.0);
    CHECK(before > after && before - after >= bytes * 7 / 8);
    expect_bytes(p - page, page, 0x4d); expect_bytes(p + bytes, page, 0x8b);
    hos_commit(addr, bytes);
    expect_bytes(p, bytes, 0);
    expect_bytes(p - page, page, 0x4d); expect_bytes(p + bytes, page, 0x8b);
    puts("RECLAIM zero_on_recommit=1 neighbors=preserved");
}
int main(void) {
    setvbuf(stdout, NULL, _IONBF, 0);
    const size_t page = (size_t)getpagesize();
    CHECK(page == 4096 || page == 16384);
    CHECK(reserve_guest());
    memset(A64_MEM, 0x56, page);
    memset(A64_MEM + HOS_GUEST_SPACE - page, 0xa8, page);
    CHECK(!reserve_guest());
    CHECK(strstr(last_log, "already occupied"));
    expect_bytes(A64_MEM, page, 0x56);
    expect_bytes(A64_MEM + HOS_GUEST_SPACE - page, page, 0xa8);
    CHECK(commit_init_calls == 2 && hos_guest_memfd == -1);
    printf("RESERVATION base=%#llx bytes=%llu page=%zu duplicate_refused=1 first_last_preserved=1\n",
           (unsigned long long)A64_HOST_BASE, (unsigned long long)HOS_GUEST_SPACE, page);
    /* The top-down region allocator will use this page later. Give our collision
       sentinel back first; a commit must not erase data already present. */
    CHECK(!hos_posix_guest_discard(A64_MEM + HOS_GUEST_SPACE - page, page));
    expect_bytes(A64_MEM + HOS_GUEST_SPACE - page, page, 0);
    const uint64_t addr = HOS_HEAP_BASE + 0x200000;
    memset(GP(addr), 0x91, 2 * 65536);
    hos_commit(addr + 4096, 65536);
    CHECK(committed_at == (uintptr_t)GP(addr) && committed_size == 2 * 65536);
    expect_bytes(GP(addr), 2 * 65536, 0x91);
    puts("COMMIT rounding=65536 live_bytes_preserved=1");
    adjacent_pages(addr, 128, 1);
    adjacent_pages(0x200000, 32, 0);
    byte_ranges(addr + 0x400000, page);
    host_allocations(page);
    region_lifecycle();
    failure_paths(addr + 0x800000, page);
    reclaim_large(addr + 0x10000000, page);
    CHECK(import_checks > 0);
    CHECK(!munmap(A64_MEM, HOS_GUEST_SPACE));
    puts("PASS: reservation, adjacent guest pages, byte ranges, errors and physical reclaim");
    return 0;
}
