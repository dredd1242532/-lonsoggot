"""scripts/renhdrum.py: the pinned renhdrum.dll, over a made-up PE (no game, no network, no DLL of its own).

The script's job is to tell a clean checkout whether the renhdrum.dll beside the sources is the one this repository was
tested with, and whether host/haptic.c could use it at all. Both questions are answered from the file itself (its SHA-256,
the exports it binds, the DLLs it imports), so a synthetic PE with one section is enough to check every path: a good
copy passes; a copy whose imports name the VC++ Runtime fails and says why; a copy without the five exports fails and
names them; a copy whose bytes are not the pin's fails; no copy at all fails with what to do about it.
"""
import contextlib
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("renhdrum", ROOT / "scripts" / "renhdrum.py")
rh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rh)

EXPORTS = ("renhdrum_version", "renhdrum_dualsense_dsp_create", "renhdrum_dualsense_dsp_destroy",
           "renhdrum_dualsense_dsp_set_half_target", "renhdrum_dualsense_dsp_fill_f32")


def pe(imports=("kernel32.dll",), exports=EXPORTS, machine=0x8664, magic=0x20B):
    """A PE32+ image with one .rdata section: an export directory naming `exports`, an import table naming `imports`"""
    rva = 0x1000
    body = bytearray()

    def put(data, align=4):
        while len(body) % align:
            body.append(0)
        at = rva + len(body)
        body.extend(data)
        return at

    # (the strings first, so the directories can point at them)
    strings = {}
    for name in imports + exports:
        strings[name] = put(name.encode() + b"\0", 1)
    # (an export directory: 40 bytes, then AddressOfFunctions, AddressOfNames, AddressOfNameOrdinals)
    export_dir_at = put(b"\0" * 40)
    funcs = put(b"\0" * (4 * len(exports)))
    names = put(b"\0" * (4 * len(exports)))
    ords = put(b"\0" * (2 * len(exports)))
    for i, name in enumerate(exports):
        struct.pack_into("<I", body, names - rva + 4 * i, strings[name])
        struct.pack_into("<H", body, ords - rva + 2 * i, i)
    struct.pack_into("<I", body, export_dir_at - rva + 16, 1)              # Base
    struct.pack_into("<I", body, export_dir_at - rva + 20, len(exports))   # NumberOfFunctions
    struct.pack_into("<I", body, export_dir_at - rva + 24, len(exports))   # NumberOfNames
    struct.pack_into("<I", body, export_dir_at - rva + 28, funcs)          # AddressOfFunctions
    struct.pack_into("<I", body, export_dir_at - rva + 32, names)          # AddressOfNames
    struct.pack_into("<I", body, export_dir_at - rva + 36, ords)           # AddressOfNameOrdinals
    # (an import table: one 20-byte descriptor per DLL, then an all-zero one)
    import_dir_at = put(b"\0" * 20 * (len(imports) + 1))
    for i, name in enumerate(imports):
        struct.pack_into("<I", body, import_dir_at - rva + 20 * i + 12, strings[name])

    opt_size = 240
    section_at = 0x40 + 4 + 20 + opt_size
    raw = 0x200
    header = bytearray(0x40)
    header[0:2] = b"MZ"
    struct.pack_into("<I", header, 0x3C, 0x40)
    out = bytearray(header)
    out += b"PE\0\0"
    out += struct.pack("<HHIIIHH", machine, 1, 0, 0, 0, opt_size, 0x22)
    opt = bytearray(opt_size)
    struct.pack_into("<H", opt, 0, magic)
    dirs = 112 if magic == 0x20B else 96
    struct.pack_into("<II", opt, dirs + 0, export_dir_at, 40)          # the export directory
    struct.pack_into("<II", opt, dirs + 8, import_dir_at, 20 * (len(imports) + 1))   # the import directory
    out += opt
    out += struct.pack("<8sIIII", b".rdata", len(body), rva, raw, (len(body) + raw - 1) // raw * raw)
    out += b"\0" * (raw - len(out))
    out += body
    return bytes(out)


class PinnedDll(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="renhdrum-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, True))
        self.here = self.dir / "third_party" / "renhdrum"
        self.here.mkdir(parents=True)
        self.saved = (rh.HERE, rh.DLL, rh.PIN)
        rh.HERE = str(self.here)
        rh.DLL = str(self.here / "renhdrum.dll")
        rh.PIN = str(self.here / "renhdrum.dll.sha256")
        self.addCleanup(self.restore)

    def restore(self):
        rh.HERE, rh.DLL, rh.PIN = self.saved

    def pin(self, data):
        (self.here / "renhdrum.dll").write_bytes(data)
        (self.here / "renhdrum.dll.sha256").write_text(
            "# the pin\n%s  %d  renhdrum.dll\n" % (hashlib.sha256(data).hexdigest(), len(data)))

    def run_verify(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                code = rh.main(["verify"])
            except SystemExit as e:
                code = e.code
        return code, out.getvalue()

    def test_a_pinned_copy_passes(self):
        self.pin(pe())
        code, said = self.run_verify()
        self.assertEqual(code, 0, said)
        self.assertIn("this file is it", said)
        self.assertIn("usable", said)
        self.assertIn("kernel32.dll", said)

    def test_a_copy_needing_the_vc_runtime_fails_and_says_why(self):
        self.pin(pe(imports=("kernel32.dll", "VCRUNTIME140.dll")))
        code, said = self.run_verify()
        self.assertEqual(code, 1, said)
        self.assertIn("vcruntime140.dll", said)   # (the script lowercases what it reads)
        self.assertIn("crt-static", said)
        self.assertNotIn("usable:", said)

    def test_a_copy_without_the_exports_fails_and_names_them(self):
        self.pin(pe(exports=("renhdrum_version",)))
        code, said = self.run_verify()
        self.assertEqual(code, 1, said)
        for name in EXPORTS[1:]:
            self.assertIn("no export %s" % name, said)

    def test_other_bytes_fail_even_with_the_right_shape(self):
        good = pe()
        self.pin(good + b"\0" * 8)          # (the pin still says the shorter file: not the bytes measured)
        (self.here / "renhdrum.dll.sha256").write_text(
            "%s  %d  renhdrum.dll\n" % (hashlib.sha256(good).hexdigest(), len(good)))
        code, said = self.run_verify()
        self.assertEqual(code, 1, said)
        self.assertIn("not the pinned ones", said)

    def test_a_damaged_file_does_not_end_the_run_with_a_traceback(self):
        self.pin(pe()[:600])
        code, said = self.run_verify()
        self.assertEqual(code, 1, said)
        self.assertIn("damaged", said)

    def test_no_copy_says_what_to_do(self):
        code, said = self.run_verify()
        self.assertEqual(code, 1, said)
        self.assertIn("is not there", said)
        self.assertIn("build --src", said)

    def test_a_pin_that_is_not_a_pin_stops(self):
        (self.here / "renhdrum.dll").write_bytes(pe())
        (self.here / "renhdrum.dll.sha256").write_text("nonsense\n")
        code, said = self.run_verify()
        self.assertEqual(code, 1, said)
        self.assertIn("is not `<sha256>  <size>  renhdrum.dll`", said)

    def test_the_repositorys_own_copy_is_pinned(self):
        """When a copy is present it is the pin's (a checkout without one is fine: the game runs without it)"""
        dll = ROOT / "third_party" / "renhdrum" / "renhdrum.dll"
        pin = ROOT / "third_party" / "renhdrum" / "renhdrum.dll.sha256"
        self.assertTrue(pin.is_file(), "the pin travels with the sources: %s" % pin)
        if not dll.is_file():
            self.skipTest("no renhdrum.dll in this checkout")
        line = [l.split() for l in pin.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.strip().startswith("#")][0]
        self.assertEqual(dll.stat().st_size, int(line[1].replace(",", "")))
        self.assertEqual(rh.sha256_of(str(dll)), line[0].lower())


if __name__ == "__main__":
    unittest.main()
