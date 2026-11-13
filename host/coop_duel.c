/* (longshot) No game calls, health pointer, file writes, network thread or allocations. */
#include "coop_duel.h"
#include "coop_proto.h"
#include <stdio.h>
#include <string.h>
#include <limits.h>

static void put32(uint8_t *p, uint32_t v) { for (unsigned i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (8 * i)); }
static void put64(uint8_t *p, uint64_t v) { for (unsigned i = 0; i < 8; i++) p[i] = (uint8_t)(v >> (8 * i)); }
static uint32_t get32(const uint8_t *p) { return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static uint64_t get64(const uint8_t *p) { uint64_t v = 0; for (unsigned i = 0; i < 8; i++) v |= (uint64_t)p[i] << (8 * i); return v; }
static int present(const CoopRules *r, int id, int lo, int hi, int *out) {
    int value;
    if (!coop_feature_present(r, id, &value) || value < lo || value > hi) return 0;
    if (out) *out = value;
    return 1;
}

int coop_duel_terms(CoopDuelTerms *out, const CoopRules *r, const CoopArena *a,
                    uint64_t id, uint64_t maker, const uint8_t member[2], const uint64_t session[2]) {
    int hearts, rounds, grace, damage, age, kit, ignored;
    if (!out || !r || !a || !member || !session || !id || !maker || !r->epoch ||
        member[0] < 1 || member[0] >= member[1] || member[1] > COOP_MAX_MEMBERS ||
        !session[0] || !session[1] || session[0] == session[1] || !coop_arena_valid(a) ||
        !present(r, 80, 1, 1, NULL) || !present(r, 16, 1, 1, NULL) ||
        coop_feature_present(r, 17, &ignored) || !present(r, 18, 0, 400, &damage) ||
        !present(r, 19, 1, 1, NULL) || !present(r, 20, 250, 2000, &grace) || !present(r, 21, 0, 0, NULL) ||
        !present(r, 31, 1, 40, &hearts) || !present(r, 32, 0, 1, &age) || age != a->age ||
        !present(r, 34, 1, 5, &rounds) || !(rounds & 1) || !present(r, 35, 0, 0, NULL) ||
        !present(r, 36, 0, 0, NULL) || !present(r, 37, a->id, a->id, NULL) ||
        !present(r, 38, 0, 0, NULL) || !present(r, 39, 0, 0, NULL) ||
        !present(r, 40, 0, 32767, &kit) || kit != (age ? 12 : 3) ||
        !present(r, 83, 1, 1, NULL) || !present(r, 97, 1, 1, NULL)) return 0;
    /* No silently ignored weapon/magic/consumable or non-native balance setting. */
    const int zero[] = {22,23,24,25,26,27,28,29,33,41,42,43,44,45,46,47,82,84,85,86,87,88,89,90,91,92,93,94,95,96};
    for (size_t i = 0; i < sizeof zero / sizeof *zero; i++)
        if (coop_feature_present(r, zero[i], &ignored) && ignored) return 0;
    if (r->features.count > COOP_FEATURE_MAX) return 0;
    unsigned last = 0;
    for (unsigned i = 0; i < r->features.count; i++) {
        unsigned next = r->features.bytes[3 * i];
        if (!next || next <= last) return 0;
        last = next;
    }
    for (unsigned i = 0; i < COOP_CAT_N; i++) if (r->mode[i] > COOP_MODE_POOL) return 0;
    CoopDuelTerms t = {0};
    t.rules = *r; t.arena = *a; t.id = id; t.maker_key = maker;
    memcpy(t.member, member, 2); memcpy(t.session, session, sizeof t.session);
    t.hearts = (uint16_t)hearts; t.rounds = (uint16_t)rounds; t.grace_ms = (uint16_t)grace;
    t.damage_percent = (uint16_t)damage; t.kit_bits = (uint16_t)kit;
    uint8_t bytes[320] = {0}, arena[32];
    if (!coop_arena_digest(a, arena)) return 0;
    memcpy(bytes, "longshot-coop/1 duel", 19);
    put64(bytes + 24, id); put64(bytes + 32, maker);
    put64(bytes + 40, session[0]); put64(bytes + 48, session[1]);
    bytes[56] = member[0]; bytes[57] = member[1]; put32(bytes + 58, r->epoch);
    memcpy(bytes + 62, r->mode, COOP_CAT_N); bytes[69] = r->features.count;
    memcpy(bytes + 70, r->features.bytes, (size_t)r->features.count * 3);
    memcpy(bytes + 262, arena, 32);
    /* Damage/catalog version is explicitly present in the complete canonical feature bytes above. */
    coop_blake2b(t.digest, sizeof t.digest, NULL, 0, bytes, sizeof bytes);
    *out = t; return 1;
}

void coop_duel_terms_text(const CoopDuelTerms *t, char *out, size_t cap) {
    if (!out || !cap) return;
    if (!t) { snprintf(out, cap, "Duel terms unavailable"); return; }
    snprintf(out, cap, "Kokiri arena %u; %u hearts; best of %u; %s sword and shield; damage %u%%; grace %u ms",
        t->arena.version, t->hearts, t->rounds, t->arena.age ? "adult" : "child", t->damage_percent, t->grace_ms);
}

static int control_valid(const CoopDuelControl *m) {
    if (!m || m->kind < COOP_DUEL_READY || m->kind > COOP_DUEL_CANCEL || !m->epoch || !m->round ||
        !m->duel || !m->session || m->closed > 1 || m->hp > 160 || m->capabilities > COOP_DUEL_CAP_REQUIRED) return 0;
    if (m->kind == COOP_DUEL_READY)
        return !m->closed && !m->hp && !m->sequence && m->save_generation && m->area_generation && m->kit_generation;
    if (m->capabilities || m->save_generation || m->area_generation || m->kit_generation) return 0;
    return m->kind == COOP_DUEL_STATUS ? m->sequence != 0 : !m->closed && !m->hp && !m->sequence;
}

size_t coop_duel_control_pack(uint8_t *p, size_t cap, const CoopDuelControl *m) {
    if (!p || cap < COOP_DUEL_CONTROL_BYTES || !control_valid(m)) return 0;
    memset(p, 0, COOP_DUEL_CONTROL_BYTES);
    p[0] = COOP_DUEL_VERSION; p[1] = m->kind; p[2] = m->closed;
    put32(p + 4, m->epoch); put32(p + 8, m->round); put32(p + 12, m->capabilities);
    put32(p + 16, m->sequence); put32(p + 20, m->hp);
    put64(p + 24, m->duel); put64(p + 32, m->session);
    put64(p + 40, m->save_generation); put64(p + 48, m->area_generation); put64(p + 56, m->kit_generation);
    memcpy(p + 64, m->digest, 32); return COOP_DUEL_CONTROL_BYTES;
}

int coop_duel_control_parse(const uint8_t *p, size_t n, CoopDuelControl *out) {
    if (!p || !out || n != COOP_DUEL_CONTROL_BYTES || p[0] != COOP_DUEL_VERSION || p[3] || get32(p + 20) > 160) return 0;
    for (unsigned i = 96; i < COOP_DUEL_CONTROL_BYTES; i++) if (p[i]) return 0;
    CoopDuelControl m = {0};
    m.kind = p[1]; m.closed = p[2]; m.epoch = get32(p + 4); m.round = get32(p + 8);
    m.capabilities = get32(p + 12); m.sequence = get32(p + 16); m.hp = (uint16_t)get32(p + 20);
    m.duel = get64(p + 24); m.session = get64(p + 32);
    m.save_generation = get64(p + 40); m.area_generation = get64(p + 48); m.kit_generation = get64(p + 56);
    memcpy(m.digest, p + 64, 32);
    if (!control_valid(&m)) return 0;
    *out = m; return 1;
}

static int context_reason(const CoopDuelContext *c, uint64_t now, int proof) {
    if (!c || !c->enabled || !c->diagnostic) return COOP_DUEL_DISABLED;
    if (!c->joined || !c->accepted || !c->offer || !c->epoch) return COOP_DUEL_NOT_ACCEPTED;
    if (!c->temporary || c->self > 1 || !c->save_generation || !c->area_generation || !c->kit_generation ||
        !c->playing || c->menu_or_chat || c->sampled_ms > now || now - c->sampled_ms > COOP_DUEL_HIT_AGE_MS) return COOP_DUEL_CONTEXT;
    if (proof && c->capabilities != COOP_DUEL_CAP_REQUIRED) return COOP_DUEL_CAPABILITY;
    return COOP_DUEL_OK;
}

void coop_duel_interrupt(CoopDuel *d, int reason) {
    if (!d) return;
    d->ready[0] = d->ready[1] = d->closed[0] = d->closed[1] = 0;
    d->countdown_ms = d->settle_ms = 0;
    d->state = COOP_DUEL_INTERRUPTED; d->reason = reason;
}

int coop_duel_begin(CoopDuel *d, const CoopDuelTerms *t, const CoopDuelContext *c, uint64_t now) {
    if (!d || !t) return COOP_DUEL_TERMS;
    int why = context_reason(c, now, 0);
    if (why) return why;
    CoopDuelTerms checked;
    if (!coop_duel_terms(&checked, &t->rules, &t->arena, t->id, t->maker_key, t->member, t->session) ||
        memcmp(checked.digest, t->digest, 32) || c->epoch != t->rules.epoch) return COOP_DUEL_TERMS;
    if (d->terms.id && !memcmp(d->terms.session, t->session, sizeof t->session) && t->id <= d->terms.id) return COOP_DUEL_REPLAY;
    *d = (CoopDuel){0}; d->terms = checked; d->context = *c; d->begun_ms = d->now_ms = now;
    d->round = 1; d->state = COOP_DUEL_PREVIEW;
    d->hp[0] = d->hp[1] = (uint16_t)(4 * checked.hearts); d->sequence = 1;
    d->attack_tokens = 32000; d->budget_ms = now;
    return COOP_DUEL_OK;
}

static void settle(CoopDuel *d, uint64_t now) {
    if (d->state == COOP_DUEL_ACTIVE && (!d->hp[0] || !d->hp[1])) {
        d->state = COOP_DUEL_SETTLING; d->settle_ms = now;
    }
}
static void finish(CoopDuel *d) {
    if (d->state != COOP_DUEL_SETTLING || !d->closed[0] || !d->closed[1]) return;
    if (d->hp[0] && d->hp[1]) { coop_duel_interrupt(d, COOP_DUEL_PEER); return; }
    d->last_winner = d->hp[0] ? 1 : d->hp[1] ? 2 : 0;
    if (d->last_winner) d->score[d->last_winner - 1]++;
    d->ready[0] = d->ready[1] = 0;
    const unsigned wins = d->terms.rounds / 2 + 1;
    d->state = d->score[0] >= wins || d->score[1] >= wins ? COOP_DUEL_FINISHED : COOP_DUEL_ROUND_END;
}

int coop_duel_update(CoopDuel *d, const CoopDuelContext *c, uint64_t now) {
    if (!d) return COOP_DUEL_DISABLED;
    if (!c || !c->enabled || !c->diagnostic) {
        if (d->state != COOP_DUEL_OFF && d->state != COOP_DUEL_INTERRUPTED) coop_duel_interrupt(d, COOP_DUEL_DISABLED);
        return COOP_DUEL_DISABLED;
    }
    if (d->state == COOP_DUEL_OFF || d->state == COOP_DUEL_INTERRUPTED) return d->reason ? d->reason : COOP_DUEL_DISABLED;
    int why = context_reason(c, now, d->state != COOP_DUEL_PREVIEW || d->ready[0] || d->ready[1]);
    if (!why && (c->offer != d->context.offer || c->epoch != d->terms.rules.epoch || c->self != d->context.self ||
        c->save_generation != d->context.save_generation || c->area_generation != d->context.area_generation ||
        c->kit_generation != d->context.kit_generation || now < d->now_ms)) why = COOP_DUEL_CONTEXT;
    if (why) { coop_duel_interrupt(d, why); return why; }
    d->context = *c; d->now_ms = now;
    if ((d->state == COOP_DUEL_PREVIEW || d->state == COOP_DUEL_COUNTDOWN) && now - d->begun_ms > COOP_DUEL_READY_MS) {
        coop_duel_interrupt(d, COOP_DUEL_TIMEOUT); return d->reason;
    }
    if (d->state == COOP_DUEL_COUNTDOWN && now - d->countdown_ms >= COOP_DUEL_COUNTDOWN_MS) {
        d->state = COOP_DUEL_ACTIVE; d->round_ms = now;
    }
    if (d->state == COOP_DUEL_SETTLING) {
        if (!d->closed[c->self] && now - d->settle_ms >= COOP_DUEL_SETTLE_MS) {
            if (d->sequence == UINT32_MAX) { coop_duel_interrupt(d, COOP_DUEL_LIMIT); return d->reason; }
            d->closed[c->self] = 1; d->sequence++;
        }
        finish(d);
        if (d->state == COOP_DUEL_SETTLING && now - d->settle_ms >= COOP_DUEL_CLOSE_MS) {
            coop_duel_interrupt(d, COOP_DUEL_TIMEOUT); return d->reason;
        }
    }
    return COOP_DUEL_OK;
}

static void message(const CoopDuel *d, unsigned kind, CoopDuelControl *m) {
    *m = (CoopDuelControl){0}; m->kind = (uint8_t)kind; m->epoch = d->terms.rules.epoch;
    m->round = d->round; m->duel = d->terms.id; m->session = d->terms.session[d->context.self];
    memcpy(m->digest, d->terms.digest, 32);
}
static void countdown(CoopDuel *d, uint64_t now) {
    if (d->state == COOP_DUEL_PREVIEW && d->ready[0] && d->ready[1]) {
        d->state = COOP_DUEL_COUNTDOWN; d->countdown_ms = now;
    }
}

int coop_duel_ready(CoopDuel *d, const CoopDuelContext *c, const uint8_t digest[32], uint32_t epoch,
                    uint64_t now, CoopDuelControl *out) {
    int why = coop_duel_update(d, c, now);
    if (why) return why;
    if (!out || !digest || d->state != COOP_DUEL_PREVIEW) return COOP_DUEL_CONTEXT;
    if (epoch != d->terms.rules.epoch || memcmp(digest, d->terms.digest, 32)) return COOP_DUEL_STALE;
    why = context_reason(c, now, 1);
    if (why) { d->reason = why; return why; }
    if (d->ready[c->self]) return COOP_DUEL_REPLAY;
    d->ready[c->self] = 1; d->reason = COOP_DUEL_OK;
    message(d, COOP_DUEL_READY, out); out->capabilities = c->capabilities;
    out->save_generation = c->save_generation; out->area_generation = c->area_generation; out->kit_generation = c->kit_generation;
    countdown(d, now); return COOP_DUEL_OK;
}

int coop_duel_receive(CoopDuel *d, const CoopDuelContext *c, unsigned from, uint64_t session,
                      const CoopDuelControl *m, uint64_t now) {
    int why = coop_duel_update(d, c, now);
    if (why) return why;
    const unsigned peer = 1 - c->self;
    if (!control_valid(m) || from != d->terms.member[peer] || !session || session != d->terms.session[peer] ||
        m->session != session) return COOP_DUEL_PEER;
    if (m->duel != d->terms.id || m->round != d->round || m->epoch != d->terms.rules.epoch ||
        memcmp(m->digest, d->terms.digest, 32)) return COOP_DUEL_STALE;
    if (m->kind == COOP_DUEL_CANCEL) { coop_duel_interrupt(d, COOP_DUEL_PEER); return COOP_DUEL_OK; }
    if (m->kind == COOP_DUEL_READY) {
        if (d->state != COOP_DUEL_PREVIEW || d->ready[peer]) return COOP_DUEL_REPLAY;
        if (m->capabilities != COOP_DUEL_CAP_REQUIRED) { d->reason = COOP_DUEL_CAPABILITY; return d->reason; }
        d->ready[peer] = 1; countdown(d, now); return COOP_DUEL_OK;
    }
    if ((d->state != COOP_DUEL_ACTIVE && d->state != COOP_DUEL_SETTLING) || !d->ready[peer] ||
        d->closed[peer] || m->sequence <= d->peer_sequence) return COOP_DUEL_REPLAY;
    if (m->hp > d->hp[peer] || (m->closed && m->hp && d->hp[c->self])) return COOP_DUEL_PEER;
    d->peer_sequence = m->sequence; d->hp[peer] = m->hp; d->closed[peer] = m->closed;
    settle(d, now); finish(d); return COOP_DUEL_OK;
}

int coop_duel_status(const CoopDuel *d, CoopDuelControl *out) {
    if (!d || !out || d->context.self > 1 || (d->state != COOP_DUEL_ACTIVE && d->state != COOP_DUEL_SETTLING &&
        d->state != COOP_DUEL_ROUND_END && d->state != COOP_DUEL_FINISHED)) return 0;
    message(d, COOP_DUEL_STATUS, out); out->sequence = d->sequence;
    out->hp = d->hp[d->context.self]; out->closed = d->closed[d->context.self]; return 1;
}

int coop_duel_next_round(CoopDuel *d, const CoopDuelContext *c, uint64_t now) {
    int why = coop_duel_update(d, c, now);
    if (why) return why;
    if (d->state != COOP_DUEL_ROUND_END) return COOP_DUEL_CONTEXT;
    if (d->round == UINT32_MAX) { coop_duel_interrupt(d, COOP_DUEL_LIMIT); return d->reason; }
    d->round++; d->hp[0] = d->hp[1] = (uint16_t)(4 * d->terms.hearts);
    d->closed[0] = d->closed[1] = d->ready[0] = d->ready[1] = 0;
    d->attack_high = d->attack_bits = d->last_hit_ms = 0; d->remainder = 0;
    d->sequence = 1; d->peer_sequence = 0; d->begun_ms = now; d->state = COOP_DUEL_PREVIEW;
    return COOP_DUEL_OK;
}

/* Same bounded high-water/bitmap admission as oot_coop_hit_once: old IDs never revive after silence. */
static int attack_once(CoopDuel *d, uint64_t id) {
    if (!id) return 0;
    if (id > d->attack_high) {
        uint64_t step = id - d->attack_high;
        d->attack_bits = step >= 64 ? 1 : d->attack_bits << step | 1; d->attack_high = id; return 1;
    }
    uint64_t step = d->attack_high - id;
    if (step >= 64 || (d->attack_bits & (UINT64_C(1) << step))) return 0;
    d->attack_bits |= UINT64_C(1) << step; return 1;
}

int coop_duel_hit(CoopDuel *d, const CoopDuelContext *c, const CoopDuelAttack *a,
                  uint64_t now, CoopDuelObserve observe, void *local) {
    int why = coop_duel_update(d, c, now);
    if (why) return why;
    const unsigned self = c->self, peer = 1 - self;
    if (!a || a->from != d->terms.member[peer] || a->session != d->terms.session[peer]) return COOP_DUEL_PEER;
    if (a->duel != d->terms.id || a->epoch != d->terms.rules.epoch || a->round != d->round) return COOP_DUEL_STALE;
    if (!attack_once(d, a->attack)) return COOP_DUEL_REPLAY;
    /* Consume even refused attacks; a grace/miss/late attack cannot become a hit by another EVENT ID. */
    if ((d->state != COOP_DUEL_ACTIVE && d->state != COOP_DUEL_SETTLING) || d->closed[self] || !d->hp[self] ||
        a->observed_ms < d->round_ms || a->observed_ms > now || now - a->observed_ms > COOP_DUEL_HIT_AGE_MS ||
        (d->state == COOP_DUEL_SETTLING && a->observed_ms > d->settle_ms)) return COOP_DUEL_STALE;
    const uint64_t elapsed = now - d->budget_ms;
    d->attack_tokens = elapsed >= 2000 ? 32000 :
        d->attack_tokens + (uint32_t)elapsed * 16 > 32000 ? 32000 : d->attack_tokens + (uint32_t)elapsed * 16;
    d->budget_ms = now;
    if (d->attack_tokens < 1000) return COOP_DUEL_LIMIT;
    d->attack_tokens -= 1000;
    if (d->last_hit_ms && now - d->last_hit_ms < d->terms.grace_ms) return COOP_DUEL_GRACE;
    CoopDuelContact contact = {0};
    if (!observe || !observe(a, &contact, local)) return COOP_DUEL_CAPABILITY;
    if (contact.contact != 1 || contact.shielded || contact.invulnerable) return COOP_DUEL_MISS;
    if (!contact.native_units || contact.native_units > 160) return COOP_DUEL_TERMS;
    if (d->sequence == UINT32_MAX) { coop_duel_interrupt(d, COOP_DUEL_LIMIT); return d->reason; }
    unsigned scaled = (unsigned)contact.native_units * d->terms.damage_percent + d->remainder;
    unsigned amount = scaled / 100;
    d->remainder = (uint16_t)(scaled % 100); d->last_hit_ms = now;
    d->hp[self] = amount >= d->hp[self] ? 0 : (uint16_t)(d->hp[self] - amount);
    d->sequence++; settle(d, now); return COOP_DUEL_OK;
}

const char *coop_duel_reason(int reason, unsigned caps) {
    if (reason == COOP_DUEL_CAPABILITY) {
        if (!(caps & COOP_DUEL_CAP_BUILD)) return "This game build cannot run the duel adapter.";
        if (!(caps & COOP_DUEL_CAP_COPY)) return "Load a temporary save copy first.";
        if (!(caps & COOP_DUEL_CAP_KIT)) return "The sword and shield kit has not been checked in the game.";
        if (!(caps & COOP_DUEL_CAP_SPAWN)) return "Both grounded arena spawns must be surveyed and checked.";
        if (!(caps & COOP_DUEL_CAP_ENVIRONMENT)) return "The arena's enemies, interactions and pickups are not safely fenced.";
        if (!(caps & COOP_DUEL_CAP_CONTACT)) return "Native sword contact, range and cover are not proven. This build cannot start a fight.";
        if (!(caps & COOP_DUEL_CAP_DAMAGE)) return "Native sword damage is not proven. This build cannot start a fight.";
        return "Native shield handling is not proven. This build cannot start a fight.";
    }
    static const char *const text[] = {
        "Ready for these terms", "Duel diagnostics are off", "These duel rules are unsupported",
        "Accept the current room first", "A required duel capability is missing", "Play safely in the agreed temporary arena; close menus and chat",
        "The terms or round changed; read them again", "This action was already handled", "The receiver is in hit grace time",
        "The receiver observed no eligible hit", "The duel was interrupted by a missing response", "The duel was interrupted or the peer did not match",
        "The duel reached its safety limit"
    };
    return reason >= 0 && (size_t)reason < sizeof text / sizeof *text ? text[reason] : "The duel is unavailable";
}

void coop_duel_score_text(const CoopDuel *d, char *out, size_t cap) {
    if (!out || !cap) return;
    if (!d) { snprintf(out, cap, "No duel"); return; }
    static const char *const states[] = {"Off", "Read terms", "Countdown", "Duel", "Finishing round", "Round ended", "Match ended", "Interrupted"};
    const unsigned state = d->state <= COOP_DUEL_INTERRUPTED ? d->state : COOP_DUEL_INTERRUPTED;
    snprintf(out, cap, "%s | round %u, best of %u | %u-%u | hearts %u.%02u / %u.%02u",
        states[state], d->round, d->terms.rounds, d->score[0], d->score[1], d->hp[0] / 4, d->hp[0] % 4 * 25,
        d->hp[1] / 4, d->hp[1] % 4 * 25);
}
