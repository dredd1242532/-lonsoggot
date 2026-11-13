"""Compile the real game-free arena/Ready/ledger and diagnostic menu, including live consent/kit/input fences; no game, GPU or real save.

--mutations runs deliberately broken disposable copies, each of which must fail a behavioral assertion.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cc", "gcc"]
ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = {
    "ready-digest": ("coop_duel.c", "epoch != d->terms.rules.epoch || memcmp(digest, d->terms.digest, 32)", "epoch != d->terms.rules.epoch"),
    "capability": ("coop_duel.c", "proof && c->capabilities != COOP_DUEL_CAP_REQUIRED", "proof && c->capabilities > COOP_DUEL_CAP_REQUIRED"),
    "dedup": ("coop_duel.c", "if (!attack_once(d, a->attack))", "if (!attack_once(d, a->attack) && 0)"),
    "grace": ("coop_duel.c", "now - d->last_hit_ms < d->terms.grace_ms", "now - d->last_hit_ms > UINT64_MAX - d->terms.grace_ms"),
    "arena-age": ("coop_arena.c", "a->age > 1", "a->age > 2"),
    "off": ("coop_duel_menu.c", "!env_on(\"HOS_COOP_HITS_TRACE\")) return;", "0) return;"),
    "kit-ready": ("coop_duel_menu.c", "if (!hos_oot_coop_kit_ready(offer.token, offer.rules.epoch))", "if ((void)hos_oot_coop_kit_ready(offer.token, offer.rules.epoch), 0)"),
    "chat-input": ("coop_duel_menu.c", "rx_option(HOS_OPT_CHAT_FOCUS, 0) || rx_option(1, 0)", "((void)rx_option(HOS_OPT_CHAT_FOCUS, 0), 0) || rx_option(1, 0)"),
    "menu-input": ("coop_duel_menu.c", "rx_option(HOS_OPT_CHAT_FOCUS, 0) || rx_option(1, 0)", "rx_option(HOS_OPT_CHAT_FOCUS, 0) || ((void)rx_option(1, 0), 0)"),
}


def run(cc, temp, mutation=None):
    sources = ["coop_arena.c", "coop_duel.c", "coop_duel_menu.c"]
    paths = []
    for name in sources:
        text = (ROOT / "host" / name).read_text(encoding="utf-8")
        if mutation and name == MUTATIONS[mutation][0]:
            _, old, new = MUTATIONS[mutation]
            assert text.count(old) == 1, (mutation, "mutation anchor changed")
            text = text.replace(old, new)
        path = temp / name
        path.write_text(text, encoding="utf-8")
        paths.append(str(path))
    executable = temp / ("duel.exe" if os.name == "nt" else "duel")
    mono = ROOT / "third_party/monocypher"
    command = [cc, "-std=c11", "-D_POSIX_C_SOURCE=200809L", "-D_CRT_SECURE_NO_WARNINGS", "-Wall", "-Wextra", "-Werror", "-O1", "-g",
               "-I" + str(ROOT / "host"), "-I" + str(mono)]
    if os.name != "nt":
        command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
    command += paths + [str(ROOT / "host/coop_state.c"), str(ROOT / "host/coop_proto.c"), str(temp / "mono.o"),
                        str(ROOT / "tests/coop_duel_test.c"), "-o", str(executable)]
    if os.name != "nt":
        command += ["-lm"]
    print("COMMAND:", " ".join(command), flush=True)
    subprocess.run(command, check=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("HOS_COOP")}
    result = subprocess.run([str(executable), str(temp / "synthetic-arena.bin")], env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(result.stdout, end="", flush=True)
    if mutation:
        assert result.returncode != 0 and "FAIL line" in result.stdout, (mutation, result.returncode, result.stdout)
        print("PASS: deliberately broken", mutation, "caught behaviorally", flush=True)
    else:
        assert result.returncode == 0, result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default=os.environ.get("CC", "gcc"))
    parser.add_argument("--mutations", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="longshot-duel-") as name:
        temp = Path(name)
        subprocess.run([args.cc, "-std=c99", "-O1", "-c", str(ROOT / "third_party/monocypher/monocypher.c"),
                        "-o", str(temp / "mono.o")], check=True)
        run(args.cc, temp)
        if args.mutations:
            for mutation in MUTATIONS:
                run(args.cc, temp, mutation)


if __name__ == "__main__":
    main()
