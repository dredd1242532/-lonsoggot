/* Game-free host nonlocal returns. Linked to the real hos_setjmp.c object, not an extracted replacement.
   The nested-slot loop is the day-one CPU probe; the assembly fixture independently controls register traffic. */
#include "hos.h"
#include <inttypes.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <windows.h>

__attribute__((returns_twice)) int recomp_sj_setjmp(void **);
void hos_setjmp_return(Ctx *, uint64_t);

/* Assembly writes this exact layout; assertions keep a C edit from silently moving its offsets. */
typedef struct {
    uint64_t gpr[10], fp[2], sp[2], platform[2], vector[8];
    uint64_t first, second, control[2], auxiliary[2], chain[2], lr[2];
} Observed;
_Static_assert(offsetof(Observed, fp) == 80, "assembly fp offset");
_Static_assert(offsetof(Observed, sp) == 96, "assembly sp offset");
_Static_assert(offsetof(Observed, platform) == 112, "assembly platform offset");
_Static_assert(offsetof(Observed, vector) == 128, "assembly vector offset");
_Static_assert(offsetof(Observed, first) == 192, "assembly first return offset");
_Static_assert(offsetof(Observed, second) == 200, "assembly second return offset");
_Static_assert(offsetof(Observed, control) == 208, "assembly control offset");
_Static_assert(offsetof(Observed, auxiliary) == 224, "assembly auxiliary offset");
_Static_assert(offsetof(Observed, chain) == 240, "assembly chain offset");
_Static_assert(offsetof(Observed, lr) == 256, "assembly lr offset");
void arm64_host_registers(void **, Ctx *, const uint64_t *, Observed *);

static const uint64_t patterns[18] = {
    UINT64_C(0x19ab23cd45ef6789), UINT64_C(0x20ba32dc54fe9876),
    UINT64_C(0x21ac35df47e968ba), UINT64_C(0x22ca53fd74e986ab),
    UINT64_C(0x23bd46ea58f079cb), UINT64_C(0x24db64ae85f097bc),
    UINT64_C(0x25ce57fb69018adc), UINT64_C(0x26ec75bf9601a8cd),
    UINT64_C(0x27df680c7a129bed), UINT64_C(0x28fd86c0a712b9de),
    UINT64_C(0x3847566574839201), UINT64_C(0x4958677685940312),
    UINT64_C(0x5a69788796051423), UINT64_C(0x6b7a899807162534),
    UINT64_C(0x7c8b9a0918273645), UINT64_C(0x8d9cab1a29384756),
    UINT64_C(0x9eadbc2b3a495867), UINT64_C(0xafbecd3c4b5a6978)
};
static Ctx ctx;
static unsigned count, failures;
#if defined(__aarch64__)
static unsigned value_case;
#endif

/* This executable has no host logger, game or device. Keep the real jump lookup's diagnostic observable. */
void hos_log(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    putchar('\n');
    va_end(ap);
}

static void equal(const char *name, uint64_t actual, uint64_t expected) {
    if (actual != expected) {
        printf("FAIL %s actual=%#" PRIx64 " expected=%#" PRIx64 "\n", name, actual, expected);
        ++failures;
    }
}

static void nested_returns(void) {
    const unsigned before = failures;
    for (count = 0; count < 64; ++count) {
        void **slot = recomp_sj_push(&ctx, 0x2000, 0x2004, 0, 0);
        const int returned = recomp_sj_setjmp(slot);
        if (!returned) {
            (void)recomp_sj_push(&ctx, 0x3000, 0x3004, 0, 0);
            ctx.x[0] = count + 1;
            hos_setjmp_return(&ctx, 0x2004);
            ++failures;
        } else {
            equal("nested return", returned, 1);
            equal("nested target", recomp_sj_target(slot), 0x2004);
            equal("guest return value", ctx.x[0], count + 1);
        }
        recomp_sj_pop(slot);
    }
    printf("jump_owned nested_returns=%u failures=%u\n", count, failures - before);
}

