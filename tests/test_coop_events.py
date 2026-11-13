"""Game-free reliable session events over real sealed loopback datagrams and a simulated relay clock.
No loss, 30 percent loss, reordering, duplicate/new envelopes, missing fragments, queue pressure,
member departure, consent/session binding, codec rejection and concurrent callers. Linux runs use
ASan/UBSan and a separate ThreadSanitizer build, as test_coop_session.py does.

python -I -B tests/test_coop_events.py [--cc gcc] [--source-root path/to/other/source]
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cc", "gcc"]
ROOT = Path(__file__).resolve().parents[1]
CASES = ("no-loss", "loss", "reorder", "parts", "duplicate", "queue", "acceptance", "codec", "threads",
         "cancel", "cancel-accept", "reset-ack", "delayed-ack", "reaccept-step", "save-scope", "presence-scope", "counters")


def compile_fixture(cc, source, folder, tsan=False, mutation=None):
    exe = folder / ("events-tsan.exe" if tsan else "events.exe")
    if os.name == "nt":
        flags, libs = ["-D__USE_MINGW_ANSI_STDIO=1", "-D_CRT_SECURE_NO_WARNINGS"], ["-lws2_32", "-lbcrypt", "-liphlpapi"]
    else:
        flags = ["-fsanitize=thread"] if tsan else ["-fsanitize=address,undefined", "-fno-sanitize-recover=undefined"]
        libs = ["-pthread"]
    mono = folder / ("mono-tsan.o" if tsan else "mono.o")
    if mutation:
        text = (source / "host/coop.c").read_text()
        if mutation == "deadline":
            text = text.replace("if (e->deadline && now >= e->deadline)", "if (0 && e->deadline && now >= e->deadline)")
        elif mutation == "scope":
            text = text.replace("if (!save_kind(kind)) return accepted_locked(s, member) || (presentation_kind(kind) && presence_locked(s, member));",
                                "(void)kind; return accepted_locked(s, member);")
        elif mutation == "cancel":
            text = text.replace("if (e->id && save_kind(e->kind) && !memcmp(e->transfer, transfer, 16)) event_out_discard(s, e);",
                                "if (0 && e->id && save_kind(e->kind) && !memcmp(e->transfer, transfer, 16)) event_out_discard(s, e);")
        (folder / "coop.c").write_text(text)
    commands = [
        [cc, "-std=c99", "-O2", "-w", "-c", str(source / "third_party/monocypher/monocypher.c"), "-o", str(mono)] + flags,
        [cc, "-std=c11", "-D_GNU_SOURCE", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-I" + str(folder), "-I" + str(source / "host"),
         str(ROOT / "tests/coop_events_test.c"), str(source / "host/coop_net.c"), str(source / "host/coop_proto.c"),
         str(source / "host/coop_relay.c"), str(mono), "-o", str(exe)] + flags + libs,
    ]
    for command in commands:
        r = subprocess.run(command, capture_output=True, text=True, timeout=300)
        if r.returncode:
            print(r.stdout + r.stderr)
            if tsan:
                print("SKIP: compiler cannot link ThreadSanitizer fixture")
                return None
            raise SystemExit("FAIL: reliable event fixture does not compile")
    print("compiled " + " ".join(flags))
    return exe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default=os.environ.get("CC") or shutil.which("gcc") or "clang")
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--mutation", choices=["deadline", "scope", "cancel"])
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="longshot-coop-events-") as temp:
        exe = compile_fixture(args.cc, args.source_root, Path(temp), mutation=args.mutation)
        cases = (args.case,) if args.case else CASES
        for case in cases:
            subprocess.run([str(exe), case], check=True, timeout=60)
        if os.name != "nt" and not args.case and not args.mutation:
            tsan = compile_fixture(args.cc, args.source_root, Path(temp), tsan=True)
            if tsan:
                subprocess.run([str(tsan), "threads"], check=True, timeout=60)
    print("PASS: reliable session events, %d cases and requested sanitizer coverage" % len(cases))


if __name__ == "__main__":
    main()
