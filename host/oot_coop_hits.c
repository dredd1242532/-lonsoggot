/* (longshot) Native combat observations. The supported game's life update is already patched for Protect hearts;
   this module sees successful updates and carries bounded, consented observations. It never applies a received hit.
   Game-facing offsets are concentrated below. No enemy list, placement table, asset or lifted code is included. */
#include "coop_net.h"
#include <windows.h>
#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "hos.h"
#include "coop.h"
#include "oot_coop.h"
#include "oot_coop_hits.h"
#include "../gpu/vk/compat/longshot_env.h"

/* ---- wire and application-level replay protection (also compiled by the game-free fixture) */
static void hit_put16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void hit_put32(uint8_t *p, uint32_t v) { for (unsigned i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (8 * i)); }
static void hit_put64(uint8_t *p, uint64_t v) { for (unsigned i = 0; i < 8; i++) p[i] = (uint8_t)(v >> (8 * i)); }
static uint16_t hit_get16(const uint8_t *p) { return (uint16_t)(p[0] | (unsigned)p[1] << 8); }
static uint32_t hit_get32(const uint8_t *p) { uint32_t v = 0; for (unsigned i = 0; i < 4; i++) v |= (uint32_t)p[i] << (8 * i); return v; }
static uint64_t hit_get64(const uint8_t *p) { uint64_t v = 0; for (unsigned i = 0; i < 8; i++) v |= (uint64_t)p[i] << (8 * i); return v; }

static int hit_observation_valid(const OotCoopHit *h) {
    if (h->phase != OOT_HIT_OBSERVATION || h->source_kind != OOT_HIT_UNKNOWN || h->source_member || h->source_id ||
        h->attack_class || !h->origin_session || !h->event_id || !h->area || !h->epoch ||
        h->target_member < 1 || h->target_member > COOP_MAX_MEMBERS || !h->target_id ||
        h->before > INT32_MAX || h->after >= h->before || h->before - h->after > UINT32_MAX / 256u ||
        h->amount_q8 != (h->before - h->after) * 256u || h->flags || h->grace_ms ||
        h->authority_term || h->source_generation || h->target_generation) return 0;
    if (h->target_kind == OOT_HIT_PLAYER) {
        if (h->target_id != h->origin_session) return 0;
    } else if (h->target_kind != OOT_HIT_LOCAL || h->target_id > (uint64_t)UINT32_MAX + 1) return 0;
    for (int i = 0; i < 3; i++)
        if (!isfinite(h->impact[i]) || !isfinite(h->direction[i]) || h->impact[i] != 0 || h->direction[i] != 0) return 0;
    return 1;
}

size_t oot_coop_hit_pack(uint8_t *p, size_t cap, const OotCoopHit *h) {
    if (!p || !h || cap < OOT_HIT_BYTES || !hit_observation_valid(h)) return 0;
    memset(p, 0, OOT_HIT_BYTES);
    p[0] = OOT_HIT_VERSION; p[1] = h->phase; p[2] = h->source_kind; p[3] = h->target_kind;
    p[4] = h->source_member; p[5] = h->target_member; hit_put16(p + 6, h->attack_class);
    hit_put64(p + 8, h->origin_session); hit_put64(p + 16, h->event_id);
    hit_put64(p + 24, h->area); hit_put64(p + 32, h->epoch);
    hit_put64(p + 40, h->source_id); hit_put64(p + 48, h->target_id);
    hit_put32(p + 56, h->before); hit_put32(p + 60, h->after);
    hit_put32(p + 64, h->amount_q8); hit_put32(p + 68, h->tick);
    /* Observation position/direction, grace, flags and world terms are unknown: canonical zeros. */
    return OOT_HIT_BYTES;
}

