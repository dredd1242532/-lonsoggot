/* Game-free Darwin host behavior, linked to the real shim, fibers, jumps and profiler. */
#include "hos.h"
#include "darwin_fiber.h"
#include "darwin_prof.h"
#include <windows.h>
#include <psapi.h>
#include <dlfcn.h>
#include <errno.h>
#include <inttypes.h>
#include <mach/mach_vm.h>
#include <mach/thread_policy.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <unistd.h>

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line=%d: %s (errno=%d winerr=%u)\n", __LINE__, #x, errno, GetLastError()); exit(1); } } while (0)
__attribute__((returns_twice)) int recomp_sj_setjmp(void **);
void hos_setjmp_return(Ctx *, uint64_t);
static _Atomic unsigned log_seen;
static _Atomic uint64_t profile_guest;
void hos_log(const char *fmt, ...) {
    char line[8192];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof line, fmt, ap);
    va_end(ap);
    if (strstr(line, "prof: busiest threads")) atomic_fetch_or(&log_seen, 1);
    if (strstr(line, "prof: threads in")) atomic_fetch_or(&log_seen, 2);
    if (strstr(line, "prof-sample leaf")) atomic_fetch_or(&log_seen, 4);
    if (strstr(line, "prof-sample ours")) atomic_fetch_or(&log_seen, 8);
    puts(line);
}
void hos_log_flush(void) { fflush(stdout); }
int64_t hos_now_ns(void) { return (int64_t)clock_gettime_nsec_np(CLOCK_UPTIME_RAW); }
/* The fixture has no guest. This authored registry entry lets HOS_SAMPLE_ALL exercise its guest selection,
   in addition to the real kernel/shim thread names, without linking or running any game-derived object. */
int hos_darwin_guest_thread_name(uint64_t id, char *out, size_t cap) {
    if (!id || id != atomic_load(&profile_guest)) return 0;
    snprintf(out, cap, "mkw_vk_fixture");
    return 1;
}
static void until(_Atomic int *flag) {
    const int64_t end = hos_now_ns() + 5000000000LL;
    while (!atomic_load(flag) && hos_now_ns() < end) Sleep(1);
    CHECK(atomic_load(flag));
}
static uint64_t ft(FILETIME t) { return (uint64_t)t.dwHighDateTime << 32 | t.dwLowDateTime; }
static vm_region_basic_info_data_64_t region(uintptr_t where, mach_vm_address_t *base, mach_vm_size_t *size) {
    vm_region_basic_info_data_64_t info;
    mach_msg_type_number_t count = VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t object = MACH_PORT_NULL;
    *base = where;
    CHECK(mach_vm_region(mach_task_self(), base, size, VM_REGION_BASIC_INFO_64, (vm_region_info_t)&info, &count, &object) == KERN_SUCCESS);
    if (object) mach_port_deallocate(mach_task_self(), object);
    CHECK(*base <= where && where - *base < *size);
    return info;
}
static void guarded(uintptr_t stack_address) {
    mach_vm_address_t base;
    mach_vm_size_t size;
    vm_region_basic_info_data_64_t body = region(stack_address, &base, &size);
    CHECK((body.protection & (VM_PROT_READ | VM_PROT_WRITE)) == (VM_PROT_READ | VM_PROT_WRITE));
    const uintptr_t low = base, high = base + size;
    vm_region_basic_info_data_64_t bottom = region(low - 1, &base, &size);
    CHECK(bottom.protection == VM_PROT_NONE && size >= (size_t)sysconf(_SC_PAGESIZE));
    vm_region_basic_info_data_64_t top = region(high, &base, &size);
    CHECK(top.protection == VM_PROT_NONE && size >= (size_t)sysconf(_SC_PAGESIZE));
}

