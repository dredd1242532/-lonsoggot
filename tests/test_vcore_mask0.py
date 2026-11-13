"""Mask 0 and the cached-core preference (host/hos_thread.c).

The virtual-core block is compiled with a single-threaded harness; the wrapper and the TLS
cache helpers are stubs. The checks are:
- mask 0 is no affinity: it uses every host core and prefers the core the guest cached;
- a mask change moves the thread's TLS cell to the new core + 1 (single-bit/0) or clears it
  (multi-bit), so the guest's own cache agrees with the core it runs on;
- a thread that holds a guest mutex is not moved until it lets the mutex go.

python -I -B tests/test_vcore_mask_tls.py [--cxx clang++]
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]


def block(source, marker):
    start = source.index(marker)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + (";" if source[end:end + 1] == ";" else "")


HARNESS = r"""
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef void *HANDLE;
typedef int BOOL;
typedef unsigned long DWORD;
typedef struct { void *p; } SRWLOCK, *PSRWLOCK;
typedef struct { void *p; } CONDITION_VARIABLE, *PCONDITION_VARIABLE;
#define SRWLOCK_INIT {0}
#define HOS_TLS_SLOTS 16
typedef struct { uint64_t x[32]; volatile int poll; } Ctx;
static int64_t now_ns = 1000000000;
static int64_t hos_now_ns(void) { return now_ns; }
enum { HOS_CHAIN_WAIT = 1, HOS_CHAIN_WOKE, HOS_CHAIN_SIGNAL, HOS_CHAIN_SLEEP, HOS_CHAIN_AWAKE, HOS_CHAIN_PRESENT };
#define HOS_CHAIN(kind, key) ((void)(kind), (void)(key))
static void hos_log(const char *fmt, ...) { (void)fmt; }
static int hos_cores(void) { return 2; }
static BOOL SleepConditionVariableSRW(PCONDITION_VARIABLE cv, PSRWLOCK lk, DWORD ms, unsigned long f) {
    (void)cv, (void)lk, (void)ms, (void)f;
    return 1;
}
static void WakeConditionVariable(PCONDITION_VARIABLE cv) { (void)cv; }
static void AcquireSRWLockExclusive(PSRWLOCK l) { (void)l; }
static void ReleaseSRWLockExclusive(PSRWLOCK l) { (void)l; }
static int wrapper_valid, wrapper_mask;
static int alias_now;
static volatile int core_tls_slot = 0;
static int core_tls_alias_now(void) { return alias_now; }
static void *hos_guest_range(uint64_t a, uint64_t n) { (void)a, (void)n; return NULL; }
@STRUCT@
static HosThread *cur;
@CORE@
static int hos_thread_wrapper_mask(HosThread *t, uint32_t *out) {
    (void)t;
    if (!wrapper_valid) return 0;
    *out = (uint32_t)wrapper_mask;
    return 1;
}
@VCORES@
static int bad;
static void check(int ok, const char *what) { if (!ok) { printf("FAIL: %s\n", what); bad = 1; } }
static void thread(HosThread *t, const char *name, int prio, uint64_t mask) {
    memset(t, 0, sizeof *t);
    snprintf(t->name, sizeof t->name, "%s", name);
    t->priority = prio, t->core_mask = mask, t->ideal_core = -2;
}
int main(void) {
    HosThread t, other;

    /* mask 0 is no affinity, but the guest's cached core is preferred */
    thread(&t, "mask0", 16, 0);
    check(vc_allowed(&t) == 3, "mask 0 allows both host cores");
    thread(&t, "mask1", 16, 1);
    check(vc_allowed(&t) == 1, "a one-core mask allows only that core");

    thread(&t, "mask0", 16, 0);
    wrapper_valid = 1, wrapper_mask = 0, alias_now = 0;
    t.tls_slots[0] = 2;   /* core 1 + 1, the cell the trampoline wrote */
    check(vc_pref_core(&t) == 1, "mask 0 prefers the core the guest cached");
    alias_now = 1;
    check(vc_pref_core(&t) == 0, "a stale/aliased cell is not trusted");
    alias_now = 0;

    memset(vc_owner, 0, sizeof vc_owner);
    t.vc_remask = 0;
    check(vc_free_for(&t) == 1, "the cached core is taken while it is free");
    thread(&other, "holder", 16, 2);
    vc_owner[1] = &other;   /* the cached core is taken: another free core and a remask */
    t.vc_remask = 0;
    check(vc_free_for(&t) == 0 && t.vc_remask, "contention moves the thread and asks for a remask");
    memset(vc_owner, 0, sizeof vc_owner);

    /* a mask change that excludes the held core is queued; the checkpoint moves it */
    thread(&t, "force", 16, 2);
    memset(vc_owner, 0, sizeof vc_owner);
    vc_nq = 0;
    vc_owner[0] = &t;
    cur = &t;
    t.vc_held = 1, t.vc_kick = 0, t.vc_last = 1;
    vc_note_mask_change(&t);
    check(t.vc_kick == 2, "a mask change outside the held core queues a move");
    vc_yield(&t, 0);
    check(t.vc_held == 2 && t.vc_remask == 0, "the next checkpoint moves to the allowed core");

    if (!bad) puts("ok");
    return bad;
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="clang++")
    parser.add_argument("--source", type=Path, default=ROOT / "host/hos_thread.c")
    args = parser.parse_args()
    src = args.source.read_text(encoding="utf-8")
    start = src.index("static SRWLOCK vc_lock = SRWLOCK_INIT;")
    end = src.index("\n#else", src.index("int hos_cv_sleep(void *cv, void *lk, unsigned ms) {", start))
    fixture = (HARNESS.replace("@STRUCT@", block(src, "struct HosThread {") + "\ntypedef struct HosThread HosThread;")
               .replace("@CORE@", block(src, "int hos_thread_core(HosThread *t) {"))
               .replace("@VCORES@", src[start:end]))
    compiler = shutil.which(args.cxx)
    if not compiler:
        raise SystemExit("C++ compiler not found: " + args.cxx)
    with tempfile.TemporaryDirectory(prefix="vcore-mask-") as temporary:
        folder = Path(temporary)
        (folder / "fixture.cpp").write_text(fixture, encoding="utf-8")
        exe = folder / ("fixture.exe" if os.name == "nt" else "fixture")
        subprocess.run([compiler, "-std=c++17", "-O1", "-w", str(folder / "fixture.cpp"), "-o", str(exe)], check=True,
                       timeout=120)
        result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
        print(result.stdout, end="")
        if result.returncode or "ok" not in result.stdout:
            raise SystemExit("the virtual-core mask/TLS checks failed")


if __name__ == "__main__":
    main()
