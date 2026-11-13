/* Ported from coop-warp's local request/arrival model. No WARP packet and no remote command path.
   The gameplay owner holds consent across native calls; area-only changes preserve this journey's ticket. */
#include "coop_net.h"
#include <windows.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "hos.h"
#include "coop.h"
#include "coop_players.h"
#include "oot_coop.h"
#include "oot_coop_warp.h"

enum { WARP_IDLE, WARP_QUEUED, WARP_LOAD, WARP_POSE, WARP_CHECK };
enum { WARP_ASK_MS = 10000, WARP_LOAD_MS = 90000, WARP_FRESH_MS = 3000, WARP_POSE_MS = 6000 };
enum { WARP_VALIDATE, WARP_TRAVEL, WARP_PLACE };
typedef struct { int operation, destination; float pos[3]; } WarpWrite;
typedef struct { Ctx *c; uint64_t actor, components; } WarpGame;
static struct {
    SRWLOCK lock;
    int active, phase, member, registered, started, child;
    uint64_t began, arrived;
    CoopGameplayTicket ticket;
    char key[24], map[80], note[160];
    float target[3];
} W = {.lock = SRWLOCK_INIT};

static void note(const char *text) {
    snprintf(W.note, sizeof W.note, "%s", text);
    hos_log("coop: travel: member %d: %s", W.member, W.note);
}
static void finish(const char *text) {
    W.phase = WARP_IDLE;
    __atomic_store_n(&W.active, 0, __ATOMIC_RELEASE);
    note(text);
}
static void cancel(const char *reason) {
    hos_log("coop: travel: cancellation reason: %s", reason);
    if (W.phase == WARP_CHECK) {
        finish("Arrival already submitted; no further movement will be made.");
        return;
    }
    finish(W.started ? "Travel cancelled after the map had started loading: you are at its entrance."
                     : "Travel cancelled, you stayed.");
}

/* Called under the central owner's lock, never the socket or menu thread for a native effect. */
static int warp_write(void *game, const void *payload) {
    const WarpWrite *write = payload;
    if (write->operation == WARP_VALIDATE) return 1;
    WarpGame *g = game;
    OotCoopWarpLocal local;
    if (!g || !hos_oot_coop_warp_local(g->c, g->actor, g->components, &local) ||
        local.event || local.loading || !local.safe || !hos_oot_coop_playing()) return 0;
    if (write->operation == WARP_TRAVEL)
        return !local.travelling && hos_oot_coop_warp_travel(g->c, g->actor, write->destination);
    if (write->operation != WARP_PLACE || strcmp(local.map, W.map) || local.child != W.child) return 0;
    return hos_oot_coop_warp_place(g->c, g->components, write->pos);
}
static int valid(void) {
    const WarpWrite check = {0};
    return hos_oot_coop_gameplay_dispatch(&W.ticket, &check, sizeof check, NULL);
}

static const char *member_now(int id, const char *key) {
    CoopView view;
    coop_view_get(&view);
    if (view.state != COOP_JOINED) return "Join a room first.";
    if (id < 1 || id > COOP_MAX_MEMBERS || id == view.self) return "Choose another player.";
    const CoopPeer *member = NULL;
    for (int i = 0; i < view.n && i < COOP_MAX_MEMBERS; i++)
        if (view.member[i].id == id) member = &view.member[i];
    if (!member) return "That player left the room.";
    if (member->away) return "That player is away.";
    CoopOffer offer;
    if (!hos_oot_coop_offer(&offer) || !offer.accepted) return "Accept this room and its rules first.";
    int on = 0;
    if (!coop_feature_present(&offer.rules, COOP_RULE_WARP, &on) || on != 1) return "Go to friend is off in this room.";
    if (!coop_member_accepted(id)) return "That player's identity is not accepted.";
    for (int i = 0; i < offer.n && i < COOP_MAX_MEMBERS; i++)
        if (offer.member[i].id == id && key && *key && !strcmp(offer.member[i].key, key)) return NULL;
    return "The player changed; choose the friend again.";
}
static const char *pose_now(int id, CoopPos *pose, uint64_t *age) {
    if (!hos_oot_coop_players_snapshot(id, pose, age)) return "No current shared position from that friend.";
    if (*age >= WARP_FRESH_MS) return "That friend's position is stale; wait for a fresh one.";
    if (!(pose->flags & COOP_POS_LOADED)) return "That friend is at the title screen.";
    if (!(pose->flags & COOP_POS_IN_WORLD)) return "That friend is paused, loading or in an event.";
    if (pose->flags & COOP_POS_SLOW) return "That friend is not sharing a precise position.";
    if (!pose->area[0]) return "That friend's place is unknown.";
    if (!isfinite(pose->yaw)) return "That friend's position is unsafe.";
    for (int i = 0; i < 3; i++)
        if (!isfinite(pose->pos[i]) || fabsf(pose->pos[i]) > COOP_PLAYERS_FAR - 2) return "That friend's position is unsafe.";
    return NULL;
}
static int destination_for(const CoopPos *pose, int child) {
    const int destination = hos_oot_coop_warp_destination(pose->area, child);
    const int other_age = ((pose->flags & COOP_POS_ADULT) != 0) == (child != 0);
    return destination >= 0 && (!other_age || hos_oot_coop_warp_destination(pose->area, !child) == destination)
        ? destination : -1;
}

