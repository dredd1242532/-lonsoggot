"""Execute the temporary kit's actual acceptance/owner dispatch boundary without a game."""
import argparse
import importlib.util
from pathlib import Path
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cc", "gcc"]
ROOT = Path(__file__).resolve().parents[1]
FIXTURE = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "coop.h"
#include "coop_gameplay.h"
#include "oot_coop_kit.h"
typedef struct { int unused; } Ctx;
static struct {
    int lock, on, bound, loaded, gate, presence;
    CoopSync *eng;
    CoopGameplay gameplay;
    OotCoopKitRun kit;
    OotCoopKitWork kit_work;
    uint64_t mgr, bool_entries, int_entries, save_generation, area_generation;
    CoopView view;
} G;
static int locked, native_calls, guest_reads, temporary = 1;
static volatile int hos_oot_coop_kit_active;
static CoopOffer current;
static CoopView room;
enum { C_BOOL = 8, C_INT = 0xc8, CN_ENTRIES = 0x10 };
static int TryAcquireSRWLockExclusive(int *lock) { (void)lock; assert(!locked); locked = 1; return 1; }
static void ReleaseSRWLockExclusive(int *lock) { (void)lock; assert(locked); locked = 0; }
static int rd_ok(uint64_t p, unsigned n) { return p && n <= 112; }
static uint64_t recomp_load64(Ctx *c, uint64_t p) { (void)c; guest_reads++; return p == 0x84c9e090 ? 100 : p == 100 ? 200 : 0; }
static uint64_t coop_now_ms(void) { return 1; }
int coop_profile_active(void) { return temporary; }
void coop_view_get(CoopView *out) { *out = room; }
int coop_sync_offer(CoopSync *s, CoopOffer *out, uint64_t now) { (void)s; (void)now; *out = current; return current.token != 0; }
int coop_sync_open(CoopSync *s) { (void)s; return 0; } /* no temporary progress/copy authorization */
int coop_sync_member_accepted(const CoopSync *s, int id, uint64_t session) { (void)s; return current.accepted && id == 2 && session == 202; }
void coop_sync_rebind(CoopSync *s) { (void)s; current.accepted = 0; }
static void publish_members(void) {}
int oot_coop_kit_step(Ctx *c, OotCoopKitRun *r, const OotCoopKitWork *w) {
    (void)c; assert(locked && temporary && w->kit.bits == 3 && w->kit.age == 0);
    if (r->state == OOT_KIT_CANCELLED) return 0;
    native_calls++; r->state = OOT_KIT_READY; r->offer = w->offer; r->epoch = w->epoch; r->generation = w->generation;
    return 1;
}
@@REGISTRY@@
@@PRODUCTION@@
static void setup(void) {
    memset(&G, 0, sizeof G); memset(&current, 0, sizeof current); memset(&room, 0, sizeof room);
    G.on = G.bound = 1; G.eng = (CoopSync *)(uintptr_t)1; G.save_generation = G.area_generation = 1;
    room.state = COOP_JOINED; room.session = 101; room.n = 1; room.member[0].id = 2; room.member[0].session = 202;
    current.token = 7; current.rules.epoch = 8;
    assert(coop_feature_set(&current.rules, 32, 0)); assert(coop_feature_set(&current.rules, 40, 3));
    assert(coop_feature_set(&current.rules, 83, 1));
    native_calls = guest_reads = 0; hos_oot_coop_kit_active = 0;
}
int main(void) {
    Ctx c = {0}; setup(); G.on = 0; hos_oot_coop_kit_update(&c);
    assert(!guest_reads && !native_calls); G.on = 1;
    temporary_kit_present(&c, 10, 1); hos_oot_coop_kit_update(&c);
    assert(!native_calls && !G.gameplay.context.capabilities); /* joined is not accepted */
    current.accepted = 1; temporary_kit_present(&c, 10, 1);
    assert(G.gameplay.context.capabilities == COOP_GAMEPLAY_TEMPORARY);
    CoopGameplayTicket stale;
    assert(coop_gameplay_ticket(&G.gameplay, COOP_GAMEPLAY_KIT, 0, 101, &stale));
    assert(!hos_oot_coop_kit_ready(7, 8)); hos_oot_coop_kit_update(&c);
    assert(native_calls == 1 && hos_oot_coop_kit_ready(7, 8));
    temporary_kit_present(&c, 10, 1); hos_oot_coop_kit_update(&c); assert(native_calls == 1); /* not repeated per frame */
    current.accepted = 0; gameplay_revoke_locked(&room);
    assert(!hos_oot_coop_kit_ready(7, 8) && G.kit.state == OOT_KIT_CANCELLED);
    assert(!coop_gameplay_dispatch(&G.gameplay, &stale, &G.kit_work, sizeof G.kit_work, &c));
    current.accepted = 1; temporary_kit_present(&c, 10, 1); hos_oot_coop_kit_update(&c); assert(native_calls == 1);
    setup(); current.accepted = 1; temporary_kit_present(&c, 10, 1); current.rules.epoch++;
    hos_oot_coop_kit_update(&c); assert(!native_calls && !G.gameplay.context.capabilities);
    setup(); current.accepted = 1; temporary_kit_present(&c, 10, 1); room.state = COOP_LEFT;
    hos_oot_coop_kit_update(&c); assert(!native_calls && !G.gameplay.context.capabilities);
    setup(); current.accepted = 1; temporary_kit_present(&c, 10, 1); room.session++;
    hos_oot_coop_kit_update(&c); assert(!native_calls && !G.gameplay.context.capabilities);
    setup(); current.accepted = 1; G.bound = 0; temporary_kit_present(&c, 10, 1);
    assert(G.bound && G.save_generation == 2 && !current.accepted && !G.gameplay.context.capabilities);
    setup(); current.accepted = 1; temporary_kit_present(&c, 10, 0); hos_oot_coop_kit_update(&c); assert(!native_calls);
    setup(); current.accepted = 1; assert(coop_feature_set(&current.rules, 40, 16)); temporary_kit_present(&c, 10, 1);
    hos_oot_coop_kit_update(&c); assert(!native_calls && !G.gameplay.context.capabilities);
    setup(); temporary = 0; CoopOffer o; assert(!offer_of(&o, 1) && strstr(o.refusal, "Temporary save"));
    temporary = 1; assert(offer_of(&o, 1));
    puts("PASS: actual kit publication and dispatch: off/unaccepted/no progress, apply-time revoke, stale ticket, changed rules/session, load reacceptance, repeat and normal-save offer refusal");
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cc", default="gcc")
    p.add_argument("--source-root", type=Path, default=ROOT)
    a = p.parse_args()
    spec = importlib.util.spec_from_file_location("oot_fixture", ROOT / "tests/test_oot_coop.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    source = (a.source_root / "host/oot_coop.c").read_text()
    functions = ["static void gameplay_revoke_locked(", "static int offer_of(", "static int kit_write(",
                 "static void temporary_kit_present(", "void hos_oot_coop_kit_update(", "int hos_oot_coop_kit_ready("]
    production = "\n".join(helper.piece(source, marker) for marker in functions)
    state = (a.source_root / "host/coop_state.c").read_text()
    registry = state[state.index("// Add each lane's definitions"):state.index("static void features_write(")]
    registry = registry.replace(helper.piece(state, "static int features_equal("), "")
    with tempfile.TemporaryDirectory(prefix="coop-kit-gate-") as name:
        folder = Path(name)
        c, exe = folder / "fixture.c", folder / "fixture.exe"
        c.write_text(FIXTURE.replace("@@REGISTRY@@", registry).replace("@@PRODUCTION@@", production))
        subprocess.run([a.cc, "-std=c11", "-O1", "-Wall", "-Wextra", "-Werror", "-D_CRT_SECURE_NO_WARNINGS",
                        "-I" + str(a.source_root / "host"), str(c), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
