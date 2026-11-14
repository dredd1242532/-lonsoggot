"""tests/run_all.py's reader of what a test declares in its own file (RUN_ALL_ARGS, RUN_ALL_MODULES, RUN_ALL_SKIP): the
valid shapes, the malformed ones that stop the run with the file and line, placeholders find_tools does not know, OSes
it does not know, and the retired central tables, which a branch from before the move must not fill again.

python -I -B tests/test_run_all_declarations.py
"""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("run_all", ROOT / "tests" / "run_all.py")
run_all = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_all)

TOOLS = {"cxx": None, "vulkan_headers": None, "spirv_headers": None, "spirv_val": None, "glslang": None, "dotnet": None,
         "dav1d": None}


class Reader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def read(self, source):
        path = os.path.join(self.tmp.name, "test_x.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(source))
        return run_all.read_declarations(path, "tests/test_x.py", TOOLS)

    def bad(self, source, *words):
        with self.assertRaises(run_all.DeclarationError) as e:
            self.read(source)
        self.assertIn("tests/test_x.py:", str(e.exception))
        for w in words:
            self.assertIn(w, str(e.exception))
        return str(e.exception)

    def test_nothing_declared(self):
        self.assertEqual(self.read('"""RUN_ALL_ARGS in the docstring is not a declaration"""\nimport os\n'),
                         {"args": [], "modules": [], "skip": {}, "unparsed": None})

    def test_all_three(self):
        d = self.read('''
            """doc"""
            import sys

            RUN_ALL_ARGS = ["--cxx", "{cxx}", "--headers", "{vulkan_headers}", "--cc", "gcc"]
            RUN_ALL_MODULES = ["zstandard"]
            # (a GPU)
            RUN_ALL_SKIP = {
                "linux": "needs a Vulkan device",
                "windows": "needs a Vulkan device",
            }


            def main():
                RUN_ALL_ARGS = len   # (not top level: not a declaration)
            ''')
        self.assertEqual(d["args"], ["--cxx", "{cxx}", "--headers", "{vulkan_headers}", "--cc", "gcc"])
        self.assertEqual(d["modules"], ["zstandard"])
        self.assertEqual(d["skip"], {"linux": "needs a Vulkan device", "windows": "needs a Vulkan device"})

    def test_annotated_and_concatenated(self):
        d = self.read('RUN_ALL_SKIP: dict = {"windows": "a long reason"\n                               " in two parts"}\n')
        self.assertEqual(d["skip"], {"windows": "a long reason in two parts"})

    def test_syntax_error_declares_nothing(self):
        # (the runner does not stop for it: the check fails, with why, on every OS)
        d = self.read('RUN_ALL_ARGS = ["--cxx"\n')
        self.assertEqual((d["args"], d["modules"], d["skip"]), ([], [], {}))
        self.assertIn("tests/test_x.py: does not parse", d["unparsed"])

    def test_not_a_literal(self):
        self.bad('CXX = "{cxx}"\nRUN_ALL_ARGS = ["--cxx", CXX]\n', "RUN_ALL_ARGS", "plain literal", ":2:")
        self.bad('RUN_ALL_MODULES = list(("zstandard",))\n', "plain literal")
        self.bad('RUN_ALL_ARGS = ["--cxx"] + ["{cxx}"]\n', "plain literal")

    def test_wrong_shape(self):
        self.bad('RUN_ALL_ARGS = "--cxx {cxx}"\n', "list of strings")
        self.bad('RUN_ALL_ARGS = ("--cxx", "{cxx}")\n', "list of strings")
        self.bad('RUN_ALL_MODULES = ["zstandard", 1]\n', "list of strings")
        self.bad('RUN_ALL_SKIP = ["windows"]\n', "dict of OS -> reason")
        self.bad('RUN_ALL_SKIP = ({"windows"}, "why")\n', "dict of OS -> reason")
        self.bad('RUN_ALL_SKIP = {"windows": ""}\n', "dict of OS -> reason")

    def test_assigned_other_ways(self):
        self.bad('RUN_ALL_ARGS = ["--cc", "gcc"]\nRUN_ALL_ARGS = ["--cxx", "g++"]\n', "assigned twice", ":2:")
        self.bad('RUN_ALL_ARGS = []\nRUN_ALL_ARGS += ["--cc", "gcc"]\n', "NAME = literal")
        self.bad('RUN_ALL_ARGS, X = ["--cc"], 1\n', "NAME = literal")
        self.bad('RUN_ALL_ARGS = X = ["--cc", "gcc"]\n', "NAME = literal")
        self.bad('RUN_ALL_SKIP = {}\nRUN_ALL_SKIP["windows"] = "why"\n', "NAME = literal")

    def test_set_or_changed_elsewhere(self):
        # (the runner reads only a top-level NAME = literal: anything else would be ignored without a word)
        self.bad('import os\nif os.name == "nt":\n    RUN_ALL_SKIP = {"windows": "why"}\n', "top level", ":3:")
        self.bad('try:\n    import zstandard\nexcept ImportError:\n    RUN_ALL_MODULES = ["zstandard"]\n', "top level")
        self.bad('RUN_ALL_ARGS = ["--cxx"]\nRUN_ALL_ARGS.append("{cxx}")\n', ".append", ":2:")
        self.bad('RUN_ALL_SKIP = {}\nRUN_ALL_SKIP.update(windows="why")\n', ".update")
        self.bad('RUN_ALL_ARGS = ["--cc", "gcc"]\ndel RUN_ALL_ARGS\n', "top level")
        self.bad('import os\nif os.name == "nt":\n    RUN_ALL_SKIP["windows"] = "why"\nRUN_ALL_SKIP = {}\n', "NAME = literal")
        # (reading one is fine, and so is a function's own name)
        d = self.read('RUN_ALL_ARGS = ["--cc", "gcc"]\nprint(RUN_ALL_ARGS)\n\n\ndef f():\n    RUN_ALL_ARGS = []\n'
                      '    RUN_ALL_ARGS.append(1)\n')
        self.assertEqual(d["args"], ["--cc", "gcc"])

    def test_unknown_name(self):
        self.bad('RUN_ALL_ARG = ["--cxx", "{cxx}"]\n', "RUN_ALL_ARG is not", "RUN_ALL_ARGS")

    def test_unknown_placeholder(self):
        self.bad('RUN_ALL_ARGS = ["--cxx", "{cxx}", "--headers", "{vulkan_header}"]\n', "{vulkan_header}",
                 "unknown placeholder", "vulkan_headers")

    def test_placeholders_are_find_tools(self):
        # (the reader is checked against what find_tools looks for: TOOLS above must be that set)
        self.assertEqual(set(run_all.find_tools()), set(TOOLS))

    def test_unknown_os(self):
        self.bad('RUN_ALL_SKIP = {"win32": "why"}\n', "unknown OS 'win32'", "linux, windows")
        self.bad('RUN_ALL_SKIP = {"windows": "why", "freebsd": "why"}\n', "unknown OS 'freebsd'")

    def test_macos_is_accepted_alone_and_with_the_existing_systems(self):
        self.assertEqual(self.read('RUN_ALL_SKIP = {"macos": "needs a device"}\n')["skip"],
                         {"macos": "needs a device"})
        reasons = {"linux": "Linux reason", "windows": "Windows reason", "macos": "Mac reason"}
        self.assertEqual(self.read("RUN_ALL_SKIP = %r\n" % reasons)["skip"], reasons)

    def test_macos_still_requires_a_nonempty_reason(self):
        for reason in ("", "  ", None, 1):
            self.bad("RUN_ALL_SKIP = %r\n" % {"macos": reason}, "dict of OS -> reason")


def platform_runner(name, platform):
    """Load the runner itself with the platform values, not a copy of its detection expression."""
    spec = importlib.util.spec_from_file_location("run_all_platform", ROOT / "tests" / "run_all.py")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.object(os, "name", name), mock.patch.object(sys, "platform", platform):
        spec.loader.exec_module(module)
    return module


class Platforms(unittest.TestCase):
    def test_linux_and_windows_detection_is_unchanged(self):
        for name, platform, expected, windows in (("posix", "linux", "linux", False),
                                                  ("nt", "win32", "windows", True)):
            with self.subTest(platform=platform):
                runner = platform_runner(name, platform)
                self.assertEqual((runner.OS, runner.WINDOWS), (expected, windows))

    def test_darwin_is_macos_not_linux(self):
        runner = platform_runner("posix", "darwin")
        self.assertEqual((runner.OS, runner.WINDOWS), ("macos", False))

    def test_find_tools_keeps_each_platforms_choices(self):
        available = {name: "/tools/" + name for name in ("custom++", "clang++", "g++", "c++", "spirv-val", "glslang", "dotnet")}
        for name, platform in (("posix", "linux"), ("nt", "win32"), ("posix", "darwin")):
            runner = platform_runner(name, platform)
            for compiler in ("custom++", "not-installed"):
                with self.subTest(platform=platform, compiler=compiler), \
                        mock.patch.dict(os.environ, {"CXX": compiler}, clear=True), \
                        mock.patch.object(runner.shutil, "which", side_effect=available.get), \
                        mock.patch.object(runner, "header_dir", side_effect=lambda env, package, probe: "/headers/" + package), \
                        mock.patch.object(runner, "dav1d_prefix", return_value="/dav1d") as dav1d:
                    tools = runner.find_tools()
                    self.assertEqual(tools, {
                        "cxx": available["custom++" if compiler == "custom++" else "clang++"],
                        "spirv_val": available["spirv-val"], "glslang": available["glslang"], "dotnet": available["dotnet"],
                        "spirv_headers": "/headers/spirv-headers", "vulkan_headers": "/headers/vulkanheaders",
                        "dav1d": None if name == "nt" else "/dav1d",
                    })
                    self.assertEqual(dav1d.call_count, 0 if name == "nt" else 1)

    def test_darwin_dav1d_uses_pkg_config_only_after_existing_roots(self):
        prefix = os.path.join("sdk", "dav1d")
        header = os.path.join(prefix, "include", "dav1d", "dav1d.h")
        reply = subprocess.CompletedProcess([], 0, prefix + "\n")
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(run_all.os.path, "isdir", return_value=False), \
                mock.patch.object(run_all.os.path, "exists", side_effect=lambda p: p == header), \
                mock.patch.object(run_all, "which", return_value="/tools/pkg-config"), \
                mock.patch.object(run_all.subprocess, "run", return_value=reply) as command:
            self.assertEqual(run_all.dav1d_prefix(), prefix)
            command.assert_called_once_with(["/tools/pkg-config", "--variable=prefix", "dav1d"],
                                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                            text=True, timeout=5)
            command.reset_mock()
            with mock.patch.dict(os.environ, {"LONGSHOT_DAV1D": prefix}):
                self.assertEqual(run_all.dav1d_prefix(), prefix)
            command.assert_not_called()

    def test_missing_dav1d_stays_missing_without_breaking_discovery(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(run_all.os.path, "isdir", return_value=False), \
                mock.patch.object(run_all.os.path, "exists", return_value=False), \
                mock.patch.object(run_all, "which", return_value="/tools/pkg-config"), \
                mock.patch.object(run_all.subprocess, "run") as command:
            for platform in ("linux", "win32"):
                with self.subTest(platform=platform), mock.patch.object(sys, "platform", platform):
                    self.assertIsNone(run_all.dav1d_prefix())
                    command.assert_not_called()
            with mock.patch.object(sys, "platform", "darwin"):
                for failure in (OSError("unavailable"), subprocess.TimeoutExpired("pkg-config", 5)):
                    command.side_effect = failure
                    self.assertIsNone(run_all.dav1d_prefix())
                command.side_effect = None
                for code, prefix in ((1, ""), (0, ""), (0, "/missing")):
                    command.return_value = subprocess.CompletedProcess([], code, prefix)
                    self.assertIsNone(run_all.dav1d_prefix())
                command.reset_mock()
                with mock.patch.object(run_all, "which", return_value=None):
                    self.assertIsNone(run_all.dav1d_prefix())
                command.assert_not_called()


class Runner(unittest.TestCase):
    def run_main(self, root, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(run_all, "ROOT", root), mock.patch.object(sys, "argv", ["run_all.py", *argv]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = run_all.main()
        return rc, out.getvalue(), err.getvalue()

    def test_bad_declaration_stops_the_run(self):
        with tempfile.TemporaryDirectory() as root:
            for d in ("tests", "nx2"):
                os.mkdir(os.path.join(root, d))
            with open(os.path.join(root, "nx2", "test_bad.py"), "w", encoding="utf-8") as f:
                f.write('import os\n\nRUN_ALL_SKIP = {"window": "why"}\n')
            rc, out, err = self.run_main(root)
        self.assertEqual(rc, 2)
        self.assertIn("nx2/test_bad.py:3:", err)
        self.assertEqual(out, "")

    def test_bad_declaration_elsewhere_does_not_stop_a_named_run(self):
        with tempfile.TemporaryDirectory() as root:
            for d in ("tests", "nx2"):
                os.mkdir(os.path.join(root, d))
            with open(os.path.join(root, "nx2", "test_bad.py"), "w", encoding="utf-8") as f:
                f.write('RUN_ALL_SKIP = {"window": "why"}\n')
            with open(os.path.join(root, "tests", "test_good.py"), "w", encoding="utf-8") as f:
                f.write('import sys\n\nRUN_ALL_ARGS = ["--flag"]\nsys.exit(0 if sys.argv[1:] == ["--flag"] else 1)\n')
            with mock.patch.object(run_all, "compile_all", lambda: run_all.Result(0, b"")):
                rc, out, err = self.run_main(root, "test_good")
                self.assertEqual(rc, 0, out + err)
                self.assertIn("tests/test_good.py", out)
                rc, out, err = self.run_main(root, "test_bad")
                self.assertEqual(rc, 2)
                self.assertIn("nx2/test_bad.py:1:", err)

    def test_unparsed_test_fails_without_running(self):
        with tempfile.TemporaryDirectory() as root:
            for d in ("tests", "nx2"):
                os.mkdir(os.path.join(root, d))
            with open(os.path.join(root, "tests", "test_broken.py"), "w", encoding="utf-8") as f:
                f.write('RUN_ALL_SKIP = {"windows": "why"\n')
            for os_name in ("linux", "windows", "macos"):
                with mock.patch.object(run_all, "OS", os_name), \
                        mock.patch.object(run_all, "compile_all", lambda: run_all.Result(0, b"")):
                    rc, out, err = self.run_main(root, "test_broken")
                self.assertEqual(rc, 1, os_name)
                self.assertIn("does not parse", out)

    def test_stale_central_entry_stops_the_run(self):
        # (a branch from before the move that still adds its line to ARGS, MODULES or SKIP)
        with mock.patch.dict(run_all.ARGS, {"tests/test_new.py": ["--cxx", "{cxx}"]}), \
                mock.patch.dict(run_all.SKIP, {"tests/test_new.py": ({"windows"}, "why")}):
            rc, out, err = self.run_main(str(ROOT))
        self.assertEqual(rc, 2)
        self.assertIn("retired", err)
        self.assertIn("tests/test_new.py: declare it in the test: RUN_ALL_ARGS = ['--cxx', '{cxx}']", err)
        self.assertIn("tests/test_new.py: declare it in the test: RUN_ALL_SKIP = {'windows': 'why'}", err)
        self.assertEqual(out, "")

    def test_platform_skip_overrides_and_step_summary(self):
        for os_name in ("linux", "windows", "macos"):
            with self.subTest(os=os_name), tempfile.TemporaryDirectory() as root:
                for d in ("tests", "nx2"):
                    os.mkdir(os.path.join(root, d))
                reason = "only the other platform has this subject"
                Path(root, "tests", "test_platform.py").write_text(
                    "RUN_ALL_SKIP = %r\nraise SystemExit(19)\n" % {os_name: reason}, encoding="utf-8")
                summary = Path(root, "summary.txt")
                for flags, code, passed, failed, skipped in (((), 0, 1, 0, 1),
                                                            (("--no-skip",), 1, 1, 1, 0),
                                                            (("test_platform",), 1, 0, 1, 0)):
                    summary.write_text("", encoding="utf-8")
                    with self.subTest(flags=flags), mock.patch.object(run_all, "OS", os_name), \
                            mock.patch.object(run_all, "find_tools", return_value=TOOLS), \
                            mock.patch.object(run_all, "cpp_checks", return_value=([], [])), \
                            mock.patch.object(run_all, "load_times", return_value={}), \
                            mock.patch.object(run_all, "compile_all", lambda: run_all.Result(0, b"")), \
                            mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary), "GITHUB_ACTIONS": "false"}):
                        rc, out, err = self.run_main(root, "--jobs", "1", *flags)
                        self.assertEqual(rc, code, out + err)
                        self.assertEqual(err, "")
                        head = out.strip().splitlines()[-1]
                        self.assertRegex(head, r"^%d passed, %d failed, %d skipped in \d+ s \(%s, 1 at once\)$"
                                         % (passed, failed, skipped, os_name))
                        step = summary.read_text(encoding="utf-8")
                        self.assertIn("### Tests (%s): %s" % (os_name, head), step)
                        self.assertIn("`tests/test_platform.py`", step)
                        if skipped:
                            self.assertIn("| skip |  | `tests/test_platform.py` | " + reason + " |", step)
                        else:
                            self.assertIn("| **FAIL** |", step)
                            self.assertNotIn(reason, step)

    def test_no_skip_does_not_supply_missing_tools(self):
        for os_name in ("linux", "windows", "macos"):
            with self.subTest(os=os_name), tempfile.TemporaryDirectory() as root:
                for d in ("tests", "nx2"):
                    os.mkdir(os.path.join(root, d))
                Path(root, "tests", "test_tool.py").write_text(
                    'RUN_ALL_ARGS = ["--tool", "{glslang}"]\nRUN_ALL_SKIP = %r\n'
                    'raise RuntimeError("the missing-tool child must not run")\n' % {os_name: "platform reason"},
                    encoding="utf-8")
                for flags, code, result in ((("--strict",), 0, "platform reason"),
                                             (("--no-skip",), 0, "not found here: glslang"),
                                             (("--no-skip", "--strict"), 1, "not found: glslang (--strict)")):
                    with self.subTest(flags=flags), mock.patch.object(run_all, "OS", os_name), \
                            mock.patch.object(run_all, "find_tools", return_value=TOOLS), \
                            mock.patch.object(run_all, "cpp_checks", return_value=([], [])), \
                            mock.patch.object(run_all, "compile_all", lambda: run_all.Result(0, b"")), \
                            mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}):
                        rc, out, err = self.run_main(root, "--jobs", "1", *flags)
                        self.assertEqual(rc, code, out + err)
                        self.assertIn(result, out)
                        self.assertNotIn("the missing-tool child must not run", out)

    def test_macos_repository_excludes_only_absent_subjects(self):
        expected = {
            "nx2/test_bc_storage_views.py", "nx2/test_dll.py", "nx2/test_insn.py", "nx2/test_leaf.py",
            "tests/test_present_aspect_vulkan.py", "tests/test_fs_path_utf8.py", "tests/test_log_batch.py",
            "tests/test_portable_builder_home.py", "tests/test_portable_builder_startup.py",
            "tests/test_portable_setup_paths.py", "tests/test_portable_setup_start.py", "tests/test_portable_setup_transaction.py",
            "tests/test_precompile_compute_guard.py", "tests/test_setup_runtime.py", "tests/test_setup_runtime_paths.py",
            "tests/test_shader_module_guard.py", "tests/test_appimage_package_retry.py", "tests/test_prof_symbols.py",
        }
        skipped = set()
        for d in ("tests", "nx2"):
            for path in sorted((ROOT / d).glob("test_*.py")):
                rel = d + "/" + path.name
                if run_all.read_declarations(str(path), rel, TOOLS)["skip"].get("macos"):
                    skipped.add(rel)
        # In particular, no skip for control, cache files, memory queries, low-memory notices or timing failures.
        self.assertEqual(skipped, expected)

    def test_this_repository(self):
        self.assertEqual((run_all.ARGS, run_all.MODULES, run_all.SKIP), ({}, {}, {}),
                         "tests/run_all.py's ARGS, MODULES and SKIP are retired: declare RUN_ALL_ARGS, RUN_ALL_MODULES "
                         "or RUN_ALL_SKIP in the test file instead (see the docstring of tests/run_all.py)")
        for d in ("tests", "nx2"):
            for f in sorted(os.listdir(ROOT / d)):
                if f.startswith("test_") and f.endswith(".py"):
                    run_all.read_declarations(str(ROOT / d / f), d + "/" + f, TOOLS)


if __name__ == "__main__":
    unittest.main()