static _Atomic int thread_ready, thread_release;
static uint64_t native_id;
static DWORD win_id;
static DWORD WINAPI ordinary_thread(void *p) {
    (void)p;
    CHECK(pthread_threadid_np(NULL, &native_id) == 0);
    win_id = GetCurrentThreadId();
    CHECK(win_id == pthread_mach_thread_np(pthread_self()));
    CHECK(darwin_thread_id(GetCurrentThread()) == native_id);
    CHECK(SetThreadDescription(GetCurrentThread(), L"DarwinHost kernel name beyond fifteen") == S_OK);
    char name[64];
    CHECK(pthread_getname_np(pthread_self(), name, sizeof name) == 0);
    CHECK(!strcmp(name, "DarwinHost kernel name beyond fifteen"));
    const uintptr_t bottom = (uintptr_t)pthread_get_stackaddr_np(pthread_self()) - pthread_get_stacksize_np(pthread_self());
    mach_vm_address_t base;
    mach_vm_size_t size;
    CHECK(region(bottom - 1, &base, &size).protection == VM_PROT_NONE);
    CHECK(size >= (size_t)sysconf(_SC_PAGESIZE));
    atomic_store(&thread_ready, 1);
    while (!atomic_load(&thread_release)) YieldProcessor();
    return 37;
}
static void test_threads(void) {
    errno = 0;
    const int nice_before = getpriority(PRIO_PROCESS, 0);
    CHECK(errno == 0);
    DWORD id;
    HANDLE h = CreateThread(NULL, (1u << 20) + 17, ordinary_thread, NULL, 0, &id);
    CHECK(h);
    until(&thread_ready);
    CHECK(id == win_id && GetThreadId(h) == win_id);
    CHECK(darwin_thread_id(h) == native_id);
    HANDLE opened = OpenThread(THREAD_QUERY_INFORMATION, FALSE, id);
    CHECK(opened && darwin_thread_id(opened) == native_id);
    HANDLE duplicate = NULL;
    CHECK(DuplicateHandle(GetCurrentProcess(), h, GetCurrentProcess(), &duplicate, 0, FALSE, DUPLICATE_SAME_ACCESS));
    CHECK(SetThreadPriority(h, THREAD_PRIORITY_LOWEST));
    thread_precedence_policy_data_t policy;
    mach_msg_type_number_t count = THREAD_PRECEDENCE_POLICY_COUNT;
    boolean_t defaults = FALSE;
    CHECK(thread_policy_get(id, THREAD_PRECEDENCE_POLICY, (thread_policy_t)&policy, &count, &defaults) == KERN_SUCCESS);
    CHECK(policy.importance == THREAD_PRIORITY_LOWEST);
    CHECK(GetThreadPriority(h) == THREAD_PRIORITY_LOWEST);
    CHECK(SetThreadPriority(h, THREAD_PRIORITY_NORMAL));
    count = THREAD_PRECEDENCE_POLICY_COUNT;
    CHECK(thread_policy_get(id, THREAD_PRECEDENCE_POLICY, (thread_policy_t)&policy, &count, &defaults) == KERN_SUCCESS);
    CHECK(policy.importance == THREAD_PRIORITY_NORMAL);
    errno = 0;
    CHECK(getpriority(PRIO_PROCESS, 0) == nice_before && errno == 0);
    CHECK(SetThreadDescription(h, L"DarwinHost retained remote description") == S_FALSE);
    PWSTR name = NULL;
    CHECK(GetThreadDescription(h, &name) == S_OK && !wcscmp(name, L"DarwinHost retained remote description"));
    LocalFree(name);
    char label[64];
    CHECK(darwin_thread_name(native_id, label, sizeof label) && !strcmp(label, "DarwinHost retained remote description"));
    FILETIME created, ended, kernel, user;
    CHECK(GetThreadTimes(h, &created, &ended, &kernel, &user));
    CHECK(ft(created) > 0 && ft(ended) == 0 && ft(kernel) + ft(user) > 0);
    const uint64_t before = ft(kernel) + ft(user);
    atomic_store(&thread_release, 1);
    CHECK(WaitForSingleObject(h, 5000) == WAIT_OBJECT_0);
    DWORD code;
    CHECK(GetExitCodeThread(h, &code) && code == 37);
    CHECK(GetThreadTimes(h, &created, &ended, &kernel, &user));
    CHECK(ft(ended) >= ft(created) && ft(kernel) + ft(user) >= before);
    CHECK(CloseHandle(opened) && CloseHandle(duplicate) && CloseHandle(h));
    CHECK(!CreateThread(NULL, 0, ordinary_thread, NULL, CREATE_SUSPENDED, NULL) && GetLastError() == ERROR_NOT_SUPPORTED);
    DWORD_PTR process = 123, system = 456;
    CHECK(!GetProcessAffinityMask(GetCurrentProcess(), &process, &system));
    CHECK(!process && !system && GetLastError() == ERROR_NOT_SUPPORTED);
    CHECK(!SetThreadAffinityMask(GetCurrentThread(), 1) && GetLastError() == ERROR_NOT_SUPPORTED);
    DWORD size = 0;
    CHECK(!GetLogicalProcessorInformation(NULL, &size) && size == 0 && GetLastError() == ERROR_NOT_SUPPORTED);
    SYSTEM_INFO info;
    GetSystemInfo(&info);
    CHECK(info.dwPageSize == (DWORD)sysconf(_SC_PAGESIZE));
    CHECK(info.dwNumberOfProcessors > 0 && !info.dwActiveProcessorMask);
#if defined(__aarch64__)
    CHECK(info.wProcessorArchitecture == 12);
#else
    CHECK(info.wProcessorArchitecture == 9);
#endif
    printf("threads native_id_bits=%zu win_id_is_mach_port=1 names=kernel_and_remote priority=restored times=live_and_exit guards=%u affinity=explicitly_unsupported\n",
           sizeof native_id * 8, info.dwPageSize);
}

