"""Temporary-copy presentation consent through the real signed engine, host owner and POS arrival.
--mutation deliberately breaks one boundary; successful compilation followed by a failing assertion is required.
No game files, game build, GPU or native save access.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cc", "gcc"]
ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = ("carry", "download", "revoke", "return", "durable", "consent", "position", "chat")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def replace_once(text, before, after):
    assert text.count(before) == 1, before
    return text.replace(before, after, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc")
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--mutation", choices=MUTATIONS)
    args = parser.parse_args()
    root = args.source_root.resolve()
    piece = load("oot_extract", ROOT / "tests/test_oot_coop.py").piece
    with tempfile.TemporaryDirectory(prefix="coop-copy-presence-") as directory:
        folder = Path(directory)
        if args.mutation == "chat":
            text = (root / "host/coop.c").read_text()
            text = replace_once(text, "(presentation_kind(kind) && presence_locked(s, member))",
                                "(presentation_kind(kind) && (presence_locked(s, member) || member == 1))")
            (folder / "coop.c").write_text(text)
            events = load("event_fixture", ROOT / "tests/test_coop_events.py")
            exe = events.compile_fixture(args.cc, root, folder)
            print("compiled copy-presence mutation fixture", flush=True)
            subprocess.run([str(exe), "presence-scope"], check=True, timeout=60)
            return
        engine = (root / "host/coop_state.c").read_text()
        host = (root / "host/oot_coop.c").read_text()
        players = (root / "host/oot_coop_players.c").read_text()
        if args.mutation == "carry":
            engine = replace_once(engine, "s->accepted = s->have_pin = s->nacc = s->copy = s->copy_wanted = 0;",
                                  "s->copy_wanted = 0;")
        elif args.mutation == "download":
            marker = "s->download_token = token;"
            engine = replace_once(engine, marker, "coop_sync_presence_accept(s, token);\n    " + marker)
        elif args.mutation == "revoke":
            before = piece(engine, "void coop_sync_presence_revoke(")
            engine = replace_once(engine, before, "void coop_sync_presence_revoke(CoopSync *s) { (void)s; }")
        elif args.mutation == "return":
            before = piece(engine, "void coop_sync_temporary(")
            after = replace_once(before, "coop_sync_presence_revoke(s);", "if (active) coop_sync_presence_revoke(s);")
            engine = replace_once(engine, before, after)
        elif args.mutation == "durable":
            host = replace_once(host, "context.capabilities = temporary ? 0 : COOP_GAMEPLAY_DURABLE;",
                                "context.capabilities = COOP_GAMEPLAY_DURABLE;")
        elif args.mutation == "consent":
            host = replace_once(host, "return o->presence_accepted;", "return 1;")
        elif args.mutation == "position":
            players = replace_once(players,
                "if (!hos_oot_coop_gameplay_ticket(COOP_GAMEPLAY_BODIES, (unsigned)from, &ticket)) return;",
                "memset(&ticket, 0, sizeof ticket); (void)hos_oot_coop_gameplay_ticket(COOP_GAMEPLAY_BODIES, (unsigned)from, &ticket);")
        (folder / "coop_state.c").write_text(engine)
        markers = ["static int presentation_locked(", "static int kit_consent_locked(",
                   "static void gameplay_revoke_locked(", "static void publish_members(",
                   "static int offer_of(", "int hos_oot_coop_presentation(", "int hos_oot_coop_gameplay_ticket(",
                   "int hos_oot_coop_gameplay_dispatch(", "int hos_oot_coop_presence_accept(", "int hos_oot_coop_accept(",
                   "static void gameplay_context_locked("]
        production = "\n".join(piece(host, marker) for marker in markers)
        production += "\n" + piece(players, "static int receive_pose(") + "\n" + piece(players, "static void on_pos(")
        extracted = folder / "host-pieces.c"
        extracted.write_text(production)
        exe = folder / "copy-presence.exe"
        flags = ["-D_CRT_SECURE_NO_WARNINGS"]
        if os.name != "nt": flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=undefined"]
        command = [args.cc, "-std=c11", "-O1", "-g", "-Wall", "-Wextra", "-Werror", *flags,
                   "-I" + str(folder), "-I" + str(root / "host"),
                   '-DCOPY_PRESENCE_HOST="' + extracted.as_posix() + '"',
                   str(ROOT / "tests/coop_copy_presence_test.c"), str(root / "host/coop_proto.c"),
                   str(root / "host/coop_players.c"), str(root / "third_party/monocypher/monocypher.c"), "-lm", "-o", str(exe)]
        print("compile:", " ".join(command), flush=True)
        subprocess.run(command, check=True)
        print("compiled copy-presence fixture", flush=True)
        subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == "__main__":
    main()