int hos_oot_coop_warp_ask(int member, const char *key) {
    AcquireSRWLockExclusive(&W.lock);
    if (W.phase != WARP_IDLE) {
        note("Travel is already under way.");
        ReleaseSRWLockExclusive(&W.lock);
        return 0;
    }
    W.member = member;
    const char *why = member_now(member, key);
    CoopPos pose;
    uint64_t age = 0;
    if (!why) why = pose_now(member, &pose, &age);
    if (!why && !hos_oot_coop_playing()) why = "Load your save and finish any event before travelling.";
    if (!why && !W.registered) {
        const CoopGameplayWriter writer = {COOP_GAMEPLAY_VERSION, sizeof writer, COOP_GAMEPLAY_TRAVEL,
                                          COOP_GAMEPLAY_TEMPORARY, sizeof(WarpWrite), warp_write};
        W.registered = hos_oot_coop_gameplay_register(&writer) ? 1 : -1;
    }
    if (!why && (W.registered < 0 || !hos_oot_coop_gameplay_ticket(COOP_GAMEPLAY_TRAVEL, (unsigned)member, &W.ticket)))
        why = "Travel is not safe or permitted right now; try again.";
    if (why) {
        note(why);
        ReleaseSRWLockExclusive(&W.lock);
        return 0;
    }
    snprintf(W.key, sizeof W.key, "%s", key);
    snprintf(W.map, sizeof W.map, "%s", pose.area);
    W.began = coop_now_ms(), W.started = 0, W.phase = WARP_QUEUED;
    __atomic_store_n(&W.active, 1, __ATOMIC_RELEASE);
    note("Waiting for the game to travel...");
    ReleaseSRWLockExclusive(&W.lock);
    return 1;
}

static int current(void) {
    const uint64_t now = coop_now_ms();
    if (now < W.began || now - W.began >= (W.phase == WARP_QUEUED ? WARP_ASK_MS : WARP_LOAD_MS)) {
        cancel("travel deadline expired");
        return 0;
    }
    const int granted = valid();
    if (granted < 0) return 0;   /* owner busy: defer, do not report a revocation */
    const char *why = granted ? member_now(W.member, W.key) : "acceptance, rules, room or save changed";
    if (why) { cancel(why); return 0; }
    return 1;
}
void hos_oot_coop_warp_note(char *out, size_t cap) {
    AcquireSRWLockExclusive(&W.lock);
    if (W.phase != WARP_IDLE) current();
    if (cap) snprintf(out, cap, "%s", W.note);
    ReleaseSRWLockExclusive(&W.lock);
}