static _Thread_local int thread_cookie;
__attribute__((noinline)) static int cookie(void) { return thread_cookie; }
static void *moving, *root[2];
static Ctx fiber_ctx;
static uintptr_t fiber_stack;
static _Atomic int a_waiting, b_waiting, allow_a, allow_b, migrated;
typedef struct { uint64_t reg[18], fp[2], sp[2], control[2], auxiliary[2]; } FiberRegisters;
_Static_assert(offsetof(FiberRegisters, fp) == 144, "fiber FP offset");
_Static_assert(offsetof(FiberRegisters, sp) == 160, "fiber SP offset");
_Static_assert(offsetof(FiberRegisters, control) == 176, "fiber control offset");
_Static_assert(offsetof(FiberRegisters, auxiliary) == 192, "fiber auxiliary offset");
void darwin_fiber_registers(void *, const uint64_t *, FiberRegisters *);
__attribute__((noinline)) void moving_fiber(void *arg) {
    volatile uint64_t local = UINT64_C(0xabcddcba12344321);
    fiber_stack = (uintptr_t)&local;
    CHECK(arg == (void *)(uintptr_t)0x1234 && GetFiberData() == arg);
    CHECK(GetCurrentFiber() == moving && cookie() == 101);
    CHECK(darwin_fiber_local(0) == NULL);
    void **slot = recomp_sj_push(&fiber_ctx, 0x7004, 0, 0, 0);
    const int jumped = recomp_sj_setjmp(slot);
    if (!jumped) {
        atomic_store(&a_waiting, 1);
        while (!atomic_load(&allow_a)) YieldProcessor();
        uint64_t patterns[18];
        for (unsigned i = 0; i < 18; i++) patterns[i] = UINT64_C(0x8877665500000000) | (i + 1);
        FiberRegisters registers = {0};
        darwin_fiber_registers(root[0], patterns, &registers);
#if defined(__aarch64__)
        const unsigned gpr = 10, vector = 8;
#else
        const unsigned gpr = 5, vector = 0;
#endif
        for (unsigned i = 0; i < gpr; i++) CHECK(registers.reg[i] == patterns[i]);
        for (unsigned i = 0; i < vector; i++) CHECK(registers.reg[10 + i] == patterns[10 + i]);
        CHECK(registers.fp[0] == registers.fp[1] && registers.sp[0] == registers.sp[1]);
        CHECK(!(registers.sp[1] & 15) && registers.control[0] == registers.control[1] && registers.auxiliary[0] == registers.auxiliary[1]);
        printf("fiber_registers migration=1 gpr=%u vector=%u fp=1 sp=1 control=1 auxiliary=1\n", gpr + 1, vector);
        CHECK(cookie() == 202 && GetCurrentFiber() == moving);
        CHECK(local == UINT64_C(0xabcddcba12344321));
        atomic_store(&b_waiting, 1);
        while (!atomic_load(&allow_b)) YieldProcessor();
        (void)recomp_sj_push(&fiber_ctx, 0x8004, 0, 0, 0);
        fiber_ctx.x[0] = 77;
        hos_setjmp_return(&fiber_ctx, 0x7004);
        CHECK(0);
    } else {
        CHECK(jumped == 1 && recomp_sj_target(slot) == 0x7004 && fiber_ctx.x[0] == 77);
        CHECK(cookie() == 202 && local == UINT64_C(0xabcddcba12344321));
        atomic_store(&migrated, 1);
    }
    recomp_sj_pop(slot);
    SwitchToFiber(root[1]);
    CHECK(0);
}
static DWORD WINAPI first_fiber_thread(void *arg) {
    (void)arg;
    thread_cookie = 101;
    Ctx c = {0};
    void **slot = recomp_sj_push(&c, 0x7004, 0, 0, 0);  /* before conversion: this must become the root's state */
    const int jumped = recomp_sj_setjmp(slot);
    if (!jumped) {
        root[0] = ConvertThreadToFiberEx(NULL, FIBER_FLAG_FLOAT_SWITCH);
        CHECK(root[0]);
        SwitchToFiber(moving);
        CHECK(cookie() == 101 && GetCurrentFiber() == root[0]);
        CHECK(ConvertFiberToThread() && !GetCurrentFiber());
        c.x[0] = 11;
        hos_setjmp_return(&c, 0x7004);   /* the root's slot, not the suspended fiber's */
        CHECK(0);
    } else CHECK(jumped == 1 && c.x[0] == 11);
    recomp_sj_pop(slot);
    return 11;
}
static DWORD WINAPI second_fiber_thread(void *arg) {
    (void)arg;
    thread_cookie = 202;
    CHECK(darwin_fiber_local(0) == NULL);
    Ctx c = {0};
    void **slot = recomp_sj_push(&c, 0x7004, 0, 0, 0);
    CHECK(recomp_sj_setjmp(slot) == 0);  /* a TLS-only bug jumps here instead of the migrating fiber */
    root[1] = ConvertThreadToFiberEx(NULL, FIBER_FLAG_FLOAT_SWITCH);
    CHECK(root[1]);
    SwitchToFiber(moving);
    CHECK(atomic_load(&migrated) && cookie() == 202);
    CHECK(ConvertFiberToThread());
    recomp_sj_pop(slot);
    return 22;
}
static int symbol_is(uintptr_t pc, const void *function) {
    Dl_info info;
    return dladdr((const void *)pc, &info) && info.dli_saddr == function;
}
static void sample_fiber(HANDLE h) {
    for (int i = 0; i < 10; i++) {
        DarwinSample sample;
        CHECK(darwin_prof_sample(GetThreadId(h), &sample) >= 2);
        CHECK(symbol_is(sample.pc[0], (const void *)moving_fiber));
    }
}
static void *fresh_root;
static void fresh_fiber(void *arg) {
    (void)arg;
    CHECK(darwin_fiber_local(0) == NULL);
    SwitchToFiber(fresh_root);
    CHECK(0);
}
static void exiting_fiber(void *arg) {
    Ctx c = {0};
    (void)recomp_sj_push(&c, 0x9004, 0, 0, 0);
    if ((uintptr_t)arg == 1) ExitThread(73);
    if ((uintptr_t)arg == 2) DeleteFiber(GetCurrentFiber());
}
static DWORD WINAPI fiber_exit_thread(void *arg) {
    CHECK(ConvertThreadToFiber(NULL));
    void *fiber = CreateFiber(1u << 20, exiting_fiber, arg);
    CHECK(fiber);
    SwitchToFiber(fiber);
    CHECK(0);                        /* return/delete/ExitThread all terminate this pthread from its root */
    return 99;
}
static void test_fibers(void) {
    moving = CreateFiberEx(64u << 10, 4u << 20, FIBER_FLAG_FLOAT_SWITCH, moving_fiber, (void *)(uintptr_t)0x1234);
    CHECK(moving);
    HANDLE a = CreateThread(NULL, 0, first_fiber_thread, NULL, 0, NULL);
    CHECK(a);
    until(&a_waiting);
    CHECK(ConvertThreadToFiber(NULL));
    SetLastError(0);
    SwitchToFiber(moving);             /* another pthread cannot take a still-running context */
    CHECK(GetLastError() == ERROR_INVALID_PARAMETER);
    CHECK(ConvertFiberToThread());
    guarded(fiber_stack);
    sample_fiber(a);
    atomic_store(&allow_a, 1);
    CHECK(WaitForSingleObject(a, 5000) == WAIT_OBJECT_0);
    DWORD code;
    CHECK(GetExitCodeThread(a, &code) && code == 11);
    /* The shim's completion predicate precedes pthread TLS destructors. Wait for the actual Mach
       thread to die, while this handle still pins its port name, before allowing migration. */
    mach_port_type_t kind = 0;
    const int64_t ended_by = hos_now_ns() + 5000000000LL;
    do {
        CHECK(mach_port_type(mach_task_self(), GetThreadId(a), &kind) == KERN_SUCCESS);
        if (kind & MACH_PORT_TYPE_DEAD_NAME) break;
        Sleep(1);
    } while (hos_now_ns() < ended_by);
    CHECK(kind & MACH_PORT_TYPE_DEAD_NAME);
    puts("fiber_creator native_thread_dead=1 TLS_destructors_completed=1");
    CHECK(CloseHandle(a));
    /* The first native thread is gone BEFORE another one resumes the fiber's still-live jump frame. */
    HANDLE b = CreateThread(NULL, 0, second_fiber_thread, NULL, 0, NULL);
    CHECK(b);
    until(&b_waiting);
    sample_fiber(b);
    atomic_store(&allow_b, 1);
    CHECK(WaitForSingleObject(b, 5000) == WAIT_OBJECT_0);
    CHECK(GetExitCodeThread(b, &code) && code == 22);
    CHECK(CloseHandle(b));
    CHECK(atomic_load(&migrated));
    DeleteFiber(moving);
    fresh_root = ConvertThreadToFiber(NULL);
    CHECK(fresh_root);
    void *fresh = CreateFiber(1u << 20, fresh_fiber, NULL);
    CHECK(fresh);
    SwitchToFiber(fresh);
    DeleteFiber(fresh);
    CHECK(ConvertFiberToThread());
    for (uintptr_t i = 0; i < 3; i++) {
        HANDLE h = CreateThread(NULL, 0, fiber_exit_thread, (void *)i, 0, NULL);
        CHECK(h && WaitForSingleObject(h, 5000) == WAIT_OBJECT_0);
        CHECK(GetExitCodeThread(h, &code) && code == (i == 1 ? 73u : 0u));
        CHECK(CloseHandle(h));
    }
    puts("fibers migration=after_creator_exit nonlocal=root_and_fiber native_TLS=current guards=both_native_pages interrupted_samples=20 exit_modes=3");
}

