/* SPDX-License-Identifier: GPL-3.0-or-later */
/* macOS counterpart of prof.c. CPU times and identities come from Mach; libproc supplies kernel names.
   Sampling starts at thread_get_state's interrupted PC and walks safe, monotonic native frame records.
   _Unwind_Backtrace in a signal handler cannot find that PC on Darwin (the retained 0/100 finding).
   No signal disposition, fault handler, hardware watchpoint, TLS register, or target context is changed. */
#if defined(__APPLE__)
#include "darwin_prof.h"
#include "darwin_wait.h"
#include <dlfcn.h>
#include <errno.h>
#include <inttypes.h>
#include <libproc.h>
#include <limits.h>
#include <mach/mach_vm.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <windows.h>
#if defined(__aarch64__)
#include <mach/arm/thread_status.h>
#elif defined(__x86_64__)
#include <mach/i386/thread_status.h>
#else
#error "Darwin profiling requires arm64 or x86_64"
#endif

void hos_log(const char *format, ...);
int64_t hos_now_ns(void);

int darwin_prof_threads(DarwinProfileThread *out, int capacity, int *omitted) {
    if (!out || capacity < 1 || !omitted) return -EINVAL;
    *omitted = 0;
    thread_act_array_t ports = NULL;
    mach_msg_type_number_t count = 0;
    if (task_threads(mach_task_self(), &ports, &count) != KERN_SUCCESS) return -EIO;
    int n = 0;
    for (mach_msg_type_number_t i = 0; i < count; i++) {
        thread_identifier_info_data_t identity;
        thread_basic_info_data_t basic;
        mach_msg_type_number_t ni = THREAD_IDENTIFIER_INFO_COUNT, nb = THREAD_BASIC_INFO_COUNT;
        if (n >= capacity) (*omitted)++;
        else if (thread_info(ports[i], THREAD_IDENTIFIER_INFO, (thread_info_t)&identity, &ni) == KERN_SUCCESS &&
                 thread_info(ports[i], THREAD_BASIC_INFO, (thread_info_t)&basic, &nb) == KERN_SUCCESS) {
            DarwinProfileThread *t = &out[n++];
            memset(t, 0, sizeof *t);
            t->port = ports[i];
            t->id = identity.thread_id;
            t->cpu_ns = ((uint64_t)basic.user_time.seconds + (uint64_t)basic.system_time.seconds) * 1000000000ULL +
                        ((uint64_t)basic.user_time.microseconds + (uint64_t)basic.system_time.microseconds) * 1000;
            struct proc_threadinfo info;
            if (proc_pidinfo(getpid(), PROC_PIDTHREADID64INFO, t->id, &info, sizeof info) == sizeof info)
                snprintf(t->name, sizeof t->name, "%.*s", (int)sizeof info.pth_name, info.pth_name);
            continue;              /* this send right belongs to the snapshot now */
        }
        mach_port_deallocate(mach_task_self(), ports[i]);
    }
    vm_deallocate(mach_task_self(), (vm_address_t)ports, (vm_size_t)count * sizeof *ports);
    return n;
}
void darwin_prof_release(DarwinProfileThread *threads, int count) {
    for (int i = 0; i < count; i++) {
        if (threads[i].port) mach_port_deallocate(mach_task_self(), threads[i].port);
        threads[i].port = MACH_PORT_NULL;
    }
}

/* Only one sampler can suspend at a time: two profiling callers must never suspend each other while
   holding the other's sampling lock. The lazy Mach stubs are resolved before suspending any target. */
