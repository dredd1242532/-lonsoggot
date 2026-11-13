/* Notice observations are downstream of the existing signed progress admission, never new packets. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "coop_state.c"
static CoopSync *games[3];
static uint8_t saved[COOP_MAX_MESSAGE];
static size_t saved_n;
static int observations;
static uint32_t wanted;
static uint32_t limit(int cat, uint32_t hash, void *user) { (void)user; return cat == COOP_CAT_ITEMS && hash == 123 ? 1 : 0; }
static void observe(int member, uint64_t session, int cat, uint32_t hash, uint32_t value, void *user) {
    (void)user;
    assert(member == 1 && session == 101 && cat == COOP_CAT_ITEMS && hash == 123 && value == wanted);
    observations++;
}
static int send_to(int to, uint8_t kind, const uint8_t *b, size_t n, void *user) {
    CoopSync *s = user;
    const int target = 3 - s->self;
    if (to && to != target) return 1;
    if (s->self == 1 && kind == COOP_K_SECTION) { memcpy(saved, b, n); saved_n = n; }
    coop_sync_receive(games[target], s->self, kind, b, n, s->now);
    return 1;
}
static CoopSync *make(int id) {
    CoopSync *s = coop_sync_new((uint64_t)id, 123);
    uint8_t seed[32] = {0}; seed[0] = (uint8_t)id;
    coop_sync_seed(s, seed); coop_sync_name(s, id == 1 ? "Alice" : "Bob");
    const int ids[] = {1,2}; const uint64_t sessions[] = {101,202};
    coop_sync_room(s, "K7XG2MRTB9DHW4PZ5N", id, ids, sessions, 2, 1, 1000);
    coop_sync_rebind(s); coop_sync_limits(s, limit, NULL);
    CoopRules r; coop_rules_default(&r);
    coop_sync_settings(s, &r, id == 1);
    return s;
}
int main(void) {
    games[1] = make(1); games[2] = make(2);
    CoopSync *a = games[1], *b = games[2];
    coop_sync_observe_shared(b, observe, NULL);
    for (uint64_t now = 1000; now < 12000; now += 100) {
        coop_sync_tick(a, now, send_to, a); coop_sync_tick(b, now, send_to, b);
    }
    coop_sync_local(a, COOP_CAT_ITEMS, 123, 1, 12000);
    for (uint64_t now = 12000; now < 14000; now += 100) {
        coop_sync_tick(a, now, send_to, a); coop_sync_tick(b, now, send_to, b);
    }
    assert(!observations && !coop_sync_flag(b, COOP_CAT_ITEMS, 123));
    CoopOffer o;
    assert(coop_sync_offer(b, &o, 15000) && coop_sync_accept(b, o.token)); coop_sync_copied(b, 1);
    wanted = 1;
    /* Send every section rather than assuming a hash partition's implementation. */
    for (int i = 0; i < COOP_SYNC_SECTIONS; i++) send_section(a, i, 2, send_to, a);
    assert(observations == 1 && coop_sync_flag(b, COOP_CAT_ITEMS, 123) == 1);
    const int before = observations;
    if (saved_n) {
        coop_sync_receive(b, 1, COOP_K_SECTION, saved, saved_n, 16000);
        saved[saved_n - 1] ^= 1;
        coop_sync_receive(b, 1, COOP_K_SECTION, saved, saved_n, 16001);
    }
    for (int i = 0; i < COOP_SYNC_SECTIONS; i++) send_section(a, i, 2, send_to, a);
    assert(observations == before);
    assert(coop_feature_set(&a->mine, COOP_RULE_SOCIAL, 1)); originate(a);
    send_digest(a, 2, send_to, a);
    assert(coop_sync_offer(b, &o, 17000) && !o.accepted); /* identity consent is not acceptance of changed terms */
    assert(observations == before);
    coop_sync_free(a); coop_sync_free(b);
    puts("PASS chat-progress: signed shared flags only after acceptance, unchanged/duplicate/forged sections do not repeat notices");
    return 0;
}
