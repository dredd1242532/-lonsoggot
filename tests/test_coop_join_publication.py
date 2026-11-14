"""Late native-engine creation must consume JOINED without relying on another view publication.
Real session JOINED/ROSTER/DATA parsing, queued loopback transport, signed identities/charters and host callbacks.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUN_ALL_ARGS = ["--cc", "gcc"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default=os.environ.get("CC", "gcc"))
    parser.add_argument("--source-root", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.source_root.resolve()
    spec = importlib.util.spec_from_file_location("oot_extract", ROOT / "tests/test_oot_coop.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    source = (root / "host/oot_coop.c").read_text()
    state = helper.piece(source, "static struct {")
    ending = "} G = {.lock = SRWLOCK_INIT, .pub_lock = SRWLOCK_INIT};"
    assert state.count(ending) == 1
    state = state.replace("static struct {", "typedef struct {", 1).replace(
        ending, "} Host;\nstatic Host hosts[2];\n#define G hosts[selected]")
    declarations = "\n".join((helper.piece(source, "typedef struct { uint32_t hash; int32_t value; uint32_t frame; int done; } TestSet;"),
                                helper.piece(source, "enum { PUB_CONTRIBUTORS"),
                                helper.piece(source, "enum { ST_UNSEEN"), state))
    markers = ["static int ensure_engine(", "static int send_fn(",
               "static int presentation_locked(", "static int kit_consent_locked(",
               "static void gameplay_revoke_locked(", "static void publish_members(", "static void on_message(",
               "static void view_changed(", "static void sync_on(", "static void on_view(",
               "static int offer_of(", "static int key_accepted(", "static uint32_t shown_of(", "static void publish(",
               "static void publish_none(", "static void offer_update(", "int hos_oot_coop_offer(",
               "int hos_oot_coop_accept(", "static void on_tick(", "static void gameplay_context_locked("]
    functions = "\n".join(helper.piece(source, marker) for marker in markers)
    with tempfile.TemporaryDirectory(prefix="coop-join-publication-") as directory:
        folder = Path(directory)
        (folder / "host-state.h").write_text(declarations)
        (folder / "host-functions.h").write_text(functions)
        exe = folder / "join.exe"
        libs = ["-lws2_32", "-lbcrypt", "-liphlpapi"] if os.name == "nt" else ["-pthread", "-lm"]
        command = [args.cc, "-std=gnu11", "-D_GNU_SOURCE", "-D_CRT_SECURE_NO_WARNINGS", "-O1", "-Wall", "-Wextra", "-Werror",
                   "-I" + str(folder), "-I" + str(root / "host"), str(ROOT / "tests/coop_join_publication_test.c"),
                   *[str(root / path) for path in ("host/coop_net.c", "host/coop_proto.c", "host/coop_relay.c",
                                                   "host/coop_state.c", "third_party/monocypher/monocypher.c")],
                   "-o", str(exe), *libs]
        print("COMPILE", " ".join(command), flush=True)
        subprocess.run(command, check=True)
        subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == "__main__":
    main()
