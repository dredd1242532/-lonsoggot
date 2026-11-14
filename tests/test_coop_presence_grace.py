"""Body-only stall grace through the real native gate, pose policy, body adapter and capability owner.
--source-root pins production sources for failing-before checks; --case selects one deterministic boundary.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUN_ALL_ARGS = ["--cc", "gcc"]
STALLS = tuple(f"{kind}-{seconds}" for kind in ("clock", "pose", "peer") for seconds in (3, 9, 11))
IMMEDIATE = ("loading", "transition0", "transition1", "transition2", "transition3", "save", "title", "unloaded",
             "player", "origin", "leave", "member", "replacement", "rules", "permission", "consent", "copy", "return", "remote-map", "remote-load")
CASES = STALLS + tuple(f"immediate-{case}-{held}" for held in (0, 9) for case in IMMEDIATE) + ("no-new-clock", "no-new-peer", "failed-create", "clock-jumps")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default=os.environ.get("CC", "gcc"))
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--case", choices=CASES)
    args = parser.parse_args()
    source = args.source_root.resolve()
    spec = importlib.util.spec_from_file_location("oot_extract", ROOT / "tests/test_oot_coop.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    coop = (source / "host/oot_coop.c").read_text()
    gate = "\n".join(helper.piece(coop, marker) for marker in (
        "#define PLAYER_PTR", "#define F_PLAY_CLOCK", "#define F_LOADING", "enum {\n    C_BOOL",
        "static const uint32_t transition_flags[]", "static uint64_t entry_of(", "static void unbind(",
        "static int player_in_world(", "static volatile int title_starts;", "void hos_oot_coop_rom_open(", "static int gate("))
    players = (source / "host/oot_coop_players.c").read_text()
    players = players[players.index('#define PLAYERS_BUILD "'):]
    with tempfile.TemporaryDirectory(prefix="coop-presence-grace-") as name:
        folder = Path(name)
        (folder / "players-body.h").write_text(players)
        (folder / "gate-body.h").write_text(gate)
        exe = folder / "grace.exe"
        command = [args.cc, "-std=gnu11", "-D_CRT_SECURE_NO_WARNINGS", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                   "-Wno-unused-function", "-I" + str(folder), "-I" + str(source / "host"), "-I" + str(ROOT / "tests"),
                   '-DOOT_COOP_PLAYERS_BODY="players-body.h"', str(ROOT / "tests/coop_presence_grace_test.c"),
                   str(source / "host/coop_players.c"), str(source / "host/coop_proto.c"), str(source / "host/coop_state.c"),
                   str(source / "third_party/monocypher/monocypher.c"), "-lm", "-o", str(exe)]
        print("COMPILE", " ".join(command), flush=True)
        subprocess.run(command, check=True)
        failed = []
        for case in (args.case,) if args.case else CASES:
            result = subprocess.run([str(exe), case], text=True, capture_output=True, timeout=20)
            print(result.stdout, end="")
            print(result.stderr, end="")
            print(f"CASE {case}: exit {result.returncode}", flush=True)
            if result.returncode:
                failed.append(case)
        if failed:
            raise SystemExit("FAIL: " + ", ".join(failed))


if __name__ == "__main__":
    main()
