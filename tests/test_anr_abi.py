"""Call the production ANR parameter adapter from an independently compiled MSVC-ABI mock DLL.

Windows x64 only: a CRT-free Microsoft-target DLL calls all 17 virtual slots in the
llvm-mingw host. No NVIDIA SDK, ANR binary, model, game or GPU is needed.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]
RUN_ALL_SKIP = {"linux": "Windows x64 DLL loader and MSVC/MinGW calling boundary"}

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="clang++")
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("This fixture needs Windows x64; tests/run_all.py declares the Linux skip")
    cxx = Path(shutil.which(args.cxx) or args.cxx).resolve()
    clang = next(iter(sorted(cxx.parent.glob("clang-[0-9]*.exe"), reverse=True)), None)
    clang = clang or shutil.which("clang")
    linker = cxx.parent / "ld.lld.exe"
    if not clang or not linker.is_file():
        raise SystemExit("ANR cross-ABI check needs LLVM clang and ld.lld beside the llvm-mingw compiler")
    with tempfile.TemporaryDirectory(prefix="anr-abi-") as temp:
        folder = Path(temp) / "interface-a\u00e7\u00e3o"
        folder.mkdir()
        obj, dll, exe = folder / "mock.obj", folder / "mock.dll", folder / "host.exe"
        subprocess.run([str(clang), "--target=x86_64-pc-windows-msvc", "-std=c++17", "-fno-exceptions", "-fno-rtti",
                        "-fno-stack-protector", "-O2", "-c", str(ROOT / "tests/anr_abi_mock.cpp"), "-o", str(obj)],
                       check=True, timeout=30)
        subprocess.run([str(linker), "-flavor", "link", "/dll", "/noentry", "/nodefaultlib", "/machine:x64",
                        "/out:" + str(dll), str(obj), "/export:anr_abi_probe"], check=True, timeout=30)
        subprocess.run([str(cxx), "-std=c++17", "-O2", "-static", "-Wall", "-Wextra", "-Werror", "-municode",
                        "-I" + str(ROOT / "gpu/vk"), str(ROOT / "tests/anr_abi_host.cpp"), "-o", str(exe)],
                       check=True, timeout=30)
        subprocess.run([str(exe), str(dll)], check=True, timeout=15)


if __name__ == "__main__":
    main()