int oot_coop_hit_parse(const uint8_t *p, size_t n, OotCoopHit *h) {
    if (!p || !h || n != OOT_HIT_BYTES || p[0] != OOT_HIT_VERSION) return 0;
    OotCoopHit v = {0};
    v.phase = p[1]; v.source_kind = p[2]; v.target_kind = p[3]; v.source_member = p[4]; v.target_member = p[5];
    v.attack_class = hit_get16(p + 6); v.origin_session = hit_get64(p + 8); v.event_id = hit_get64(p + 16);
    v.area = hit_get64(p + 24); v.epoch = hit_get64(p + 32); v.source_id = hit_get64(p + 40); v.target_id = hit_get64(p + 48);
    v.before = hit_get32(p + 56); v.after = hit_get32(p + 60); v.amount_q8 = hit_get32(p + 64); v.tick = hit_get32(p + 68);
    for (int i = 0; i < 3; i++) {
        uint32_t a = hit_get32(p + 72 + 4 * i), b = hit_get32(p + 84 + 4 * i);
        memcpy(&v.impact[i], &a, 4); memcpy(&v.direction[i], &b, 4);
    }
    v.grace_ms = hit_get16(p + 96); v.flags = hit_get16(p + 98);
    v.authority_term = hit_get32(p + 100); v.source_generation = hit_get32(p + 104); v.target_generation = hit_get32(p + 108);
    if (!hit_observation_valid(&v)) return 0;
    *h = v;
    return 1;
}

int oot_coop_hit_once(OotCoopHitSeen *s, uint64_t id) {
    if (!id) return 0;
    if (id > s->high) {
        const uint64_t d = id - s->high;
        s->bits = d >= 64 ? 1 : (s->bits << d) | 1;
        s->high = id;
        return 1;
    }
    const uint64_t d = s->high - id;
    if (d >= 64 || (s->bits & (UINT64_C(1) << d))) return 0;
    s->bits |= UINT64_C(1) << d;
    return 1;
}

int oot_coop_hit_rule(const CoopRules *r, int id, int *value) {
    if (!r || !r->epoch || r->features.count > COOP_FEATURE_MAX) return 0;
    for (unsigned i = 0; i < r->features.count; i++) {
        const uint8_t *p = r->features.bytes + 3 * i;
        if (p[0] == id) {
            if (value) *value = (int16_t)hit_get16(p + 1);
            return 1;
        }
    }
    return 0;
}

int oot_coop_hit_admit(OotCoopHitGate *g, const OotCoopHitContext *v, const OotCoopHitReceipt *r,
                      int member, unsigned attack_class, uint32_t claimed_damage, uint64_t at_ms) {
    if (!g || !v || !r || !v->sessions || v->self < 1 || v->self > COOP_MAX_MEMBERS ||
        member < 1 || member > COOP_MAX_MEMBERS || member == v->self ||
        !(v->accepted_members & (1u << member)) || !r->session || !r->event_id ||
        r->session != v->sessions[member] || v->rule_id < 1 || v->rule_id > 255 ||
        !v->max_bytes || v->max_bytes > COOP_MAX_MESSAGE || !v->max_damage ||
        !v->max_age_ms || v->max_age_ms > 10000 ||
        !v->per_second || v->per_second > OOT_HIT_CLAIM_RATE_MAX ||
        !v->burst || v->burst > OOT_HIT_CLAIM_RATE_MAX) return 0;

    OotCoopHitPeerGate *p = &g->peer[member];
    const uint32_t capacity = v->burst * 1000;
    if (p->session != r->session)
        *p = (OotCoopHitPeerGate){.session = r->session, .budget_at = v->now_ms, .tokens = capacity};
    if (v->now_ms < p->budget_at || !oot_coop_hit_once(&p->seen, r->event_id)) return 0;
    const uint64_t elapsed = v->now_ms - p->budget_at;
    /* Cap before multiplying: a long pause or a synthetic extreme clock cannot overflow the bucket. */
    if (elapsed >= (capacity + v->per_second - 1) / v->per_second)
        p->tokens = capacity;
    else {
        const uint32_t refilled = p->tokens + (uint32_t)elapsed * v->per_second;
        p->tokens = refilled < capacity ? refilled : capacity;
    }
    p->budget_at = v->now_ms;
    if (p->tokens < 1000) return 0;
    p->tokens -= 1000;

    int permitted = 0;
    return v->room_accepted == 1 && v->playing == 1 && v->local_opt_in == 1 && v->peer_opt_in == 1 &&
           v->peer_opt_in_session == r->session &&
           !v->in_event && v->now_ms >= v->safe_after_ms &&
           oot_coop_hit_rule(v->rules, v->rule_id, &permitted) && permitted == 1 &&
           attack_class < OOT_HIT_CLAIM_CLASSES && (v->allowed_classes & (1u << attack_class)) &&
           claimed_damage && claimed_damage <= v->max_damage && r->bytes && r->bytes <= v->max_bytes &&
           at_ms <= v->now_ms && v->now_ms - at_ms <= v->max_age_ms;
}

