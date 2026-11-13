// Game-free session event tests. The real session sender, envelope and receiver run over loopback;
// only the relay's scheduling is simulated, so loss and reordering are deterministic.
#ifndef _CRT_SECURE_NO_WARNINGS
#define _CRT_SECURE_NO_WARNINGS
#endif
#include "coop.c"
#include <assert.h>

static char stats_line[512];
void hos_log(const char *fmt, ...) {
    char line[512];
    va_list ap; va_start(ap, fmt); vsnprintf(line, sizeof line, fmt, ap); va_end(ap);
    if (strstr(line, "stats:")) snprintf(stats_line, sizeof stats_line, "%s", line);
}
int hos_ini_option(const char *key, int otherwise) { (void)key; return otherwise; }
int hos_ini_text(const char *key, char *out, size_t n) { (void)key; (void)out; (void)n; return 0; }

static CoopSess *a, *b;
static CoopUdp *relay;
static unsigned received, raw_received, dropped, forwarded;
static uint64_t last_id, clock_ms;
static uint8_t expected[COOP_EVENT_MAX];
static size_t expected_n;
static int expect_pattern;
static unsigned rng = 1234567;
static uint8_t saved[COOP_MAX_DGRAM];
static size_t saved_n;
static unsigned sent_parts[16];
static int lose_part, lost_part, ack_loss;

typedef struct { uint8_t bytes[COOP_MAX_DGRAM]; size_t n; } Packet;
static Packet pending[256];
static unsigned npending;

static void receive_event(int from, uint64_t session, uint64_t id, const uint8_t *bytes, size_t n, void *user) {
    (void)user;
    assert(from == 1 && session == 101 && id > last_id);
    if (expect_pattern) assert(n == expected_n && !memcmp(bytes, expected, n));
    last_id = id;
    received++;
}
static void receive_raw(int from, const uint8_t *bytes, size_t n, void *user) {
    (void)from; (void)bytes; (void)n; (void)user;
    raw_received++;
}

/* (grant, not "accept": winsock2.h declares accept(), and the host's network header brings it in on Windows) */
static void grant(CoopSess *s, uint32_t members) {
    CoopView v;
    coop_sess_view(s, &v);
    coop_sess_accept_members(s, &v, members);
}

static void setup(void) {
    CoopParams p = {.name = "test", .on = 0};
    a = coop_sess_new(&p), b = coop_sess_new(&p);
    relay = coop_udp_open(0);
    assert(a && b && relay);
    CoopRoster roster = {.n = 2, .m = {{.id = 1, .session = 101}, {.id = 2, .session = 202}}};
    CoopSess *pair[] = {a, b};
    for (int i = 0; i < 2; i++) {
        CoopSess *s = pair[i];
        s->sock = coop_udp_open(0);
        assert(s->sock && coop_net_resolve("127.0.0.1", coop_udp_port(relay), &s->relay));
        s->relay_known = 1, s->room = 17, s->self = i + 1;
        s->session = s->room_session = i ? 202 : 101;
        memcpy(s->secret, "H7K3WMBCDFGH234567", COOP_SECRET_LEN + 1);
        coop_room_keys(s->secret, s->locator, s->msg_key);
        s->roster = roster;
        set_state(s, COOP_JOINED);
        publish(s);
        grant(s, 1u << (i ? 1 : 2));
    }
    assert(coop_sess_on_event(b, COOP_K_TEST, receive_event, NULL));
    assert(coop_sess_on_message(b, COOP_K_TEST, receive_raw, NULL));
    clock_ms = 1000;
    lose_part = -1;
}

static void deliver(const uint8_t *bytes, size_t n) {
    CoopHdr h;
    const uint8_t *payload;
    assert(coop_dgram_parse(bytes, n, &h, &payload));
    CoopSess *s = h.dst == 1 ? a : b;
    on_data(s, bytes, n, &h, payload, clock_ms);
    forwarded++;
}

