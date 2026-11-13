"""The replay's GPU-time collection, compiled from the real vk_replay.cpp against a fake query pool. With RX_VK_TS_RUN=1, a run
of finished recording slots is read in one vkGetQueryPoolResults, each recording counted once, none read before its queries are
available; unset, one slot a call. No Vulkan or game.

    python -I -B tests/test_gpu_time_run.py [--cxx clang++] [--source gpu/vk/vk_replay.cpp] [--broken] [--case NAME ...]

Negative controls: --source with the collection that reads one slot a call (main before this change) fails the "run" case;
--broken drops the host reset of a slot that comes round, and the "stale" case must then fail.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]
CASES = ("run", "behind", "own_unavailable", "half_written", "ring_end", "not_pending", "stale", "each")
RESET = "        vkResetQueryPool(dev.device, ts_pool, 2 * r.slot, 2);"
START = "int64_t st_gpu_ns, st_gpu_max_ns;\n"
FUNC = "void gpu_time_collect(Recording& r) {"


def actual_block(source, broken=False):
    if source.count(START) != 1 or source.count(FUNC) != 1:
        raise SystemExit("vk_replay.cpp: the anchors %r and %r must each appear once" % (START.strip(), FUNC))
    start = source.index(START) + len(START)
    at = source.index(FUNC) + len(FUNC)
    depth = 1
    while depth:
        depth += {"{": 1, "}": -1}.get(source[at], 0)
        at += 1
    block = source[start:at]
    if broken:
        if block.count(RESET) != 1:
            raise SystemExit("--broken: the anchor %r must appear once in gpu_time_collect" % RESET.strip())
        block = block.replace(RESET, "")
    return block


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cxx", default=os.environ.get("CXX", "clang++"))
    parser.add_argument("--source", type=Path, default=ROOT / "gpu/vk/vk_replay.cpp")
    parser.add_argument("--broken", action="store_true", help="negative control: no host reset when a slot comes round")
    parser.add_argument("--case", action="append", choices=CASES)
    args = parser.parse_args()
    cxx = shutil.which(args.cxx)
    if not cxx:
        raise SystemExit("C++ compiler not found: " + args.cxx)
    block = actual_block(args.source.read_text(encoding="utf-8"), args.broken)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    env = dict(os.environ)
    env["PATH"] = str(Path(cxx).parent) + os.pathsep + env.get("PATH", "")
    with tempfile.TemporaryDirectory(prefix="hookshot-gpu-time-") as folder:
        tmp = Path(folder)
        (tmp / "gpu_time_actual.inc").write_text(block + "\n", encoding="utf-8")
        exe = tmp / ("gpu_time.exe" if os.name == "nt" else "gpu_time")
        command = [cxx, "-std=c++20", "-O0", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-I" + str(tmp),
                   str(ROOT / "tests/gpu_time_run_test.cpp"), "-o", str(exe)]
        result = subprocess.run(command, cwd=tmp, env=env, capture_output=True, text=True, timeout=120, creationflags=flags)
        if result.returncode:
            print(result.stdout + result.stderr)
            return result.returncode
        failed = 0
        for case in args.case or CASES:
            result = subprocess.run([str(exe), case], cwd=tmp, env=env, capture_output=True, text=True, timeout=10,
                                    creationflags=flags)
            print((result.stdout + result.stderr).strip())
            failed += result.returncode != 0
        print("gpu time run: %d cases, %d failed" % (len(args.case or CASES), failed))
        return int(failed != 0)


if __name__ == "__main__":
    raise SystemExit(main())