/* ---- native boundary: the known build only, never a packet-supplied guest pointer */
#define HITS_BUILD "e0f3ccfaf50065ae4ea01eb5aeb13b1a45da3348"
enum { HIT_LIFE_OWNER = 24, HIT_LIFE_VALUE = 7184, HIT_VALUE_CURRENT = 8, HIT_ACTOR_NUMBER = 24 };
int hos_oot_is_player(Ctx *c, uint64_t component);
int hos_oot_hits_active, hos_oot_attack_observe;
static uint64_t hit_invocation, hit_busy_drops;
static const OotCoopLifePolicy *hit_policy;

enum { HIT_PENDING = 64, HIT_BATCH = 8, HIT_LIFETIME_MS = 1000, HIT_RATE = 4096 };
enum { NATIVE_LIFE, NATIVE_SETUP, NATIVE_RENEW, NATIVE_OFF };
typedef struct {
    uint64_t invocation;
    int64_t delta;
    uint32_t owner, before, requested, after, primary, secondary, layer;
    int32_t power;
    float multiplier;
    unsigned kind, path, player;
} HitNative;
typedef struct {
    uint8_t bytes[OOT_HIT_BYTES];
    uint64_t at, sessions[COOP_MAX_MEMBERS + 1];
    unsigned recipients;
} HitPending;
static struct {
    SRWLOCK lock;
    CoopView view;
    OotCoopHitGate ingress;
    CoopRules rules;
    uint64_t sessions[COOP_MAX_MEMBERS + 1], area, epoch, budget_at;
    char map[COOP_POS_TEXT + 1];
    int started, known, trace, share, policy_on, playing;
    unsigned tokens, accepted, native_count;
    uint64_t received, dropped, native_dropped;
    HitPending pending[HIT_PENDING];
    HitNative native[HIT_PENDING];
} H = {.lock = SRWLOCK_INIT};

static void hit_active_locked(void) {
    __atomic_store_n(&hos_oot_hits_active, H.known && (H.trace || H.share || H.policy_on), __ATOMIC_RELEASE);
    __atomic_store_n(&hos_oot_attack_observe, H.known && H.trace, __ATOMIC_RELEASE);
}

int hos_oot_hits_bind(const OotCoopLifePolicy *p) {
    if (!p || !p->before || !p->committed) return 0;
    const OotCoopLifePolicy *expected = NULL;
    return __atomic_compare_exchange_n(&hit_policy, &expected, p, 0, __ATOMIC_RELEASE, __ATOMIC_ACQUIRE) || expected == p;
}

void hos_oot_hits_policy_on(int on) {
    AcquireSRWLockExclusive(&H.lock);
    __atomic_store_n(&H.policy_on, on && __atomic_load_n(&hit_policy, __ATOMIC_ACQUIRE) != NULL, __ATOMIC_RELAXED);
    hit_active_locked();
    ReleaseSRWLockExclusive(&H.lock);
}

uint64_t hos_oot_hits_begin(void) {
    if (!__atomic_load_n(&hos_oot_hits_active, __ATOMIC_ACQUIRE)) return 0;
    uint64_t old = __atomic_load_n(&hit_invocation, __ATOMIC_RELAXED);
    do {
        if (old == (UINT64_MAX >> 1)) return 0;
    } while (!__atomic_compare_exchange_n(&hit_invocation, &old, old + 1, 1, __ATOMIC_RELAXED, __ATOMIC_RELAXED));
    return old + 1;
}

