"""Signed two-file transfer through real encrypted loopback events and native staging.
Authored fixture bytes only. --mutation signature|hash|bounds removes one guard;
the same behavioral tests must then fail (no source-string assertions).
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cc", "gcc"]
ROOT = Path(__file__).resolve().parents[1]
CASES = ("ok", "loss", "signature", "control-signature", "recipient", "size", "chunk", "hash", "duplicate", "cancel", "revoke", "revoke-publish", "deadline", "disk-full")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cc", default="gcc")
    p.add_argument("--source-root", type=Path, default=ROOT)
    p.add_argument("--mutation", choices=["signature", "hash", "bounds"])
    a = p.parse_args()
    root = a.source_root
    with tempfile.TemporaryDirectory(prefix="coop-save-") as t:
        tmp = Path(t)
        source = (root / "host/coop_save.c").read_text()
        if a.mutation == "signature":
            source = source.replace("!coop_sign_check(p + 288, b.peer, p, 288)", "0")
        elif a.mutation == "hash":
            source = source.replace("!verify_disk(s, t)", "0")
        elif a.mutation == "bounds":
            source = source.replace("!s || !src || !sizes(src)", "!s || !src")
        c = tmp / "save.c"
        c.write_text(source)
        exe = tmp / "save.exe"
        libs = ["-lws2_32", "-lbcrypt", "-liphlpapi"] if os.name == "nt" else ["-pthread"]
        cmd = [a.cc, "-std=c11", "-D_GNU_SOURCE", "-D_CRT_SECURE_NO_WARNINGS", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
               "-Wno-unused-function", "-I" + str(root / "host"), "-I" + str(ROOT / "tests"),
               str(ROOT / "tests/coop_save_test.c"), str(c), str(root / "host/coop_profile.c"),
               str(root / "host/coop_net.c"), str(root / "host/coop_proto.c"), str(root / "host/coop_relay.c"),
               str(root / "third_party/monocypher/monocypher.c"), "-o", str(exe)] + libs
        subprocess.run(cmd, check=True)
        for case in CASES:
            work = tmp / case
            work.mkdir()
            slot = work / "local-save/slot_05"
            slot.mkdir(parents=True)
            authored = bytes((i * 29 + 3) & 255 for i in range(160019))
            (slot / "progress.sav").write_bytes(authored[:120572])
            (slot / "caption.sav").write_bytes(authored[120572:])
            (work / "local-save/account.sav").write_bytes(b"local only")
            subprocess.run([str(exe), str(work), case], check=True, timeout=60)
            assert (slot / "progress.sav").read_bytes() + (slot / "caption.sav").read_bytes() == authored
            for export in (work / "coop-sessions").glob("*/export"):
                assert {f.name for f in export.iterdir()} == {"progress.sav", "caption.sav"}
        # The runtime adapter has a bounded socket-to-disk queue, no game headers.
        subprocess.run([a.cc, "-std=c11", "-D_GNU_SOURCE", "-D_CRT_SECURE_NO_WARNINGS", "-Wall", "-Wextra", "-Werror",
                        "-I" + str(root / "host"), "-fsyntax-only", str(root / "host/coop_save_runtime.c")], check=True)
    print("PASS: signed save transfer, 14 behavioral cases; runtime adapter compiled")


if __name__ == "__main__":
    main()
