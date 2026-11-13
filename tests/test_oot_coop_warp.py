"""Safe local Go to friend: production state machine, real POS store and capability owner, native adapter.
No dump or game. --mutations compiles deliberately broken copies; each must fail behaviorally.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cc", "gcc", "--mutations"]
ROOT = Path(__file__).resolve().parents[1]

FIXTURE = r'''
#include <assert.h>
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "coop.h"
#include "coop_players.h"
#include "oot_coop.h"
#include "oot_coop_warp.h"
typedef int SRWLOCK;
#define SRWLOCK_INIT 0
static void AcquireSRWLockExclusive(SRWLOCK *l) { (void)l; }
static void ReleaseSRWLockExclusive(SRWLOCK *l) { (void)l; }
static int TryAcquireSRWLockExclusive(SRWLOCK *l) { (void)l; return 1; }
static uint64_t now = 1000;
uint64_t coop_now_ms(void) { return now; }
static char log_lines[16384];
void hos_log(const char *fmt, ...) {
    const size_t length = strlen(log_lines);
    va_list args; va_start(args, fmt);
    vsnprintf(log_lines + length, sizeof log_lines - length, fmt, args);
    va_end(args);
}
static CoopView view;
static CoopOffer offer;
static CoopPlayers poses;
static CoopGameplay owner;
static CoopGameplayContext context;
static OotCoopWarpLocal local;
static int playing, local_ok, placed, place_ok, travels, travel_ok, accepted, busy;
static uint32_t sequence;
void coop_view_get(CoopView *out) { *out = view; }
int hos_oot_coop_offer(CoopOffer *out) { *out = offer; return offer.token != 0; }
int coop_member_accepted(int id) { return accepted && id == 2; }
int hos_oot_coop_playing(void) { return playing; }
int hos_oot_coop_gameplay_register(const CoopGameplayWriter *w) { return coop_gameplay_register(&owner, w); }
int hos_oot_coop_gameplay_ticket(unsigned slot, unsigned member, CoopGameplayTicket *t) {
    return coop_gameplay_ticket(&owner, slot, member, context.accepted_session[member], t);
}
int hos_oot_coop_gameplay_dispatch(const CoopGameplayTicket *t, const void *p, size_t n, void *g) {
    return busy ? -1 : coop_gameplay_dispatch(&owner, t, p, n, g);
}
int hos_oot_coop_players_snapshot(int id, CoopPos *out, uint64_t *age) {
    return coop_players_last(&poses, id, now, out, age);
}
int hos_oot_coop_warp_local(Ctx *c, uint64_t actor, uint64_t comp, OotCoopWarpLocal *out) {
    (void)c; (void)actor; (void)comp; *out = local; return local_ok;
}
int hos_oot_coop_warp_event(Ctx *c) { (void)c; return local.event; }
int hos_oot_coop_warp_place(Ctx *c, uint64_t comp, const float pos[3]) {
    (void)c; (void)comp;
    if (!place_ok) return 0;
    placed++;
    memcpy(local.pos, pos, sizeof local.pos);
    return 1;
}
int hos_oot_coop_warp_destination(const char *map, int child) {
    if (!strcmp(map, child ? "Map_A" : "Map_A_Adult")) return 0;
    if (!strcmp(map, child ? "Map_B" : "Map_B_Adult")) return 1;
    return -1;
}
int hos_oot_coop_warp_travel(Ctx *c, uint64_t actor, int destination) {
    (void)c; (void)actor; assert(destination == 1); travels++; return travel_ok;
}
@@WARP@@
static void publish(void) { coop_gameplay_publish(&owner, &context); }
static void feed(const char *map, int flags, float x) {
    CoopPos p = {.pseq = ++sequence, .t_ms = (uint32_t)now, .flags = (uint8_t)flags, .pos = {x, 5, 20}, .yaw = 0};
    snprintf(p.area, sizeof p.area, "%s", map);
    uint8_t b[COOP_MAX_MESSAGE];
    size_t n = coop_pos_pack(b, sizeof b, &p);
    assert(n && coop_players_receive(&poses, 2, b, n, now));
}
static void fresh(void) { feed("Map_B", COOP_POS_IN_WORLD | COOP_POS_LOADED, 30); }
static void reset(void) {
    memset(&W, 0, sizeof W); memset(&view, 0, sizeof view); memset(&offer, 0, sizeof offer);
    memset(&poses, 0, sizeof poses); memset(&local, 0, sizeof local); memset(&owner, 0, sizeof owner);
    memset(&context, 0, sizeof context); memset(log_lines, 0, sizeof log_lines);
    now += 100000;
    view.state = COOP_JOINED, view.self = 1, view.n = 2;
    view.member[0].id = 1, view.member[0].session = 101;
    view.member[1].id = 2, view.member[1].session = 202;
    const int ids[] = {1, 2}; const uint64_t sessions[] = {101, 202};
    coop_players_roster(&poses, 1, ids, sessions, 2);
    offer.token = 12, offer.accepted = 1, offer.n = 1, offer.member[0].id = 2;
    snprintf(offer.member[0].key, sizeof offer.member[0].key, "1111-2222-3333-4444");
    coop_rules_default(&offer.rules);
    assert(coop_feature_set(&offer.rules, COOP_RULE_WARP, 1));
    offer.rules.epoch = 1;
    context.version = COOP_GAMEPLAY_VERSION, context.size = sizeof context;
    context.capabilities = COOP_GAMEPLAY_TEMPORARY, context.offer = 12, context.rules_epoch = 1;
    context.room_session = 101, context.save_generation = 1, context.area_generation = 1;
    context.accepted_session[2] = 202; publish();
    playing = local_ok = place_ok = travel_ok = accepted = 1;
    travels = placed = busy = sequence = 0;
    snprintf(local.map, sizeof local.map, "Map_A"); local.child = local.safe = 1;
    fresh();
}
static void tick(unsigned ms) { now += ms; Ctx c = {0}; hos_oot_coop_warp_update(&c, 1, 2); }
static int ask(void) { return hos_oot_coop_warp_ask(2, "1111-2222-3333-4444"); }
static void says(const char *word) { char text[160]; hos_oot_coop_warp_note(text, sizeof text); assert(strstr(text, word)); }
static void refused(const char *word) { assert(!ask()); says(word); assert(!travels && !placed && !W.active); }
static void queued_refusal(const char *word) { assert(ask()); tick(1); says(word); assert(!travels && !placed && !W.active); }
static void start_travel(void) { assert(ask()); tick(1); assert(travels == 1 && W.phase == WARP_LOAD && !placed); }
static void arrival(void) {
    start_travel(); local.loading = 1; tick(1); assert(!placed);
    snprintf(local.map, sizeof local.map, "Map_B"); local.loading = 0; local.event = 1; tick(1); assert(!placed);
    local.event = 0; context.area_generation++; publish(); tick(1);
    assert(W.phase == WARP_POSE && !placed);
}
static void cancel_check(int started) {
    tick(1); says(started ? "Travel cancelled after the map had started loading" : "Travel cancelled, you stayed.");
    assert(!W.active && !placed && travels == started);
    assert(strstr(log_lines, started ? "Travel cancelled after the map had started loading" : "Travel cancelled, you stayed."));
    tick(100000); assert(!placed && travels == started); // cancelled work never revives later
}
int main(void) {
    reset(); assert(coop_feature_definition(COOP_RULE_WARP)->def == 0);
    assert(!coop_feature_set(&offer.rules, COOP_RULE_WARP, -1) && !coop_feature_set(&offer.rules, COOP_RULE_WARP, 2));
    assert(coop_feature_set(&offer.rules, COOP_RULE_WARP, 0)); refused("off in this room");
    reset(); offer.rules.features.count = 0; refused("off in this room");
    reset(); view.state = COOP_LEFT; refused("Join a room");
    reset(); assert(!hos_oot_coop_warp_ask(1, "x")); says("another player");
    reset(); view.n = 1; refused("left");
    reset(); view.member[1].away = 1; refused("away");
    reset(); offer.accepted = 0; refused("Accept");
    reset(); accepted = 0; refused("identity");
    reset(); assert(!hos_oot_coop_warp_ask(2, "changed")); says("changed");
    reset(); memset(&poses.r[2], 0, sizeof poses.r[2]); refused("No current");
    reset(); now += WARP_FRESH_MS; refused("stale");
    reset(); feed("", 0, 0); refused("title screen");
    reset(); feed("Map_B", COOP_POS_LOADED, 0); refused("event");
    reset(); feed("Map_B", COOP_POS_IN_WORLD | COOP_POS_LOADED | COOP_POS_SLOW, 0); refused("precise");
    reset(); feed("", COOP_POS_IN_WORLD | COOP_POS_LOADED, 0); refused("unknown");
    reset(); playing = 0; refused("Load your save");
    reset(); local.event = 1; queued_refusal("event is playing");
    reset(); local.event = 1; assert(ask()); { Ctx c = {0}; hos_oot_coop_warp_present(&c); }
    says("event is playing"); assert(!W.active && !travels && !placed);
    reset(); local.loading = 1; queued_refusal("safely");
    reset(); local.safe = 0; queued_refusal("safely");
    reset(); local.travelling = 1; queued_refusal("already under way");
    reset(); local_ok = 0; assert(ask()); cancel_check(0);
    reset(); feed("Map_B_Adult", COOP_POS_IN_WORLD | COOP_POS_LOADED | COOP_POS_ADULT, 10); queued_refusal("ages differ");
    reset(); feed("Map_B", COOP_POS_IN_WORLD | COOP_POS_LOADED | COOP_POS_ADULT, 10); queued_refusal("ages differ");
    reset(); feed("BossRoom", COOP_POS_IN_WORLD | COOP_POS_LOADED, 10); queued_refusal("safe supported");
    reset(); travel_ok = 0; assert(ask()); tick(1); says("refused the map change; you stayed"); assert(travels == 1 && !placed);
    reset(); assert(ask()); assert(!ask()); says("already under way"); tick(1); assert(travels == 1);
    // Before request: cancellation does not move the player or request a map.
    reset(); assert(ask()); context.capabilities = 0; publish(); cancel_check(0);
    reset(); assert(ask()); now += WARP_ASK_MS; cancel_check(0);
    // Between request and load: no rollback, no placement, no duplicate request.
    reset(); start_travel(); context.rules_epoch++; publish(); cancel_check(1);
    // After load but before placement, including revoke/reaccept between updates.
    reset(); arrival(); coop_gameplay_publish(&owner, NULL); publish(); cancel_check(1);
    reset(); arrival(); view.state = COOP_LEFT; cancel_check(1);
    reset(); arrival(); context.accepted_session[2]++; publish(); cancel_check(1);
    reset(); arrival(); context.save_generation++; publish(); cancel_check(1);
    reset(); arrival(); now += WARP_LOAD_MS; cancel_check(1);
    // Friend changes map during load: stale target, never an automatic second journey.
    reset(); start_travel(); local.loading = 1; feed("Map_A", COOP_POS_IN_WORLD | COOP_POS_LOADED, 9); cancel_check(1);
    reset(); arrival(); feed("Map_A", COOP_POS_IN_WORLD | COOP_POS_LOADED, 9); cancel_check(1);
    reset(); arrival(); tick(100); assert(!placed); // pre-load packet never places
    fresh(); tick(1); assert(placed == 1 && fabsf(local.pos[0] - 31.25f) < .001f);
    tick(200); says("Arrived near"); assert(!W.active && travels == 1);
    tick(500); assert(travels == 1 && placed == 1);
    // Same-area placement changes no map, including cancellation in that phase.
    reset(); snprintf(local.map, sizeof local.map, "Map_B"); assert(ask()); tick(1); assert(!travels && !placed);
    fresh(); tick(1); tick(200); says("Arrived near"); assert(!travels && placed == 1);
    reset(); snprintf(local.map, sizeof local.map, "Map_B"); assert(ask()); tick(1); context.offer++; publish(); cancel_check(0);
    reset(); arrival(); place_ok = 0; fresh(); cancel_check(1);
    reset(); arrival(); now += WARP_POSE_MS; tick(1); says("Travel cancelled after"); assert(!placed);
    reset(); arrival(); fresh(); busy = 1; tick(1); assert(!placed); busy = 0; tick(1); assert(placed == 1);
    reset(); arrival(); fresh(); tick(1); local.pos[0] += 100; tick(200); says("did not keep"); assert(placed == 1 && travels == 1);
    reset(); poses.r[2].pose[poses.r[2].head].pos[0] = NAN; refused("unsafe");
    reset(); poses.r[2].pose[poses.r[2].head].yaw = INFINITY; refused("unsafe");
    puts("PASS: travel refusals, four cancellation phases, owner revoke/reaccept, first cross-map and same-map placement, no double travel");
}
'''

NATIVE = r'''
#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "oot_coop_warp.h"
typedef void (*A64Fn)(Ctx *);
static uint32_t stack[256];
static int available = 1, calls, broken_link, event, flight, loading, childhood = 1, bool_ok = 1;
static int teleports, native_ok = 1;
static const char *override_event, *override_starter;
static const char *fake_getenv(const char *name) { return strstr(name, "STARTER") ? override_starter : override_event; }
#define getenv fake_getenv
static const char *hos_main_build_id = "e0f3ccfaf50065ae4ea01eb5aeb13b1a45da3348";
#define OOT_EVENTMGR_SLOT 6000
#define HOS_OPT_CHEAT_FLIGHT 33
static struct { int64_t requested_at; int result; } teleport;
static struct { int asked; } oot_bridge_tp;
static const struct { const char *child_banc, *adult_banc; } oot_destinations[] = {{"Map_A", "Map_A_Adult"}, {"Map_B", "Map_B_Adult"}, {"BossRoom", "BossRoom"}};
static void *hos_guest_range(uint64_t address, uint64_t size) { return address && address <= UINT64_MAX - size ? stack : NULL; }
static uint64_t recomp_load64(Ctx *c, uint64_t a) {
    (void)c;
    if (a == 0x84c54f10ULL + 0xe8) return 0x806d2530ULL;
    if (a == 179) return broken_link ? 0 : 200;
    if (a == 232) return 300;
    if (a == 316) return 400;
    if (a == 408) return 77;
    if (a == 77) return 0x84c54f10ULL;
    if (a == 6000) return 6100;
    if (a == 6100) return 6200;
    if (a == 6304) return (uint64_t)event;
    assert(0); return 0;
}
static int oot_gamedata_string(Ctx *c, uint32_t hash, char *out, size_t cap) { (void)c; (void)hash; snprintf(out, cap, "Map_A"); return 1; }
static int oot_gamedata_bool(Ctx *c, uint32_t hash, int *out) { (void)c; *out = hash == 0xef2b60c2u ? loading : childhood; return bool_ok; }
static float oot_load_float(Ctx *c, uint64_t address) { (void)c; (void)address; return 1; }
static int rx_option(int id, int fallback) { (void)fallback; assert(id == HOS_OPT_CHEAT_FLIGHT); return flight; }
static int64_t hos_now_ns(void) { return 10000000000LL; }
static int oot_teleport(Ctx *c, uint64_t actor, unsigned destination) { (void)c; assert(actor == 1 && destination == 1); teleports++; return native_ok; }
static void recomp_store32_sp(Ctx *c, uint64_t address, uint32_t value) { (void)c; assert(address < sizeof stack); stack[address / 4] = value; }
static void move(Ctx *c) {
    calls++; assert(c->x[0] == 77 && c->x[1] == 496 && c->x[31] == 496);
    float pos[3]; memcpy(pos, stack + 496 / 4, sizeof pos);
    assert(pos[0] == 1 && pos[1] == 2 && pos[2] == 3 && !stack[508 / 4]);
    memset(c, 0xa5, sizeof *c);
}
static A64Fn a64_lookup(uint64_t address) { assert(address == 0x806d2530ULL); return available ? move : NULL; }
@@NATIVE@@
int main(void) {
    Ctx c = {0}; c.x[31] = 512; Ctx before = c;
    const float pos[3] = {1, 2, 3};
    assert(hos_oot_coop_warp_place(&c, 99, pos) && calls == 1 && !memcmp(&c, &before, sizeof c));
    available = 0; assert(!hos_oot_coop_warp_place(&c, 99, pos) && calls == 1); available = 1;
    broken_link = 1; assert(!hos_oot_coop_warp_place(&c, 99, pos) && calls == 1); broken_link = 0;
    float bad[3] = {NAN, 2, 3}; assert(!hos_oot_coop_warp_place(&c, 99, bad));
    assert(!hos_oot_coop_warp_place(&c, UINT64_MAX, pos));
    assert(hos_oot_coop_warp_destination("Map_A", 1) == 0 && hos_oot_coop_warp_destination("Map_B_Adult", 0) == 1);
    assert(hos_oot_coop_warp_destination("Map_B", 0) < 0 && hos_oot_coop_warp_destination("BossRoom", 1) < 0);
    OotCoopWarpLocal local;
    assert(hos_oot_coop_warp_local(&c, 1, 99, &local) && local.safe && !local.event && !local.travelling);
    flight = 1; assert(hos_oot_coop_warp_local(&c, 1, 99, &local) && !local.safe); flight = 0;
    event = 1; assert(hos_oot_coop_warp_local(&c, 1, 99, &local) && local.event); event = 0;
    bool_ok = 0; assert(!hos_oot_coop_warp_local(&c, 1, 99, &local)); bool_ok = 1;
    oot_bridge_tp.asked = 1; assert(hos_oot_coop_warp_local(&c, 1, 99, &local) && local.travelling); oot_bridge_tp.asked = 0;
    hos_main_build_id = "other"; assert(!hos_oot_coop_warp_local(&c, 1, 99, &local));
    teleport.result = 1; assert(hos_oot_coop_warp_travel(&c, 1, 1) && teleports == 1);
    teleport.result = 0; assert(!hos_oot_coop_warp_travel(&c, 1, 1) && teleports == 2);
    override_event = "other"; assert(!hos_oot_coop_warp_travel(&c, 1, 1) && teleports == 2); override_event = NULL;
    override_starter = "0"; assert(!hos_oot_coop_warp_travel(&c, 1, 1) && teleports == 2); override_starter = NULL;
    assert(!hos_oot_coop_warp_travel(&c, 1, 2) && teleports == 2);
    puts("PASS: native destination allowlist, build/controller/event gates, map-request acceptance, position arguments and register restoration");
}
'''


def block(text, marker):
    start = text.index(marker)
    return text[start:text.index("\n}\n", start) + 3]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc")
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--mutations", action="store_true")
    args = parser.parse_args()
    root = args.source_root.resolve()
    source = (root / "host/oot_coop_warp.c").read_text(encoding="utf-8")
    production = source[source.index("enum { WARP_IDLE"):]
    native = (root / "host/hos_main.c").read_text(encoding="utf-8")
    hook = block(native, "void hos_oot_lift_player(")
    assert hook.index("hos_oot_is_player(c, movement - 0x1d50)") < hook.index("hos_oot_coop_warp_update(c, actor, components)")
    assert hook.index("oot_update_player_cheats(c, actor, components)") < hook.index("hos_oot_coop_warp_update(c, actor, components)")
    native_cut = "\n".join(block(native, marker) for marker in (
        "static uint64_t oot_warp_controller(", "int hos_oot_coop_warp_destination(", "int hos_oot_coop_warp_event(",
        "int hos_oot_coop_warp_local(", "int hos_oot_coop_warp_travel(", "int hos_oot_coop_warp_place("))
    with tempfile.TemporaryDirectory(prefix="coop-travel-") as temp:
        folder = Path(temp)
        (folder / "a64.h").write_text("#pragma once\n#include <stdint.h>\ntypedef struct { uint64_t x[32]; } Ctx;\n")
        mono = folder / "mono.o"
        subprocess.run([args.cc, "-std=c99", "-O1", "-w", "-c", str(root / "third_party/monocypher/monocypher.c"), "-o", str(mono)], check=True)
        extra = [str(root / "host" / f) for f in ("coop_state.c", "coop_players.c", "coop_proto.c")] + [str(mono), "-lm"]

        def run(name, text, objects, broken=False):
            file, exe = folder / (name + ".c"), folder / (name + ".exe")
            file.write_text(text, encoding="utf-8")
            subprocess.run([args.cc, "-std=c11", "-D_CRT_SECURE_NO_WARNINGS", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                            "-I" + str(folder), "-I" + str(root / "host"), str(file), *objects, "-o", str(exe)], check=True)
            result = subprocess.run([str(exe)], capture_output=broken, text=True)
            if broken:
                assert result.returncode != 0, "mutation survived: " + name
                print("CAUGHT:", name, "exit", result.returncode, flush=True)
            else:
                result.check_returncode()

        run("travel", FIXTURE.replace("@@WARP@@", production), extra)
        run("native", NATIVE.replace("@@NATIVE@@", native_cut), ["-lm"])
        if args.mutations:
            mutations = {
                "stale": ("*age >= WARP_FRESH_MS", "*age >= UINT64_MAX"),
                "rule_off": ("|| on != 1", "|| on == 7"),
                "unaccepted": ("if (!coop_member_accepted(id))", "if (0 && !coop_member_accepted(id))"),
                "local_event": ("if (!why && local.event)", "if (0 && !why && local.event)"),
                "single_flight": ("if (W.phase != WARP_IDLE) {", "if (0 && W.phase != WARP_IDLE) {"),
                "fresh_arrival": ("if (age > now - W.arrived)", "if (0 && age > now - W.arrived)"),
                "cancel_message": ("finish(W.started ?", "finish(0 ?"),
            }
            for name, (old, new) in mutations.items():
                assert old in production, name
                run(name, FIXTURE.replace("@@WARP@@", production.replace(old, new, 1)), extra, True)
            run("age_and_map", FIXTURE.replace("@@WARP@@", production).replace('return -1;\n}\nint hos_oot_coop_warp_travel', 'return 1;\n}\nint hos_oot_coop_warp_travel'), extra, True)
            print("PASS: 8 compiled behavioral mutations caught", flush=True)


if __name__ == "__main__":
    main()
