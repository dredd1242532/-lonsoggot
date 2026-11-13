"""Signed pre-accept room modes and refusal reasons through the actual progress engine."""
import argparse
from pathlib import Path
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cc", "gcc"]
ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cc", default="gcc")
    p.add_argument("--source-root", type=Path, default=ROOT)
    args = p.parse_args()
    root = args.source_root.resolve()
    with tempfile.TemporaryDirectory(prefix="coop-room-preview-") as name:
        folder = Path(name)
        obj, exe = folder / "mono.o", folder / "preview.exe"
        subprocess.run([args.cc, "-std=c99", "-O1", "-w", "-c", str(root / "third_party/monocypher/monocypher.c"), "-o", str(obj)], check=True)
        subprocess.run([args.cc, "-std=c11", "-O1", "-Wall", "-Wextra", "-Werror", "-D_CRT_SECURE_NO_WARNINGS",
                        "-I" + str(root / "host"), str(ROOT / "tests/coop_room_preview_test.c"),
                        str(root / "host/coop_proto.c"), str(obj), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
