/* Canonical Darwin guest backing and diagnostic watches. Link into the host
   executable ONLY; darwin_mem.c and the Win32 shim may also live in dylibs.
   Guest ownership remains 4 KiB; these watch states describe native host pages.
   No renderer callback is made while holding the ownership mutex. */
#if defined(__APPLE__)
#include "darwin_mem.h"
#include <errno.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

static pthread_mutex_t memory_mu = PTHREAD_MUTEX_INITIALIZER;
static uintptr_t guest_base, guest_end;
static size_t host_page;
static int ready;

typedef struct ImportPin {
    uint64_t lo, hi;
    size_t refs;
    struct ImportPin *next;
} ImportPin;
static ImportPin *pins;

/* A lazy byte per native page costs address space, not a resident 512 GiB
   ledger. Touched watch states never move or disappear, including in a signal.
   BUSY is held only over protection syscalls/metadata, NEVER a guest access or
   an allocation: a faulting thread cannot interrupt itself holding this lock. */
enum { WATCH_OFF, WATCH_RW, WATCH_RO, WATCH_BUSY };
static unsigned char *watch_state;
static struct { uintptr_t lo, hi; } watches[16];
static unsigned watch_count;

static void mark_written(uintptr_t lo, size_t size) {
#if defined(A64_WRITE_TRACK)
    extern void hos_mark_written(const void *, size_t);
    hos_mark_written((void *)lo, size);
#else
    (void)lo, (void)size;
#endif
}

static int guest_range(const void *addr, size_t size) {
    const uintptr_t lo = (uintptr_t)addr;
    if (!__atomic_load_n(&ready, __ATOMIC_ACQUIRE) || lo < guest_base || lo >= guest_end || size > guest_end - lo) {
        errno = EINVAL;
        return 0;
    }
    return 1;
}

static unsigned watch_take(size_t page) {
    for (;;) {
        unsigned char old = __atomic_load_n(&watch_state[page], __ATOMIC_ACQUIRE);
        if (old != WATCH_BUSY && __atomic_compare_exchange_n(&watch_state[page], &old, WATCH_BUSY, 0,
                                                            __ATOMIC_ACQUIRE, __ATOMIC_RELAXED)) return old;
    }
}
static void watch_put(size_t page, unsigned state) {
    __atomic_store_n(&watch_state[page], (unsigned char)state, __ATOMIC_RELEASE);
}

/* Checked under memory_mu. Faults may only restore a known watch's RO -> RW;
   they never remove backing or relax an unrelated/explicit protection. */
static int covered(uintptr_t lo, uintptr_t hi, int writable) {
    while (lo < hi) {
        DarwinMemInfo info;
        if (darwin_mem_query((void *)lo, &info)) return -1;
        if (!info.mapped || info.end <= lo) { errno = EINVAL; return -1; }
        const uintptr_t end = info.end < hi ? info.end : hi;
        if (writable && (info.protect & (PROT_READ | PROT_WRITE)) != (PROT_READ | PROT_WRITE)) {
            if (info.protect != PROT_READ) { errno = EACCES; return -1; }
            for (uintptr_t p = lo & ~(host_page - 1); p < end; p += host_page) {
                const size_t page = (p - guest_base) / host_page;
                const unsigned state = watch_take(page);
                watch_put(page, state);
                if (state != WATCH_RO && state != WATCH_RW) { errno = EACCES; return -1; }
            }
        }
        lo = end;
    }
    return 0;
}

