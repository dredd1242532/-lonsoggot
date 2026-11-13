/* Local control/lobby adapter. Socket callbacks copy bounded bytes only; a single
   opt-in disk worker verifies and writes them. No network callback chooses a slot. */
#include "coop_save.h"
#include "coop_profile.h"
#include "coop_state.h"
#include "coop.h"
#include "coop_net.h"
#include <string.h>
#include <time.h>

int hos_oot_coop_save_accept(uint32_t token);
int hos_oot_coop_save_binding(int member, CoopSaveBinding *out);
int hos_oot_coop_offer(CoopOffer *out);
int hos_save_export(unsigned slot, CoopSaveSource *out);

static CoopOnce once = COOP_ONCE_INIT;
static CoopLock state_lock, queue_lock;
static CoopThread worker;
static CoopSave *exchange;
static int running;
static struct {
    int from; uint64_t session; uint8_t kind; size_t n;
    uint8_t bytes[COOP_SAVE_CHUNK_HEAD + COOP_SAVE_CHUNK];
} queue[8];
static unsigned first, count;
static int overflow;

static uint64_t utc_ms(void) { return (uint64_t)time(NULL) * 1000; }
static void initialize(void *unused) {
    (void)unused; coop_lock_init(&state_lock); coop_lock_init(&queue_lock);
}
static int binding(void *u, int member, CoopSaveBinding *out) { (void)u; return hos_oot_coop_save_binding(member, out); }
static int send_event(void *u, int member, uint8_t kind, const uint8_t id[16], uint64_t deadline, const void *p, size_t n) {
    (void)u; return coop_send_save_event(member, kind, id, deadline, p, n);
}
static void cancel(void *u, const uint8_t id[16]) { (void)u; coop_cancel_save_events(id); }
static int source_read(void *u, const char *id, unsigned f, uint32_t off, void *p, size_t n) {
    (void)u; return coop_profile_export_read(id, f, off, p, n);
}
static int stage(void *u, char id[33]) { (void)u; return coop_profile_stage(id); }
static int write_chunk(void *u, const char *id, unsigned f, uint32_t off, const void *p, size_t n) {
    (void)u; return coop_profile_chunk(id, f, off, p, n);
}
static int read_chunk(void *u, const char *id, unsigned f, uint32_t off, void *p, size_t n) {
    (void)u; return coop_profile_read(id, f, off, p, n);
}
static int publish(void *u, int member, const char *id, const uint8_t manifest[COOP_SAVE_OFFER_BYTES],
                   const CoopSaveBinding *expected, uint64_t deadline, uint64_t expiry) {
    (void)u;
    CoopSaveBinding current;
    if (!coop_profile_seal(id, manifest)) return 0;
    return coop_now_ms() < deadline && utc_ms() < expiry && binding(NULL, member, &current) &&
           coop_save_same_binding(&current, expected) && coop_profile_publish(id);
}
static void abandon(void *u, const char *id) { (void)u; coop_profile_abandon(id); }
static const CoopSaveOps ops = {binding, send_event, cancel, source_read, stage, write_chunk, read_chunk, publish, abandon};

static void receive(int from, uint64_t session, uint64_t event, const uint8_t *p, size_t n, void *kind) {
    (void)event;
    if (n > sizeof queue[0].bytes) return;
    coop_lock(&queue_lock);
    if (count == 8) overflow = 1;
    else {
        unsigned at = (first + count++) % 8;
        queue[at].from = from; queue[at].session = session; queue[at].kind = (uint8_t)(uintptr_t)kind; queue[at].n = n;
        memcpy(queue[at].bytes, p, n);
    }
    coop_unlock(&queue_lock);
}
static void run(void *unused) {
    (void)unused;
    for (;;) {
        uint8_t bytes[COOP_SAVE_CHUNK_HEAD + COOP_SAVE_CHUNK];
        int from = 0, failed; uint64_t session = 0; uint8_t kind = 0; size_t n = 0;
        coop_lock(&queue_lock);
        failed = overflow; overflow = 0;
        if (count) {
            from = queue[first].from; session = queue[first].session; kind = queue[first].kind; n = queue[first].n;
            memcpy(bytes, queue[first].bytes, n); first = (first + 1) % 8; count--;
        }
        coop_unlock(&queue_lock);
        coop_lock(&state_lock);
        uint64_t now = coop_now_ms(), utc = utc_ms();
        if (failed) coop_save_cancel(exchange, now);
        if (from) coop_save_receive(exchange, from, session, kind, bytes, n, now, utc);
        coop_save_tick(exchange, coop_now_ms(), utc_ms());
        coop_unlock(&state_lock);
        coop_sleep_ms(25);
    }
}

/* Called only from the local bridge. identity accepts the current signed preview
   for downloading, offer explicitly names a local slot and intended recipient,
   accept echoes the exact manifest hash. No mount/load operation exists here. */
int hos_coop_save_exchange(const char *action, int member, unsigned slot, uint32_t token, const uint8_t hash[32],
                          CoopSaveView *out, CoopOffer *preview) {
    if (!action || !out || !preview || (strcmp(action, "status") && strcmp(action, "identity") &&
        strcmp(action, "offer") && strcmp(action, "accept") && strcmp(action, "cancel"))) return 0;
    coop_once(&once, initialize, NULL);
    memset(preview, 0, sizeof *preview);
    hos_oot_coop_offer(preview);
    if (!strcmp(action, "identity") && !hos_oot_coop_save_accept(token)) return 0;
    coop_lock(&state_lock);
    int ok = 1;
    if (strcmp(action, "status") && !running) {
        exchange = coop_save_new(&ops, NULL);
        ok = exchange != NULL;
        for (unsigned k = COOP_K_SAVE_OFFER; ok && k <= COOP_K_SAVE_CHUNK; k++) ok = coop_on_save_event((uint8_t)k, receive, (void *)(uintptr_t)k);
        if (ok) ok = coop_thread_start(&worker, run, NULL);
        if (ok) running = 1;
        else { coop_save_free(exchange); exchange = NULL; }
    }
    if (ok && !strcmp(action, "offer")) {
        CoopSaveBinding bound;
        CoopSaveSource source;
        CoopSaveView current;
        coop_save_view(exchange, &current);
        ok = current.outgoing != COOP_SAVE_OFFERED && current.outgoing != COOP_SAVE_TRANSFERRING &&
             binding(NULL, member, &bound) && hos_save_export(slot, &source) &&
             coop_save_offer(exchange, member, &source, coop_now_ms(), utc_ms());
    } else if (ok && !strcmp(action, "accept")) ok = coop_save_accept(exchange, hash, coop_now_ms(), utc_ms());
    else if (ok && !strcmp(action, "cancel")) coop_save_cancel(exchange, coop_now_ms());
    else if (strcmp(action, "identity") && strcmp(action, "status")) ok = 0;
    coop_save_view(exchange, out);
    coop_unlock(&state_lock);
    return ok;
}
