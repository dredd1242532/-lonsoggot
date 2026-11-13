"""A sender-chosen transfer ID cannot hide a signed cancellation of the other direction.
Uses the real save state machine and signatures; no save is accepted or written.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cc", "gcc"]
ROOT = Path(__file__).resolve().parents[1]
FIXTURE = r'''
#include "coop_save.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static CoopSaveBinding bound;
static uint8_t other_sk[64], offer[COOP_SAVE_OFFER_BYTES];
static unsigned captured, disk_calls;
static int binding(void *u, int member, CoopSaveBinding *out) { (void)u; *out = bound; return member == 2; }
static int send_event(void *u, int member, uint8_t kind, const uint8_t id[16], uint64_t deadline, const void *p, size_t n) {
    (void)u; (void)id; (void)deadline;
    assert(member == 2);
    if (kind == COOP_K_SAVE_OFFER) { assert(n == sizeof offer); memcpy(offer, p, n); captured++; }
    return 1;
}
static void cancel_event(void *u, const uint8_t id[16]) { (void)u; (void)id; }
static int source_read(void *u, const char *id, unsigned file, uint32_t off, void *p, size_t n) {
    (void)u; (void)id; (void)file; (void)off; (void)p; (void)n; disk_calls++; return 0;
}
static int stage(void *u, char id[33]) { (void)u; (void)id; disk_calls++; return 0; }
static int write_data(void *u, const char *id, unsigned file, uint32_t off, const void *p, size_t n) {
    (void)u; (void)id; (void)file; (void)off; (void)p; (void)n; disk_calls++; return 0;
}
static int publish(void *u, int member, const char *id, const uint8_t manifest[COOP_SAVE_OFFER_BYTES],
                   const CoopSaveBinding *expected, uint64_t deadline, uint64_t expiry) {
    (void)u; (void)member; (void)id; (void)manifest; (void)expected; (void)deadline; (void)expiry; disk_calls++; return 0;
}
static void abandon(void *u, const char *id) { (void)u; (void)id; disk_calls++; }
int main(void) {
    uint8_t seed[32] = {1}; coop_sign_keypair(seed, bound.sk, bound.own);
    seed[0] = 2; coop_sign_keypair(seed, other_sk, bound.peer);
    bound.own_session = 101; bound.peer_session = 202; bound.epoch = 1; memset(bound.room, 3, 16);
    CoopSaveOps ops = {binding, send_event, cancel_event, source_read, stage, write_data, source_read, publish, abandon};
    CoopSave *s = coop_save_new(&ops, NULL); assert(s);
    CoopSaveSource source = {.size = {1, 1}, .generation = 1, .local_id = "authored"};
    assert(coop_save_offer(s, 2, &source, 1000, 1000)); coop_save_tick(s, 1000, 1000); assert(captured == 1);
    /* The peer signs its own valid offer with the same transfer ID, but opposite
       owner/recipient identities and sessions. Neither identity is forged. */
    memcpy(offer + 40, bound.peer, 32); memcpy(offer + 72, bound.own, 32);
    uint8_t tmp[8]; memcpy(tmp, offer + 104, 8); memcpy(offer + 104, offer + 112, 8); memcpy(offer + 112, tmp, 8);
    coop_sign(offer + 288, other_sk, offer, 288);
    coop_save_receive(s, 2, 202, COOP_K_SAVE_OFFER, offer, sizeof offer, 1000, 1000);
    CoopSaveView view; coop_save_view(s, &view);
    assert(view.incoming == COOP_SAVE_OFFERED && view.outgoing == COOP_SAVE_OFFERED);
    uint8_t control[COOP_SAVE_CONTROL_BYTES] = {0};
    memcpy(control, offer, 24); control[23] = 2; control[24] = 4;
    memcpy(control + 32, offer + 24, 16); memcpy(control + 48, view.manifest_hash, 32);
    coop_sign(control + 96, other_sk, control, 96);
    coop_save_receive(s, 2, 202, COOP_K_SAVE_CONTROL, control, sizeof control, 1000, 1000);
    coop_save_view(s, &view);
    assert(view.incoming == COOP_SAVE_CANCELLED && view.outgoing == COOP_SAVE_OFFERED && !disk_calls);
    coop_save_free(s);
    puts("PASS: signed cancellation routes by manifest, not a colliding owner-selected transfer ID");
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cc", default="gcc")
    p.add_argument("--source-root", type=Path, default=ROOT)
    a = p.parse_args()
    root = a.source_root
    with tempfile.TemporaryDirectory(prefix="coop-save-control-") as temporary:
        tmp = Path(temporary)
        c = tmp / "control.c"
        c.write_text(FIXTURE)
        exe = tmp / "control.exe"
        libs = ["-lws2_32", "-lbcrypt", "-liphlpapi"] if os.name == "nt" else ["-pthread"]
        subprocess.run([a.cc, "-std=c11", "-D_GNU_SOURCE", "-D_CRT_SECURE_NO_WARNINGS", "-O1", "-Wall", "-Wextra", "-Werror",
                        "-I" + str(root / "host"), str(c), str(root / "host/coop_save.c"), str(root / "host/coop_net.c"),
                        str(root / "host/coop_proto.c"), str(root / "third_party/monocypher/monocypher.c"),
                        "-o", str(exe)] + libs, check=True)
        subprocess.run([str(exe)], check=True, timeout=30)


if __name__ == "__main__":
    main()
