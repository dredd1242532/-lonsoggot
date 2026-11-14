/* Game-free cross-image wait regression. GPL-3.0-or-later.
   Appended to the real synchronization group by test_darwin_waits.py. Each dylib
   links its own unmodified darwin_wait.c; only the main links the state provider.
   A separate real provider dylib is a decoy, never an accepted wait-state owner. */
#include <inttypes.h>
#include <mach/mach.h>

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line=%d: %s errno=%d\n", \
    __LINE__, #x, errno); exit(1); } } while (0)
#define LOAD(p) __atomic_load_n((p), __ATOMIC_SEQ_CST)
#define STORE(p, v) __atomic_store_n((p), (v), __ATOMIC_SEQ_CST)

typedef BOOL (*WaitFn)(volatile void *, void *, SIZE_T, DWORD);
typedef void (*WakeFn)(void *);
typedef uint32_t *(*ProviderFn)(void);
struct Image {
    const char *name;
    void *handle;
    WaitFn wait;
    WakeFn single, all;
    DWORD (*error)(void);
    void (*set_error)(DWORD);
};
struct Layout { const char *name; size_t offset, size, changed; };
static const struct Layout layouts[] = {
    {"u8-aligned", 0, 1, 0}, {"u8-subword", 3, 1, 0},
    {"u16-aligned", 0, 2, 1}, {"u16-cross-word", 3, 2, 1},
    {"u32-aligned", 0, 4, 3}, {"u32-cross-word", 2, 4, 3},
    {"u64-upper-half", 0, 8, 4}, {"u64-cross-word-upper-half", 1, 8, 7},
};
static uint32_t *host_sequences;
static int64_t sharing_now(void) { return (int64_t)clock_gettime_nsec_np(CLOCK_UPTIME_RAW); }
static void sharing_pause(void) { struct timespec t = {0, 100000}; nanosleep(&t, NULL); }
static uint64_t sequence_sum(const uint32_t *sequences) {
    uint64_t sum = 0;
    for (size_t i = 0; i < DARWIN_ADDRESS_WAIT_SEQUENCE_COUNT; i++) sum += LOAD(&sequences[i]);
    return sum;
}
static void wake_checked(WakeFn wake, void *address) {
    const uint64_t before = sequence_sum(host_sequences);
    wake(address);
    CHECK(sequence_sum(host_sequences) == before + 1);
}
static struct Image open_image(const char *name, const char *path) {
    struct Image image = {.name = name, .handle = dlopen(path, RTLD_NOW | RTLD_LOCAL)};
    if (!image.handle) { fprintf(stderr, "dlopen %s: %s\n", name, dlerror()); exit(1); }
    image.wait = (WaitFn)dlsym(image.handle, "WaitOnAddress");
    image.single = (WakeFn)dlsym(image.handle, "WakeByAddressSingle");
    image.all = (WakeFn)dlsym(image.handle, "WakeByAddressAll");
    image.error = (DWORD (*)(void))dlsym(image.handle, "GetLastError");
    image.set_error = (void (*)(DWORD))dlsym(image.handle, "SetLastError");
    CHECK(image.wait && image.single && image.all && image.error && image.set_error);
    CHECK(!dlsym(image.handle, "hos_posix_address_wait_sequences"));
    return image;
}
static void check_images(struct Image *images, const void *main_entry) {
    Dl_info main_info, entries[3];
    CHECK(dladdr(main_entry, &main_info));
    for (int i = 0; i < 3; i++) {
        CHECK(dladdr((void *)images[i].wait, &entries[i]));
        Dl_info single, all;
        CHECK(dladdr((void *)images[i].single, &single) && dladdr((void *)images[i].all, &all));
        CHECK(entries[i].dli_fbase == single.dli_fbase && entries[i].dli_fbase == all.dli_fbase);
        for (int j = 0; j < i; j++) {
            CHECK(images[i].wait != images[j].wait && images[i].single != images[j].single && images[i].all != images[j].all);
            CHECK(entries[i].dli_fbase != entries[j].dli_fbase);
        }
        images[i].set_error((DWORD)(100 + i));
    }
    CHECK(entries[0].dli_fbase == main_info.dli_fbase);
    for (int i = 0; i < 3; i++) CHECK(images[i].error() == (DWORD)(100 + i));
    CHECK(!dlsym(RTLD_MAIN_ONLY, "WaitOnAddress"));
    CHECK(!dlsym(RTLD_MAIN_ONLY, "WakeByAddressSingle"));
    CHECK(!dlsym(RTLD_MAIN_ONLY, "WakeByAddressAll"));
    puts("IMAGES distinct_dylibs=2 distinct_wait_entries=3 distinct_wake_entries=3 dladdr_identity=1 image_local_errors=3 main_win32_exports=0");
}

