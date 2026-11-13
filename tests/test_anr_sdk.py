"""The optional ANR addon: fake HTTP/ZIP only, no real addon, game, compiler or GPU.

python -I -B tests/test_anr_sdk.py
"""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


anr = load("anr_sdk_under_test", "scripts/anr_sdk.py")
make_game = load("make_game_anr_under_test", "nx2/make_game.py")


def addon(extra=(), missing=()):
    data = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as z:
        warnings.simplefilter("ignore", UserWarning)
        for name in anr.FILES:
            if name not in missing:
                z.writestr(anr.PREFIX + "/" + name, ("authored " + name).encode())
        z.writestr("Apex Neural Rendering FG/install.bat", b"must never be installed or run")
        for name, contents, mode in extra:
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            z.writestr(info, contents)
    return data.getvalue()


class ContractTests(unittest.TestCase):
    def test_pinned_contract(self):
        self.assertEqual(anr.TOOL.sha256, "717e93b3e63cb08dbaf6240cd585bae94d498c2749d8603d800d17ad97ef9e41")
        self.assertEqual(anr.TOOL.size, 3231158)
        self.assertEqual(anr.TOOL.urls, ["https://github.com/apex-compute/ANR/releases/download/FG-V.B15/anr-fg-V.B15-addon.zip"])
        self.assertEqual(anr.TOOL.prefix, "Apex Neural Rendering FG/dlssg")
        self.assertEqual(len(anr.FILES), 19)
        self.assertEqual(len(set(anr.FILES)), 19)


class AddonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="anr-sdk-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache, self.game = self.root / "cache", self.root / "game"
        self.data = addon()
        self.pin(self.data)
        self.supported = mock.patch.object(anr, "supported", return_value=True)
        self.supported.start()
        self.addCleanup(self.supported.stop)

    def pin(self, data):
        tool = anr.tools.Tool("anr", anr.VERSION, "windows-x64", hashlib.sha256(data).hexdigest(), len(data), "anr", anr.PREFIX,
                              [anr.URL])
        patch = mock.patch.object(anr, "TOOL", tool)
        patch.start()
        self.addCleanup(patch.stop)

    def fetch(self, data=None):
        with mock.patch.object(anr.tools.urllib.request, "urlopen", return_value=io.BytesIO(self.data if data is None else data)):
            return anr.fetch(str(self.cache), say=lambda _: None)

    def paths(self):
        return sorted(str(p.relative_to(self.game)).replace(os.sep, "/") for p in self.game.rglob("*") if p.is_file())

    def test_http_cache_and_only_the_complete_subtree_installed(self):
        with mock.patch.object(anr.tools.urllib.request, "urlopen", return_value=io.BytesIO(self.data)) as opened:
            archive = anr.fetch(str(self.cache), say=lambda _: None)
        self.assertEqual(opened.call_args.args[0].full_url, anr.URL)
        with mock.patch.object(anr.tools.urllib.request, "urlopen", side_effect=AssertionError("cache downloaded again")):
            self.assertEqual(anr.fetch(str(self.cache), say=lambda _: None), archive)
        placed = anr.install(archive, str(self.game), say=lambda _: None)
        self.assertEqual(placed, str(self.game / "anr"))
        self.assertEqual(self.paths(), sorted("anr/" + name for name in anr.FILES))
        self.assertEqual(list(self.game.glob(".anr-*")), [])

    def test_bad_size_or_hash_never_published_and_partial_cleaned(self):
        for bad in (self.data[:-1], bytes([self.data[0] ^ 1]) + self.data[1:], self.data + b"too large"):
            with self.subTest(size=len(bad)), self.assertRaises(ValueError):
                self.fetch(bad)
            self.assertFalse(any(self.cache.iterdir()))
        self.assertFalse(self.game.exists())

    def test_interrupted_download_cleans_partial(self):
        class Cut(io.BytesIO):
            def read(self, size=-1):
                if self.tell():
                    raise OSError("authored interruption")
                return super().read(20)
        with mock.patch.object(anr.tools.urllib.request, "urlopen", return_value=Cut(self.data)), self.assertRaises(ValueError):
            anr.fetch(str(self.cache), say=lambda _: None)
        self.assertFalse(any(self.cache.iterdir()))

    def test_damaged_cache_is_fetched_again(self):
        archive = Path(self.fetch())
        archive.write_bytes(b"broken cache")
        self.assertEqual(Path(self.fetch()).read_bytes(), self.data)

    def test_malformed_subtree_rejected_before_install(self):
        lead = anr.PREFIX + "/"
        cases = [
            addon(missing=("kernels/pack.nrx",)),
            addon(extra=[(lead + "../outside", b"escape", stat.S_IFREG)]),
            addon(extra=[(lead + "/absolute", b"escape", stat.S_IFREG)]),
            addon(extra=[(lead + "C:/absolute", b"escape", stat.S_IFREG)]),
            addon(extra=[(lead + "..\\outside", b"escape", stat.S_IFREG)]),
            addon(extra=[(lead + "NVNGX_DLSSG.DLL", b"duplicate", stat.S_IFREG)]),
            addon(extra=[(lead + "nvngx_dlssg.dll", b"duplicate", stat.S_IFREG)]),
            addon(extra=[(lead + "nvngx_dlssg.dll", b"../outside", stat.S_IFLNK)]),
        ]
        for data in cases:
            with self.subTest(sha=hashlib.sha256(data).hexdigest()):
                self.pin(data)
                with self.assertRaises(ValueError):
                    self.fetch(data)
                self.assertFalse(self.game.exists())
                self.assertFalse((self.root / "outside").exists())

    def test_invalid_zip_rejected(self):
        self.pin(b"not a zip")
        with self.assertRaises(zipfile.BadZipFile):
            self.fetch(b"not a zip")
        self.assertFalse(self.game.exists())

    def test_install_rechecks_archive_before_touching_game(self):
        archive = Path(self.fetch())
        archive.write_bytes(b"changed after download")
        with self.assertRaises(ValueError):
            anr.install(str(archive), str(self.game), say=lambda _: None)
        self.assertFalse(self.game.exists())

    def test_existing_manual_package_left_unchanged(self):
        archive = self.fetch()
        dst = self.game / "anr"
        dst.mkdir(parents=True)
        (dst / "nvngx_dlssg.dll").write_bytes(b"a player's different DLL")
        said = []
        self.assertIsNone(anr.install(archive, str(self.game), say=said.append))
        self.assertEqual((dst / "nvngx_dlssg.dll").read_bytes(), b"a player's different DLL")
        self.assertEqual(self.paths(), ["anr/nvngx_dlssg.dll"])
        self.assertIn("left unchanged", said[0])

    def test_reinstall_and_repair_preserve_the_players_ini(self):
        archive = self.fetch()
        dst = Path(anr.install(archive, str(self.game), say=lambda _: None))
        config = dst / "nr_fg.ini"
        config.write_bytes(b"a player's settings")
        anr.install(archive, str(self.game), say=lambda _: None)
        self.assertEqual(config.read_bytes(), b"a player's settings")
        (dst / "kernels/pack.nrx").unlink()
        anr.install(archive, str(self.game), say=lambda _: None)
        self.assertEqual(config.read_bytes(), b"a player's settings")
        self.assertEqual(self.paths(), sorted("anr/" + name for name in anr.FILES))

    def test_complete_package_with_provider_logs_is_ready_without_writes(self):
        archive = self.fetch()
        dst = Path(anr.install(archive, str(self.game), say=lambda _: None))
        (dst / "nr_fg.ini").write_bytes(b"a player's settings")
        log = dst / "nr_fg-20261113.log"
        log.write_bytes(b"provider output")
        before = {p: (self.game / p).read_bytes() for p in self.paths()}
        original_hash = anr.tools.sha256_file
        def no_log_reads(path):
            self.assertNotEqual(Path(path), log, "provider logs must not be opened")
            return original_hash(path)
        with mock.patch.object(anr.tools, "sha256_file", side_effect=no_log_reads), \
                mock.patch.object(anr.tools, "unpack", side_effect=AssertionError("complete package rewritten")), \
                mock.patch.object(anr.os, "replace", side_effect=AssertionError("complete package rewritten")):
            self.assertEqual(anr.install(archive, str(self.game), say=lambda _: None), str(dst))
        self.assertEqual({p: (self.game / p).read_bytes() for p in self.paths()}, before)

    def test_incomplete_package_with_provider_logs_is_preserved(self):
        archive = self.fetch()
        dst = Path(anr.install(archive, str(self.game), say=lambda _: None))
        (dst / "kernels/pack.nrx").unlink()
        (dst / "nr_fg.ini").write_bytes(b"a player's settings")
        (dst / "nr_fg-20261113.log").write_bytes(b"provider output")
        before = {p: (self.game / p).read_bytes() for p in self.paths()}
        with mock.patch.object(anr.tools, "unpack", side_effect=AssertionError("package with logs replaced")):
            self.assertIsNone(anr.install(archive, str(self.game), say=lambda _: None))
        self.assertEqual({p: (self.game / p).read_bytes() for p in self.paths()}, before)

    def test_failed_publish_restores_previous_package(self):
        archive = self.fetch()
        dst = Path(anr.install(archive, str(self.game), say=lambda _: None))
        (dst / "kernels/pack.nrx").unlink()
        (dst / "nr_fg.ini").write_bytes(b"keep settings")
        before = {p: (self.game / p).read_bytes() for p in self.paths()}
        replace = os.replace
        def locked(src, target):
            if Path(src).name == "package":
                raise PermissionError("authored locked destination")
            return replace(src, target)
        with mock.patch.object(anr.os, "replace", side_effect=locked), self.assertRaises(PermissionError):
            anr.install(archive, str(self.game), say=lambda _: None)
        self.assertEqual({p: (self.game / p).read_bytes() for p in self.paths()}, before)
        self.assertEqual(list(self.game.glob(".anr-*")), [])

    def test_failed_rollback_keeps_original_package_and_reports_backup(self):
        archive = self.fetch()
        dst = Path(anr.install(archive, str(self.game), say=lambda _: None))
        (dst / "kernels/pack.nrx").unlink()
        (dst / "nr_fg.ini").write_bytes(b"keep settings")
        replace = os.replace
        def locked(src, target):
            if Path(src).name in ("package", "previous"):
                raise PermissionError("authored locked destination")
            return replace(src, target)
        with mock.patch.object(anr.os, "replace", side_effect=locked), self.assertRaisesRegex(OSError, "original package is preserved"):
            anr.install(archive, str(self.game), say=lambda _: None)
        backups = list(self.game.glob(".anr-*/previous"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "nr_fg.ini").read_bytes(), b"keep settings")

    def test_reparse_package_is_not_traversed_or_replaced(self):
        archive = self.fetch()
        (self.game / "anr").mkdir(parents=True)
        with mock.patch.object(anr, "linked", return_value=True), \
                mock.patch.object(anr.os, "walk", side_effect=AssertionError("followed reparse package")):
            self.assertIsNone(anr.install(archive, str(self.game), say=lambda _: None))
        self.assertTrue((self.game / "anr").is_dir())

    def test_unreadable_existing_tree_is_preserved(self):
        archive = self.fetch()
        anr.install(archive, str(self.game), say=lambda _: None)
        def unreadable(*args, **kwargs):
            kwargs["onerror"](PermissionError("authored unreadable package"))
            return iter(())
        with mock.patch.object(anr.os, "walk", side_effect=unreadable), self.assertRaises(PermissionError):
            anr.install(archive, str(self.game), say=lambda _: None)
        self.assertEqual(self.paths(), sorted("anr/" + name for name in anr.FILES))

    def test_off_and_unsupported_do_not_download_or_create_folders(self):
        with mock.patch.object(anr, "fetch", side_effect=AssertionError("Off downloaded")):
            self.assertEqual(anr.main([str(self.cache), "--game", str(self.game)]), 0)
        with mock.patch.object(anr, "supported", return_value=False), \
                mock.patch.object(anr.tools, "fetch", side_effect=AssertionError("unsupported downloaded")), self.assertRaises(ValueError):
            anr.fetch(str(self.cache))
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.game.exists())


