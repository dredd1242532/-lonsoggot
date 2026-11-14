"""Build a64emit (suyu's AArch64 -> C instruction translator) as a shared library for a64_lift.py.

Usage: python build_emit.py -> a64emit.dll (Windows), liba64emit.so (Linux), or
liba64emit-<process-arch>.dylib (macOS), next to this file. Source changes rebuild;
macOS also keys the compiler/version/flags, independently of RX_ARCH's game target.
"""
import hashlib, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import toolchain

SRC = [os.path.join(HERE, "a64emit.cpp"), os.path.join(HERE, "suyu", "arm64_to_c.h")]
OUT = os.path.join(HERE, "a64emit.dll" if os.name == "nt" else "liba64emit.so")
if sys.platform == "darwin":
    OUT = os.path.join(HERE, "liba64emit-%s.dylib" % toolchain.architecture(host=True))


def command():
    """(longshot) Host-process compiler command, callable without building the helper."""
    cmd = [toolchain.need(toolchain.find("clang++", host=True)), "-std=c++20", "-O2",
           "-dynamiclib" if sys.platform == "darwin" else "-shared", "-o", OUT, SRC[0],
           "-Wno-everything"] + toolchain.native_target(host=True) + toolchain.native_link(host=True)
    if os.name != "nt":
        cmd += ["-fPIC"]
    return cmd


def build(force=False):
    body = b"".join(open(f, "rb").read() for f in SRC)
    cmd = None
    if sys.platform == "darwin":
        cmd = command()
        body += ("\n".join(cmd) + "\n" + toolchain.version("clang++", host=True)).encode()
    key = hashlib.sha1(body).hexdigest()
    stamp = OUT + ".sha1"
    if not force and os.path.exists(OUT) and os.path.exists(stamp) and open(stamp).read() == key:
        return OUT
    cmd = cmd or command()
    print("building", os.path.basename(OUT))
    subprocess.run(cmd, check=True)
    open(stamp, "w").write(key)
    return OUT


if __name__ == "__main__":
    print(build(force="--force" in sys.argv))