static int hit_readable(uint64_t a, size_t n) { return a >= 0x100000 && hos_guest_range(a, n) && hos_mem_region(a, n); }
static int hit_change(Ctx *c, uint64_t token, unsigned path, uint32_t before, uint32_t requested, OotCoopLifeChange *out) {
    const uint64_t component = c->x[19], cell = c->x[13];
    if (!token || path > 1 || !hit_readable(component, HIT_LIFE_VALUE + 8)) return 0;
    const uint64_t value = recomp_load64(c, component + HIT_LIFE_VALUE), owner = recomp_load64(c, component + HIT_LIFE_OWNER);
    if (!value || cell != value + HIT_VALUE_CURRENT || !hit_readable(cell, 4) || !hit_readable(owner, HIT_ACTOR_NUMBER + 4)) return 0;
    *out = (OotCoopLifeChange){token, component, owner, cell, before, requested, (uint32_t)c->x[12],
                             path ? (int64_t)(int32_t)c->x[9] : -(int64_t)(int32_t)c->x[9], path};
    return 1;
}

uint32_t hos_oot_hits_before(Ctx *c, uint64_t token, unsigned path) {
    const uint32_t desired = (uint32_t)c->x[12];
    const OotCoopLifePolicy *policy = __atomic_load_n(&hit_policy, __ATOMIC_ACQUIRE);
    OotCoopLifeChange change;
    if (!policy || !__atomic_load_n(&H.policy_on, __ATOMIC_RELAXED) ||
        !hit_change(c, token, path, (uint32_t)c->x[11], desired, &change)) return desired;
    const uint32_t filtered = policy->before(&change, policy->user);
    /* A policy cannot undo Protect hearts, increase a requested loss, or invent healing. */
    if (desired < change.before) return filtered >= desired && filtered <= change.before ? filtered : change.before;
    return desired;
}

static int hit_try_lock(void) {
    if (TryAcquireSRWLockExclusive(&H.lock)) return 1;
    __atomic_fetch_add(&hit_busy_drops, 1, __ATOMIC_RELAXED);
    return 0;
}

static void hit_trace_locked(const HitNative *sample) {
    if (!H.trace) return;
    if (H.native_count == HIT_PENDING) { H.native_dropped++; return; }
    H.native[H.native_count++] = *sample;
}

void hos_oot_hits_committed(Ctx *c, uint64_t token, unsigned path, uint32_t requested) {
    OotCoopLifeChange change;
    if ((uint32_t)c->x[11] != (uint32_t)c->x[10] ||
        !hit_change(c, token, path, (uint32_t)c->x[10], requested, &change)) return;
    const OotCoopLifePolicy *policy = __atomic_load_n(&hit_policy, __ATOMIC_ACQUIRE);
    if (policy && __atomic_load_n(&H.policy_on, __ATOMIC_RELAXED)) policy->committed(&change, policy->user);
    const int player = hos_oot_is_player(c, change.component);
    const uint32_t owner = (uint32_t)recomp_load32(c, change.owner + HIT_ACTOR_NUMBER);
    const uint64_t now = coop_now_ms();
    if (!hit_try_lock()) return;
    const HitNative sample = {.kind = NATIVE_LIFE, .invocation = token, .owner = owner, .player = player,
        .before = change.before, .requested = requested, .after = change.after, .delta = change.delta, .path = path};
    hit_trace_locked(&sample);
    if (!H.share || !H.playing || H.view.state != COOP_JOINED || !H.area || change.after >= change.before ||
        change.before - change.after > UINT32_MAX / 256u) { ReleaseSRWLockExclusive(&H.lock); return; }
    OotCoopHit hit = {.phase = OOT_HIT_OBSERVATION, .target_kind = player ? OOT_HIT_PLAYER : OOT_HIT_LOCAL,
        .target_member = (uint8_t)H.view.self, .origin_session = H.view.session,
        .event_id = (token << 1) | path, .area = H.area, .epoch = H.epoch,
        .target_id = player ? H.view.session : (uint64_t)owner + 1,
        .before = change.before, .after = change.after, .amount_q8 = (change.before - change.after) * 256u,
        .tick = (uint32_t)now};
    for (unsigned i = 0; i < HIT_PENDING; i++) if (!H.pending[i].recipients) {
        HitPending *p = &H.pending[i];
        if (!oot_coop_hit_pack(p->bytes, sizeof p->bytes, &hit)) break;
        p->at = now;
        for (int id = 1; id <= COOP_MAX_MEMBERS; id++)
            if (id != H.view.self && (H.accepted & (1u << id)) && H.sessions[id]) {
                p->recipients |= 1u << id; p->sessions[id] = H.sessions[id];
            }
        ReleaseSRWLockExclusive(&H.lock);
        return;
    }
    H.dropped++;
    ReleaseSRWLockExclusive(&H.lock);
}

