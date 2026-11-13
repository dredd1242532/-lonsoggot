// Co-op, the other players' places (coop_players.h): which position messages are taken, and who is shown where.
#include "coop_players.h"

#include <math.h>
#include <string.h>

static int member_ok(int id) { return id >= 1 && id <= COOP_MAX_MEMBERS; }

void coop_players_roster(CoopPlayers *p, int self, const int *ids, const uint64_t *sessions, int n) {
    uint8_t listed[COOP_MAX_MEMBERS + 1] = {0};
    uint64_t session[COOP_MAX_MEMBERS + 1] = {0};
    int others = 0;
    for (int i = 0; i < n; i++) {
        if (!member_ok(ids[i]) || listed[ids[i]]) continue;
        listed[ids[i]] = 1, session[ids[i]] = sessions[i];
        others += ids[i] != self;
    }
    for (int id = 1; id <= COOP_MAX_MEMBERS; id++)
        if (!listed[id] || !p->listed[id] || session[id] != p->session[id]) memset(&p->r[id], 0, sizeof p->r[id]);
    if (!n) p->taken = p->refused = 0, p->sent_any = 0;
    memcpy(p->listed, listed, sizeof listed), memcpy(p->session, session, sizeof session);
    p->self = n ? self : 0, p->others = others;
}

int coop_players_receive(CoopPlayers *p, int from, const uint8_t *bytes, size_t n, uint64_t now_ms) {
    CoopPos pos;
    int ok = member_ok(from) && from != p->self && p->listed[from] && coop_pos_parse(bytes, n, &pos);
    for (int k = 0; ok && k < 3; k++) ok = fabsf(pos.pos[k]) <= COOP_PLAYERS_FAR;
    if (!ok) {
        p->refused++;
        return 0;
    }
    CoopPlayerTrack *r = &p->r[from];
    const int fresh = coop_pos_fresh(&r->seen, p->session[from], pos.pseq, now_ms);
    if (!fresh) {   // sent again, or older than the last one taken
        p->refused++;
        return 0;
    }
    // another session's, or from before a silence; or the sender starts or stops showing the others (its slow places are
    // seconds apart): not places to move between now
    if (fresh == 2 || (r->n && ((r->pose[r->head].flags ^ pos.flags) & COOP_POS_SLOW))) r->n = 0;
    const int32_t off = (int32_t)((uint32_t)now_ms - pos.t_ms);
    r->offset = !r->n || off < r->offset ? off : r->offset == INT32_MAX ? INT32_MAX : r->offset + 1;
    r->head = (r->head + 1) % COOP_PLAYERS_HIST;
    r->pose[r->head] = pos, r->recv_ms[r->head] = now_ms;
    if (r->n < COOP_PLAYERS_HIST) r->n++;
    p->taken++;
    return 1;
}

size_t coop_players_send(CoopPlayers *p, uint8_t *out, size_t cap, CoopPos *me, uint64_t now_ms) {
    if (!p->others) return 0;
    if (p->sent_any) {
        const uint64_t since = now_ms - p->sent_ms;
        const int full = (me->flags & COOP_POS_IN_WORLD) && !(me->flags & COOP_POS_SLOW);
        const int changed = me->flags != p->sent_flags || strcmp(me->area, p->sent_area);
        if (since < COOP_PLAYERS_SEND_MS || (!full && !changed && since < COOP_PLAYERS_SLOW_MS)) return 0;
    }
    me->pseq = p->pseq + 1, me->t_ms = (uint32_t)now_ms;
    const size_t n = coop_pos_pack(out, cap, me);
    if (n) {
        p->pseq++, p->sent_ms = now_ms, p->sent_any = 1, p->sent_flags = me->flags;
        memcpy(p->sent_area, me->area, sizeof p->sent_area);
    }
    return n;
}

static float turn_between(float a, float b, float t) {   // from angle a toward b the short way
    float d = fmodf(b - a + 3.14159265f, 6.28318531f);
    if (d < 0) d += 6.28318531f;
    return a + (d - 3.14159265f) * t;
}

