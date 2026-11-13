"""Execute the runtime's actual progress writer/dispatch with a recording native boundary."""
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
#include "coop_state.h"
#include "coop_gameplay.h"
typedef struct { int unused; } Ctx;
typedef struct { uint32_t hash; int cat, is_int; } GFlag;
static GFlag flag = {123, COOP_CAT_ITEMS, 0};
static int locked, guest_calls, writes, takes, cancel_after_take, untaken;
static int temporary;
int coop_profile_active(void) { return temporary; }
static int TryAcquireSRWLockExclusive(int *lock) { (void)lock; assert(!locked); locked = 1; return 1; }
static void ReleaseSRWLockExclusive(int *lock) { (void)lock; assert(locked); locked = 0; }
static struct {
    int lock, open, bound, gate, loaded, presence;
    CoopSync *eng;
    CoopGameplay gameplay;
    uint64_t save_generation, area_generation, applied_log_at;
    unsigned applied[COOP_CAT_N], applied_total;
} G;
static CoopOffer current;
static CoopView room;
#define TRACE_ON 0
enum { APPLY_PER_FRAME = 8 };
static uint64_t coop_now_ms(void) { return 1; }
static void hos_log(const char *format, ...) { (void)format; }
void coop_view_get(CoopView *out) { *out = room; }
int coop_sync_offer(CoopSync *engine, CoopOffer *out, uint64_t now) { (void)engine; (void)now; *out = current; return current.token != 0; }
int coop_sync_open(CoopSync *engine) { (void)engine; return current.accepted; }
int coop_sync_member_accepted(const CoopSync *engine, int id, uint64_t session) { (void)engine; return id == 2 && session == 202 && current.accepted; }
static int presence_rule_present, presence_rule_value;
int coop_feature_present(const CoopRules *rules, int id, int *value) {
    (void)rules; assert(id == 99); *value = presence_rule_value; return presence_rule_present;
}
int coop_sync_take(CoopSync *engine, CoopApply *out, int max) {
    (void)engine; assert(locked && max > 0); takes++;
    out[0] = (CoopApply){0, COOP_CAT_ITEMS, 123, 1};
    if (cancel_after_take) coop_gameplay_publish(&G.gameplay, NULL);
    return 1;
}
void coop_sync_untake(CoopSync *engine, const CoopApply *value) { (void)engine; (void)value; untaken++; }
void coop_sync_pool_wrote(CoopSync *engine, uint32_t hash, int32_t value) { (void)engine; (void)hash; (void)value; }
const char *coop_cat_name(int category) { (void)category; return "items"; }
static const GFlag *flag_by_hash(uint32_t hash) { return hash == flag.hash ? &flag : NULL; }
static int int_pending(Ctx *ctx, uint64_t mgr, uint32_t hash) { (void)ctx; (void)mgr; (void)hash; guest_calls++; return 0; }
static int32_t raw_of(Ctx *ctx, const GFlag *f) { (void)ctx; (void)f; guest_calls++; return 0; }
static int apply_value(const GFlag *f, const CoopApply *value, int32_t raw, int32_t *out) { (void)raw; *out = value->value; return f != NULL; }
static int set_flag(Ctx *ctx, uint64_t mgr, int integer, uint32_t hash, int32_t value) {
    (void)ctx; (void)mgr; (void)integer; assert(locked && hash == 123 && value == 1); writes++; guest_calls++; return 1;
}
@@PRODUCTION@@
int main(void) {
    Ctx ctx = {0};
    G.eng = (CoopSync *)(uintptr_t)1;
    apply(&ctx, 1234); assert(!takes && !guest_calls && !writes);
    G.open = G.bound = G.gate = G.loaded = 1;
    G.save_generation = 1; G.area_generation = 1;
    room.state = COOP_JOINED; room.session = 101; room.n = 1;
    room.member[0].id = 2; room.member[0].session = 202;
    current.token = 9; current.accepted = 1; current.rules.epoch = 3;
    apply(&ctx, 1234); assert(takes == 1 && writes == 1 && !locked);
    assert(!G.presence);   // missing permission is not the registry default
    presence_rule_present = 1;
    gameplay_context_locked(); assert(!G.presence);   // explicit off
    presence_rule_value = 1;
    gameplay_context_locked(); assert(G.presence);
    G.save_generation = 0;
    gameplay_context_locked(); assert(!G.presence);   // invalid native context fails closed
    G.save_generation = 1, presence_rule_value = 0;
    gameplay_context_locked(); assert(!G.presence);
    cancel_after_take = 1;
    const int before = guest_calls;
    apply(&ctx, 1234); assert(takes == 2 && writes == 1 && guest_calls == before && !locked);
    cancel_after_take = 0;
    current.accepted = 0;
    apply(&ctx, 1234); assert(takes == 2 && guest_calls == before && writes == 1);
    current.accepted = 1; G.gate = 0;
    apply(&ctx, 1234); assert(takes == 2 && guest_calls == before && writes == 1);
    G.gate = 1; temporary = 1;
    apply(&ctx, 1234); assert(takes == 2 && guest_calls == before && writes == 1 && !G.gameplay.context.capabilities);
    assert(!untaken);
    puts("PASS: actual progress apply uses one registered writer; off, revoked queued ticket, revoked acceptance and invalid game context make no native calls");
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cc", default="gcc")
    p.add_argument("--source-root", type=Path, default=ROOT)
    args = p.parse_args()
    spec = importlib.util.spec_from_file_location("oot_fixture", ROOT / "tests/test_oot_coop.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = (args.source_root / "host/oot_coop.c").read_text()
    markers = ["typedef struct { Ctx *ctx; uint64_t mgr; } ProgressWrite;", "static int progress_write(",
               "static void gameplay_context_locked(", "static void apply("]
    production = "\n".join(module.piece(source, marker) for marker in markers)
    with tempfile.TemporaryDirectory(prefix="coop-progress-owner-") as name:
        folder = Path(name)
        fixture, exe = folder / "fixture.c", folder / "fixture.exe"
        fixture.write_text(FIXTURE.replace("@@PRODUCTION@@", production))
        subprocess.run([args.cc, "-std=c11", "-O1", "-Wall", "-Wextra", "-Werror", "-I" + str(args.source_root / "host"),
                        str(fixture), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
