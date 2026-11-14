/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Darwin's deprecated ucontext is not an arm64 fiber implementation. Save only the native callee-saved
   state, never x18/the pthread pointer, signal masks, or a previous thread's TLS. This is separate from
   recomp_sj_setjmp's ABI. A suspended fiber can migrate; a pthread's original stack cannot.

   The outgoing context stays claimed until its registers have been saved AND SP is on the destination
   stack. Publishing it earlier lets a second pthread resume a stack still being used by the first. */
#if defined(__APPLE__)
#include "darwin_fiber.h"
#include <errno.h>
#include <pthread.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>

#if defined(__arm64e__)
#error "Darwin fibers support arm64, not arm64e authenticated contexts"
#endif

typedef struct { uintptr_t word[32]; } FiberContext;
typedef struct Fiber {
    FiberContext context;
    void *mapping, *param, *local;
    size_t mapping_size, local_size;
    void (*entry)(void *);
    pthread_t home;                 /* nonzero only for the pthread's root */
    unsigned active;
} Fiber;
typedef struct {
    Fiber *root, *current, *ending;
    void *local;
    size_t local_size;
    void (*thread_exit)(uint32_t);
    uint32_t exit_code;
} FiberThread;
static _Thread_local FiberThread *local_thread;

static void dispose(Fiber *f) {
    if (f->mapping) munmap(f->mapping, f->mapping_size);
    free(f->local);
    free(f);
}
static void thread_dispose(void *p) {
    FiberThread *t = p;
    local_thread = NULL;
    /* ExitThread routes active user fibers through the root before pthread_exit. Suspended user fibers
       are independently owned by their handles and must survive this pthread (they may migrate). */
    if (t->root) dispose(t->root);
    free(t->local);
    free(t);
}
static pthread_key_t key;
static pthread_once_t key_once = PTHREAD_ONCE_INIT;
static int key_error;
static void make_key(void) { key_error = pthread_key_create(&key, thread_dispose); }
/* Out of line on purpose: a fiber switch may change the pthread while its C callers remain live. */
__attribute__((noinline)) static FiberThread *thread_state(int create) {
    __asm__ volatile("" ::: "memory");   /* never CSE a TLS read across a migrating context switch */
    if (local_thread || !create) return local_thread;
    pthread_once(&key_once, make_key);
    if (key_error) { errno = key_error; return NULL; }
    FiberThread *t = calloc(1, sizeof *t);
    if (!t) return NULL;
    const int e = pthread_setspecific(key, t);
    if (e) { free(t); errno = e; return NULL; }
    local_thread = t;
    return t;
}