// The place of r shown at now: between the two positions around COOP_PLAYERS_BEHIND_MS ago by the sender's clock, the
// newest one when that is older, the oldest kept when all are newer.
static void place_at(const CoopPlayerTrack *r, uint32_t now, CoopPos *out) {
    const uint32_t t = now - (uint32_t)r->offset - COOP_PLAYERS_BEHIND_MS;
    const CoopPos *newest = &r->pose[r->head];
    *out = *newest;
    if ((int32_t)(t - newest->t_ms) >= 0) return;
    for (int k = 0; k + 1 < r->n; k++) {
        const CoopPos *b = &r->pose[(r->head - k + COOP_PLAYERS_HIST) % COOP_PLAYERS_HIST];
        const CoopPos *a = &r->pose[(r->head - k - 1 + COOP_PLAYERS_HIST) % COOP_PLAYERS_HIST];
        if ((int32_t)(t - a->t_ms) < 0) {
            *out = *a;
            continue;
        }
        const float f = b->t_ms == a->t_ms ? 1.0f : (float)(int32_t)(t - a->t_ms) / (float)(int32_t)(b->t_ms - a->t_ms);
        *out = f < 0.5f ? *a : *b;
        // a map change between the two, or one of them in no map (it carries no place): no place between them
        if (strcmp(a->area, b->area) || !(a->flags & b->flags & COOP_POS_IN_WORLD)) return;
        for (int i = 0; i < 3; i++) out->pos[i] = a->pos[i] + (b->pos[i] - a->pos[i]) * f;
        out->yaw = turn_between(a->yaw, b->yaw, f);
        for (int i = 0; i < out->nslots && i < a->nslots && i < b->nslots; i++)
            if (!strcmp(a->slot[i].node, b->slot[i].node) && b->slot[i].frame >= a->slot[i].frame)
                out->slot[i].frame = a->slot[i].frame + (b->slot[i].frame - a->slot[i].frame) * f;
        return;
    }
}

int coop_players_show(const CoopPlayers *p, int id, uint32_t accepted, const char *map, uint64_t now_ms, CoopPos *out) {
    if (!member_ok(id) || id == p->self || !p->listed[id]) return COOP_PLAYER_ABSENT;
    if (!(accepted >> id & 1)) return COOP_PLAYER_NOT_ACCEPTED;
    const CoopPlayerTrack *r = &p->r[id];
    if (!r->n || now_ms - r->recv_ms[r->head] >= COOP_PLAYERS_SILENT_MS) return COOP_PLAYER_NOT_HEARD;
    if (r->pose[r->head].flags & COOP_POS_SLOW) {   // its places are seconds apart and not meant to be shown
        if (out) *out = r->pose[r->head];
        return COOP_PLAYER_SLOW;
    }
    CoopPos at;
    place_at(r, (uint32_t)now_ms, &at);
    if (out) *out = at;
    if (!(at.flags & COOP_POS_IN_WORLD) || !at.area[0]) return COOP_PLAYER_NOT_IN_MAP;
    if (!map || strcmp(at.area, map)) return COOP_PLAYER_OTHER_MAP;
    return COOP_PLAYER_SHOWN;
}

const char *coop_players_why(int reason) {
    switch (reason) {
    case COOP_PLAYER_SHOWN: return "shown";
    case COOP_PLAYER_ABSENT: return "left the room";
    case COOP_PLAYER_NOT_ACCEPTED: return "not accepted or not identified";
    case COOP_PLAYER_SLOW: return "its game shows nobody";
    case COOP_PLAYER_NOT_HEARD: return "not heard";
    case COOP_PLAYER_NOT_IN_MAP: return "in no map";
    case COOP_PLAYER_OTHER_MAP: return "in another map";
    }
    return "?";
}

int coop_players_last(const CoopPlayers *p, int id, uint64_t now_ms, CoopPos *out, uint64_t *age_ms) {
    if (!member_ok(id) || id == p->self || !p->listed[id] || !p->r[id].n) return 0;
    const CoopPlayerTrack *r = &p->r[id];
    if (out) *out = r->pose[r->head];
    if (age_ms) *age_ms = now_ms - r->recv_ms[r->head];
    return 1;
}
