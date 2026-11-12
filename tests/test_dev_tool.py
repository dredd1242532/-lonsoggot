"""nx2/profiles.py and nx2/dev.py (the dev-loop proposal's A9): the build's environment made in one place, and the tool that says
whether the next build translates the game again and why, shows the last builds' times, and runs the last build again.

Nothing here runs a compiler or needs the game: the work folders are tiny authored files.

python -I -B tests/test_dev_tool.py
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "nx2"), str(ROOT / "third_party" / "a64-kit" / "tools")]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


profiles = load("profiles", ROOT / "nx2" / "profiles.py")
dev = load("dev_under_test", ROOT / "nx2" / "dev.py")
make_game = load("make_game_dev", ROOT / "nx2" / "make_game.py")
CLEAN = {k: v for k, v in os.environ.items() if not k.startswith(("A64_", "OOT_", "RX_", "SUYU_", "LONGSHOT_PROFILE"))}


class ProfileTests(unittest.TestCase):
    def test_release_is_what_make_game_built(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "hot_funcs.txt").write_text("f_80000000\n")
            (Path(d) / "longshot_pieces.prof").write_bytes(b"profile")
            env = profiles.build_env(d, "E", "R", "O")
            self.assertEqual(env, {"OOT_WORK": d, "OOT_EXEFS": "E", "OOT_ROMFS": "R", "OOT_DUMP": "O", "RX_RELEASE": "1",
                                   "A64_HOT_CHUNK": "1", "A64_HOT_FUNCS": os.path.join(d, "hot_funcs.txt"),
                                   "RX_PGO": os.path.join(d, "longshot_pieces.prof")})
            self.assertNotIn("RX_PGO", profiles.build_env(d, "E", "R", "O", profile_ok=lambda p: False))
            self.assertNotIn("RX_RELEASE", profiles.build_env(d, "E", "R", "O", name="dev"))
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(set(profiles.build_env(d, "E", "R", "O")),
                             {"OOT_WORK", "OOT_EXEFS", "OOT_ROMFS", "OOT_DUMP", "RX_RELEASE", "A64_HOT_CHUNK"})

    def test_the_profile_is_release_unless_it_says_dev(self):
        self.assertEqual(profiles.profile({}), "release")
        self.assertEqual(profiles.profile({"LONGSHOT_PROFILE": "dev"}), "dev")
        with self.assertRaises(SystemExit):
            profiles.profile({"LONGSHOT_PROFILE": "fast"})

    def test_make_game_takes_its_environment_from_profiles_and_records_the_build(self):
        src = (ROOT / "nx2" / "make_game.py").read_text(encoding="utf-8")
        self.assertIn("env = profiles.build_env(work, exefs, romfs, out, profile_readable, profiles.profile())", src)
        self.assertIn("record_build(work, out, env)", src)
        self.assertNotIn('"RX_RELEASE": "1", "A64_HOT_CHUNK": "1"}', src)   # (the old dictionary, made in place)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(sys, "argv", ["nx2/make_game.py", "--nup", "x.nup"]), \
                mock.patch.dict(os.environ, dict(CLEAN, LONGSHOT_MINGW="C:/mingw"), clear=True):
            make_game.record_build(d, "O", {"OOT_WORK": d})
            data = json.loads((Path(d) / make_game.LAST_BUILD).read_text(encoding="utf-8"))
        self.assertEqual((data["argv"], data["toolchain"], data["profile"]), (["--nup", "x.nup"], {"LONGSHOT_MINGW": "C:/mingw"}, "release"))


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="longshot-dev-")
        self.addCleanup(self.temporary.cleanup)
        d = Path(self.temporary.name)
        self.work, self.exefs = d / "work", d / "work" / "exefs"
        for p in (self.exefs, self.work / "image", self.work / "sa" / "gen", self.work / "sa" / "image"):
            p.mkdir(parents=True)
        (self.exefs / "main").write_bytes(b"NSO0 authored")
        (self.work / "hot_funcs.txt").write_text("f_80000000\n")
        (self.work / "sa" / "gen" / "a64_table.c").write_text("")
        (self.work / "sa" / "image" / "image.bin").write_bytes(b"")
        self.env = profiles.build_env(str(self.work), str(self.exefs), "", "")
        (self.work / "last_build.json").write_text(json.dumps({"env": self.env, "out": ""}))

    def lifted(self):
        with mock.patch.dict(os.environ, CLEAN, clear=True):
            make_game.write_lift_key(str(self.work), make_game.lift_key(str(self.exefs), str(self.work), self.env))

    def status(self):
        with mock.patch.dict(os.environ, CLEAN, clear=True):
            return dev.lift_status(str(self.work), dev.last_build(str(self.work)), make_game)

    def test_the_key_is_written_with_what_it_was_made_of(self):
        self.lifted()
        stored = json.loads((self.work / "sa" / make_game.LIFT_PARTS).read_text(encoding="utf-8"))
        self.assertIn("game/main", stored["parts"]["files"])
        self.assertIn("work/hot_funcs.txt", stored["parts"]["files"])
        self.assertIn("nx2/lift.py", stored["parts"]["files"])
        self.assertEqual(stored["parts"]["env"], {"A64_HOT_CHUNK": "1"})

    def test_an_unchanged_folder_skips_the_lift(self):
        self.lifted()
        self.assertIn("skips the translation", self.status()[0])

    def test_a_change_is_named(self):
        self.lifted()
        (self.work / "hot_funcs.txt").write_text("f_80000004\n")
        with mock.patch.dict(CLEAN, {"A64_NO_VFPX": "1"}):
            lines = self.status()
        self.assertIn("translates the game again", lines[0])
        self.assertIn("  changed: work/hot_funcs.txt", lines)
        self.assertIn("  environment A64_NO_VFPX: (unset) -> 1", lines)

    def test_no_record_says_so(self):
        self.assertIn("not on record", self.status()[0])

    def test_no_game_code_says_so(self):
        (self.work / "exefs" / "main").unlink()
        (self.work / "exefs").rmdir()
        self.assertIn("cannot tell", self.status()[0])

    def test_differences_name_each_part(self):
        before = {"python": "3.12.1 (x)", "hle": ["imp_a"], "files": {"nx2/a.py": "1", "nx2/b.py": "2"}, "env": {}}
        now = {"python": "3.14.7 (x)", "hle": ["imp_a", "imp_b"], "files": {"nx2/a.py": "9", "nx2/c.py": "3"}, "env": {"A64_X": "1"}}
        self.assertEqual(dev.differences(before, now), ["Python: 3.12.1 -> 3.14.7", "host/ HLE functions: +imp_b", "changed: nx2/a.py",
                                                        "file gone: nx2/b.py", "new file: nx2/c.py",
                                                        "environment A64_X: (unset) -> 1"])


class TimesAndBuildTests(unittest.TestCase):
    def test_the_last_builds_side_by_side(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "build_times.json").write_text(json.dumps({"builds": [
                {"when": "2026-11-12 03:00:00", "kind": "build", "total_seconds": 32.4, "compile_seconds": 28.3,
                 "phases": {"lift": 0.3, "compile": 28.3, "compile: planning": 7.5, "compile: compiling": 18.0}},
                {"when": "2026-11-12 03:10:00", "kind": "build", "total_seconds": 26.0, "compile_seconds": 23.0,
                 "phases": {"lift": 0.2, "compile": 23.0, "compile: planning": 2.0, "compile: compiling": 17.0}}]}))
            rows = dev.times_table(d, 3)
        self.assertTrue(rows[1].startswith("total") and "32 s" in rows[1] and "26 s" in rows[1])
        planning = next(r for r in rows if r.strip().startswith("planning"))
        self.assertIn("7.5 s", planning)
        self.assertIn("2.0 s", planning)
        self.assertLess(next(i for i, r in enumerate(rows) if r.startswith("compile ")), rows.index(planning))

    def test_build_runs_the_last_build_again(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "last_build.json").write_text(json.dumps({"argv": ["--nup", "x.nup", "--out", "g"], "python": "py.exe",
                                                                  "root": "R", "profile": "release",
                                                                  "toolchain": {"LONGSHOT_MINGW": "C:/mingw"}}))
            with mock.patch.object(dev.subprocess, "call", return_value=0) as call, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(dev.build(d, dev=True), 0)
            cmd, = call.call_args.args
            self.assertEqual(cmd, ["py.exe", "-I", "-u", os.path.join("nx2", "make_game.py"), "--nup", "x.nup", "--out", "g"])
            self.assertEqual(call.call_args.kwargs["cwd"], "R")
            self.assertEqual((call.call_args.kwargs["env"]["LONGSHOT_PROFILE"], call.call_args.kwargs["env"]["LONGSHOT_MINGW"]),
                             ("dev", "C:/mingw"))
            with self.assertRaises(SystemExit):
                dev.build(str(Path(d) / "none"), dev=False)


if __name__ == "__main__":
    raise SystemExit(unittest.main(verbosity=1))