/* These paths must not acquire address-bucket state, even in a process whose
   executable deliberately lacks that provider. No elapsed-time measurements. */
static void independent_primitives(void) {
    LARGE_INTEGER frequency, counter;
    CHECK(QueryPerformanceFrequency(&frequency) && frequency.QuadPart == 1000000000LL);
    CHECK(QueryPerformanceCounter(&counter) && counter.QuadPart > 0);
    CHECK(GetTickCount64() > 0);
    volatile uint32_t word = 1;
    CHECK(!darwin_wait_word(&word, 0, 0));
    CHECK(darwin_wait_word(&word, 1, 0) == -1 && errno == ETIMEDOUT);
    SRWLOCK lock = {0}; CONDITION_VARIABLE cv = CONDITION_VARIABLE_INIT;
    CHECK(TryAcquireSRWLockExclusive(&lock));
    CHECK(!SleepConditionVariableSRW(&cv, &lock, 0, 0) && GetLastError() == ERROR_TIMEOUT);
    ReleaseSRWLockExclusive(&lock);
    CHECK(TryAcquireSRWLockShared(&lock));
    CHECK(!SleepConditionVariableSRW(&cv, &lock, 0, CONDITION_VARIABLE_LOCKMODE_SHARED) && GetLastError() == ERROR_TIMEOUT);
    ReleaseSRWLockShared(&lock);
    CHECK(!pthread_rwlock_destroy(&lock.lock));
    CRITICAL_SECTION cs; InitializeCriticalSection(&cs);
    EnterCriticalSection(&cs); CHECK(TryEnterCriticalSection(&cs)); LeaveCriticalSection(&cs);
    CHECK(!SleepConditionVariableCS(&cv, &cs, 0) && GetLastError() == ERROR_TIMEOUT);
    LeaveCriticalSection(&cs); DeleteCriticalSection(&cs);
    puts("INDEPENDENT clock_domain_frequency=1 word_poll=1 cold_srw=1 cv_exclusive_shared_cs=1 recursive_cs=1");
}

