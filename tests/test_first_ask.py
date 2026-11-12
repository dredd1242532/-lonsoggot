# SPDX-License-Identifier: GPL-3.0-or-later
"""A new pipeline is asked of the caches by its first draw (gpu/vk/first_ask.h, gpu/vk/vk_draw.cpp graphics_pipeline): the
budget a frame (so many asks, so much time, a new frame anew), compiled and run alone; and the source: the ask is made without
compiling, before the draw's pipeline is handed to a worker, an answered one makes the entry ready, and one the budget or the
caches refuse takes the path there was. No game or GPU is needed.
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]


def check_source():
    draw = (ROOT / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    start = draw.index("PipeEntry* graphics_pipeline(const Program& p, const NvnState& s, const Targets& at, VkPrimitiveTopology topo,")
    body = draw[start:draw.index("\n}\n", start)]
    ask = body.find("build_graphics(p, s, at, topo, -1, &needs_compiling)")
    if ask < 0:
        raise SystemExit("graphics_pipeline does not ask the caches for a new pipeline without compiling")
    if not body.index("budget.allow(texture_frame_no(), programs_frame_ms())") < ask < body.index("jobs_push("):
        raise SystemExit("the ask is not inside the frame's budget, or not before the pipeline goes to a worker")
    after = body[ask:body.index("jobs_push(")]
    for needle in ("budget.spent(", "e->ready.store(true, std::memory_order_release);", "return remember(e);", "st_first_ask_over++"):
        if needle not in after:
            raise SystemExit("graphics_pipeline's first ask no longer has: " + needle)
    # (this process's own cache is asked before the driver's disk cache: an in-memory answer is a fraction of the other)
    before = body[:ask]
    if not before.rindex("ask_start_first = true;") > before.rindex("budget.allow(") or "ask_start_first = false;" not in after:
        raise SystemExit("the first ask does not ask this process's own cache first")
    if "dev.vk13.pipelineCreationCacheControl" not in body[:ask]:
        raise SystemExit("the ask is made where the driver cannot be asked without compiling")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="first-ask-") as tmp:
        exe = Path(tmp) / "first-ask"
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-D_CRT_SECURE_NO_WARNINGS",
                        str(ROOT / "tests/first_ask_test.cpp"), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == "__main__":
    main()
