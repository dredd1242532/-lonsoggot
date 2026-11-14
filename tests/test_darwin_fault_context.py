"""Darwin fault bridge: real owned-page faults, signal return, chaining and a faulting handler stack.

Extracts the exception section of win32_posix.c verbatim and links darwin_fault.c when present. --source lets the
same check compile main's unported bridge (it must fail there, not skip a missing helper). No game or GPU needed.
By default Apple clang builds the running Python process's architecture; repeated --arch options select targets
explicitly. arch executes x86_64 under Rosetta on Apple Silicon. One child per target intentionally dies on its
handler's protected stack, with core dumps disabled; macOS can still write crash reports.

python -I -B tests/test_darwin_fault_context.py [--source TREE] [--arch arm64 --arch x86_64] [--out NEW_FOLDER]
"""
import argparse
from pathlib import Path
import platform
import shlex
import signal
import subprocess
import sys
import tempfile

RUN_ALL_ARGS = ["--cc", "clang"]
RUN_ALL_SKIP = {"windows": "Darwin signal ABI: real faults run only on macOS"}

ROOT = Path(__file__).resolve().parents[1]
PREFIX = r'''
#include <windows.h>
#include <signal.h>
#include <sys/ucontext.h>
'''
FIXTURE = r'''
#include <inttypes.h>
#include <sys/resource.h>

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); return 1; } } while (0)
#define SIGNAL_CHECK(x) do { if (!(x)) _exit(90); } while (0)

uintptr_t fault_sp;
unsigned long live_reg;
extern void read_pc(void), read_redirect(void), write_pc(void), write_redirect(void);
/* These labels belong to their containing function's Mach-O atom. Without
   .alt_entry the linker can fold the identical register-setup prefixes alone. */
#if defined(__x86_64__)
__attribute__((naked, noinline)) static unsigned fault_read(void *p) {
    __asm__ volatile("movq %rsp, _fault_sp(%rip)\nmovl $0x2468, %r10d\n"
                     ".globl _read_pc\n.alt_entry _read_pc\n_read_pc:\nmovzbl (%rdi), %eax\njmp 1f\n"
                     ".globl _read_redirect\n.alt_entry _read_redirect\n_read_redirect:\nmovl $73, %eax\n"
                     "1: movq %r10, _live_reg(%rip)\nret\n");
}
__attribute__((naked, noinline)) static unsigned fault_write(void *p, unsigned value) {
    __asm__ volatile("movq %rsp, _fault_sp(%rip)\nmovl $0x2468, %r10d\n"
                     ".globl _write_pc\n.alt_entry _write_pc\n_write_pc:\nmovb %sil, (%rdi)\nmovl $41, %eax\njmp 1f\n"
                     ".globl _write_redirect\n.alt_entry _write_redirect\n_write_redirect:\nmovl $73, %eax\n"
                     "1: movq %r10, _live_reg(%rip)\nret\n");
}
#else
__attribute__((naked, noinline)) static unsigned fault_read(void *p) {
    __asm__ volatile("adrp x9, _fault_sp@PAGE\nmov x10, sp\nstr x10, [x9, _fault_sp@PAGEOFF]\nmov x11, #0x2468\n"
                     ".globl _read_pc\n.alt_entry _read_pc\n_read_pc:\nldrb w0, [x0]\nb 1f\n"
                     ".globl _read_redirect\n.alt_entry _read_redirect\n_read_redirect:\nmov w0, #73\n"
                     "1: adrp x9, _live_reg@PAGE\nstr x11, [x9, _live_reg@PAGEOFF]\nret\n");
}
__attribute__((naked, noinline)) static unsigned fault_write(void *p, unsigned value) {
    __asm__ volatile("adrp x9, _fault_sp@PAGE\nmov x10, sp\nstr x10, [x9, _fault_sp@PAGEOFF]\nmov x11, #0x2468\n"
                     ".globl _write_pc\n.alt_entry _write_pc\n_write_pc:\nstrb w1, [x0]\nmov w0, #41\nb 1f\n"
                     ".globl _write_redirect\n.alt_entry _write_redirect\n_write_redirect:\nmov w0, #73\n"
                     "1: adrp x9, _live_reg@PAGE\nstr x11, [x9, _live_reg@PAGEOFF]\nret\n");
}
#endif

static size_t page_size;
static unsigned char *page, *wanted;
static uintptr_t alt_lo, alt_hi;
static volatile sig_atomic_t writing, repair, owns, faults, searches, filters, chained, bus, segv, stack_calls;

static LONG inspect(EXCEPTION_POINTERS *e) {
    const EXCEPTION_RECORD *r = e->ExceptionRecord;
    CONTEXT *c = e->ContextRecord;
    SIGNAL_CHECK(r->ExceptionCode == EXCEPTION_ACCESS_VIOLATION && r->NumberParameters == 2);
    SIGNAL_CHECK(r->ExceptionFlags == 0 && r->ExceptionRecord == NULL);
    SIGNAL_CHECK(r->ExceptionInformation[0] == (ULONG_PTR)writing);
    SIGNAL_CHECK(r->ExceptionInformation[1] == (uintptr_t)wanted);
    SIGNAL_CHECK(c->Rip == (uintptr_t)(writing ? write_pc : read_pc));
    SIGNAL_CHECK(r->ExceptionAddress == (void *)(uintptr_t)c->Rip && c->Rsp == fault_sp);
    SIGNAL_CHECK(!c->Dr0 && !c->Dr1 && !c->Dr2 && !c->Dr3 && !c->Dr6 && !c->Dr7);
    for (unsigned i = 2; i < EXCEPTION_MAXIMUM_PARAMETERS; i++) SIGNAL_CHECK(r->ExceptionInformation[i] == 0);
#if defined(__x86_64__)
    SIGNAL_CHECK(c->ContextFlags == CONTEXT_FULL && c->Rdi == (uintptr_t)wanted && c->R10 == 0x2468);
#else
    SIGNAL_CHECK(c->ContextFlags == CONTEXT_CONTROL && c->EFlags == 0);
    SIGNAL_CHECK(!c->Rax && !c->Rcx && !c->Rdx && !c->Rbx && !c->Rbp && !c->Rsi && !c->Rdi);
    SIGNAL_CHECK(!c->R8 && !c->R9 && !c->R10 && !c->R11 && !c->R12 && !c->R13 && !c->R14 && !c->R15);
#endif
    sigset_t mask;
    SIGNAL_CHECK(sigprocmask(SIG_SETMASK, NULL, &mask) == 0);
    SIGNAL_CHECK(!sigismember(&mask, SIGBUS) && !sigismember(&mask, SIGSEGV));
    SIGNAL_CHECK(!sigismember(&mask, SIGILL) && !sigismember(&mask, SIGFPE));
    faults++;
    return EXCEPTION_CONTINUE_SEARCH;
}
static LONG search_first(EXCEPTION_POINTERS *e) {
    (void)e;
    searches++;
    return EXCEPTION_CONTINUE_SEARCH;
}
static LONG recover(EXCEPTION_POINTERS *e) {
    if (!owns) return EXCEPTION_CONTINUE_SEARCH;
    SIGNAL_CHECK(searches == faults);
    if (repair) SIGNAL_CHECK(mprotect(page, page_size, PROT_READ | PROT_WRITE) == 0);
    else e->ContextRecord->Rip = (uintptr_t)(writing ? write_redirect : read_redirect);
#if defined(__x86_64__)
    e->ContextRecord->R10 = 0x1357;   /* prove that a live integer register edit survives sigreturn */
#endif
    return EXCEPTION_CONTINUE_EXECUTION;
}
static LONG last_filter(EXCEPTION_POINTERS *e) {
    filters++;
    return recover(e);
}
static void prior_info(int sig, siginfo_t *si, void *raw) {
    ucontext_t *u = raw;
    SIGNAL_CHECK(si->si_addr == wanted && !owns);
    SIGNAL_CHECK(sig == SIGBUS || sig == SIGSEGV);
    bus += sig == SIGBUS;
    segv += sig == SIGSEGV;
    chained++;
#if defined(__x86_64__)
    SIGNAL_CHECK(u->uc_mcontext->__ss.__rip == (uintptr_t)(writing ? write_pc : read_pc));
    u->uc_mcontext->__ss.__rip = (uintptr_t)(writing ? write_redirect : read_redirect);
#else
    SIGNAL_CHECK(u->uc_mcontext->__ss.__pc == (uintptr_t)(writing ? write_pc : read_pc));
    u->uc_mcontext->__ss.__pc = (uintptr_t)(writing ? write_redirect : read_redirect);
#endif
}
static void prior_plain(int sig) {
    SIGNAL_CHECK((sig == SIGBUS || sig == SIGSEGV) && !owns);
    chained++;
    SIGNAL_CHECK(mprotect(page, page_size, PROT_READ | PROT_WRITE) == 0);
}
static LONG break_handler_stack(EXCEPTION_POINTERS *e) {
    (void)e;
    volatile unsigned char local = 0;
    stack_calls++;
    SIGNAL_CHECK(stack_calls == 1 && (uintptr_t)&local >= alt_lo && (uintptr_t)&local < alt_hi);
    static const char marker[] = "HANDLER_STACK entered=1 on_alternate_stack=1\n";
    SIGNAL_CHECK(write(STDOUT_FILENO, marker, sizeof marker - 1) == sizeof marker - 1);
    SIGNAL_CHECK(mprotect((void *)((uintptr_t)&local & ~(page_size - 1)), page_size, PROT_NONE) == 0);
    local = 1;   /* the actual handler stack page is inaccessible, not just an unrelated pointer */
    _exit(91);   /* reaching this is failure; the Python parent checks signal death, not any nonzero exit */
}

static int roundtrip(void) {
    _STRUCT_MCONTEXT m, original;
    memset(&m, 0x2a, sizeof m);
    ucontext_t u = {0};
    u.uc_mcontext = &m;
    CONTEXT c;
#if defined(__x86_64__)
    m.__ss.__rflags = 0x246;
#endif
    original = m;
    context_from(&c, &u);
    context_to(&u, &c);
    CHECK(memcmp(&m, &original, sizeof m) == 0);   /* all FP, SIMD, exception and other unmapped state survives */
    c.Rip += 4;
    c.Rsp += 16;
#if defined(__x86_64__)
    c.Rax = 1, c.Rcx = 2, c.Rdx = 3, c.Rbx = 4, c.Rbp = 5, c.Rsi = 6, c.Rdi = 7;
    c.R8 = 8, c.R9 = 9, c.R10 = 10, c.R11 = 11, c.R12 = 12, c.R13 = 13, c.R14 = 14, c.R15 = 15;
    c.EFlags = 0x247;
    original.__ss.__rax = 1, original.__ss.__rcx = 2, original.__ss.__rdx = 3, original.__ss.__rbx = 4;
    original.__ss.__rbp = 5, original.__ss.__rsi = 6, original.__ss.__rdi = 7;
    original.__ss.__r8 = 8, original.__ss.__r9 = 9, original.__ss.__r10 = 10, original.__ss.__r11 = 11;
    original.__ss.__r12 = 12, original.__ss.__r13 = 13, original.__ss.__r14 = 14, original.__ss.__r15 = 15;
    original.__ss.__rip += 4, original.__ss.__rsp += 16, original.__ss.__rflags = 0x247;
#else
    /* x64-only edits have no native meaning and must not corrupt ARM GPRs or CPSR. */
    c.Rax = c.Rbp = c.R10 = c.EFlags = 0xdead;
    original.__ss.__pc += 4, original.__ss.__sp += 16;
#endif
    context_to(&u, &c);
    CHECK(memcmp(&m, &original, sizeof m) == 0);
#ifdef LONGSHOT_DARWIN_FAULT_H
#if defined(__x86_64__)
    m.__es.__trapno = 14;
    m.__es.__err = 0; CHECK(darwin_fault_access(&u) == 0);
    m.__es.__err = 2; CHECK(darwin_fault_access(&u) == 1);
    m.__es.__err = 16; CHECK(darwin_fault_access(&u) == 8);
    m.__es.__trapno = 13; m.__es.__err = 2; CHECK(darwin_fault_access(&u) == 0);
#else
    m.__es.__esr = 0x24u << 26; CHECK(darwin_fault_access(&u) == 0);
    m.__es.__esr |= 1u << 6; CHECK(darwin_fault_access(&u) == 1);
    m.__es.__esr = 0x21u << 26; CHECK(darwin_fault_access(&u) == 8);
    m.__es.__esr = 1u << 6; CHECK(darwin_fault_access(&u) == 0);
#endif
#endif
    puts("ROUNDTRIP pc_sp=1 integer_contract=1 untouched_machine_state=1 syndrome_classes=1");
    return 0;
}

int main(int argc, char **argv) {
    CHECK(argc == 3);
    const int rounds = atoi(argv[2]);
    CHECK(rounds > 0);
    setvbuf(stdout, NULL, _IONBF, 0);
    struct rlimit limit;
    CHECK(getrlimit(RLIMIT_CORE, &limit) == 0 && limit.rlim_cur == 0);
    page_size = (size_t)sysconf(_SC_PAGESIZE);
    page = mmap(NULL, page_size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    CHECK(page != MAP_FAILED);
    wanted = page + 13;
    const int plain = !strcmp(argv[1], "plain"), broken_stack = !strcmp(argv[1], "handler-stack");
    struct sigaction sa = {0};
    if (plain) sa.sa_handler = prior_plain;
    else { sa.sa_sigaction = prior_info; sa.sa_flags = SA_SIGINFO; }
    sigemptyset(&sa.sa_mask);
    CHECK(sigaction(SIGBUS, &sa, NULL) == 0 && sigaction(SIGSEGV, &sa, NULL) == 0);
    if (broken_stack) {
        void *alt = mmap(NULL, 128 * 1024, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
        CHECK(alt != MAP_FAILED);
        alt_lo = (uintptr_t)alt, alt_hi = alt_lo + 128 * 1024;
        stack_t ss = {.ss_sp = alt, .ss_size = 128 * 1024};
        CHECK(sigaltstack(&ss, NULL) == 0);
        CHECK(AddVectoredExceptionHandler(1, break_handler_stack));
        CHECK(mprotect(page, page_size, PROT_NONE) == 0);
        fault_read(wanted);
        return 92;
    }
    CHECK(roundtrip() == 0);
    CHECK(AddVectoredExceptionHandler(0, inspect));
    CHECK(AddVectoredExceptionHandler(0, recover));
    CHECK(AddVectoredExceptionHandler(1, search_first));
    CHECK(SetUnhandledExceptionFilter(last_filter) == NULL);
    CHECK(SetUnhandledExceptionFilter(last_filter) == last_filter);
    for (int sig = 1; sig < 32; sig++) {
        if (sig != SIGBUS && sig != SIGSEGV && sig != SIGILL && sig != SIGFPE) continue;
        CHECK(sigaction(sig, NULL, &sa) == 0 && sa.sa_sigaction != NULL);
        CHECK((sa.sa_flags & SA_SIGINFO) && (sa.sa_flags & SA_NODEFER));
    }
    const unsigned long edited_reg =
#if defined(__x86_64__)
        0x1357;
#else
        0x2468;
#endif
    if (!plain) {
        owns = 1;
        for (repair = 0; repair < 2; repair++) {
            for (int i = 0; i < rounds; i++) {
                CHECK(mprotect(page, page_size, PROT_READ | PROT_WRITE) == 0);
                *wanted = 37;
                CHECK(mprotect(page, page_size, PROT_NONE) == 0);
                writing = 0;
                CHECK(fault_read(wanted) == (repair ? 37u : 73u) && live_reg == edited_reg);
                CHECK(mprotect(page, page_size, PROT_READ) == 0);
                writing = 1;
                CHECK(fault_write(wanted, 55) == (repair ? 41u : 73u) && live_reg == edited_reg);
                CHECK(*wanted == (repair ? 55 : 37));
            }
        }
        CHECK(faults == rounds * 4 && filters == 0);
        CHECK(RemoveVectoredExceptionHandler((void *)recover) == 1);
        CHECK(RemoveVectoredExceptionHandler((void *)recover) == 0);
        repair = 0;
        for (int i = 0; i < rounds; i++) {
            CHECK(mprotect(page, page_size, PROT_NONE) == 0);
            writing = 0;
            CHECK(fault_read(wanted) == 73 && live_reg == edited_reg);
        }
        CHECK(filters == rounds);
        CHECK(AddVectoredExceptionHandler(0, recover));
    }
    owns = 0;
    for (int i = 0; i < rounds; i++) {
        CHECK(mprotect(page, page_size, PROT_READ | PROT_WRITE) == 0);
        *wanted = 37;
        CHECK(mprotect(page, page_size, PROT_NONE) == 0);
        writing = 0;
        CHECK(fault_read(wanted) == (plain ? 37u : 73u) && live_reg == 0x2468);
    }
    CHECK(chained == rounds);
    if (!plain) {
        unsigned char *gap = mmap(NULL, 3 * page_size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
        CHECK(gap != MAP_FAILED && munmap(gap + page_size, page_size) == 0);
        wanted = gap + page_size + 13;
        for (int i = 0; i < rounds; i++) CHECK(fault_read(wanted) == 73);
        CHECK(bus == rounds && segv == rounds && chained == 2 * rounds);
        CHECK(munmap(gap, page_size) == 0 && munmap(gap + 2 * page_size, page_size) == 0);
        CHECK(faults == rounds * 7 && searches == faults && filters == rounds * 3);
    } else CHECK(faults == rounds && searches == faults && filters == rounds);
    CHECK(RemoveVectoredExceptionHandler((void *)recover) == 1);
    CHECK(RemoveVectoredExceptionHandler((void *)inspect) == 1);
    CHECK(RemoveVectoredExceptionHandler((void *)search_first) == 1);
    CHECK(SetUnhandledExceptionFilter(NULL) == last_filter);
    CHECK(munmap(page, page_size) == 0);
    printf("FAULT_BRIDGE mode=%s rounds=%d page=%zu faults=%d chained=%d SIGBUS=%d SIGSEGV=%d pass=1\n",
           argv[1], rounds, page_size, faults, chained, bus, segv);
    return 0;
}
'''