static pthread_mutex_t sample_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_once_t sample_once = PTHREAD_ONCE_INIT;
static void warm_sample_calls(void) {
    uintptr_t value = 0, copy = 0;
    mach_vm_size_t copied = 0;
    (void)mach_vm_read_overwrite(mach_task_self(), (mach_vm_address_t)&value, sizeof value,
                                 (mach_vm_address_t)&copy, &copied);
    natural_t state[256];
    mach_msg_type_number_t count = 256;
    (void)thread_get_state(MACH_PORT_NULL, 0, state, &count);
    (void)thread_suspend(MACH_PORT_NULL);
    (void)thread_resume(MACH_PORT_NULL);
}
static void walk_frames(DarwinSample *out, uintptr_t pc, uintptr_t sp, uintptr_t fp) {
    if (!pc) { out->error = KERN_INVALID_ADDRESS; return; }
    out->pc[out->count++] = pc;
    if (!fp) { out->stop = DARWIN_SAMPLE_NO_FRAME; return; }
    while (fp && out->count < DARWIN_SAMPLE_DEPTH) {
        if (fp < sp || (fp & (sizeof(uintptr_t) - 1)) || fp > UINTPTR_MAX - 2 * sizeof(uintptr_t)) {
            out->stop = DARWIN_SAMPLE_INVALID_FRAME;
            return;
        }
        uintptr_t frame[2];
        mach_vm_size_t copied = 0;
        const kern_return_t e = mach_vm_read_overwrite(mach_task_self(), fp, sizeof frame,
                                                       (mach_vm_address_t)frame, &copied);
        if (e != KERN_SUCCESS || copied != sizeof frame) {
            out->stop = DARWIN_SAMPLE_READ_FAILED;
            return;
        }
        if (!frame[1]) return;
        out->pc[out->count++] = frame[1];
        if (frame[0] && frame[0] <= fp) { out->stop = DARWIN_SAMPLE_INVALID_FRAME; return; }
        sp = fp + sizeof frame;
        fp = frame[0];
    }
    if (fp) out->stop = DARWIN_SAMPLE_DEPTH_LIMIT;
}
int darwin_prof_sample(thread_t thread, DarwinSample *out) {
    if (!out) return 0;
    memset(out, 0, sizeof *out);
    if (!thread || thread == pthread_mach_thread_np(pthread_self())) {
        out->error = KERN_INVALID_ARGUMENT;
        return 0;
    }
    pthread_once(&sample_once, warm_sample_calls);
    pthread_mutex_lock(&sample_mu);
    out->error = thread_suspend(thread);
    if (out->error == KERN_SUCCESS) {
#if defined(__aarch64__)
        arm_thread_state64_t state;
        mach_msg_type_number_t count = ARM_THREAD_STATE64_COUNT;
        out->error = thread_get_state(thread, ARM_THREAD_STATE64, (thread_state_t)&state, &count);
        if (out->error == KERN_SUCCESS)
            walk_frames(out, arm_thread_state64_get_pc(state), arm_thread_state64_get_sp(state), arm_thread_state64_get_fp(state));
#else
        x86_thread_state64_t state;
        mach_msg_type_number_t count = x86_THREAD_STATE64_COUNT;
        out->error = thread_get_state(thread, x86_THREAD_STATE64, (thread_state_t)&state, &count);
        if (out->error == KERN_SUCCESS) walk_frames(out, state.__rip, state.__rsp, state.__rbp);
#endif
        const kern_return_t resumed = thread_resume(thread);
        if (resumed != KERN_SUCCESS) out->error = resumed;
    }
    pthread_mutex_unlock(&sample_mu);
    return out->error == KERN_SUCCESS ? out->count : 0;
}

