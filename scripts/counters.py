"""HOS_COUNTERS' file (gpu/vk/vk_replay.cpp counters_tick): a table, the difference between two points, or what only grew.

    python -I scripts/counters.py FILE                  every line, its time and the counters that matter most
    python -I scripts/counters.py FILE --diff [A B]     line B against line A (counted from 0; default the first and the last)
    python -I scripts/counters.py FILE --grew           the levels that went up line after line and never down: a leak to
                                                        look at, across a loop of the same scene

The file's first line names the format ({"counters": 1, ...}); one of another format is refused. The counters "so far"
(presents, synced and written-back bytes, draws, pipelines, program cache hits) only grow by nature, so --grew leaves
them out. Standard library only.
"""
import argparse
import json
import sys

FORMAT = 1
SO_FAR = {"t", "presents", "synced_mb", "written_back_mb", "draws", "pipelines", "program_cache_hits"}
TABLE = ["t", "presents", "private_mb", "heap_used_mb", "vram_mb", "system_mb", "textures", "texture_mb", "mirror_mb"]


def load(path):
    """(head, lines): the first line's object, and each counters line's; a file of another format stops the run"""
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(l) for l in f if l.strip()]
    if not rows or rows[0].get("counters") != FORMAT:
        raise SystemExit("%s: not a HOS_COUNTERS file of format %d (its first line: %s)" % (path, FORMAT, rows[0] if rows else "none"))
    return rows[0], rows[1:]


def table(lines, cols=TABLE):
    cols = [c for c in cols if any(c in l for l in lines)]
    out = [" ".join("%12s" % c for c in cols)]
    for l in lines:
        out.append(" ".join("%12s" % ("-" if l.get(c) is None else "%.1f" % l[c] if isinstance(l[c], float) else l[c]) for c in cols))
    return "\n".join(out)


def diff(lines, a=0, b=-1):
    """{counter: (at a, at b, b - a)} for the numbers both lines have"""
    la, lb = lines[a], lines[b]
    return {k: (la[k], lb[k], lb[k] - la[k]) for k in la if k in lb and isinstance(la[k], (int, float))}


def grew(lines):
    """the levels (not the counters so far) that went up from the first line to the last and never down in between"""
    if len(lines) < 2:
        return {}
    out = {}
    for k in lines[0]:
        if k in SO_FAR or not isinstance(lines[0][k], (int, float)):
            continue
        vals = [l.get(k) for l in lines]
        if None in vals:
            continue
        if vals[-1] > vals[0] and all(y >= x for x, y in zip(vals, vals[1:])):
            out[k] = (vals[0], vals[-1])
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("file")
    p.add_argument("--diff", nargs="*", type=int, metavar="LINE")
    p.add_argument("--grew", action="store_true")
    a = p.parse_args(argv)
    head, lines = load(a.file)
    print("%s, every %s s, %d lines" % (head.get("version", "?"), head.get("every_s", "?"), len(lines)))
    if not lines:
        return 0
    if a.diff is not None:
        if len(a.diff) not in (0, 2):
            raise SystemExit("--diff: two lines, or none for the first and the last")
        i, j = a.diff if a.diff else (0, len(lines) - 1)
        print("line %d (t %.1f s) -> line %d (t %.1f s)" % (i, lines[i].get("t", 0), j, lines[j].get("t", 0)))
        for k, (x, y, d) in diff(lines, i, j).items():
            if k != "t":
                print("  %-20s %12g -> %12g  %+g" % (k, x, y, d))
    elif a.grew:
        g = grew(lines)
        if not g:
            print("no level only grew")
        for k, (x, y) in g.items():
            print("  %-20s %12g -> %12g (up at every line or level)" % (k, x, y))
    else:
        print(table(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
