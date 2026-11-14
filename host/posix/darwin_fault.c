/* Darwin signal ABI: sys/_types/_ucontext.h and mach/{arm,i386}/_structs.h.
   Access bits: ARM ESR_EL1 data-abort WnR / instruction-abort EC; Intel page-fault
   error code W/R and I/D. See sigaction(2) for the return and masking contract. */
#if defined(__APPLE__)
#include "darwin_fault.h"

void darwin_fault_context_from(CONTEXT *c, const ucontext_t *uc) {
    memset(c, 0, sizeof *c);
#if defined(__x86_64__)
    const struct __darwin_x86_thread_state64 *s = &uc->uc_mcontext->__ss;
    c->Rax = s->__rax, c->Rcx = s->__rcx, c->Rdx = s->__rdx, c->Rbx = s->__rbx;
    c->Rsp = s->__rsp, c->Rbp = s->__rbp, c->Rsi = s->__rsi, c->Rdi = s->__rdi;
    c->R8 = s->__r8, c->R9 = s->__r9, c->R10 = s->__r10, c->R11 = s->__r11;
    c->R12 = s->__r12, c->R13 = s->__r13, c->R14 = s->__r14, c->R15 = s->__r15;
    c->Rip = s->__rip, c->EFlags = (DWORD)s->__rflags, c->ContextFlags = CONTEXT_FULL;
#elif defined(__aarch64__)
    c->Rip = uc->uc_mcontext->__ss.__pc;
    c->Rsp = uc->uc_mcontext->__ss.__sp;
    c->ContextFlags = CONTEXT_CONTROL;
#else
#error Unsupported Darwin fault context architecture
#endif
}

void darwin_fault_context_to(ucontext_t *uc, const CONTEXT *c) {
#if defined(__x86_64__)
    struct __darwin_x86_thread_state64 *s = &uc->uc_mcontext->__ss;
    s->__rax = c->Rax, s->__rcx = c->Rcx, s->__rdx = c->Rdx, s->__rbx = c->Rbx;
    s->__rsp = c->Rsp, s->__rbp = c->Rbp, s->__rsi = c->Rsi, s->__rdi = c->Rdi;
    s->__r8 = c->R8, s->__r9 = c->R9, s->__r10 = c->R10, s->__r11 = c->R11;
    s->__r12 = c->R12, s->__r13 = c->R13, s->__r14 = c->R14, s->__r15 = c->R15;
    s->__rip = c->Rip, s->__rflags = c->EFlags;
#elif defined(__aarch64__)
    uc->uc_mcontext->__ss.__pc = c->Rip;
    uc->uc_mcontext->__ss.__sp = c->Rsp;
#endif
}

ULONG_PTR darwin_fault_access(const ucontext_t *uc) {
#if defined(__x86_64__)
    if (uc->uc_mcontext->__es.__trapno == 14) {   /* page fault, not an unrelated trap's error bits */
        const unsigned err = uc->uc_mcontext->__es.__err;
        return (err & 16) ? 8 : (err & 2) ? 1 : 0;
    }
#elif defined(__aarch64__)
    const unsigned esr = uc->uc_mcontext->__es.__esr, ec = esr >> 26;
    if (ec == 0x20 || ec == 0x21) return 8;   /* instruction abort, lower/same exception level */
    if (ec == 0x24 || ec == 0x25) return (esr >> 6) & 1;   /* data abort: WnR */
#endif
    return 0;
}

static void (*dispatch_fault)(int, siginfo_t *, void *);
static __thread volatile sig_atomic_t dispatching;

static void darwin_fault_signal(int sig, siginfo_t *si, void *raw) {
    if (dispatching) _exit(128 + sig);
    dispatching = 1;
    dispatch_fault(sig, si, raw);
    dispatching = 0;
}

void darwin_fault_action(struct sigaction *sa) {
    /* Blocking a synchronous Darwin fault can keep retrying it, including when
       the callback's stack becomes inaccessible. Allow delivery, but never call
       the bridge recursively. A failed nested stack delivery is left to the OS. */
    (void)dispatching;   /* initialize this installing thread's TLS before delivery */
    dispatch_fault = sa->sa_sigaction;
    sa->sa_sigaction = darwin_fault_signal;
    sa->sa_flags = SA_SIGINFO | SA_ONSTACK | SA_NODEFER;
    sigemptyset(&sa->sa_mask);
}
#endif
