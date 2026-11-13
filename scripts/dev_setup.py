"""A new developer's PC, ready for Longshot: what is set up, what is missing, and each missing step done on request
(docs/dev-starter.md). Standard library only, Windows and Linux; nothing is installed or changed without asking.

    python -I scripts/dev_setup.py            check each step, and offer to do the ones that are missing
    python -I scripts/dev_setup.py --check    check only: change nothing (exit 1 when a required step is missing)
    python -I scripts/dev_setup.py --yes      do every missing step without asking
    python -I scripts/dev_setup.py --skip graphify,checks

The steps, in order (--skip takes their names):

- python:    Python 3.10 or newer, and requirements.txt's packages (offers pip install -r requirements.txt)
- compiler:  on Windows llvm-mingw (LONGSHOT_MINGW, or an llvm-mingw folder in the clone); on Linux clang and lld
- headers:   the Vulkan headers some checks compile against (system, LONGSHOT_VULKAN_HEADERS or a CPM cache); without
             them those checks are skipped, not failed
- signin:    the team's one sign-in (forge.py login: git.magicbean.dev, the team chat, test requests)
- fork:      your fork of Longshot and its remote forge-fork (forge.py fork)
- graphify:  the code graph (graphify, PyPI's graphifyy, pinned below): installed with uv or pipx, built from the code
             only (no LLM), and kept current by git hooks. .graphifyignore keeps the game's files out; the graph,
             graphify-out/, stays on this PC
- checks:    python -I -B tests/run_all.py, the game-free checks

Nothing here reads the game or a dump.
"""
import argparse
import glob
import importlib.util
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPHIFY = "graphifyy==0.9.101"   # (PyPI's name for graphify, Apache-2.0: the version the guide was written with)
MODULES = {"lz4": "lz4", "numpy": "numpy", "capstone": "capstone", "unicorn": "unicorn", "zstandard": "zstandard"}
# (what .graphifyignore must keep out: the game's files, as .gitignore's first section lists them)
NEVER_GRAPHED = ("work/", "gen/", "image/", "exefs/", "romfs/", "gamefile/", "*.bin", "*.keys", "*.nso", "*.nca",
                 "*.nsp", "*.xci", "*.nup", "*.nsz", "*.xcz")
STEPS = ("python", "compiler", "headers", "signin", "fork", "graphify", "checks")
WINDOWS = os.name == "nt"


class Setup:
    def __init__(self, check, yes):
        self.check, self.yes = check, yes
        self.missing = []   # (required steps still missing)
        self.notes = []     # (optional ones)

    def say(self, mark, step, text):
        print("%-4s %-9s %s" % (mark, step, text))

    def ok(self, step, text):
        self.say("ok", step, text)

    def lack(self, step, text, required=True):
        self.say("--" if required else "..", step, text)
        (self.missing if required else self.notes).append(step)

    def ask(self, question):
        """Whether to do a step: never with --check, always with --yes, else the person's answer"""
        if self.check:
            return False
        if self.yes:
            return True
        try:
            return input("     %s [y/N] " % question).strip().lower() in ("y", "yes")
        except EOFError:
            return False

    def run(self, args, **kw):
        print("     $ " + " ".join(args))
        return subprocess.run(args, cwd=ROOT, **kw).returncode == 0


def git(*args):
    r = subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def python_step(s):
    if sys.version_info < (3, 10):
        s.lack("python", "Python %d.%d: 3.10 or newer is needed (python.org; on Windows not the Microsoft Store build)"
               % sys.version_info[:2])
        return
    gone = [pkg for mod, pkg in MODULES.items() if importlib.util.find_spec(mod) is None]
    if not gone:
        s.ok("python", "Python %d.%d with requirements.txt's packages" % sys.version_info[:2])
        return
    s.lack("python", "missing packages: %s" % ", ".join(gone))
    if s.ask("Install them (python -m pip install -r requirements.txt)?") and \
            s.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]):
        s.missing.remove("python")