static void update(Ctx *c, uint64_t actor, uint64_t components) {
    if (!current()) return;
    const uint64_t now = coop_now_ms();
    OotCoopWarpLocal local;
    if (!hos_oot_coop_warp_local(c, actor, components, &local)) { cancel("local state unavailable"); return; }
    CoopPos pose;
    uint64_t age = 0;
    const char *why = pose_now(W.member, &pose, &age);
    if (W.phase == WARP_QUEUED) {
        if (!why && strcmp(pose.area, W.map)) { cancel("friend changed map before travel"); return; }
        if (!why && local.event) why = "An event is playing here; finish it before travelling.";
        if (!why && (local.loading || !local.safe || !hos_oot_coop_playing())) why = "You cannot be moved safely right now.";
        if (!why && local.travelling) why = "Travel is already under way.";
        const int destination = why ? -1 : destination_for(&pose, local.child);
        if (!why && destination < 0) why = ((pose.flags & COOP_POS_ADULT) != 0) == (local.child != 0)
            ? "Your ages differ and this place does not allow it." : "This place has no safe supported arrival.";
        if (why) { finish(why); return; }
        W.child = local.child;
        if (strcmp(local.map, W.map)) {
            const WarpWrite travel = {WARP_TRAVEL, destination, {0}};
            WarpGame game = {c, actor, components};
            const int result = hos_oot_coop_gameplay_dispatch(&W.ticket, &travel, sizeof travel, &game);
            if (result < 0) return;
            if (!result) { finish("The game refused the map change; you stayed."); return; }
            W.started = 1, W.phase = WARP_LOAD;
            note("Map change accepted; waiting for the entrance...");
        } else {
            W.phase = WARP_POSE, W.arrived = now;
            note("Same place: waiting for a fresh position...");
        }
        return;
    }
    if (W.phase == WARP_LOAD) {
        /* While our area reloads the presentation generation may have no pose yet. Never follow a second map. */
        if (!why && strcmp(pose.area, W.map)) { cancel("friend changed map during loading"); return; }
        if (strcmp(local.map, W.map) || local.event || local.loading || !hos_oot_coop_playing()) return;
        W.phase = WARP_POSE, W.arrived = now;
        note("At the entrance; waiting for a fresh position...");
        return;
    }
    if (strcmp(local.map, W.map) || local.child != W.child) { cancel("local map or age changed"); return; }
    if (local.event || local.loading || !local.safe || !hos_oot_coop_playing()) { cancel("local movement became unsafe"); return; }
    if (W.phase == WARP_POSE) {
        if (why) {
            if (now - W.arrived >= WARP_POSE_MS) cancel(why);
            return;
        }
        if (strcmp(pose.area, W.map)) { cancel("friend changed map before placement"); return; }
        if (destination_for(&pose, local.child) < 0) { cancel("friend's place became incompatible"); return; }
        if (age > now - W.arrived) {
            if (now - W.arrived >= WARP_POSE_MS) cancel("no fresh position after arrival");
            return;
        }
        W.target[0] = pose.pos[0] + 1.25f * cosf(pose.yaw);
        W.target[1] = pose.pos[1] + 0.1f;
        W.target[2] = pose.pos[2] - 1.25f * sinf(pose.yaw);
        WarpWrite place = {WARP_PLACE, 0, {0}};
        memcpy(place.pos, W.target, sizeof place.pos);
        WarpGame game = {c, actor, components};
        const int result = hos_oot_coop_gameplay_dispatch(&W.ticket, &place, sizeof place, &game);
        if (result < 0) return;
        if (!result) { cancel("native placement refused"); return; }
        W.phase = WARP_CHECK, W.arrived = now;
        hos_log("coop: travel: placement submitted once for member %d", W.member);
    } else if (W.phase == WARP_CHECK && now - W.arrived >= 200) {
        float distance = 0;
        for (int i = 0; i < 3; i++) distance += (local.pos[i] - W.target[i]) * (local.pos[i] - W.target[i]);
        finish(isfinite(distance) && distance <= 4 ? "Arrived near your friend." :
               "The game did not keep the arrival position. Use its exits or try again.");
    }
}
void hos_oot_coop_warp_update(Ctx *c, uint64_t actor, uint64_t components) {
    if (!__atomic_load_n(&W.active, __ATOMIC_ACQUIRE) || !TryAcquireSRWLockExclusive(&W.lock)) return;
    if (W.phase != WARP_IDLE) update(c, actor, components);
    ReleaseSRWLockExclusive(&W.lock);
}
void hos_oot_coop_warp_present(Ctx *c) {
    if (!__atomic_load_n(&W.active, __ATOMIC_ACQUIRE) || !TryAcquireSRWLockExclusive(&W.lock)) return;
    if (W.phase != WARP_IDLE && current() && W.phase == WARP_QUEUED && hos_oot_coop_warp_event(c))
        finish("An event is playing here; finish it before travelling.");
    ReleaseSRWLockExclusive(&W.lock);
}