/* Setup completion is not contact: retain native IDs and power inputs, even for repeated setup with the same IDs.
   Renewal and off records distinguish those operations. No animation name or controller button supplies a hit. */
static int hit_sensor_owner(Ctx *c, uint64_t sensor, uint32_t *number) {
    if (!hit_readable(sensor, 152)) return 0;
    const uint64_t owner = recomp_load64(c, sensor + 24);
    if (!hit_readable(owner, 632)) return 0;
    const uint64_t components = recomp_load64(c, owner + 624);
    if (!hit_readable(components, 960) || recomp_load64(c, components + 952) != sensor) return 0;
    *number = (uint32_t)recomp_load32(c, owner + HIT_ACTOR_NUMBER);
    return 1;
}

void hos_oot_hits_attack(Ctx *c, uint64_t sensor, uint64_t record) {
    if (!__atomic_load_n(&hos_oot_attack_observe, __ATOMIC_ACQUIRE)) return;
    HitNative sample = {.kind = NATIVE_SETUP};
    if (!hit_sensor_owner(c, sensor, &sample.owner) || !hit_readable(record, 428)) return;
    unsigned slot;
    for (slot = 0; slot < 3; slot++) if (recomp_load64(c, sensor + 128 + 8 * slot) == record) break;
    if (slot == 3) return;
    sample.path = slot;
    sample.primary = (uint32_t)recomp_load32(c, record + 400);
    sample.secondary = (uint32_t)recomp_load32(c, record + 408);
    sample.power = (int32_t)recomp_load32(c, record + 320);
    const uint32_t bits = (uint32_t)recomp_load32(c, record + 324);
    memcpy(&sample.multiplier, &bits, sizeof bits);
    if (!sample.primary || !sample.secondary || !isfinite(sample.multiplier) || sample.multiplier < 0) return;
    sample.invocation = hos_oot_hits_begin();
    if (!sample.invocation || !hit_try_lock()) return;
    hit_trace_locked(&sample);
    ReleaseSRWLockExclusive(&H.lock);
}

void hos_oot_hits_attack_id(Ctx *c, uint64_t record) {
    if (!__atomic_load_n(&hos_oot_attack_observe, __ATOMIC_ACQUIRE) || !hit_readable(record, 368)) return;
    HitNative sample = {.kind = NATIVE_RENEW, .primary = (uint32_t)recomp_load32(c, record + 352),
        .secondary = (uint32_t)recomp_load32(c, record + 360)};
    if (!sample.secondary) return;
    sample.invocation = hos_oot_hits_begin();
    if (!sample.invocation || !hit_try_lock()) return;
    hit_trace_locked(&sample);
    ReleaseSRWLockExclusive(&H.lock);
}

void hos_oot_hits_attack_off(Ctx *c, uint64_t sensor, unsigned layer) {
    if (!__atomic_load_n(&hos_oot_attack_observe, __ATOMIC_ACQUIRE)) return;
    HitNative sample = {.kind = NATIVE_OFF, .layer = layer};
    if (!hit_sensor_owner(c, sensor, &sample.owner)) return;
    sample.invocation = hos_oot_hits_begin();
    if (!sample.invocation || !hit_try_lock()) return;
    hit_trace_locked(&sample);
    ReleaseSRWLockExclusive(&H.lock);
}