static int tasks(DarwinProfileThread *out, int cap) {
    int omitted;
    const int n = darwin_prof_threads(out, cap, &omitted);
    if (n < 0) { hos_log("prof: Darwin thread snapshot failed (%s)", strerror(-n)); return 0; }
    if (omitted) hos_log("prof: Darwin thread snapshot omitted %d threads (capacity %d)", omitted, cap);
    for (int i = 0; i < n; i++) {
        char name[64];
        out[i].guest = hos_darwin_guest_thread_name(out[i].id, name, sizeof name);
        if (out[i].guest || darwin_thread_name(out[i].id, name, sizeof name))
            snprintf(out[i].name, sizeof out[i].name, "%s", name);
        if (!out[i].name[0]) snprintf(out[i].name, sizeof out[i].name, "tid %" PRIu64, out[i].id);
    }
    return n;
}
static const char *module(uintptr_t pc, uintptr_t *base) {
    Dl_info info;
    *base = 0;
    if (!dladdr((const void *)pc, &info) || !info.dli_fbase) return "?";
    *base = (uintptr_t)info.dli_fbase;
    const char *path = info.dli_fname ? info.dli_fname : "?";
    const char *slash = strrchr(path, '/');
    return slash ? slash + 1 : path;
}
static void write_stack(FILE *f, const DarwinSample *sample) {
    for (int i = 0; i < sample->count; i++) {
        uintptr_t base;
        const char *name = module(sample->pc[i], &base);
        fprintf(f, "%s%s+%" PRIxPTR, i ? ";" : "", name, sample->pc[i] - base);
    }
    fputc('\n', f);
}
enum { MAX_THREADS = 512, HOT_SLOTS = 4096 };
typedef struct { uint64_t id, cpu; } Previous;
typedef struct { Previous thread[MAX_THREADS]; int count; int64_t at; } Times;
static void log_times(DarwinProfileThread *threads, int n, Times *last, int64_t now, int busiest) {
    uint64_t delta[MAX_THREADS] = {0};
    int rank[MAX_THREADS];
    const int64_t elapsed = now - last->at;
    for (int i = 0; i < n; i++) {
        rank[i] = i;
        for (int j = 0; j < last->count; j++)
            if (threads[i].id == last->thread[j].id && threads[i].cpu_ns >= last->thread[j].cpu) {
                delta[i] = threads[i].cpu_ns - last->thread[j].cpu;
                break;
            }
    }
    for (int i = 0; i < n; i++) last->thread[i] = (Previous){threads[i].id, threads[i].cpu_ns};
    last->count = n;
    last->at = now;
    if (elapsed <= 0) return;
    char line[4096];
    int len = busiest ? snprintf(line, sizeof line, "prof: busiest threads (%% of a core):") :
                       snprintf(line, sizeof line, "prof: threads in %.0f s:", elapsed / 1e9);
    uint64_t total = 0;
    for (int i = 0; i < n; i++) total += delta[i];
    if (busiest)
        for (int i = 0; i < n && i < 10; i++)
            for (int j = i + 1; j < n; j++)
                if (delta[rank[j]] > delta[rank[i]]) { int swap = rank[i]; rank[i] = rank[j]; rank[j] = swap; }
    for (int k = 0; k < n && (!busiest || k < 10) && len < (int)sizeof line - 100; k++) {
        const int i = rank[k];
        const double percent = delta[i] * 100.0 / (double)elapsed;
        if (busiest || percent >= 5.0)
            len += snprintf(line + len, sizeof line - (size_t)len, " %s %.0f%%;", threads[i].name, percent);
    }
    if (busiest) snprintf(line + len, sizeof line - (size_t)len, " all %.0f", total * 100.0 / (double)elapsed);
    hos_log("%s", line);
}
typedef struct { uintptr_t pc; uint32_t count; } Hot;
static void add_hot(Hot *table, uintptr_t pc) {
    if (!pc) return;
    for (unsigned i = (unsigned)((pc >> 2) * 2654435761u) & (HOT_SLOTS - 1), k = 0; k < HOT_SLOTS; k++, i = (i + 1) & (HOT_SLOTS - 1)) {
        if (!table[i].pc || table[i].pc == pc) { table[i].pc = pc; table[i].count++; return; }
    }
}
static void log_hot(Hot *table, const char *which, unsigned total) {
    char line[2048];
    int len = snprintf(line, sizeof line, "prof-sample %s (%u samples):", which, total);
    for (int r = 0; r < 30 && len < (int)sizeof line - 112; r++) {
        int best = -1;
        for (int i = 0; i < HOT_SLOTS; i++) if (table[i].count && (best < 0 || table[i].count > table[best].count)) best = i;
        if (best < 0) break;
        uintptr_t base;
        const char *name = module(table[best].pc, &base);
        len += snprintf(line + len, sizeof line - (size_t)len, " %s+%" PRIxPTR " %u,", name, table[best].pc - base, table[best].count);
        table[best].count = 0;
    }
    hos_log("%s", line);
    memset(table, 0, HOT_SLOTS * sizeof *table);
}
typedef struct {
    uint64_t profile_ns, times_ns;
    char *want, *one_path, *all_path;
    int chain;
} Profile;
static void profile_free(Profile *p) { free(p->want); free(p->one_path); free(p->all_path); free(p); }
static DWORD WINAPI profile_thread(void *arg) {
    Profile *p = arg;
    SetThreadDescription(GetCurrentThread(), L"longshot-prof");
    const thread_t self = pthread_mach_thread_np(pthread_self());
    const uintptr_t executable = (uintptr_t)GetModuleHandleA(NULL);
    DarwinProfileThread threads[MAX_THREADS];
    int n = 0;
    Times busy = {0}, times = {0};
    Hot leaf[HOT_SLOTS] = {0}, ours[HOT_SLOTS] = {0};
    unsigned total = 0, failed = 0, partial = 0;
    kern_return_t last_error = 0;
    uint64_t chosen = 0;
    int64_t refresh = 0, go_check = 0, all_at = 0, all_start = 0, sample_log = hos_now_ns();
    FILE *one = p->one_path ? fopen(p->one_path, "w") : NULL;
    if (p->one_path && !one) hos_log("prof: cannot open sample file %s (%s)", p->one_path, strerror(errno));
    FILE *all = NULL;
    char *go = NULL;
    if (p->all_path) {
        const size_t len = strlen(p->all_path);
        go = malloc(len + 4);
        if (go) { memcpy(go, p->all_path, len); memcpy(go + len, ".go", 4); }
        else hos_log("prof: cannot allocate all-thread sampling marker path");
    }
    hos_log("prof: Darwin native 64-bit thread IDs; interrupted-PC/frame-pointer sampling (frames without frame records may be absent)");
    hos_log("prof: remote thread descriptions are retained by Longshot; only a thread can update its Darwin kernel name");
    for (;;) {
        const int64_t now = hos_now_ns();
        if (now >= refresh) {
            darwin_prof_release(threads, n);
            n = tasks(threads, MAX_THREADS);
            refresh = now + 1000000000LL;
            if (!busy.at) { busy.at = now; log_times(threads, n, &busy, now, 1); }
            if (!times.at) { times.at = now; log_times(threads, n, &times, now, 0); }
        }
        if (p->profile_ns && (uint64_t)(now - busy.at) >= p->profile_ns) log_times(threads, n, &busy, now, 1);
        if (p->times_ns && (uint64_t)(now - times.at) >= p->times_ns) log_times(threads, n, &times, now, 0);
        if (go && now >= go_check) {
            const int exists = access(go, F_OK) == 0;
            go_check = now + 200000000;
            if (!exists && all) { fclose(all); all = NULL; hos_log("prof: all-thread sampling stopped"); }
            if (exists && !all) {
                all = fopen(p->all_path, "a");
                if (!all) hos_log("prof: cannot open all-thread sample file %s (%s)", p->all_path, strerror(errno));
                else {
                    all_start = now;
                    fprintf(all, "# t0_ns %lld\n", (long long)all_start);
                    hos_log("prof: all-thread sampling into %s", p->all_path);
                }
            }
        }
        for (int i = 0; i < n; i++) {
            DarwinProfileThread *t = &threads[i];
            if (t->port == self) continue;
            const int single = p->want && strstr(t->name, p->want) && (!chosen || chosen == t->id);
            const int every = all && now >= all_at && (t->guest || !strncmp(t->name, "mkw_vk", 6));
            if (!single && !every) continue;
            if (single && !chosen) { chosen = t->id; hos_log("prof: sampling thread \"%s\" (native tid %" PRIu64 ")", t->name, chosen); }
            DarwinSample sample;
            const int count = darwin_prof_sample(t->port, &sample);
            if (!count) { failed++; last_error = sample.error; continue; }
            if (sample.stop != DARWIN_SAMPLE_COMPLETE) partial++;
            if (single) {
                total++;
                add_hot(leaf, sample.pc[0]);
                for (int k = 0; k < count; k++) {
                    uintptr_t base;
                    (void)module(sample.pc[k], &base);
                    if (base == executable) { add_hot(ours, sample.pc[k]); break; }
                }
                if (one) write_stack(one, &sample);
            }
            if (every) {
                if (p->chain) fprintf(all, "# at %lld\n", (long long)hos_now_ns());
                char name[64];
                snprintf(name, sizeof name, "%s", t->name);
                for (char *c = name; *c; c++) if (*c == ' ' || *c == '|') *c = '_';
                fprintf(all, "%lld %s|", (long long)((now - all_start) / 1000000), name);
                if (sample.count > 16) sample.count = 16;
                write_stack(all, &sample);
            }
        }
        if (now >= all_at) all_at = now + 2000000;
        const uint64_t log_interval = p->profile_ns ? p->profile_ns : 1000000000ULL;
        if ((uint64_t)(now - sample_log) >= log_interval) {
            if (p->want) { log_hot(leaf, "leaf", total); log_hot(ours, "ours", total); }
            if (failed || partial) hos_log("prof: Darwin sampling: %u failed (Mach error %d), %u incomplete frame chains", failed, last_error, partial);
            total = failed = partial = 0;
            sample_log = now;
            if (one) fflush(one);
            if (all) fflush(all);
        }
        Sleep(p->want || all ? 1 : 20);
    }
    return 0;
}
static uint64_t interval(const char *name) {
    const char *s = getenv(name);
    if (!s || !*s) return 0;
    char *end;
    errno = 0;
    const unsigned long long seconds = strtoull(s, &end, 10);
    if (errno || *end || *s == '-' || seconds > (uint64_t)INT64_MAX / 1000000000ULL) {
        hos_log("prof: invalid %s interval", name);
        return 0;
    }
    return seconds * 1000000000ULL;
}
static char *option(const char *name) {
    const char *s = getenv(name);
    return s && *s ? strdup(s) : NULL;
}
void hos_prof_start(void) {
    static unsigned started;
    unsigned idle = 0;
    if (!__atomic_compare_exchange_n(&started, &idle, 1, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) return;
    const char *watch = getenv("HOS_WATCH");
    if (watch && *watch) hos_log("watch: HOS_WATCH is unavailable on Darwin: no hardware data breakpoints were armed (requires a separately controlled debugger)");
    const uint64_t profile_ns = interval("HOS_PROFILE"), times_ns = interval("HOS_THREAD_TIMES");
    const char *want = getenv("HOS_SAMPLE"), *all = getenv("HOS_SAMPLE_ALL");
    if (want && *want && !profile_ns) hos_log("prof: HOS_SAMPLE needs a positive HOS_PROFILE interval");
    if (!profile_ns && !times_ns && !(all && *all)) return;
    Profile *p = calloc(1, sizeof *p);
    if (!p) { hos_log("prof: cannot allocate Darwin profiler state"); return; }
    p->profile_ns = profile_ns;
    p->times_ns = times_ns;
    p->chain = getenv("HOS_CHAIN") != NULL;
    if (profile_ns && want && *want) {
        p->want = option("HOS_SAMPLE");
        p->one_path = option("HOS_SAMPLE_FILE");
        const char *file = getenv("HOS_SAMPLE_FILE");
        if (file && *file && !p->one_path) {
            hos_log("prof: cannot retain HOS_SAMPLE_FILE");
            profile_free(p);
            return;
        }
    }
    p->all_path = option("HOS_SAMPLE_ALL");
    if ((profile_ns && want && *want && !p->want) || (all && *all && !p->all_path)) {
        hos_log("prof: cannot retain Darwin profiler options");
        profile_free(p);
        return;
    }
    HANDLE thread = CreateThread(NULL, 0, profile_thread, p, 0, NULL);
    if (!thread) { hos_log("prof: cannot start Darwin profiler (error %lu)", (unsigned long)GetLastError()); profile_free(p); }
    else CloseHandle(thread);
}
#endif
