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
            /* The helper is byte-exact. Public wrapper contracts stay those of
               the existing POSIX layer: decommit outward, reset inward, at 4 KiB. */
            size_t lo = off, hi = off + n;
            if (op == 1 || op == 3) { lo &= ~(size_t)4095; hi = (hi + 4095) & ~(size_t)4095; }
            if (op == 2) { lo = (lo + 4095) & ~(size_t)4095; hi &= ~(size_t)4095; }
            if (hi > lo) memset(expected + lo, 0, hi - lo);
            CHECK(!memcmp(p, expected, bytes));
            hos_commit(addr + off, n);
            CHECK(!memcmp(p, expected, bytes));
            count++;
        }
    }
    CHECK(!hos_posix_guest_discard(p + 3, 0));
    CHECK(!memcmp(p, expected, bytes));
    free(expected);
    printf("BYTE_RANGES cases=%u host_page=%zu helper_byte_exact=1 wrappers_guest_rounded=1 zero_on_recommit=1\n", count, page);
}
static void host_allocations(size_t page) {
    unsigned char *p = VirtualAlloc(NULL, 4 * page, MEM_RESERVE, PAGE_NOACCESS);
    CHECK(p && !((uintptr_t)p % page));
    CHECK(protection(p) == VM_PROT_NONE);
    CHECK(!VirtualFree(p + 1, page, MEM_DECOMMIT));
    CHECK(GetLastError() == ERROR_ACCESS_DENIED);
    CHECK(!VirtualAlloc(p, page, MEM_RESET, PAGE_READWRITE));
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
    const size_t discarded = (2 * page + 20 + 4095) & ~(size_t)4095;
    expect_bytes(p, discarded, 0);
    expect_bytes(p + discarded, 4 * page - discarded, 0x83);
    CHECK(VirtualFree(p, 0, MEM_RELEASE));
    CHECK(VirtualAlloc(p, 4 * page, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE) == p);
    expect_bytes(p, 4 * page, 0);
    CHECK(VirtualFree(p, 0, MEM_RELEASE));
    printf("HOST_ALLOC page=%zu reserve_commit_protect_release=1 duplicate_preserved=1 decommit_guest_rounded=1\n", page);
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
    CHECK(darwin_mem_map(p + page, page, PROT_READ | PROT_WRITE) == p + page);
    /* Only the fixture made this hole. The shared shim must never create a
       guest release record that could later bypass a canonical import pin. */
    CHECK(!VirtualFree(p + page, 0, MEM_RELEASE));
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

typedef struct {
    void *image;
    void *(*alloc)(void *, SIZE_T, DWORD, DWORD);
    BOOL (*release)(void *, SIZE_T, DWORD);
    BOOL (*protect)(void *, SIZE_T, DWORD, DWORD *);
    SIZE_T (*query)(const void *, MEMORY_BASIC_INFORMATION *, SIZE_T);
} MemoryImage;
static MemoryImage memory_image(const char *path) {
    MemoryImage m = {0};
    m.image = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!m.image) { fprintf(stderr, "%s\n", dlerror()); exit(2); }
    m.alloc = (void *(*)(void *, SIZE_T, DWORD, DWORD))dlsym(m.image, "VirtualAlloc");
    m.release = (BOOL (*)(void *, SIZE_T, DWORD))dlsym(m.image, "VirtualFree");
    m.protect = (BOOL (*)(void *, SIZE_T, DWORD, DWORD *))dlsym(m.image, "VirtualProtect");
    m.query = (SIZE_T (*)(const void *, MEMORY_BASIC_INFORMATION *, SIZE_T))dlsym(m.image, "VirtualQuery");
    CHECK(m.alloc && m.release && m.protect && m.query);
    return m;
}

/* The only intercepted operation supplies a latch immediately before the REAL
   production discard. It does not replace successes, errors, bytes or maps. */
