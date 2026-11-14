// Real session/host/signed-engine integration. Only native saves, rendering and relay scheduling are stand-ins.
#ifndef _CRT_SECURE_NO_WARNINGS
#define _CRT_SECURE_NO_WARNINGS
#endif
#include "coop_net.h"
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static uint64_t clock_ms = 1000;
#define coop_now_ms() clock_ms
#include "coop.c"
#include "coop_state.h"
#include "coop_gameplay.h"
#include "coop_players.h"
#include "oot_coop_kit.h"

static int selected;
static CoopSess *sessions[2];
static CoopUdp *relay;
static unsigned digest_received[2], view_callbacks[2];
static volatile int hos_oot_coop_kit_active;
typedef struct { int unused; } GFlag;
#define SRWLOCK CoopLock
#include "host-state.h"
#undef SRWLOCK
#define TRACE_ON (G.trace)

void hos_log(const char *format, ...) {
    (void)format;
}
int hos_ini_option(const char *key, int otherwise) { (void)key; return otherwise; }
int hos_ini_text(const char *key, char *out, size_t cap) { (void)key; (void)out; (void)cap; return 0; }
int coop_profile_active(void) { return 0; }
static uint32_t limit_fn(int category, uint32_t hash, void *user) {
    (void)category; (void)hash; (void)user; return 999;
}
static void trace_line(void *user, const char *line) { (void)user; (void)line; }
static void hos_coop_chat_shared(int member, uint64_t serial, int category, uint32_t hash, uint32_t value, void *user) {
    (void)member; (void)serial; (void)category; (void)hash; (void)value; (void)user;
}
static void hos_coop_chat_room(const CoopView *view, const CoopOffer *offer, uint32_t members) {
    (void)view; (void)offer; (void)members;
}
static void hos_coop_chat_scene(int safe) { (void)safe; }
static void hos_oot_hits_room(const CoopRules *rules, int active) { (void)rules; (void)active; }
static void read_rules(CoopRules *rules, int say) {
    (void)say; coop_rules_default(rules);
    assert(coop_feature_set(rules, COOP_RULE_PRESENCE, COOP_PRESENCE_CHILD_BODY));
}
static int room_key(const char *code, int make, uint8_t key[32]) {
    assert(code[0]); memset(key, 0x31 + selected, 32); return make;
}
static void make_snapshot(void) { /* The fixture acknowledges a requested backup explicitly, after acceptance. */ }
static int accept_always(void) { return 0; }
static void reset_pools(void) { assert(0 && "no pool reset requested"); }
static void remove_by_fingerprint(const char *key) { (void)key; assert(0 && "no removal requested"); }
static void say_stats(uint64_t now) { (void)now; }
static uint64_t fixture_client_id(void) { return sessions[selected]->client_id; }
static void fixture_view_get(CoopView *view) { coop_sess_view(sessions[selected], view); }
static void fixture_accept_members(const CoopView *view, uint32_t members) {
    coop_sess_accept_members(sessions[selected], view, members);
}
static void fixture_presence_members(const CoopView *view, uint32_t members) {
    coop_sess_presence_members(sessions[selected], view, members);
}
static void fixture_download_members(const CoopView *view, uint32_t members) {
    coop_sess_download_members(sessions[selected], view, members);
}
static int fixture_send_to(int member, uint8_t kind, const void *bytes, size_t size) {
    return coop_sess_send(sessions[selected], member, kind, bytes, size);
}
static int fixture_send(uint8_t kind, const void *bytes, size_t size) { return fixture_send_to(0, kind, bytes, size); }

#define AcquireSRWLockExclusive coop_lock
#define ReleaseSRWLockExclusive coop_unlock
#define AcquireSRWLockShared coop_lock
#define ReleaseSRWLockShared coop_unlock
#define coop_client_id fixture_client_id
#define coop_view_get fixture_view_get
#define coop_accept_members fixture_accept_members
#define coop_presence_members fixture_presence_members
#define coop_download_members fixture_download_members
#define coop_send fixture_send
#define coop_send_to fixture_send_to
#define publish host_publish
#define on_view host_on_view
#define on_message host_on_message
#define on_tick host_on_tick
#include "host-functions.h"
#undef publish
#undef on_view
#undef on_message
#undef on_tick
#undef coop_client_id
#undef coop_view_get
#undef coop_accept_members
#undef coop_presence_members
#undef coop_download_members
#undef coop_send
#undef coop_send_to

