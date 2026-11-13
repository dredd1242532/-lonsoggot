"""Route-A gameplay owner: exact ABI, one writer, off path and revocable queued tickets."""
import argparse
from pathlib import Path
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cc", "gcc"]
ROOT = Path(__file__).resolve().parents[1]
FIXTURE = r'''
#include <assert.h>
#include <stdio.h>
#include "coop_gameplay.h"
static int calls, other_calls;
static int write_state(void *game, const void *payload) { calls++; *(int *)game = *(const int *)payload; return 1; }
static int other_write(void *game, const void *payload) { (void)game; (void)payload; other_calls++; return 1; }
static CoopGameplayContext context(void) {
    CoopGameplayContext c = {0};
    c.version = COOP_GAMEPLAY_VERSION; c.size = sizeof c;
    c.capabilities = COOP_GAMEPLAY_PRESENTATION | COOP_GAMEPLAY_DURABLE;
    c.offer = 9; c.rules_epoch = 3; c.room_session = 101; c.save_generation = 4; c.area_generation = 5;
    c.accepted_session[2] = 202;
    return c;
}
int main(void) {
    CoopGameplay owner = {0};
    CoopGameplayWriter writer = {COOP_GAMEPLAY_VERSION, sizeof writer, COOP_GAMEPLAY_BODIES,
                                COOP_GAMEPLAY_PRESENTATION, sizeof(int), write_state};
    CoopGameplayWriter bad = writer;
    bad.version++; assert(!coop_gameplay_register(&owner, &bad));
    bad = writer; bad.size--; assert(!coop_gameplay_register(&owner, &bad));
    assert(coop_gameplay_register(&owner, &writer));
    bad = writer; bad.write = other_write;
    assert(!coop_gameplay_register(&owner, &bad) && !coop_gameplay_register(&owner, &writer));
    CoopGameplayTicket ticket;
    int value = 0, payload = 17;
    assert(!coop_gameplay_ticket(&owner, COOP_GAMEPLAY_BODIES, 2, 202, &ticket));
    assert(!coop_gameplay_dispatch(&owner, &ticket, &payload, sizeof payload, &value));
    assert(!calls && !other_calls && !value); // disabled means no guest callback, not a hidden no-op guest write
    CoopGameplayContext c = context(); coop_gameplay_publish(&owner, &c);
    assert(!coop_gameplay_ticket(&owner, COOP_GAMEPLAY_BODIES, 2, 203, &ticket));
    assert(!coop_gameplay_ticket(&owner, COOP_GAMEPLAY_BODIES, 3, 202, &ticket));
    assert(coop_gameplay_ticket(&owner, COOP_GAMEPLAY_BODIES, 2, 202, &ticket));
    CoopGameplayTicket wrong = ticket;
    wrong.version++; assert(!coop_gameplay_dispatch(&owner, &wrong, &payload, sizeof payload, &value));
    wrong = ticket; wrong.size--; assert(!coop_gameplay_dispatch(&owner, &wrong, &payload, sizeof payload, &value));
    assert(!coop_gameplay_dispatch(&owner, &ticket, &payload, sizeof payload - 1, &value));
    assert(!calls && !other_calls);
    assert(coop_gameplay_dispatch(&owner, &ticket, &payload, sizeof payload, &value));
    assert(value == 17 && calls == 1 && other_calls == 0);
    for (int change = 0; change < 6; change++) {
        c = context(); coop_gameplay_publish(&owner, &c);
        assert(coop_gameplay_ticket(&owner, COOP_GAMEPLAY_BODIES, 2, 202, &ticket));
        switch (change) {
        case 0: c.accepted_session[2] = 0; break;
        case 1: c.room_session++; break;
        case 2: c.rules_epoch++; break;
        case 3: c.save_generation++; break;
        case 4: c.area_generation++; break;
        default: c.capabilities = 0; break;
        }
        coop_gameplay_publish(&owner, &c);
        assert(!coop_gameplay_dispatch(&owner, &ticket, &payload, sizeof payload, &value));
        c = context(); coop_gameplay_publish(&owner, &c);
        assert(!coop_gameplay_dispatch(&owner, &ticket, &payload, sizeof payload, &value)); // reaccept cannot revive it
    }
    assert(calls == 1);
    assert(coop_gameplay_ticket(&owner, COOP_GAMEPLAY_BODIES, 2, 202, &ticket));
    c.version++; coop_gameplay_publish(&owner, &c);
    assert(!coop_gameplay_dispatch(&owner, &ticket, &payload, sizeof payload, &value));
    c = context(); c.size--; coop_gameplay_publish(&owner, &c);
    assert(!coop_gameplay_ticket(&owner, COOP_GAMEPLAY_BODIES, 2, 202, &ticket));
    c = context(); c.capabilities = 8; coop_gameplay_publish(&owner, &c);
    assert(!coop_gameplay_ticket(&owner, COOP_GAMEPLAY_BODIES, 2, 202, &ticket));
    assert(calls == 1 && !other_calls);
    puts("PASS: owner off makes no guest calls; identity/room/rules/save/area revoke queued work; version/size fail closed; exactly one writer");
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc")
    parser.add_argument("--source-root", type=Path, default=ROOT)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="coop-gameplay-") as name:
        folder = Path(name)
        source, exe = folder / "fixture.c", folder / "fixture.exe"
        source.write_text(FIXTURE)
        subprocess.run([args.cc, "-std=c11", "-O1", "-Wall", "-Wextra", "-Werror", "-I" + str(args.source_root / "host"),
                        str(source), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
