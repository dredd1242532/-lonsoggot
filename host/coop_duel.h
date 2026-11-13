/* (longshot) Game-free, receiver-owned duel state. No native health/save writer. */
#ifndef COOP_DUEL_H
#define COOP_DUEL_H
#include "coop_arena.h"
#include "coop_state.h"

enum { COOP_DUEL_VERSION = 1, COOP_DUEL_CONTROL_BYTES = 104, COOP_DUEL_COUNTDOWN_MS = 3000,
       COOP_DUEL_READY_MS = 30000, COOP_DUEL_HIT_AGE_MS = 250, COOP_DUEL_SETTLE_MS = 500,
       COOP_DUEL_CLOSE_MS = 2000 };
enum { COOP_DUEL_CAP_BUILD = 1, COOP_DUEL_CAP_COPY = 2, COOP_DUEL_CAP_KIT = 4,
       COOP_DUEL_CAP_SPAWN = 8, COOP_DUEL_CAP_ENVIRONMENT = 16, COOP_DUEL_CAP_CONTACT = 32,
       COOP_DUEL_CAP_DAMAGE = 64, COOP_DUEL_CAP_SHIELD = 128, COOP_DUEL_CAP_REQUIRED = 255 };
enum { COOP_DUEL_OFF, COOP_DUEL_PREVIEW, COOP_DUEL_COUNTDOWN, COOP_DUEL_ACTIVE,
       COOP_DUEL_SETTLING, COOP_DUEL_ROUND_END, COOP_DUEL_FINISHED, COOP_DUEL_INTERRUPTED };
enum { COOP_DUEL_OK, COOP_DUEL_DISABLED, COOP_DUEL_TERMS, COOP_DUEL_NOT_ACCEPTED,
       COOP_DUEL_CAPABILITY, COOP_DUEL_CONTEXT, COOP_DUEL_STALE, COOP_DUEL_REPLAY,
       COOP_DUEL_GRACE, COOP_DUEL_MISS, COOP_DUEL_TIMEOUT, COOP_DUEL_PEER, COOP_DUEL_LIMIT };
enum { COOP_DUEL_READY = 1, COOP_DUEL_STATUS = 2, COOP_DUEL_CANCEL = 3 };

typedef struct {
    CoopRules rules; /* verified signed charter, copied; every unknown feature is also hashed */
    CoopArena arena;
    uint64_t id, maker_key, session[2]; /* fresh agreed match ID; never reuse in these sessions */
    uint8_t member[2]; /* ascending; the two participants, not a remote-selected actor */
    uint8_t digest[32];
    uint16_t hearts, rounds, grace_ms, damage_percent, kit_bits;
} CoopDuelTerms;
/* Accepts only native-model sword/shield, no healing/hazards, and best of1/3/5. */
int coop_duel_terms(CoopDuelTerms *out, const CoopRules *rules, const CoopArena *arena,
                    uint64_t id, uint64_t maker_key, const uint8_t members[2], const uint64_t sessions[2]);
void coop_duel_terms_text(const CoopDuelTerms *terms, char *out, size_t cap);

typedef struct {
    int enabled, diagnostic, joined, accepted, temporary, menu_or_chat, playing;
    unsigned self, capabilities; /* self is participant index0/1; capability proofs are local, not packet data */
    uint32_t offer, epoch;
    uint64_t save_generation, area_generation, kit_generation, sampled_ms;
} CoopDuelContext;
typedef struct {
    uint8_t kind, closed;
    uint32_t epoch, round, capabilities, sequence;
    uint64_t duel, session, save_generation, area_generation, kit_generation;
    uint16_t hp;
    uint8_t digest[32];
} CoopDuelControl;
size_t coop_duel_control_pack(uint8_t *out, size_t cap, const CoopDuelControl *control);
int coop_duel_control_parse(const uint8_t *bytes, size_t n, CoopDuelControl *out);

typedef struct {
    CoopDuelTerms terms;
    CoopDuelContext context;
    uint64_t begun_ms, now_ms, countdown_ms, round_ms, settle_ms, last_hit_ms;
    uint64_t attack_high, attack_bits, budget_ms;
    uint32_t round, sequence, peer_sequence;
    uint16_t hp[2], score[2], remainder;
    uint32_t attack_tokens; /* thousandths; at most32 observations burst,16/s thereafter */
    uint8_t state, ready[2], closed[2], last_winner; /* last_winner:0 draw,1/2 participant */
    int reason;
} CoopDuel;
/* Serialized by its host owner. begin is explicit local setup; it never means Ready. */
int coop_duel_begin(CoopDuel *duel, const CoopDuelTerms *terms, const CoopDuelContext *context, uint64_t now_ms);
/* Every action rechecks current consent/context. Off returns before clock/observer/IO; transitions cancel once. */
int coop_duel_update(CoopDuel *duel, const CoopDuelContext *context, uint64_t now_ms);
void coop_duel_interrupt(CoopDuel *duel, int reason);
int coop_duel_ready(CoopDuel *duel, const CoopDuelContext *context, const uint8_t digest[32],
                    uint32_t epoch, uint64_t now_ms, CoopDuelControl *out);
int coop_duel_receive(CoopDuel *duel, const CoopDuelContext *context, unsigned from, uint64_t session,
                      const CoopDuelControl *message, uint64_t now_ms);
/* STATUS only describes this receiver's hearts; never changes its own hearts when received. */
int coop_duel_status(const CoopDuel *duel, CoopDuelControl *out);
int coop_duel_next_round(CoopDuel *duel, const CoopDuelContext *context, uint64_t now_ms);

typedef struct {
    uint64_t duel, session, attack, observed_ms; /* observed_ms is receiver-local, never a remote clock */
    uint32_t epoch, round;
    unsigned from;
} CoopDuelAttack;
typedef struct {
    int contact, shielded, invulnerable;
    uint16_t native_units; /* receiver's proven pre-clamp quarter hearts, NOT an attacker's claim */
} CoopDuelContact;
/* Trusted local observer checks activation, kit, native target, range/facing/height and cover. No network
   decoder may provide this callback or its result. Missing callback refuses, never geometry-by-guess. */
typedef int (*CoopDuelObserve)(const CoopDuelAttack *, CoopDuelContact *, void *local);
int coop_duel_hit(CoopDuel *duel, const CoopDuelContext *context, const CoopDuelAttack *attack,
                  uint64_t now_ms, CoopDuelObserve observe, void *local);
void coop_duel_score_text(const CoopDuel *duel, char *out, size_t cap);
const char *coop_duel_reason(int reason, unsigned capabilities);
#endif
