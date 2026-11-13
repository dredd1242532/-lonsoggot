/* Authored packets, identities and progress flags; no game, files or socket. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "coop_chat.c"
#include "coop_net.h"

static CoopView view;
static CoopOffer offer;
static uint64_t clock_ms = 10000;
static uint8_t packet[COOP_CHAT_PACKET];
static size_t packet_n;
static CoopEventFn event_fn;
static int accepted = 1, queued = 1, sends;
uint64_t coop_now_ns(void) { return clock_ms * 1000000; }
void coop_once(CoopOnce *o, void (*fn)(void *), void *arg) { static int done; (void)o; if (!done) { done = 1; fn(arg); } }
void coop_lock_init(CoopLock *l) { (void)l; }
void coop_lock(CoopLock *l) { (void)l; }
void coop_unlock(CoopLock *l) { (void)l; }
int hos_ini_option(const char *key, int otherwise) { assert(!strcmp(key, "coop_notices")); return otherwise; }
int coop_on_event(uint8_t kind, CoopEventFn fn, void *user) { assert(kind == COOP_K_CHAT && !user); event_fn = fn; return 1; }
int coop_member_accepted(int member) { return accepted && member == 2; }
int coop_send_event(int member, uint8_t kind, const void *bytes, size_t n) {
    assert(member == 0 && kind == COOP_K_CHAT && n <= sizeof packet);
    if (!queued) return COOP_EVENT_FULL;
    memcpy(packet, bytes, n); packet_n = n; sends++;
    return COOP_EVENT_QUEUED;
}
static void setup(CoopChat *s) {
    coop_chat_init(s);
    memset(&view, 0, sizeof view); memset(&offer, 0, sizeof offer);
    view.state = COOP_JOINED; view.self = 1; view.session = 101; view.n = 2;
    strcpy(view.name, "Alice");
    view.member[0].id = 1; view.member[0].session = 101;
    view.member[1].id = 2; view.member[1].session = 202;
    offer.accepted = 1; offer.token = 3; offer.rules.epoch = 4; offer.n = 1;
    assert(coop_feature_set(&offer.rules, COOP_RULE_SOCIAL, COOP_SOCIAL_CHAT));
    offer.member[0].id = 2; offer.member[0].heard = 1;
    strcpy(offer.member[0].name, "Bob"); strcpy(offer.member[0].key, "1111-2222-3333-4444");
    coop_chat_room(s, &view, &offer, 4, clock_ms);
    assert(s->enabled && s->permission && s->members == 4);
}
static size_t message(uint64_t sequence, const void *text, size_t n) {
    assert(n <= HOS_COOP_CHAT_BYTES);
    packet[0] = 1; write_le(packet + 1, 4, 4); write_le(packet + 5, sequence, 8); write_le(packet + 13, n, 2);
    memcpy(packet + COOP_CHAT_HEAD, text, n);
    return packet_n = n + COOP_CHAT_HEAD;
}
static void text_cases(void) {
    char out[HOS_COOP_CHAT_BYTES + 1];
    const char *good = "héllo 世界";
    assert(coop_text_clean(out, sizeof out, (const uint8_t *)good, strlen(good)) == strlen(good));
    assert(!strcmp(good, out));
    const uint8_t bad[][4] = {{0x80}, {0xc0, 0xaf}, {0xe0, 0x80, 0xaf}, {0xed, 0xa0, 0x80}, {0xf4, 0x90, 0x80, 0x80}, {0xf5, 0x80, 0x80, 0x80}, {0xe2, 0x82}};
    const unsigned lengths[] = {1,2,3,3,4,4,2};
    for (unsigned i = 0; i < sizeof lengths / sizeof *lengths; i++) assert(!coop_text_clean(out, sizeof out, bad[i], lengths[i]));
    const char controls[] = " \nA\t\177\302\205\342\200\256B\342\201\246\357\273\277 ";
    assert(coop_text_clean(out, sizeof out, (const uint8_t *)controls, sizeof controls - 1) == 2 && !strcmp(out, "AB"));
    char big[HOS_COOP_CHAT_BYTES + 2]; memset(big, 'x', sizeof big);
    assert(coop_text_clean(out, sizeof out, (uint8_t *)big, HOS_COOP_CHAT_BYTES) == HOS_COOP_CHAT_BYTES);
    assert(!coop_text_clean(out, sizeof out, (uint8_t *)big, HOS_COOP_CHAT_BYTES + 1));
    assert(!coop_text_clean(out, sizeof out, (const uint8_t *)" \n ", 3));
    puts("PASS chat-text: UTF-8, malformed encodings, controls/bidi, exact byte cap, empty refusal");
}
static void wire_cases(void) {
    CoopChat s; setup(&s); MkwVkCoopFeed feed;
    message(1, "valid", 5);
    assert(!coop_chat_receive(&s, 2, 202, packet, COOP_CHAT_HEAD, 12000));
    assert(!coop_chat_receive(&s, 2, 202, packet, COOP_CHAT_PACKET + 1, 12000));
    packet[0] = 2; assert(!coop_chat_receive(&s, 2, 202, packet, packet_n, 12000));
    packet[0] = 1; packet[1] = 5; assert(!coop_chat_receive(&s, 2, 202, packet, packet_n, 12000));
    message(0, "valid", 5); assert(!coop_chat_receive(&s, 2, 202, packet, packet_n, 12000));
    message(1, "valid", 5); packet[13] = 6;
    assert(!coop_chat_receive(&s, 2, 202, packet, packet_n, 12000));
    packet[13] = 5; assert(!coop_chat_receive(&s, 2, 202, packet, packet_n, 14000)); /* malformed stays consumed */
    const uint8_t malformed[] = {0xed, 0xa0, 0x80};
    message(2, malformed, sizeof malformed);
    assert(!coop_chat_receive(&s, 2, 202, packet, packet_n, 15000));
    const char cleanable[] = "hé\\n\\t\\342\\200\\256llo";
    message(3, cleanable, sizeof cleanable - 1);
    assert(coop_chat_receive(&s, 2, 202, packet, packet_n, 17000));
    assert(coop_chat_snapshot(&s, &feed, 17000, 1) && !strcmp(feed.line[0].text, "héllo"));
    char boundary[HOS_COOP_CHAT_BYTES]; memset(boundary, 'x', sizeof boundary);
    message(4, boundary, sizeof boundary);
    assert(coop_chat_receive(&s, 2, 202, packet, packet_n, 19000));
    assert(coop_chat_snapshot(&s, &feed, 19000, 1) && strlen(feed.line[0].text) == sizeof boundary);
    assert(!coop_chat_receive(&s, 2, 202, packet, packet_n, 100000)); /* no silence reset */
    puts("PASS chat-wire: version/epoch/sequence/length refusals, malformed UTF-8 consumed, controls cleaned, exact maximum and delayed replay");
}

