/* Darwin's CPU-specific host operations. The x86 path keeps the existing pause instruction. */
#ifndef LONGSHOT_DARWIN_CPU_H
#define LONGSHOT_DARWIN_CPU_H
#if defined(__APPLE__)
#ifdef __cplusplus
extern "C" {
#endif

#if defined(__aarch64__)
/* The caller, not a wrapper around this call, must retain the returns_twice declaration. */
__attribute__((returns_twice)) int recomp_sj_setjmp(void **buf);
__attribute__((noreturn)) void recomp_sj_longjmp(void **buf, int value);
#endif

static inline __attribute__((always_inline)) void darwin_cpu_pause(void) {
#if defined(__aarch64__)
    __asm__ volatile("yield");
#else
    __builtin_ia32_pause();
#endif
}

#ifdef __cplusplus
}
#endif
#endif
#endif
