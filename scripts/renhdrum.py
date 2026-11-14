"""The pinned renhdrum.dll: verify the one in third_party/renhdrum, or make one that can be verified.

    python -I scripts/renhdrum.py verify
    python -I scripts/renhdrum.py build --src <a RenHDrum checkout at v0.3.0>
    python -I scripts/renhdrum.py fetch <a url or a path to a renhdrum.dll>

Why a script and not a note in the README: the DLL is not committed (the repository's .gitignore excludes `*.dll`, as it
does SDL2.dll), so a clean checkout has no way to tell a good copy from a wrong one. `verify` says which one is there;
`build` makes it from the pinned source with the flags that keep the C runtime static (so a player needs no VC++
Runtime); `fetch` takes a copy from anywhere and refuses it unless its bytes are the pinned ones.

The pin is `third_party/renhdrum/renhdrum.dll.sha256` (tracked, so it travels with the sources): the size and the SHA-256
of the DLL this repository was tested with, and the version the ABI check in host/haptic.c expects.
"""
import argparse
import hashlib
import os
import shutil
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.join(ROOT, "third_party", "renhdrum")
DLL = os.path.join(HERE, "renhdrum.dll")
PIN = os.path.join(HERE, "renhdrum.dll.sha256")
VERSION = 0x000300
# (the DLL's own imports, as the static CRT leaves them: anything else means the VC++ Runtime is needed, which a player
# may not have. host/haptic.c loads it with LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32, so a
# missing runtime would only cost the haptics -- but a release should not lean on that)
ALLOWED_IMPORTS = {
    "kernel32.dll", "ntdll.dll",
    "api-ms-win-core-synch-l1-2-0.dll",
    "api-ms-win-core-synch-l1-1-0.dll",
    "api-ms-win-core-sysinfo-l1-1-0.dll",
    "api-ms-win-core-libraryloader-l1-2-0.dll",
    "api-ms-win-core-errorhandling-l1-1-0.dll",
    "api-ms-win-core-handle-l1-1-0.dll",
    "api-ms-win-core-memory-l1-1-0.dll",
    "api-ms-win-core-file-l1-1-0.dll",
    "api-ms-win-core-synch-l1-2-1.dll",
    "api-ms-win-core-profile-l1-1-0.dll",
    "api-ms-win-core-string-l1-1-0.dll",
    "api-ms-win-core-interlocked-l1-1-0.dll",
    "api-ms-win-core-debug-l1-1-0.dll",
    "api-ms-win-core-rtlsupport-l1-1-0.dll",
    "api-ms-win-core-processenvironment-l1-1-0.dll",
    "api-ms-win-core-localization-l1-2-0.dll",
}


def shown(path):
    """A path as a message shows it: relative to the repository, or absolute when it is on another drive"""
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:
        return os.path.abspath(path)