static void collect(unsigned loss) {
    uint8_t bytes[COOP_MAX_DGRAM], plain[COOP_MAX_MESSAGE], kind;
    CoopAddr from;
    int n;
    while ((n = coop_udp_recv(relay, bytes, sizeof bytes, &from, 0)) > 0) {
        CoopHdr h;
        const uint8_t *payload;
        size_t count;
        assert(coop_dgram_parse(bytes, (size_t)n, &h, &payload));
        assert(coop_open(payload, h.len, a->msg_key, h.src, h.dst, &kind, plain, sizeof plain, &count));
        if (kind == COOP_K_EVENT) {
            CoopEventPart part;
            assert(coop_event_part_parse(plain, count, &part));
            sent_parts[part.part]++;
            if (!saved_n) memcpy(saved, bytes, (size_t)n), saved_n = (size_t)n;
            if (part.part == lose_part && !lost_part) { lost_part = 1; dropped++; continue; }
        }
        if (kind == COOP_K_EVENT_ACK && ack_loss) { dropped++; continue; }
        rng = rng * 1664525u + 1013904223u;
        if (rng % 100 < loss) { dropped++; continue; }
        assert(npending < 256);
        memcpy(pending[npending].bytes, bytes, (size_t)n);
        pending[npending++].n = (size_t)n;
    }
}

static void pump(unsigned milliseconds, unsigned loss, int reorder) {
    for (unsigned tick = 0; tick < milliseconds; tick += 8) {
        clock_ms += 8;
        events_tick(a, clock_ms), events_tick(b, clock_ms);
        collect(loss);
        if (!reorder || tick % 128 == 120 || tick + 8 >= milliseconds) {
            if (reorder) while (npending) { Packet *p = &pending[--npending]; deliver(p->bytes, p->n); }
            else {
                for (unsigned i = 0; i < npending; i++) deliver(pending[i].bytes, pending[i].n);
                npending = 0;
            }
        }
    }
}

static unsigned queued(void) {
    unsigned n = 0;
    coop_lock(&a->lock);
    for (unsigned i = 0; i < COOP_EVENT_SLOTS; i++) n += a->event_out[i].id != 0;
    coop_unlock(&a->lock);
    return n;
}

static void test_delivery(unsigned loss, int reorder) {
    for (unsigned i = 0; i < COOP_EVENT_SLOTS; i++)
        assert(coop_sess_send_event(a, 2, COOP_K_TEST, &i, sizeof i) == COOP_EVENT_QUEUED);
    pump(20000, loss, reorder);
    assert(received == COOP_EVENT_SLOTS && !queued() && !raw_received);
    if (loss) assert(dropped && forwarded);
    puts("PASS events: no loss / 30 percent loss / reordered FIFO");
}

static void test_parts(void) {
    expected_n = COOP_EVENT_MAX, expect_pattern = 1;
    for (unsigned i = 0; i < sizeof expected; i++) expected[i] = (uint8_t)(i * 7u);
    lose_part = 5;
    assert(coop_sess_send_event(a, 2, COOP_K_TEST, expected, expected_n) == COOP_EVENT_QUEUED);
    pump(512, 0, 1);
    assert(lost_part && received == 0);   // a hole never reaches the application
    pump(12000, 30, 1);
    assert(received == 1 && !queued() && sent_parts[5] >= 2);
    puts("PASS events: 16 KiB reassembly, missing part, whole or nothing");
}

static void test_duplicate(void) {
    ack_loss = 1;
    assert(coop_sess_send_event(a, 2, COOP_K_TEST, NULL, 0) == COOP_EVENT_QUEUED);
    pump(2000, 0, 0);
    assert(received == 1 && queued() == 1 && sent_parts[0] > 1);
    // An actual recorded envelope is rejected; even resetting that window cannot redeliver the event.
    deliver(saved, saved_n);
    memset(b->rw, 0, sizeof b->rw);
    deliver(saved, saved_n);
    assert(received == 1);
    ack_loss = 0;
    pump(1000, 0, 0);
    assert(received == 1 && !queued());
    puts("PASS events: lost ACK, freshly sealed retries, duplicate and envelope replay");
}