struct SharingWaiter {
    struct Image *image;
    unsigned char *address;
    unsigned char compare[8];
    size_t size;
    volatile uint32_t ready, done;
    BOOL result;
};
static void *sharing_waiter(void *arg) {
    struct SharingWaiter *waiter = arg;
    STORE(&waiter->ready, 1);
    waiter->result = waiter->image->wait(waiter->address, waiter->compare, waiter->size, INFINITE);
    STORE(&waiter->done, 1);
    return NULL;
}
static void await_ready(struct SharingWaiter *waiter) {
    const int64_t end = sharing_now() + 5000000000LL;
    while (!LOAD(&waiter->ready)) { CHECK(sharing_now() < end); sharing_pause(); }
}
static int kernel_wait(pthread_t thread, struct SharingWaiter *waiter) {
    await_ready(waiter);
    const int64_t end = sharing_now() + 5000000000LL;
    for (;;) {
        if (LOAD(&waiter->done)) return 0;   /* spurious early return: not queued-wake evidence */
        thread_basic_info_data_t info;
        mach_msg_type_number_t count = THREAD_BASIC_INFO_COUNT;
        const kern_return_t result = thread_info(pthread_mach_thread_np(thread), THREAD_BASIC_INFO, (thread_info_t)&info, &count);
        if (LOAD(&waiter->done)) return 0;
        CHECK(result == KERN_SUCCESS);
        if (info.run_state == TH_STATE_WAITING) return 1;
        CHECK(sharing_now() < end);
        sharing_pause();
    }
}
static void finish_waiter(pthread_t thread, struct SharingWaiter *waiter) {
    const int64_t end = sharing_now() + 5000000000LL;
    while (!LOAD(&waiter->done)) { CHECK(sharing_now() < end); sharing_pause(); }
    CHECK(!pthread_join(thread, NULL));
    CHECK(waiter->result);
}
static void polls(struct Image *image, const struct Layout *layout) {
    _Alignas(8) unsigned char data[24] = {0}, compare[8] = {0};
    unsigned char *address = data + layout->offset;
    CHECK(!image->wait(address, compare, layout->size, 0) && image->error() == ERROR_TIMEOUT);
    STORE(address + layout->changed, 1);
    CHECK(image->wait(address, compare, layout->size, 0));
    CHECK(image->wait(address, compare, layout->size, 10));
    CHECK(image->wait(address, compare, layout->size, INFINITE));
    STORE(address + layout->changed, 0);
    STORE(address + layout->size, 1);   /* an adjacent byte is not part of the comparison */
    CHECK(!image->wait(address, compare, layout->size, 0) && image->error() == ERROR_TIMEOUT);
    CHECK(!image->wait(address, compare, layout->size, 1) && image->error() == ERROR_TIMEOUT);
    printf("POLLS image=%s layout=%s size=%zu zero_equal_timeout=1 full_width_mismatch=3 adjacent_ignored=1 finite_timeout=1\n",
           image->name, layout->name, layout->size);
}
static void pair(struct Image *waiting, struct Image *waking, const struct Layout *layout, int all, int queued) {
    enum { ROUNDS = 4 };
    int observed = 0, early = 0;
    printf("BEGIN wait=%s wake=%s layout=%s wrapper=%s phase=%s rounds=%d\n", waiting->name, waking->name,
           layout->name, all ? "all" : "single", queued ? "queued" : "entry-race", ROUNDS);
    for (int round = 0; round < ROUNDS; round++) {
        _Alignas(8) unsigned char data[24] = {0};
        struct SharingWaiter waiter = {.image = waiting, .address = data + layout->offset, .size = layout->size};
        pthread_t thread;
        CHECK(!pthread_create(&thread, NULL, sharing_waiter, &waiter));
        if (queued) {
            if (kernel_wait(thread, &waiter)) observed++;
            else early++;
        } else {
            await_ready(&waiter);
            if (LOAD(&waiter.done)) early++;
            else observed++;
        }
        STORE(waiter.address + layout->changed, 1);
        wake_checked(all ? waking->all : waking->single, waiter.address);
        finish_waiter(thread, &waiter);
    }
    printf("PAIR wait=%s wake=%s layout=%s size=%zu wrapper=%s phase=%s rounds=%d observed=%d early_unjudged=%d completed=%d\n",
           waiting->name, waking->name, layout->name, layout->size, all ? "all" : "single",
           queued ? "queued" : "entry-race", ROUNDS, observed, early, ROUNDS);
    CHECK(observed > 0);   /* fixed attempts, no retry-to-pass or all-spurious coverage */
}
static void broadcast(struct Image *images, const struct Layout *layout, int all) {
    enum { WAITERS = 4 };
    _Alignas(8) unsigned char data[24] = {0};
    struct SharingWaiter waiters[WAITERS] = {0}; pthread_t threads[WAITERS];
    printf("BEGIN broadcast layout=%s wrapper=%s waiters=%d\n", layout->name, all ? "all" : "single", WAITERS);
    int observed = 0;
    for (int i = 0; i < WAITERS; i++) {
        waiters[i].image = &images[i % 3];
        waiters[i].address = data + layout->offset;
        waiters[i].size = layout->size;
        CHECK(!pthread_create(&threads[i], NULL, sharing_waiter, &waiters[i]));
    }
    for (int i = 0; i < WAITERS; i++) observed += kernel_wait(threads[i], &waiters[i]);
    STORE(data + layout->offset + layout->changed, 1);
    wake_checked(all ? images[2].all : images[1].single, data + layout->offset);
    for (int i = 0; i < WAITERS; i++) finish_waiter(threads[i], &waiters[i]);
    printf("BROADCAST layout=%s size=%zu wrapper=%s kernel_wait_observed=%d completed=%d\n",
           layout->name, layout->size, all ? "all" : "single", observed, WAITERS);
    CHECK(observed == WAITERS);
}

