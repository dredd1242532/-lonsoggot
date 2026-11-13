/* Signed checkpoint exchange over coop.h's reliable events. Callers serialize
   these functions on a disk worker, never the native game or socket thread. */
#pragma once
#include "coop_proto.h"
#include <stdint.h>
#include <stddef.h>

enum { COOP_SAVE_MAX = 1048576, COOP_SAVE_CHUNK = 8192, COOP_SAVE_CHUNKS = 128,
       COOP_SAVE_OFFER_BYTES = 352, COOP_SAVE_CONTROL_BYTES = 160, COOP_SAVE_CHUNK_HEAD = 64,
       COOP_SAVE_IDLE_MS = 15000, COOP_SAVE_DEADLINE_MS = 240000,
       COOP_SAVE_IDLE = 0, COOP_SAVE_OFFERED, COOP_SAVE_TRANSFERRING, COOP_SAVE_VERIFIED, COOP_SAVE_CANCELLED };
typedef struct CoopSaveBinding {
    uint8_t own[32], peer[32], sk[64], room[16];
    uint64_t own_session, peer_session;
    uint32_t epoch;
} CoopSaveBinding;
typedef struct CoopSaveSource {
    uint32_t size[2];
    uint8_t hash[2][32], package[32];
    uint64_t generation;
    char local_id[33];
} CoopSaveSource;
typedef struct {
    /* Fresh verified identity/download consent and charter on every operation. */
    int (*binding)(void *user, int member, CoopSaveBinding *out);
    int (*send)(void *user, int member, uint8_t kind, const uint8_t id[16], uint64_t deadline, const void *bytes, size_t n);
    void (*cancel)(void *user, const uint8_t id[16]);
    int (*source_read)(void *user, const char *local_id, unsigned file, uint32_t offset, void *out, size_t n);
    int (*stage)(void *user, char local_id[33]);
    int (*write)(void *user, const char *local_id, unsigned file, uint32_t offset, const void *bytes, size_t n);
    int (*read)(void *user, const char *local_id, unsigned file, uint32_t offset, void *out, size_t n);
    /* Retain the signed manifest, then recheck binding and both clocks immediately
       before the non-replacing rename (disk I/O may have blocked). */
    int (*publish)(void *user, int member, const char *local_id, const uint8_t manifest[COOP_SAVE_OFFER_BYTES],
                   const CoopSaveBinding *expected, uint64_t deadline_ms, uint64_t expiry_utc_ms);
    void (*abandon)(void *user, const char *local_id);
} CoopSaveOps;
typedef struct CoopSave CoopSave;
/* One allocation on local opt-in; no per-tick or per-chunk allocation. */
CoopSave *coop_save_new(const CoopSaveOps *ops, void *user);
void coop_save_free(CoopSave *s);
/* Local owner approval only; never called by receive(). The immutable source is
   already captured through the native save barrier. Returns 1 when offered. */
int coop_save_offer(CoopSave *s, int recipient, const CoopSaveSource *source, uint64_t now_ms, uint64_t utc_ms);
/* Exact displayed manifest hash; identity/download acceptance alone does not
   accept these bytes. Neither this nor a transport ACK mounts a save. */
int coop_save_accept(CoopSave *s, const uint8_t manifest_hash[32], uint64_t now_ms, uint64_t utc_ms);
void coop_save_cancel(CoopSave *s, uint64_t now_ms);
void coop_save_receive(CoopSave *s, int from, uint64_t session, uint8_t kind, const uint8_t *bytes, size_t n, uint64_t now_ms, uint64_t utc_ms);
void coop_save_tick(CoopSave *s, uint64_t now_ms, uint64_t utc_ms);
typedef struct {
    int incoming, outgoing, owner, recipient;
    uint32_t received_bytes, sent_bytes, total_bytes;
    uint8_t manifest_hash[32];
    char verified_id[33];
} CoopSaveView;
void coop_save_view(const CoopSave *s, CoopSaveView *out);
int coop_save_same_binding(const CoopSaveBinding *a, const CoopSaveBinding *b);