static void test_queue_and_departure(void) {
    assert(coop_sess_send_event(a, 2, COOP_K_TEST, expected, COOP_EVENT_MAX + 1) == COOP_EVENT_INVALID);
    assert(coop_sess_send_event(a, 2, COOP_K_EVENT, NULL, 0) == COOP_EVENT_INVALID);
    for (int i = 0; i < COOP_EVENT_SLOTS; i++) assert(coop_sess_send_event(a, 0, COOP_K_TEST, expected, sizeof expected) == 1);
    assert(coop_sess_send_event(a, 2, COOP_K_TEST, NULL, 0) == COOP_EVENT_FULL);
    pump(64, 0, 0);
    assert(!received);
    CoopRoster changed = a->roster;
    changed.n = 1;
    apply_roster(a, &changed), publish(a);
    events_tick(a, clock_ms);
    assert(!queued() && !coop_sess_member_accepted(a, 2));
    assert(coop_sess_send_event(a, 2, COOP_K_TEST, NULL, 0) == COOP_EVENT_UNAVAILABLE);
    changed.n = 2, changed.m[1].session = 303;
    apply_roster(a, &changed), publish(a);
    assert(!coop_sess_member_accepted(a, 2));
    grant(a, 1u << 2);
    assert(coop_sess_member_accepted(a, 2));
    assert(coop_sess_send_event(a, 2, COOP_K_TEST, NULL, 0) == COOP_EVENT_QUEUED);
    go_left(a, "test", 0), publish(a);
    assert(!queued() && !coop_sess_member_accepted(a, 2));
    puts("PASS events: bounded queue, recipient departure/session replacement, local leave");
}

static void test_acceptance(void) {
    CoopView stale;
    coop_sess_view(b, &stale);
    grant(b, 0);
    assert(!coop_sess_member_accepted(b, 1));
    assert(coop_sess_send_event(a, 2, COOP_K_TEST, NULL, 0) == COOP_EVENT_QUEUED);
    pump(1000, 0, 0);
    assert(!received && queued() == 1);
    grant(b, 1u << 1);
    pump(1000, 0, 0);
    assert(received == 1 && !queued());
    CoopRoster changed = b->roster;
    changed.m[0].session = 404;
    apply_roster(b, &changed), publish(b);
    assert(!coop_sess_member_accepted(b, 1));
    coop_sess_accept_members(b, &stale, 1u << 1);
    assert(!coop_sess_member_accepted(b, 1));
    // A message addressed to an earlier recipient session must not count after its own rejoin.
    events_reset(b);
    b->session = b->room_session = 505;
    changed.m[0].session = 101, changed.m[1].session = 505;
    apply_roster(b, &changed), publish(b), grant(b, 1u << 1);
    memset(b->rw, 0, sizeof b->rw);
    deliver(saved, saved_n);
    assert(received == 1);
    puts("PASS events: unaccepted sender, stale acceptance, recipient session binding");
}

