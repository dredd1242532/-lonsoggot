"""8- and 16-bit stores: the global compare-exchange loop's page-table lookup and the 8/16-bit storage declarations.

The loop looked the byte's own address up in the page table as if it were its word's: it ran on a misaligned word, put
the byte in the wrong word, and a byte in a page's last three bytes was dropped. The 8/16-bit storage buffer views went
without their capabilities (StorageBuffer8BitAccess, StorageBuffer16BitAccess) and extensions. tests/narrow_global_store_ir.cpp
runs the real shader-info pass and EmitSPIRV for each case and checks its module. No GPU or game files needed.

    python -I -B tests/test_narrow_global_store.py --headers <SPIRV-Headers/include> [--cxx clang++] [--spirv-val spirv-val]
"""
import argparse
import concurrent.futures
import os
from pathlib import Path
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}", "--headers", "{spirv_headers}"]

ROOT = Path(__file__).resolve().parents[1]
VK = ROOT / "gpu" / "vk"
ENV_ON_H = str(VK / "compat" / "longshot_env.h")   # (env_on, the switches' rule)
CASES = ("loop-page-word", "storage-u8-view", "storage-u16-view", "loop-global-only")


def build_fixture(cxx, flags, sources, exe):
    """One object a source, as many at once as there are CPUs, then linked (as nx2/test_unsigned_image_types.py)"""
    (exe.parent / "obj").mkdir()
    objs = [exe.parent / "obj" / ("%03d.o" % i) for i in range(len(sources))]
    with concurrent.futures.ThreadPoolExecutor(os.cpu_count() or 2) as pool:
        list(pool.map(lambda job: subprocess.run([cxx, *flags, "-c", str(job[0]), "-o", str(job[1])], check=True),
                      zip(sources, objs)))
    subprocess.run([cxx, *map(str, objs), "-o", str(exe)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="clang++")
    parser.add_argument("--headers", type=Path, required=True)
    parser.add_argument("--spirv-val", help="also validate the modules (not needed for the checks)")
    args = parser.parse_args()
    sources = sorted({p for directory in ("eden/shader_recompiler/backend/spirv", "eden/shader_recompiler/frontend/ir",
                                          "eden/shader_recompiler/ir_opt", "sirit/src")
                      for p in (VK / directory).rglob("*.cpp")})
    flags = ["-std=c++20", "-include", ENV_ON_H, "-O1", "-D_CRT_SECURE_NO_WARNINGS", "-DVK_NO_PROTOTYPES", "-DMKWVK_INCLUDES=2"]
    for header in ("algorithm", "cstdio", "cstdlib", "cstring", "functional", "string", "vector"):
        flags += ["-include", header]
    flags += ["-I" + str(p) for p in (VK / "compat", ROOT / "gpu/compat", VK / "eden", ROOT / "gpu/third_party/suyu",
                                      VK / "sirit/include", VK / "sirit/src", args.headers)]
    # (the translator's switches change what it emits: none of them for the fixture)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("RX_VK_", "EDEN_"))}
    work = ROOT / "work"
    work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="narrow-global-store-", dir=work) as temp:
        folder = Path(temp).resolve()
        exe = folder / "fixture.exe"
        build_fixture(args.cxx, flags, [ROOT / "tests/narrow_global_store_ir.cpp", *sources], exe)
        r = subprocess.run([str(exe), str(folder)], env=env, capture_output=True, text=True, timeout=120)
        print(r.stdout + r.stderr, end="")
        if r.returncode:
            raise SystemExit("FAIL: the narrow store modules")
        if args.spirv_val:
            for case in CASES:
                subprocess.run([args.spirv_val, "--target-env", "vulkan1.3", "--uniform-buffer-standard-layout",
                                str(folder / ("%s.spv" % case))], check=True)
            print("spirv-val: %d modules ok" % len(CASES))
    print("PASS: narrow stores: the global loop looks up the aligned word; 8/16-bit storage declared with its views alone")


if __name__ == "__main__":
    main()
