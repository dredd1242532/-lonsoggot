/* macOS 14.4+ wait primitives for the POSIX Win32 layer. GPL-3.0-or-later. */
#ifndef LONGSHOT_DARWIN_WAIT_H
#define LONGSHOT_DARWIN_WAIT_H
#if defined(__APPLE__)
#include <pthread.h>
#include <stddef.h>
#include <stdint.h>
#include <time.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Unlike a pthread rwlock, zeroed Win32 SRW storage must be usable. The once word
   publishes a real pthread initialization before any reader or writer enters. */
typedef struct { uint32_t initialized; pthread_rwlock_t lock; } DarwinSRWLock;
#define DARWIN_SRWLOCK_INIT {2, PTHREAD_RWLOCK_INITIALIZER}
void darwin_srw_init(DarwinSRWLock *lock);
pthread_rwlock_t *darwin_srw_native(DarwinSRWLock *lock);
int darwin_srw_trylock(DarwinSRWLock *lock, int shared);

/* All elapsed time is awake uptime in ns (CLOCK_UPTIME_RAW / mach_absolute_time),
   not Darwin CLOCK_MONOTONIC, which counts system sleep. FILETIME stays realtime. */
int64_t darwin_now_ns(void);
void darwin_monitor_init(pthread_mutex_t *mu, pthread_cond_t *cv);
int darwin_cond_timedwait(pthread_cond_t *cv, pthread_mutex_t *mu, const struct timespec *until);
int darwin_wait_until_ns(int64_t until);

/* One wait attempt: callers recheck predicates after spurious/error returns.
   UINT32_MAX is infinite; zero is a comparison/poll, never the OS's invalid zero deadline. */
int darwin_wait_word(volatile uint32_t *word, uint32_t seen, uint32_t ms);
void darwin_wake_word(volatile uint32_t *word, int all);

/* Defined only in the main executable; both image-local helpers resolve this
   export once. Its zero-initialized sequence words live for the whole process. */
#define DARWIN_ADDRESS_WAIT_SEQUENCE_COUNT 256
uint32_t *hos_posix_address_wait_sequences(void);

/* A missing executable provider is fatal, never an image-private fallback. */
int darwin_wait_address(volatile void *addr, const void *compare, size_t size, uint32_t ms);
void darwin_wake_address(void *addr);

#ifdef __cplusplus
}
#endif
#endif
#endif