static void test_codecs(void) {
    uint8_t bytes[COOP_EVENT_HEAD + COOP_EVENT_PART];
    CoopEventPart p = {.recipient_session = 7, .id = 8, .kind = COOP_K_TEST, .total = COOP_EVENT_MAX, .part = 15, .bytes = expected}, out;
    size_t n = coop_event_part_pack(bytes, sizeof bytes, &p);
    assert(n == sizeof bytes && coop_event_part_parse(bytes, n, &out));
    assert(out.id == 8 && out.total == COOP_EVENT_MAX && out.part == 15);
    for (size_t i = 0; i < n; i++) assert(!coop_event_part_parse(bytes, i, &out));
    bytes[20] = 16;
    assert(!coop_event_part_parse(bytes, n, &out));
    bytes[20] = 15, bytes[0] = 2;
    assert(!coop_event_part_parse(bytes, n, &out));
    p.total = COOP_EVENT_MAX + 1;
    assert(!coop_event_part_pack(bytes, sizeof bytes, &p));
    assert(coop_event_parts(0) == 1 && coop_event_parts(COOP_EVENT_MAX) == 65535 && !coop_event_parts(COOP_EVENT_MAX + 1));
    CoopEventAck ack = {.sender_session = 1, .id = 2, .parts = 3}, decoded;
    n = coop_event_ack_pack(bytes, sizeof bytes, &ack);
    assert(n == COOP_EVENT_ACK_SIZE && coop_event_ack_parse(bytes, n, &decoded));
    for (size_t i = 0; i < n; i++) assert(!coop_event_ack_parse(bytes, i, &decoded));
    assert(!coop_event_ack_parse(bytes, n + 1, &decoded));
    puts("PASS events: bounded little-endian codecs, malformed lengths and versions");
}

static int race_done;
static void race_sender(void *unused) {
    (void)unused;
    for (int i = 0; i < 2000; i++) {
        coop_sess_send_event(a, 2, COOP_K_TEST, &i, sizeof i);
        coop_sess_member_accepted(a, 2);
    }
    __atomic_store_n(&race_done, 1, __ATOMIC_RELEASE);
}
static void test_threads(void) {
    CoopThread t;
    assert(coop_thread_start(&t, race_sender, NULL));
    while (!__atomic_load_n(&race_done, __ATOMIC_ACQUIRE)) {
        pump(8, 0, 0);
        grant(a, 1u << 2);
    }
    assert(coop_thread_join(&t, 5000));
    pump(10000, 0, 0);
    assert(!queued());
    puts("PASS events: concurrent enqueue, consent publication, read and stepping");
}

// Reset regressions use the same real envelopes as the existing cases; old test bodies remain unchanged.
static void deliver_pending(Packet *last_ack) {
    for (unsigned i = 0; i < npending; i++) {
        CoopHdr h;
        const uint8_t *payload;
        assert(coop_dgram_parse(pending[i].bytes, pending[i].n, &h, &payload));
        if (last_ack && h.src == 2) *last_ack = pending[i];
        deliver(pending[i].bytes, pending[i].n);
    }
    npending = 0;
}

static void reseal_packet(const Packet *packet) {
    CoopHdr h;
    const uint8_t *payload;
    uint8_t bytes[COOP_MAX_MESSAGE], kind;
    size_t n;
    assert(coop_dgram_parse(packet->bytes, packet->n, &h, &payload));
    assert(coop_open(payload, h.len, a->msg_key, h.src, h.dst, &kind, bytes, sizeof bytes, &n));
    send_sealed(h.src == 1 ? a : b, h.dst, kind, bytes, n);
    collect(0);
    deliver_pending(NULL);
}

static Packet first_half(void) {
    expect_pattern = 1, expected_n = sizeof expected;
    for (unsigned i = 0; i < sizeof expected; i++) expected[i] = (uint8_t)(i * 11u + 3);
    assert(coop_sess_send_event(a, 2, COOP_K_TEST, expected, sizeof expected) == COOP_EVENT_QUEUED);
    pump(256, 0, 0);
    assert(!received && b->event_in[1].parts == 0xff && a->event_out[0].target[2].acked == 0xff);
    // Ask for another ACK of those same pieces, and retain its actual sealed bytes for a delayed-ACK test.
    Packet first = {.n = saved_n}, ack = {0};
    memcpy(first.bytes, saved, saved_n);
    reseal_packet(&first);
    clock_ms += EVENT_ACK_MS + 8;
    events_tick(b, clock_ms);
    collect(0);
    deliver_pending(&ack);
    assert(ack.n);
    return ack;
}