static void hit_on_view(const CoopView *v, void *user) {
    (void)user;
    AcquireSRWLockExclusive(&H.lock);
    if (H.started && (int32_t)(v->version - H.view.version) < 0) { ReleaseSRWLockExclusive(&H.lock); return; }
    if (v->session != H.view.session || strcmp(v->code, H.view.code)) {
        memset(H.pending, 0, sizeof H.pending); memset(&H.ingress, 0, sizeof H.ingress);
    }
    memset(H.sessions, 0, sizeof H.sessions);
    if (v->state == COOP_JOINED)
        for (int i = 0; i < v->n && i < COOP_MAX_MEMBERS; i++)
            if (v->member[i].id >= 1 && v->member[i].id <= COOP_MAX_MEMBERS && !v->member[i].away)
                H.sessions[v->member[i].id] = v->member[i].session;
    H.view = *v;
    ReleaseSRWLockExclusive(&H.lock);
}

static void hit_on_message(int from, uint64_t session, uint64_t event, const uint8_t *bytes, size_t n, void *user) {
    (void)user; (void)event;
    OotCoopHit hit;
    if (from < 1 || from > COOP_MAX_MEMBERS || !coop_member_accepted(from) ||
        !oot_coop_hit_parse(bytes, n, &hit) || hit.target_member != from || hit.origin_session != session) return;
    const uint64_t now = coop_now_ms();
    AcquireSRWLockExclusive(&H.lock);
    const OotCoopHitReceipt receipt = {session, hit.event_id, n};
    const OotCoopHitContext context = {
        .rules = &H.rules, .sessions = H.sessions, .accepted_members = 1u << from,
        .allowed_classes = 1u, .max_damage = UINT32_MAX / 256u, .max_bytes = OOT_HIT_BYTES,
        .now_ms = now, .max_age_ms = HIT_LIFETIME_MS, .per_second = 32, .burst = 32,
        .peer_opt_in_session = session, .self = H.view.self, .rule_id = OOT_RULE_HIT_EVENTS,
        .room_accepted = H.share && H.view.state == COOP_JOINED,
        .playing = 1, .local_opt_in = 1, .peer_opt_in = 1,
    };
    const int admitted = oot_coop_hit_admit(&H.ingress, &context, &receipt, from, 0, hit.before - hit.after, now);
    if (admitted) H.received++;
    ReleaseSRWLockExclusive(&H.lock);
    if (admitted) hos_log("coop: hit: member %d observed native %s %u -> %u (event %llu); observation only", from,
        hit.target_kind == OOT_HIT_PLAYER ? "player" : "actor", hit.before, hit.after, (unsigned long long)hit.event_id);
}

void hos_oot_hits_room(const CoopRules *rules, int accepted) {
    int value = 0;
    const int share = accepted && oot_coop_hit_rule(rules, OOT_RULE_HIT_EVENTS, &value) && value == 1;
    AcquireSRWLockExclusive(&H.lock);
    if (!share || !rules || memcmp(&H.rules, rules, sizeof H.rules)) memset(H.pending, 0, sizeof H.pending);
    H.rules = accepted && rules ? *rules : (CoopRules){0};
    H.share = share;
    hit_active_locked();
    ReleaseSRWLockExclusive(&H.lock);
}

