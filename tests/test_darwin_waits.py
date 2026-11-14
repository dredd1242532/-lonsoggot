"""Darwin waits from the real Win32 layer, without a game, device or window.

The fixture cuts the synchronization functions out of win32_posix.c and links
darwin_wait.c and the executable-only darwin_wait_state.c, with the real windows.h.
All locks, wakes, waits and clocks are real primitives. The out-of-scope fiber-only
ucontext include and pseudo-thread lookup are omitted; ordinary HThread handles
exercise the real completion monitor. A second fixture loads two distinct dylibs
with image-local shims and the real canonical host provider.

python -I -B tests/test_darwin_waits.py [--arch arm64 --arch x86_64]
    [--source-tree PATH] [--out NEW_DIRECTORY] [--cc clang] [--cxx clang++] [--shared-only]

Default: the running Mac's architecture. x86_64 on Apple Silicon needs Rosetta.
The deployment target is 14.4. Other platforms do not execute Darwin fixtures.
Tolerances are fixed before measurements: 10 ms waits allow 0.1 ms early and
50 ms late; zero-time polls allow 50 ms scheduling overhead. Every sample and
outlier is printed, not discarded. Four workers at most, with 5 s progress
watchdogs and a 90 s process limit. Wake cardinality's 2 ms settle/observation
windows are repeated observations, not a proof of every scheduler interleaving.
--shared-only runs the cross-image behavior and C++ header fixtures without the
elapsed-time campaign. It checks queued and entry-race wakes, all widths and both
broadcast wrappers; a provider outside the main executable must fail closed.
"""
import argparse
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import platform
import shlex
import subprocess
import sys
import tempfile

RUN_ALL_ARGS = ["--cc", "clang", "--cxx", "{cxx}"]
RUN_ALL_SKIP = {"windows": "Darwin 14.4+ public address waits and pthread extensions"}

ROOT = Path(__file__).resolve().parents[1]


def fixture(root, harness=None):
    source = (root / "host/posix/win32_posix.c").read_text(encoding="utf-8")
    # Retain preprocessor branches, not a rewritten copy of the implementation.
    prefix = source[:source.index("/* ---- threads ---- */")]
    # Fibers are deliberately not extracted; their deprecated header rejects Darwin
    # without feature macros, independently of whether this wait group compiles.
    prefix = prefix.replace("#include <ucontext.h>\n", "")
    # The thread record is the first typedef in the threads section: tagged (struct HThread, for
    # Darwin's thread registry links) or untagged. An empty cut must stop the check, not compile.
    threads = source.index("/* ---- threads ---- */")
    thread = source[source.index("typedef struct", threads):source.index("static __thread HThread *self_thread;")]
    if "} HThread;" not in thread:
        raise SystemExit("FAIL: win32_posix.c's HThread definition was not found in its threads section")
    events = source[source.index("/* ---- events and semaphores ---- */"):
                    source.index("/* ---- fibers", source.index("/* ---- events and semaphores ---- */"))]
    lookup = "static HThread *thread_of(HANDLE h) { return is(h, H_THREAD) ? (HThread *)h : NULL; }\n"
    return prefix + thread + lookup + events + (HARNESS if harness is None else harness)


