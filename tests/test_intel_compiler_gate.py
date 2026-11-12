"""Windows Intel's compiler is entered by one thread, and a fault creating a shader module leaves that program out.

Intel UHD on Windows (igvk64) read address 0x170 during the first scene and the process ended. The stack was two frames
in hookshot_vk.dll and then the thread start, and the log had no "driver faulted compiling" line, so the call was not
inside guarded_graphics or guarded_compute. vkCreateShaderModule (translation, the movie and upscale helpers, a masked
clear) and gpl_link's vkCreateGraphicsPipelines were not guarded, and graphics pipelines were still built side by side.
NVIDIA, AMD, and Intel on other systems are not held. RX_VK_NO_INTEL_SERIAL leaves them side by side.

python -I -B tests/test_intel_compiler_gate.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def block(text, marker, start_at=0):
    start = text.index(marker, start_at)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def brace_block(text, open_at):
    """text[open_at] is '{'; return through its match, including the braces."""
    end = open_at + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[open_at:end]


def need(cond, why):
    if not cond:
        raise SystemExit("FAIL: " + why)


def main():
    internal = (ROOT / "gpu/vk/vk_internal.h").read_text(encoding="utf-8")
    gate = block(internal, "struct IntelDriverGate {")
    need("#ifdef _WIN32" in gate, "the lock is not limited to Windows")
    need('env_on("RX_VK_NO_INTEL_SERIAL")' in gate, "RX_VK_NO_INTEL_SERIAL does not turn the lock off")
    need("dev.props.vendorID != 0x8086" in gate, "a vendor other than Intel takes the lock")
    need("std::unique_lock<std::mutex>" in gate, "the compiler is not entered by one thread")
    draw = (ROOT / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
    win = block(draw, "__declspec(noinline) VkResult guarded_shader_module(")
    need("__try" in win and "vkCreateShaderModule" in win and "return (VkResult)-77;" in win,
         "a fault in vkCreateShaderModule is not turned into a left-out program")
    linux_at = draw.index("#else", draw.index("__declspec(noinline) VkResult guarded_shader_module("))
    linux = block(draw, "VkResult guarded_shader_module(", linux_at)
    need("sigsetjmp(fault_jmp, 1)" in linux and "vkCreateShaderModule" in linux,
         "Linux does not guard vkCreateShaderModule")
    for fn in ("VkResult cached_graphics(", "VkResult cached_compute("):
        body = block(draw, fn)
        need(body.index("IntelDriverGate") < body.index("create_cached"), fn + " compiles before it takes the lock")
    graphics = block(draw, "VkResult graphics_with(")
    need("return cached_graphics" in graphics, "the play-cache path takes the lock twice")
    link = block(draw, "VkPipeline gpl_link(")
    need(link.index("IntelDriverGate") < link.index("guarded_graphics"), "gpl_link's pipeline is not guarded")
    need("vkCreateGraphicsPipelines" not in link, "gpl_link still calls the driver with no guard")
    clear = block(draw, "VkPipeline clear_pipeline(")
    gate_at = clear.index("IntelDriverGate gate;")
    held = brace_block(clear, clear.rindex("{", 0, gate_at))
    need("cached_graphics(" not in held, "a masked clear holds the lock across cached_graphics (it takes the same lock)")
    need(clear.index(held) < clear.index("cached_graphics"), "the masked clear's modules are created after the pipeline")
    module = block((ROOT / "gpu/vk/vk_shader.cpp").read_text(encoding="utf-8"),
                   "VkShaderModule make_module(")
    need("IntelDriverGate" in module and "guarded_shader_module" in module, "translation still creates a module unguarded")
    temporal = (ROOT / "gpu/vk/vk_temporal.cpp").read_text(encoding="utf-8")
    pipe = block(temporal, "bool make_pipeline(")
    actor = block(temporal, "bool make_actor_pipeline()")
    need("guarded_shader_module" in pipe and "guarded_compute" in pipe, "a temporal compute pipeline is unguarded")
    need("guarded_shader_module" in actor and "guarded_graphics" in actor, "the actor-motion pipeline is unguarded")
    need("vkCreateShaderModule" not in pipe and "vkCreateComputePipelines" not in pipe, "make_pipeline still calls the driver raw")
    need("vkCreateGraphicsPipelines" not in actor, "make_actor_pipeline still calls the driver raw")
    print("Intel's compiler on Windows is entered by one thread, and a fault leaves that shader out: ok")


if __name__ == "__main__":
    main()
