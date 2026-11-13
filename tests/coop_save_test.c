/* Authored bytes through the real sealed event helper, signatures and native
   staging filesystem. No game-derived bytes, filenames or native parser. */
#define main transport_fixture_main
#include "coop_events_test.c"
#undef main
#include "coop_save.h"
#include "coop_profile.h"
#include "../third_party/monocypher/monocypher.h"
#include <errno.h>

typedef struct { CoopSess *session; CoopSave *save; int side; } Side;
static Side side[2];
static uint8_t source_bytes[160019], keys[2][32], secret_keys[2][64];
static int allowed[2] = {1, 1}, published, staged, write_fault, changed;
static const char *fault;
static uint8_t captured_chunk[COOP_SAVE_CHUNK_HEAD + COOP_SAVE_CHUNK];
static size_t captured_n;
static int bind_save(void *u, int member, CoopSaveBinding *out) {
    Side *s = u; unsigned i = (unsigned)s->side;
    if (!allowed[i] || member != (i ? 1 : 2)) return 0;
    memset(out, 0, sizeof *out);
    memcpy(out->own, keys[i], 32); memcpy(out->peer, keys[1 - i], 32); memcpy(out->sk, secret_keys[i], 64);
    memset(out->room, 0x31, 16); out->epoch = 1;
    out->own_session = i ? 202 : 101; out->peer_session = i ? 101 : 202;
    return 1;
}
static int save_send(void *u, int member, uint8_t kind, const uint8_t id[16], uint64_t deadline, const void *bytes, size_t n) {
    Side *s = u;
    uint8_t data[COOP_SAVE_CHUNK_HEAD + COOP_SAVE_CHUNK];
    assert(n <= sizeof data); memcpy(data, bytes, n);
    if (!changed && kind == COOP_K_SAVE_OFFER && fault) {
        if (!strcmp(fault, "signature")) { data[320] ^= 1; changed = 1; }
        if (!strcmp(fault, "recipient")) { data[72] ^= 1; coop_sign(data + 288, secret_keys[0], data, 288); changed = 1; }
        if (!strcmp(fault, "size")) { memset(data + 156, 0xff, 4); coop_sign(data + 288, secret_keys[0], data, 288); changed = 1; }
    }
    if (fault && !strcmp(fault, "control-signature") && kind == COOP_K_SAVE_CONTROL && s->side == 1) data[100] ^= 1;
    if (kind == COOP_K_SAVE_CHUNK && s->side == 0) {
        if (!captured_n) memcpy(captured_chunk, data, n), captured_n = n;
        if (!changed && fault && (!strcmp(fault, "hash") || !strcmp(fault, "chunk"))) {
            if (!strcmp(fault, "hash")) data[n - 1] ^= 1;
            else data[8] ^= 1;
            changed = 1;
        }
    }
    return coop_sess_send_save_event(s->session, member, kind, id, deadline, data, n);
}
static void save_cancel_events(void *u, const uint8_t id[16]) { coop_sess_cancel_save_events(((Side *)u)->session, id); }
static int source_read(void *u, const char *id, unsigned f, uint32_t off, void *p, size_t n) {
    (void)u;
    assert(f < 2 && off + n <= (f ? 39447u : 120572u));
    return coop_profile_export_read(id, f, off, p, n);
}
static int stage_save(void *u, char id[33]) { (void)u; staged++; return coop_profile_stage(id); }
static int write_save(void *u, const char *id, unsigned f, uint32_t off, const void *p, size_t n) {
    (void)u;
    if (write_fault) { errno = ENOSPC; return 0; }
    return coop_profile_chunk(id, f, off, p, n);
}
static int read_save(void *u, const char *id, unsigned f, uint32_t off, void *p, size_t n) {
    (void)u; return coop_profile_read(id, f, off, p, n);
}
static int publish_save(void *u, int member, const char *id, const uint8_t manifest[COOP_SAVE_OFFER_BYTES],
                        const CoopSaveBinding *expected, uint64_t deadline, uint64_t expiry) {
    CoopSaveBinding current;
    assert(coop_profile_seal(id, manifest));
    if (fault && !strcmp(fault, "revoke-publish")) allowed[1] = 0;
    int ok = clock_ms < deadline && clock_ms < expiry && bind_save(u, member, &current) &&
             coop_save_same_binding(&current, expected) && coop_profile_publish(id);
    published += ok; return ok;
}
static void abandon_save(void *u, const char *id) { (void)u; coop_profile_abandon(id); }
static const CoopSaveOps save_ops = {bind_save, save_send, save_cancel_events, source_read, stage_save, write_save, read_save, publish_save, abandon_save};
static void recv_save(int from, uint64_t session, uint64_t event, const uint8_t *p, size_t n, void *user) {
    (void)event;
    uintptr_t value = (uintptr_t)user;
    unsigned i = (unsigned)(value >> 8); uint8_t kind = (uint8_t)value;
    coop_save_receive(side[i].save, from, session, kind, p, n, clock_ms, clock_ms);
}
static void download_grant(CoopSess *s, uint32_t mask) {
    CoopView v; coop_sess_view(s, &v); coop_sess_download_members(s, &v, mask);
}
int main(int argc, char **argv) {
    assert(argc == 3);
    fault = strcmp(argv[2], "ok") ? argv[2] : NULL;
    setup();
    assert(coop_profile_init(argv[1], NULL));
    grant(a, 0); grant(b, 0);
    download_grant(a, 1u << 2); download_grant(b, 1u << 1);
    assert(!coop_sess_member_accepted(a, 2) && !coop_sess_member_accepted(b, 1));
    assert(coop_sess_send_event(a, 2, COOP_K_TEST, NULL, 0) == COOP_EVENT_UNAVAILABLE);
    assert(!coop_sess_on_save_event(a, COOP_K_TEST, recv_save, NULL));
    for (unsigned i = 0; i < 2; i++) {
        uint8_t seed[32]; memset(seed, (int)i + 11, sizeof seed);
        coop_sign_keypair(seed, secret_keys[i], keys[i]);
        side[i].side = (int)i; side[i].session = i ? b : a;
        side[i].save = coop_save_new(&save_ops, &side[i]); assert(side[i].save);
        for (unsigned k = COOP_K_SAVE_OFFER; k <= COOP_K_SAVE_CHUNK; k++)
            assert(coop_sess_on_save_event(side[i].session, (uint8_t)k, recv_save, (void *)(uintptr_t)((i << 8) | k)));
    }
    for (unsigned i = 0; i < sizeof source_bytes; i++) source_bytes[i] = (uint8_t)(i * 29 + 3);
    CoopSaveSource src = {.size = {120572, 39447}, .generation = 5, .local_id = "authored"};
    CoopSaveSource oversized = src;
    oversized.size[0] = COOP_SAVE_MAX + 1;
    assert(!coop_save_offer(side[0].save, 2, &oversized, clock_ms, clock_ms));
    coop_blake2b(src.hash[0], 32, NULL, 0, source_bytes, src.size[0]);
    coop_blake2b(src.hash[1], 32, NULL, 0, source_bytes + src.size[0], src.size[1]);
    crypto_blake2b_ctx h; crypto_blake2b_init(&h, 32);
    uint8_t lengths[8]; for (unsigned i = 0; i < 8; i++) lengths[i] = (uint8_t)(src.size[i / 4] >> (8 * (i % 4)));
    crypto_blake2b_update(&h, lengths, 8); crypto_blake2b_update(&h, source_bytes, sizeof source_bytes); crypto_blake2b_final(&h, src.package);
    char root[1024];
    assert(snprintf(root, sizeof root, "%s/local-save", argv[1]) < (int)sizeof root);
    CoopSaveSource captured;
    assert(coop_profile_export(root, 5, 5, &captured));
    assert(!memcmp(captured.size, src.size, sizeof src.size) && !memcmp(captured.hash, src.hash, sizeof src.hash) &&
           !memcmp(captured.package, src.package, 32));
    src = captured;
    assert(coop_save_offer(side[0].save, 2, &src, clock_ms, clock_ms));
    int accepted = 0, interrupted = 0;
    for (int step = 0; step < 12000; step++) {
        CoopSaveView v;
        coop_save_view(side[1].save, &v);
        if (v.incoming == COOP_SAVE_OFFERED && !accepted) {
            uint8_t wrong[32]; memcpy(wrong, v.manifest_hash, 32); wrong[0] ^= 1;
            assert(!coop_save_accept(side[1].save, wrong, clock_ms, clock_ms) && !staged && !published);
            assert(coop_save_accept(side[1].save, v.manifest_hash, clock_ms, clock_ms)); accepted = 1;
            if (fault && !strcmp(fault, "disk-full")) write_fault = 1;
        }
        if (v.received_bytes && !interrupted && fault) {
            if (!strcmp(fault, "cancel")) coop_save_cancel(side[1].save, clock_ms), interrupted = 1;
            if (!strcmp(fault, "revoke")) allowed[1] = 0, download_grant(b, 0), interrupted = 1;
            if (!strcmp(fault, "deadline")) clock_ms += COOP_SAVE_DEADLINE_MS, interrupted = 1;
            if (!strcmp(fault, "duplicate")) {
                captured_chunk[captured_n - 1] ^= 1;
                coop_save_receive(side[1].save, 1, 101, COOP_K_SAVE_CHUNK, captured_chunk, captured_n, clock_ms, clock_ms);
                interrupted = 1;
            }
        }
        coop_save_tick(side[0].save, clock_ms, clock_ms); coop_save_tick(side[1].save, clock_ms, clock_ms);
        pump(8, fault && !strcmp(fault, "loss") ? 30 : 0, 1);
        CoopSaveView sent; coop_save_view(side[0].save, &sent); coop_save_view(side[1].save, &v);
        if (sent.outgoing == COOP_SAVE_VERIFIED || sent.outgoing == COOP_SAVE_CANCELLED) break;
    }
    CoopSaveView v, sent; coop_save_view(side[1].save, &v); coop_save_view(side[0].save, &sent);
    if (!fault || !strcmp(fault, "loss")) {
        assert(v.incoming == COOP_SAVE_VERIFIED && sent.outgoing == COOP_SAVE_VERIFIED && published == 1);
        assert(v.received_bytes == sizeof source_bytes && sent.sent_bytes == sizeof source_bytes && v.verified_id[0]);
        assert(!coop_profile_active() && !coop_profile_restart_token()[0]);
        printf("PASS save transfer: %zu bytes, 20 chunks, verified receipt, clock %llu ms, no native mount\n", sizeof source_bytes, (unsigned long long)(clock_ms - 1000));
    } else {
        if (!strcmp(fault, "signature") || !strcmp(fault, "recipient") || !strcmp(fault, "size")) assert(!staged && !accepted);
        assert(!published && v.incoming != COOP_SAVE_VERIFIED && !v.verified_id[0] && !coop_profile_active());
        printf("PASS save refusal: %s; no published or mounted partial\n", fault);
    }
    coop_save_free(side[0].save); coop_save_free(side[1].save);
    coop_sess_free(a); coop_sess_free(b); coop_udp_close(relay);
    return 0;
}