static void register_returns(void) {
    Observed got;
    memset(&got, 0xff, sizeof got);
    const unsigned before = failures;
    void **slot = recomp_sj_push(&ctx, 0x5004, 0, 0, 0);
    arm64_host_registers(slot, &ctx, patterns, &got);
    recomp_sj_pop(slot);
#if defined(__aarch64__)
    const unsigned gprs = 10, vectors = 8;
    const char *arch = "arm64";
#else
    const unsigned gprs = 5, vectors = 0;
    const char *arch = "x86_64";
#endif
    for (unsigned i = 0; i < gprs; ++i) {
        char name[32];
#if defined(__aarch64__)
        snprintf(name, sizeof name, "x%u", 19 + i);
#else
        static const char *names[] = {"rbx", "r12", "r13", "r14", "r15"};
        snprintf(name, sizeof name, "%s", names[i]);
#endif
        equal(name, got.gpr[i], patterns[i]);
    }
    for (unsigned i = 0; i < vectors; ++i) {
        char name[32];
        snprintf(name, sizeof name, "d%u", 8 + i);
        equal(name, got.vector[i], patterns[10 + i]);
    }
    equal("frame pointer", got.fp[1], got.fp[0]);
    equal("stack pointer", got.sp[1], got.sp[0]);
    equal("stack alignment", got.sp[1] & 15, 0);
    equal("frame chain", got.chain[1], got.chain[0]);
    if (!got.chain[1] || got.fp[1] < got.sp[1] || got.fp[1] - got.sp[1] > 256) ++failures;
    equal("initial return", got.first, 0);
    equal("second return", got.second, 1);
    equal("FPCR / MXCSR", got.control[1], got.control[0]);
    equal("FPSR / x87 control", got.auxiliary[1], got.auxiliary[0]);
#if defined(__aarch64__)
    equal("reserved x18", got.platform[1], got.platform[0]);
    equal("link register", got.lr[1], got.lr[0]);
#endif
    printf("jump_registers arch=%s gpr=%u vector=%u returns=%" PRIu64 ",%" PRIu64
           " sp=%d fp=%d chain=%d fp_control=%d fp_auxiliary=%d failures=%u\n",
           arch, gprs + 1, vectors, got.first, got.second,
           got.sp[1] == got.sp[0], got.fp[1] == got.fp[0], got.chain[1] == got.chain[0],
           got.control[1] == got.control[0], got.auxiliary[1] == got.auxiliary[0], failures - before);
#if defined(__aarch64__)
    printf("jump_platform x18=%d lr=%d\n", got.platform[1] == got.platform[0], got.lr[1] == got.lr[0]);
#endif
}

#if defined(__aarch64__)
static void return_values(void) {
    static const int values[] = {0, 1, -7, INT_MIN, INT_MAX};
    const unsigned before = failures;
    /* An eight-byte-aligned buffer, like SjSlot.buf at offset 40, need not be sixteen-byte aligned. */
    _Alignas(16) struct { uint64_t guard_before; void *buf[32]; uint64_t guard_after; } env;
    env.guard_before = UINT64_C(0xabcdef0123456789);
    env.guard_after = UINT64_C(0x9876543210fedcba);
    equal("buffer alignment fixture", (uintptr_t)env.buf & 15, 8);
    for (value_case = 0; value_case < sizeof values / sizeof values[0]; ++value_case) {
        const int got = recomp_sj_setjmp(env.buf);
        if (!got) recomp_sj_longjmp(env.buf, values[value_case]);
        equal("longjmp value", (uint64_t)(int64_t)got,
              (uint64_t)(int64_t)(values[value_case] ? values[value_case] : 1));
        equal("buffer guard before", env.guard_before, UINT64_C(0xabcdef0123456789));
        equal("buffer guard after", env.guard_after, UINT64_C(0x9876543210fedcba));
    }
    printf("jump_values cases=%u buffer_bytes=%zu failures=%u\n", value_case, sizeof env.buf, failures - before);
}
#endif

int main(void) {
    setvbuf(stdout, NULL, _IONBF, 0);
    nested_returns();
    register_returns();
#if defined(__aarch64__)
    return_values();
#endif
    YieldProcessor();
    printf("host_cpu failures=%u\n", failures);
    return failures != 0;
}
