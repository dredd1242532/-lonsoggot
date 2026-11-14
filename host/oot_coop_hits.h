/* (longshot) Combat events and the native life boundary. No game data is carried on the wire.
   docs/coop.md describes the observation-only stage and its consent/security boundary. */
#ifndef OOT_COOP_HITS_H
#define OOT_COOP_HITS_H
#include <stddef.h>
#include <stdint.h>
#include "a64.h"
#include "coop_proto.h"
#include "coop_state.h"

enum { OOT_HIT_VERSION = 1, OOT_HIT_BYTES = 112 };
enum { OOT_HIT_OBSERVATION = 1, OOT_HIT_CANDIDATE = 2, OOT_HIT_DECISION = 3 };
enum { OOT_HIT_UNKNOWN = 0, OOT_HIT_PLAYER = 1, OOT_HIT_WORLD = 2, OOT_HIT_LOCAL = 3 };
enum { OOT_RULE_HIT_EVENTS = 30 };
typedef struct {
    uint8_t phase, source_kind, target_kind, source_member, target_member;
    uint16_t attack_class;
    uint64_t origin_session, event_id, area, epoch, source_id, target_id;
    uint32_t before, after, amount_q8, tick;
    float impact[3], direction[3];
    uint16_t grace_ms, flags;
    uint32_t authority_term, source_generation, target_generation;
} OotCoopHit;
/* Version 1 currently accepts only committed native observations. Other phases fail closed until their
   adjudicators exist. Fields with no established source/position are zero, not guessed from a nearby actor. */
size_t oot_coop_hit_pack(uint8_t *out, size_t cap, const OotCoopHit *hit);
int oot_coop_hit_parse(const uint8_t *bytes, size_t n, OotCoopHit *hit);
typedef struct { uint64_t high, bits; } OotCoopHitSeen;
/* A session/encounter change resets this explicitly. Silence never resets a hit replay window. */
int oot_coop_hit_once(OotCoopHitSeen *seen, uint64_t event_id);
/* The signed list must contain the rule. Registry defaults are not consent by an older room maker. */
int oot_coop_hit_rule(const CoopRules *rules, int id, int *value);

/* Format-independent receive admission, without a lock/thread of its own. For game-affecting claims, decode in
   the owning layer and call on the receiver's game thread immediately before local adjudication. Observers can
   use their own lock instead. No HIT packet, socket or guest pointer is required.
   receipt comes from the session, not fields a peer claims about its identity; bytes is the actual decoded
   message's size. at_ms and context.now_ms are receiver-local times, never unconverted remote clock values.
   The caller still checks target/session, area/duel scope, attack freshness/geometry, shield/invulnerability and
   the final amount; 1 means eligible for that local decision, not proof of collision or permission to write a save.
   Damage units belong to the caller (native quarter-hearts or separate duel HP), with its own explicit ceiling. */
enum { OOT_HIT_CLAIM_CLASSES = 32, OOT_HIT_CLAIM_RATE_MAX = 32 };
typedef struct {
    uint64_t session, event_id;
    size_t bytes;
} OotCoopHitReceipt;
typedef struct {
    const CoopRules *rules;          /* current verified maker-signed rules */
    const uint64_t *sessions;        /* COOP_MAX_MEMBERS+1 current sessions, indexed by member */
    uint32_t accepted_members, allowed_classes, max_damage;
    size_t max_bytes;               /* 1..COOP_MAX_MESSAGE: PvP needs no large fragmented payload */
    uint64_t now_ms, safe_after_ms;  /* safe_after includes the receiver's post-event recovery */
    uint64_t peer_opt_in_session;   /* the peer's opt-in belongs to this session, not a reused member number */
    uint32_t max_age_ms, per_second, burst; /* age 1..10000 ms; rate and burst 1..OOT_HIT_CLAIM_RATE_MAX */
    int self, rule_id, room_accepted, playing, local_opt_in, peer_opt_in, in_event;
} OotCoopHitContext;
typedef struct {
    uint64_t session, budget_at;
    uint32_t tokens;                /* thousandths of an event */
    OotCoopHitSeen seen;
} OotCoopHitPeerGate;
typedef struct { OotCoopHitPeerGate peer[COOP_MAX_MEMBERS + 1]; } OotCoopHitGate;
/* Zero initially; caller serializes calls and resets only for a new room/duel incarnation, never for silence.
   An accepted sender's new event is consumed even when policy/rate/time rejects it: do not retry it after an
   event ends or consent changes. Invalid/unaccepted identities cannot poison another sender's replay window.
   A false result is a final application disposition, not a request for the transport to resend it. */