HARNESS = r'''
#include <inttypes.h>
#include <mach/mach_time.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line=%d: %s errno=%d last_error=%u\n", \
    __LINE__, #x, errno, GetLastError()); exit(1); } } while (0)
#define LOAD(p) __atomic_load_n((p), __ATOMIC_SEQ_CST)
#define STORE(p, v) __atomic_store_n((p), (v), __ATOMIC_SEQ_CST)
#define ADD(p, v) __atomic_add_fetch((p), (v), __ATOMIC_SEQ_CST)
enum { WORKERS = 4, ROUNDS = 10000, CARDINALITY = 50, TIMINGS = 50 };
static int64_t observed_ns(void) { return (int64_t)clock_gettime_nsec_np(CLOCK_UPTIME_RAW); }
static void pause_ns(long ns) {
    struct timespec t = {ns / 1000000000L, ns % 1000000000L};
    while (nanosleep(&t, &t) && errno == EINTR) {}
}
static void await_count(volatile uint32_t *p, uint32_t wanted) {
    const int64_t limit = observed_ns() + 5000000000LL;
    while (LOAD(p) != wanted) { CHECK(observed_ns() < limit); pause_ns(100000); }
}
static void quiet_count(volatile uint32_t *p, uint32_t wanted) {
    const int64_t until = observed_ns() + 2000000;
    do { CHECK(LOAD(p) == wanted); pause_ns(100000); } while (observed_ns() < until);
}
static void join_all(pthread_t *threads) {
    for (int i = 0; i < WORKERS; i++) CHECK(!pthread_join(threads[i], NULL));
}
static void close_event(HANDLE h) {
    HEvent *e = h;
    CHECK(!pthread_cond_destroy(&e->cv));
    CHECK(!pthread_mutex_destroy(&e->mu));
    free(e);
}
static int order_double(const void *a, const void *b) {
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}
static void stats(const char *name, double *samples, int n, double target) {
    int early = 0, late = 0;
    for (int i = 0; i < n; i++) {
        int e = samples[i] < target - 0.1, l = samples[i] > target + 50.0;
        early += e; late += l;
        printf("SAMPLE %s round=%d elapsed_ms=%.6f early=%d late=%d\n", name, i, samples[i], e, l);
    }
    qsort(samples, (size_t)n, sizeof *samples, order_double);
    const double median = n % 2 ? samples[n / 2] : (samples[n / 2 - 1] + samples[n / 2]) / 2;
    printf("TIMING %s rounds=%d target_ms=%.3f early_tolerance_ms=0.100 late_tolerance_ms=50.000 "
           "min_ms=%.6f median_ms=%.6f p99_ms=%.6f max_ms=%.6f early=%d late=%d\n",
           name, n, target, samples[0], median, samples[(n * 99) / 100], samples[n - 1], early, late);
    CHECK(!early && !late);
}
static DWORD remaining_ms(int64_t until) {
    const int64_t left = until - observed_ns();
    return left <= 0 ? 0 : (DWORD)((left + 999999) / 1000000);
}

static void clocks_and_polls(void) {
    LARGE_INTEGER f, counter;
    CHECK(QueryPerformanceFrequency(&f) && f.QuadPart == 1000000000LL);
    for (int i = 0; i < 10000; i++) {
        const int64_t before = observed_ns();
        CHECK(QueryPerformanceCounter(&counter));
        CHECK(counter.QuadPart >= before && counter.QuadPart <= observed_ns());
        const uint64_t first = (uint64_t)observed_ns() / 1000000;
        const uint64_t tick = GetTickCount64();
        CHECK(tick >= first && tick <= (uint64_t)observed_ns() / 1000000);
    }
    struct timespec before, after;
    FILETIME ft;
    CHECK(!clock_gettime(CLOCK_REALTIME, &before));
    GetSystemTimeAsFileTime(&ft);
    CHECK(!clock_gettime(CLOCK_REALTIME, &after));
    const uint64_t ticks = (uint64_t)ft.dwHighDateTime << 32 | ft.dwLowDateTime;
    const uint64_t lo = ((uint64_t)before.tv_sec + 11644473600ULL) * 10000000 + before.tv_nsec / 100;
    const uint64_t hi = ((uint64_t)after.tv_sec + 11644473600ULL) * 10000000 + after.tv_nsec / 100;
    CHECK(ticks >= lo && ticks <= hi);
    _Alignas(8) unsigned char data[24] = {0}, compare[8] = {0};
    const int widths[] = {1, 2, 4, 8}, offsets[] = {3, 3, 2, 1};
    const int64_t start = observed_ns();
    for (int i = 0; i < 4; i++) {
        CHECK(!WaitOnAddress(data + offsets[i], compare, (SIZE_T)widths[i], 0));
        CHECK(GetLastError() == ERROR_TIMEOUT);
        data[offsets[i] + widths[i] - 1] = 1;
        CHECK(WaitOnAddress(data + offsets[i], compare, (SIZE_T)widths[i], 0));
        data[offsets[i] + widths[i] - 1] = 0;
    }
    SRWLOCK lock = SRWLOCK_INIT;
    CONDITION_VARIABLE cv = CONDITION_VARIABLE_INIT;
    AcquireSRWLockExclusive(&lock);
    CHECK(!SleepConditionVariableSRW(&cv, &lock, 0, 0) && GetLastError() == ERROR_TIMEOUT);
    ReleaseSRWLockExclusive(&lock);
    CRITICAL_SECTION cs;
    InitializeCriticalSection(&cs);
    EnterCriticalSection(&cs); EnterCriticalSection(&cs);
    CHECK(TryEnterCriticalSection(&cs));
    LeaveCriticalSection(&cs); LeaveCriticalSection(&cs);
    CHECK(!SleepConditionVariableCS(&cv, &cs, 0) && GetLastError() == ERROR_TIMEOUT);
    LeaveCriticalSection(&cs); DeleteCriticalSection(&cs);
    CHECK(!pthread_rwlock_destroy(&lock.lock));
    double elapsed[] = {(double)(observed_ns() - start) / 1e6};
    stats("zero_polls", elapsed, 1, 0);
    puts("CLOCKS qpc_uptime_brackets=10000 tick_uptime_brackets=10000 frequency=1000000000 filetime_realtime=1");
    puts("POLLS widths=1,2,4,8 unaligned_and_cross_word=1 mismatch_success=4 timeout_error=4 cv_srw=1 cv_cs=1 cs_recursive=1");
}

static void finite_timeouts(void) {
    SRWLOCK lock = SRWLOCK_INIT; CONDITION_VARIABLE cv = CONDITION_VARIABLE_INIT;
    CRITICAL_SECTION cs; InitializeCriticalSection(&cs);
    volatile uint64_t word = 0; uint64_t compare = 0;
    const char *names[] = {"srw_finite", "srw_shared_finite", "cs_finite", "address32_finite", "address64_finite"};
    for (int kind = 0; kind < 5; kind++) {
        double samples[10];
        for (int i = 0; i < 10; i++) {
            if (kind == 0) AcquireSRWLockExclusive(&lock);
            else if (kind == 1) AcquireSRWLockShared(&lock);
            else if (kind == 2) EnterCriticalSection(&cs);
            const int64_t start = observed_ns();
            BOOL r;
            if (kind < 2) r = SleepConditionVariableSRW(&cv, &lock, 10, kind ? CONDITION_VARIABLE_LOCKMODE_SHARED : 0);
            else if (kind == 2) r = SleepConditionVariableCS(&cv, &cs, 10);
            else r = WaitOnAddress(&word, &compare, kind == 3 ? 4 : 8, 10);
            samples[i] = (double)(observed_ns() - start) / 1e6;
            CHECK(!r && GetLastError() == ERROR_TIMEOUT);
            if (kind == 0) ReleaseSRWLockExclusive(&lock);
            else if (kind == 1) ReleaseSRWLockShared(&lock);
            else if (kind == 2) LeaveCriticalSection(&cs);
        }
        stats(names[kind], samples, 10, 10);
    }
    CHECK(!pthread_rwlock_destroy(&lock.lock)); DeleteCriticalSection(&cs);
    puts("FINITE_TIMEOUTS no_signal_calls=50 false_and_ERROR_TIMEOUT=50");
}

struct AddressLane { volatile uint32_t seq, ack; volatile uint64_t wide; int wide_mode; };
static void address_until(volatile uint32_t *word, uint32_t wanted) {
    const int64_t end = observed_ns() + 5000000000LL;
    for (;;) {
        const uint32_t have = LOAD(word);
        if (have == wanted) return;
        CHECK(observed_ns() < end);
        uint32_t compare = have;
        CHECK(WaitOnAddress(word, &compare, sizeof compare, remaining_ms(end)));
    }
}
static void *address_worker(void *arg) {
    struct AddressLane *lane = arg;
    for (uint32_t n = 1; n <= ROUNDS; n++) {
        if (lane->wide_mode) {
            const uint64_t wanted = (uint64_t)n << 32;
            const int64_t until = observed_ns() + 5000000000LL;
            for (;;) {
                uint64_t compare = LOAD(&lane->wide);
                if (compare == wanted) break;
                CHECK(observed_ns() < until);
                CHECK(WaitOnAddress(&lane->wide, &compare, sizeof compare, remaining_ms(until)));
            }
        } else address_until(&lane->seq, n);
        STORE(&lane->ack, n);
        WakeByAddressSingle((void *)&lane->ack);
    }
    return NULL;
}
static void address_contention(int wide) {
    struct AddressLane lanes[WORKERS] = {0}; pthread_t threads[WORKERS];
    for (int i = 0; i < WORKERS; i++) {
        lanes[i].wide_mode = wide;
        CHECK(!pthread_create(&threads[i], NULL, address_worker, &lanes[i]));
    }
    for (uint32_t n = 1; n <= ROUNDS; n++) {
        for (int i = 0; i < WORKERS; i++) {
            if (wide) { STORE(&lanes[i].wide, (uint64_t)n << 32); WakeByAddressSingle((void *)&lanes[i].wide); }
            else { STORE(&lanes[i].seq, n); WakeByAddressSingle((void *)&lanes[i].seq); }
        }
        for (int i = 0; i < WORKERS; i++) address_until(&lanes[i].ack, n);
    }
    join_all(threads);
    printf("NO_LOST_WAKE address_bits=%d workers=%d rounds_per_worker=%d handshakes=%d upper_half_only=%d lost=0\n",
           wide ? 64 : 32, WORKERS, ROUNDS, WORKERS * ROUNDS, wide);
}

struct CVLane { SRWLOCK lock; CONDITION_VARIABLE cv; uint32_t seq, ack; };
static void *cv_worker(void *arg) {
    struct CVLane *lane = arg;
    for (uint32_t n = 1; n <= ROUNDS; n++) {
        AcquireSRWLockExclusive(&lane->lock);
        while (lane->seq != n) CHECK(SleepConditionVariableSRW(&lane->cv, &lane->lock, 5000, 0));
        lane->ack = n;
        ReleaseSRWLockExclusive(&lane->lock);
        WakeConditionVariable(&lane->cv);
    }
    return NULL;
}
static void cv_contention(void) {
    struct CVLane lanes[WORKERS] = {0}; pthread_t threads[WORKERS];
    for (int i = 0; i < WORKERS; i++) CHECK(!pthread_create(&threads[i], NULL, cv_worker, &lanes[i]));
    for (uint32_t n = 1; n <= ROUNDS; n++) {
        for (int i = 0; i < WORKERS; i++) {
            AcquireSRWLockExclusive(&lanes[i].lock); lanes[i].seq = n;
            ReleaseSRWLockExclusive(&lanes[i].lock); WakeConditionVariable(&lanes[i].cv);
        }
        for (int i = 0; i < WORKERS; i++) {
            AcquireSRWLockExclusive(&lanes[i].lock);
            while (lanes[i].ack != n) CHECK(SleepConditionVariableSRW(&lanes[i].cv, &lanes[i].lock, 5000, 0));
            ReleaseSRWLockExclusive(&lanes[i].lock);
        }
    }
    join_all(threads);
    for (int i = 0; i < WORKERS; i++) CHECK(!pthread_rwlock_destroy(&lanes[i].lock.lock));
    puts("NO_LOST_WAKE condition_variable workers=4 rounds_per_worker=10000 handshakes=40000 zero_initialized_locks=4 lost=0");
}

struct WakeGroup {
    SRWLOCK lock; CONDITION_VARIABLE cv; CRITICAL_SECTION cs;
    volatile uint32_t ready, returned, release, word; int mode;
};
static void *wake_waiter(void *arg) {
    struct WakeGroup *g = arg;
    if (!g->mode) {
        AcquireSRWLockExclusive(&g->lock); ADD(&g->ready, 1);
        CHECK(SleepConditionVariableSRW(&g->cv, &g->lock, INFINITE, 0));
        ReleaseSRWLockExclusive(&g->lock);
    } else {
        uint32_t compare = 0; ADD(&g->ready, 1);
        CHECK(WaitOnAddress(&g->word, &compare, sizeof compare, INFINITE));
    }
    ADD(&g->returned, 1); return NULL;
}
static void cardinality(void) {
    for (int mode = 0; mode < 3; mode++) {
        for (int round = 0; round < CARDINALITY; round++) {
            struct WakeGroup g = {0}; pthread_t threads[WORKERS]; g.mode = mode;
            for (int i = 0; i < WORKERS; i++) CHECK(!pthread_create(&threads[i], NULL, wake_waiter, &g));
            await_count(&g.ready, WORKERS); pause_ns(2000000); CHECK(!LOAD(&g.returned));
            if (!mode) {
                WakeConditionVariable(&g.cv); await_count(&g.returned, 1); quiet_count(&g.returned, 1);
                WakeAllConditionVariable(&g.cv);
            } else if (mode == 1) WakeByAddressSingle((void *)&g.word);
            else WakeByAddressAll((void *)&g.word);
            await_count(&g.returned, WORKERS); join_all(threads);
            if (!mode) CHECK(!pthread_rwlock_destroy(&g.lock.lock));
        }
        printf("WAKE_CARDINALITY kind=%s rounds=%d waiters=4 first_wake=%d final_wake=4 settle_ms=2 observation_ms=2\n",
               !mode ? "cv_one_then_all" : mode == 1 ? "address_single_broadcast" : "address_all", CARDINALITY, !mode ? 1 : 4);
    }
}
static void *held_waiter(void *arg) {
    struct WakeGroup *g = arg;
    if (g->mode == 2) EnterCriticalSection(&g->cs);
    else if (g->mode == 1) AcquireSRWLockShared(&g->lock);
    else AcquireSRWLockExclusive(&g->lock);
    STORE(&g->ready, 1);
    while (!LOAD(&g->word)) {
        if (g->mode == 2) CHECK(SleepConditionVariableCS(&g->cv, &g->cs, INFINITE));
        else CHECK(SleepConditionVariableSRW(&g->cv, &g->lock, INFINITE, g->mode ? CONDITION_VARIABLE_LOCKMODE_SHARED : 0));
    }
    STORE(&g->returned, 1);
    await_count(&g->release, 1);   /* keep the reacquired lock, so the driver can test its mode */
    if (g->mode == 2) LeaveCriticalSection(&g->cs);
    else if (g->mode == 1) ReleaseSRWLockShared(&g->lock);
    else ReleaseSRWLockExclusive(&g->lock);
    return NULL;
}
static void reacquisition(void) {
    SRWLOCK cold_exclusive = {0}, cold_shared = {0};
    CHECK(TryAcquireSRWLockExclusive(&cold_exclusive)); ReleaseSRWLockExclusive(&cold_exclusive);
    CHECK(TryAcquireSRWLockShared(&cold_shared)); ReleaseSRWLockShared(&cold_shared);
    CHECK(!pthread_rwlock_destroy(&cold_exclusive.lock)); CHECK(!pthread_rwlock_destroy(&cold_shared.lock));
    for (int mode = 0; mode < 3; mode++) {
        struct WakeGroup g = {0}; pthread_t thread; g.mode = mode;
        InitializeSRWLock(&g.lock); InitializeCriticalSection(&g.cs);
        CHECK(!pthread_create(&thread, NULL, held_waiter, &g)); await_count(&g.ready, 1);
        if (mode == 2) EnterCriticalSection(&g.cs); else AcquireSRWLockExclusive(&g.lock);
        STORE(&g.word, 1);
        if (mode == 2) LeaveCriticalSection(&g.cs); else ReleaseSRWLockExclusive(&g.lock);
        WakeAllConditionVariable(&g.cv); await_count(&g.returned, 1);
        if (mode == 2) CHECK(!TryEnterCriticalSection(&g.cs));
        else {
            CHECK(!TryAcquireSRWLockExclusive(&g.lock));
            const int shared = TryAcquireSRWLockShared(&g.lock);
            CHECK(shared == (mode == 1));
            if (shared) ReleaseSRWLockShared(&g.lock);
        }
        STORE(&g.release, 1); CHECK(!pthread_join(thread, NULL));
        CHECK(!pthread_rwlock_destroy(&g.lock.lock)); DeleteCriticalSection(&g.cs);
    }
    puts("LOCKS explicit=1 static_initializer=1 zero_storage=1 cold_try=2 srw_exclusive_reacquired=1 srw_shared_reacquired=1 cs_reacquired=1");
}

struct ObjectGroup { HANDLE object; volatile uint32_t ready, done; };
static void *object_waiter(void *arg) {
    struct ObjectGroup *g = arg; ADD(&g->ready, 1);
    CHECK(WaitForSingleObject(g->object, INFINITE) == WAIT_OBJECT_0); ADD(&g->done, 1); return NULL;
}
static void objects(void) {
    for (int round = 0; round < CARDINALITY; round++) {
        for (int kind = 0; kind < 3; kind++) {
            struct ObjectGroup g = {0}; pthread_t threads[WORKERS];
            g.object = kind == 2 ? CreateSemaphoreA(NULL, 0, 4, NULL) : CreateEventA(NULL, kind, FALSE, NULL);
            CHECK(g.object && WaitForSingleObject(g.object, 0) == WAIT_TIMEOUT);
            for (int i = 0; i < WORKERS; i++) CHECK(!pthread_create(&threads[i], NULL, object_waiter, &g));
            await_count(&g.ready, WORKERS);
            if (!kind) {
                for (uint32_t n = 1; n <= WORKERS; n++) { CHECK(SetEvent(g.object)); await_count(&g.done, n); quiet_count(&g.done, n); }
            } else if (kind == 1) { CHECK(SetEvent(g.object)); await_count(&g.done, WORKERS); }
            else {
                LONG previous = -1;
                CHECK(ReleaseSemaphore(g.object, 2, &previous) && previous == 0);
                await_count(&g.done, 2); quiet_count(&g.done, 2);
                CHECK(ReleaseSemaphore(g.object, 2, &previous) && previous == 0); await_count(&g.done, WORKERS);
            }
            join_all(threads);
            if (kind == 1) {
                CHECK(WaitForSingleObject(g.object, 0) == WAIT_OBJECT_0);
                CHECK(WaitForSingleObject(g.object, 0) == WAIT_OBJECT_0);
                CHECK(ResetEvent(g.object));
            }
            CHECK(WaitForSingleObject(g.object, 0) == WAIT_TIMEOUT);
            if (kind == 2) {
                LONG previous = -1;
                CHECK(!ReleaseSemaphore(g.object, 5, &previous) && previous == 0 && GetLastError() == ERROR_INVALID_PARAMETER);
                CHECK(ReleaseSemaphore(g.object, 4, &previous) && previous == 0);
                CHECK(!ReleaseSemaphore(g.object, 1, &previous) && previous == 4);
                for (int i = 0; i < 4; i++) CHECK(WaitForSingleObject(g.object, 0) == WAIT_OBJECT_0);
                CHECK(WaitForSingleObject(g.object, 0) == WAIT_TIMEOUT);
            }
            close_event(g.object);
        }
    }
    HANDLE e = CreateEventA(NULL, FALSE, TRUE, NULL);
    CHECK(SetEvent(e) && SetEvent(e));   /* coalesced early signals, not semaphore tokens */
    CHECK(WaitForSingleObject(e, 0) == WAIT_OBJECT_0 && WaitForSingleObject(e, 0) == WAIT_TIMEOUT);
    close_event(e);
    puts("OBJECTS rounds=50 auto_exact_one=50 manual_all_persistent=50 semaphore_two_then_two=50 over_max_rejected=100 early_signal_coalesces=1");
}

struct Noise { HEvent *event; SRWLOCK lock; CONDITION_VARIABLE cv; volatile uint32_t word, stop, wakes; int kind; };
static void *noise_worker(void *arg) {
    struct Noise *n = arg;
    while (!LOAD(&n->stop)) {
        if (!n->kind) { CHECK(!pthread_mutex_lock(&n->event->mu)); CHECK(!pthread_cond_broadcast(&n->event->cv)); CHECK(!pthread_mutex_unlock(&n->event->mu)); }
        else if (n->kind == 1) WakeAllConditionVariable(&n->cv);
        else WakeByAddressAll((void *)&n->word);
        ADD(&n->wakes, 1); pause_ns(200000);
    }
    return NULL;
}
static void noisy_deadlines(void) {
    for (int kind = 0; kind < 4; kind++) {
        struct Noise n = {0}; pthread_t thread; double samples[TIMINGS]; int returns = 0;
        n.kind = kind == 3 ? 0 : kind;
        n.event = kind == 3 ? CreateSemaphoreA(NULL, 0, 1, NULL) : CreateEventA(NULL, FALSE, FALSE, NULL);
        CHECK(!pthread_create(&thread, NULL, noise_worker, &n));
        for (int round = 0; round < TIMINGS; round++) {
            const int64_t start = observed_ns(), until = start + 10000000;
            if (!n.kind) CHECK(WaitForSingleObject(n.event, 10) == WAIT_TIMEOUT);
            else if (kind == 1) {
                AcquireSRWLockExclusive(&n.lock);
                for (DWORD ms; (ms = remaining_ms(until)) != 0;) {
                    CHECK(SleepConditionVariableSRW(&n.cv, &n.lock, ms, 0) || GetLastError() == ERROR_TIMEOUT); returns++;
                }
                ReleaseSRWLockExclusive(&n.lock);
            } else {
                for (DWORD ms; (ms = remaining_ms(until)) != 0;) {
                    uint32_t compare = 0;
                    CHECK(WaitOnAddress(&n.word, &compare, sizeof compare, ms) || GetLastError() == ERROR_TIMEOUT); returns++;
                }
            }
            samples[round] = (double)(observed_ns() - start) / 1e6;
        }
        STORE(&n.stop, 1); CHECK(!pthread_join(thread, NULL)); CHECK(LOAD(&n.wakes) > TIMINGS);
        const char *name = kind == 0 ? "event_spurious" : kind == 1 ? "cv_spurious" : kind == 2 ? "address_spurious" : "semaphore_spurious";
        printf("NOISE %s broadcasts=%u caller_wait_returns=%d\n", name, LOAD(&n.wakes), returns);
        stats(name, samples, TIMINGS, 10); close_event(n.event);
        if (kind == 1) { CHECK(returns > TIMINGS); CHECK(!pthread_rwlock_destroy(&n.lock.lock)); }
        if (kind == 2) CHECK(returns > TIMINGS);
    }
}

static volatile sig_atomic_t delivered_signals;
static void handled_signal(int number) { (void)number; delivered_signals++; }
struct Interrupt { pthread_t target; volatile uint32_t stop; };
static void *interrupt_worker(void *arg) {
    struct Interrupt *n = arg;
    while (!LOAD(&n->stop)) { CHECK(!pthread_kill(n->target, SIGUSR1)); pause_ns(500000); }
    return NULL;
}
static void timers(void) {
    struct sigaction action = {0}, old;
    action.sa_handler = handled_signal; sigemptyset(&action.sa_mask); CHECK(!sigaction(SIGUSR1, &action, &old));
    struct Interrupt n = {pthread_self(), 0}; pthread_t thread; double samples[TIMINGS];
    HANDLE timer = CreateWaitableTimerExW(NULL, NULL, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
    CHECK(timer && !pthread_create(&thread, NULL, interrupt_worker, &n));
    for (int round = 0; round < TIMINGS; round++) {
        LARGE_INTEGER due; due.QuadPart = -100000;
        const int64_t start = observed_ns();
        CHECK(SetWaitableTimerEx(timer, &due, 0, NULL, NULL, NULL, 0));
        const int64_t absolute = ((HTimer *)timer)->due;
        pause_ns(2000000);   /* already armed: waiting must not restart its relative 10 ms */
        CHECK(WaitForSingleObject(timer, INFINITE) == WAIT_OBJECT_0);
        CHECK(((HTimer *)timer)->due == absolute);
        samples[round] = (double)(observed_ns() - start) / 1e6;
    }
    STORE(&n.stop, 1); CHECK(!pthread_join(thread, NULL));
    CHECK(delivered_signals > TIMINGS); CHECK(!sigaction(SIGUSR1, &old, NULL));
    stats("absolute_timer_interrupted", samples, TIMINGS, 10);
    double expired[1]; const int64_t start = observed_ns();
    CHECK(WaitForSingleObject(timer, 0) == WAIT_OBJECT_0); expired[0] = (double)(observed_ns() - start) / 1e6;
    stats("expired_timer_poll", expired, 1, 0);
    LARGE_INTEGER due; due.QuadPart = -100000;
    CHECK(SetWaitableTimerEx(timer, &due, 0, NULL, NULL, NULL, 0));
    const int64_t zero_start = observed_ns(); CHECK(WaitForSingleObject(timer, 0) == WAIT_OBJECT_0);
    double ignored[1] = {(double)(observed_ns() - zero_start) / 1e6}; stats("timer_inherited_ignores_ms", ignored, 1, 10);
    free(timer);
    printf("TIMER handled_signals=%d absolute_deadline_preserved=50 inherited_ms_ignored=1 periodic_apc_not_implemented=1\n", delivered_signals);
}
static void *finish_thread_monitor(void *arg) {
    HThread *thread = arg; pause_ns(20000000);
    CHECK(!pthread_mutex_lock(&thread->mu)); thread->done = 1; thread->code = 7;
    CHECK(!pthread_cond_broadcast(&thread->cv)); CHECK(!pthread_mutex_unlock(&thread->mu)); return NULL;
}
static void *set_after_sleep(void *arg) { Sleep(10); CHECK(SetEvent(arg)); return NULL; }
static void completion_and_polling(void) {
    HThread thread = {0}; thread.h.type = H_THREAD; monitor_init(&thread.mu, &thread.cv);
    pthread_t worker; CHECK(!pthread_create(&worker, NULL, finish_thread_monitor, &thread));
    CHECK(WaitForSingleObject(&thread, 0) == WAIT_TIMEOUT);
    CHECK(WaitForSingleObject(&thread, INFINITE) == WAIT_OBJECT_0);
    CHECK(WaitForSingleObject(&thread, 0) == WAIT_OBJECT_0 && thread.code == 7);
    CHECK(!pthread_join(worker, NULL)); CHECK(!pthread_cond_destroy(&thread.cv)); CHECK(!pthread_mutex_destroy(&thread.mu));
    HANDLE hs[] = {CreateEventA(NULL, TRUE, FALSE, NULL), CreateEventA(NULL, TRUE, TRUE, NULL)};
    CHECK(WaitForMultipleObjects(2, hs, FALSE, 0) == WAIT_OBJECT_0 + 1);
    CHECK(WaitForMultipleObjects(2, hs, TRUE, 0) == WAIT_TIMEOUT);
    CHECK(SetEvent(hs[0]) && WaitForMultipleObjects(2, hs, TRUE, 0) == WAIT_OBJECT_0);
    CHECK(ResetEvent(hs[0]) && ResetEvent(hs[1]));
    const int64_t start = observed_ns(); CHECK(WaitForMultipleObjects(2, hs, FALSE, 10) == WAIT_TIMEOUT);
    double sample[] = {(double)(observed_ns() - start) / 1e6}; stats("multiple_objects_deadline", sample, 1, 10);
    CHECK(!pthread_create(&worker, NULL, set_after_sleep, hs[1]));
    CHECK(WaitForMultipleObjects(2, hs, FALSE, INFINITE) == WAIT_OBJECT_0 + 1);
    CHECK(!pthread_join(worker, NULL));
    close_event(hs[0]); close_event(hs[1]);
    hs[0] = CreateEventA(NULL, FALSE, TRUE, NULL); hs[1] = CreateEventA(NULL, TRUE, FALSE, NULL);
    CHECK(WaitForMultipleObjects(2, hs, TRUE, 0) == WAIT_TIMEOUT);
    CHECK(WaitForSingleObject(hs[0], 0) == WAIT_TIMEOUT);   /* inherited: wait-all already consumed the first */
    close_event(hs[0]); close_event(hs[1]);
    puts("COMPLETION real_monitor=1 initial_poll_timeout=1 infinite_woken=1 persistent_done=1");
    puts("MULTIWAIT first_index=1 all_ready=1 zero_timeout=1 infinite_woken=1 fixed_deadline=1 inherited_partial_consumption=1");
}
int main(void) {
    setvbuf(stdout, NULL, _IONBF, 0);
    mach_timebase_info_data_t timebase; CHECK(!mach_timebase_info(&timebase));
    printf("ENV pointer_bits=%zu page_bytes=%ld timebase=%u/%u deployment_floor=14.4\n", sizeof(void *) * 8,
           sysconf(_SC_PAGESIZE), timebase.numer, timebase.denom);
    puts("TOLERANCES declared_before_run target_ms=10 early_ms=0.1 late_ms=50 zero_poll_ms=50 progress_seconds=5");
    clocks_and_polls(); finite_timeouts(); address_contention(0); address_contention(1); cv_contention(); cardinality(); reacquisition();
    objects(); noisy_deadlines(); timers(); completion_and_polling();
    puts("PASS darwin_waits real_primitives source_linked");
    return 0;
}
'''

