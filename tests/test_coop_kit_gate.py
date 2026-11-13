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
    uint32_t kit_offer, kit_epoch;
    uint64_t kit_session, kit_generation, save_generation, area_generation;
    CoopView view;
} G;
static int locked, native_calls, temporary = 1;
static volatile int hos_oot_coop_kit_active;
static CoopOffer current;
static CoopView room;
static int TryAcquireSRWLockExclusive(int *lock) { (void)lock; assert(!locked); locked = 1; return 1; }
static void ReleaseSRWLockExclusive(int *lock) { (void)lock; assert(locked); locked = 0; }
static uint64_t coop_now_ms(void) { return 1; }
static void hos_log(const char *fmt, ...) { (void)fmt; }
int coop_profile_active(void) { return temporary; }
void coop_view_get(CoopView *out) { *out = room; }
void coop_sync_temporary(CoopSync *s, int active) { (void)s; (void)active; }
uint32_t coop_sync_presence_members(CoopSync *s) { (void)s; return current.presence_accepted ? 1u << 2 : 0; }
int coop_sync_offer(CoopSync *s, CoopOffer *out, uint64_t now) { (void)s; (void)now; *out = current; return current.token != 0; }
int coop_sync_open(CoopSync *s) { (void)s; return 0; }
int coop_sync_member_accepted(const CoopSync *s, int id, uint64_t session) { (void)s; (void)id; (void)session; return 0; }
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
    temporary=1; G.on=G.bound=G.loaded=G.gate=1; G.eng=(CoopSync *)(uintptr_t)1;
    G.save_generation=G.area_generation=1;
    room.state=COOP_JOINED; room.session=101; room.n=1; room.member[0].id=2; room.member[0].session=202;
    G.view=room; current.token=7; current.rules.epoch=8;
    assert(coop_feature_set(&current.rules,32,0)); assert(coop_feature_set(&current.rules,40,3));
    assert(coop_feature_set(&current.rules,83,1)); assert(coop_feature_set(&current.rules,COOP_RULE_PRESENCE,1));
    native_calls=0; hos_oot_coop_kit_active=0;
}
static void frame(Ctx *c, int open) {
    TryAcquireSRWLockExclusive(&G.lock); gameplay_context_locked(); ReleaseSRWLockExclusive(&G.lock);
    temporary_kit_present(c,10,open);
}
static void accept_kit(Ctx *c) {
    char stamp[64]; current.presence_accepted=1; frame(c,1);
    assert(hos_oot_coop_kit_token(stamp,sizeof stamp) && hos_oot_coop_kit_accept(stamp,current.token));
    frame(c,1);
}
int main(void) {
    Ctx c={0}; char stamp[64]; setup(); G.on=0; hos_oot_coop_kit_update(&c); assert(!native_calls); G.on=1;
    frame(&c,1); hos_oot_coop_kit_update(&c); assert(!native_calls && !G.gameplay.context.capabilities);
    current.presence_accepted=1; frame(&c,1); hos_oot_coop_kit_update(&c);
    assert(!native_calls && G.gameplay.context.capabilities==COOP_GAMEPLAY_PRESENTATION); /* presence alone is not a kit */
    accept_kit(&c); CoopGameplayTicket stale;
    assert(coop_gameplay_ticket(&G.gameplay,COOP_GAMEPLAY_KIT,0,101,&stale));
    assert(!hos_oot_coop_kit_ready(7,8)); hos_oot_coop_kit_update(&c); assert(native_calls==1 && hos_oot_coop_kit_ready(7,8));
    frame(&c,1); hos_oot_coop_kit_update(&c); assert(native_calls==1);
    current.presence_accepted=0; gameplay_revoke_locked(&room);
    assert(!hos_oot_coop_kit_ready(7,8) && G.kit.state==OOT_KIT_CANCELLED);
    assert(!coop_gameplay_dispatch(&G.gameplay,&stale,&G.kit_work,sizeof G.kit_work,&c));
    accept_kit(&c); hos_oot_coop_kit_update(&c); assert(native_calls==1); /* reaccept never gives again */
    for(int changed=0;changed<4;changed++) {
        setup(); accept_kit(&c); assert(hos_oot_coop_kit_token(stamp,sizeof stamp));
        if(changed==0)current.rules.epoch++;
        if(changed==1)room.state=COOP_LEFT;
        if(changed==2)room.session++;
        if(changed==3)G.save_generation++;
        assert(!hos_oot_coop_kit_accept(stamp,7)); hos_oot_coop_kit_update(&c);
        assert(!native_calls && !G.gameplay.context.capabilities);
    }
    setup(); accept_kit(&c); frame(&c,0); hos_oot_coop_kit_update(&c); assert(!native_calls);
    setup(); current.presence_accepted=1; assert(coop_feature_set(&current.rules,40,16));
    assert(!hos_oot_coop_kit_token(stamp,sizeof stamp)); frame(&c,1); hos_oot_coop_kit_update(&c); assert(!native_calls);
    setup(); current.presence_accepted=1; assert(hos_oot_coop_kit_token(stamp,sizeof stamp)); temporary=0;
    assert(!hos_oot_coop_kit_accept(stamp,7) && !hos_oot_coop_kit_token(stamp,sizeof stamp));
    CoopOffer o; assert(!offer_of(&o,1) && strstr(o.refusal,"Temporary save")); temporary=1; assert(offer_of(&o,1));
    puts("PASS: kit requires explicit copy consent; presence-only, wrong generation, normal save, epoch/session/leave, stale ticket, off, repeat and unsupported kit refuse");
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
    functions = ["static int presentation_locked(", "static int kit_consent_locked(", "static int kit_stamp_locked(",
                 "int hos_oot_coop_kit_token(", "int hos_oot_coop_kit_accept(", "static void gameplay_revoke_locked(",
                 "static int offer_of(", "static void gameplay_context_locked(", "static int kit_write(",
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