static _Atomic int sampling_ready, sampling_end;
static volatile uint64_t sink;
/* A real call forces the framed canary to keep its own record on arm64. -fno-omit-frame-pointer alone
   permits leaf-frame omission there. The original frameless loop is tested separately below, not waived. */
__attribute__((noinline, optnone)) static uint64_t sampling_seed(void) { return atomic_load(&profile_guest); }
__attribute__((noinline, optnone)) void sample_leaf(void) {
    sink = sampling_seed();
    atomic_store(&sampling_ready, 1);   /* publish only after returning into the framed target */
    while (!atomic_load(&sampling_end)) { sink++; __asm__ volatile("" ::: "memory"); }
}
__attribute__((noinline, optnone)) void sample_frameless(void) {
    atomic_store(&sampling_ready, 1);
    while (!atomic_load(&sampling_end)) { sink++; __asm__ volatile("" ::: "memory"); }
}
__attribute__((noinline)) void sample_middle(void) { sample_leaf(); sink++; }
__attribute__((noinline)) void sample_outer(void) { sample_middle(); sink++; }
static DWORD WINAPI sampling_thread(void *arg) {
    CHECK(SetThreadDescription(GetCurrentThread(), L"mkw_vk_fixture") == S_OK);
    atomic_store(&profile_guest, darwin_thread_id(GetCurrentThread()));
    if (arg) sample_frameless();
    else sample_outer();
    return 0;
}
static void test_sampling(int profile) {
    HANDLE h = CreateThread(NULL, 0, sampling_thread, NULL, 0, NULL);
    CHECK(h);
    until(&sampling_ready);
    DarwinProfileThread threads[128];
    int omitted;
    const int n = darwin_prof_threads(threads, 128, &omitted);
    CHECK(n > 0 && !omitted);
    int found = 0;
    for (int i = 0; i < n; i++)
        if (threads[i].id == darwin_thread_id(h)) {
            CHECK(!strcmp(threads[i].name, "mkw_vk_fixture"));
            found++;
        }
    CHECK(found == 1);
    darwin_prof_release(threads, n);
    unsigned interrupted = 0, callers = 0;
    for (int i = 0; i < 100; i++) {
        DarwinSample sample;
        const int frames = darwin_prof_sample(GetThreadId(h), &sample);
        CHECK(frames >= 3 && sample.error == KERN_SUCCESS);
        interrupted += symbol_is(sample.pc[0], (const void *)sample_leaf);
        int middle = 0, outer = 0;
        for (int k = 1; k < frames; k++) {
            middle |= symbol_is(sample.pc[k], (const void *)sample_middle);
            outer |= symbol_is(sample.pc[k], (const void *)sample_outer);
        }
        callers += middle && outer;
    }
    printf("framed_sampling interrupted_PC=%u caller_chains=%u required=100\n", interrupted, callers);
    CHECK(interrupted == 100 && callers == 100);
    DarwinSample self;
    CHECK(!darwin_prof_sample(GetCurrentThreadId(), &self) && self.error == KERN_INVALID_ARGUMENT);
    if (profile) {
        hos_prof_start();
        const int64_t end = hos_now_ns() + 5000000000LL;
        while (atomic_load(&log_seen) != 15 && hos_now_ns() < end) Sleep(20);
        CHECK(atomic_load(&log_seen) == 15);
    }
    atomic_store(&sampling_end, 1);
    CHECK(WaitForSingleObject(h, 5000) == WAIT_OBJECT_0 && CloseHandle(h));
    printf("sampling requested=100 interrupted_PC=%u caller_chains=%u self_refused=1 live_profiler=%d\n", interrupted, callers, profile);
    atomic_store(&sampling_ready, 0);
    atomic_store(&sampling_end, 0);
    h = CreateThread(NULL, 0, sampling_thread, (void *)(uintptr_t)1, 0, NULL);
    CHECK(h);
    until(&sampling_ready);
    unsigned frameless = 0;
    for (int i = 0; i < 100; i++) {
        DarwinSample sample;
        CHECK(darwin_prof_sample(GetThreadId(h), &sample) >= 1);
        frameless += symbol_is(sample.pc[0], (const void *)sample_frameless);
    }
    atomic_store(&sampling_end, 1);
    CHECK(WaitForSingleObject(h, 5000) == WAIT_OBJECT_0 && CloseHandle(h));
    printf("frameless_sampling interrupted_PC=%u required=100 omitted_callers=not_claimed\n", frameless);
    CHECK(frameless == 100);
}
static void test_modules(void) {
    char path[MAX_PATH], loaded[MAX_PATH];
    const DWORD n = GetModuleFileNameA(NULL, path, sizeof path);
    CHECK(n && n < sizeof path && path[0] == '/');
    CHECK(GetModuleFileNameA(GetModuleHandleA(NULL), loaded, sizeof loaded) == n && !strcmp(path, loaded));
    char short_path[4];
    CHECK(GetModuleFileNameA(NULL, short_path, sizeof short_path) == sizeof short_path && short_path[3] == 0);
    CHECK(GetLastError() == ERROR_INSUFFICIENT_BUFFER);
    HMODULE m = LoadLibraryA("SDL2.dll");  /* authored marker dylib beside this fixture, never an installed SDL */
    CHECK(m);
    int (*marker)(void) = (int (*)(void))(void *)GetProcAddress(m, "darwin_host_library_marker");
    CHECK(marker && marker() == 314);
    CHECK(GetModuleFileNameA(m, loaded, sizeof loaded));
    char *name = strrchr(path, '/');
    CHECK(name);
    strcpy(name + 1, "libSDL2-2.0.0.dylib");
    CHECK(!strcmp(path, loaded));
    HMODULE base;
    CHECK(GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT, (const char *)(void *)marker, &base));
    CHECK(GetModuleFileNameA(base, loaded, sizeof loaded) && !strcmp(path, loaded));
    HMODULE again = LoadLibraryA(path);
    CHECK(again == m && FreeLibrary(again) && marker() == 314 && FreeLibrary(m));
    /* hos_load_error (host/rx_backend.c logs it): dlerror()'s text for a library that did not load, cleared by the next call */
    strcpy(name + 1, "absent_vk.dll");
    CHECK(!LoadLibraryA(path) && GetLastError() == ERROR_FILE_NOT_FOUND);
    strcpy(name + 1, "absent_vk.dylib");
    CHECK(hos_load_error()[0] && strstr(hos_load_error(), path));
    CHECK(!LoadLibraryA("kernel32.dll") && GetLastError() == ERROR_FILE_NOT_FOUND && !hos_load_error()[0]);
    strcpy(name + 1, "absent_vk.dll");
    CHECK(!LoadLibraryA(path) && hos_load_error()[0]);
    strcpy(name + 1, "libSDL2-2.0.0.dylib");
    m = LoadLibraryA(path);
    CHECK(m && !hos_load_error()[0] && FreeLibrary(m));
    puts("modules executable=dyld_path truncated=reported opaque_handle=recorded address_base=dladdr packaged_alias=preferred "
         "load_error=dlerror_text");
}
static void test_memory(void) {
    PROCESS_MEMORY_COUNTERS_EX memory;
    CHECK(GetProcessMemoryInfo(GetCurrentProcess(), (PROCESS_MEMORY_COUNTERS *)&memory, sizeof memory));
    CHECK(memory.cb == sizeof memory && memory.WorkingSetSize && memory.PeakWorkingSetSize && memory.PrivateUsage);
    CHECK(memory.PagefileUsage == memory.PrivateUsage);
    CHECK(!GetProcessMemoryInfo(GetCurrentProcess(), (PROCESS_MEMORY_COUNTERS *)&memory, sizeof(DWORD)));
    CHECK(GetLastError() == ERROR_INVALID_PARAMETER);
    puts("memory resident=queried peak=Darwin_bytes private=ledger_footprint short_buffer=refused");
}
int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc == 2 && !strcmp(argv[1], "--profile")) test_sampling(1);
    else {
        test_threads();
        test_fibers();
        test_modules();
        test_memory();
        test_sampling(0);
    }
    puts("darwin_host PASS");
    return 0;
}
