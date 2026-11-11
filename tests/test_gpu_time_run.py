"""The replay's GPU-time collection, compiled from the real vk_replay.cpp against a fake query pool: a run of finished recording
slots read in one vkGetQueryPoolResults, each recording counted once, none read before its queries are available. No Vulkan or game.

    python -I -B tests/test_gpu_time_run.py [--cxx clang++] [--source gpu/vk/vk_replay.cpp] [--case NAME ...]

Negative control: --source with the collection that reads one slot a call (main before this change) fails the "run" case.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]
CASES = ("run", "behind", "own_unavailable", "half_written", "ring_end", "not_pending", "each")
START = "int64_t st_gpu_ns, st_gpu_max_ns;\n"
FUNC = "void gpu_time_collect(Recording& r) {"


def actual_block(source):
    if source.count(START) != 1 or source.count(FUNC) != 1:
        raise SystemExit("vk_replay.cpp: the anchors %r and %r must each appear once" % (START.strip(), FUNC))
    start = source.index(START) + len(START)
    at = source.index(FUNC) + len(FUNC)
    depth = 1
    while depth:
        depth += {"{": 1, "}": -1}.get(source[at], 0)
        at += 1
    return source[start:at]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cxx", default=os.environ.get("CXX", "clang++"))
    parser.add_argument("--source", type=Path, default=ROOT / "gpu/vk/vk_replay.cpp")
    parser.add_argument("--case", action="append", choices=CASES)
    args = parser.parse_args()
    cxx = shutil.which(args.cxx)
    if not cxx:
        raise SystemExit("C++ compiler not found: " + args.cxx)
    block = actual_block(args.source.read_text(encoding="utf-8"))
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