CPP = r'''#include <windows.h>
int main() {
    SRWLOCK zero = {}, initialized = SRWLOCK_INIT, explicit_lock;
    InitializeSRWLock(&explicit_lock);
    for (SRWLOCK *lock : {&zero, &initialized, &explicit_lock}) {
        AcquireSRWLockShared(lock);
        ReleaseSRWLockShared(lock);
        if (!TryAcquireSRWLockExclusive(lock)) return 1;
        ReleaseSRWLockExclusive(lock);
        if (pthread_rwlock_destroy(&lock->lock)) return 2;
    }
    LARGE_INTEGER f, now;
    if (!QueryPerformanceFrequency(&f) || !QueryPerformanceCounter(&now) || f.QuadPart != 1000000000LL) return 3;
    puts("PASS darwin_waits C++ header and lock initialization");
    return 0;
}
'''


def command(args, out, name, seconds):
    print("COMMAND " + shlex.join(map(str, args)), flush=True)
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=seconds)
        text, code = result.stdout, result.returncode
    except subprocess.TimeoutExpired as error:
        text = error.stdout or b""
        if isinstance(text, bytes):
            text = text.decode(errors="replace")
        text += f"\nTIMEOUT after {seconds} seconds\n"
        code = 124
    (out / (name + ".log")).write_text(text, encoding="utf-8")
    print(text, end="" if text.endswith("\n") else "\n", flush=True)
    print(f"EXIT {name}={code}", flush=True)
    return code