static pthread_mutex_t pause_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t pause_cv = PTHREAD_COND_INITIALIZER;
static uintptr_t pause_addr;
static int pause_enabled, pause_entered, pause_continue;
static unsigned raw_discards;
static int observed_discard(void *addr, size_t size) {
    __atomic_add_fetch(&raw_discards, 1, __ATOMIC_RELAXED);
    CHECK(!pthread_mutex_lock(&pause_mu));
    if (pause_enabled && (uintptr_t)addr == pause_addr) {
        pause_entered = 1;
        CHECK(!pthread_cond_broadcast(&pause_cv));
        while (!pause_continue) CHECK(!pthread_cond_wait(&pause_cv, &pause_mu));
    }
    CHECK(!pthread_mutex_unlock(&pause_mu));
    return darwin_mem_discard(addr, size);
}
static void pause_discard(uint64_t addr) {
    CHECK(!pthread_mutex_lock(&pause_mu));
    pause_addr = (uintptr_t)GP(addr);
    pause_enabled = 1, pause_entered = 0, pause_continue = 0;
    CHECK(!pthread_mutex_unlock(&pause_mu));
}
static void wait_discard(void) {
    CHECK(!pthread_mutex_lock(&pause_mu));
    while (!pause_entered) CHECK(!pthread_cond_wait(&pause_cv, &pause_mu));
    CHECK(!pthread_mutex_unlock(&pause_mu));
}
static void resume_discard(void) {
    CHECK(!pthread_mutex_lock(&pause_mu));
    pause_enabled = 0, pause_continue = 1;
    CHECK(!pthread_cond_broadcast(&pause_cv));
    CHECK(!pthread_mutex_unlock(&pause_mu));
}
static void await_flag(unsigned *flag) {
    const int64_t until = hos_now_ns() + INT64_C(10000000000);
    while (!__atomic_load_n(flag, __ATOMIC_ACQUIRE)) {
        CHECK(hos_now_ns() < until);
        sched_yield();
    }
}
static void expect_locked(pthread_mutex_t *mu) {
    const int result = pthread_mutex_trylock(mu);
    if (!result) pthread_mutex_unlock(mu);
    CHECK(result == EBUSY);
}