static void provider_abort(int signal_number) {
    (void)signal_number;
    /* The fixture catches only this expected fatal condition, so a missing
       provider does not leave an intentional crash report on the user's Mac. */
    static const char message[] = "EXPECTED missing-provider SIGABRT\n";
    (void)write(STDERR_FILENO, message, sizeof message - 1);
    _Exit(86);
}
static int missing_provider(struct Image *images, const char *probe) {
    independent_primitives();
    struct sigaction action = {0};
    action.sa_handler = provider_abort;
    CHECK(!sigemptyset(&action.sa_mask) && !sigaction(SIGABRT, &action, NULL));
    const char *separator = strchr(probe, '-');
    CHECK(separator);
    struct Image *image = NULL;
    for (int i = 0; i < 3; i++) if (!strcmp(separator + 1, images[i].name)) image = &images[i];
    CHECK(image);
    printf("MISSING provider_scope=main_only decoy_present=1 action=%s expected_exit=86\n", probe);
    uint64_t value = 0, compare = 0;
    if (!strncmp(probe, "wait-", 5)) (void)image->wait(&value, &compare, sizeof value, INFINITE);
    else if (!strncmp(probe, "single-", 7)) image->single(&value);
    else if (!strncmp(probe, "all-", 4)) image->all(&value);
    else CHECK(0);
    fputs("FAIL missing provider allowed a wait/wake to return\n", stderr);
    return 1;
}
int main(int argc, char **argv) {
    CHECK(argc == 4 || argc == 5);
    setvbuf(stdout, NULL, _IONBF, 0);
    void *decoy = dlopen(argv[3], RTLD_NOW | RTLD_GLOBAL);
    if (!decoy) { fprintf(stderr, "dlopen decoy: %s\n", dlerror()); return 1; }
    ProviderFn decoy_provider = (ProviderFn)dlsym(decoy, "hos_posix_address_wait_sequences");
    CHECK(decoy_provider);
    uint32_t *decoy_sequences = decoy_provider();
    CHECK(decoy_sequences && !sequence_sum(decoy_sequences));
    struct Image images[] = {
        {.name = "host", .wait = WaitOnAddress, .single = WakeByAddressSingle, .all = WakeByAddressAll,
         .error = GetLastError, .set_error = SetLastError},
        open_image("A", argv[1]), open_image("B", argv[2]),
    };
    check_images(images, (void *)main);
    ProviderFn provider = (ProviderFn)dlsym(RTLD_MAIN_ONLY, "hos_posix_address_wait_sequences");
    if (argc == 5) {
        CHECK(!provider);
        CHECK((ProviderFn)dlsym(RTLD_DEFAULT, "hos_posix_address_wait_sequences") == decoy_provider);
        return missing_provider(images, argv[4]);
    }
    CHECK(provider && provider != decoy_provider);
    Dl_info owner, executable;
    CHECK(dladdr((void *)provider, &owner) && dladdr((void *)main, &executable));
    CHECK(owner.dli_fbase == executable.dli_fbase);
    host_sequences = provider();
    CHECK(host_sequences && host_sequences == provider() && host_sequences != decoy_sequences);
    CHECK((uintptr_t)host_sequences % _Alignof(uint32_t) == 0 && !sequence_sum(host_sequences));
    printf("PROVIDER canonical_main=1 stable_zero_words=%d separate_unused_decoy=1 page_bytes=%ld\n",
           DARWIN_ADDRESS_WAIT_SEQUENCE_COUNT, sysconf(_SC_PAGESIZE));
    independent_primitives();
    /* Resolve wait and wake bindings before thread_info observes an owned
       waiter's kernel state. A lazy loader wait must not count as address wait. */
    for (int i = 0; i < 3; i++) {
        uint32_t value = 0, compare = 0;
        CHECK(!images[i].wait(&value, &compare, sizeof value, 1) && images[i].error() == ERROR_TIMEOUT);
        wake_checked(images[i].single, &value);
        wake_checked(images[i].all, &value);
    }
    for (size_t layout = 0; layout < sizeof layouts / sizeof *layouts; layout++) {
        for (int wait = 0; wait < 3; wait++) {
            polls(&images[wait], &layouts[layout]);
            for (int wake = 0; wake < 3; wake++) {
                for (int all = 0; all < 2; all++) {
                    pair(&images[wait], &images[wake], &layouts[layout], all, 1);
                    pair(&images[wait], &images[wake], &layouts[layout], all, 0);
                }
            }
        }
        broadcast(images, &layouts[layout], 0);
        broadcast(images, &layouts[layout], 1);
    }
    CHECK(host_sequences == provider() && !sequence_sum(decoy_sequences));
    printf("STATE canonical_wake_increments=%" PRIu64 " decoy_wake_increments=0 stable_provider=1\n", sequence_sum(host_sequences));
    CHECK(!dlclose(images[2].handle) && !dlclose(images[1].handle) && !dlclose(decoy));
    puts("PASS darwin_wait_sharing full_width_cross_image real_host_provider no_win32_interposition");
    return 0;
}
