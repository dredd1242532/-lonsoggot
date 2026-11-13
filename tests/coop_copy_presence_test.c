// Authored identities and poses through the production engine and host owner; no game data.
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "coop_state.c"
#include "coop.h"
#include "coop_gameplay.h"
#include "coop_players.h"

static int temporary, native_writes, hit_share;
static uint32_t accepted_mask, presence_mask, download_mask;
static uint64_t now = 1000;
static CoopView live;
static struct {
    int lock, pub_lock, on, bound, gate, loaded, presence, pub_has;
    CoopSync *eng;
    CoopView view;
    CoopOffer pub;
    CoopGameplay gameplay;
    uint64_t save_generation, area_generation;
    uint32_t pub_shown, want_accept;
} G;
static struct {
    int lock, trace;
    CoopPlayers store;
    CoopGameplayTicket ticket[COOP_MAX_MEMBERS + 1];
    struct { int any; uint8_t flags; char area[COOP_POS_TEXT + 1]; } heard[COOP_MAX_MEMBERS + 1];
} P;
int coop_profile_active(void) { return temporary; }
static uint64_t coop_now_ms(void) { return now; }
static void AcquireSRWLockExclusive(int *lock) { (void)lock; }
static void AcquireSRWLockShared(int *lock) { (void)lock; }
static int TryAcquireSRWLockExclusive(int *lock) { (void)lock; return 1; }
static void ReleaseSRWLockExclusive(int *lock) { (void)lock; }
static void ReleaseSRWLockShared(int *lock) { (void)lock; }
void coop_view_get(CoopView *out) { *out = live; }
void coop_accept_members(const CoopView *v, uint32_t mask) { accepted_mask = v ? mask : 0; }
void coop_presence_members(const CoopView *v, uint32_t mask) { presence_mask = v ? mask : 0; }
void coop_download_members(const CoopView *v, uint32_t mask) { download_mask = v ? mask : 0; }
static void hos_oot_hits_room(const CoopRules *rules, int accepted) { hit_share = rules && accepted; }
static void hos_log(const char *fmt, ...) { (void)fmt; }
static void say_heard(int from, const CoopPos *pose) { (void)from; (void)pose; }
static void publish(const CoopOffer *offer, int has, uint64_t at);
#include COPY_PRESENCE_HOST
static void publish(const CoopOffer *offer, int has, uint64_t at) {
    (void)at;
    G.pub = *offer; G.pub_has = has;
    publish_members();
}

