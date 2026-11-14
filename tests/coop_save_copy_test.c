/* Authored checkpoints, two signed room engines and actual copy storage. The
   host tick/publication and save bridge run unchanged. Native play/backup and
   transport scheduling are seams; the opt-in worker is stepped synchronously. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "coop_state.c"
#include "coop.h"
#include "coop_net.h"
#include "coop_profile.h"
#include "coop_save.h"
#include "coop_gameplay.h"
#include "coop_chat.h"
#include "oot_coop_kit.h"
#ifndef _WIN32
typedef int SRWLOCK;
#define AcquireSRWLockExclusive(p) ((void)(p))
#define AcquireSRWLockShared(p) ((void)(p))
#define ReleaseSRWLockExclusive(p) ((void)(p))
#define ReleaseSRWLockShared(p) ((void)(p))
#define TryAcquireSRWLockExclusive(p) ((void)(p), 1)
#endif

typedef struct {
    SRWLOCK lock, pub_lock;
    int on, bound, gate, loaded, presence, open, pub_has, pub_maker, pub_ncontrib;
    int view_any, logged_unid, said_disagree;
    unsigned view_seen, rules_seen;
    uint32_t logged, pub_shown, want_accept, kit_offer, kit_epoch;
    uint64_t save_generation, area_generation, kit_session, kit_generation;
    uint64_t pub_contrib_at, want_remove_at, want_reset;
    char want_remove[24];
    CoopSync *eng;
    CoopView view;
    CoopOffer pub;
    CoopContributor pub_contrib[24];
    CoopGameplay gameplay;
    OotCoopKitRun kit;
    CoopSave *save;
    uint32_t accepted, presented, downloads;
    int chat, hits;
    unsigned sections, exports;
} Host;
static Host hosts[2], *selected;
#define G (*selected)
static volatile int hos_oot_coop_kit_active;
static uint64_t clock_ms;
static int deliver = 1;
static void on_message(int from, const uint8_t *bytes, size_t n, void *user);
void coop_view_get(CoopView *out) { *out = G.view; }
void coop_accept_members(const CoopView *v, uint32_t mask) { G.accepted = v ? mask : 0; }
void coop_presence_members(const CoopView *v, uint32_t mask) { G.presented = v ? mask : 0; }
void coop_download_members(const CoopView *v, uint32_t mask) { G.downloads = v ? mask : 0; }
static void hos_oot_hits_room(const CoopRules *r, int accepted) { G.hits = r && accepted; }
void hos_coop_chat_room(const CoopView *v, const CoopOffer *o, uint32_t members) { G.chat = v && o && o->accepted && members; }
void hos_coop_chat_scene(int safe) { (void)safe; }
static void hos_log(const char *fmt, ...) { (void)fmt; }
static int accept_always(void) { return 0; }
static void sync_on(void) { assert(G.eng); }
static void view_changed(const CoopView *v) { (void)v; assert(0 && "fixture roster is already installed"); }
static void make_snapshot(void) { assert(!coop_profile_active()); }
static void reset_pools(void) { assert(!coop_profile_active()); coop_sync_reset_pools(G.eng); }
static void remove_by_fingerprint(const char *key) { (void)key; assert(0 && "no removal requested"); }
static void say_stats(uint64_t now) { (void)now; }
int coop_send_to(int member, uint8_t kind, const void *bytes, size_t n) {
    Host *sender = selected;
    int from = G.view.self;
    if (kind == COOP_K_SECTION) G.sections++;
    if (deliver && (!member || member == 3 - from)) {
        selected = &hosts[2 - from];
        on_message(from, bytes, n, (void *)(uintptr_t)kind);
        selected = sender;
    }
    return 1;
}
int coop_send(uint8_t kind, const void *bytes, size_t n) { return coop_send_to(0, kind, bytes, n); }
#include COPY_SAVE_HOST

/* Include the complete adapter, keeping its independent locks and real storage
   callbacks. There is no game, so the fixture supplies the local export barrier. */