static void receive_view(const CoopView *view, void *user) {
    const int saved = selected;
    selected = (int)(uintptr_t)user; view_callbacks[selected]++;
    host_on_view(view, NULL); selected = saved;
}
static void receive_digest(int from, const uint8_t *bytes, size_t size, void *user) {
    const int saved = selected;
    selected = (int)(uintptr_t)user; digest_received[selected]++;
    host_on_message(from, bytes, size, (void *)(uintptr_t)COOP_K_DIGEST); selected = saved;
}
static void hooks(int side) {
    assert(coop_sess_on_view(sessions[side], receive_view, (void *)(uintptr_t)side));
    assert(coop_sess_on_message(sessions[side], COOP_K_DIGEST, receive_digest, (void *)(uintptr_t)side));
}
static void boot_engine(int side) {
    selected = side;
    assert(!G.eng && ensure_engine());
    coop_sync_schema(G.eng, 77); coop_sync_rebind(G.eng);
    G.bound = G.gate = G.loaded = 1; G.save_generation = G.area_generation = 1;
}
static void relay_packet(int side, uint8_t type, const void *payload, size_t size, uint32_t sequence) {
    selected = side;
    CoopSess *s = sessions[side];
    const CoopHdr header = {.ver = COOP_PROTO, .type = type, .room = 17, .dst = (uint8_t)(side + 1), .seq = sequence};
    const size_t n = coop_dgram_pack(s->rx, sizeof s->rx, &header, payload, size, s->locator);
    assert(n); handle_datagram(s, n, &s->relay, clock_ms); publish(s);
}
static void joined(int side, const CoopRoster *roster) {
    CoopJoined reply = {.member = (uint8_t)(side + 1), .nonce = sessions[side]->nonce, .roster = *roster};
    uint8_t payload[512];
    const size_t n = coop_joined_pack(payload, sizeof payload, &reply);
    assert(n); relay_packet(side, COOP_T_JOINED, payload, n, 1);
    assert(sessions[side]->state == COOP_JOINED && sessions[side]->self == side + 1);
}
static void roster(int side, const CoopRoster *members) {
    uint8_t payload[512];
    const size_t n = coop_roster_pack(payload, sizeof payload, members);
    assert(n); relay_packet(side, COOP_T_ROSTER, payload, n, sessions[side]->roster_seq + 1);
}
static void transport(void) {
    for (int side = 0; side < 2; side++) flush_outbox(sessions[side]);
    uint8_t bytes[COOP_MAX_DGRAM]; CoopAddr from; int size;
    while ((size = coop_udp_recv(relay, bytes, sizeof bytes, &from, 0)) > 0) {
        CoopHdr h; const uint8_t *payload;
        assert(coop_dgram_parse(bytes, (size_t)size, &h, &payload));
        assert(h.type == COOP_T_DATA && h.src >= 1 && h.src <= 2);
        const int side = 2 - h.src;
        assert(h.dst == COOP_DST_ALL || h.dst == side + 1);
        memcpy(sessions[side]->rx, bytes, (size_t)size);
        selected = side;
        handle_datagram(sessions[side], (size_t)size, &sessions[side]->relay, clock_ms);
    }
}
static void tick(void) {
    clock_ms += 100;
    for (selected = 0; selected < 2; selected++) host_on_tick(clock_ms, NULL);
    transport();
}
static void pump(unsigned ticks, const CoopRoster *members) {
    for (unsigned n = 0; n < ticks; n++) {
        if (!(n % 10)) for (int side = 0; side < 2; side++) roster(side, members);
        tick();
    }
}
static CoopOffer preview(int side) {
    selected = side; CoopOffer result = {0};
    hos_oot_coop_offer(&result); return result;
}
static void accept_room(int side) {
    CoopOffer before = preview(side);
    if (!before.token || !before.rules.epoch || !before.maker_key[0] || before.unidentified ||
        before.n != 1 || !before.member[0].heard || !before.member[0].key[0]) {
        fprintf(stderr, "FAIL join: side %d offer %u epoch %u peers %d unidentified %d digests %u view_any %d seen %u live %u\n",
                side, before.token, before.rules.epoch, before.n, before.unidentified, digest_received[side],
                G.view_any, G.view_seen, sessions[side]->view.version);
        exit(1);
    }
    assert(hos_oot_coop_accept(before.token));
    host_on_tick(clock_ms, NULL);
    assert(coop_sync_copy_wanted(G.eng));
    coop_sync_copied(G.eng, 1); /* Synthetic native-backup completion, not fabricated identity or acceptance. */
}
static void scenario(int order) {
    const char *label = order == 0 ? "engine before JOINED" : order == 1 ? "JOINED before engine" : "JOINED before hooks";
    memset(hosts, 0, sizeof hosts); memset(digest_received, 0, sizeof digest_received); memset(view_callbacks, 0, sizeof view_callbacks);
    relay = coop_udp_open(0); assert(relay);
    for (int side = 0; side < 2; side++) {
        const CoopParams params = {.name = side ? "B" : "A", .client_id = (uint64_t)(1001 + side)};
        sessions[side] = coop_sess_new(&params); assert(sessions[side]);
        CoopSess *s = sessions[side];
        s->sock = coop_udp_open(0); assert(s->sock);
        assert(coop_net_resolve("127.0.0.1", coop_udp_port(relay), &s->relay));
        s->relay_known = 1; s->state = COOP_CONNECTING;
        s->session = side ? 202 : 101; s->nonce = 700 + (unsigned)side;
        s->intent = side ? COOP_INTENT_JOIN : COOP_INTENT_CREATE;
        memcpy(s->want, "H7K3WMBCDFGH234567", COOP_SECRET_LEN + 1);
        coop_room_keys(s->want, s->locator, s->msg_key);
        coop_lock_init(&hosts[side].lock); coop_lock_init(&hosts[side].pub_lock);
        hosts[side].trace = 1;
        if (!side || order != 2) hooks(side);
    }
    boot_engine(0);
    CoopRoster members = {.n = 1, .m = {{.id = 1, .flags = COOP_FLAG_MAKER, .session = 101, .name = "A"},
                                       {.id = 2, .session = 202, .name = "B"}}};
    joined(0, &members);
    if (order == 0) boot_engine(1);
    members.n = 2; joined(1, &members); roster(0, &members);
    if (order == 2) hooks(1);
    const unsigned version = sessions[1]->view.version;
    // JOINED has already published both members, but the presenting thread has not created B's engine yet.
    roster(1, &members); tick(); roster(1, &members); tick();
    if (order != 0) boot_engine(1);
    // Identity/charter packets go through the real encrypted sender, receiver and host callbacks.
    pump(60, &members);
    assert(sessions[1]->view.version == version); /* unchanged heartbeats are still not new views */
    accept_room(0); pump(30, &members); accept_room(1); pump(30, &members);
    CoopOffer offers[2] = {preview(0), preview(1)};
    assert(offers[0].accepted && offers[1].accepted && !offers[0].unidentified && !offers[1].unidentified);
    assert(offers[0].rules.epoch == offers[1].rules.epoch && !strcmp(offers[0].maker_key, offers[1].maker_key));
    assert(digest_received[0] && digest_received[1]);
    for (selected = 0; selected < 2; selected++) {
        assert(G.view_any && G.view_seen == sessions[selected]->view.version);
        assert(G.view.n == 2 && coop_sync_open(G.eng));
        gameplay_context_locked(); assert(G.presence == COOP_PRESENCE_CHILD_BODY);
        const uint64_t generation = G.gameplay.generation;
        const unsigned callbacks = view_callbacks[selected];
        roster(selected, &members); host_on_tick(clock_ms, NULL); gameplay_context_locked();
        assert(G.gameplay.generation == generation && view_callbacks[selected] == callbacks && G.presence);
    }
    printf("PASS join: %s; two identified members, matching signed charter, explicit acceptance, heartbeat presence stable\n", label);
    for (int side = 0; side < 2; side++) {
        coop_sync_free(hosts[side].eng); coop_sess_free(sessions[side]);
#ifndef _WIN32
        pthread_mutex_destroy(&hosts[side].lock.m); pthread_mutex_destroy(&hosts[side].pub_lock.m);
#endif
    }
    coop_udp_close(relay);
}
int main(void) {
    for (int order = 0; order < 3; order++) scenario(order);
}