/* context is fresh receiver-owned state/limits, not values supplied by the peer. A gameplay layer also
   deduplicates its stable attack ID if it can submit that attack as more than one transport event. */
int oot_coop_hit_admit(OotCoopHitGate *gate, const OotCoopHitContext *context, const OotCoopHitReceipt *receipt,
                      int member, unsigned attack_class, uint32_t claimed_damage, uint64_t at_ms);

/* One invocation token covers both CAS paths and every retry of that native update. Policy callbacks run on
   guest worker threads: bounded bookkeeping only, no guest calls, no writes and no retained Ctx/pointers.
   The immutable callback object lives until process exit. A replica policy can retain before to prevent death
   before an authority decision. Deferred authority writes must remain tagged until their eventual commit. */
typedef struct {
    uint64_t invocation, component, owner, cell;
    uint32_t before, requested, after; /* committed: requested is the native target before Protect hearts/policy */
    int64_t delta;                    /* signed pre-clamp aggregate, not before-after or one attributed weapon */
    unsigned path;
} OotCoopLifeChange;
typedef struct {
    uint32_t (*before)(const OotCoopLifeChange *, void *user);
    void (*committed)(const OotCoopLifeChange *, void *user);
    void *user;
} OotCoopLifePolicy;
int hos_oot_hits_bind(const OotCoopLifePolicy *policy);
void hos_oot_hits_policy_on(int on);

/* Local trace v2 storage bounds, independent of setup detail, room locks and network queues:
   from empty, 1024 loss/request records and 16384 setup/renewal activation headers can be outstanding.
   This covers 16 loss requests/s and 256 activation requests/s for 60s without a drain. Every repeated
   setup counts, not just distinct IDs or player swings. Each present drains at most 256 per lane.
   Optional setup/non-loss/off detail keeps 64 per kind; not-kept counts are exact until uint64 exhaustion.
   Any complete-lane overflow/counter exhaustion stays incomplete for the process lifetime. Pending is
   reported separately: complete=yes alone does not mean all offered records have reached the log.
   Producers reserve with atomics (no observer mutex, logging, allocation or network); no guest pointer
   survives. Disabled hooks only test the cached switch; disabled drains do no clock/guest/lock work. */
enum { OOT_HIT_TRACE_LOSSES = 1024, OOT_HIT_TRACE_ACTIVATIONS = 16384, OOT_HIT_TRACE_DETAILS = 64 };

extern int hos_oot_hits_active;
uint64_t hos_oot_hits_begin(void);
uint32_t hos_oot_hits_before(Ctx *c, uint64_t token, unsigned path);
void hos_oot_hits_committed(Ctx *c, uint64_t token, unsigned path, uint32_t requested);
/* Called once by co-op start; rules are published alongside the existing accepted-room offer. */
void hos_oot_hits_start(void);
void hos_oot_hits_room(const CoopRules *rules, int accepted);
/* Present-thread drain and observed local context. Empty/unsafe context clears pending diagnostic sends.
   This is not the duel's ready barrier, scene generation or collision authority. */
void hos_oot_hits_frame(const char *area, int playing);
extern int hos_oot_attack_observe;
/* Read-only native completion hooks. No incoming packet calls these; no pointers survive the callback. */
void hos_oot_hits_attack(Ctx *c, uint64_t sensor, uint64_t record);
void hos_oot_hits_attack_id(Ctx *c, uint64_t record);
void hos_oot_hits_attack_off(Ctx *c, uint64_t sensor, unsigned layer);
#endif