static void receive_cases(void) {
    CoopChat s; setup(&s);
    MkwVkCoopFeed feed;
    size_t n = message(1, "hello", 5);
    assert(!coop_chat_receive(&s, 0, 202, packet, n, 12000));
    assert(!coop_chat_receive(&s, 2, 999, packet, n, 12000));
    assert(!coop_chat_receive(&s, 3, 303, packet, n, 12000));
    assert(coop_chat_receive(&s, 2, 202, packet, n, 12000));
    assert(!coop_chat_receive(&s, 2, 202, packet, n, 15000));
    message(2, "fast", 4); assert(!coop_chat_receive(&s, 2, 202, packet, packet_n, 12001));
    assert(!coop_chat_receive(&s, 2, 202, packet, packet_n, 15000)); /* rate rejection consumed */
    assert(coop_chat_mute(&s, 2));
    message(3, "muted", 5); assert(!coop_chat_receive(&s, 2, 202, packet, packet_n, 18000));
    assert(coop_chat_snapshot(&s, &feed, 18000, 1) && !feed.count && feed.muted == 4);
    assert(coop_chat_mute(&s, 2));
    assert(!coop_chat_receive(&s, 2, 202, packet, packet_n, 20000));
    assert(!coop_chat_mute(&s, 1) && !coop_chat_mute(&s, 9));
    offer.accepted = 0; coop_chat_room(&s, &view, &offer, 0, 21000);
    message(4, "declined", 8); assert(!coop_chat_receive(&s, 2, 202, packet, packet_n, 22000));
    assert(!coop_chat_snapshot(&s, &feed, 22000, 1) && !feed.count && !feed.can_chat);
    offer.accepted = 1; coop_chat_room(&s, &view, &offer, 4, 23000);
    message(1, "replay", 6); assert(!coop_chat_receive(&s, 2, 202, packet, packet_n, 24000));
    message(5, "fresh", 5); assert(coop_chat_receive(&s, 2, 202, packet, packet_n, 24000));
    offer.rules.epoch++; coop_chat_room(&s, &view, &offer, 4, 25000);
    message(6, "old rules", 9); assert(!coop_chat_receive(&s, 2, 202, packet, packet_n, 26000));
    assert(coop_chat_snapshot(&s, &feed, 26000, 1) && !feed.count);
    offer.rules.features.count = 0; coop_chat_room(&s, &view, &offer, 4, 27000);
    assert(!s.permission && !coop_chat_prepare(&s, "no permission", packet, 28000));
    assert(!coop_feature_set(&offer.rules, COOP_RULE_SOCIAL, 2));
    puts("PASS chat-admission: unaccepted/declined/session/rule refusal, mute, duplicate/replay and consumed rate refusals");
}
static void history_notices(void) {
    CoopChat s; setup(&s); MkwVkCoopFeed feed;
    assert(coop_chat_snapshot(&s, &feed, clock_ms, 1) && feed.count == 1 && feed.line[0].kind == HOS_COOP_FEED_JOIN);
    view.member[1].away = 1; coop_chat_room(&s, &view, &offer, 4, 12000);
    assert(coop_chat_snapshot(&s, &feed, 12000, 1) && feed.line[0].kind == HOS_COOP_FEED_LOST);
    view.member[1].away = 0; coop_chat_room(&s, &view, &offer, 4, 14000);
    assert(coop_chat_snapshot(&s, &feed, 14000, 1) && feed.line[0].kind == HOS_COOP_FEED_BACK);
    const uint32_t item = first_get_hash("Weapon_Range_SlingShot");
    assert(!strcmp(coop_chat_item_label(item), "Fairy Slingshot"));
    coop_chat_shared(&s, 2, 202, COOP_CAT_ITEMS, item, 1, 16000);
    assert(coop_chat_snapshot(&s, &feed, 16000, 1) && feed.line[0].kind == HOS_COOP_FEED_ITEM && !strcmp(feed.line[0].text, "Fairy Slingshot"));
    coop_chat_shared(&s, 2, 202, COOP_CAT_SONGS, first_get_hash("Item_Melody_TimeSong"), 1, 18000);
    assert(coop_chat_snapshot(&s, &feed, 18000, 1) && !strcmp(feed.line[0].text, "Song of Time"));
    assert(coop_chat_notices(&s, 1) && !coop_chat_notices(&s, 3));
    coop_chat_shared(&s, 2, 202, COOP_CAT_ITEMS, item, 1, 20000);
    assert(coop_chat_snapshot(&s, &feed, 20000, 1) && !feed.count);
    view.n = 1; offer.n = 0; coop_chat_room(&s, &view, &offer, 0, 22000);
    assert(coop_chat_snapshot(&s, &feed, 22000, 1) && feed.line[0].kind == HOS_COOP_FEED_LEFT);
    setup(&s); assert(coop_chat_notices(&s, 0));
    for (unsigned i = 0; i < 30; i++) {
        size_t n = message(i + 1, "latest", 6);
        assert(coop_chat_receive(&s, 2, 202, packet, n, 30000 + i * 1500));
    }
    assert(s.count == COOP_CHAT_HISTORY);
    assert(coop_chat_snapshot(&s, &feed, 73500, 1) && feed.count == HOS_COOP_FEED_LINES);
    assert(coop_chat_snapshot(&s, &feed, 73500, 0) && !feed.count && !feed.visible && !feed.can_chat);
    assert(coop_chat_snapshot(&s, &feed, 90000, 1) && !feed.count);
    coop_chat_room(&s, NULL, NULL, 0, 91000);
    assert(!coop_chat_snapshot(&s, &feed, 91000, 1) && !s.count);
    puts("PASS chat-feed: join/left/lost/back/item/song notices, local modes, history/expiry caps, unsafe scene and off");
}
static void runtime(void) {
    CoopChat tmp; setup(&tmp); MkwVkCoopFeed feed;
    assert(!hos_coop_feed(&feed));
    hos_coop_chat_start(); assert(event_fn);
    hos_coop_chat_room(&view, &offer, 4); hos_coop_chat_scene(1);
    assert(hos_coop_feed(&feed) && feed.can_chat);
    queued = 0; assert(!hos_coop_chat_command(HOS_COOP_CHAT_SEND, "hi", 0) && !sends);
    queued = 1; assert(hos_coop_chat_command(HOS_COOP_CHAT_SEND, "hi", 0) && sends == 1);
    assert(!hos_coop_chat_command(HOS_COOP_CHAT_SEND, "fast", 0) && sends == 1);
    assert(hos_coop_feed(&feed) && !strcmp(feed.line[0].text, "hi"));
    clock_ms += 2000; message(20, "remote", 6); accepted = 0;
    event_fn(2, 202, 20, packet, packet_n, NULL);
    assert(hos_coop_feed(&feed) && strcmp(feed.line[0].text, "remote"));
    accepted = 1; event_fn(2, 202, 21, packet, packet_n, NULL);
    assert(hos_coop_feed(&feed) && !strcmp(feed.line[0].text, "remote"));
    hos_coop_chat_scene(0); assert(hos_coop_feed(&feed) && !feed.visible && !feed.can_chat);
    assert(!hos_coop_chat_command(HOS_COOP_CHAT_SEND, "cutscene", 0));
    hos_coop_chat_room(NULL, NULL, 0); assert(!hos_coop_feed(&feed));
    assert(!hos_coop_chat_command(HOS_COOP_CHAT_SEND, "off", 0));
    puts("PASS chat-runtime: real callbacks, queue failure/no echo, accepted EVENT receive, scene cancellation, off");
}
int main(void) {
    text_cases(); wire_cases(); receive_cases(); history_notices(); runtime();
    return 0;
}