static void query_ranges(uint64_t addr, size_t page, const MemoryImage *one, const MemoryImage *two) {
    DWORD old = 0;
    MEMORY_BASIC_INFORMATION info;
    unsigned char *p = VirtualAlloc(NULL, 6 * page, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    CHECK(p);
    CHECK(VirtualProtect(p + page + 13, 1, PAGE_READONLY, &old) && old == PAGE_READWRITE);
    CHECK(VirtualQuery(p + page + 31, &info, sizeof info) == sizeof info);
    CHECK(info.BaseAddress == p + page && info.RegionSize == page && info.Protect == PAGE_READONLY && info.State == MEM_COMMIT);
    CHECK(info.AllocationBase == p);
    CHECK(VirtualProtect(p + page, page, PAGE_READWRITE, &old) && old == PAGE_READONLY);
    CHECK(VirtualProtect(p + 3 * page, page, PAGE_NOACCESS, &old) && old == PAGE_READWRITE);
    CHECK(two->query(p + 3 * page + 1, &info, sizeof info) == sizeof info);
    CHECK(info.BaseAddress == p + 3 * page && info.RegionSize == page && info.Protect == PAGE_NOACCESS);
    CHECK(!munmap(p + 2 * page, page));
    CHECK(one->query(p + 2 * page + 1, &info, sizeof info) == sizeof info);
    CHECK(info.State == MEM_FREE && info.BaseAddress == p + 2 * page && info.RegionSize == page && !info.AllocationBase);
    CHECK(darwin_mem_map(p + 2 * page, page, PROT_READ | PROT_WRITE) == p + 2 * page);
    CHECK(VirtualFree(p, 0, MEM_RELEASE));
    p = GP(addr);
    CHECK(one->protect(p + page + 13, 1, PAGE_READONLY, &old) && old == PAGE_READWRITE);
    CHECK(two->query(p + page + 37, &info, sizeof info) == sizeof info);
    CHECK(info.BaseAddress == p + page && info.RegionSize == page && info.Protect == PAGE_READONLY && info.State == MEM_COMMIT);
    CHECK(info.AllocationBase == GP(0));
    CHECK(two->protect(p + page, page, PAGE_NOACCESS, &old) && old == PAGE_READONLY);
    CHECK(one->query(p + page, &info, sizeof info) == sizeof info && info.State == MEM_COMMIT && info.Protect == PAGE_NOACCESS);
    CHECK(one->protect(p + page, page, PAGE_READWRITE, &old) && old == PAGE_NOACCESS);
    CHECK(!one->query(p, &info, sizeof info - 1));
    CHECK(!two->query(p, NULL, sizeof info));
    CHECK(!one->query((void *)UINTPTR_MAX, &info, sizeof info));
    CHECK(!munmap(p + page, page));
    CHECK(two->query(p + page + 9, &info, sizeof info) == sizeof info && info.State == MEM_FREE && info.RegionSize == page);
    CHECK(!one->alloc(p + page, page, MEM_COMMIT, PAGE_READWRITE));
    CHECK(darwin_mem_map(p + page, page, PROT_READ | PROT_WRITE) == p + page);
    puts("QUERY kernel_ranges=1 native_granules=1 guest_noaccess_committed=1 holes_not_committed=1 old_protection=real cross_image=1");
}

static unsigned char *shared_alias(void *addr, size_t size) {
    mach_vm_address_t alias = 0;
    vm_prot_t cur = 0, max = 0;
    CHECK(mach_vm_remap(mach_task_self(), &alias, size, 0, VM_FLAGS_ANYWHERE, mach_task_self(),
                        (mach_vm_address_t)addr, FALSE, &cur, &max, VM_INHERIT_NONE) == KERN_SUCCESS);
    CHECK(cur == (VM_PROT_READ | VM_PROT_WRITE));
    return (unsigned char *)(uintptr_t)alias;
}
static void pin_lifetime(uint64_t addr, size_t page, const MemoryImage *one, const MemoryImage *two) {
    unsigned char *p = GP(addr);
    memset(p, 0x46, 3 * page);
    CHECK(!hos_posix_guest_import_pin(addr, addr));
    CHECK(!hos_posix_guest_import_pin(HOS_GUEST_SPACE - page, HOS_GUEST_SPACE + page));
    CHECK(!hos_posix_guest_import_pin(addr + 1, addr + page));
    CHECK(hos_posix_guest_import_pin(addr, addr + 2 * page));
    CHECK(hos_posix_guest_import_pin(addr, addr + 2 * page));
    CHECK(hos_posix_guest_import_pin(addr + page, addr + 3 * page));
    unsigned char *alias = shared_alias(p, 3 * page);
    unsigned before = __atomic_load_n(&raw_discards, __ATOMIC_RELAXED);
    CHECK(one->alloc(p, 2 * page, MEM_COMMIT, PAGE_READWRITE) == p);
    expect_bytes(alias, 3 * page, 0x46);
    CHECK(two->release(p, page, MEM_DECOMMIT));
    expect_bytes(p, page, 0); expect_bytes(alias, page, 0);
    expect_bytes(alias + page, 2 * page, 0x46);
    CHECK(__atomic_load_n(&raw_discards, __ATOMIC_RELAXED) == before);
    errno = 0;
    hos_posix_guest_import_unpin(addr, addr + page); /* Not an acquired range. */
    CHECK(errno == EINVAL);
    hos_posix_guest_import_unpin(addr, addr + 2 * page); /* Duplicate still owns it. */
    memset(p, 0x67, 3 * page);
    CHECK(one->release(p, 2 * page, MEM_DECOMMIT));
    expect_bytes(alias, 2 * page, 0); expect_bytes(alias + 2 * page, page, 0x67);
    CHECK(__atomic_load_n(&raw_discards, __ATOMIC_RELAXED) == before);
    hos_posix_guest_import_unpin(addr, addr + 2 * page); /* Overlapping retired owner remains. */
    memset(p, 0x81, 3 * page);
    CHECK(two->release(p, 2 * page, MEM_DECOMMIT));
    expect_bytes(alias, 2 * page, 0); expect_bytes(alias + 2 * page, page, 0x81);
    CHECK(__atomic_load_n(&raw_discards, __ATOMIC_RELAXED) == before);
    hos_posix_guest_import_unpin(addr + page, addr + 3 * page);
    CHECK(!pins);
    memset(p, 0x92, 3 * page);
    CHECK(one->release(p, 2 * page, MEM_DECOMMIT));
    CHECK(__atomic_load_n(&raw_discards, __ATOMIC_RELAXED) == before + 1);
    expect_bytes(p, 2 * page, 0); expect_bytes(alias, 2 * page, 0x92);
    expect_bytes(p + 2 * page, page, 0x92);
    CHECK(!munmap(alias, 3 * page));
    puts("PIN_LIFETIME duplicate=1 overlap=1 retired_reference=1 full_padding=1 shared_alias_preserved=1 final_unpin_allows_remap=1");
}

typedef struct { uint64_t addr; size_t size; unsigned started, done; uint64_t result; int pages; } MemoryRace;
static void *race_region_free(void *raw) {
    MemoryRace *r = raw;
    if (r->pages) memory_call(imp__ZN2nn2os15FreeMemoryPagesEmm, r->addr, r->size, 0);
    else hos_region_free(r->addr);
    return NULL;
}
static void *race_region_alloc(void *raw) {
    MemoryRace *r = raw;
    __atomic_store_n(&r->started, 1, __ATOMIC_RELEASE);
    if (r->pages) {
        memory_call(imp__ZN2nn2os19AllocateMemoryPagesEmm, r->addr, r->size, 0);
        r->result = r->addr;
    } else r->result = hos_region_alloc(r->size, 4096);
    memset(GP(r->result), 0xbd, r->size);
    __atomic_store_n(&r->done, 1, __ATOMIC_RELEASE);
    return NULL;
}
static void concurrent_free(size_t page) {
    for (int kind = 0; kind < 2; kind++) {
        MemoryRace r = {.addr = hos_region_alloc(page, 4096), .size = page, .pages = kind};
        memset(GP(r.addr), 0x59, page);
        pause_discard(r.addr);
        pthread_t freeing, allocating;
        CHECK(!pthread_create(&freeing, NULL, race_region_free, &r));
        wait_discard();
        CHECK(!pthread_create(&allocating, NULL, race_region_alloc, &r));
        await_flag(&r.started);
        expect_locked(&lock);       /* Actual hos_mem allocation lock, not a sleep. */
        expect_locked(&memory_mu);  /* Actual canonical pin/remap lock. */
        CHECK(!__atomic_load_n(&r.done, __ATOMIC_ACQUIRE));
        resume_discard();
        CHECK(!pthread_join(freeing, NULL) && !pthread_join(allocating, NULL));
        CHECK(r.result == r.addr);
        expect_bytes(GP(r.result), page, 0xbd);
        hos_region_free(r.result);
    }
    puts("CONCURRENT_FREE region_and_page_apis=2 deterministic_latch=1 ownership_held_during_discard=1 reallocated_bytes_preserved=1");
}
static void *race_discard(void *raw) {
    MemoryRace *r = raw;
    CHECK(!hos_posix_guest_discard(GP(r->addr), r->size));
    return NULL;
}
static void *race_pin(void *raw) {
    MemoryRace *r = raw;
    __atomic_store_n(&r->started, 1, __ATOMIC_RELEASE);
    r->result = (uint64_t)hos_posix_guest_import_pin(r->addr, r->addr + r->size);
    __atomic_store_n(&r->done, 1, __ATOMIC_RELEASE);
    return NULL;
}
static void concurrent_pin(uint64_t addr, size_t page) {
    MemoryRace r = {.addr = addr, .size = page};
    memset(GP(addr), 0x72, page);
    pause_discard(addr);
    pthread_t freeing, importing;
    CHECK(!pthread_create(&freeing, NULL, race_discard, &r));
    wait_discard();
    CHECK(!pthread_create(&importing, NULL, race_pin, &r));
    await_flag(&r.started);
    expect_locked(&memory_mu);
    CHECK(!__atomic_load_n(&r.done, __ATOMIC_ACQUIRE));
    resume_discard();
    CHECK(!pthread_join(freeing, NULL) && !pthread_join(importing, NULL));
    CHECK(r.result == 1);
    unsigned char *alias = shared_alias(GP(addr), page);
    expect_bytes(alias, page, 0);
    const unsigned before = __atomic_load_n(&raw_discards, __ATOMIC_RELAXED);
    memset(GP(addr), 0x28, page);
    CHECK(!hos_posix_guest_discard(GP(addr), page));
    CHECK(__atomic_load_n(&raw_discards, __ATOMIC_RELAXED) == before);
    expect_bytes(alias, page, 0);
    hos_posix_guest_import_unpin(addr, addr + page);
    CHECK(!munmap(alias, page) && !pins);
    puts("CONCURRENT_PIN deterministic_latch=1 pin_waits_for_remap=1 imported_backing_is_new_generation=1 subsequent_free_keeps_backing=1");
}

static volatile sig_atomic_t memory_faults, expected_refusal;
static void memory_signal(int sig, siginfo_t *si, void *raw) {
    (void)sig;
    CONTEXT context;
    darwin_fault_context_from(&context, raw);
    EXCEPTION_RECORD record = {0};
    record.ExceptionCode = EXCEPTION_ACCESS_VIOLATION;
    record.ExceptionAddress = (void *)(uintptr_t)context.Rip;
    record.NumberParameters = 2;
    record.ExceptionInformation[0] = darwin_fault_access(raw);
    record.ExceptionInformation[1] = (ULONG_PTR)si->si_addr;
    EXCEPTION_POINTERS pointers = {&record, &context};
    if (wts_handler(&pointers) != EXCEPTION_CONTINUE_EXECUTION) _exit(expected_refusal ? 91 : 92);
    memory_faults++;
    darwin_fault_context_to(raw, &context);
}
static void install_memory_signal(void) {
    struct sigaction action = {0};
    action.sa_sigaction = memory_signal;
    darwin_fault_action(&action);
    CHECK(!sigaction(SIGSEGV, &action, NULL));
    CHECK(!sigaction(SIGBUS, &action, NULL));
}
static void clear_marks(uint64_t addr, size_t size) {
    for (uint64_t p = addr >> 12; p < (addr + size + 4095) >> 12; p++)
        __atomic_store_n(&a64_dirty[p], 0, __ATOMIC_RELAXED);
}
static void expect_refused_child(pid_t child) {
    int status = 0;
    CHECK(child > 0 && waitpid(child, &status, 0) == child);
    CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 91);
}
static void watch_pages(uint64_t addr, size_t page) {
    unsigned char *p = GP(addr), *watched = p + page;
    memset(p, 0x73, 3 * page);
    install_memory_signal();
    CHECK(!hos_posix_guest_watch(watched + (page > 4096 ? 4096 : 0) + 17, 1));
    const uint64_t lo = addr + page;
    uint64_t range[1][2] = {{lo, lo + page}};
    MEMORY_BASIC_INFORMATION info;
    CHECK(VirtualQuery(watched + 17, &info, sizeof info) == sizeof info);
    CHECK(info.BaseAddress == watched && info.RegionSize == page && info.Protect == PAGE_READONLY && info.State == MEM_COMMIT);
    for (size_t subpage = 0; subpage < page / 4096; subpage++) {
        CHECK(!hos_posix_guest_watch_rearm());
        clear_marks(addr, 3 * page);
        const sig_atomic_t before = memory_faults;
        *(volatile unsigned char *)(watched + subpage * 4096 + 31) = (unsigned char)(subpage + 1);
        CHECK(memory_faults == before + 1);
        for (size_t k = 0; k < page / 4096; k++) CHECK(__atomic_load_n(&a64_dirty[(lo >> 12) + k], __ATOMIC_ACQUIRE) == 1);
        CHECK(!__atomic_load_n(&a64_dirty[(addr >> 12)], __ATOMIC_RELAXED));
        CHECK(!__atomic_load_n(&a64_dirty[((addr + 2 * page) >> 12)], __ATOMIC_RELAXED));
        uint32_t *dirty = NULL;
        CHECK(collect_in(range, 1, &dirty) == (int64_t)(page / 4096));
        free(dirty);
        /* No second trap while RW: the submit collector still includes every
           native-page subpage, even when a host store has no A64_MARK. */
        *(volatile unsigned char *)(watched + ((subpage + 1) * 4096 - 1)) = 0x44;
        CHECK(host_collect_in(range, 1, &dirty) == (int64_t)(page / 4096));
        free(dirty);
    }
    expect_bytes(p, page, 0x73); expect_bytes(p + 2 * page, page, 0x73);
    CHECK(!hos_posix_guest_watch_rearm());
    pid_t child = fork(); CHECK(child >= 0);
    if (!child) {
        expected_refusal = 1;
        CHECK(mach_vm_protect(mach_task_self(), (mach_vm_address_t)watched, page, TRUE, VM_PROT_READ) == KERN_SUCCESS);
        *(volatile unsigned char *)watched = 0;
        _exit(93);
    }
    expect_refused_child(child);
    child = fork(); CHECK(child >= 0);
    if (!child) {
        expected_refusal = 1;
        CHECK(hos_posix_guest_watch_fault(watched) == 1);
        CHECK(mach_vm_protect(mach_task_self(), (mach_vm_address_t)watched, page, TRUE, VM_PROT_NONE) == KERN_SUCCESS);
        CHECK(hos_posix_guest_watch_rearm() == -1);
        uint32_t *dirty = NULL;
        CHECK(host_collect_in(range, 1, &dirty) == (int64_t)(page / 4096));
        free(dirty);
        *(volatile unsigned char *)watched = 0;
        _exit(93);
    }
    expect_refused_child(child);
    CHECK(hos_posix_guest_import_pin(lo, lo + page));
    const size_t cleared = page > 4096 ? 4096 : 2048;
    CHECK(!hos_posix_guest_discard(watched + 2048, cleared));
    expect_bytes(watched + 2048, cleared, 0);
    hos_posix_guest_import_unpin(lo, lo + page);
    DWORD old;
    CHECK(VirtualProtect(watched, page, PAGE_NOACCESS, &old));
    CHECK(hos_posix_guest_watch_fault(watched) == 0);
    CHECK(hos_posix_guest_watch(watched, page) == -1);
    CHECK(VirtualProtect(watched, page, PAGE_READWRITE, &old));
    CHECK(hos_posix_guest_watch(watched, page) == -1); /* A cancelled duplicate is not success. */
    printf("WATCH host_page=%zu subpages=%zu real_faults=%d dirty_granule=4096 neighbor_bytes=preserved restore_error=refused rearm_error=refused\n",
           page, page / 4096, (int)memory_faults);
}

