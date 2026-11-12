"""The development loop's entry point (the dev-loop proposal's A9, #2636): what the next build will do and why, what the last
ones took, and the last build again, with the same arguments, tools and environment as the builders' (nx2/profiles.py).

    python -I nx2/dev.py status [--work DIR]          whether the next build translates the game again, and why; the last build
    python -I nx2/dev.py times [-n N] [--work DIR]    the last builds' times, phase by phase (build_times.json, make_game.py)
    python -I nx2/dev.py build [--dev] [--work DIR]   the last make_game.py build again (its last_build.json); --dev: the dev
                                                      profile (LONGSHOT_PROFILE=dev: a console, no shader preparation at start)

The work folder is --work, else OOT_WORK, else the checkout's work/. Nothing here reads the game's files beyond what the build's
own key reads (the exefs files' hashes), and nothing it prints comes from them. Standard library only.
"""
import argparse
import importlib.util
import json
import os
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def work_folder(given):
    return os.path.abspath(given or os.environ.get("OOT_WORK") or os.path.join(ROOT, "work"))


def make_game():
    """nx2/make_game.py as a module: its own key, parts and checks, so the answers are the build's"""
    for p in (os.path.join(ROOT, "third_party", "a64-kit", "tools"), HERE):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location("make_game_for_dev", os.path.join(HERE, "make_game.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def last_build(work):
    return read_json(os.path.join(work, "last_build.json"))


def differences(before, now):
    """What changed between two lift keys' parts, as lines a person reads"""
    lines = []
    if before.get("python") != now.get("python"):
        lines.append("Python: %s -> %s" % (before.get("python", "?").split()[0], now.get("python", "?").split()[0]))
    old, new = set(before.get("hle", [])), set(now.get("hle", []))
    if old != new:
        lines.append("host/ HLE functions: %s" % ", ".join(["+" + n for n in sorted(new - old)] + ["-" + n for n in sorted(old - new)]))
    of, nf = before.get("files", {}), now.get("files", {})
    for name in sorted(set(of) | set(nf)):
        if name not in of:
            lines.append("new file: %s" % name)
        elif name not in nf:
            lines.append("file gone: %s" % name)
        elif of[name] != nf[name]:
            lines.append("changed: %s" % name)
    oe, ne = before.get("env", {}), now.get("env", {})
    for name in sorted(set(oe) | set(ne)):
        if oe.get(name) != ne.get(name):
            lines.append("environment %s: %s -> %s" % (name, oe.get(name, "(unset)"), ne.get(name, "(unset)")))
    return lines


def lift_status(work, record, mg):
    """Whether the next build skips the lift, and if not, why (the stored key's parts against today's)"""
    import profiles
    exefs = (record or {}).get("env", {}).get("OOT_EXEFS") or os.path.join(work, "exefs")
    if not os.path.isdir(exefs):
        return ["lift: cannot tell, the game's code is not in %s (a player's build lets it go)" % exefs]
    env = profiles.build_env(work, exefs, (record or {}).get("env", {}).get("OOT_ROMFS", ""), (record or {}).get("out", ""),
                             name=profiles.profile())
    key = mg.lift_key(exefs, work, env)
    if mg.lifted_before(work, key) or mg.kept_before(work, key):
        return ["lift: the next build skips the translation (the game, the tools and the settings are the last lift's)"]
    stored = read_json(os.path.join(work, "sa", mg.LIFT_PARTS))
    if not stored or "parts" not in stored:
        return ["lift: the next build translates the game again; what the last lift was made from is not on record (a lift "
                "from before nx2/dev.py, or none yet)"]
    why = differences(stored["parts"], mg.KEY_PARTS[key])
    if not why:
        why = ["the lifted files changed since the lift, or the lift did not finish"]
    return ["lift: the next build translates the game again:"] + ["  " + line for line in why]


def times_table(work, n):
    """The last n builds side by side: total, compile, and each phase build_times.json kept (A1)"""
    data = read_json(os.path.join(work, "build_times.json")) or {}
    builds = [b for b in data.get("builds", []) if isinstance(b, dict)][-n:]
    if not builds:
        return ["no builds recorded in %s" % os.path.join(work, "build_times.json")]
    names = []
    for b in builds:
        for name in (b.get("phases") or {}):
            if name not in names:
                names.append(name)
    rows = [("", [b.get("when", "?")[5:16] + " " + b.get("kind", "") for b in builds]),
            ("total", ["%.0f s" % b.get("total_seconds", 0) for b in builds]),
            ("compile (and links)", ["%.0f s" % b.get("compile_seconds", 0) for b in builds])]
    order = [s for s in ("checks", "read", "lift", "compile", "finish") if s in names]
    for stage in order:
        rows.append((stage, ["%.0f s" % (b.get("phases") or {}).get(stage, 0) if stage in (b.get("phases") or {}) else "-"
                             for b in builds]))
        for name in names:
            if name.startswith(stage + ": "):
                rows.append(("  " + name.split(": ", 1)[1],
                             ["%.1f s" % (b.get("phases") or {})[name] if name in (b.get("phases") or {}) else "-" for b in builds]))
    width = max(len(r[0]) for r in rows)
    return ["%-*s  %s" % (width, label, "  ".join("%16s" % c for c in cells)) for label, cells in rows]


def replayed(argv):
    """The last build's arguments for this one: all of them but --log and its file (that build's log, not this one's)"""
    out, skip = [], False
    for arg in argv:
        if skip:
            skip = False
        elif arg == "--log":
            skip = True
        elif not arg.startswith("--log="):
            out.append(arg)
    return out


def build(work, dev):
    """The last make_game.py build again: its arguments, its Python, its tools' variables; --dev for the dev profile"""
    record = last_build(work)
    if not record:
        raise SystemExit("no last build in %s: build once with nx2/make_game.py (or the builder) first" % work)
    env = dict(os.environ, **record.get("toolchain", {}))
    env["LONGSHOT_PROFILE"] = "dev" if dev else record.get("profile", "release")
    cmd = [record.get("python") or sys.executable, "-I", "-u", os.path.join("nx2", "make_game.py")] + replayed(record.get("argv", []))
    shown = subprocess.list2cmdline(cmd) if os.name == "nt" else " ".join(shlex.quote(c) for c in cmd)
    print("dev: " + shown + ("  (dev profile)" if dev else ""), flush=True)
    return subprocess.call(cmd, cwd=record.get("root") or ROOT, env=env)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("status", "times", "build"):
        q = sub.add_parser(name)
        q.add_argument("--work")
        if name == "times":
            q.add_argument("-n", type=int, default=3)
        if name == "build":
            q.add_argument("--dev", action="store_true")
    a = p.parse_args(argv)
    work = work_folder(a.work)
    if a.command == "build":
        return build(work, a.dev)
    if a.command == "times":
        print("\n".join(times_table(work, a.n)))
        return 0
    record = last_build(work)
    print("work folder: %s%s" % (work, "" if record else " (no last build recorded)"))
    print("\n".join(lift_status(work, record, make_game())))
    print("\n".join(["the last builds:"] + times_table(work, 1)))
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