def fixture(source):
    text = (source / "host/posix/win32_posix.c").read_text(encoding="utf-8")
    begin = text.index("/* ---- exceptions ---- */")
    end = text.index("/* ---- the window's state", begin)
    include = '#include "darwin_fault.h"\n' if (source / "host/posix/darwin_fault.h").is_file() else ""
    return PREFIX + include + '#line %d "host/posix/win32_posix.c"\n' % (text[:begin].count("\n") + 1) + text[begin:end] + '\n#line 1 "fault_fixture.c"\n' + FIXTURE


def command(cmd, out, label, seconds):
    print("command=" + shlex.join(cmd), flush=True)
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=seconds)
    except subprocess.TimeoutExpired as e:
        text = e.stdout or b""
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        result = subprocess.CompletedProcess(cmd, 124, text + "\nTIMEOUT after %d seconds\n" % seconds)
    (out / (label + ".log")).write_text("command=" + shlex.join(cmd) + "\n" + result.stdout + "\nexit=%d\n" % result.returncode,
                                       encoding="utf-8")
    print(result.stdout, end="" if result.stdout.endswith("\n") else "\n", flush=True)
    print("exit=%d" % result.returncode, flush=True)
    return result


def run(a, out):
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    crash_dir = Path.home() / "Library/Logs/DiagnosticReports"
    before = {p for p in crash_dir.iterdir() if p.is_file()} if crash_dir.is_dir() else None
    src = out / "fault_fixture.c"
    src.write_text(fixture(a.source), encoding="utf-8")
    helpers = [a.source / "host/posix/darwin_fault.c"]
    helpers = [str(p) for p in helpers if p.is_file()]
    targets = a.arch or [platform.machine()]
    failed = False
    for arch in targets:
        binary = out / ("fault-" + arch)
        built = command([a.cc, "-arch", arch, "-mmacosx-version-min=14.4", "-std=gnu11", "-O2", "-g0", "-Wall", "-Wextra",
                         "-Werror", "-ferror-limit=0", "-fno-color-diagnostics", "-I", str(a.source / "host/posix"),
                         str(src), *helpers, "-o", str(binary)], out, "build-" + arch, 45)
        if built.returncode:
            print("FAIL %s bridge compilation" % arch)
            failed = True
            continue
        for mode in a.case or ("handler-stack", "matrix", "plain"):
            result = command(["arch", "-" + arch, str(binary), mode, str(a.rounds)], out, arch + "-" + mode, 30)
            if mode == "handler-stack":
                # An undeliverable arm64 stack fault is SIGILL here; a deliverable nested fault uses the guard's _exit.
                fatal = (-signal.SIGILL, -signal.SIGBUS, -signal.SIGSEGV, 128 + signal.SIGBUS, 128 + signal.SIGSEGV)
                passed = result.returncode in fatal and result.stdout.count("HANDLER_STACK entered=1") == 1
            else:
                passed = result.returncode == 0 and "pass=1" in result.stdout
            print("%s %s %s" % ("PASS" if passed else "FAIL", arch, mode))
            failed |= not passed
    if before is None:
        print("CRASH_REPORTS unavailable: HOME has no DiagnosticReports directory (the suite wrapper counts the real one)")
    else:
        after = {p for p in crash_dir.iterdir() if p.is_file()}
        print("CRASH_REPORTS before=%d after=%d new=%d" % (len(before), len(after), len(after - before)))
        for p in sorted(after - before):
            print("new_crash_report=" + p.name)
    print("%s Darwin fault bridge" % ("FAIL" if failed else "PASS"))
    return int(failed)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cc", default="clang")
    p.add_argument("--source", type=Path, default=ROOT)
    p.add_argument("--arch", choices=("arm64", "x86_64"), action="append")
    p.add_argument("--case", choices=("handler-stack", "matrix", "plain"), action="append")
    p.add_argument("--rounds", type=int, default=256)
    p.add_argument("--out", type=Path)
    a = p.parse_args()
    if a.rounds < 1:
        p.error("--rounds must be positive")
    if sys.platform == "darwin":
        if a.out:
            a.out.mkdir(parents=True, exist_ok=False)
            return run(a, a.out.resolve())
        with tempfile.TemporaryDirectory(prefix="ls-fault-") as tmp:
            return run(a, Path(tmp))
    print("SKIP: Darwin signal ABI; no Windows or Linux runtime changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
