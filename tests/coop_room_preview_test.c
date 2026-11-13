// Authored room terms through the real signed codec and acceptance engine; no game data or network.
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "coop_state.c"

static uint8_t packet[COOP_MAX_MESSAGE];
static size_t packet_size;
static int capture(int to, uint8_t kind, const uint8_t *b, size_t n, void *user) {
    (void)to; (void)user;
    if (kind == COOP_K_DIGEST) { assert(n <= sizeof packet); memcpy(packet, b, n); packet_size = n; }
    return 1;
}
static CoopSync *make(int id) {
    CoopSync *s = coop_sync_new((uint64_t)id, 123);
    uint8_t seed[32] = {0}; seed[0] = (uint8_t)id;
    coop_sync_seed(s, seed);
    coop_sync_name(s, id == 1 ? "Maker" : "Joiner");
    const int ids[] = {1, 2}; const uint64_t sessions[] = {101, 202};
    coop_sync_room(s, "K7XG2MRTB9DHW4PZ5N", id, ids, sessions, 2, 1, 1000);
    coop_sync_rebind(s);
    return s;
}
static void deliver(CoopSync *a, CoopSync *b, int format) {
    packet_size = 0;
    send_digest(a, b->self, capture, NULL);
    assert(packet_size);
    packet[0] = (uint8_t)format;
    coop_sync_receive(b, a->self, COOP_K_DIGEST, packet, packet_size, 2000);
}
static void charter(CoopSync *a, int mode, int absent) {
    CoopRules r; coop_rules_default(&r);
    assert(coop_feature_set(&r, COOP_RULE_ROOM_MODE, mode > 1 ? 1 : mode));
    coop_sync_settings(a, &r, 1); originate(a);
    // An incompatible maker can sign any bytes. The receiver still has to refuse them.
    if (absent) a->ch.features.count = 0;
    else for (unsigned i = 0; i < a->ch.features.count; i++)
        if (a->ch.features.bytes[3 * i] == COOP_RULE_ROOM_MODE) a->ch.features.bytes[3 * i + 1] = (uint8_t)mode;
    uint8_t m[sizeof SIG_RULES - 1 + CTX_LEN + CHARTER_FIELDS_MAX];
    coop_sign(a->ch.sig, a->sk, m, charter_message(a, &a->ch, m));
}
static void refused(int mode, int absent, int format, const char *reason) {
    CoopSync *a = make(1), *b = make(2);
    charter(a, mode, absent); deliver(a, b, format);
    CoopOffer offer;
    assert(!coop_sync_offer(b, &offer, 8000));
    assert(strstr(offer.refusal, reason));
    assert(!coop_sync_accept(b, offer.token) && !coop_sync_open(b));
    assert(!coop_sync_download_accept(b, offer.token) && !coop_sync_download_members(b));
    coop_sync_free(a); coop_sync_free(b);
}
int main(void) {
    CoopRules permissions; coop_rules_default(&permissions);
    assert(COOP_PRESENCE_SUPPORTED == 95);
    assert(coop_feature_set(&permissions, COOP_RULE_PRESENCE, 2)); // adult bodies
    assert(!coop_feature_set(&permissions, COOP_RULE_PRESENCE, 32));
    assert(!coop_feature_set(&permissions, COOP_RULE_PRESENCE, 128));
    for (int mask = 0; mask <= 255; mask++) {
        const int supported = !(mask & ~COOP_PRESENCE_SUPPORTED);
        assert(coop_feature_set(&permissions, COOP_RULE_PRESENCE, mask) == supported);
    }
    assert(!coop_feature_set(&permissions, COOP_RULE_PRESENCE, -1));
    assert(coop_feature_set(&permissions, COOP_RULE_PRESENCE, COOP_PRESENCE_SUPPORTED));
    int mask = 0;
    assert(coop_feature_present(&permissions, COOP_RULE_PRESENCE, &mask) && mask == 95);
    refused(0, 1, 5, "Missing signed room mode");
    refused(-1, 0, 5, "mode is off");
    refused(7, 0, 5, "unknown mode");
    refused(0, 0, 4, "Format-4 peer");
    CoopSync *a = make(1), *b = make(2);
    CoopRules mine; coop_rules_default(&mine);
    mine.mode[COOP_CAT_PROGRESS] = COOP_MODE_OWN;
    coop_sync_settings(b, &mine, 0);
    const CoopRules saved = b->mine;
    charter(a, COOP_ROOM_COOP, 0); deliver(a, b, 5); deliver(b, a, 5);
    CoopOffer first, next;
    assert(coop_sync_offer(b, &first, 8000));
    assert(first.rules.mode[COOP_CAT_PROGRESS] == COOP_MODE_SHARED && first.maker_key[0]);
    assert(!memcmp(&b->mine, &saved, sizeof saved));
    assert(coop_sync_download_accept(b, first.token));
    assert(coop_sync_download_members(b) == (1u << 1) && !coop_sync_open(b) && !b->accepted);
    assert(coop_sync_accept(b, first.token)); coop_sync_copied(b, 1);
    assert(coop_sync_open(b));
    CoopRules changed = a->mine;
    assert(coop_feature_set(&changed, 249, 17));
    coop_sync_settings(a, &changed, 1); originate(a); deliver(a, b, 5);
    assert(coop_sync_offer(b, &next, 8001) && !next.accepted && next.token != first.token);
    assert(!coop_sync_accept(b, first.token));
    assert(!coop_sync_download_members(b) && !coop_sync_download_accept(b, first.token));
    int value = 0;
    assert(coop_feature_present(&next.rules, 249, &value) && value == 17);
    assert(!memcmp(&b->mine, &saved, sizeof saved));
    assert(coop_feature_set(&changed, COOP_RULE_ROOM_MODE, COOP_ROOM_PVP_SETUP));
    coop_sync_settings(a, &changed, 1); originate(a); deliver(a, b, 5);
    assert(coop_sync_offer(b, &next, 8002) && coop_sync_accept(b, next.token));
    coop_sync_copied(b, 1);
    assert(!coop_sync_open(b));   // a PvP setup room never becomes ordinary shared-progress gameplay
    assert(coop_sync_download_accept(b, next.token) && coop_sync_download_members(b) == (1u << 1));
    CoopApply apply;
    assert(!coop_sync_take(b, &apply, 1));
    coop_sync_free(a); coop_sync_free(b);
    puts("PASS: signed co-op/PvP preview, stale acceptance, unchanged local defaults; missing/off/unknown mode and format4 refused; PvP gameplay closed");
    return 0;
}