def run(args, out):
    root = args.source_tree.resolve()
    src = out / "waits.c"
    src.write_text(fixture(root), encoding="utf-8")
    cpp = out / "header.cpp"
    cpp.write_text("#include <initializer_list>\n" + CPP, encoding="utf-8")
    api = out / "wait_api.c"
    api.write_text(fixture(root, ""), encoding="utf-8")
    sharing = out / "sharing.c"
    sharing_template = ROOT / "tests/darwin_wait_sharing_test.c"
    sharing.write_text(fixture(root, sharing_template.read_text(encoding="utf-8")), encoding="utf-8")
    inputs = [root / "host/posix" / name for name in
              ("win32_posix.c", "windows.h", "darwin_wait.c", "darwin_wait.h", "darwin_wait_state.c")]
    inputs += [Path(__file__).resolve(), sharing_template, src, cpp, api, sharing]
    (out / "source-sha256.json").write_text(json.dumps(
        {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}, indent=2) + "\n", encoding="utf-8")
    failed = False
    for arch in args.arch or [platform.machine()]:
        common = ["-arch", arch, "-mmacosx-version-min=14.4", "-O2", "-Wall", "-Wextra", "-Werror",
                  "-Wno-unused-function", "-pthread", "-I", str(root / "host/posix")]
        link = []
        for name in ("darwin_wait", "darwin_wait_state"):
            helper = root / "host/posix" / (name + ".c")
            obj = out / (arch + "-" + name + ".o")
            code = command([args.cc, "-std=gnu11", *common, "-c", str(helper), "-o", str(obj)], out, arch + "-" + name, 45)
            if code:
                failed = True
                break
            link.append(str(obj))
        if len(link) != 2:
            continue
        exports = ["-Wl,-exported_symbol,_hos_posix_*", "-Wl,-undefined,error"]
        if not args.shared_only:
            binary = out / (arch + "-waits")
            code = command([args.cc, "-std=gnu11", *common, str(src), *link, *exports, "-o", str(binary)], out, arch + "-compile", 45)
            if code:
                failed = True
            elif command(["/usr/bin/arch", "-" + arch, str(binary)], out, arch + "-run", 90):
                failed = True
        cpp_binary = out / (arch + "-header")
        code = command([args.cxx, "-std=c++17", *common, str(cpp), *link, *exports, "-o", str(cpp_binary)], out, arch + "-cpp-compile", 45)
        if code:
            failed = True
        elif command(["/usr/bin/arch", "-" + arch, str(cpp_binary)], out, arch + "-cpp-run", 30):
            failed = True
        images = []
        for letter in ("A", "B"):
            image = out / (arch + "-wait-" + letter + ".dylib")
            code = command([args.cc, "-std=gnu11", *common, "-dynamiclib", str(api), link[0],
                            "-Wl,-undefined,error", "-Wl,-install_name,@rpath/" + image.name, "-o", str(image)],
                           out, arch + "-image-" + letter, 45)
            if code:
                failed = True
                break
            images.append(str(image))
        if len(images) != 2:
            continue
        decoy = out / (arch + "-decoy-state.dylib")
        code = command([args.cc, *common, "-dynamiclib", link[1], "-Wl,-undefined,error",
                        "-Wl,-install_name,@rpath/" + decoy.name, "-o", str(decoy)], out, arch + "-decoy-build", 45)
        if code:
            failed = True
            continue
        binary = out / (arch + "-sharing")
        code = command([args.cc, "-std=gnu11", *common, str(sharing), *link, *exports, "-o", str(binary)],
                       out, arch + "-sharing-build", 45)
        if code:
            failed = True
        elif command(["/usr/bin/arch", "-" + arch, str(binary), *images, str(decoy)], out, arch + "-sharing-run", 90):
            failed = True
        missing = out / (arch + "-missing-provider")
        code = command([args.cc, "-std=gnu11", *common, str(sharing), link[0], "-Wl,-undefined,error",
                        "-Wl,-exported_symbol,_main", "-o", str(missing)], out, arch + "-missing-build", 45)
        if code:
            failed = True
            continue
        for operation in ("wait", "single", "all"):
            for image in ("host", "A", "B"):
                probe = operation + "-" + image
                code = command(["/usr/bin/arch", "-" + arch, str(missing), *images, str(decoy), probe],
                               out, arch + "-missing-" + probe, 15)
                if code != 86:   # the fixture catches the expected SIGABRT, avoiding intentional crash reports
                    failed = True
        print(f"SHARING_CHECKS arch={arch} missing_provider_probes=9 expected_exit=86", flush=True)
    print(("FAIL" if failed else "PASS") + " test_darwin_waits architectures=" + ",".join(args.arch or [platform.machine()]) +
          " mode=" + ("sharing-only" if args.shared_only else "all"))
    return int(failed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="clang")
    parser.add_argument("--cxx", default="clang++")
    parser.add_argument("--arch", choices=("arm64", "x86_64"), action="append")
    parser.add_argument("--source-tree", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--shared-only", action="store_true", help="cross-image behavior and C++ headers, without the timing campaign")
    args = parser.parse_args()
    if sys.platform == "darwin":
        if args.out:
            args.out.mkdir(parents=True, exist_ok=False)
        with nullcontext(str(args.out)) if args.out else tempfile.TemporaryDirectory(prefix="darwin-waits-") as folder:
            return run(args, Path(folder))
    print("SKIP: test_darwin_waits requires macOS 14.4 or newer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