class BuilderTests(unittest.TestCase):
    def test_off_does_not_even_load_the_helper(self):
        args = SimpleNamespace(fetch_anr_runtime=False, work="unused")
        with mock.patch.object(make_game, "anr_script", side_effect=AssertionError("Off loaded helper")):
            self.assertIsNone(make_game.fetch_anr_runtime(args))
            self.assertIsNone(make_game.place_anr_runtime(args, "unused"))

    def test_builder_cache_contract_and_no_install_network(self):
        args = SimpleNamespace(fetch_anr_runtime=True, work="authored-work")
        helper = mock.Mock(NOTICE="notice", TOOL=SimpleNamespace(sha256="authored-sha"))
        with mock.patch.object(make_game, "anr_script", return_value=helper), mock.patch.object(make_game, "LINUX", False), \
                mock.patch("sys.stdout", io.StringIO()):
            make_game.fetch_anr_runtime(args)
            make_game.place_anr_runtime(args, "authored-game")
        helper.fetch.assert_called_once()
        self.assertEqual(helper.fetch.call_args.args, (os.path.join(args.work, "anr-sdk"),))
        self.assertEqual(helper.install.call_args.args, (os.path.join(args.work, "anr-sdk", "authored-sha.zip"), "authored-game"))

    def test_unsupported_builder_fails_before_fetch(self):
        helper = mock.Mock(supported=mock.Mock(return_value=False))
        with mock.patch.object(make_game, "anr_script", return_value=helper), \
                mock.patch.object(make_game, "say"), self.assertRaises(SystemExit):
            make_game.fetch_anr_runtime(SimpleNamespace(fetch_anr_runtime=True, work="unused"))
        helper.fetch.assert_not_called()

    def test_flag_off_by_default(self):
        with mock.patch("sys.argv", ["make_game.py"]):
            self.assertFalse(make_game.parse_args().fetch_anr_runtime)

    def test_only_verified_install_reports_ready(self):
        helper = mock.Mock(TOOL=SimpleNamespace(sha256="authored-sha"))
        args = SimpleNamespace(fetch_anr_runtime=True, work="unused")
        with mock.patch.object(make_game, "anr_script", return_value=helper):
            for placed in (None, "authored-game/anr"):
                helper.install.return_value = placed
                with mock.patch("sys.stdout", io.StringIO()) as output:
                    self.assertEqual(make_game.place_anr_runtime(args, "authored-game"), placed)
                self.assertEqual("@@anr-runtime ready" in output.getvalue(), bool(placed))


if __name__ == "__main__":
    unittest.main()