#if defined(__aarch64__)
__attribute__((naked, noinline)) static void context_swap(FiberContext *from, const FiberContext *to, unsigned *release) {
    __asm__ volatile(
        "stp x19, x20, [x0, #0]\n\t"
        "stp x21, x22, [x0, #16]\n\t"
        "stp x23, x24, [x0, #32]\n\t"
        "stp x25, x26, [x0, #48]\n\t"
        "stp x27, x28, [x0, #64]\n\t"
        "stp x29, x30, [x0, #80]\n\t"
        "mov x9, sp\n\t"
        "str x9, [x0, #96]\n\t"
        "stp d8, d9, [x0, #112]\n\t"
        "stp d10, d11, [x0, #128]\n\t"
        "stp d12, d13, [x0, #144]\n\t"
        "stp d14, d15, [x0, #160]\n\t"
        "mrs x9, fpcr\n\t"
        "mrs x10, fpsr\n\t"
        "stp x9, x10, [x0, #176]\n\t"
        "ldp x19, x20, [x1, #0]\n\t"
        "ldp x21, x22, [x1, #16]\n\t"
        "ldp x23, x24, [x1, #32]\n\t"
        "ldp x25, x26, [x1, #48]\n\t"
        "ldp x27, x28, [x1, #64]\n\t"
        "ldp x29, x30, [x1, #80]\n\t"
        "ldr x9, [x1, #96]\n\t"
        "ldp d8, d9, [x1, #112]\n\t"
        "ldp d10, d11, [x1, #128]\n\t"
        "ldp d12, d13, [x1, #144]\n\t"
        "ldp d14, d15, [x1, #160]\n\t"
        "ldp x10, x11, [x1, #176]\n\t"
        "msr fpcr, x10\n\t"
        "msr fpsr, x11\n\t"
        "mov sp, x9\n\t"
        "stlr wzr, [x2]\n\t"
        "ret\n\t");
}
#elif defined(__x86_64__)
__attribute__((naked, noinline)) static void context_swap(FiberContext *from, const FiberContext *to, unsigned *release) {
    __asm__ volatile(
        "movq %rbx, 0(%rdi)\n\t"
        "movq %rbp, 8(%rdi)\n\t"
        "movq %r12, 32(%rdi)\n\t"
        "movq %r13, 40(%rdi)\n\t"
        "movq %r14, 48(%rdi)\n\t"
        "movq %r15, 56(%rdi)\n\t"
        "leaq 8(%rsp), %rax\n\t"
        "movq %rax, 64(%rdi)\n\t"
        "movq (%rsp), %rax\n\t"
        "movq %rax, 72(%rdi)\n\t"
        "stmxcsr 240(%rdi)\n\t"
        "fnstcw 244(%rdi)\n\t"
        "movq 0(%rsi), %rbx\n\t"
        "movq 8(%rsi), %rbp\n\t"
        "movq 32(%rsi), %r12\n\t"
        "movq 40(%rsi), %r13\n\t"
        "movq 48(%rsi), %r14\n\t"
        "movq 56(%rsi), %r15\n\t"
        "ldmxcsr 240(%rsi)\n\t"
        "fldcw 244(%rsi)\n\t"
        "movq 64(%rsi), %rsp\n\t"
        "movl $0, (%rdx)\n\t"   /* x86 stores release all earlier context stores */
        "jmpq *72(%rsi)\n\t");
}
#else
#error "Darwin fibers require arm64 or x86_64"
#endif

__attribute__((noreturn)) static void finish_exit(FiberThread *t) {
    const uint32_t code = t->exit_code;
    Fiber *ending = t->ending;
    t->ending = NULL;
    if (ending) dispose(ending);     /* now on the root, never on the mapping being released */
    t->thread_exit(code);
    abort();                        /* the registered ExitThread callback must not return */
}
__attribute__((noreturn)) static void fiber_entry(void) {
    FiberThread *t = thread_state(0);
    Fiber *f = t->current;
    f->entry(f->param);
    /* Returning from a fiber has the Win32 meaning: end its current pthread, not its creator. */
    darwin_fiber_before_thread_exit(0);
    abort();
}

