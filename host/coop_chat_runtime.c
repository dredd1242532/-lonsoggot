/* Thread-safe adapter: consent owner -> volatile feed -> renderer. No text logging. */
#include "coop_chat.h"
#include "coop_net.h"
#include <string.h>

static CoopOnce once = COOP_ONCE_INIT;
static CoopLock lock;
static CoopChat chat;
static int active, scene_safe;
int hos_ini_option(const char *key, int otherwise);

static void receive(int from, uint64_t session, uint64_t event_id, const uint8_t *bytes, size_t n, void *user) {
    (void)event_id; (void)user;
    if (!__atomic_load_n(&active, __ATOMIC_ACQUIRE) || !coop_member_accepted(from)) return;
    coop_lock(&lock);
    coop_chat_receive(&chat, from, session, bytes, n, coop_now_ms());
    coop_unlock(&lock);
}
static void initialize(void *unused) {
    (void)unused;
    coop_lock_init(&lock);
    coop_chat_init(&chat);
    int mode = hos_ini_option("coop_notices", 2);
    if (mode >= 0 && mode <= 2) chat.notices = mode;
    coop_on_event(COOP_K_CHAT, receive, NULL);
}
void hos_coop_chat_start(void) { coop_once(&once, initialize, NULL); }
void hos_coop_chat_room(const CoopView *v, const CoopOffer *offer, uint32_t accepted) {
    hos_coop_chat_start();
    coop_lock(&lock);
    coop_chat_room(&chat, v, offer, accepted, coop_now_ms());
    __atomic_store_n(&active, chat.enabled, __ATOMIC_RELEASE);
    coop_unlock(&lock);
}
void hos_coop_chat_scene(int safe) { __atomic_store_n(&scene_safe, safe != 0, __ATOMIC_RELEASE); }
void hos_coop_chat_shared(int member, uint64_t session, int category, uint32_t hash, uint32_t value, void *user) {
    (void)user;
    if (!__atomic_load_n(&active, __ATOMIC_ACQUIRE)) return;
    coop_lock(&lock);
    coop_chat_shared(&chat, member, session, category, hash, value, coop_now_ms());
    coop_unlock(&lock);
}
int hos_coop_feed(MkwVkCoopFeed *out) {
    if (!out) return 0;
    if (!__atomic_load_n(&active, __ATOMIC_ACQUIRE)) return 0; /* off: one atomic, no lock/clock/copy */
    coop_lock(&lock);
    const int result = coop_chat_snapshot(&chat, out, coop_now_ms(), __atomic_load_n(&scene_safe, __ATOMIC_ACQUIRE));
    coop_unlock(&lock);
    return result;
}
int hos_coop_chat_command(int command, const char *text, uint32_t number) {
    /* Settings may be read before the session starts; initializing registers a handler, never a socket/thread. */
    hos_coop_chat_start();
    coop_lock(&lock);
    int result = 0;
    if (command == HOS_COOP_CHAT_MUTE) result = coop_chat_mute(&chat, number);
    else if (command == HOS_COOP_CHAT_NOTICES) result = coop_chat_notices(&chat, number);
    else if (command == HOS_COOP_CHAT_SEND && __atomic_load_n(&scene_safe, __ATOMIC_ACQUIRE)) {
        uint8_t packet[COOP_CHAT_PACKET];
        const uint64_t now = coop_now_ms();
        const size_t n = coop_chat_prepare(&chat, text, packet, now);
        if (n && coop_send_event(0, COOP_K_CHAT, packet, n) == COOP_EVENT_QUEUED) {
            coop_chat_sent(&chat, packet, n, now);
            result = 1;
        }
    }
    coop_unlock(&lock);
    return result;
}