#define publish runtime_publish
#include "coop_save_runtime.c"
#undef publish
static CoopSaveSource captured;
int hos_save_export(unsigned slot, CoopSaveSource *out) {
    char root[COOP_PROFILE_PATH];
    assert(slot == 5); /* exactly the locally approved slot, not a newest-slot fallback */
    assert(snprintf(root, sizeof root, "%s/save/profile", coop_profile_root()) < (int)sizeof root);
    G.exports++;
    int ok = coop_profile_export(root, slot, G.exports, out);
    if (ok) captured = *out;
    return ok;
}
typedef struct {
    int from, to; uint8_t kind; size_t n;
    uint8_t bytes[COOP_SAVE_CHUNK_HEAD + COOP_SAVE_CHUNK];
} Packet;
static Packet packets[64];
static unsigned packet_count;
static uint8_t signed_offer[COOP_SAVE_OFFER_BYTES];
int coop_send_save_event(int member, uint8_t kind, const uint8_t id[16], uint64_t deadline, const void *p, size_t n) {
    (void)id; (void)deadline;
    assert(packet_count < 64 && n <= sizeof packets[0].bytes);
    Packet *q = &packets[packet_count++];
    q->from = G.view.self; q->to = member; q->kind = kind; q->n = n; memcpy(q->bytes, p, n);
    if (kind == COOP_K_SAVE_OFFER) { assert(n == sizeof signed_offer); memcpy(signed_offer, p, n); }
    return 1;
}
void coop_cancel_save_events(const uint8_t id[16]) { (void)id; }
int coop_on_save_event(uint8_t kind, CoopEventFn fn, void *u) { (void)kind; (void)fn; (void)u; assert(0); return 0; }
static void choose(int side) { selected = &hosts[side]; exchange = G.save; }
static void room_ticks(void) {
    for (int step = 0; step < 8; step++) {
        clock_ms += 500;
        for (int side = 0; side < 2; side++) { choose(side); on_tick(clock_ms, NULL); }
    }
}
static CoopSaveView status(CoopOffer *o) {
    CoopSaveView v;
    assert(hos_coop_save_exchange("status", 0, 0, 0, NULL, &v, o));
    return v;
}
static int action(const char *name, int member, uint32_t token, const uint8_t *hash) {
    CoopSaveView v; CoopOffer o;
    return hos_coop_save_exchange(name, member, 5, token, hash, &v, &o);
}
static void closed(void) {
    CoopRules rules; uint32_t members = 99;
    publish_members(); gameplay_context_locked();
    assert(!hos_oot_coop_presentation(&rules, &members) && !members && !rules.epoch);
    assert(!G.presented && !G.accepted && !G.pub_shown && !G.chat && !G.hits && !G.presence);
    assert(!G.gameplay.context.capabilities && !coop_sync_open(G.eng) && !coop_sync_copy_wanted(G.eng));
    assert(!G.eng->nregs && !G.eng->npools);
    CoopApply value; assert(!coop_sync_take(G.eng, &value, 1));
}
static void setup(void) {
    int ids[] = {1, 2}; uint64_t sessions[] = {101, 202};
    clock_ms = coop_now_ms();
    CoopRules rules; coop_rules_default(&rules);
    assert(coop_feature_set(&rules, COOP_RULE_PRESENCE, 1));
    assert(coop_feature_set(&rules, 101, 1));
    assert(coop_feature_set(&rules, 30, 1));
    for (int side = 0; side < 2; side++) {
        choose(side);
        G.eng = coop_sync_new((uint64_t)side + 1, 77); assert(G.eng);
        uint8_t seed[32] = {0}; seed[0] = (uint8_t)(side + 1); coop_sync_seed(G.eng, seed);
        coop_sync_name(G.eng, side ? "Owner" : "Maker");
        coop_sync_temporary(G.eng, coop_profile_active());
        G.view.state = COOP_JOINED; G.view.version = G.view_seen = 1; G.view_any = 1;
        G.view.self = side + 1; G.view.session = sessions[side]; G.view.n = 2;
        for (int i = 0; i < 2; i++) { G.view.member[i].id = ids[i]; G.view.member[i].session = sessions[i]; }
        G.on = G.gate = G.loaded = 1; G.bound = !coop_profile_active();
        G.save_generation = G.area_generation = 1;
        CoopOffer o;
        status(&o); assert(!o.token && !o.rules.epoch);
        assert(!action("identity", 0, 1, NULL));
        coop_sync_room(G.eng, "H7K3WMBCDFGH234567", side + 1, ids, sessions, 2, 1, clock_ms);
        coop_sync_settings(G.eng, &rules, side == 0);
        /* No receive worker thread in this fixture: same adapter ops and per-side
           exchange, with explicit ticks/delivery below. Status/consent are real. */
        G.save = coop_save_new(&ops, NULL); assert(G.save);
    }
    running = 1;
}
static void consent(void) {
    for (int side = 0; side < 2; side++) {
        choose(side);
        CoopOffer o; status(&o);
        int accepted = action("identity", 0, o.token, NULL);
        printf("OBSERVED domain=%s member=%d token=%08x epoch=%u maker=%u peers=%d unidentified=%d identity=%d\n",
               coop_profile_active() ? "copy" : "normal", G.view.self, o.token, o.rules.epoch, o.rules.maker, o.n, o.unidentified, accepted);
        fflush(stdout);
        assert(o.token && o.rules.epoch && o.rules.maker == 1 && o.maker_key[0]);
        assert(o.n == 1 && o.member[0].id == 2 - side && o.member[0].heard && o.member[0].key[0]);
        assert(!o.accepted && !o.presence_accepted && accepted);
        assert(G.downloads == (1u << (2 - side)));
        CoopSaveBinding b; assert(hos_oot_coop_save_binding(2 - side, &b));
        assert(b.epoch == o.rules.epoch && b.own_session == G.view.session && b.peer_session == hosts[1 - side].view.session);
        assert(!memcmp(b.own, G.eng->pk, 32) && !memcmp(b.peer, hosts[1 - side].eng->pk, 32));
        closed();
    }
}
static void step_transfer(void) {
    for (int side = 0; side < 2; side++) { choose(side); coop_save_tick(exchange, coop_now_ms(), utc_ms()); }
    unsigned n = packet_count;
    for (unsigned i = 0; i < n; i++) {
        Packet *p = &packets[i]; choose(p->to - 1);
        coop_save_receive(exchange, p->from, hosts[p->from - 1].view.session, p->kind, p->bytes, p->n, coop_now_ms(), utc_ms());
    }
    assert(packet_count == n); packet_count = 0;
}
static void reject_altered_offer(unsigned offset, int resign) {
    uint8_t altered[COOP_SAVE_OFFER_BYTES]; memcpy(altered, signed_offer, sizeof altered); altered[offset] ^= 1;
    if (resign) coop_sign(altered + 288, hosts[1].eng->sk, altered, 288);
    choose(0);
    CoopSave *probe = coop_save_new(&ops, NULL); assert(probe);
    coop_save_receive(probe, 2, 202, COOP_K_SAVE_OFFER, altered, sizeof altered, coop_now_ms(), utc_ms());
    CoopSaveView v; coop_save_view(probe, &v); assert(v.incoming == COOP_SAVE_IDLE && !v.received_bytes);
    coop_save_free(probe);
}
int main(int argc, char **argv) {
    assert(argc >= 3);
    assert(coop_profile_init(argv[1], argc == 4 ? argv[3] : NULL));
    if (!strcmp(argv[2], "prepare")) { assert(coop_profile_prepare()); puts(coop_profile_restart_token()); return 0; }
    assert(coop_profile_active() == !strcmp(argv[2], "copy"));
    char mount[COOP_PROFILE_PATH]; snprintf(mount, sizeof mount, "%s", coop_profile_root());
    setup();
    /* A maker with a silent/unidentified member cannot grant download consent. */
    deliver = 0; room_ticks();
    choose(0); CoopOffer before; status(&before);
    assert(!action("identity", 0, before.token, NULL));
    assert(!G.downloads);
    /* Establish the reported precondition independently of host publication:
       both identities and the signed charter really reached the shared engine.
       The old early-return mutation must fail even with this state available. */
    deliver = 1;
    for (int step = 0; step < 8; step++) {
        clock_ms += 500;
        for (int side = 0; side < 2; side++) {
            choose(side); coop_sync_tick(G.eng, clock_ms, send_fn, NULL);
        }
    }
    for (int side = 0; side < 2; side++) {
        choose(side); CoopOffer ready;
        assert(coop_sync_offer(G.eng, &ready, clock_ms));
        assert(ready.token && ready.rules.epoch && !ready.unidentified &&
               ready.n == 1 && ready.member[0].heard && ready.rules.maker == 1);
        printf("ENGINE domain=%s member=%d identified=2 epoch=%u maker=%u\n",
               coop_profile_active() ? "copy" : "normal", G.view.self, ready.rules.epoch, ready.rules.maker);
        fflush(stdout);
    }
    room_ticks(); consent();
    if (coop_profile_active()) {
        for (int side = 0; side < 2; side++) {
            choose(side);
            assert(!coop_sync_accept(G.eng, G.pub.token) && !coop_sync_reset_pools(G.eng));
            coop_sync_local(G.eng, COOP_CAT_PROGRESS, 123, 9, clock_ms);
            coop_sync_pool_local(G.eng, COOP_CAT_KEYS, 456, 4, 10, clock_ms);
        }
        room_ticks();
        for (int side = 0; side < 2; side++) { choose(side); closed(); assert(!G.sections); }
    }
    /* Only the non-maker's local offer action captures a slot. Identity/status
       alone captured nothing and authorized no packet bytes or native effect. */
    assert(!hosts[0].exports && !hosts[1].exports && !packet_count);
    choose(1); assert(action("offer", 1, 0, NULL)); assert(G.exports == 1);
    CoopSaveSource approved = captured;
    step_transfer();
    reject_altered_offer(140, 0); /* generation, still carrying the owner's old signature */
    reject_altered_offer(164, 0); /* approved file digest */
    reject_altered_offer(72, 1);  /* a valid owner signature cannot choose another recipient */
    reject_altered_offer(104, 1); /* or another owner session */
    reject_altered_offer(136, 1); /* or another charter epoch */
    choose(0); CoopOffer preview; CoopSaveView v = status(&preview);
    assert(v.incoming == COOP_SAVE_OFFERED && !v.received_bytes && !v.verified_id[0]);
    assert(!memcmp(signed_offer + 164, approved.hash, 64) && !memcmp(signed_offer + 228, approved.package, 32));
    assert(coop_sign_check(signed_offer + 288, hosts[1].eng->pk, signed_offer, 288));
    uint8_t old_manifest[32], wrong[32]; memcpy(old_manifest, v.manifest_hash, 32); memcpy(wrong, old_manifest, 32); wrong[0] ^= 1;
    assert(!action("accept", 0, 0, wrong));
    assert(action("accept", 0, 0, old_manifest));
    for (int step = 0; step < 100; step++) {
        step_transfer(); choose(0); v = status(&preview);
        if (v.incoming == COOP_SAVE_VERIFIED) break;
    }
    assert(v.incoming == COOP_SAVE_VERIFIED && v.received_bytes == approved.size[0] + approved.size[1]);
    step_transfer(); choose(1); v = status(&preview); assert(v.outgoing == COOP_SAVE_VERIFIED);
    assert(G.exports == 1); /* every chunk comes from the same approved immutable capture */
    puts("PASS: non-maker approval, signed exact snapshot/recipient/session/epoch, exact manifest acceptance, verified bytes");
    /* Changed terms invalidate both the old confirmation and the live binding,
       before another export or acceptance can start. */
    uint32_t tokens[2];
    for (int side = 0; side < 2; side++) { choose(side); status(&preview); tokens[side] = preview.token; }
    CoopRules changed = hosts[0].eng->mine;
    assert(coop_feature_set(&changed, 101, 0)); coop_sync_settings(hosts[0].eng, &changed, 1); room_ticks();
    for (int side = 0; side < 2; side++) {
        choose(side); assert(!action("identity", 0, tokens[side], NULL));
        assert(!action("offer", 2 - side, 0, NULL));
        assert(!action("accept", 0, 0, old_manifest));
        CoopSaveBinding b; assert(!hos_oot_coop_save_binding(2 - side, &b)); closed(); assert(!G.downloads);
    }
    assert(!hosts[0].exports && hosts[1].exports == 1);
    consent(); choose(1); assert(action("offer", 1, 0, NULL)); step_transfer();
    choose(0); v = status(&preview); assert(v.incoming == COOP_SAVE_OFFERED && !v.received_bytes);
    assert(memcmp(v.manifest_hash, old_manifest, 32) && !action("accept", 0, 0, old_manifest));
    assert(action("cancel", 0, 0, NULL));
    assert(!strcmp(mount, coop_profile_root()) &&
           !strcmp(coop_profile_restart_token(), coop_profile_active() ? "normal" : ""));
    for (int side = 0; side < 2; side++) { choose(side); closed(); coop_save_free(G.save); coop_sync_free(G.eng); }
    puts("PASS: pre-offer/unidentified/stale terms refuse; download grants no presentation/progress; no mount/restart change");
    return 0;
}
