# SPDX-License-Identifier: GPL-3.0-or-later
"""Which of the game's compute programs are held to 32 lanes (gpu/vk/compute_pin.h, used by gpu/vk/vk_draw.cpp).

Held to 32 lanes, Mesa's compiler for Intel GPUs makes the SIMD32 program alone, and its register allocation took minutes
and gigabytes over the game's large compute programs: on an Iris Xe with 32 GB the preparation was killed for memory or
never ended. A program that cannot tell its lanes computes the same at any lane count, so on Mesa's Intel driver only the
programs that can keep the pin: subgroup operations or matrices, or shared memory in a workgroup of one warp.

Compiles the rule and checks it: each subgroup flag keeps the pin, shared memory keeps it only up to 32 invocations, a
program with no stage keeps it, RX_VK_COMPUTE_PIN=all and lanes over the default, other GPUs keep every pin. And the
source: every game compute pipeline (dispatch, load, precompile, the preparation's warm) is described by
compute_pipeline_info, which pins through pin_compute, so all of them ask the caches for the same pipeline; the rule reads
the program's record, never its SPIR-V; the default is Mesa's Intel driver alone. No game or GPU is needed.

python -I -B tests/test_compute_pin.py [--cxx g++]
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]

MAIN = r"""
#include "../gpu/vk/compute_pin.h"
#include <cstdio>
using namespace compute_pin;
static int fails = 0;
#define CHECK(c, what) do { if (!(c)) { std::printf("FAIL: %s\n", what); fails++; } } while (0)
static Uses with(void (*set)(Uses&)) { Uses u; u.invocations = 128; set(u); return u; }
int main() {
    const Mode lanes = mode_for(true, nullptr);
    CHECK(lanes == Mode::Lanes, "Mesa's Intel driver: lanes by default");
    CHECK(mode_for(false, nullptr) == Mode::All, "other drivers: every program pinned, as before");
    CHECK(mode_for(false, "lanes") == Mode::Lanes && mode_for(true, "all") == Mode::All, "RX_VK_COMPUTE_PIN over the default");
    CHECK(mode_for(true, "yes") == Mode::Lanes && mode_for(false, "") == Mode::All, "any other text: the default");

    Uses plain; plain.invocations = 128;
    CHECK(!needed(lanes, true, plain), "nothing that tells the lanes: not pinned");
    CHECK(needed(Mode::All, true, plain), "all: pinned");
    CHECK(needed(lanes, false, plain), "no stage to read: pinned");
    void (*flags[])(Uses&) = {[](Uses& u) { u.vote = true; }, [](Uses& u) { u.mask = true; },
                              [](Uses& u) { u.invocation_id = true; }, [](Uses& u) { u.shuffles = true; },
                              [](Uses& u) { u.quad_shuffles = true; }, [](Uses& u) { u.matrix = true; },
                              [](Uses& u) { u.fswzadd = true; }};
    for (auto set : flags) CHECK(needed(lanes, true, with(set)) && sees_lanes_ops(with(set)), "a subgroup flag keeps the pin");
    Uses shared = plain; shared.shared_memory = 1536;
    CHECK(!needed(lanes, true, shared), "shared memory in a workgroup of 128: its barriers order it, not pinned");
    shared.invocations = 32;
    CHECK(needed(lanes, true, shared), "shared memory in one warp: pinned");
    shared.invocations = 8;
    CHECK(needed(lanes, true, shared) && !sees_lanes_ops(shared), "shared memory in less than a warp: pinned, for its memory");
    Uses one_warp = plain; one_warp.invocations = 32;
    CHECK(!needed(lanes, true, one_warp), "one warp without shared memory: not pinned");

    std::printf(fails ? "compute_pin: FAILED\n" : "compute_pin: ok\n");
    return fails ? 1 : 0;
}
"""


def block(text, head):
    start = text.index(head)
    at = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[at] == "{") - (text[at] == "}")
        at += 1
    return text[start:at]


def check_source():
    draw = (ROOT / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    needed = block(draw, "bool compute_pin_needed(const Program& p) {")
    for needle, why in (("dev.driver_id == VK_DRIVER_ID_INTEL_OPEN_SOURCE_MESA", "the default is no longer Mesa's Intel driver alone"),
                        ('getenv("RX_VK_COMPUTE_PIN")', "the switch is gone"),
                        ("compute_program_uses(p, p.stages[0])", "the rule no longer reads the program's record")):
        if needed.find(needle) < 0:
            raise SystemExit("compute_pin_needed: " + why)
    uses = block(draw, "compute_pin::Uses compute_stage_uses(const Stage& st, uint32_t shared_memory) {")
    for flag in ("uses_subgroup_vote", "uses_subgroup_mask", "uses_subgroup_invocation_id", "uses_subgroup_shuffles",
                 "uses_quad_shuffles", "uses_cooperative_matrix", "uses_fswzadd"):
        if "st.info." + flag not in uses:
            raise SystemExit(f"compute_stage_uses no longer reads {flag}: a program using it would lose its pin")
    if "p.workgroup[0] * p.workgroup[1] * p.workgroup[2]" not in block(draw, "compute_pin::Uses compute_program_uses("):
        raise SystemExit("compute_program_uses no longer counts the workgroup's invocations")
    pin = block(draw, "void pin_compute(VkPipelineShaderStageCreateInfo& st, const Program& p) {")
    if "if (compute_pin_needed(p)) pin_subgroup(st, p.workgroup[0]);" not in pin:
        raise SystemExit("pin_compute no longer pins through compute_pin_needed")
    info = block(draw, "VkComputePipelineCreateInfo compute_pipeline_info(const Program& q, bool noopt) {")
    if "pin_compute(ci.stage, q);" not in info or "pin_subgroup(" in info:
        raise SystemExit("compute_pipeline_info does not pin through pin_compute")
    # (every game compute pipeline from compute_pipeline_info: one stage pinned by hand would ask the caches for another pipeline)
    if "pin_subgroup(ci.stage" in draw:
        raise SystemExit("a compute pipeline is pinned with pin_subgroup directly: use compute_pipeline_info")
    prepare = block(draw, "bool prepare_run(void (*progress)(size_t done, size_t total), bool background, bool refresh_states) {")
    if "const VkComputePipelineCreateInfo ci = compute_pipeline_info(p, true);" not in prepare:
        raise SystemExit("prepare_run's warm describes its compute pipelines itself, not through compute_pipeline_info")
    if "spirv" in needed or "spirv" in uses:
        raise SystemExit("the pin reads the SPIR-V: a program read without its code would ask for another pipeline")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="compute-pin-") as tmp:
        src, exe = Path(tmp) / "t.cpp", Path(tmp) / "t"
        src.write_text(MAIN.replace("../gpu/vk/compute_pin.h", str(ROOT / "gpu/vk/compute_pin.h")), encoding="utf-8")
        subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", str(src), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True, timeout=60)


if __name__ == "__main__":
    main()