typedef struct { uint64_t addr; unsigned posted, acknowledged; } DirtyProducer;
static void *dirty_producer(void *raw) {
    DirtyProducer *p = raw;
    for (unsigned round = 1; round <= 200; round++) {
        while (__atomic_load_n(&p->acknowledged, __ATOMIC_ACQUIRE) != round - 1) sched_yield();
        __atomic_store_n((uint32_t *)GP(p->addr), round, __ATOMIC_RELAXED);
        A64_MARK(p->addr, sizeof(uint32_t));
        __atomic_store_n(&p->posted, round, __ATOMIC_RELEASE);
    }
    return NULL;
}
static void concurrent_dirty(uint64_t addr) {
    DirtyProducer producers[2] = {{.addr = addr + 255 * 4096}, {.addr = addr + 256 * 4096}};
    pthread_t threads[2];
    clear_marks(addr, 512 * 4096);
    for (int i = 0; i < 2; i++) CHECK(!pthread_create(&threads[i], NULL, dirty_producer, &producers[i]));
    uint64_t ranges[1][2] = {{addr, addr + 512 * 4096}};
    unsigned collected = 0;
    const int64_t until = hos_now_ns() + INT64_C(10000000000);
    while (collected < 400) {
        uint32_t *dirty = NULL;
        const int64_t n = collect_in(ranges, 1, &dirty);
        for (int64_t k = 0; k < n; k++) {
            int i = dirty[k] == (producers[0].addr >> 12) ? 0 : 1;
            CHECK(dirty[k] == (producers[i].addr >> 12));
            const unsigned value = __atomic_load_n((uint32_t *)GP(producers[i].addr), __ATOMIC_RELAXED);
            while (__atomic_load_n(&producers[i].posted, __ATOMIC_ACQUIRE) < value) sched_yield();
            const unsigned seen = __atomic_load_n(&producers[i].acknowledged, __ATOMIC_RELAXED);
            CHECK(value >= seen && value <= seen + 1);
            /* A64_MARK may publish the same page twice (first and last byte).
               Duplicate reports are legal; every published value must arrive. */
            if (value > seen) {
                __atomic_store_n(&producers[i].acknowledged, value, __ATOMIC_RELEASE);
                collected++;
            }
        }
        free(dirty);
        CHECK(hos_now_ns() < until);
    }
    for (int i = 0; i < 2; i++) CHECK(!pthread_join(threads[i], NULL));
    clear_marks(HOS_GUEST_SPACE - 4096, 4096);
    hos_mark_written(GP(HOS_GUEST_SPACE - 1), SIZE_MAX);
    CHECK(__atomic_load_n(&a64_dirty[A64_DIRTY_PAGES - 1], __ATOMIC_ACQUIRE) == 1);
    uint64_t empty[1][2] = {{addr + 1, addr + 1}};
    __atomic_store_n(&a64_dirty[addr >> 12], 1, __ATOMIC_RELEASE);
    uint32_t *dirty = NULL;
    CHECK(collect_in(empty, 1, &dirty) == 0);
    free(dirty);
    CHECK(__atomic_load_n(&a64_dirty[addr >> 12], __ATOMIC_ACQUIRE) == 1);
    printf("DIRTY concurrent_published=400 acknowledged=%u wide_boundary=255/256 empty_range_preserves=1 overflow_clamped=1\n", collected);
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    const size_t page = (size_t)getpagesize();
    CHECK(page == 4096 || page == 16384);
    CHECK(argc >= 3);
    const MemoryImage one = memory_image(argv[1]), two = memory_image(argv[2]);
    CHECK(one.alloc != two.alloc && one.release != two.release && one.protect != two.protect && one.query != two.query);
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
    adjacent_pages(0x200000, 32, 1);
    byte_ranges(addr + 0x400000, page);
    host_allocations(page);
    region_lifecycle();
    query_ranges(addr + 0x1800000, page, &one, &two);
    pin_lifetime(addr + 0x2000000, page, &one, &two);
    concurrent_free(page);
    concurrent_pin(addr + 0x2800000, page);
    watch_pages(addr + 0x3000000, page);
    concurrent_dirty(addr + 0x4000000);
    failure_paths(addr + 0x800000, page);
    if (argc < 4 || strcmp(argv[3], "--behavior-only")) reclaim_large(addr + 0x10000000, page);
    else puts("RECLAIM carried_previous_RSS_proof=1 experiment_not_repeated=1");
    CHECK(import_checks == 0 && !pins);
    CHECK(!dlclose(one.image) && !dlclose(two.image));
    CHECK(!munmap(A64_MEM, HOS_GUEST_SPACE));
    puts("PASS: reservation, adjacent guest pages, real queries, watches, dirty collection and canonical import/free lifetime");
    return 0;
}