def compiler_step(s):
    if WINDOWS:
        found = os.environ.get("LONGSHOT_MINGW") or (os.path.join(ROOT, "llvm-mingw")
                                                    if os.path.isdir(os.path.join(ROOT, "llvm-mingw")) else "")
        if found and glob.glob(os.path.join(found, "bin", "clang*")):
            s.ok("compiler", "llvm-mingw at %s" % found)
        else:
            s.lack("compiler", "no llvm-mingw: get its ucrt-x86_64 build and set LONGSHOT_MINGW "
                               "(CONTRIBUTING.md, Development setup)")
        return
    clang, lld = shutil.which("clang") or shutil.which("clang++"), shutil.which("ld.lld")
    if clang and lld:
        s.ok("compiler", "clang and lld (%s)" % clang)
    else:
        s.lack("compiler", "missing %s: install what is missing, or build in scripts/linux/docker.sh (CONTRIBUTING.md)"
               % " and ".join(n for n, p in (("clang", clang), ("lld", lld)) if not p))


def headers_step(s):
    flat = [os.environ.get("LONGSHOT_VULKAN_HEADERS", "")] + ([] if WINDOWS else ["/usr/include", "/usr/local/include"])
    caches = [os.environ.get("RX_CPM", ""), os.path.join(ROOT, "cpm")]   # (a CPM cache keeps them a few folders down)
    for p in filter(None, flat + caches):
        if os.path.isfile(os.path.join(p, "vulkan", "vulkan.h")) or \
                (p in caches and glob.glob(os.path.join(p, "**", "vulkan", "vulkan.h"), recursive=True)):
            s.ok("headers", "Vulkan headers under %s" % p)
            return
    s.lack("headers", "no Vulkan headers found: the checks that compile against them are skipped (RX_CPM, or your "
                      "distribution's vulkan headers package)", required=False)


def forge(*args, **kw):
    return subprocess.run([sys.executable, "-I", os.path.join("scripts", "forge.py"), *args], cwd=ROOT, **kw)


