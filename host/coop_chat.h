/* In-memory chat/notices; no files, guest pointers or native writes. */
#pragma once
#include "coop.h"
#include "coop_state.h"
#include "../gpu/vk/vk_api.h"
enum { COOP_CHAT_HEAD = 15, COOP_CHAT_PACKET = COOP_CHAT_HEAD + HOS_COOP_CHAT_BYTES,
       COOP_CHAT_HISTORY = 16, COOP_CHAT_LIFE_MS = 8000, COOP_CHAT_RATE_MS = 1500 };
typedef struct {
    uint64_t session, received, next_ms, notice_ms;
    char name[32], key[24];
    int present, accepted, away, muted;
} CoopChatPeer;
typedef struct {
    MkwVkCoopFeedLine line;
    uint64_t at, session;
} CoopChatLine;
typedef struct {
    uint64_t room_session, sequence, send_ms;
    uint32_t epoch, members, generation;
    int enabled, permission, self, notices, count, next;
    CoopChatPeer peer[COOP_MAX_MEMBERS + 1];
    CoopChatLine history[COOP_CHAT_HISTORY];
} CoopChat;
void coop_chat_init(CoopChat *s);
void coop_chat_room(CoopChat *s, const CoopView *v, const CoopOffer *offer, uint32_t accepted, uint64_t now);
size_t coop_chat_prepare(CoopChat *s, const char *text, uint8_t out[COOP_CHAT_PACKET], uint64_t now);
void coop_chat_sent(CoopChat *s, const uint8_t *packet, size_t n, uint64_t now);
int coop_chat_receive(CoopChat *s, int from, uint64_t session, const uint8_t *packet, size_t n, uint64_t now);
int coop_chat_mute(CoopChat *s, unsigned member);
int coop_chat_notices(CoopChat *s, unsigned mode);
void coop_chat_shared(CoopChat *s, int member, uint64_t session, int category, uint32_t hash, uint32_t value, uint64_t now);
int coop_chat_snapshot(const CoopChat *s, MkwVkCoopFeed *out, uint64_t now, int safe);
const char *coop_chat_item_label(uint32_t hash);

/* Runtime adapters. The consent owner publishes under its lock; callbacks never acquire that lock. */
void hos_coop_chat_start(void);
void hos_coop_chat_room(const CoopView *v, const CoopOffer *offer, uint32_t accepted);
void hos_coop_chat_shared(int member, uint64_t session, int category, uint32_t hash, uint32_t value, void *user);
void hos_coop_chat_scene(int safe);
int hos_coop_feed(MkwVkCoopFeed *out);
int hos_coop_chat_command(int command, const char *text, uint32_t number);
