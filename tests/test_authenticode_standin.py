"""Pure-Python stand-in export checks: no compiler, signer, key generation, server or executable run.

    python -I -B tests/test_authenticode_standin.py
"""
import contextlib
import hashlib
import importlib.util
import io
from pathlib import Path, PureWindowsPath
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("authenticode_standin", ROOT / "tests/authenticode_standin.py")
standin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(standin)


class StandinExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="authenticode-export-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "private"
        self.source.mkdir()
        self.output = self.root / "export"
        for name in ("unsigned.exe", "LongshotBuilder-Setup.exe", "LongshotBuilder.exe", "LongshotBuilder.dll"):
            (self.source / name).write_bytes(("authored bytes for " + name).encode())
        for name in ("code.pem", "TSA.pem"):
            (self.source / name).write_bytes(b"-----BEGIN CERTIFICATE-----\npublic fixture\n-----END CERTIFICATE-----\n")
        for name in ("code.key", "TSA.key", "test.c", "tsa.cnf", "tampered.exe", "tampered-package.zip", "test-trust.pem"):
            (self.source / name).write_bytes(b"must not be exported")
        with zipfile.ZipFile(self.source / "builder.zip", "w") as z:
            z.writestr("LongshotBuilder.exe", b"positive signed setup fixture")
            z.writestr("LongshotBuilder/LongshotBuilder-Setup.exe", b"positive signed setup fixture")
            z.writestr("LongshotBuilder/LongshotBuilder.exe", b"positive signed apphost fixture")
            z.writestr("LongshotBuilder/LongshotBuilder.dll", b"positive signed library fixture")

    def export(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            standin.export_bundle(self.source, self.output)
        return out.getvalue()

    def test_exports_only_named_public_files_and_correct_hashes(self):
        text = self.export()
        expected = {"unsigned-standin.exe", "signed-setup-standin.exe", "signed-apphost-standin.exe",
                    "signed-library-standin.dll", "standin-package.zip", "signer.pem", "timestamp.pem", "SHA256SUMS"}
        self.assertEqual({p.name for p in self.output.iterdir()}, expected)
        self.assertEqual((self.output / "standin-package.zip").read_bytes(), (self.source / "builder.zip").read_bytes())
        self.assertEqual((self.output / "signed-setup-standin.exe").read_bytes(), (self.source / "LongshotBuilder-Setup.exe").read_bytes())
        self.assertEqual((self.output / "signed-library-standin.dll").read_bytes(), (self.source / "LongshotBuilder.dll").read_bytes())
        sums = dict((name, digest) for digest, name in (line.split("  ", 1) for line in (self.output / "SHA256SUMS").read_text().splitlines()))
        self.assertEqual(set(sums), expected - {"SHA256SUMS"})
        for name, digest in sums.items():
            self.assertEqual(digest, hashlib.sha256((self.output / name).read_bytes()).hexdigest())
        self.assertIn("tiny authored stand-ins", text)
        self.assertIn("not Builder binaries", text)
        self.assertIn("No private key", text)
        self.assertFalse(any(p.suffix == ".key" for p in self.output.iterdir()))

    def test_refuses_nonempty_folder_without_replacing_anything(self):
        self.output.mkdir()
        keep = self.output / ".keep"
        keep.write_bytes(b"user data")
        with self.assertRaises(ValueError):
            self.export()
        self.assertEqual(list(self.output.iterdir()), [keep])
        self.assertEqual(keep.read_bytes(), b"user data")

    def test_accepts_an_existing_empty_folder(self):
        self.output.mkdir()
        self.export()
        self.assertTrue((self.output / "SHA256SUMS").is_file())

    def test_private_key_in_certificate_input_is_not_exported(self):
        (self.source / "code.pem").write_bytes(b"-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n")
        with self.assertRaises(ValueError):
            self.export()
        self.assertFalse(self.output.exists())

    def test_cli_refuses_nonempty_output_before_any_build_command(self):
        self.output.mkdir()
        (self.output / "keep.txt").write_text("keep")
        argv = [str(ROOT / "tests/authenticode_standin.py"), "--osslsigncode", "unused", "--out", str(self.output)]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(standin, "run") as run, \
                contextlib.redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit) as stopped:
            standin.main()
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("empty directory", error.getvalue())
        run.assert_not_called()

    def test_default_still_uses_temporary_files_without_export(self):
        class StopBeforeCompile(Exception):
            pass
        argv = [str(ROOT / "tests/authenticode_standin.py"), "--osslsigncode", "unused"]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(standin, "run", side_effect=StopBeforeCompile), \
                self.assertRaises(StopBeforeCompile):
            standin.main()
        self.assertFalse(self.output.exists())

    def test_cli_keeps_the_good_zip_not_the_negative_fixture(self):
        # Simulate only the external tools; run main's actual packaging, negative-file selection and export.
        image = bytearray(512)
        struct.pack_into("<I", image, 0x3c, 0x80)
        image[0x80:0x84] = b"PE\0\0"
        image[0x98:0xa0] = b".text\0\0\0"
        struct.pack_into("<I", image, 0x98 + 20, 256)
        image[256] = 0xc3

        def tool(args):
            if args[0] == "lld-link":
                Path(next(str(a)[5:] for a in args if str(a).startswith("/out:"))).write_bytes(image)
            elif len(args) > 1 and args[1] == "sign":
                Path(args[args.index("-out") + 1]).write_bytes(Path(args[args.index("-in") + 1]).read_bytes())
            return b""

        def certificate(folder, name, usage):
            key, cert = folder / (name + ".key"), folder / (name + ".pem")
            key.write_bytes(b"private fixture: never export")
            cert.write_bytes(b"-----BEGIN CERTIFICATE-----\npublic fixture\n-----END CERTIFICATE-----\n")
            return key, cert

        real_load = standin.load
        auth = mock.Mock()
        auth.SigningError = ValueError
        auth.ARCHIVE_FILES = ("LongshotBuilder.exe", "LongshotBuilder/LongshotBuilder-Setup.exe", "LongshotBuilder/LongshotBuilder.exe",
                              "LongshotBuilder/LongshotBuilder.dll")
        auth.fingerprint.return_value = "f" * 64
        auth.verify_file.side_effect = ValueError("negative signature fixture")

        def sign(files, *args, **kwargs):
            for file in files:
                file.write_bytes(file.read_bytes() + b"fixture signature")

        def verify_archive(archive, *args):
            with zipfile.ZipFile(archive) as z:
                if any(z.read(name)[256] != 0xc3 for name in auth.ARCHIVE_FILES):
                    raise ValueError("negative package fixture")

        auth.sign_files.side_effect = sign
        auth.verify_archive.side_effect = verify_archive
        argv = [str(ROOT / "tests/authenticode_standin.py"), "--osslsigncode", "unused", "--out", str(self.output)]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(standin, "run", side_effect=tool), \
                mock.patch.object(standin, "certificate", side_effect=certificate), \
                mock.patch.object(standin, "timestamp_server", return_value=contextlib.nullcontext("http://unused")), \
                mock.patch.object(standin, "load", side_effect=lambda n: auth if n == "authenticode" else real_load(n)), \
                contextlib.redirect_stdout(io.StringIO()):
            standin.main()
        with zipfile.ZipFile(self.output / "standin-package.zip") as z:
            self.assertEqual(z.read(auth.ARCHIVE_FILES[2]), (self.output / "signed-apphost-standin.exe").read_bytes())
            self.assertEqual(z.read(auth.ARCHIVE_FILES[2])[256], 0xc3)
            self.assertEqual(z.read(auth.ARCHIVE_FILES[3]), (self.output / "signed-library-standin.dll").read_bytes())

    def test_tsa_paths_are_quoted_for_windows_and_spaces(self):
        self.assertEqual(standin.config_path(PureWindowsPath(r"C:\Users\Example Name\TSA.key")),
                         '"C:/Users/Example Name/TSA.key"')
        self.assertEqual(standin.config_path(Path('/tmp/a"b\\c.pem')), '"/tmp/a\\"b\\\\c.pem"')


if __name__ == "__main__":
    unittest.main()
