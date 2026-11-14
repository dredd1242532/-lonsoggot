/* SPDX-License-Identifier: GPL-3.0-or-later */
#pragma once
#if defined(__APPLE__)
#include <mach/mach.h>
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
enum { DARWIN_SAMPLE_DEPTH = 48 };
enum DarwinSampleStop {
    DARWIN_SAMPLE_COMPLETE, DARWIN_SAMPLE_NO_FRAME, DARWIN_SAMPLE_INVALID_FRAME,
    DARWIN_SAMPLE_READ_FAILED, DARWIN_SAMPLE_DEPTH_LIMIT
};
typedef struct {
    uintptr_t pc[DARWIN_SAMPLE_DEPTH];
    int count;
    enum DarwinSampleStop stop;
    kern_return_t error;
} DarwinSample;
typedef struct {
    thread_t port;                  /* owned send right: release with darwin_prof_release */
    uint64_t id, cpu_ns;            /* the native 64-bit identity, never a DWORD or pid_t */
    char name[64];
    int guest;
} DarwinProfileThread;
/* Native task snapshot. Negative errno on failure; omitted counts threads beyond capacity. */
int darwin_prof_threads(DarwinProfileThread *out, int capacity, int *omitted);
void darwin_prof_release(DarwinProfileThread *threads, int count);
/* Briefly suspend exactly this thread, capture its interrupted PC/SP/FP, read validated frame records,
   then resume before any allocator, logger or symbol lookup. Self-sampling is refused. The first PC is
   not guessed by searching an unwinder's signal trampoline. Omitted-frame-pointer callers can be absent. */
int darwin_prof_sample(thread_t thread, DarwinSample *out);
/* hos_thread.c: copy the guest label while its owning thread record is locked. */
int hos_darwin_guest_thread_name(uint64_t id, char *out, size_t cap);
void hos_prof_start(void);
#ifdef __cplusplus
}
#endif
#endif