static void test_cancel_partial(int receiver_reaccepts) {
    first_half();
    grant(a, 0);   // a save rebind cancels without changing this sender's session
    assert(!queued() && a->session == 101);
    if (receiver_reaccepts) grant(b, 0), grant(b, 1u << 1);
    grant(a, 1u << 2);
    for (int i = 0; i < 2; i++)
        assert(coop_sess_send_event(a, 2, COOP_K_TEST, expected, sizeof expected) == COOP_EVENT_QUEUED);
    pump(30000, 30, 1);
    assert(received == 2 && last_id == 3 && !queued());
    Packet old = {.n = saved_n};
    memcpy(old.bytes, saved, saved_n);
    reseal_packet(&old);
    pump(1000, 0, 0);
    assert(received == 2 && last_id == 3);   // abandoned id1 cannot return after ids2/3
    puts("PASS events: cancelled partial, acceptance again, later FIFO events and no duplicate");
}

static void test_receiver_reset(int delayed_ack) {
    Packet old_ack = first_half();
    grant(b, 0);
    events_tick(b, clock_ms);   // the receiver drops its first half; sender still remembers its ACK
    grant(b, 1u << 1);
    if (delayed_ack) {
        pump(8, 0, 0);    // one replacement-assembly part; do not advance the sender again yet
        clock_ms += EVENT_ACK_MS;
        events_tick(b, clock_ms);
        collect(0);
        deliver_pending(NULL);   // first new-assembly ACK, after its real coalescing deadline
        uint16_t parts = a->event_out[0].target[2].acked;
        assert(parts && !(parts & 0xff));   // the old first-half mask is not part of this assembly
        reseal_packet(&old_ack);           // a delayed old ACK, with a fresh envelope counter
        assert(a->event_out[0].target[2].acked == parts);
    }
    pump(16000, delayed_ack ? 30 : 0, 1);
    assert(received == 1 && last_id == 1 && !queued());
    puts("PASS events: receiver reset cannot merge ACK masks; delayed old ACK cannot roll back assembly");
}

static void test_reaccept_between_steps(void) {
    first_half();
    grant(b, 0), grant(b, 1u << 1);   // no stepping call observes the revoked binding
    pump(8, 0, 0);
    assert(!received && !(b->event_in[1].parts & 0xff));
    pump(16000, 30, 1);
    assert(received == 1 && last_id == 1 && !queued());
    puts("PASS events: revoke/reaccept between steps still discards the old assembly");
}

static void test_save_scope(void) {
    uint8_t id[16] = {1}, other[16] = {2};
    CoopView av, bv;
    coop_sess_view(a, &av); coop_sess_view(b, &bv);
    grant(a, 0); grant(b, 0);
    coop_sess_download_members(a, &av, 1u << 2);
    coop_sess_download_members(b, &bv, 1u << 1);
    assert(!coop_sess_member_accepted(a, 2) && !coop_sess_member_accepted(b, 1));
    assert(coop_sess_send_event(a, 2, COOP_K_TEST, NULL, 0) == COOP_EVENT_UNAVAILABLE);
    assert(coop_sess_send_event(a, 2, COOP_K_SAVE_CHUNK, NULL, 0) == COOP_EVENT_INVALID);
    assert(!coop_sess_on_save_event(b, COOP_K_TEST, receive_event, NULL));
    assert(coop_sess_on_save_event(b, COOP_K_SAVE_CHUNK, receive_event, NULL));
    assert(coop_sess_send_save_event(a, 0, COOP_K_SAVE_CHUNK, id, clock_ms + 1000, NULL, 0) == COOP_EVENT_INVALID);
    assert(coop_sess_send_save_event(a, 2, COOP_K_SAVE_CHUNK, id, clock_ms + 1, expected, sizeof expected) == 1);
    pump(16, 0, 0);
    assert(!queued() && !received); /* expired work is retired before any fragment */
    assert(coop_sess_send_save_event(a, 2, COOP_K_SAVE_CHUNK, id, clock_ms + 10000, expected, sizeof expected) == 1);
    assert(coop_sess_send_save_event(a, 2, COOP_K_SAVE_CHUNK, other, clock_ms + 10000, expected, sizeof expected) == COOP_EVENT_FULL);
    pump(80, 0, 0); /* leave a partial assembly at the receiver */
    assert(!received);
    grant(a, 1u << 2); grant(b, 1u << 1);
    assert(coop_sess_send_event(a, 2, COOP_K_TEST, NULL, 0) == 1);
    coop_sess_cancel_save_events(a, other); assert(queued() == 2);
    coop_sess_cancel_save_events(a, id); assert(queued() == 1);
    pump(1000, 0, 0);
    assert(received == 1 && !queued()); /* later ordinary FIFO survives scoped cancellation */
    coop_sess_download_members(a, NULL, 0);
    assert(coop_sess_member_accepted(a, 2)); /* revoking downloads cannot revoke gameplay consent */
    puts("PASS events: isolated download scope, chunk quota, deadline, scoped cancellation and surviving FIFO");
}