void *hos_posix_guest_reserve(void *addr, size_t size) {
    pthread_mutex_lock(&memory_mu);
    if (ready) { pthread_mutex_unlock(&memory_mu); errno = EEXIST; return MAP_FAILED; }
    const size_t page = (size_t)getpagesize();
    if (!addr || !size || (page != 4096 && page != 16384) || ((uintptr_t)addr & (page - 1)) ||
        (size & (page - 1)) || size > UINTPTR_MAX - (uintptr_t)addr) {
        pthread_mutex_unlock(&memory_mu); errno = EINVAL; return MAP_FAILED;
    }
    unsigned char *states = mmap(NULL, size / page, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (states == MAP_FAILED) { pthread_mutex_unlock(&memory_mu); return MAP_FAILED; }
    void *p = darwin_mem_map(addr, size, PROT_READ | PROT_WRITE);
    if (p == MAP_FAILED) {
        const int err = errno;
        munmap(states, size / page);
        pthread_mutex_unlock(&memory_mu); errno = err; return MAP_FAILED;
    }
    guest_base = (uintptr_t)p;
    guest_end = guest_base + size;
    host_page = page;
    watch_state = states;
    __atomic_store_n(&ready, 1, __ATOMIC_RELEASE);
    pthread_mutex_unlock(&memory_mu);
    return p;
}

int hos_posix_guest_commit(void *addr, size_t size) {
    pthread_mutex_lock(&memory_mu);
    const int result = guest_range(addr, size) ? covered((uintptr_t)addr, (uintptr_t)addr + size, 0) : -1;
    /* Already committed, demand-paged backing: just as on Windows, committing
       again must not clear bytes or change existing protections (including watches). */
    pthread_mutex_unlock(&memory_mu);
    return result;
}

int hos_posix_guest_import_pin(uint64_t lo, uint64_t hi) {
    pthread_mutex_lock(&memory_mu);
    if (!ready || lo >= hi || hi > guest_end - guest_base || ((lo | hi) & (host_page - 1)) ||
        covered(guest_base + lo, guest_base + hi, 0)) {
        pthread_mutex_unlock(&memory_mu); errno = EINVAL; return 0;
    }
    for (ImportPin *p = pins; p; p = p->next) {
        if (p->lo != lo || p->hi != hi) continue;
        if (p->refs == SIZE_MAX) { pthread_mutex_unlock(&memory_mu); errno = EOVERFLOW; return 0; }
        p->refs++;
        pthread_mutex_unlock(&memory_mu);
        return 1;
    }
    ImportPin *p = malloc(sizeof *p);
    if (!p) { pthread_mutex_unlock(&memory_mu); errno = ENOMEM; return 0; }
    *p = (ImportPin){lo, hi, 1, pins};
    pins = p;
    pthread_mutex_unlock(&memory_mu);
    return 1;
}

void hos_posix_guest_import_unpin(uint64_t lo, uint64_t hi) {
    pthread_mutex_lock(&memory_mu);
    for (ImportPin **at = &pins; *at; at = &(*at)->next) {
        ImportPin *p = *at;
        if (p->lo != lo || p->hi != hi) continue;
        if (!--p->refs) { *at = p->next; free(p); }
        pthread_mutex_unlock(&memory_mu);
        return;
    }
    /* A mismatched release must never release somebody else's backing. */
    pthread_mutex_unlock(&memory_mu);
    errno = EINVAL;
}

static int unwatch_for_write(uintptr_t lo, uintptr_t hi) {
    const unsigned n = __atomic_load_n(&watch_count, __ATOMIC_ACQUIRE);
    for (unsigned i = 0; i < n; i++) {
        const uintptr_t begin = lo > watches[i].lo ? lo : watches[i].lo;
        const uintptr_t end = hi < watches[i].hi ? hi : watches[i].hi;
        for (uintptr_t p = begin; p < end; p += host_page) {
            const size_t page = (p - guest_base) / host_page;
            const unsigned state = watch_take(page);
            if (state == WATCH_RO && darwin_mem_protect((void *)p, host_page, PROT_READ | PROT_WRITE)) {
                watch_put(page, state);
                return -1;
            }
            watch_put(page, state == WATCH_RO ? WATCH_RW : state);
        }
    }
    return 0;
}

int hos_posix_guest_discard(void *addr, size_t size) {
    if (!size) return 0;
    pthread_mutex_lock(&memory_mu);
    const uintptr_t lo = (uintptr_t)addr, hi = lo + size;
    if (!guest_range(addr, size) || covered(lo, hi, 1) ||
        unwatch_for_write(lo & ~(host_page - 1), (hi + host_page - 1) & ~(host_page - 1))) {
        pthread_mutex_unlock(&memory_mu);
        return -1;
    }
    int imported = 0;
    for (const ImportPin *p = pins; p; p = p->next)
        if (lo - guest_base < p->hi && hi - guest_base > p->lo) { imported = 1; break; }
    /* Pin lookup and replacement are one transaction. A pin cannot arrive after
       the lookup but before remapping; a retired import remains in this ledger.
       Zeroing pinned bytes is distinct from replacing their memory object. */
    const int result = imported ? (memset(addr, 0, size), 0) : darwin_mem_discard(addr, size);
    if (!result) mark_written(lo, size);
    pthread_mutex_unlock(&memory_mu);
    return result;
}

int hos_posix_guest_protect(void *addr, size_t size, int protect, int *old) {
    pthread_mutex_lock(&memory_mu);
    if (!size || !guest_range(addr, size) || covered((uintptr_t)addr, (uintptr_t)addr + size, 0)) {
        pthread_mutex_unlock(&memory_mu); errno = EINVAL; return -1;
    }
    DarwinMemInfo first;
    if (darwin_mem_query(addr, &first)) { pthread_mutex_unlock(&memory_mu); return -1; }
    uintptr_t lo = (uintptr_t)addr & ~(host_page - 1);
    const uintptr_t hi = ((uintptr_t)addr + size + host_page - 1) & ~(host_page - 1);
    const unsigned n = __atomic_load_n(&watch_count, __ATOMIC_ACQUIRE);
    while (lo < hi) {
        uintptr_t next = hi;
        int watched = 0;
        for (unsigned i = 0; i < n; i++) {
            if (lo >= watches[i].lo && lo < watches[i].hi) { watched = 1; break; }
            if (lo < watches[i].lo && watches[i].lo < next) next = watches[i].lo;
        }
        const size_t page = (lo - guest_base) / host_page;
        const unsigned state = watched ? watch_take(page) : WATCH_OFF;
        if (watched) next = lo + host_page;
        if (darwin_mem_protect((void *)lo, next - lo, protect)) {
            if (watched) watch_put(page, state);
            pthread_mutex_unlock(&memory_mu);
            return -1;
        }
        /* Explicit protection supersedes a diagnostic watch. Never turn an
           intentional NOACCESS/RO page writable in response to a stale watch. */
        if (watched) watch_put(page, WATCH_OFF);
        lo = next;
    }
    if (old) *old = first.protect;
    pthread_mutex_unlock(&memory_mu);
    return 0;
}

int hos_posix_guest_watch(const void *addr, size_t size) {
    pthread_mutex_lock(&memory_mu);
    if (!size || !guest_range(addr, size)) { pthread_mutex_unlock(&memory_mu); errno = EINVAL; return -1; }
    const uintptr_t lo = (uintptr_t)addr & ~(host_page - 1);
    const uintptr_t hi = ((uintptr_t)addr + size + host_page - 1) & ~(host_page - 1);
    if (covered(lo, hi, 1)) { pthread_mutex_unlock(&memory_mu); return -1; }
    unsigned n = __atomic_load_n(&watch_count, __ATOMIC_ACQUIRE);
    for (unsigned i = 0; i < n; i++) {
        if (watches[i].lo != lo || watches[i].hi != hi) continue;
        int failed = 0;
        for (uintptr_t p = lo; p < hi; p += host_page) {
            const size_t page = (p - guest_base) / host_page;
            const unsigned state = watch_take(page);
            watch_put(page, state);
            if (state == WATCH_OFF) failed = 1;
        }
        pthread_mutex_unlock(&memory_mu);
        if (failed) { errno = EACCES; return -1; }
        return 0;
    }
    if (n == 16) { pthread_mutex_unlock(&memory_mu); errno = ENOSPC; return -1; }
    watches[n].lo = lo, watches[n].hi = hi;
    /* Publish before any protection changes: every affected native-page
       subpage is recognized even during registration or a partial failure. */
    __atomic_store_n(&watch_count, n + 1, __ATOMIC_RELEASE);
    int err = 0;
    for (uintptr_t p = lo; p < hi; p += host_page) {
        const size_t page = (p - guest_base) / host_page;
        const unsigned state = watch_take(page);
        if (state == WATCH_RO) { watch_put(page, state); continue; }
        if (darwin_mem_protect((void *)p, host_page, PROT_READ)) {
            if (!err) err = errno;
            watch_put(page, WATCH_OFF);
        } else watch_put(page, WATCH_RO);
    }
    mark_written(lo, hi - lo);
    pthread_mutex_unlock(&memory_mu);
    if (err) { errno = err; return -1; }
    return 0;
}

int hos_posix_guest_watch_fault(const void *addr) {
    if (!guest_range(addr, 1)) return 0;
    const uintptr_t lo = (uintptr_t)addr & ~(host_page - 1);
    const size_t page = (lo - guest_base) / host_page;
    /* No pthread/dlsym/malloc/log calls here. See WATCH_BUSY's invariant above. */
    const unsigned state = watch_take(page);
    if (state == WATCH_OFF) { watch_put(page, state); return 0; }
    if (darwin_mem_protect((void *)lo, host_page, PROT_READ | PROT_WRITE)) {
        watch_put(page, state);
        return -1;
    }
    mark_written(lo, host_page);
    watch_put(page, WATCH_RW);
    return 1;
}

int hos_posix_guest_watch_rearm(void) {
    pthread_mutex_lock(&memory_mu);
    const unsigned n = __atomic_load_n(&watch_count, __ATOMIC_ACQUIRE);
    int err = 0;
    for (unsigned i = 0; i < n; i++) {
        for (uintptr_t p = watches[i].lo; p < watches[i].hi; p += host_page) {
            const size_t page = (p - guest_base) / host_page;
            const unsigned state = watch_take(page);
            if (state != WATCH_RW) { watch_put(page, state); continue; }
            if (darwin_mem_protect((void *)p, host_page, PROT_READ)) {
                if (!err) err = errno;
                watch_put(page, WATCH_OFF);
            } else watch_put(page, WATCH_RO);
        }
    }
    pthread_mutex_unlock(&memory_mu);
    if (err) { errno = err; return -1; }
    return 0;
}

void hos_posix_guest_watch_mark(void) {
    const unsigned n = __atomic_load_n(&watch_count, __ATOMIC_ACQUIRE);
    /* A fault precedes the retried store. Keep the full native page visible to
       every collection, even if collection races that store or rearming fails.
       These are optional debug watches, not a replacement for A64's 4 KiB marks. */
    for (unsigned i = 0; i < n; i++) mark_written(watches[i].lo, watches[i].hi - watches[i].lo);
}
#endif
