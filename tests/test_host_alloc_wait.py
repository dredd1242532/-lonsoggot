"""The host's own allocations at Windows' commit limit (issue 333). A malloc or calloc in the NVN layer that failed at the
limit ended the game at once ("FATAL: native NVN: recording handle set"), with no wait and no out-of-memory notice. Now
hos_xmalloc / hos_xcalloc / hos_xrealloc (host/mkw_glue.c) wait for commit as a guest commit does, then say out of
memory, show the notice and only then stop.

Compiles the real hos_out_of_memory and hos_alloc_failed (and the pieces around them) cut out of host/mkw_glue.c with
tests/host_alloc_wait_test.c's fake malloc, clock and notice, and runs each case in its own process:

- recovers: refused three times, allocated 150 ms on, logged; realloc keeps the old block while it waits; no notice;
- limit: never given: 5 s of tries, the line, the notice shown on the caller's thread (it stops next), then the fatal
  that says out of memory; a guest commit refused after says nothing again;
- commit-first: a guest commit's notice already up on its own thread: the fatal waits until it is closed;
- not-limit: refused with plenty of commit left: no notice, the fatal says how much was left;
- overflow: calloc's count x size past size_t: the fatal at once, no wait;
- headless: HOS_HEADLESS shows no notice and so waits for none;
- test-switch: HOS_TEST_ALLOC_REFUSE refuses that label as at the limit, and no other.

And by text: hos_commit's and the first-touch handler's failure (commit_refused) still go through the same notice, and
the NVN layer's and the video service's allocations go through the helpers, with no die() or hos_fatal() of their own
on a NULL.

python -I -B tests/test_host_alloc_wait.py [--cc gcc]
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

RUN_ALL_ARGS = ["--cc", "gcc"]

ROOT = Path(__file__).resolve().parents[1]
CASES = ("recovers", "limit", "commit-first", "not-limit", "overflow", "headless", "test-switch")


def block(text, head):
    at = text.find(head)
    if at < 0:
        raise SystemExit("FAIL: not found: " + head)
    depth, i = 0, text.index("{", at)
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[at:j + 1]
    raise SystemExit("FAIL: unbalanced: " + head)


def check_sites():
    glue = (ROOT / "host/mkw_glue.c").read_text(encoding="utf-8")
    refused = block(glue, "static void commit_refused(uint64_t lo, uint64_t size, DWORD err)")
    if "hos_out_of_memory(0)" not in refused:
        raise SystemExit("FAIL: commit_refused does not say out of memory through hos_out_of_memory")
    nvn = (ROOT / "host/nvn_native.c").read_text(encoding="utf-8")
    for head, helper in (("static cmd_rec *cmd_adopt(", "hos_xcalloc("), ("static void cmd_release(", "hos_xrealloc("),
                         ("static void live_handle_add(", "hos_xcalloc("), ("static void pins_grow(", "hos_xcalloc("),
                         ("static void *pool_get(", "hos_xmalloc(")):
        body = block(nvn, head)
        if helper not in body or re.search(r"\b(malloc|calloc|realloc)\(", body):
            raise SystemExit("FAIL: %s allocates without %s" % (head, helper))
    if nvn.count("hos_xmalloc(") != 2:
        raise SystemExit("FAIL: pool_get's two mallocs are not both hos_xmalloc")
    for head in ("static void cmd_reserve_rec(", "static uint8_t *flatten("):
        body = block(nvn, head)
        if "pool_get(" not in body or re.search(r"if \(!\w+\) die\(", body):
            raise SystemExit("FAIL: %s checks pool_get for NULL (it never is) or does not use it" % head)
    leftover = re.findall(r"if \(!\w+\) die\(\"(?:command buffer table|recording|texture copies)[^\n]*", nvn)
    if leftover:
        raise SystemExit("FAIL: an allocation in nvn_native.c still dies on NULL: " + leftover[0])
    video = (ROOT / "host/nn_video.c").read_text(encoding="utf-8")
    if "hos_fatal(NULL, \"video: out of memory" in video or video.count("hos_x") < 2:
        raise SystemExit("FAIL: nn_video.c's decoder table and image pool do not use the helpers")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc")
    args = parser.parse_args()
    check_sites()
    glue = (ROOT / "host/mkw_glue.c").read_text(encoding="utf-8")
    pieces = [block(glue, head) for head in (
        "static DWORD WINAPI oom_box(LPVOID unused)", "void hos_out_of_memory(int wait)",
        "static int alloc_refused(const char *what)", "static void *alloc_try(",
        "void *hos_alloc_failed(int kind,", "void *hos_xmalloc(size_t size, const char *what)",
        "void *hos_xcalloc(size_t count, size_t size, const char *what)",
        "void *hos_xrealloc(void *old, size_t size, const char *what)")]
    if "static volatile LONG oom_state;" not in glue:
        raise SystemExit("FAIL: oom_state is not the static the fixture stands in for")
    cc = shutil.which(args.cc)
    if not cc:
        raise SystemExit("test_host_alloc_wait: no C compiler %r (pass --cc)" % args.cc)
    flags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0
    with tempfile.TemporaryDirectory(prefix="host-alloc-wait-") as temporary:
        folder = Path(temporary)
        (folder / "host_alloc_wait.inc").write_text("\n".join(pieces) + "\n", encoding="utf-8")
        executable = folder / "fixture.exe"
        subprocess.run([cc, "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-I", str(folder),
                        str(ROOT / "tests/host_alloc_wait_test.c"), "-o", str(executable)], check=True, creationflags=flags)
        for case in CASES:
            result = subprocess.run([str(executable), case], capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=30, creationflags=flags)
            print(result.stdout, end="")
            if result.returncode:
                print(result.stderr, end="", file=sys.stderr)
                raise SystemExit("FAIL: case %s (exit %d)" % (case, result.returncode))
    print("PASS: a host allocation at the commit limit waits, then says out of memory and shows the notice before it stops")


if __name__ == "__main__":
    main()
