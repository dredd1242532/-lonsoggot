"""Co-op's other players, without the game (host/coop_players.c, host/oot_coop_players.c).

Part 1, host/coop_players.c compiled as it is: a position message (POS) is taken only from a member the room's roster
lists (not this player's own id), when it parses (version, printable names, finite numbers), names a place a map can
have and is newer than the last one taken from that member in that session; a member is shown only while the player
accepted it, it was heard within 3 s, its Link stands in a map and that map is this game's; between two positions the
place shown lies between them, never between a place and a message without one; a member that left, or another
session under its id, starts from nothing; this game's own position goes out at most 15 times a second and only with
others in the room. The low rate: a game that shows nobody (COOP_POS_SLOW), or whose Link stands in no map, sends one
message in 2 s and at once at a change of map or flags; a member whose game shows nobody is never shown, and starts its
places anew when it switches showing on; each member's last message is kept for the Co-op tab (coop_players_last).

Part 2, host/oot_coop_players.c from its first constant to its end, on a fake guest memory (tests/
oot_coop_players_test.c): switched off, it registers all the same, says this game's map and that a save is played
(no place) every 2 s and at once at a map change, keeps a member's message for the tab's reader and makes nothing;
until co-op is on and a save is played Link's update reads nothing; the present's tick says the title screen (no map)
and a load (the last map) at once, and an event that holds Link for 2.5 s; off with one line on another
build of the game or without one of the game's routines; the game's create request called with the manager, the name,
the placement and the stack words; the actor taken once made, placed every frame, deleted when the member changes map,
falls silent or leaves, and when this game stops being played; nothing sent, made, moved or deleted while the game is
not played or an event plays; a request never made cancelled; an actor the game removed itself not deleted again; the
counts of what was made and deleted; the guest's registers as they were after every call; and the test's made-up
member fed from a file.

The production body adapter and codec are executed below, not judged by source-text assertions. Hook placement
and generated resources still need the current-head production build and the separate two-game proof.

    python -I -B tests/test_coop_players.py [--cc gcc]
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

RUN_ALL_ARGS = ["--cc", "gcc"]

ROOT = Path(__file__).resolve().parents[1]

STEPS = """# seconds what
0 actor Test_Actor
0 at = 1 0 2 0
2 at = 1 0 2 0
4 at = 3 0 4 90
5 at Elsewhere 0 0 0 0
6 gone
12 abs Forest 30 6 40 90
13 abs Forest 30 6 40 90
14 abs Forest 30 6 44 90
15 gone
a line that is not a step
"""




def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cc", default=os.environ.get("CC") or shutil.which("gcc") or shutil.which("clang") or "gcc")
    p.add_argument("--source-root", type=Path, default=ROOT)
    a = p.parse_args()
    host = a.source_root.resolve() / "host"
    players = (host / "oot_coop_players.c").read_text(encoding="utf-8")
    marker = '#define PLAYERS_BUILD "'
    if players.count(marker) != 1:
        print("FAIL: %r is not once in host/oot_coop_players.c" % marker)
        return 1
    with tempfile.TemporaryDirectory(prefix="hookshot-coop-players-") as temp:
        temp = Path(temp)
        body = temp / "oot_coop_players_body.c"
        body.write_text(players[players.index(marker):], encoding="utf-8")
        steps = temp / "steps.txt"
        steps.write_text(STEPS, encoding="utf-8")
        exe = temp / "oot_coop_players_test.exe"
        base = [a.cc, "-std=gnu11", "-D_CRT_SECURE_NO_WARNINGS", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-I" + str(host),
                '-DOOT_COOP_PLAYERS_BODY="%s"' % body.as_posix(), str(ROOT / "tests" / "oot_coop_players_test.c"),
                str(host / "coop_players.c"), str(host / "coop_proto.c"), str(ROOT / "third_party" / "monocypher" / "monocypher.c"),
                "-lm", "-o", str(exe)]
        tries = [["-fsanitize=address,undefined", "-fno-sanitize-recover=undefined"], []] if os.name != "nt" else [[]]
        for i, extra in enumerate(tries):
            r = subprocess.run(base + extra, capture_output=True, text=True, timeout=300)
            if r.returncode == 0:
                print("compiled%s" % (" with " + " ".join(extra) if extra else ""))
                break
            if i + 1 == len(tries):
                print(r.stdout + r.stderr)
                print("FAIL: the fixture does not compile")
                return 1
        r = subprocess.run([str(exe), str(steps)], capture_output=True, text=True, timeout=300)
        sys.stdout.write(r.stdout + r.stderr)
        if r.returncode:
            print("FAIL: tests/oot_coop_players_test.c exited %d" % r.returncode)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