void *darwin_fiber_convert(void *param, void (*thread_exit)(uint32_t)) {
    if (!thread_exit) { errno = EINVAL; return NULL; }
    FiberThread *t = thread_state(1);
    if (!t) return NULL;
    if (t->root) { errno = EALREADY; return NULL; }
    Fiber *f = calloc(1, sizeof *f);
    if (!f) return NULL;
    f->home = pthread_self();
    f->param = param;
    f->active = 1;
    f->local = t->local;
    f->local_size = t->local_size;
    t->local = NULL;
    t->local_size = 0;
    t->root = t->current = f;
    t->thread_exit = thread_exit;
    return f;
}
int darwin_fiber_unconvert(void) {
    FiberThread *t = thread_state(0);
    if (!t || !t->root || t->current != t->root) return EINVAL;
    t->local = t->root->local;
    t->local_size = t->root->local_size;
    free(t->root);
    t->root = t->current = NULL;
    return 0;
}
void *darwin_fiber_create(size_t size, void (*entry)(void *), void *param) {
    if (!entry) { errno = EINVAL; return NULL; }
    const long native_page = sysconf(_SC_PAGESIZE);
    if (native_page <= 0) { errno = EINVAL; return NULL; }
    const size_t page = (size_t)native_page;
    if (size < (1u << 20)) size = 1u << 20;
    if (size > SIZE_MAX - 3 * page) { errno = ENOMEM; return NULL; }
    size = (size + page - 1) / page * page;
    const size_t total = size + 2 * page;
    void *mapping = mmap(NULL, total, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mapping == MAP_FAILED) return NULL;
    if (mprotect((char *)mapping + page, size, PROT_READ | PROT_WRITE)) {
        const int e = errno;
        munmap(mapping, total);
        errno = e;
        return NULL;
    }
    Fiber *f = calloc(1, sizeof *f);
    if (!f) { munmap(mapping, total); errno = ENOMEM; return NULL; }
    f->mapping = mapping;
    f->mapping_size = total;
    f->entry = entry;
    f->param = param;
    const uintptr_t top = ((uintptr_t)mapping + page + size) & ~(uintptr_t)15;
#if defined(__aarch64__)
    f->context.word[11] = (uintptr_t)fiber_entry;
    f->context.word[12] = top;
    __asm__ volatile("mrs %0, fpcr" : "=r"(f->context.word[22]));
    __asm__ volatile("mrs %0, fpsr" : "=r"(f->context.word[23]));
#else
    f->context.word[8] = top - 8;     /* a SysV function enters with RSP + 8 aligned to 16 */
    f->context.word[9] = (uintptr_t)fiber_entry;
    *(uintptr_t *)(top - 8) = 0;
    __asm__ volatile("stmxcsr %0" : "=m"(*(uint32_t *)((char *)&f->context + 240)));
    __asm__ volatile("fnstcw %0" : "=m"(*(uint16_t *)((char *)&f->context + 244)));
#endif
    return f;
}
int darwin_fiber_switch(void *p) {
    FiberThread *t = thread_state(0);
    Fiber *to = p;
    if (!t || !t->root || !to) return EINVAL;
    Fiber *from = t->current;
    if (from == to) return 0;
    if (to->home && !pthread_equal(to->home, pthread_self())) return EPERM;
    unsigned idle = 0;
    if (!__atomic_compare_exchange_n(&to->active, &idle, 1, 0, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED)) return EBUSY;
    t->current = to;
    context_swap(&from->context, &to->context, &from->active);
    /* Do not use t here: this frame may now be executing on a different pthread. */
    FiberThread *resumed = thread_state(0);
    if (resumed->ending) finish_exit(resumed);
    return 0;
}
void darwin_fiber_before_thread_exit(uint32_t code) {
    FiberThread *t = thread_state(0);
    if (!t || !t->root || t->current == t->root) return;
    t->ending = t->current;
    t->exit_code = code;
    if (darwin_fiber_switch(t->root)) abort();
    abort();                        /* this ending fiber is never resumed */
}
int darwin_fiber_delete(void *p) {
    Fiber *f = p;
    if (!f) return 0;
    FiberThread *t = thread_state(0);
    if (t && t->current == f) {
        if (f == t->root) t->thread_exit(0);
        darwin_fiber_before_thread_exit(0);
        abort();
    }
    unsigned idle = 0;
    if (f->home || !__atomic_compare_exchange_n(&f->active, &idle, 1, 0, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED)) return EBUSY;
    dispose(f);
    return 0;
}
void *darwin_fiber_current(void) {
    FiberThread *t = thread_state(0);
    return t ? t->current : NULL;
}
void *darwin_fiber_data(void) {
    Fiber *f = darwin_fiber_current();
    return f ? f->param : NULL;
}
void *darwin_fiber_local(size_t size) {
    FiberThread *t = thread_state(size != 0);
    if (!t) return NULL;
    void **local = t->current ? &t->current->local : &t->local;
    if (!size) return *local;
    size_t *local_size = t->current ? &t->current->local_size : &t->local_size;
    if (*local && *local_size != size) { errno = EINVAL; return NULL; }
    if (!*local) {
        *local = calloc(1, size);
        if (*local) *local_size = size;
    }
    return *local;
}
#endif
