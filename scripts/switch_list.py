"""The game's switches, one list (host/switches.def), kept in step with what host/, gpu/vk, gpu/render and gpu/compat read.

    python -I scripts/switch_list.py              the switches read and not listed, and the listed ones read nowhere
    python -I scripts/switch_list.py --update     adds a line for each one read and not listed (its area, and its type
                                                  where the reads make it certain), drops the ones read nowhere, and
                                                  moves an area to a file that reads it

A switch is read through getenv, env_on, env_int or env_float (or menu_env_on, env_override) with its name written out, or
named in a table as a string literal of its own ("RX_VK_...", read through a variable). eden, sirit and third_party are
not ours and not read. Standard library only.
"""
import argparse
import collections
import os
import re
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DEF = os.path.join(ROOT, "host", "switches.def")
TOPS = ("host", "gpu/vk", "gpu/render", "gpu/compat")   # (the game, its Vulkan renderer, the WebGPU fallback, their helpers)
SKIP = {"eden", "sirit", "third_party", "tests"}
EXTS = (".c", ".cpp", ".h", ".hpp", ".inc")
CALL = re.compile(r'\b(getenv|secure_getenv|env_on|env_int|env_float|menu_env_on|env_override)\s*\(\s*"([A-Z][A-Z0-9_]+)"')
LIT = re.compile(r'"((?:RX|HOS|LONGSHOT|EDEN|NVN)_[A-Z0-9_]+)"')
LINE = re.compile(r'^SWITCH\((\w+), (FLAG|INT|FLOAT|TEXT|PATH|ANY), (GAME|LOCAL|SYSTEM), "([^"]*)", "((?:[^"\\]|\\.)*)"\)\s*$')
# (the variables of the system and of the libraries the game reads, not switches of its own)
SYSTEM = re.compile(r"^(XDG_\w+|TMPDIR|HOME|APPIMAGE|USERPROFILE|USERNAME|USER|WINDIR|WAYLAND_DISPLAY|DISPLAY|SDL_\w+|"
                    r"LOCALAPPDATA|APPDATA|PATH|LANG|LC_\w+)$")


def reads(root=ROOT):
    """{name: {"files": set, "types": set}}: every switch host/ and gpu/vk read, with the file and what each read says
    about its type"""
    found = collections.defaultdict(lambda: {"files": set(), "types": set()})
    for top in TOPS:
        for d, ds, fs in os.walk(os.path.join(root, top)):
            ds[:] = sorted(x for x in ds if x not in SKIP)
            for f in sorted(fs):
                if not f.endswith(EXTS):
                    continue
                path = os.path.join(d, f)
                rel = os.path.relpath(path, root).replace(os.sep, "/")
                for line in open(path, encoding="utf-8", errors="replace"):
                    called = set()
                    for m in CALL.finditer(line):
                        fn, name = m.groups()
                        called.add(name)
                        rest = line[m.end():]
                        found[name]["files"].add(rel)
                        found[name]["types"].add(
                            "FLAG" if fn in ("env_on", "menu_env_on") else "INT" if fn == "env_int" else
                            "FLOAT" if fn == "env_float" or re.match(r"[^;]*\b(atof|strtod)\(", rest) else
                            "INT" if re.match(r"[^;]*\b(atoi|atol|atoll|strtoul?l?)\(", rest) else "TEXT")
                    for name in LIT.findall(line):
                        if name not in called:
                            found[name]["files"].add(rel)
                            found[name]["types"].add("TABLE")
    return found


def listed(path=DEF):
    """{name: (type, scope, area, what)} from switches.def, in its order; a line it cannot read stops the run"""
    out = collections.OrderedDict()
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        if not line.startswith("SWITCH("):
            continue
        m = LINE.match(line)
        if not m:
            sys.exit("%s:%d: not a SWITCH(NAME, TYPE, SCOPE, \"area\", \"what\") line: %s" % (path, n, line.strip()))
        name, typ, scope, area, what = m.groups()
        if name in out:
            sys.exit("%s:%d: %s is listed twice" % (path, n, name))
        out[name] = (typ, scope, area, what)
    return out


def certain_type(name, info):
    """the type the reads make certain: one kind of read (a table's name says nothing), or a file or folder by its name"""
    if name.endswith(("_FILE", "_DIR")):
        return "PATH"
    kinds = info["types"] - {"TABLE"}
    return next(iter(kinds)) if len(kinds) == 1 and kinds != {"TEXT"} else "ANY"


def line_for(name, typ, scope, area, what):
    return 'SWITCH(%s, %s, %s, "%s", "%s")' % (name, typ, scope, area, what.replace("\\", "\\\\").replace('"', '\\"'))


def compare(root=ROOT, path=DEF):
    found, have = reads(root), listed(path)
    return sorted(set(found) - set(have)), sorted(set(have) - set(found)), found, have


def update(root=ROOT, path=DEF):
    """the missing ones added (sorted in), the ones read nowhere dropped; the file's head and every other line kept"""
    missing, gone, found, have = compare(root, path)
    text = open(path, encoding="utf-8").read()
    first = re.search(r"(?m)^SWITCH\(", text)   # (a line that starts with it: the head's comment shows one too)
    head = text[:first.start()] if first else text
    rows = {}
    for n, (typ, scope, area, what) in have.items():
        if n not in gone:   # (its area moved where the file named no longer reads it)
            rows[n] = line_for(n, typ, scope, area if area in found[n]["files"] else sorted(found[n]["files"])[0], what)
    for n in missing:
        info = found[n]
        scope = "SYSTEM" if SYSTEM.match(n) else "LOCAL" if n.endswith(("_FILE", "_DIR")) else "GAME"   # (as the allow-list takes it)
        rows[n] = line_for(n, certain_type(n, info), scope, sorted(info["files"])[0], "")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(head + "\n".join(rows[n] for n in sorted(rows)) + "\n")
    return missing, gone


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--update", action="store_true", help="add the missing lines and drop the ones read nowhere")
    a = p.parse_args(argv)
    if a.update:
        missing, gone = update()
        print("host/switches.def: %d added, %d dropped" % (len(missing), len(gone)))
        for n in missing:
            print("  + %s" % n)
        for n in gone:
            print("  - %s" % n)
        return 0
    missing, gone, _, have = compare()
    for n in missing:
        print("read, not in host/switches.def: %s" % n)
    for n in gone:
        print("in host/switches.def, read nowhere: %s" % n)
    print("%d switches listed; %d missing, %d read nowhere%s" % (len(have), len(missing), len(gone),
                                                                ": python -I scripts/switch_list.py --update" if missing or gone else ""))
    return 1 if missing or gone else 0


if __name__ == "__main__":
    sys.exit(main())