static CoopSync *pair[2];
static unsigned sections[2];
static int route(int to, uint8_t kind, const uint8_t *bytes, size_t n, void *user) {
    int from = *(int *)user;
    if (kind == COOP_K_SECTION) sections[from - 1]++;
    if (!to || to == 3 - from) coop_sync_receive(pair[2 - from], from, kind, bytes, n, now);
    return 1;
}
static void exchange(void) {
    for (int step = 0; step < 8; step++) {
        now += 500;
        for (int from = 1; from <= 2; from++) coop_sync_tick(pair[from - 1], now, route, &from);
    }
}
static uint32_t limit(int cat, uint32_t hash, void *user) { (void)cat; (void)hash; (void)user; return 999; }
static CoopOffer offer(void) {
    CoopOffer o;
    assert(coop_sync_offer(G.eng, &o, now));
    return o;
}
static int writer(void *game, const void *payload) { (void)game; (void)payload; native_writes++; return 1; }
static void setup(void) {
    int ids[] = {1, 2}; uint64_t sessions[] = {101, 202};
    CoopRules rules; coop_rules_default(&rules);
    assert(coop_feature_set(&rules, COOP_RULE_PRESENCE, 1));
    assert(coop_feature_set(&rules, 30, 1));
    for (int i = 0; i < 2; i++) {
        pair[i] = coop_sync_new((uint64_t)i + 1, 77); assert(pair[i]);
        uint8_t seed[32] = {0}; seed[0] = (uint8_t)(i + 1);
        coop_sync_seed(pair[i], seed);
        coop_sync_name(pair[i], i ? "Joiner" : "Maker");
        coop_sync_limits(pair[i], limit, NULL);
        coop_sync_room(pair[i], "H7K3WMBCDFGH234567", i + 1, ids, sessions, 2, 1, now);
        coop_sync_settings(pair[i], &rules, i == 0);
        coop_sync_rebind(pair[i]);
    }
    exchange();
    live.state = COOP_JOINED; live.version = 1; live.self = 2; live.session = 202; live.n = 2;
    live.member[0].id = 1; live.member[0].session = 101;
    live.member[1].id = 2; live.member[1].session = 202;
    G.view = live; G.eng = pair[1]; G.on = G.bound = G.gate = G.loaded = 1;
    G.save_generation = G.area_generation = 1;
    for (unsigned slot = 0; slot < COOP_GAMEPLAY_WRITERS; slot++) {
        unsigned cap = slot == COOP_GAMEPLAY_BODIES ? COOP_GAMEPLAY_PRESENTATION :
                       slot == COOP_GAMEPLAY_WORLD ? COOP_GAMEPLAY_TEMPORARY : COOP_GAMEPLAY_DURABLE;
        CoopGameplayWriter w = {COOP_GAMEPLAY_VERSION, sizeof w, slot, cap, sizeof(int), writer};
        assert(coop_gameplay_register(&G.gameplay, &w));
    }
    coop_players_roster(&P.store, 2, ids, sessions, 2);
}
static void closed(void) {
    CoopRules rules; uint32_t members = 123;
    publish_members(); gameplay_context_locked();
    assert(!hos_oot_coop_presentation(&rules, &members) && !members && !rules.epoch);
    assert(!presence_mask && !accepted_mask && !G.presence && !G.gameplay.context.capabilities && !hit_share);
}
static void no_durable(void) {
    assert(!coop_sync_open(G.eng) && !coop_sync_member_accepted(G.eng, 1, 101));
    assert(!G.eng->accepted && !G.eng->nacc && !coop_sync_copy_wanted(G.eng));
    assert(!(G.gameplay.context.capabilities & (COOP_GAMEPLAY_DURABLE | COOP_GAMEPLAY_TEMPORARY)));
    CoopApply apply;
    assert(!coop_sync_take(G.eng, &apply, 1));
    for (unsigned slot = 0; slot < COOP_GAMEPLAY_WRITERS; slot++) {
        if (slot == COOP_GAMEPLAY_BODIES) continue;
        CoopGameplayTicket ticket;
        assert(!hos_oot_coop_gameplay_ticket(slot, 0, &ticket));
    }
    assert(!native_writes);
}
static void grant_presence(void) {
    CoopOffer o = offer();
    assert(hos_oot_coop_presence_accept(o.token));
    gameplay_context_locked();
    CoopRules rules; uint32_t members = 0;
    assert(hos_oot_coop_presentation(&rules, &members) && rules.epoch == o.rules.epoch && members == (1u << 1));
    assert(G.presence && G.gameplay.context.capabilities == COOP_GAMEPLAY_PRESENTATION);
    assert(!accepted_mask && presence_mask == members && G.pub_shown == members && hit_share);
    no_durable();
}
static void pose_arrival(int expected) {
    CoopPos pose = {0}; pose.pseq = P.store.taken + 1; pose.flags = COOP_POS_IN_WORLD | COOP_POS_LOADED;
    snprintf(pose.area, sizeof pose.area, "authored-map"); pose.pos[0] = 10.0f;
    uint8_t bytes[COOP_MAX_MESSAGE]; size_t n = coop_pos_pack(bytes, sizeof bytes, &pose); assert(n);
    unsigned before = P.store.taken;
    on_pos(1, bytes, n, NULL);
    assert(P.store.taken == before + (unsigned)expected);
}
int main(void) {
    setup();
    CoopOffer normal = offer();
    assert(!hos_oot_coop_presence_accept(normal.token));
    assert(coop_sync_accept(G.eng, normal.token)); coop_sync_copied(G.eng, 1);
    assert(coop_sync_open(G.eng));
    gameplay_context_locked();
    assert(G.gameplay.context.capabilities == (COOP_GAMEPLAY_PRESENTATION | COOP_GAMEPLAY_DURABLE));
    CoopGameplayTicket old;
    assert(hos_oot_coop_gameplay_ticket(COOP_GAMEPLAY_PROGRESS, 0, &old));
    coop_sync_local(G.eng, COOP_CAT_ITEMS, 123, 8, now);
    temporary = 1;
    closed(); no_durable();
    assert(!G.eng->nregs && !G.eng->npools);
    assert(!hos_oot_coop_presence_accept(normal.token));
    int payload = 1;
    assert(!hos_oot_coop_gameplay_dispatch(&old, &payload, sizeof payload, NULL));
    pose_arrival(0);
    CoopOffer copy = offer();
    G.pub = copy; G.pub_has = 1;
    assert(!hos_oot_coop_accept(copy.token) && !G.want_accept && !coop_sync_accept(G.eng, copy.token));
    assert(coop_sync_download_accept(G.eng, copy.token));
    closed(); assert(download_mask == (1u << 1)); no_durable(); pose_arrival(0);
    // Local flags/pools and received signed sections cannot enter or leave a copy's engine.
    coop_sync_local(G.eng, COOP_CAT_PROGRESS, 123, 9, now);
    coop_sync_pool_local(G.eng, COOP_CAT_KEYS, 456, 4, 10, now);
    coop_sync_local(pair[0], COOP_CAT_PROGRESS, 123, 10, now);
    sections[1] = 0; exchange();
    assert(!sections[1] && !G.eng->nregs && !G.eng->npools);
    closed();
    grant_presence(); pose_arrival(1);
    assert(hos_oot_coop_gameplay_ticket(COOP_GAMEPLAY_BODIES, 1, &old));
    coop_sync_presence_revoke(G.eng);
    closed(); pose_arrival(0);
    assert(!hos_oot_coop_gameplay_dispatch(&old, &payload, sizeof payload, NULL));
    assert(!hos_oot_coop_presence_accept(copy.token));
    grant_presence();
    // A maker rule change closes immediately on receipt; reaccepting never revives old tickets.
    CoopRules changed = pair[0]->mine;
    assert(coop_feature_set(&changed, COOP_RULE_PRESENCE, 0));
    coop_sync_settings(pair[0], &changed, 1); exchange(); closed();
    assert(!hos_oot_coop_presence_accept(copy.token));
    assert(coop_feature_set(&changed, COOP_RULE_PRESENCE, 1));
    coop_sync_settings(pair[0], &changed, 1); exchange(); closed(); grant_presence();
    // Both arrival and departure change the membership token, even if nobody accepted the newcomer.
    uint32_t before_member = offer().token;
    int ids[] = {1, 2, 3}; uint64_t sessions[] = {101, 202, 303};
    coop_sync_room(G.eng, G.eng->code, 2, ids, sessions, 3, 1, now);
    closed(); assert(!hos_oot_coop_presence_accept(before_member));
    coop_sync_room(G.eng, G.eng->code, 2, ids, sessions, 2, 1, now);
    exchange(); closed(); assert(!hos_oot_coop_presence_accept(before_member)); grant_presence();
    // Leave and rejoin the same room do not restore copy presence.
    coop_sync_room(G.eng, "", 0, NULL, NULL, 0, 0, now);
    live.state = COOP_LEFT; G.view = live;
    closed();
    coop_sync_room(G.eng, "H7K3WMBCDFGH234567", 2, ids, sessions, 2, 1, now);
    live.state = COOP_JOINED; G.view = live; exchange(); closed(); grant_presence();
    // Return and a later new mount grant nothing, not even an earlier normal-save acceptance.
    temporary = 0;
    closed(); assert(!coop_sync_open(G.eng) && !coop_sync_download_members(G.eng));
    assert(!G.eng->accepted && !G.eng->nacc && !G.eng->presence_token);
    assert(!hos_oot_coop_presence_accept(offer().token));
    temporary = 1; closed(); no_durable();
    coop_sync_free(pair[0]); coop_sync_free(pair[1]);
    puts("PASS: copy presence, independent download/normal consent, no durable APIs/traffic, return/revoke/leave/rules/members, stale tickets, position arrival");
}
