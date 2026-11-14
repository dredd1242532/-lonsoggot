"""Exercise actual production stage-all/copy/swap statements with authored temporary files.

python -I -B tests/test_finish_output.py
"""
import ast
import contextlib
import ctypes
import errno
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "nx2" / "make_game.py"
spec = importlib.util.spec_from_file_location("make_game_finish", SOURCE)
make_game = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_game)


def finish_statements(source, filename):
    main = next(node for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef) and node.name == "main")
    starts = [i for i, node in enumerate(main.body) if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == "staged" for target in node.targets)]
    assert starts
    start = starts[0]
    end = next(i for i, node in enumerate(main.body[start + 1:], start + 1)
               if isinstance(node, ast.If) and any(isinstance(call, ast.Call)
               and isinstance(call.func, ast.Name) and call.func.id == "shader_census_cmd" for call in ast.walk(node)))
    # Include real launcher/destination selection, list/append, stage-copy and swap; no rewritten installation algorithm.
    return compile(ast.Module(body=main.body[start:end], type_ignores=[]), filename, "exec")


FINISH = finish_statements(SOURCE.read_text(encoding="utf-8"), str(SOURCE))


class FinishOutputTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="longshot-finish-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.addCleanup(self.clear_fixture_readonly_modes)
        self.work, self.out = self.root / "work", self.root / "game"
        self.built = self.work / "sa" / "build"
        self.built.mkdir(parents=True)
        self.out.mkdir()
        self.source, self.destination = self.built / "longshot.exe", self.out / "longshot.exe"
        self.source.write_bytes(b"new synthetic executable")
        self.destination.write_bytes(b"existing synthetic executable")
        (self.work / "sa" / "image").mkdir()
        self.image = self.work / "sa" / "image" / "image.bin"
        self.image.write_bytes(b"new synthetic image")
        (self.out / "image.bin").write_bytes(b"existing synthetic image")

    def clear_fixture_readonly_modes(self):
        # Test-owned temporary files only: production must never unlock destinations or pre-existing .new files.
        for path in self.root.rglob("*"):
            assert path.resolve().is_relative_to(self.root.resolve())
            if path.is_file():
                path.chmod(stat.S_IREAD | stat.S_IWRITE)

    def finish(self, launcher=False):
        if launcher:
            (self.root / "LongshotBuilder-Setup.exe").write_bytes(b"new synthetic launcher")
        with mock.patch.object(make_game, "BESIDE", ["longshot.exe"]):
            # Comprehensions use globals. Keep one namespace, as in production, and discover only authored setup files.
            namespace = dict(make_game.__dict__, built=str(self.built), out=str(self.out), work=str(self.work),
                             ROOT=str(self.root / "tools"), LINUX=False if launcher else make_game.LINUX,
                             DARWIN=False,  # these existing fixtures deliberately use the COFF filename; macOS has a closure manifest
                             a=SimpleNamespace(fsr=False, no_fsr=False, update=None, fetch_fsr_runtime=False))
            exec(FINISH, namespace)

    def assert_installed_unchanged(self):
        self.assertEqual(self.destination.read_bytes(), b"existing synthetic executable")
        self.assertEqual((self.out / "image.bin").read_bytes(), b"existing synthetic image")

    def assert_no_staging_files(self):
        self.assertEqual(sorted(path.name for path in self.out.iterdir()), ["image.bin", "longshot.exe"])

    def stopped_output(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as stopped:
                self.finish()
        self.assertEqual(stopped.exception.code, 1)
        self.assertIn("@@error", output.getvalue())
        return output.getvalue()

    def test_completed_copy_replaces_existing_files_with_source_mode(self):
        self.source.chmod(0o755)
        self.finish()
        self.assertEqual(self.destination.read_bytes(), self.source.read_bytes())
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), stat.S_IMODE(self.source.stat().st_mode))
        self.assertEqual((self.out / "image.bin").read_bytes(), self.image.read_bytes())
        self.assert_no_staging_files()

    def test_all_sources_staged_before_either_installed_file_changes(self):
        copy = make_game.shutil.copy2
        copied = []

        def observe_copy(source, destination):
            self.assert_installed_unchanged()
            result = copy(source, destination)
            self.assert_installed_unchanged()
            copied.append(Path(source))
            return result

        with mock.patch.object(make_game.shutil, "copy2", side_effect=observe_copy):
            self.finish()
        self.assertEqual(copied, [self.source, self.image])
        self.assertEqual(self.destination.read_bytes(), self.source.read_bytes())
        self.assertEqual((self.out / "image.bin").read_bytes(), self.image.read_bytes())
        self.assert_no_staging_files()

    def test_first_install_without_existing_files(self):
        self.destination.unlink()
        (self.out / "image.bin").unlink()
        self.finish()
        self.assertEqual(self.destination.read_bytes(), self.source.read_bytes())
        self.assertEqual((self.out / "image.bin").read_bytes(), self.image.read_bytes())
        self.assert_no_staging_files()

    def test_launcher_stages_all_three_files_before_replacement(self):
        binary = self.out / "bin" / "longshot.exe"
        binary.parent.mkdir()
        binary.write_bytes(b"existing synthetic game in bin")
        copy = make_game.shutil.copy2
        copied = []

        def observe_copy(source, destination):
            self.assert_installed_unchanged()
            self.assertEqual(binary.read_bytes(), b"existing synthetic game in bin")
            result = copy(source, destination)
            self.assert_installed_unchanged()
            self.assertEqual(binary.read_bytes(), b"existing synthetic game in bin")
            copied.append(Path(source))
            return result

        with mock.patch.object(make_game.shutil, "copy2", side_effect=observe_copy):
            self.finish(launcher=True)
        self.assertEqual(copied, [self.source, self.image, self.root / "LongshotBuilder-Setup.exe"])
        self.assertEqual(binary.read_bytes(), self.source.read_bytes())
        self.assertEqual(self.destination.read_bytes(), b"new synthetic launcher")
        self.assertEqual((self.out / "image.bin").read_bytes(), self.image.read_bytes())
        self.assertEqual(list(self.out.rglob("*.new")), [])

    def test_partial_launcher_copy_preserves_game_image_and_launcher(self):
        binary = self.out / "bin" / "longshot.exe"
        binary.parent.mkdir()
        binary.write_bytes(b"existing synthetic game in bin")
        copy = make_game.shutil.copy2

        def partial_launcher(source, destination):
            if Path(source) == self.root / "LongshotBuilder-Setup.exe":
                Path(destination).write_bytes(b"partial launcher")
                raise OSError(errno.ENOSPC, "synthetic disk-full error")
            return copy(source, destination)

        with mock.patch.object(make_game.shutil, "copy2", side_effect=partial_launcher):
            with self.assertRaises(OSError):
                self.finish(launcher=True)
        self.assert_installed_unchanged()
        self.assertEqual(binary.read_bytes(), b"existing synthetic game in bin")
        self.assertEqual(binary.with_name("longshot.exe.new").read_bytes(), self.source.read_bytes())
        self.assertEqual((self.out / "image.bin.new").read_bytes(), self.image.read_bytes())
        self.assertEqual((self.out / "longshot.exe.new").read_bytes(), b"partial launcher")

    def test_partial_executable_copy_preserves_both_installed_files(self):
        def partial_copy(source, destination):
            Path(destination).write_bytes(b"incomplete replacement")
            raise OSError(errno.ENOSPC, "synthetic disk-full error")

        with mock.patch.object(make_game.shutil, "copy2", side_effect=partial_copy):
            with self.assertRaises(OSError):
                self.finish()
        self.assert_installed_unchanged()
        self.assertEqual((self.out / "longshot.exe.new").read_bytes(), b"incomplete replacement")
        self.assertFalse((self.out / "image.bin.new").exists())

    def test_partial_image_copy_preserves_both_installed_files(self):
        copy = make_game.shutil.copy2

        def partial_image(source, destination):
            if Path(source) == self.image:
                self.assert_installed_unchanged()
                Path(destination).write_bytes(b"incomplete image")
                raise OSError(errno.ENOSPC, "synthetic disk-full error")
            return copy(source, destination)

        with mock.patch.object(make_game.shutil, "copy2", side_effect=partial_image):
            with self.assertRaises(OSError):
                self.finish()
        self.assert_installed_unchanged()
        self.assertEqual((self.out / "longshot.exe.new").read_bytes(), self.source.read_bytes())
        self.assertEqual((self.out / "image.bin.new").read_bytes(), b"incomplete image")

    def test_readonly_source_mode_is_preserved_on_success(self):
        self.source.chmod(stat.S_IREAD)
        self.finish()
        self.assertEqual(self.destination.read_bytes(), self.source.read_bytes())
        self.assertFalse(self.destination.stat().st_mode & stat.S_IWRITE)
        self.assertEqual((self.out / "image.bin").read_bytes(), self.image.read_bytes())
        self.assert_no_staging_files()

    def test_denied_swap_keeps_complete_readonly_staging_file(self):
        self.source.chmod(stat.S_IREAD)
        with mock.patch.object(make_game.os, "replace", side_effect=PermissionError("synthetic denied replacement")):
            message = self.stopped_output()
        self.assertIn("writable", message)
        self.assertNotIn("the game is running", message)
        self.assert_installed_unchanged()
        self.assertEqual((self.out / "longshot.exe.new").read_bytes(), self.source.read_bytes())
        self.assertFalse((self.out / "longshot.exe.new").stat().st_mode & stat.S_IWRITE)
        self.assertEqual((self.out / "image.bin.new").read_bytes(), self.image.read_bytes())

    def test_stage_copy_permission_failure_has_actionable_paths(self):
        def denied_copy(source, destination):
            Path(destination).write_bytes(b"partial staged copy")
            raise PermissionError("synthetic denied staging write")

        with mock.patch.object(make_game.shutil, "copy2", side_effect=denied_copy):
            message = self.stopped_output()
        self.assertIn("stage", message)
        self.assertIn(str(self.out / "longshot.exe.new"), message)
        self.assertIn(str(self.source), message)
        self.assertIn("readable", message)
        self.assertIn("writable", message)
        self.assertIn("No installed files were replaced", message)
        self.assert_installed_unchanged()
        self.assertEqual((self.out / "longshot.exe.new").read_bytes(), b"partial staged copy")

    @unittest.skipUnless(os.name == "nt", "Windows file-sharing contract")
    def test_locked_destination_reports_action_and_retains_staged_build(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
                                      ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.restype = ctypes.c_int
        # Test-owned file: share reads/writes, not deletion/replacement, like a running executable.
        handle = kernel.CreateFileW(str(self.destination), 0x80000000, 0x1 | 0x2, None, 3, 0x80, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value, ctypes.get_last_error())
        try:
            message = self.stopped_output()
            self.assertIn("Close any running copy", message)
            self.assertIn("writable", message)
            self.assertIn(str(self.destination), message)
            self.assertIn(str(self.source), message)
            self.assert_installed_unchanged()
            self.assertEqual((self.out / "longshot.exe.new").read_bytes(), self.source.read_bytes())
            self.assertEqual((self.out / "image.bin.new").read_bytes(), self.image.read_bytes())
        finally:
            self.assertTrue(kernel.CloseHandle(handle))
        self.finish()
        self.assertEqual(self.destination.read_bytes(), self.source.read_bytes())
        self.assertEqual((self.out / "image.bin").read_bytes(), self.image.read_bytes())
        self.assert_no_staging_files()

    @unittest.skipUnless(os.name == "nt", "Windows read-only destination contract")
    def test_readonly_destination_is_not_removed_or_unlocked(self):
        self.destination.chmod(stat.S_IREAD)
        message = self.stopped_output()
        self.assertIn("writable", message)
        self.assertNotIn("the game is running", message)
        self.assert_installed_unchanged()
        self.assertFalse(self.destination.stat().st_mode & stat.S_IWRITE)

    @unittest.skipUnless(os.name == "nt", "Windows read-only staging-file contract")
    def test_readonly_existing_stage_is_not_removed_or_unlocked(self):
        staged = self.out / "longshot.exe.new"
        staged.write_bytes(b"pre-existing staged content")
        staged.chmod(stat.S_IREAD)
        with mock.patch.object(make_game.os, "chmod", wraps=make_game.os.chmod) as chmod:
            message = self.stopped_output()
        chmod.assert_not_called()
        self.assertIn(str(staged), message)
        self.assertIn("writable", message)
        self.assert_installed_unchanged()
        self.assertEqual(staged.read_bytes(), b"pre-existing staged content")
        self.assertFalse(staged.stat().st_mode & stat.S_IWRITE)


class MacFinishOutputTests(unittest.TestCase):
    """The same production finish statements must deploy every manifest-owned dylib."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="longshot-macos-finish-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work, self.out = self.root / "work", self.root / "game"
        self.built = self.work / "sa/build"
        self.built.mkdir(parents=True)
        self.out.mkdir()
        # Authored bytes and hashes test copying, not Mach-O execution (test_macos_package.py does that).
        self.package = importlib.import_module("darwin_package")
        self.names = ["longshot", "longshot_vk.dylib", "libSDL2-2.0.0.dylib", "libMoltenVK.dylib", "libdecoder-helper.7.dylib"]
        files = {}
        for name in self.names:
            src = self.built / name
            src.write_bytes(("new authored " + name).encode())
            src.chmod(0o755)
            (self.out / name).write_bytes(("old authored " + name).encode())
            files[name] = {"sha256": self.package.digest(src)}
        manifest = {"schema": 1, "arch": "arm64", "minimum_os": "14.4", "files": files}
        (self.built / self.package.MANIFEST).write_text(json.dumps(manifest))
        (self.work / "sa/image").mkdir()
        (self.work / "sa/image/image.bin").write_bytes(b"authored image, no game content")
        (self.out / "image.bin").write_bytes(b"old authored image")

    def finish(self):
        namespace = dict(make_game.__dict__, built=str(self.built), out=str(self.out), work=str(self.work),
                         ROOT=str(self.root / "tools"), LINUX=False, DARWIN=True, darwin_package=self.package,
                         a=SimpleNamespace(fsr=False, no_fsr=False, update=None, fetch_fsr_runtime=False))
        exec(FINISH, namespace)

    def test_manifest_closure_not_only_fixed_sdl_and_ffmpeg_names_is_deployed(self):
        self.finish()
        for name in self.names + [self.package.MANIFEST]:
            self.assertEqual((self.out / name).read_bytes(), (self.built / name).read_bytes())
        self.assertTrue((self.out / "longshot").stat().st_mode & stat.S_IXUSR)
        self.assertFalse((self.out / "bin").exists())
        self.assertEqual(list(self.out.glob("*.new")), [])

    def test_changed_or_missing_manifest_file_refuses_before_any_swap(self):
        for missing in (False, True):
            path = self.built / "libdecoder-helper.7.dylib"
            if missing:
                path.unlink()
            else:
                path.write_bytes(b"changed after packaging")
            before = {p.name: p.read_bytes() for p in self.out.iterdir()}
            with self.assertRaises((OSError, self.package.DependencyError)):
                self.finish()
            self.assertEqual({p.name: p.read_bytes() for p in self.out.iterdir()}, before)

    def test_partial_dependency_copy_leaves_every_installed_file_unchanged(self):
        before = {p.name: p.read_bytes() for p in self.out.iterdir()}
        copy = make_game.shutil.copy2
        def partial(src, dst, **kwargs):
            if Path(src).name == "libdecoder-helper.7.dylib":
                Path(dst).write_bytes(b"partial authored dependency")
                raise OSError(errno.ENOSPC, "authored disk-full error")
            return copy(src, dst, **kwargs)
        with mock.patch.object(make_game.shutil, "copy2", side_effect=partial):
            with self.assertRaises(OSError):
                self.finish()
        self.assertEqual({n: (self.out / n).read_bytes() for n in before}, before)

    @unittest.skipIf(os.name == "nt", "macOS dependency alias uses a POSIX symlink")
    def test_runtime_alias_stays_internal_after_maker_copy(self):
        target = self.built / "libSDL3.0.dylib"
        target.write_bytes(b"authored SDL3 provider")
        (self.built / "libSDL3.dylib").symlink_to(target.name)
        path = self.built / self.package.MANIFEST
        manifest = json.loads(path.read_text())
        manifest["files"][target.name] = {"sha256": self.package.digest(target), "kind": "dylib"}
        manifest["files"]["libSDL3.dylib"] = {"sha256": self.package.digest(target), "kind": "alias", "target": target.name}
        path.write_text(json.dumps(manifest))
        self.finish()
        self.assertTrue((self.out / "libSDL3.dylib").is_symlink())
        self.assertEqual(os.readlink(self.out / "libSDL3.dylib"), target.name)

    def test_unmanaged_loader_cannot_override_newly_packaged_moltenvk(self):
        extra = self.out / "libvulkan.1.dylib"
        extra.write_bytes(b"authored unapproved leftover loader")
        before = {p.name: p.read_bytes() for p in self.out.iterdir()}
        with self.assertRaisesRegex(self.package.DependencyError, "shadow this package"):
            self.finish()
        self.assertEqual({p.name: p.read_bytes() for p in self.out.iterdir()}, before)


if __name__ == "__main__":
    unittest.main()