/* Present-thread drain: native workers never wait for the socket, print, allocate or retain guest pointers. */
void hos_oot_hits_frame(const char *area, int playing) {
    if (!__atomic_load_n(&hos_oot_hits_active, __ATOMIC_ACQUIRE)) return;
    const uint64_t now = coop_now_ms();
    unsigned accepted = 0;
    for (int id = 1; id <= COOP_MAX_MEMBERS; id++) if (coop_member_accepted(id)) accepted |= 1u << id;
    HitNative native[HIT_PENDING];
    HitPending batch[HIT_BATCH];
    unsigned count, n = 0;
    if (!TryAcquireSRWLockExclusive(&H.lock)) return;
    H.playing = playing && area && *area;
    H.accepted = accepted;
    if (!H.playing) area = "";
    if (strcmp(H.map, area)) {
        snprintf(H.map, sizeof H.map, "%s", area);
        uint8_t digest[8];
        coop_blake2b(digest, sizeof digest, NULL, 0, H.map, strlen(H.map));
        H.area = H.playing ? hit_get64(digest) : 0;
        if (H.epoch != UINT64_MAX) H.epoch++;
        memset(H.pending, 0, sizeof H.pending);
    }
    count = H.native_count;
    memcpy(native, H.native, count * sizeof *native);
    H.native_count = 0;
    const uint64_t dropped = H.native_dropped + __atomic_exchange_n(&hit_busy_drops, 0, __ATOMIC_RELAXED);
    H.native_dropped = 0;
    if (!H.budget_at || now - H.budget_at >= 1000) H.tokens = HIT_RATE, H.budget_at = now;
    for (unsigned i = 0; i < HIT_PENDING && n < HIT_BATCH; i++) {
        HitPending *p = &H.pending[i];
        if (!p->recipients) continue;
        if (!H.share || !H.playing || now < p->at || now - p->at > HIT_LIFETIME_MS) { p->recipients = 0; H.dropped++; continue; }
        unsigned mask = 0, recipients = 0;
        for (int id = 1; id <= COOP_MAX_MEMBERS; id++)
            if ((p->recipients & accepted & (1u << id)) && p->sessions[id] == H.sessions[id] && H.sessions[id])
                mask |= 1u << id, recipients++;
        if (!recipients) { p->recipients = 0; continue; }
        const unsigned cost = recipients * (OOT_HIT_BYTES + 54);
        if (H.tokens < cost) continue;
        H.tokens -= cost;
        batch[n] = *p; batch[n++].recipients = mask;
        p->recipients = 0;
    }
    ReleaseSRWLockExclusive(&H.lock);
    if (dropped) hos_log("coop: hit: trace incomplete: %llu native records dropped", (unsigned long long)dropped);
    for (unsigned i = 0; i < count; i++) {
        const HitNative *p = &native[i];
        if (p->kind == NATIVE_LIFE)
            hos_log("coop: hit: native %s %u -> %u requested %u pre-clamp delta %lld (invocation %llu, path %u)",
                p->player ? "player" : "actor", p->before, p->after, p->requested, (long long)p->delta,
                (unsigned long long)p->invocation, p->path);
        else
            hos_log("coop: attack: %s owner %u ids %u/%u slot %u layer %u power %d multiplier %.9g (sample %llu); observation only",
                p->kind == NATIVE_SETUP ? "setup" : p->kind == NATIVE_RENEW ? "renew" : "off",
                p->owner, p->primary, p->secondary, p->path, p->layer, p->power, (double)p->multiplier,
                (unsigned long long)p->invocation);
    }
    for (unsigned i = 0; i < n; i++) for (int id = 1; id <= COOP_MAX_MEMBERS; id++)
        if (batch[i].recipients & (1u << id))
            if (coop_send_event(id, COOP_K_HIT, batch[i].bytes, OOT_HIT_BYTES) != COOP_EVENT_QUEUED)
                hos_log("coop: hit: observation not queued for member %d; transport unavailable or full", id);
}

void hos_oot_hits_start(void) {
    AcquireSRWLockExclusive(&H.lock);
    if (H.started) { ReleaseSRWLockExclusive(&H.lock); return; }
    H.started = 1;
    H.known = !strcmp(hos_main_build_id, HITS_BUILD);
    H.trace = env_on("HOS_COOP_HITS_TRACE");
    const int known = H.known, trace = H.trace;
    ReleaseSRWLockExclusive(&H.lock);
    if (!known) {
        if (trace) hos_log("coop: hit: unsupported game build; no native observation");
        return;
    }
    if (!coop_on_event(COOP_K_HIT, hit_on_message, NULL) || !coop_on_view(hit_on_view, NULL)) {
        hos_log("coop: hit: cannot register observation hooks");
        AcquireSRWLockExclusive(&H.lock); H.known = 0; hit_active_locked(); ReleaseSRWLockExclusive(&H.lock);
        return;
    }
    CoopView view; coop_view_get(&view); hit_on_view(&view, NULL);
    AcquireSRWLockExclusive(&H.lock); hit_active_locked(); ReleaseSRWLockExclusive(&H.lock);
}