def signin_step(s):
    r = forge("whoami", capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        s.ok("signin", "signed in to git.magicbean.dev as %s" % r.stdout.strip().splitlines()[-1])
        return
    s.lack("signin", "not signed in: one sign-in with Discord for git.magicbean.dev, the team chat and test requests")
    if s.ask("Sign in now (python -I scripts/forge.py login: it prints a link to open)?") and \
            s.run([sys.executable, "-I", os.path.join("scripts", "forge.py"), "login"]):
        s.missing.remove("signin")


def fork_step(s):
    url = git("remote", "get-url", "forge-fork")
    if url:
        s.ok("fork", "your fork: forge-fork = %s" % url)
        return
    s.lack("fork", "no forge-fork remote: your fork is where your branches go", required=False)
    if s.ask("Make your fork and its remote (python -I scripts/forge.py fork)?") and \
            s.run([sys.executable, "-I", os.path.join("scripts", "forge.py"), "fork"]):
        s.notes.remove("fork")


def graphify_ignored():
    """The NEVER_GRAPHED patterns .graphifyignore lacks"""
    try:
        with open(os.path.join(ROOT, ".graphifyignore"), encoding="utf-8") as f:
            lines = {l.strip() for l in f}
    except OSError:
        lines = set()
    return [p for p in NEVER_GRAPHED if p not in lines]


def graphify_install(s):
    uv, pipx = shutil.which("uv"), shutil.which("pipx")
    if not (uv or pipx):
        s.say("", "", "neither uv nor pipx is installed: install one (https://docs.astral.sh/uv/), or "
                      "python -m pip install --user %s" % GRAPHIFY)
        return False
    cmd = [uv, "tool", "install", GRAPHIFY] if uv else [pipx, "install", GRAPHIFY]
    return s.ask("Install graphify (%s)?" % " ".join([os.path.basename(cmd[0])] + cmd[1:])) and s.run(cmd)


def graphify_step(s):
    lacking = graphify_ignored()
    if lacking:   # (the graph would take the game's files: refuse before building anything)
        s.lack("graphify", ".graphifyignore does not leave out %s: restore it from main" % ", ".join(lacking))
        return
    exe = shutil.which("graphify")
    if not exe:
        s.lack("graphify", "not installed (%s): the code graph agents query before reading widely" % GRAPHIFY,
               required=False)
        if not graphify_install(s):
            return
        exe = shutil.which("graphify")
        if not exe:
            s.say("", "", "installed, but not on PATH yet: open a new terminal (uv tool update-shell) and run this again")
            return
        s.notes.remove("graphify")
    graph = os.path.join(ROOT, "graphify-out", "graph.json")
    hooks = git("rev-parse", "--git-path", "hooks")
    hooked = bool(hooks) and os.path.isfile(os.path.join(ROOT, hooks, "post-commit"))
    if os.path.isfile(graph) and hooked:
        s.ok("graphify", "code graph at graphify-out/graph.json, kept current by git hooks")
        return
    s.lack("graphify", "%s%s" % ("no code graph yet" if not os.path.isfile(graph) else "graph built",
                                 "" if hooked else "; no hooks to keep it current"), required=False)
    if not s.ask("Build the code graph (graphify update ., code only, no LLM, about a minute) and add its git hooks?"):
        return
    env = dict(os.environ, PYTHONHASHSEED="0")   # (graphify's clustering is reproducible only with a fixed seed)
    if not s.run([exe, "update", "."], env=env):
        return
    before = git("show", "HEAD:.gitattributes")
    s.run([exe, "hook", "install"])
    # (hook install also adds a graph.json merge driver to the tracked .gitattributes: the graph is never committed,
    # so the line would only be a stray change in every clone)
    if before is not None and git("diff", "--quiet", "--", ".gitattributes") is None:
        git("checkout", "--", ".gitattributes")
    s.notes.remove("graphify")


def checks_step(s):
    if not s.ask("Run the game-free checks (python -I -B tests/run_all.py, a few minutes)?"):
        s.say("..", "checks", "not run: python -I -B tests/run_all.py")
        return
    r = subprocess.run([sys.executable, "-I", "-B", os.path.join("tests", "run_all.py")], cwd=ROOT,
                       capture_output=True, text=True)
    summary = [l for l in (r.stdout + r.stderr).splitlines() if " passed, " in l]
    failed = [l.split()[-1] for l in r.stdout.splitlines() if l.startswith("FAIL ")]
    s.say("ok" if r.returncode == 0 else "..", "checks", summary[-1] if summary else "ran (no summary line)")
    for name in sorted(set(failed)):
        s.say("", "", "failed: %s (if main fails it too, it isn't your PC: docs/dev-starter.md)" % name)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="check only: change nothing")
    mode.add_argument("--yes", action="store_true", help="do every missing step without asking")
    p.add_argument("--skip", default="", help="steps to leave out, comma-separated: " + ",".join(STEPS))
    a = p.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)   # (each line before the commands' own output)
    skip = {x.strip() for x in a.skip.split(",") if x.strip()}
    if skip - set(STEPS):
        p.error("unknown steps: %s (the steps: %s)" % (", ".join(sorted(skip - set(STEPS))), ", ".join(STEPS)))
    s = Setup(a.check, a.yes)
    for step, fn in (("python", python_step), ("compiler", compiler_step), ("headers", headers_step),
                     ("signin", signin_step), ("fork", fork_step), ("graphify", graphify_step),
                     ("checks", checks_step)):
        if step in skip:
            s.say("", step, "skipped")
        else:
            fn(s)
    print()
    if s.missing:
        print("Still to do: %s (docs/dev-starter.md)." % ", ".join(s.missing))
    else:
        print("Ready. Next: pick work (docs/worklist.md), test a change against a base (scripts/dev_packs.py), and "
              "open a PR (python -I scripts/forge.py pr create). docs/dev-starter.md has the rest.")
    return 1 if s.missing else 0


if __name__ == "__main__":
    sys.exit(main())
