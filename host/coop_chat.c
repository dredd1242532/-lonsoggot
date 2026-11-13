/* Chat and host-authored notices share one bounded, volatile feed. */
#include "coop_chat.h"
#include "coop_chat_text.h"
#include "../gpu/vk/oot_cheat_items.h"
#include <stdio.h>
#include <string.h>

#define CHAT_ITEM(label, name, kind) {label, name, 0},
#define CHAT_SECTION(name)
static struct { const char *label, *name; uint32_t hash; } items[] = {OOT_CHEAT_ITEMS(CHAT_SECTION, CHAT_ITEM)};
/* Ported from coop-players' first-get label adapter; same catalogue, no game asset or packet-selected name. */
static uint32_t first_get_hash(const char *name) {
    char text[128];
    const int n = snprintf(text, sizeof text, "IsGet.%s", name);
    if (n < 0 || (size_t)n >= sizeof text) return 0;
    const uint8_t *p = (const uint8_t *)text;
    uint32_t h = 0, k = 0;
    int i = 0;
    for (; i + 4 <= n; i += 4) {
        k = (uint32_t)p[i] | (uint32_t)p[i + 1] << 8 | (uint32_t)p[i + 2] << 16 | (uint32_t)p[i + 3] << 24;
        k *= 0xcc9e2d51u; k = k << 15 | k >> 17; k *= 0x1b873593u;
        h ^= k; h = h << 13 | h >> 19; h = h * 5 + 0xe6546b64u;
    }
    k = 0;
    for (int j = 0; i + j < n; j++) k |= (uint32_t)p[i + j] << (8 * j);
    if (i < n) { k *= 0xcc9e2d51u; k = k << 15 | k >> 17; k *= 0x1b873593u; h ^= k; }
    h ^= (uint32_t)n;
    h ^= h >> 16; h *= 0x85ebca6bu; h ^= h >> 13; h *= 0xc2b2ae35u; h ^= h >> 16;
    return h;
}
const char *coop_chat_item_label(uint32_t hash) {
    for (size_t i = 0; i < sizeof items / sizeof *items; i++) if (items[i].hash == hash) return items[i].label;
    return NULL;
}
void coop_chat_init(CoopChat *s) {
    memset(s, 0, sizeof *s);
    s->notices = 2;
    for (size_t i = 0; i < sizeof items / sizeof *items; i++) items[i].hash = first_get_hash(items[i].name);
}
static void history_clear(CoopChat *s) {
    memset(s->history, 0, sizeof s->history);
    s->next = s->count = 0;
    if (!++s->generation) ++s->generation;
}
static void add_line(CoopChat *s, int member, int kind, const char *text, uint64_t now) {
    CoopChatPeer *p = &s->peer[member];
    if (p->muted || (kind != HOS_COOP_FEED_CHAT && (!s->notices || (kind == HOS_COOP_FEED_ITEM && s->notices != 2)))) return;
    if (kind != HOS_COOP_FEED_CHAT) {
        if (now < p->notice_ms) return;
        p->notice_ms = now + 1000;
    }
    CoopChatLine *l = &s->history[s->next];
    memset(l, 0, sizeof *l);
    l->at = now, l->session = p->session;
    l->line.kind = (uint8_t)kind, l->line.member = (uint8_t)member;
    memcpy(l->line.name, p->name, sizeof l->line.name);
    snprintf(l->line.text, sizeof l->line.text, "%s", text ? text : "");
    s->next = (s->next + 1) % COOP_CHAT_HISTORY;
    if (s->count < COOP_CHAT_HISTORY) s->count++;
}
void coop_chat_room(CoopChat *s, const CoopView *v, const CoopOffer *offer, uint32_t accepted, uint64_t now) {
    if (!v || (v->state != COOP_JOINED && v->state != COOP_RECONNECTING)) {
        if (s->room_session || s->enabled) {
            const int mode = s->notices;
            const uint32_t generation = s->generation;
            memset(s, 0, sizeof *s);
            s->notices = mode, s->generation = generation;
            history_clear(s);
        }
        return;
    }
    if (s->room_session != v->session) {
        const int mode = s->notices;
        const uint32_t generation = s->generation;
        memset(s, 0, sizeof *s);
        s->notices = mode, s->generation = generation;
        s->room_session = v->session;
        history_clear(s);
    }
    int permission = 0;
    const int enabled = v->state == COOP_JOINED && offer && offer->accepted && !offer->disagree && !offer->unidentified;
    const uint32_t epoch = offer ? offer->rules.epoch : 0;
    if (enabled) coop_feature_present(&offer->rules, COOP_RULE_SOCIAL, &permission);
    permission = enabled && permission == COOP_SOCIAL_CHAT;
    if (s->epoch != epoch || s->enabled != enabled || s->permission != permission) history_clear(s);
    s->epoch = epoch, s->enabled = enabled, s->permission = permission, s->self = v->self;
    s->members = enabled ? accepted & ~(1u << v->self) : 0;
    for (int id = 1; id <= COOP_MAX_MEMBERS; id++) {
        CoopChatPeer *p = &s->peer[id];
        const CoopPeer *peer = NULL;
        const CoopOfferMember *identity = NULL;
        for (int i = 0; i < v->n && i < COOP_MAX_MEMBERS; i++) if (v->member[i].id == id) peer = &v->member[i];
        for (int i = 0; offer && i < offer->n && i < COOP_MAX_MEMBERS; i++) if (offer->member[i].id == id) identity = &offer->member[i];
        const int allow = enabled && peer && (s->members & (1u << id)) && identity && identity->heard &&
                          !identity->removed && !identity->accepted_as[0] && identity->key[0];
        if (!peer) {
            if (enabled && p->accepted && p->present) add_line(s, id, HOS_COOP_FEED_LEFT, NULL, now);
            p->present = p->accepted = 0;
            continue;
        }
        const int same = p->session == peer->session && (!identity || !strcmp(p->key, identity->key));
        const int was = same && p->accepted, away = p->away;
        if (!same) {
            const int muted = identity && p->key[0] && !strcmp(p->key, identity->key) && p->muted;
            memset(p, 0, sizeof *p);
            p->muted = muted;
        }
        p->session = peer->session, p->present = 1, p->accepted = allow, p->away = peer->away;
        if (identity && allow) {
            coop_name_clean(p->name, sizeof p->name, (const uint8_t *)identity->name, strlen(identity->name));
            snprintf(p->key, sizeof p->key, "%s", identity->key);
        }
        if (id == v->self) {
            coop_name_clean(p->name, sizeof p->name, (const uint8_t *)v->name, strlen(v->name));
            continue;
        }
        if (allow && !was) add_line(s, id, HOS_COOP_FEED_JOIN, NULL, now);
        else if (allow && away != p->away) add_line(s, id, p->away ? HOS_COOP_FEED_LOST : HOS_COOP_FEED_BACK, NULL, now);
    }
}
static uint64_t read_le(const uint8_t *p, unsigned n) {
    uint64_t value = 0;
    for (unsigned i = 0; i < n; i++) value |= (uint64_t)p[i] << (i * 8);
    return value;
}
static void write_le(uint8_t *p, uint64_t value, unsigned n) {
    for (unsigned i = 0; i < n; i++) p[i] = (uint8_t)(value >> (i * 8));
}
static size_t decode(const uint8_t *b, size_t n, char text[HOS_COOP_CHAT_BYTES + 1]) {
    if (!b || n <= COOP_CHAT_HEAD || n > COOP_CHAT_PACKET || b[0] != 1 || read_le(b + 13, 2) != n - COOP_CHAT_HEAD) return 0;
    return coop_text_clean(text, HOS_COOP_CHAT_BYTES + 1, b + COOP_CHAT_HEAD, n - COOP_CHAT_HEAD);
}
size_t coop_chat_prepare(CoopChat *s, const char *text, uint8_t out[COOP_CHAT_PACKET], uint64_t now) {
    if (!s->enabled || !s->permission || !s->members || !text || now < s->send_ms || s->sequence == UINT64_MAX) return 0;
    size_t n = 0;
    while (n <= HOS_COOP_CHAT_BYTES && text[n]) n++;
    char clean[HOS_COOP_CHAT_BYTES + 1];
    n = coop_text_clean(clean, sizeof clean, (const uint8_t *)text, n);
    if (!n) return 0;
    out[0] = 1;
    write_le(out + 1, s->epoch, 4);
    write_le(out + 5, ++s->sequence, 8);
    write_le(out + 13, n, 2);
    memcpy(out + COOP_CHAT_HEAD, clean, n);
    return COOP_CHAT_HEAD + n;
}
void coop_chat_sent(CoopChat *s, const uint8_t *packet, size_t n, uint64_t now) {
    char text[HOS_COOP_CHAT_BYTES + 1];
    if (!decode(packet, n, text)) return;
    s->send_ms = now + COOP_CHAT_RATE_MS;
    add_line(s, s->self, HOS_COOP_FEED_CHAT, text, now);
}
int coop_chat_receive(CoopChat *s, int from, uint64_t session, const uint8_t *b, size_t n, uint64_t now) {
    if (from < 1 || from > COOP_MAX_MEMBERS || from == s->self || !s->enabled || !s->permission) return 0;
    CoopChatPeer *p = &s->peer[from];
    if (!p->accepted || !(s->members & (1u << from)) || p->session != session) return 0;
    if (!b || n <= COOP_CHAT_HEAD || n > COOP_CHAT_PACKET || b[0] != 1 || read_le(b + 1, 4) != s->epoch) return 0;
    const uint64_t sequence = read_le(b + 5, 8);
    if (!sequence || sequence <= p->received) return 0;
    p->received = sequence; /* muted, malformed and rate-refused accepted messages stay consumed */
    if (p->muted || now < p->next_ms) return 0;
    p->next_ms = now + COOP_CHAT_RATE_MS;
    char text[HOS_COOP_CHAT_BYTES + 1];
    if (!decode(b, n, text)) return 0;
    add_line(s, from, HOS_COOP_FEED_CHAT, text, now);
    return 1;
}
int coop_chat_mute(CoopChat *s, unsigned member) {
    if (!s->enabled || member < 1 || member > COOP_MAX_MEMBERS || !(s->members & (1u << member))) return 0;
    s->peer[member].muted ^= 1;
    /* Clear old text on both transitions; unmuting does not replay hidden messages. */
    for (unsigned i = 0; i < COOP_CHAT_HISTORY; i++) if (s->history[i].line.member == member) s->history[i].session = 0;
    return 1;
}
int coop_chat_notices(CoopChat *s, unsigned mode) {
    if (mode > 2) return 0;
    s->notices = (int)mode;
    for (unsigned i = 0; i < COOP_CHAT_HISTORY; i++) if (s->history[i].line.kind != HOS_COOP_FEED_CHAT) s->history[i].session = 0;
    return 1;
}
void coop_chat_shared(CoopChat *s, int member, uint64_t session, int category, uint32_t hash, uint32_t value, uint64_t now) {
    if (!s->enabled || s->notices != 2 || member < 1 || member > COOP_MAX_MEMBERS ||
        !(s->members & (1u << member)) || !s->peer[member].accepted || s->peer[member].session != session ||
        (category != COOP_CAT_ITEMS && category != COOP_CAT_SONGS) || value != 1) return;
    const char *label = coop_chat_item_label(hash);
    if (!label && category != COOP_CAT_SONGS) return; /* capacity/stock changes are not first-get notices */
    add_line(s, member, HOS_COOP_FEED_ITEM, label ? label : "a song", now);
}
int coop_chat_snapshot(const CoopChat *s, MkwVkCoopFeed *out, uint64_t now, int safe) {
    memset(out, 0, sizeof *out);
    out->generation = s->generation;
    if (!s->enabled) return 0;
    out->members = s->members, out->can_chat = s->permission && s->members && safe, out->visible = safe;
    for (int i = 1; i <= COOP_MAX_MEMBERS; i++) if (s->peer[i].muted) out->muted |= 1u << i;
    if (!safe) return 1;
    for (int k = 0; k < s->count && out->count < HOS_COOP_FEED_LINES; k++) {
        const CoopChatLine *l = &s->history[(s->next + COOP_CHAT_HISTORY - 1 - k) % COOP_CHAT_HISTORY];
        const unsigned id = l->line.member;
        if (!l->session || id < 1 || id > COOP_MAX_MEMBERS || now < l->at || now - l->at >= COOP_CHAT_LIFE_MS) continue;
        const CoopChatPeer *p = &s->peer[id];
        if (p->muted || p->session != l->session ||
            (id != (unsigned)s->self && !p->accepted && l->line.kind != HOS_COOP_FEED_LEFT)) continue;
        out->line[out->count++] = l->line;
    }
    return 1;
}