def fail(message):
    print("renhdrum: " + message)
    raise SystemExit(1)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_pin():
    """(sha256, size) as the pin file says: `<sha256>  <size>  renhdrum.dll`, a comment allowed with #"""
    if not os.path.isfile(PIN):
        fail("no %s: the pin that says which renhdrum.dll this repository was tested with"
             % shown(PIN))
    with open(PIN, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            fail("%s: %r is not `<sha256>  <size>  renhdrum.dll`" % (shown(PIN), line))
        try:
            size = int(parts[1].replace(",", ""))
        except ValueError:
            fail("%s: %r is not a size" % (shown(PIN), parts[1]))
        return parts[0].lower(), size
    fail("%s has no pin in it" % shown(PIN))


# ---- what the file is, without a PE tool ----

def pe_imports_and_exports(path):
    """(the DLL names it imports, the names it exports, the machine word of its header), from the PE itself"""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 0x40 or data[:2] != b"MZ":
        fail("%s is not a Windows executable" % shown(path))
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        fail("%s has no PE header" % shown(path))
    machine, sections, _, _, _, opt_size, _ = struct.unpack_from("<HHIIIHH", data, pe + 4)
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic == 0x20B:      # PE32+
        dirs = opt + 112
    elif magic == 0x10B:    # PE32
        dirs = opt + 96
    else:
        fail("%s: optional header magic %#x is neither PE32 nor PE32+" % (shown(path), magic))

    def rva_to_offset(rva):
        table = opt + opt_size
        for i in range(sections):
            name, vsize, vaddr, rawsize, rawptr = struct.unpack_from("<8sIIII", data, table + i * 40)
            if vaddr <= rva < vaddr + max(vsize, rawsize):
                return rawptr + (rva - vaddr)
        fail("%s: %#x is in no section" % (shown(path), rva))

    def names_at(dir_rva):
        """The names of an export directory: its AddressOfNames array is a pointer of its own, not a field beside it"""
        dir_off = rva_to_offset(dir_rva)
        count = struct.unpack_from("<I", data, dir_off + 24)[0]
        array = struct.unpack_from("<I", data, dir_off + 32)[0]
        out = []
        if not array:
            return out
        base = rva_to_offset(array)
        for i in range(min(count, 4096)):
            name_rva = struct.unpack_from("<I", data, base + i * 4)[0]
            if not name_rva:
                continue
            start = rva_to_offset(name_rva)
            end = data.index(b"\0", start)
            out.append(data[start:end].decode("ascii", "replace"))
        return out

    imports = []
    try:
        imp_rva = struct.unpack_from("<I", data, dirs + 8)[0]
        if imp_rva:
            off = rva_to_offset(imp_rva)
            while True:
                entry = data[off:off + 20]
                if len(entry) < 20 or entry == b"\0" * 20:
                    break
                name_rva = struct.unpack_from("<I", entry, 12)[0]
                if name_rva:
                    start = rva_to_offset(name_rva)
                    imports.append(data[start:data.index(b"\0", start)].decode("ascii", "replace"))
                off += 20
        exports = []
        exp_rva = struct.unpack_from("<I", data, dirs)[0]
        if exp_rva:
            exports = names_at(exp_rva)
    except (struct.error, ValueError) as e:
        # (a truncated or damaged file: the pin already says so, and this must not end the run with a traceback)
        print("  the PE headers stop short (%s): the file is damaged or not a DLL" % e)
        return [i.lower() for i in imports], [], machine
    return [i.lower() for i in imports], exports, machine


def describe(path):
    """Check one file against the pin and against what host/haptic.c needs; return True when it is usable"""
    if not os.path.isfile(path):
        fail("%s is not there" % shown(path))
    size = os.path.getsize(path)
    digest = sha256_of(path)
    want_sha, want_size = read_pin()
    pinned = digest == want_sha and size == want_size
    print("%s: %d bytes, sha256 %s" % (shown(path), size, digest))
    print("the pin: %d bytes, sha256 %s%s" % (want_size, want_sha, "  (this file is it)" if pinned else ""))
    imports, exports, machine = pe_imports_and_exports(path)
    if machine != 0x8664:
        print("  machine %#x: not x86-64" % machine)
    print("  imports: %s" % (", ".join(sorted(imports)) or "(none)"))
    wanted = ("renhdrum_version", "renhdrum_dualsense_dsp_create", "renhdrum_dualsense_dsp_destroy",
              "renhdrum_dualsense_dsp_set_half_target", "renhdrum_dualsense_dsp_fill_f32")
    if exports:
        print("  exports: %d names, the five host/haptic.c binds: %s"
              % (len(exports), ", ".join(n for n in wanted if n in exports) or "(none)"))
    else:
        print("  exports: (none)")
    ok = True
    for name in wanted:
        if name not in exports:
            print("  no export %s: host/haptic.c cannot use this DLL" % name)
            ok = False
    extra = [i for i in imports if i not in ALLOWED_IMPORTS]
    if extra:
        print("  imports outside the static runtime: %s" % ", ".join(extra))
        print("  (the DLL needs the VC++ Runtime then; build it with RUSTFLAGS=-C target-feature=+crt-static)")
        ok = False
    if not pinned:
        print("  the bytes are not the pinned ones: the shipped build is the one that was measured")
        ok = False
    if ok:
        print("  usable: the pinned bytes, the five exports host/haptic.c binds, a static runtime")
    return ok, exports


def check_version(path, exports):
    """The version the DLL reports, through its own export (so the ABI host/haptic.c checks is the one measured)"""
    if "renhdrum_version" not in exports:
        return None
    import ctypes
    try:
        lib = ctypes.CDLL(path)
        lib.renhdrum_version.restype = ctypes.c_uint32
        return lib.renhdrum_version()
    except (OSError, AttributeError) as e:
        print("  cannot load it here: %s" % e)
        return None


def cmd_verify(a):
    if not os.path.isfile(DLL):
        print("renhdrum: %s is not there." % shown(DLL))
        print("  The game runs without it (standard rumble only, `Haptic backend unavailable` in the menu).")
        print("  To have the fine haptics: python -I scripts/renhdrum.py build --src <a RenHDrum checkout at v0.3.0>")
        print("  or python -I scripts/renhdrum.py fetch <a url or a path to a renhdrum.dll>")
        return 1
    ok, exports = describe(DLL)
    version = check_version(os.path.abspath(DLL), exports)
    if version is not None:
        print("  renhdrum_version() = %#08x%s" % (version, "" if version == VERSION else
                                                  "  (host/haptic.c wants %#08x)" % VERSION))
        ok = ok and version in (VERSION, VERSION | 1)
    return 0 if ok else 1


def cmd_build(a):
    src = os.path.abspath(a.src)
    if not os.path.isfile(os.path.join(src, "Cargo.toml")):
        fail("%s has no Cargo.toml: that is not a RenHDrum checkout" % src)
    env = dict(os.environ)
    env["RUSTFLAGS"] = "-C target-feature=+crt-static"
    print("renhdrum: cargo build --release in %s (RUSTFLAGS=-C target-feature=+crt-static)" % src)
    r = subprocess.run(["cargo", "build", "--release"], cwd=src, env=env)
    if r.returncode:
        fail("cargo build failed (%d)" % r.returncode)
    built = os.path.join(src, "target", "release", "renhdrum.dll")
    if not os.path.isfile(built):
        fail("no target/release/renhdrum.dll in %s" % src)
    os.makedirs(HERE, exist_ok=True)
    shutil.copyfile(built, DLL)
    print("renhdrum: copied %s to %s" % (built, shown(DLL)))
    return cmd_verify(a)


def cmd_fetch(a):
    os.makedirs(HERE, exist_ok=True)
    if a.source.startswith(("http://", "https://")):
        import urllib.request
        tmp = DLL + ".download"
        print("renhdrum: downloading %s" % a.source)
        with urllib.request.urlopen(a.source, timeout=120) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        os.replace(tmp, DLL)
    else:
        src = os.path.abspath(a.source)
        if not os.path.isfile(src):
            fail("%s is not a file" % src)
        shutil.copyfile(src, DLL)
        print("renhdrum: copied %s to %s" % (src, shown(DLL)))
    return cmd_verify(a)


def main(argv=None):
    # (a Windows console can be in a legacy code page: a name out of a binary should never end the run with a
    # UnicodeEncodeError)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description="The pinned renhdrum.dll: verify it, build it, or take a copy of it.")
    s = p.add_subparsers(dest="what", required=True)
    v = s.add_parser("verify", help="check third_party/renhdrum/renhdrum.dll against the pin and host/haptic.c's needs")
    v.set_defaults(fn=cmd_verify)
    b = s.add_parser("build", help="build it from a RenHDrum checkout with the static runtime")
    b.add_argument("--src", required=True)
    b.set_defaults(fn=cmd_build)
    f = s.add_parser("fetch", help="take a copy from a url or a path, and verify it")
    f.add_argument("source")
    f.set_defaults(fn=cmd_fetch)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