static void test_presence_scope(void) {
    CoopView av, bv;
    coop_sess_view(a, &av); coop_sess_view(b, &bv);
    grant(a, 0); grant(b, 0);
    coop_sess_download_members(b, &bv, 1u << 1);
    assert(!coop_sess_member_present(b, 1)); // download consent alone never opens chat
    assert(coop_sess_on_event(b, COOP_K_CHAT, receive_event, NULL));
    coop_sess_presence_members(a, &av, 1u << 2);
    assert(coop_sess_send_event(a, 2, COOP_K_CHAT, NULL, 0) == COOP_EVENT_QUEUED);
    pump(512, 0, 0);
    assert(!received && !b->event_in[1].parts); // refused on arrival, not hidden after storing text
    coop_sess_presence_members(b, &bv, 1u << 1);
    pump(512, 0, 0);
    assert(received == 1 && coop_sess_member_present(b, 1) && !coop_sess_member_accepted(b, 1));
    for (unsigned kind = 1; kind < COOP_K_TEST; kind++) {
        if (!coop_event_kind((uint8_t)kind) || save_kind((uint8_t)kind) || presentation_kind((uint8_t)kind)) continue;
        assert(coop_sess_send_event(a, 2, (uint8_t)kind, NULL, 0) == COOP_EVENT_UNAVAILABLE);
    }
    uint8_t transfer[16] = {1};
    assert(coop_sess_send_save_event(a, 2, COOP_K_SAVE_OFFER, transfer, clock_ms + 1000, NULL, 0) == COOP_EVENT_UNAVAILABLE);
    // A partial chat cannot survive revoke/reaccept between socket steps.
    expected_n = sizeof expected; expect_pattern = 1;
    assert(coop_sess_send_event(a, 2, COOP_K_CHAT, expected, sizeof expected) == COOP_EVENT_QUEUED);
    pump(80, 0, 0);
    assert(received == 1 && b->event_in[1].parts);
    coop_sess_presence_members(b, NULL, 0);
    coop_sess_presence_members(b, &bv, 1u << 1);
    events_tick(b, clock_ms);
    assert(!b->event_in[1].parts && received == 1);
    coop_sess_presence_members(a, NULL, 0);
    assert(!queued() && !coop_sess_member_present(a, 2));
    // Download consent remains its own scope; presentation never grants or revokes it.
    assert(bound_locked(b, 1, COOP_K_SAVE_OFFER));
    set_state(b, COOP_LEFT); publish(b);
    assert(!coop_sess_member_present(b, 1));
    puts("PASS events: presence-only chat, unaccepted arrival drop, every non-presentation kind closed, independent downloads, partial revoke and leave");
}

