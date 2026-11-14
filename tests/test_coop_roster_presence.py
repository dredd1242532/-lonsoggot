"""Repeated relay rosters must not revoke a live presentation context between view callbacks.
Executes the real session publication and extracted native-context functions without a game.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUN_ALL_ARGS = ["--cc", "gcc"]
FIXTURE = r'''
#include "coop.c"
#include "coop_state.h"
#include "coop_gameplay.h"
#include <assert.h>

void hos_log(const char *format, ...) { (void)format; }
int hos_ini_option(const char *key, int otherwise) { (void)key; return otherwise; }
int hos_ini_text(const char *key, char *out, size_t cap) { (void)key; (void)out; (void)cap; return 0; }
static CoopSess *session;
static int temporary, callbacks, expected_change, gap_seen;
static struct {
    CoopSync *eng;
    CoopView view;
    CoopGameplay gameplay;
    int bound, gate, loaded, presence;
    uint64_t save_generation, area_generation, kit_session, kit_generation;
    uint32_t kit_offer, kit_epoch;
} G;
static CoopOffer offer;
int coop_profile_active(void) { return temporary; }
static void fixture_view_get(CoopView *out) { coop_sess_view(session, out); }
void coop_sync_temporary(CoopSync *engine, int active) { (void)engine; (void)active; }
int coop_sync_offer(CoopSync *engine, CoopOffer *out, uint64_t now) {
    (void)engine; (void)now; *out = offer; return 1;
}
int coop_sync_open(CoopSync *engine) { (void)engine; return offer.accepted; }
int coop_sync_member_accepted(const CoopSync *engine, int id, uint64_t serial) {
    (void)engine; return id == 2 && serial == 202 && offer.accepted;
}
uint32_t coop_sync_presence_members(CoopSync *engine) {
    (void)engine; return offer.presence_accepted ? 1u << 2 : 0;
}
int coop_feature_present(const CoopRules *rules, int id, int *value) {
    (void)rules;
    if (id != COOP_RULE_PRESENCE) return 0;
    *value = COOP_PRESENCE_SUPPORTED; return 1;
}
#define coop_view_get fixture_view_get
@@CONTEXT@@
#undef coop_view_get

static int body_write(void *game, const void *payload) { (void)game; (void)payload; return 1; }

/* Force a game-thread refresh after publish's view_rebuild and before the sync
   consumer installs G.view. This is a deterministic legal interleaving, not a sleep. */
static void before_sync(const CoopView *view, void *user) {
    (void)user;
    callbacks++;
    gameplay_context_locked();
    if (!G.presence) gap_seen++;
    if (!expected_change && !G.presence) {
        fprintf(stderr, "unexpected presence revoke: live version %u, cached %u, unchanged roster\n",
                view->version, G.view.version);
        exit(1);
    }
    if (expected_change) assert(!G.presence); /* real changes still fail closed before acknowledgement */
    G.view = *view;
}

static void reset_context(void) {
    memset(&G, 0, sizeof G);
    G.eng = (CoopSync *)(uintptr_t)1;
    G.bound = !temporary; G.gate = G.loaded = 1;
    G.save_generation = G.area_generation = 1;
    coop_sess_view(session, &G.view);
    offer = (CoopOffer){.token = 9, .accepted = !temporary, .presence_accepted = temporary};
    offer.rules.epoch = 3;
    const CoopGameplayWriter writer = {COOP_GAMEPLAY_VERSION, sizeof writer, COOP_GAMEPLAY_BODIES,
                                      COOP_GAMEPLAY_PRESENTATION, sizeof(int), body_write};
    assert(coop_gameplay_register(&G.gameplay, &writer));
    gameplay_context_locked();
    assert(G.presence && G.gameplay.context.accepted_session[2] == 202);
    callbacks = gap_seen = expected_change = 0;
}

static void received(const CoopRoster *roster, uint32_t seq, uint64_t now) {
    uint8_t payload[512], bytes[COOP_MAX_DGRAM];
    const size_t n = coop_roster_pack(payload, sizeof payload, roster);
    CoopHdr header = {.ver = COOP_PROTO, .type = COOP_T_ROSTER, .room = session->room, .seq = seq};
    const size_t size = coop_dgram_pack(bytes, sizeof bytes, &header, payload, n, session->locator);
    const uint8_t *body;
    assert(n && size && coop_dgram_parse(bytes, size, &header, &body));
    assert(on_roster(session, bytes, size, &header, body, now));
    publish(session);
}

int main(void) {
    const CoopParams params = {.name = "fixture"};
    session = coop_sess_new(&params); assert(session);
    session->state = COOP_JOINED;
    session->self = 1; session->session = session->room_session = 101; session->room = 7;
    memcpy(session->secret, "H7K3WMBCDFGH234567", COOP_SECRET_LEN + 1);
    coop_room_keys(session->secret, session->locator, session->msg_key);
    CoopRoster original = {.n = 2, .m = {{.id = 1, .flags = COOP_FLAG_MAKER, .session = 101, .name = "A"},
                                        {.id = 2, .session = 202, .name = "B"}}};
    assert(!apply_roster(session, &original)); publish(session);
    assert(coop_sess_on_view(session, before_sync, NULL));
    for (temporary = 0; temporary <= 1; temporary++) {
        reset_context();
        CoopGameplayTicket ticket;
        assert(coop_gameplay_ticket(&G.gameplay, COOP_GAMEPLAY_BODIES, 2, 202, &ticket));
        const unsigned version = session->view.version;
        const uint64_t generation = G.gameplay.generation;
        const uint32_t seq = session->roster_seq + 1;
        received(&original, seq, 10000);
        gameplay_context_locked();
        assert(session->roster_seq == seq && session->roster_at == 10000); /* heartbeat freshness retained */
        assert(session->view.version == version && !callbacks && !gap_seen && G.presence);
        assert(G.gameplay.generation == generation);
        int pose = 0;
        assert(coop_gameplay_dispatch(&G.gameplay, &ticket, &pose, sizeof pose, NULL));

        /* Struct padding, unused slots and bytes after each name's NUL are not roster changes. */
        CoopRoster same;
        memset(&same, 0xa5, sizeof same); same.n = original.n;
        for (int i = 0; i < same.n; i++) {
            same.m[i].id = original.m[i].id; same.m[i].flags = original.m[i].flags;
            same.m[i].session = original.m[i].session; strcpy(same.m[i].name, original.m[i].name);
        }
        assert(!apply_roster(session, &same)); publish(session);
        assert(session->view.version == version && !callbacks && G.presence);
        puts("PASS: repeated roster keeps presence, body ticket and view generation; heartbeat clock advances");

        for (int change = 0; change < 10; change++) {
            session->roster = original; session->dirty = 1; session->n_hooks = 0; publish(session);
            session->n_hooks = 1; reset_context(); expected_change = 1;
            assert(coop_gameplay_ticket(&G.gameplay, COOP_GAMEPLAY_BODIES, 2, 202, &ticket));
            CoopRoster changed = original;
            if (change == 0) strcpy(changed.m[1].name, "renamed");
            if (change == 1) changed.m[1].flags |= COOP_FLAG_AWAY;
            if (change == 2) changed.m[0].flags = 0, changed.m[1].flags = COOP_FLAG_MAKER;
            if (change == 3) changed.m[1].session++;
            if (change == 4) changed.m[1].id = 3;
            if (change == 5) changed.n = 1;
            if (change == 6) changed.n = 3, changed.m[2] = (CoopMemberInfo){.id = 3, .session = 303, .name = "C"};
            if (change == 7) changed.m[0] = original.m[1], changed.m[1] = original.m[0];
            if (change == 8) strcpy(changed.m[0].name, "own name");
            if (change == 9) changed.m[0].session++;
            const unsigned old_version = session->view.version;
            assert(apply_roster(session, &changed) == (change == 9)); publish(session);
            assert(session->view.version == old_version + 1 && callbacks == 1 && gap_seen == 1 && !G.presence);
            assert(!coop_gameplay_dispatch(&G.gameplay, &ticket, &pose, sizeof pose, NULL));
        }
        session->roster = original; session->dirty = 1; session->n_hooks = 0; publish(session);
        session->n_hooks = 1; reset_context(); expected_change = 1;
        /* An unchanged roster must not erase publication already requested by a separate state/status change. */
        set_status(session, "%s", temporary ? "copy status change" : "normal status change");
        assert(!apply_roster(session, &original)); publish(session);
        assert(callbacks == 1 && !G.presence && gap_seen == 1);
        printf("PASS: %s context still revokes on all ten real roster changes and pending status publication\n",
               temporary ? "copy" : "normal");
    }
    coop_sess_free(session);
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default=os.environ.get("CC", "gcc"))
    parser.add_argument("--source-root", type=Path, default=ROOT)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("oot_fixture", ROOT / "tests/test_oot_coop.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    source = (args.source_root / "host/oot_coop.c").read_text()
    context = "\n".join(helper.piece(source, marker) for marker in (
        "static int presentation_locked(", "static int kit_consent_locked(", "static void gameplay_context_locked("))
    with tempfile.TemporaryDirectory(prefix="coop-roster-presence-") as temp:
        folder = Path(temp)
        fixture, exe = folder / "fixture.c", folder / "fixture.exe"
        fixture.write_text(FIXTURE.replace("@@CONTEXT@@", context))
        libs = ["-lws2_32", "-lbcrypt", "-liphlpapi"] if os.name == "nt" else ["-pthread", "-lm"]
        sources = [args.source_root / path for path in ("host/coop_net.c", "host/coop_proto.c", "host/coop_relay.c",
                                                      "third_party/monocypher/monocypher.c")]
        cmd = [args.cc, "-std=gnu11", "-D_GNU_SOURCE", "-D_CRT_SECURE_NO_WARNINGS", "-O1", "-Wall", "-Wextra", "-Werror",
               "-I" + str(args.source_root / "host"), str(fixture), *map(str, sources), "-o", str(exe), *libs]
        print("COMPILE", " ".join(cmd), flush=True)
        subprocess.run(cmd, check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
