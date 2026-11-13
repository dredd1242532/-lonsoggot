#include "coop_save.h"
#include "coop_net.h"
#include "../third_party/monocypher/monocypher.h"
#include <stdlib.h>
#include <string.h>

enum { SAVE_GAME = 0xe0f3ccfa, SAVE_FORMAT = 1, CTRL_ACCEPT = 1, CTRL_WRITTEN, CTRL_VERIFIED, CTRL_CANCEL };
typedef struct {
    int state, member, queued, control, prepared;
    CoopSaveBinding binding;
    CoopSaveSource source;
    uint8_t offer[COOP_SAVE_OFFER_BYTES], hash[32];
    uint32_t index, bytes;
    uint64_t deadline, activity, expiry, control_until;
    char local[33];
} Transfer;
struct CoopSave {
    CoopSaveOps ops;
    void *user;
    Transfer in, out;
    uint8_t buf[COOP_SAVE_CHUNK_HEAD + COOP_SAVE_CHUNK], compare[COOP_SAVE_CHUNK];
    struct { uint8_t id[16]; uint64_t expiry; } retired[8];
    uint64_t checks_at, now;
    unsigned checks;
};
static uint32_t u32(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static uint64_t u64(const uint8_t *p) { return u32(p) | (uint64_t)u32(p + 4) << 32; }
static void put32(uint8_t *p, uint32_t v) { for (unsigned i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (i * 8)); }
static void put64(uint8_t *p, uint64_t v) { put32(p, (uint32_t)v); put32(p + 4, (uint32_t)(v >> 32)); }
static int zeros(const uint8_t *p, size_t n) { for (size_t i = 0; i < n; i++) if (p[i]) return 0; return 1; }
static void domain(uint8_t *p, unsigned type) { memset(p, 0, 24); memcpy(p, "longshot-coop/save/1", sizeof "longshot-coop/save/1" - 1); p[23] = (uint8_t)type; }
static int domain_is(const uint8_t *p, unsigned type) { uint8_t d[24]; domain(d, type); return !memcmp(p, d, 24); }
static uint32_t chunks(const Transfer *t) {
    return (t->source.size[0] + COOP_SAVE_CHUNK - 1) / COOP_SAVE_CHUNK + (t->source.size[1] + COOP_SAVE_CHUNK - 1) / COOP_SAVE_CHUNK;
}
static int sizes(const CoopSaveSource *src) {
    return src->generation && src->size[0] && src->size[1] && src->size[0] <= COOP_SAVE_MAX &&
           src->size[1] <= COOP_SAVE_MAX - src->size[0] &&
           (src->size[0] + 8191) / 8192 + (src->size[1] + 8191) / 8192 <= COOP_SAVE_CHUNKS;
}
static int geometry(const Transfer *t, uint32_t index, unsigned *file, uint32_t *offset, size_t *n) {
    if (index >= chunks(t)) return 0;
    uint32_t first = (t->source.size[0] + COOP_SAVE_CHUNK - 1) / COOP_SAVE_CHUNK;
    *file = index >= first;
    *offset = (index - (*file ? first : 0)) * COOP_SAVE_CHUNK;
    *n = t->source.size[*file] - *offset;
    if (*n > COOP_SAVE_CHUNK) *n = COOP_SAVE_CHUNK;
    return 1;
}
int coop_save_same_binding(const CoopSaveBinding *a, const CoopSaveBinding *b) {
    return a->own_session == b->own_session && a->peer_session == b->peer_session && a->epoch == b->epoch &&
           !memcmp(a->own, b->own, 32) && !memcmp(a->peer, b->peer, 32) && !memcmp(a->room, b->room, 16);
}
static int live(CoopSave *s, Transfer *t, uint64_t now, uint64_t utc) {
    CoopSaveBinding b;
    return now < t->deadline && utc < t->expiry && now >= t->activity && now - t->activity < COOP_SAVE_IDLE_MS &&
           s->ops.binding(s->user, t->member, &b) && coop_save_same_binding(&b, &t->binding);
}
static int terminal(int state) { return state == COOP_SAVE_IDLE || state == COOP_SAVE_VERIFIED || state == COOP_SAVE_CANCELLED; }
static void cancel_one(CoopSave *s, Transfer *t, int incoming) {
    if (terminal(t->state)) return;
    s->ops.cancel(s->user, t->hash);   /* local scope is the signed manifest digest, not an owner's chosen transfer ID */
    if (incoming && t->local[0]) s->ops.abandon(s->user, t->local);
    t->state = COOP_SAVE_CANCELLED;
    t->control = CTRL_CANCEL;
    t->control_until = s->now + 1000;
}
static int retire(CoopSave *s, const uint8_t id[16], uint64_t expiry, uint64_t utc) {
    int slot = -1;
    for (unsigned i = 0; i < 8; i++) {
        if (s->retired[i].expiry > utc && !memcmp(s->retired[i].id, id, 16)) return 0;
        if (s->retired[i].expiry <= utc && slot < 0) slot = (int)i;
    }
    if (slot < 0) return 0;
    memcpy(s->retired[slot].id, id, 16); s->retired[slot].expiry = expiry;
    return 1;
}
CoopSave *coop_save_new(const CoopSaveOps *ops, void *user) {
    if (!ops || !ops->binding || !ops->send || !ops->cancel || !ops->source_read || !ops->stage ||
        !ops->write || !ops->read || !ops->publish || !ops->abandon) return NULL;
    CoopSave *s = calloc(1, sizeof *s);
    if (s) s->ops = *ops, s->user = user;
    return s;
}
void coop_save_free(CoopSave *s) { if (s) { coop_save_cancel(s, 0); crypto_wipe(s, sizeof *s); free(s); } }

int coop_save_offer(CoopSave *s, int recipient, const CoopSaveSource *src, uint64_t now, uint64_t utc) {
    if (!s || !src || !sizes(src) || !terminal(s->out.state) || now > UINT64_MAX - COOP_SAVE_DEADLINE_MS || utc > UINT64_MAX - COOP_SAVE_DEADLINE_MS) return 0;
    CoopSaveBinding b;
    if (!s->ops.binding(s->user, recipient, &b) || !b.epoch || !b.own_session || !b.peer_session) return 0;
    Transfer t = {0};
    t.binding = b; t.source = *src; t.member = recipient;
    t.state = COOP_SAVE_OFFERED; t.activity = now; t.deadline = now + COOP_SAVE_DEADLINE_MS; t.expiry = utc + COOP_SAVE_DEADLINE_MS;
    uint8_t *p = t.offer;
    domain(p, 1);
    if (!coop_random(p + 24, 16)) return 0;
    memcpy(p + 40, b.own, 32); memcpy(p + 72, b.peer, 32);
    put64(p + 104, b.own_session); put64(p + 112, b.peer_session); memcpy(p + 120, b.room, 16);
    put32(p + 136, b.epoch); put64(p + 140, src->generation); put32(p + 148, SAVE_GAME); put32(p + 152, SAVE_FORMAT);
    put32(p + 156, src->size[0]); put32(p + 160, src->size[1]); memcpy(p + 164, src->hash, 64); memcpy(p + 228, src->package, 32);
    put64(p + 260, t.expiry); put32(p + 268, COOP_SAVE_CHUNK);
    /* policy 0: no return. A sender cannot opt a borrowed save into progress sync. */
    coop_sign(p + 288, b.sk, p, 288);
    coop_blake2b(t.hash, 32, NULL, 0, p, sizeof t.offer);
    s->out = t;
    return 1;
}

static int take_offer(CoopSave *s, int from, uint64_t session, const uint8_t *p, size_t n, uint64_t now, uint64_t utc) {
    if (n != COOP_SAVE_OFFER_BYTES || !domain_is(p, 1) || !terminal(s->in.state) || !zeros(p + 272, 16) || zeros(p + 24, 16)) return 0;
    CoopSaveBinding b;
    if (!s->ops.binding(s->user, from, &b) || session != b.peer_session || memcmp(p + 40, b.peer, 32) ||
        memcmp(p + 72, b.own, 32) || u64(p + 104) != b.peer_session || u64(p + 112) != b.own_session ||
        memcmp(p + 120, b.room, 16) || u32(p + 136) != b.epoch || !b.epoch ||
        u32(p + 148) != SAVE_GAME || u32(p + 152) != SAVE_FORMAT || u32(p + 268) != COOP_SAVE_CHUNK) return 0;
    Transfer t = {0};
    t.expiry = u64(p + 260);
    t.source.generation = u64(p + 140); t.source.size[0] = u32(p + 156); t.source.size[1] = u32(p + 160);
    if (!sizes(&t.source) || utc >= t.expiry || t.expiry - utc > COOP_SAVE_DEADLINE_MS ||
        !coop_sign_check(p + 288, b.peer, p, 288) || !retire(s, p + 24, t.expiry, utc)) return 0;
    t.binding = b; t.member = from; t.activity = now; t.deadline = now + (t.expiry - utc); t.state = COOP_SAVE_OFFERED;
    memcpy(t.source.hash, p + 164, 64); memcpy(t.source.package, p + 228, 32); memcpy(t.offer, p, n);
    coop_blake2b(t.hash, 32, NULL, 0, p, n);
    s->in = t;
    return 1;
}
int coop_save_accept(CoopSave *s, const uint8_t hash[32], uint64_t now, uint64_t utc) {
    s->now = now;
    Transfer *t = &s->in;
    if (!hash || t->state != COOP_SAVE_OFFERED || memcmp(hash, t->hash, 32) || !live(s, t, now, utc)) return 0;
    if (!s->ops.stage(s->user, t->local)) { cancel_one(s, t, 1); return 0; }
    t->state = COOP_SAVE_TRANSFERRING; t->control = CTRL_ACCEPT; t->activity = now;
    return 1;
}
void coop_save_cancel(CoopSave *s, uint64_t now) {
    s->now = now;
    cancel_one(s, &s->in, 1); cancel_one(s, &s->out, 0);
}
static void control(CoopSave *s, Transfer *t, uint64_t now) {
    if (!t->control) return;
    uint64_t deadline = t->control == CTRL_CANCEL ? t->control_until : t->deadline;
    if (now >= deadline) { t->control = 0; return; }
    uint8_t p[COOP_SAVE_CONTROL_BYTES] = {0};
    domain(p, 2); p[24] = (uint8_t)t->control;
    memcpy(p + 32, t->offer + 24, 16); memcpy(p + 48, t->hash, 32); put32(p + 80, t->index);
    coop_sign(p + 96, t->binding.sk, p, 96);
    if (s->ops.send(s->user, t->member, COOP_K_SAVE_CONTROL, t->hash, deadline, p, sizeof p) == 1) t->control = 0;
}
static void take_control(CoopSave *s, int from, uint64_t session, const uint8_t *p, size_t n, uint64_t now, uint64_t utc) {
    if (n != COOP_SAVE_CONTROL_BYTES || !domain_is(p, 2) || !zeros(p + 25, 7) || !zeros(p + 84, 12)) return;
    Transfer *t = !memcmp(p + 32, s->out.offer + 24, 16) && !memcmp(p + 48, s->out.hash, 32) ? &s->out : &s->in;
    if (terminal(t->state) || t->member != from || session != t->binding.peer_session || memcmp(p + 48, t->hash, 32) ||
        !live(s, t, now, utc) || !coop_sign_check(p + 96, t->binding.peer, p, 96)) return;
    if (p[24] == CTRL_CANCEL) { cancel_one(s, t, t == &s->in); t->control = 0; return; }
    if (t != &s->out) return;
    uint32_t next = u32(p + 80);
    if (p[24] == CTRL_ACCEPT && t->state == COOP_SAVE_OFFERED && !next) {
        t->state = COOP_SAVE_TRANSFERRING; t->queued = 0; t->activity = now;
    } else if ((p[24] == CTRL_WRITTEN || p[24] == CTRL_VERIFIED) && t->state == COOP_SAVE_TRANSFERRING && t->queued && next == t->index + 1) {
        unsigned file; uint32_t off; size_t count;
        if (!geometry(t, t->index, &file, &off, &count)) return;
        if ((p[24] == CTRL_VERIFIED) != (next == chunks(t))) return;
        t->bytes += (uint32_t)count; t->index = next; t->queued = t->prepared = 0; t->activity = now;
        if (p[24] == CTRL_VERIFIED) t->state = COOP_SAVE_VERIFIED;
    }
}
static int verify_disk(CoopSave *s, Transfer *t) {
    uint8_t lengths[8], result[32];
    put32(lengths, t->source.size[0]); put32(lengths + 4, t->source.size[1]);
    crypto_blake2b_ctx all;
    crypto_blake2b_init(&all, 32); crypto_blake2b_update(&all, lengths, 8);
    for (unsigned file = 0; file < 2; file++) {
        crypto_blake2b_ctx one;
        crypto_blake2b_init(&one, 32);
        for (uint32_t off = 0; off < t->source.size[file];) {
            size_t n = t->source.size[file] - off; if (n > COOP_SAVE_CHUNK) n = COOP_SAVE_CHUNK;
            if (!s->ops.read(s->user, t->local, file, off, s->compare, n)) return 0;
            crypto_blake2b_update(&one, s->compare, n); crypto_blake2b_update(&all, s->compare, n); off += (uint32_t)n;
        }
        crypto_blake2b_final(&one, result);
        if (memcmp(result, t->source.hash[file], 32)) return 0;
    }
    crypto_blake2b_final(&all, result);
    return !memcmp(result, t->source.package, 32);
}
static void take_chunk(CoopSave *s, int from, uint64_t session, const uint8_t *p, size_t n, uint64_t now, uint64_t utc) {
    Transfer *t = &s->in;
    if (n < COOP_SAVE_CHUNK_HEAD || t->state != COOP_SAVE_TRANSFERRING || from != t->member || session != t->binding.peer_session || memcmp(p + 16, t->offer + 24, 16)) return;
    unsigned file; uint32_t off, index = u32(p + 4); size_t count;
    if (!live(s, t, now, utc) || p[0] != 1 || !zeros(p + 12, 4) || memcmp(p + 32, t->hash, 32) ||
        !geometry(t, index, &file, &off, &count) || p[1] != file || u32(p + 8) != off ||
        (size_t)(p[2] | p[3] << 8) != count || n != COOP_SAVE_CHUNK_HEAD + count || index > t->index) { cancel_one(s, t, 1); return; }
    if (index < t->index) {
        if (!s->ops.read(s->user, t->local, file, off, s->compare, count) || memcmp(s->compare, p + 64, count)) cancel_one(s, t, 1);
        return; /* duplicates cannot extend the inactivity deadline */
    }
    if (!s->ops.write(s->user, t->local, file, off, p + 64, count)) { cancel_one(s, t, 1); return; }
    t->index++; t->bytes += (uint32_t)count; t->activity = now;
    if (t->index == chunks(t)) {
        if (!verify_disk(s, t) || !live(s, t, now, utc) ||
            !s->ops.publish(s->user, t->member, t->local, t->offer, &t->binding, t->deadline, t->expiry)) {
            cancel_one(s, t, 1); return;
        }
        t->state = COOP_SAVE_VERIFIED; t->control = CTRL_VERIFIED;
    } else t->control = CTRL_WRITTEN;
}
void coop_save_receive(CoopSave *s, int from, uint64_t session, uint8_t kind, const uint8_t *p, size_t n, uint64_t now, uint64_t utc) {
    if (!s || !p || from < 1 || from > COOP_MAX_MEMBERS) return;
    s->now = now;
    if (kind == COOP_K_SAVE_CHUNK) { take_chunk(s, from, session, p, n, now, utc); return; }
    if (now - s->checks_at >= 1000) s->checks_at = now, s->checks = 0;
    if (s->checks++ >= 8) return;
    if (kind == COOP_K_SAVE_OFFER) take_offer(s, from, session, p, n, now, utc);
    else if (kind == COOP_K_SAVE_CONTROL) take_control(s, from, session, p, n, now, utc);
}
void coop_save_tick(CoopSave *s, uint64_t now, uint64_t utc) {
    s->now = now;
    Transfer *in = &s->in, *out = &s->out;
    if (!terminal(in->state) && !live(s, in, now, utc)) cancel_one(s, in, 1);
    if (!terminal(out->state) && !live(s, out, now, utc)) cancel_one(s, out, 0);
    control(s, in, now); control(s, out, now);
    if (out->state == COOP_SAVE_OFFERED && !out->queued) {
        if (s->ops.send(s->user, out->member, COOP_K_SAVE_OFFER, out->hash, out->deadline, out->offer, sizeof out->offer) == 1) out->queued = 1;
    } else if (out->state == COOP_SAVE_TRANSFERRING && !out->queued) {
        unsigned file; uint32_t off; size_t n;
        if (!geometry(out, out->index, &file, &off, &n)) return;
        uint8_t *p = s->buf;
        if (!out->prepared) {
            memset(p, 0, 64); p[0] = 1; p[1] = (uint8_t)file; p[2] = (uint8_t)n; p[3] = (uint8_t)(n >> 8);
            put32(p + 4, out->index); put32(p + 8, off); memcpy(p + 16, out->offer + 24, 16); memcpy(p + 32, out->hash, 32);
            if (!s->ops.source_read(s->user, out->source.local_id, file, off, p + 64, n)) { cancel_one(s, out, 0); return; }
            out->prepared = 1;
        }
        if (s->ops.send(s->user, out->member, COOP_K_SAVE_CHUNK, out->hash, out->deadline, p, n + 64) == 1) out->queued = 1;
    }
}
void coop_save_view(const CoopSave *s, CoopSaveView *v) {
    memset(v, 0, sizeof *v);
    if (!s) return;
    v->incoming = s->in.state; v->outgoing = s->out.state; v->owner = s->in.member; v->recipient = s->out.member;
    v->received_bytes = s->in.bytes; v->sent_bytes = s->out.bytes;
    v->total_bytes = s->in.source.size[0] + s->in.source.size[1]; memcpy(v->manifest_hash, s->in.hash, 32);
    if (s->in.state == COOP_SAVE_VERIFIED) memcpy(v->verified_id, s->in.local, 33);
}