static void test_counters(void) {
    assert(coop_sess_send_event(a,2,COOP_K_TEST,NULL,0)==COOP_EVENT_QUEUED);
    pump(1000,0,0);
    assert(!queued() && !a->n_event_retries && !a->n_event_given_up);
    const unsigned first=sent_parts[0];
    ack_loss=1;
    assert(coop_sess_send_event(a,2,COOP_K_TEST,NULL,0)==COOP_EVENT_QUEUED);
    pump(1000,0,0);
    assert(queued()==1 && a->n_event_retries==sent_parts[0]-first-1 && a->n_event_retries>0);
    grant(a,0); assert(a->n_event_given_up==1 && !queued());
    grant(a,0); events_tick(a,clock_ms); assert(a->n_event_given_up==1);
    CoopView view; coop_sess_view(a,&view); coop_sess_download_members(a,&view,1u<<2);
    uint8_t transfer[16]={1};
    assert(coop_sess_send_save_event(a,2,COOP_K_SAVE_CHUNK,transfer,clock_ms+100,NULL,0)==COOP_EVENT_QUEUED);
    events_tick(a,clock_ms+100); assert(a->n_event_given_up==2 && !queued());
    assert(coop_sess_send_save_event(a,2,COOP_K_SAVE_CHUNK,transfer,clock_ms+1000,NULL,0)==COOP_EVENT_QUEUED);
    coop_sess_cancel_save_events(a,transfer); coop_sess_cancel_save_events(a,transfer);
    assert(a->n_event_given_up==3 && !queued());
    grant(a,1u<<2);
    assert(coop_sess_send_event(a,2,COOP_K_TEST,NULL,0)==COOP_EVENT_QUEUED);
    events_reset(a); events_reset(a); assert(a->n_event_given_up==4 && !queued());
    a->last_rx=clock_ms; a->next_ping=UINT64_MAX; a->next_stats=0;
    timers(a,clock_ms);
    assert(strstr(stats_line,"reliable retries ") && strstr(stats_line,"events given up 4, round trip "));
    a->n_event_retries=a->n_event_given_up=UINT64_MAX;
    a->n_tx=a->n_rx=a->n_lost=a->n_bad_tag=a->n_sender=a->n_malformed=a->n_dup=a->n_test_drop=a->n_outbox_full=UINT32_MAX;
    a->next_stats=0; timers(a,clock_ms);
    assert(strstr(stats_line,"reliable retries 18446744073709551615, events given up 18446744073709551615, round trip "));
    puts("PASS events: retry attempts, acknowledged success, cancellation/deadline/revoke/leave abandonment once, and complete stats line");
}

int main(int argc, char **argv) {
    assert(argc == 2);
    setup();
    if (!strcmp(argv[1], "no-loss")) test_delivery(0, 0);
    else if (!strcmp(argv[1], "loss")) test_delivery(30, 0);
    else if (!strcmp(argv[1], "reorder")) test_delivery(0, 1);
    else if (!strcmp(argv[1], "parts")) test_parts();
    else if (!strcmp(argv[1], "duplicate")) test_duplicate();
    else if (!strcmp(argv[1], "queue")) test_queue_and_departure();
    else if (!strcmp(argv[1], "acceptance")) test_acceptance();
    else if (!strcmp(argv[1], "codec")) test_codecs();
    else if (!strcmp(argv[1], "threads")) test_threads();
    else if (!strcmp(argv[1], "cancel")) test_cancel_partial(0);
    else if (!strcmp(argv[1], "cancel-accept")) test_cancel_partial(1);
    else if (!strcmp(argv[1], "reset-ack")) test_receiver_reset(0);
    else if (!strcmp(argv[1], "delayed-ack")) test_receiver_reset(1);
    else if (!strcmp(argv[1], "reaccept-step")) test_reaccept_between_steps();
    else if (!strcmp(argv[1], "save-scope")) test_save_scope();
    else if (!strcmp(argv[1], "presence-scope")) test_presence_scope();
    else if (!strcmp(argv[1], "counters")) test_counters();
    else assert(0);
    coop_sess_free(a), coop_sess_free(b), coop_udp_close(relay);
    return 0;
}
