"""Signed copy-room offers through the actual host tick/publication and save runtime.
Uses real temporary-profile storage, two signed engines and the save state machine.
The worker is stepped deterministically, not started; no game or native save parser.
--mutation removes one guard: it must compile and then fail a behavioral assertion.
"""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cc", "gcc"]
ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = ("copy-offer", "unidentified", "stale-terms", "manifest", "signature", "download-presence")


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
    spec = importlib.util.spec_from_file_location("oot_extract", ROOT / "tests/test_oot_coop.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    piece = module.piece
    host = (root / "host/oot_coop.c").read_text()
    state = (root / "host/coop_state.c").read_text()
    save = (root / "host/coop_save.c").read_text()
    if args.mutation == "copy-offer":
        before = piece(host, "static void on_tick(")
        after = replace_once(before, "    sync_on();", "    sync_on();\n    if (coop_profile_active()) { publish_none(); return; }")
        host = replace_once(host, before, after)
    elif args.mutation in ("unidentified", "stale-terms", "download-presence"):
        before = piece(state, "int coop_sync_download_accept(")
        if args.mutation == "unidentified":
            after = replace_once(before, " || o.unidentified", "")
        elif args.mutation == "stale-terms":
            after = replace_once(before, " || o.token != token", "")
        else:
            after = replace_once(before, "    s->download_token = token;", "    coop_sync_presence_accept(s, token);\n    s->download_token = token;")
        state = replace_once(state, before, after)
    elif args.mutation == "manifest":
        save = replace_once(save, " || memcmp(hash, t->hash, 32)", "")
    elif args.mutation == "signature":
        save = replace_once(save, "!coop_sign_check(p + 288, b.peer, p, 288)", "0")
    markers = ["enum { PUB_CONTRIBUTORS", "static int send_fn(", "static int presentation_locked(",
               "static int kit_consent_locked(", "static void gameplay_revoke_locked(", "static void publish_members(",
               "static void on_message(", "static int offer_of(", "static int key_accepted(", "static uint32_t shown_of(",
               "static void publish(", "int hos_oot_coop_presentation(", "static void publish_none(",
               "static void offer_update(", "int hos_oot_coop_offer(", "int hos_oot_coop_save_accept(",
               "int hos_oot_coop_save_binding(", "static void on_tick(", "static void gameplay_context_locked("]
    with tempfile.TemporaryDirectory(prefix="coop-save-copy-") as directory:
        tmp = Path(directory)
        (tmp / "coop_state.c").write_text(state)
        (tmp / "coop_save.c").write_text(save)
        (tmp / "host-pieces.c").write_text("\n".join(piece(host, marker) for marker in markers))
        exe = tmp / "save-copy.exe"
        libs = ["-lws2_32", "-lbcrypt", "-liphlpapi"] if os.name == "nt" else ["-pthread"]
        cmd = [args.cc, "-std=c11", "-D_GNU_SOURCE", "-D_CRT_SECURE_NO_WARNINGS", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
               "-I" + str(tmp), "-I" + str(root / "host"), '-DCOPY_SAVE_HOST="' + (tmp / "host-pieces.c").as_posix() + '"',
               str(ROOT / "tests/coop_save_copy_test.c"), str(tmp / "coop_save.c"), str(root / "host/coop_profile.c"),
               str(root / "host/coop_net.c"), str(root / "host/coop_proto.c"),
               str(root / "third_party/monocypher/monocypher.c"), "-o", str(exe), *libs]
        print("compile:", " ".join(cmd), flush=True)
        subprocess.run(cmd, check=True)
        print("compiled save-copy fixture", flush=True)
        user = tmp / "user"
        slot = user / "save/profile/slot_05"
        slot.mkdir(parents=True)
        (slot / "progress.sav").write_bytes(b"authored checkpoint" * 1024)
        (slot / "caption.sav").write_bytes(b"authored caption")
        (slot.parent / "account.sav").write_bytes(b"never transferred")

        def hashes(path):
            return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in path.rglob("*") if p.is_file()}

        before = hashes(user / "save")
        token = subprocess.check_output([str(exe), str(user), "prepare"], text=True).strip()
        subprocess.run([str(exe), str(user), "copy", token], check=True, timeout=60)
        assert hashes(user / "save") == before
        # Identical transfer behavior in an ordinary profile; no new restrictions or grants.
        subprocess.run([str(exe), str(user), "normal"], check=True, timeout=60)
        assert hashes(user / "save") == before
        print("PASS: both profile domains retain every original save byte", flush=True)


if __name__ == "__main__":
    main()
