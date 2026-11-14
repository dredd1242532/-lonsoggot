"""HOS_VCORE_FIRST (host/hos_thread.c): the threads it names take a virtual core before any other, but their turns
(a yield, a time slice) still go by guest priority. The actual HosThread, hos_thread_core, vc_first_named and the whole
virtual-core block (from vc_lock to hos_cv_sleep) are compiled with a single-threaded harness, as in
test_vcore_escape.py: a clock the waits advance, no-op locks, and a hook in the wait that plays the holder answering
(giving its core up when it is asked, or at its checkpoint). It pins:

- a first waiter asks a worse holder for its core at once (vc_kick 1);
- a first holder hands its core to a waiter as good by guest priority at a yield, and when that waiter asks after the
  time slice (vc_kick 2, at its checkpoint);
- after such a hand-over the first thread doesn't ask for the core back at once: it waits for the time slice like any
  thread of its guest priority, then comes first again once it holds a core (before, its vc_acquire ranked it at -1 and
  asked at once, so the waiter ran one checkpoint's work per turn);
- first threads keep their guest order among themselves (they all tied at -1 before);
- a waiter better by guest priority than a first holder asks it only after the time slice (the switch's departure from
  Horizon, documented in docs/development.md);
- the parts are case-sensitive substrings, and spaces around them are skipped;
- with the switch unset, the order is the guest priority, as on main.

python -I -B tests/test_vcore_first.py [--cxx clang++]
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
#define HOS_TLS_SLOTS 64
typedef struct { uint64_t x[32]; volatile int poll; } Ctx;
/* the environment: HOS_VCORE_FIRST as the case sets it, every other variable unset (the defaults) */
static const char *first_parts;
static const char *fake_getenv(const char *name) { return strcmp(name, "HOS_VCORE_FIRST") ? NULL : first_parts; }
#define getenv fake_getenv
static int64_t now_ns = 1000000000;
static int64_t hos_now_ns(void) { return now_ns; }
enum { HOS_CHAIN_WAIT = 1, HOS_CHAIN_WOKE, HOS_CHAIN_SIGNAL, HOS_CHAIN_SLEEP, HOS_CHAIN_AWAKE, HOS_CHAIN_PRESENT };
#define HOS_CHAIN(kind, key) ((void)(kind), (void)(key))   /* (HOS_CHAIN off: host/hos.h) */
static int escapes_logged;
static void hos_log(const char *fmt, ...) { if (strstr(fmt, "ran without a core")) escapes_logged++; }
static int hos_cores(void) { return 2; }
/* (no other thread runs here: a wait for a core runs the hook, the other threads' part, then lets the clock go on,
   1 ms a look, as the real one times out) */
static void (*on_sleep)(void);
static BOOL SleepConditionVariableSRW(PCONDITION_VARIABLE cv, PSRWLOCK lk, DWORD ms, unsigned long f) {
    (void)cv, (void)lk, (void)f;
    if (on_sleep) on_sleep();
    now_ns += (int64_t)(ms ? ms : 1) * 1000000;
    return 1;
}
static void WakeConditionVariable(PCONDITION_VARIABLE cv) { (void)cv; }
static void AcquireSRWLockExclusive(PSRWLOCK l) { (void)l; }
static void ReleaseSRWLockExclusive(PSRWLOCK l) { (void)l; }
@STRUCT@
static HosThread *cur;
@CORE@
@NAMED@
@VCORES@
static int bad;
static void check(int ok, const char *what) { if (!ok) { printf("FAIL: %s\n", what); bad = 1; } }
static void thread(HosThread *t, const char *name, int prio, uint64_t mask) {
    memset(t, 0, sizeof *t);
    snprintf(t->name, sizeof t->name, "%s", name);
    t->priority = prio, t->core_mask = mask, t->ideal_core = -2;
    t->vc_first = vc_first_named(t->name);
}
static const int64_t SLICE = 2000000;   /* HOS_VCORE_SLICE_US's default, in ns */
/* the holder the waiter may ask: it blocks (gives its core up) once asked, or once the waiter has waited release_after */
static HosThread *answerer;
static int calls, kick_seen, held_at_first;
static int64_t wait_start, kick_after, release_after;
static void reset(void) {
    memset(vc_owner, 0, sizeof vc_owner);
    vc_nq = 0;
    on_sleep = NULL, answerer = NULL;
    calls = kick_seen = held_at_first = 0;
    kick_after = -1, release_after = -1;
    wait_start = now_ns;
}
static void answer(void) {
    if (!answerer) return;
    if (!calls++) held_at_first = answerer->vc_held;
    const int64_t waited = now_ns - wait_start;
    if (answerer->vc_kick && !kick_seen) kick_seen = answerer->vc_kick, kick_after = waited;
    if (kick_seen || (release_after >= 0 && waited >= release_after)) {
        HosThread *h = answerer;
        answerer = NULL;
        vc_release(h);
    }
}
static void hold(HosThread *t) { HosThread *was = cur; cur = t; hos_vcore_back(); cur = was; }
/* t waits for a core (its own vc_acquire) while answerer holds the one it may use */
static void wait_for_core(HosThread *t, HosThread *holder) {
    answerer = holder, on_sleep = answer;
    wait_start = now_ns;
    HosThread *was = cur;
    cur = t;
    hos_vcore_back();
    cur = was;
    on_sleep = NULL;
}
/* the time-slice case: the first holder F answers J's ask at its checkpoint; then J blocks once F asks for the core back */
static HosThread *slice_f, *slice_j;
static int stage, f_kick, j_held_after_turn, j_kick;
static int64_t f_kick_after, j_kick_after, f_wait_start;
static void slice_answer(void) {
    if (stage == 0 && slice_f->vc_kick) {   /* J (waiting) asked F */
        f_kick = slice_f->vc_kick, f_kick_after = now_ns - wait_start;
        stage = 1;
        f_wait_start = now_ns;
        cur = slice_f;
        hos_vcore_checkpoint();   /* F gives its core to J, and waits for one (this hook runs again, at stage 1) */
        cur = slice_j;
    } else if (stage == 1) {   /* F waits: J holds the core */
        if (!j_held_after_turn) j_held_after_turn = slice_j->vc_held;
        if (slice_j->vc_kick) {
            j_kick = slice_j->vc_kick, j_kick_after = now_ns - f_wait_start;
            stage = 2;
            vc_release(slice_j);   /* J blocks: F takes the core */
        }
    } else if (stage == 2) {   /* J waits again (it was asked mid-wait here): F blocks, J takes the core */
        stage = 3;
        vc_release(slice_f);
    }
}
int main(void) {
    HosThread f, f2, j, h;

    /* the names */
    first_parts = " Presentation , Main";
    check(vc_first_named("Presentation Thread") && vc_first_named("MainThread"),
          "a part matches as a substring of the name, the spaces around it skipped");
    check(!vc_first_named("presentation thread") && !vc_first_named("BaseProcExecutor0"),
          "a part matches case-sensitively, and only names that hold it");
    first_parts = ",";
    check(!vc_first_named("MainThread"), "empty parts match nothing");
    first_parts = NULL;
    check(!vc_first_named("Presentation Thread"), "with HOS_VCORE_FIRST unset, no thread is first");

    /* unset: the order is the guest priority, as on main */
    thread(&j, "BaseProcExecutor0", 16, 1), thread(&h, "ModuleSystemWorker0", 20, 1);
    check(vc_prio(&j) == 16 && vc_prio(&h) == 20 && vc_better(&j, &h), "unset: the virtual cores go by guest priority");

    first_parts = "Presentation";

    /* 1. a first waiter asks a worse holder for its core at once */
    reset();
    thread(&f, "Presentation Thread", 16, 1), thread(&j, "BaseProcExecutor0", 16, 1);
    hold(&j);
    check(j.vc_held == 1, "case 1: the executor holds core 0");
    wait_for_core(&f, &j);
    check(kick_seen == 1 && kick_after == 0, "case 1: the first waiter asks the holder for its core at once (vc_kick 1)");
    check(f.vc_held == 1 && !escapes_logged, "case 1: and takes it when the holder gives it up");

    /* 2. a first holder's yield hands its core to a waiter as good by guest priority, which keeps it for its slice */
    reset();
    thread(&f, "Presentation Thread", 16, 1), thread(&j, "BaseProcExecutor0", 16, 1);
    hold(&f);
    j.vc_since = now_ns, j.vc_queued = 1, vc_queue[vc_nq++] = &j;   /* J waits for core 0 */
    answerer = &j, on_sleep = answer;
    cur = &f;
    hos_vcore_yield();
    on_sleep = NULL;
    check(held_at_first == 1, "case 2: the first holder's yield gives its core to the equal waiter");
    check(kick_seen == 2 && kick_after > SLICE,
          "case 2: after the yield the first thread asks for the core back only after the time slice (vc_kick 2), "
          "not at once");
    check(f.vc_held == 1 && !f.vc_turned && vc_prio(&f) == 16 - 64,
          "case 2: once it holds a core again the first thread comes first again");
    check(!escapes_logged, "case 2: no escape");

    /* 3. after the slice, an equal waiter asks a first holder; the holder hands over at its checkpoint, and doesn't
       ask for the core back at once */
    reset();
    thread(&f, "Presentation Thread", 16, 1), thread(&j, "BaseProcExecutor0", 16, 1);
    hold(&f);
    slice_f = &f, slice_j = &j, stage = 0, f_kick = j_kick = j_held_after_turn = 0;
    on_sleep = slice_answer;
    wait_start = now_ns;
    cur = &j;
    hos_vcore_back();
    on_sleep = NULL;
    check(f_kick == 2 && f_kick_after > SLICE, "case 3: the equal waiter asks the first holder after the time slice");
    check(j_held_after_turn == 1, "case 3: the first holder gives it the core at its checkpoint");
    check(j_kick == 2 && j_kick_after > SLICE,
          "case 3: the first thread asks for it back only after the time slice, not at once");
    check(stage == 3 && j.vc_held == 1 && !f.vc_turned, "case 3: the turns go on");
    check(!escapes_logged, "case 3: no escape");

    /* 4. first threads keep their guest order */
    reset();
    thread(&f, "Presentation Thread A", 10, 1), thread(&f2, "Presentation Thread B", 20, 1);
    thread(&h, "BaseProcExecutor0", 0, 1);
    f2.vc_since = 0, f.vc_since = 100;
    check(vc_better(&f, &f2), "case 4: of two first waiters, the better by guest priority goes first, not the older");
    check(vc_better(&f2, &h), "case 4: a first thread goes before any other");
    check(vc_turn(&f, &f2) && !vc_turn(&f2, &f), "case 4: a first thread takes no better first thread's core in a turn");
    hold(&f2);
    wait_for_core(&f, &f2);
    check(kick_seen == 1 && kick_after == 0, "case 4: a better first waiter asks a worse first holder at once");
    reset();
    thread(&f, "Presentation Thread A", 10, 1), thread(&f2, "Presentation Thread B", 20, 1);
    hold(&f);
    release_after = 5 * SLICE;
    wait_for_core(&f2, &f);
    check(!kick_seen && f2.vc_held == 1, "case 4: a worse first waiter never asks a better first holder");

    /* 5. a waiter better by guest priority than a first holder asks it only after the time slice (documented) */
    reset();
    thread(&f, "Presentation Thread", 16, 1), thread(&h, "Audio", 10, 1);
    hold(&f);
    wait_for_core(&h, &f);
    check(kick_seen == 2 && kick_after > SLICE, "case 5: a better guest waiter asks a first holder after the time slice");
    check(h.vc_held == 1 && !escapes_logged, "case 5: and takes its core");

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
               .replace("@NAMED@", block(src, "static int vc_first_named(const char *name) {"))
               .replace("@VCORES@", src[start:end]))
    compiler = shutil.which(args.cxx)
    if not compiler:
        raise SystemExit("C++ compiler not found: " + args.cxx)
    with tempfile.TemporaryDirectory(prefix="vcore-first-") as temporary:
        folder = Path(temporary)
        (folder / "fixture.cpp").write_text(fixture, encoding="utf-8")
        exe = folder / ("fixture.exe" if os.name == "nt" else "fixture")
        subprocess.run([compiler, "-std=c++17", "-O1", "-w", str(folder / "fixture.cpp"), "-o", str(exe)], check=True,
                       timeout=120)
        result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
        print(result.stdout, end="")
        if result.returncode or "ok" not in result.stdout:
            raise SystemExit("HOS_VCORE_FIRST's virtual-core rules don't hold (host/hos_thread.c)")


if __name__ == "__main__":
    main()
