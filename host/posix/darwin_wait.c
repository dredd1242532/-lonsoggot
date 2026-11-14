/* Public Darwin primitives, macOS 14.4+. GPL-3.0-or-later.
   Contracts: os/os_sync_wait_on_address.h, pthread_cond_timedwait(3),
   clock_gettime(3), mach/mach_time.h. No private ulock entry points. */
#if defined(__APPLE__)
#include "darwin_wait.h"
#include <dlfcn.h>
#include <errno.h>
#include <mach/mach_time.h>
#include <os/os_sync_wait_on_address.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static pthread_once_t timebase_once = PTHREAD_ONCE_INIT;
static mach_timebase_info_data_t timebase;
static void init_timebase(void) { mach_timebase_info(&timebase); }
static uint64_t ticks_for_ns(uint64_t ns) {
    pthread_once(&timebase_once, init_timebase);
    /* Round up: a deadline must not become earlier when the timebase is 125/3. */
    const __uint128_t ticks = ((__uint128_t)ns * timebase.denom + timebase.numer - 1) / timebase.numer;
    return ticks > UINT64_MAX ? UINT64_MAX : (uint64_t)ticks;
}
int64_t darwin_now_ns(void) { return (int64_t)clock_gettime_nsec_np(CLOCK_UPTIME_RAW); }

void darwin_monitor_init(pthread_mutex_t *mu, pthread_cond_t *cv) {
    pthread_mutex_init(mu, NULL);
    pthread_cond_init(cv, NULL);
}
int darwin_cond_timedwait(pthread_cond_t *cv, pthread_mutex_t *mu, const struct timespec *until) {
    const int64_t left = (int64_t)until->tv_sec * 1000000000LL + until->tv_nsec - darwin_now_ns();
    if (left <= 0) return ETIMEDOUT;   /* caller still owns mu and rechecks its predicate */
    const struct timespec relative = {(time_t)(left / 1000000000LL), (long)(left % 1000000000LL)};
    return pthread_cond_timedwait_relative_np(cv, mu, &relative);
}
int darwin_wait_until_ns(int64_t until) {
    if (until <= 0) return 0;
    const uint64_t deadline = ticks_for_ns((uint64_t)until);
    kern_return_t r;
    do { r = mach_wait_until(deadline); } while (r == KERN_ABORTED);
    if (r == KERN_SUCCESS) return 0;
    errno = EINVAL;
    return -1;
}

int darwin_wait_word(volatile uint32_t *word, uint32_t seen, uint32_t ms) {
    if (__atomic_load_n(word, __ATOMIC_SEQ_CST) != seen) return 0;
    if (!ms) { errno = ETIMEDOUT; return -1; }
    if (ms == UINT32_MAX)
        return os_sync_wait_on_address((void *)word, seen, sizeof *word, OS_SYNC_WAIT_ON_ADDRESS_NONE);
    const uint64_t now = mach_absolute_time(), ticks = ticks_for_ns((uint64_t)ms * 1000000);
    const uint64_t deadline = ticks > UINT64_MAX - now ? UINT64_MAX : now + ticks;
    return os_sync_wait_on_address_with_deadline((void *)word, seen, sizeof *word, OS_SYNC_WAIT_ON_ADDRESS_NONE,
                                                 OS_CLOCK_MACH_ABSOLUTE_TIME, deadline);
}
void darwin_wake_word(volatile uint32_t *word, int all) {
    if (all) os_sync_wake_by_address_all((void *)word, sizeof *word, OS_SYNC_WAKE_BY_ADDRESS_NONE);
    else os_sync_wake_by_address_any((void *)word, sizeof *word, OS_SYNC_WAKE_BY_ADDRESS_NONE);
}

/* WaitOnAddress has no size argument on its wake side. A sequence bucket avoids
   mixing 4/8-byte kernel wait keys and covers subwords and cross-word comparisons.
   Snapshot before comparing; increment before waking. In particular an upper-half
   change in an 8-byte value cannot lose its wake just before kernel entry.
   Collisions are permitted spurious wakes. Both Win32 wake names broadcast here,
   as on Linux; waking only one could strand a changed subword's other waiter. */
static pthread_once_t address_sequences_once = PTHREAD_ONCE_INIT;
static uint32_t *address_sequences;
static void init_address_sequences(void) {
    /* Each image has its own shim, but only the executable owns the wait keys.
       RTLD_DEFAULT could select a renderer's accidental private provider. */
    typedef uint32_t *(*SequencesFn)(void);
    SequencesFn provider = (SequencesFn)dlsym(RTLD_MAIN_ONLY, "hos_posix_address_wait_sequences");
    if (!provider || !(address_sequences = provider())) {
        fputs("fatal: main executable has no Darwin address-wait state provider\n", stderr);
        abort();
    }
}
static uint32_t *address_sequence(const volatile void *addr) {
    pthread_once(&address_sequences_once, init_address_sequences);
    const uintptr_t word = (uintptr_t)addr >> 2;
    return &address_sequences[(word ^ (word >> 8)) & (DARWIN_ADDRESS_WAIT_SEQUENCE_COUNT - 1)];
}
int darwin_wait_address(volatile void *addr, const void *compare, size_t size, uint32_t ms) {
    uint32_t *seq = address_sequence(addr);
    const uint32_t seen = __atomic_load_n(seq, __ATOMIC_SEQ_CST);
    if (memcmp((const void *)addr, compare, size)) return 0;
    return darwin_wait_word(seq, seen, ms);
}
void darwin_wake_address(void *addr) {
    uint32_t *seq = address_sequence(addr);
    __atomic_add_fetch(seq, 1, __ATOMIC_SEQ_CST);
    darwin_wake_word(seq, 1);
}

void darwin_srw_init(DarwinSRWLock *lock) {
    /* Explicit initialization, like InitializeSRWLock, precedes publication. */
    if (pthread_rwlock_init(&lock->lock, NULL)) abort();
    __atomic_store_n(&lock->initialized, 2, __ATOMIC_RELEASE);
}
static pthread_rwlock_t *srw_native(DarwinSRWLock *lock, int wait) {
    if (__atomic_load_n(&lock->initialized, __ATOMIC_ACQUIRE) != 2) {
        uint32_t zero = 0;
        if (__atomic_compare_exchange_n(&lock->initialized, &zero, 1, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
            darwin_srw_init(lock);
            darwin_wake_word(&lock->initialized, 1);
        } else {
            if (!wait && zero != 2) return NULL;   /* TryAcquire must not wait for another initializer. */
            while (__atomic_load_n(&lock->initialized, __ATOMIC_ACQUIRE) != 2)
                darwin_wait_word(&lock->initialized, 1, UINT32_MAX);
        }
    }
    return &lock->lock;
}
pthread_rwlock_t *darwin_srw_native(DarwinSRWLock *lock) { return srw_native(lock, 1); }
int darwin_srw_trylock(DarwinSRWLock *lock, int shared) {
    pthread_rwlock_t *native = srw_native(lock, 0);
    return native && (shared ? pthread_rwlock_tryrdlock(native) : pthread_rwlock_trywrlock(native)) == 0;
}
#endif
