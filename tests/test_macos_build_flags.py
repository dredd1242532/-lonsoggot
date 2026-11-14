"""Game-free build command contracts, including real Mach-O links on macOS.

The frozen Windows/Linux arrays came from main 514591a3's expressions, not a
reimplementation of them. --source checks another tree without importing its
builder or touching its work folder. --capture-main emits those reference arrays
from the old main expressions for review; it is not used during a test run.

On macOS, real links run for the current process architecture by default. Repeat
--arch to opt into other architectures (for example arm64 and x86_64 with Rosetta).
--llvm-prefix adds a second compiler; simulated command contracts always cover both targets.
--fixtures-out keeps exact sources, commands and products outside the checkout.
No renderer, SDL, Metal or Vulkan device is created.
"""
import argparse
import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import ntpath
import os
from pathlib import Path
import platform
import posixpath
import shlex
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

RUN_ALL_ARGS = []
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT
LLVM_PREFIX = None
FIXTURES_OUT = None
ARCHES = None
WAIT_PROVIDER = None
BASE = "514591a30d707fe9f3ad31eae148c3cc1e750bff"
WORK = "/fixture/Games, Zelda"
FUNCTIONS = {"target", "linker", "HOT_ORDER", "flags", "host_flags", "renderer_flags", "executable_link",
             "renderer_link", "ngx_library", "ngx_link", "ffmpeg_linux", "header_package", "cpm_dirs", "cpm_package",
             "check_vk_symbols", "flag_text"}
CONSTANTS = {"NX2_RT", "HOST", "POSIX", "SYSROOT", "LINUX", "DARWIN", "EXE", "VK_LIB", "HOST_LIBS",
             "GPU", "WRITE_TRACK"}
ROUTES = (("linux", "linux", "posix", False), ("msvc", "win32", "nt", False), ("mingw", "linux", "posix", True))


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def os_view(name, env):
    fields = {key: getattr(os, key) for key in dir(os) if not key.startswith("__")}
    fields.update(name=name, environ=env)
    return types.SimpleNamespace(**fields)


def command_space(source, system="darwin", name="posix", mingw=False, arch="arm64", extras=False, real=False, work=WORK):
    """Execute only declarations and command expressions; never import build.py or kit.py."""
    env = dict(os.environ) if real else {}
    env.pop("RX_TARGET", None)
    env.pop("LONGSHOT_MINGW", None)
    if system == "darwin":
        env["RX_ARCH"] = arch
        if not real:
            env["SDKROOT"] = "/fixture/Mac SDK, path"
    else:
        env["RX_MARCH"] = "x86-64-v3"
    if mingw:
        env.update(RX_TARGET="windows", LONGSHOT_MINGW="/fixture/llvm-mingw")
    if extras:
        env.update(RX_RELEASE="1", RX_PGO="gen", RX_MTUNE="znver2", LONGSHOT_SYSROOT="/fixture/sysroot")
    host_os = os_view(name, env)
    osproxy = types.SimpleNamespace(**vars(host_os))
    if not real:
        # The frozen command inputs use a virtual /fixture tree on every test host.
        osproxy.path = posixpath
    # a64-kit's toolchain.py as main has it; on the simulated Mac, nx2/darwin_toolchain.py installed over it, as kit.py does
    tc = load_file("build_flags_toolchain", source / "third_party/a64-kit/tools/toolchain.py")
    tc.os = osproxy
    tc.sys = types.SimpleNamespace(platform=system)
    dt = None
    if (source / "nx2/darwin_toolchain.py").exists():
        with mock.patch.dict(sys.modules, {"toolchain": tc}):
            dt = load_file("build_flags_darwin_toolchain", source / "nx2/darwin_toolchain.py")
        dt.os = osproxy
        dt.sys = tc.sys
        if not real:
            dt.platform = types.SimpleNamespace(machine=lambda: "arm64")
            dt.darwin_sdk = lambda: env["SDKROOT"]
        if system == "darwin":
            dt.install_toolchain()
    if not real:
        tc.find = lambda tool, host=False: "/fixture/tools/" + tool
        tc.need = lambda path: path
        tc.lld_link = lambda: "/fixture/tools/lld-link.exe"
    kitroot = str(source) if real else "/fixture/tree"
    ns = {"os": osproxy, "sys": types.SimpleNamespace(platform=system), "toolchain": tc, "darwin_toolchain": dt,
          "kit": types.SimpleNamespace(ROOT=kitroot, HERE=kitroot + "/nx2", KIT=kitroot + "/third_party/a64-kit", WORK=work),
          "DARWIN": False, "PGO": env.get("RX_PGO", ""), "OPT": "O1", "FFMPEG_LIBS": ("avcodec", "swscale", "swresample", "avutil"),
          "FFMPEG_LX": ([], ["-lavcodec", "-lswscale", "-lswresample", "-lavutil"]) if extras else None,
          "FFMPEG_MAC": None, "FFMPEG": "/fixture/ffmpeg" if extras and (name == "nt" or mingw) else None,
          "NGX_SDK": "/fixture/ngx" if extras else "", "WGPU": "/fixture/wgpu", "CX16": ["-mcx16"],
          "shutil": shutil, "subprocess": subprocess, "hashlib": hashlib}
    if (source / "nx2/darwin_build.py").exists():
        with mock.patch.dict(sys.modules, {"toolchain": tc, "darwin_toolchain": dt}):
            darwin = load_file("build_flags_darwin", source / "nx2/darwin_build.py")
        darwin.os = host_os   # real discovery and temporary-directory fixtures keep their host's paths
        if not real:
            darwin.order_file = lambda path: path + ".macos"
        ns["darwin_build"] = darwin
    cpu_tree = ast.parse((source / "nx2/host_cpu.py").read_text())
    cpuns = {"os": osproxy, "sys": ns["sys"], "toolchain": tc, "darwin_toolchain": dt}
    exec(compile(ast.Module(body=[n for n in cpu_tree.body if isinstance(n, ast.FunctionDef)], type_ignores=[]),
                 "host_cpu.py", "exec"), cpuns)
    ns.update(host_march=cpuns["host_march"], host_mtune=cpuns["host_mtune"])
    path = source / "nx2/build.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in FUNCTIONS]
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), "exec"), ns)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in CONSTANTS for t in node.targets):
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), ns)
    start = next(i for i, n in enumerate(tree.body) if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "MARCH")
    stop = next(i for i in range(start + 1, len(tree.body)) if isinstance(tree.body[i], ast.FunctionDef))
    with contextlib.redirect_stdout(io.StringIO()):
        exec(compile(ast.Module(body=tree.body[start:stop], type_ignores=[]), str(path), "exec"), ns)
    ns["dav1d_windows"] = lambda: "/fixture/dav1d" if extras else None
    return ns, tree


def snapshot(ns, tree, old=False, extras=False):
    """Main's original AST expressions and the branch's callable builders use identical inputs."""
    work = ns["kit"].WORK
    values = {"cc": "/fixture/tools/clang", "cxx": "/fixture/tools/clang++", "gen": work + "/gen", "rt64": work + "/rt64",
              "vk": ns["GPU"] + "/vk", "spirv_headers": "/fixture/spirv", "vk_headers": "/fixture/vulkan",
              "vma_headers": "/fixture/vma", "gen_dir": work + "/generated", "out": work + "/build",
              "wgpu": extras, "rsp": work + "/objs.rsp", "vrsp": work + "/vk_objs.rsp"}
    values.update(exe=values["out"] + "/" + ns["EXE"], vdll=values["out"] + "/" + ns["VK_LIB"])
    ns.update(values)
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    if old:
        # These are expressions in 514591a3, selected by their actual output variable/call, not rewritten flags.
        host = next(n.value for n in ast.walk(main) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "host_fl" for t in n.targets))
        renderer = next(n.value for n in ast.walk(main) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "vflags" for t in n.targets))
        links = [n.value for n in ast.walk(main) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "link" for t in n.targets)]
        cmds = [n.args[0] for n in ast.walk(main) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess" and n.func.attr == "run"
                and n.args and isinstance(n.args[0], ast.BinOp)]
        # Linux executable, Windows executable, game DLL; AST walk retains their source order here.
        cmds.sort(key=lambda n: n.lineno)
        links.sort(key=lambda n: n.lineno)
        ns["win_manifest"] = types.SimpleNamespace(write=lambda p: p)
        def expression(node):
            return eval(compile(ast.Expression(node), "main-command", "eval"), ns)
        with mock.patch.object(ns["os"].path, "exists", side_effect=lambda p: p.endswith("hot_order.txt") or (extras and p.endswith("longshot.res"))):
            return {"target": ns["target"](), "lifted": ns["flags"](values["gen"], values["rt64"], "O1", True),
                    "host": expression(host), "renderer": expression(renderer),
                    "executable_link": expression(cmds[0 if ns["LINUX"] else 1]),
                    "renderer_link": expression(links[0 if ns["LINUX"] else 1])}
    with mock.patch.object(ns["os"].path, "exists", return_value=True):
        return {"target": ns["target"](), "lifted": ns["flags"](values["gen"], values["rt64"], "O1", True),
                "host": ns["host_flags"](values["gen"], values["rt64"]),
                "renderer": ns["renderer_flags"](values["vk"], values["spirv_headers"], values["vk_headers"], values["vma_headers"], values["gen_dir"]),
                "executable_link": ns["executable_link"](values["cc"], values["exe"], values["rsp"], wgpu=extras,
                    manifest=values["out"] + "/longshot_manifest.res", resource=work + "/longshot.res" if extras else ""),
                "renderer_link": ns["renderer_link"](values["cc"], values["cxx"], values["vdll"], values["vrsp"])}


class CommandContracts(unittest.TestCase):
    def test_windows_linux_equal_frozen_main(self):
        frozen = json.loads((ROOT / "tests/build_flags_main.json").read_text())
        self.assertEqual(frozen["base"], BASE)
        for label, system, name, mingw in ROUTES:
            for extras in (False, True):
                key = label + ("-full" if extras else "-plain")
                with self.subTest(key):
                    ns, tree = command_space(SOURCE, system, name, mingw, extras=extras)
                    self.assertEqual(snapshot(ns, tree, old="executable_link" not in ns, extras=extras), frozen["routes"][key])

    def test_frozen_arrays_on_a_windows_path_host(self):
        frozen = json.loads((ROOT / "tests/build_flags_main.json").read_text())
        host = os_view("nt", {})
        host.path, host.sep, host.altsep, host.pathsep = ntpath, ntpath.sep, ntpath.altsep, ntpath.pathsep
        # Replace this test module's host view, not the real os module used by pathlib/importlib.
        # This also makes snapshot's former global os.path point at ntpath, distinct from /fixture's path object.
        with mock.patch.dict(command_space.__globals__, {"os": host}):
            for label, system, name, mingw in ROUTES:
                for extras in (False, True):
                    key = label + ("-full" if extras else "-plain")
                    with self.subTest(key):
                        ns, tree = command_space(SOURCE, system, name, mingw, extras=extras)
                        self.assertEqual(snapshot(ns, tree, old="executable_link" not in ns, extras=extras), frozen["routes"][key])
                        if "darwin_build" in ns:
                            self.assertIs(ns["darwin_build"].os.path, ntpath)
            real, _ = command_space(SOURCE, real=True)
            self.assertIs(real["os"].path, ntpath)
            self.assertIs(os_view("posix", {}).path, ntpath)
        self.assertIs(host.path, ntpath)

    def test_mac_is_not_linux(self):
        for arch in ("arm64", "x86_64"):
            with self.subTest(arch):
                ns, _ = command_space(SOURCE, arch=arch)
                self.assertFalse(ns["LINUX"], "macOS is classified as Linux and receives ELF/x86 flags")
                self.assertTrue(ns["DARWIN"])
                self.assertEqual(ns["EXE"], "longshot")
                self.assertEqual(ns["VK_LIB"], "longshot_vk.dylib")
                flags = ns["flags"](WORK + "/gen", WORK + "/rt64", "O1", True)
                self.assertIn("-mmacosx-version-min=14.4", flags)
                self.assertEqual(flags[flags.index("-arch") + 1], arch)
                self.assertEqual("-mcx16" in flags, arch == "x86_64")
                self.assertEqual("-march=x86-64-v2" in flags, arch == "x86_64")
                self.assertNotIn("-march=x86-64-v3", flags)
                self.assertIn("-ffp-contract=off", flags)

    def test_mac_link_paths_are_separate_and_strict(self):
        ns, tree = command_space(SOURCE)
        self.assertTrue(ns["DARWIN"], "no Mach-O target")
        formed = snapshot(ns, tree)
        for key in ("executable_link", "renderer_link"):
            cmd = formed[key]
            self.assertNotIn("-fuse-ld=lld", cmd)
            self.assertFalse(any(x.startswith("-Wl,") for x in cmd), cmd)
            self.assertIn("-dead_strip", cmd)
            self.assertIn("error", cmd)
            self.assertIn("-headerpad_max_install_names", cmd)
            self.assertNotIn("-flat_namespace", cmd)
            self.assertNotIn("dynamic_lookup", cmd)
            for i, value in enumerate(cmd):
                if value in ("-map", "-install_name", "-rpath", "-order_file", "-exported_symbol"):
                    self.assertEqual(cmd[i - 1], "-Xlinker")
                    self.assertEqual(cmd[i + 1], "-Xlinker")
        self.assertIn("_hos_posix_*", formed["executable_link"])
        self.assertIn(WORK + "/hot_order.txt.macos", formed["executable_link"])
        self.assertIn("@rpath/longshot_vk.dylib", formed["renderer_link"])
        self.assertIn("@loader_path", formed["renderer_link"])
        self.assertIn("error", formed["renderer_link"])
        self.assertNotIn("dynamic_lookup", formed["renderer_link"])
        self.assertNotIn("-static-libgcc", formed["renderer_link"])

    def test_darwin_source_list_classifies_image_dependencies(self):
        ns, _ = command_space(SOURCE)
        self.assertTrue("darwin_build" in ns, "no Darwin source selection")
        shared = {"darwin_wait.c", "darwin_mem.c", "darwin_fault.c", "darwin_fiber.c", "darwin_process.c"}
        owned = {"darwin_cpu.c", "darwin_vulkan.c", "darwin_control_sockets.c", "darwin_wait_state.c",
                 "darwin_guest_mem.c", "darwin_prof.c"}
        with tempfile.TemporaryDirectory() as d:
            for n in shared | owned | {"darwin_wait.h", "prof.c"}:
                Path(d, n).touch()
            host = [Path(p).name for p in ns["darwin_build"].posix_sources(d)]
            renderer = [Path(p).name for p in ns["darwin_build"].posix_sources(d, renderer=True)]
            self.assertEqual(host, ["win32_posix.c", "display_sdl.c", "linux_stubs.c"] + sorted(shared | owned))
            self.assertEqual(renderer, ["win32_posix.c"] + sorted(shared))
            Path(d, "darwin_future_logger.c").touch()
            for image in (False, True):
                with self.assertRaisesRegex(ValueError, "unclassified.*darwin_future_logger.c"):
                    ns["darwin_build"].posix_sources(d, renderer=image)

    def test_sdk_and_compiler_change_cache_identity(self):
        ns, _ = command_space(SOURCE)
        self.assertTrue("darwin_build" in ns, "no macOS compiler identity")
        with mock.patch.object(ns["darwin_build"], "compiler_key", return_value="apple21"):
            apple = ns["flag_text"](["-arch", "arm64", "-O2"])
        with mock.patch.object(ns["darwin_build"], "compiler_key", return_value="llvm23"):
            llvm = ns["flag_text"](["-arch", "arm64", "-O2"])
        self.assertNotEqual(apple, llvm)


class DiscoveryContracts(unittest.TestCase):
    def setUp(self):
        # a64-kit's toolchain.py with nx2/darwin_toolchain.py installed over it, as kit.py and make_game.py do on a Mac
        self.tc = load_file("discovery_toolchain", SOURCE / "third_party/a64-kit/tools/toolchain.py")
        self.tc.sys = types.SimpleNamespace(platform="darwin")
        self.tmp = tempfile.TemporaryDirectory(prefix="longshot tools, ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tc.os = os_view("posix", {})
        self.dt = None
        if (SOURCE / "nx2/darwin_toolchain.py").is_file():
            with mock.patch.dict(sys.modules, {"toolchain": self.tc}):
                self.dt = load_file("discovery_darwin_toolchain", SOURCE / "nx2/darwin_toolchain.py")
            self.dt.sys, self.dt.os = self.tc.sys, self.tc.os
            self.dt.install_toolchain()

    def test_prefix_path_xcrun_order_and_missing(self):
        self.assertTrue(hasattr(self.dt, "_xcrun"), "tool discovery never asks xcrun on main")
        for tool in ("clang", "clang++", "llvm-profdata"):
            with self.subTest(tool):
                prefix = self.root / "LLVM, chosen"
                p = prefix / "bin" / tool
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("authored executable sentinel\n")
                p.chmod(0o700)
                self.tc.os.environ["EMSDK"] = str(prefix)
                with mock.patch.object(self.dt.shutil, "which", return_value="/path/" + tool) as which, \
                        mock.patch.object(self.dt, "_xcrun", return_value="/xcrun/" + tool) as xcrun:
                    # (through toolchain's own names: the installed functions are the ones a64-kit's callers reach)
                    self.assertEqual(self.tc.find(tool), str(p))
                    which.assert_not_called()
                    xcrun.assert_not_called()
                    p.unlink()
                    self.assertEqual(self.tc.find(tool), "/path/" + tool)
                    xcrun.assert_not_called()
                    which.return_value = None
                    self.assertEqual(self.tc.find(tool), "/xcrun/" + tool)
                    xcrun.return_value = ""
                    with self.assertRaisesRegex(SystemExit, "EMSDK.*PATH.*xcrun"):
                        self.tc.need(self.tc.find(tool))

    def test_host_architecture_does_not_follow_game_switch(self):
        self.assertTrue(hasattr(self.dt, "architecture"), "no host/target architecture separation")
        self.dt.platform = types.SimpleNamespace(machine=lambda: "arm64")
        self.tc.os.environ.update(RX_ARCH="x86_64", SDKROOT=self.tmp.name)
        self.assertEqual(self.dt.architecture(), "x86_64")
        self.assertEqual(self.dt.architecture(host=True), "arm64")
        self.tc.os.environ["RX_ARCH"] = "invalid"
        with self.assertRaisesRegex(SystemExit, "RX_ARCH"):
            self.dt.architecture()

    def test_pkg_config_preserves_spaces_and_explicit_prefix(self):
        ns, _ = command_space(SOURCE)
        self.assertTrue("darwin_build" in ns, "no Darwin pkg-config parser")
        darwin = ns["darwin_build"]
        seen = []
        def run(cmd, **kw):
            seen.append((cmd, kw["env"]))
            out = r"-I/opt/a\ b,c/include" if "--cflags" in cmd else r"-L/opt/a\ b,c/lib -lavcodec"
            return subprocess.CompletedProcess(cmd, 0, out, "")
        with mock.patch.object(darwin.shutil, "which", return_value="/opt/homebrew/bin/pkg-config"), \
                mock.patch.object(darwin.subprocess, "run", side_effect=run):
            flags = darwin.pkg_config(["libavcodec"], prefix="/chosen SDK, x86", static=True)
        self.assertEqual(flags, (["-I/opt/a b,c/include"], ["-L/opt/a b,c/lib", "-lavcodec"]))
        for cmd, env in seen:
            self.assertIn("--static", cmd)
            self.assertEqual(env["PKG_CONFIG_PATH"], "/chosen SDK, x86/lib/pkgconfig" + os.pathsep + "/chosen SDK, x86/share/pkgconfig")
            self.assertEqual(env["PKG_CONFIG_LIBDIR"], env["PKG_CONFIG_PATH"])

    def test_translated_python_defaults_to_native_game_but_keeps_its_helpers(self):
        self.assertTrue(hasattr(self.dt, "_apple_silicon"), "main has no physical/process CPU distinction")
        self.dt.platform = types.SimpleNamespace(machine=lambda: "x86_64")
        with mock.patch.object(self.dt, "_apple_silicon", return_value=True):
            self.assertEqual(self.dt.architecture(), "arm64")
            self.assertEqual(self.dt.architecture(host=True), "x86_64")

    def test_helpers_use_process_architecture_not_rx_arch(self):
        ns, _ = command_space(SOURCE, arch="x86_64")
        self.assertTrue(ns["DARWIN"], "main has no macOS helper path")
        tc, dt = ns["toolchain"], ns["darwin_toolchain"]
        for process_arch in ("arm64", "x86_64"):
            dt.platform = types.SimpleNamespace(machine=lambda a=process_arch: a)
            with self.subTest(process_arch=process_arch), \
                    mock.patch.dict(sys.modules, {"toolchain": tc, "darwin_toolchain": dt, "kit": ns["kit"]}), \
                    mock.patch.object(sys, "path", list(sys.path)), mock.patch.object(sys, "platform", "darwin"):
                emitter = load_file("mac_emit", SOURCE / "third_party/a64-kit/lift/build_emit.py")
                dt.install_emit(emitter)
                zbic = load_file("mac_zbic", SOURCE / "nx2/zbic.py")
                for output, cmd in ((emitter.OUT, dt.emit_command(emitter)), (zbic.DLL, zbic.command())):
                    self.assertIn(process_arch + ".dylib", output)
                    self.assertEqual(cmd[cmd.index("-arch") + 1], process_arch)
                    self.assertIn("-mmacosx-version-min=14.4", cmd)
                    self.assertIn("-dynamiclib", cmd)

    @unittest.skipUnless(sys.platform == "darwin", "kit.py and make_game.py install the macOS toolchain on macOS only")
    def test_kit_and_maker_install_the_macos_toolchain(self):
        self.assertTrue(self.dt, "main has no macOS toolchain for kit.py and make_game.py to install")
        probe = ("import sys; sys.path.insert(0, sys.argv[1]); import %s; import toolchain, build_emit, darwin_toolchain as d; "
                 "print(toolchain.find is d.find, toolchain.need is d.need, toolchain.native_target is d.native_target, "
                 "toolchain.native_link is d.native_link, build_emit.build.__qualname__, build_emit.OUT)")
        arch = real_architectures()[0]
        env = {k: v for k, v in os.environ.items() if not k.startswith(("RX_", "LONGSHOT_", "OOT_"))}
        env["OOT_WORK"] = str(self.root / "work")   # (kit.py reads work/dump.txt: an empty one of the test's)
        for module in ("kit", "make_game"):
            with self.subTest(module):
                result = subprocess.run([sys.executable, "-I", "-B", "-c", probe % module, str(SOURCE / "nx2")],
                                        capture_output=True, text=True, env=env, timeout=120)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                line = result.stdout.strip().splitlines()[-1].split(" ")
                self.assertEqual(line[:4], ["True"] * 4, result.stdout)
                self.assertEqual(line[4], "_emit_build.<locals>.build")
                self.assertEqual(" ".join(line[5:]), str(SOURCE / "third_party/a64-kit/lift" / ("liba64emit-%s.dylib" % arch)))

    def test_maker_dispatches_to_macos_without_windows_or_linux_probe(self):
        tree = ast.parse((SOURCE / "nx2/make_game.py").read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "check_windows_sdk")
        called = []
        ns = {"LINUX": False, "DARWIN": True, "check_darwin_prerequisites": lambda work: called.append(work),
              "bundled_mingw": lambda: self.fail("macOS selected a Windows toolchain probe")}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "make_game.py", "exec"), ns)
        ns["check_windows_sdk"]("authored empty work")
        self.assertEqual(called, ["authored empty work"])

    def test_macos_preflight_keeps_strict_links_without_fatal_deprecations(self):
        tree = ast.parse((SOURCE / "nx2/make_game.py").read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "check_darwin_prerequisites")
        for arch in ("arm64", "x86_64"):
            ns, _ = command_space(SOURCE, arch=arch)
            tc, commands, inspected = ns["toolchain"], [], []
            plan = types.SimpleNamespace(ffmpeg=([], ["/authored/ffmpeg.dylib"]), sdl=([], ["/authored/SDL2.dylib"]))
            build = types.SimpleNamespace(prebuilt_renderer=lambda: "/authored/renderer.dylib", FFMPEG_LIBS=ns["FFMPEG_LIBS"],
                                          darwin_build=types.SimpleNamespace(dependencies=lambda *a: plan), target=ns["target"])
            def run(command, **kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")
            scope = {"os": ns["os"], "ROOT": str(self.root), "_toolchain": tc, "sys": sys, "subprocess": subprocess,
                     "tempfile": tempfile, "fail": self.fail,
                     "darwin_package": types.SimpleNamespace(DependencyError=ValueError,
                         inspect=lambda path, target: inspected.append((path, target)))}
            exec(compile(ast.Module(body=[fn], type_ignores=[]), "make_game.py", "exec"), scope)
            with self.subTest(arch=arch), mock.patch.dict(sys.modules, {"build": build}), \
                    mock.patch.object(tc, "version", return_value="authored command fixture"), \
                    mock.patch.object(subprocess, "run", side_effect=run), contextlib.redirect_stdout(io.StringIO()):
                scope["check_darwin_prerequisites"](str(self.root))
            self.assertEqual(len(commands), 2)
            self.assertEqual([a for _, a in inspected], [arch, arch])
            for command in commands:
                self.assertEqual(command[command.index("-arch") + 1], arch)
                self.assertIn("-mmacosx-version-min=14.4", command)
                self.assertIn("-undefined", command)
                self.assertIn("error", command)
                self.assertNotIn("-fatal_warnings", command)

    def test_real_architecture_default_and_explicit_selection(self):
        for machine, expected in (("arm64", "arm64"), ("x86_64", "x86_64"), ("AMD64", "x86_64")):
            with self.subTest(machine=machine), mock.patch.object(platform, "machine", return_value=machine):
                self.assertEqual(real_architectures(), [expected])
                self.assertEqual(real_architectures(["arm64", "x86_64"]), ["arm64", "x86_64"])
                self.assertEqual(real_architectures(["x86_64", "x86_64"]), ["x86_64"])


C_LIBRARY = r'''#include <dlfcn.h>
#include <stddef.h>
int fixture_value = 11;
__attribute__((noinline)) int fixture_collision(void) { return fixture_value; }
int library_a(void) { return fixture_collision(); }
int library_host(void) {
    int (*fn)(void) = (int (*)(void))dlsym(RTLD_DEFAULT, "hos_posix_fixture");
    return fn ? fn() : -1;
}
void *library_wait_state(void) {
    void *(*provider)(void) = (void *(*)(void))dlsym(RTLD_MAIN_ONLY, "hos_posix_address_wait_sequences");
    return provider ? provider() : NULL;
}
'''
CPP_LIBRARY = r'''#include <string>
#include <dlfcn.h>
extern "C" {
int fixture_value = 22;
__attribute__((noinline)) int fixture_collision(void) { return fixture_value; }
int library_b(const char *input) {
    std::string text(input);
    text.append(64, 'x');
    return text.size() == 65 ? fixture_collision() : -1;
}
void *library_wait_state(void) {
    auto provider = (void *(*)(void))dlsym(RTLD_MAIN_ONLY, "hos_posix_address_wait_sequences");
    return provider ? provider() : nullptr;
}
}
'''
C_MAIN = r'''#include <dlfcn.h>
#include <stdio.h>
extern unsigned *hos_posix_address_wait_sequences(void);
int unused_fixture(void) { return 71; }
__attribute__((noinline)) int hos_posix_order_one(void) { return 1; }
__attribute__((noinline)) int hos_posix_order_two(void) { return 2; }
int hos_posix_fixture(void) { return 99; }
int main(void) {
    void *a = dlopen("@rpath/library A, one.dylib", RTLD_NOW | RTLD_GLOBAL);
    void *b = dlopen("@rpath/library B, two.dylib", RTLD_NOW | RTLD_GLOBAL);
    if (!a || !b) { fprintf(stderr, "dlopen: %s\n", dlerror()); return 1; }
    int (*av)(void) = (int (*)(void))dlsym(a, "library_a");
    int (*bv)(const char *) = (int (*)(const char *))dlsym(b, "library_b");
    int (*host)(void) = (int (*)(void))dlsym(a, "library_host");
    int *ad = (int *)dlsym(a, "fixture_value"), *bd = (int *)dlsym(b, "fixture_value");
    int ok = av && bv && host && ad && bd && av() == 11 && bv("B") == 22 && host() == 99 && ad != bd;
    if (ok) { *ad = 31; *bd = 42; ok = av() == 31 && bv("B") == 42; }
    ok = ok && dlsym(RTLD_DEFAULT, "unused_fixture") == NULL;
    void *(*as)(void) = (void *(*)(void))dlsym(a, "library_wait_state");
    void *(*bs)(void) = (void *(*)(void))dlsym(b, "library_wait_state");
    unsigned *cells = hos_posix_address_wait_sequences();
    int wait_ok = cells && as && bs && as() == cells && bs() == cells;
    if (wait_ok) for (unsigned i = 0; i < 256; i++) if (cells[i]) wait_ok = 0;
    if (wait_ok) { cells[255] = 0xabcdef01u; wait_ok = ((unsigned *)as())[255] == 0xabcdef01u && ((unsigned *)bs())[255] == 0xabcdef01u; }
    ok = ok && wait_ok;
    printf("canonical_wait_export=%s words=256 host_flags=production\n", wait_ok ? "PASS" : "FAIL");
    printf("binding own_a=%d own_b=%d host_export=%d distinct_data=%d result=%s\n",
           av ? av() : -1, bv ? bv("B") : -1, host ? host() : -1, ad != bd, ok ? "PASS" : "FAIL");
    dlclose(b); dlclose(a);
    return ok ? 0 : 1;
}
'''


def real_architectures(requested=None):
    """Ordinary suites require no translator; an explicitly requested unavailable target must fail."""
    machine = platform.machine().lower()
    native = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    return list(dict.fromkeys(requested or [native]))


@unittest.skipUnless(sys.platform == "darwin", "real Mach-O links require macOS; command contracts run everywhere")
class RealMachO(unittest.TestCase):
    def test_c_cpp_links_load_exports_order_and_local_binding(self):
        self.assertTrue((SOURCE / "nx2/darwin_build.py").is_file(), "main has no callable Mach-O command path")
        if FIXTURES_OUT:
            root = FIXTURES_OUT
            root.mkdir(parents=True, exist_ok=False)
        else:
            tmp = tempfile.TemporaryDirectory(prefix="longshot link, ")
            self.addCleanup(tmp.cleanup)
            root = Path(tmp.name)
        compilers = [("apple", None)] + ([("llvm23", LLVM_PREFIX)] if LLVM_PREFIX else [])
        for label, prefix in compilers:
            for arch in real_architectures(ARCHES):
                with self.subTest(compiler=label, arch=arch):
                    self.fixture(root / (label + " " + arch + ", test"), prefix, arch)

    def fixture(self, out, prefix, arch):
        out.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("RX_", "HOS_", "LONGSHOT_", "DYLD_")) and k not in ("EMSDK", "SDKROOT")}
        env["RX_ARCH"] = arch
        if prefix:
            env["EMSDK"] = str(prefix)
        else:
            # Do not accidentally test a Homebrew compiler as Apple's.
            env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        with mock.patch.dict(os.environ, env, clear=True):
            ns, _ = command_space(SOURCE, arch=arch, real=True, work=str(out))
        tc = ns["toolchain"]
        commands = []
        def run(cmd, good=True):
            cmd = [str(c) for c in cmd]
            result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
            commands.append({"command": cmd, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
            (out / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
            print("COMMAND " + shlex.join(cmd), flush=True)
            if result.stdout:
                print(result.stdout, end="", flush=True)
            if result.stderr:
                print(result.stderr, end="", flush=True)
            if good:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            else:
                self.assertNotEqual(result.returncode, 0, "an unresolved Mach-O symbol was accepted")
            return result.stdout + result.stderr
        cc, cxx = tc.need(tc.find("clang")), tc.need(tc.find("clang++"))
        run([cc, "--version"])
        run([cxx, "--version"])
        run([tc.need(tc.find("llvm-profdata")), "--version"])
        paths = {"a": out / "library A.c", "b": out / "library B.cpp", "main": out / "host main.c"}
        for key, text in (("a", C_LIBRARY), ("b", CPP_LIBRARY), ("main", C_MAIN)):
            paths[key].write_text(text)
        (out / "hot_order.txt").write_text("hos_posix_order_two\nhos_posix_order_one\n")
        cflags = ns["flags"](str(out / "gen"), str(out / "rt64"), "O1", True)
        hostflags = ns["host_flags"](str(out / "gen"), str(out / "rt64"))
        cppflags = ns["renderer_flags"](str(SOURCE / "gpu/vk"), str(out / "spirv"), str(out / "vulkan"), str(out / "vma"), str(out / "generated"))
        for key, compiler, flags in (("a", cc, cflags), ("b", cxx, cppflags), ("main", cc, hostflags)):
            run([compiler] + flags + ["-c", paths[key], "-o", out / (key + ".o")])
        provider = WAIT_PROVIDER or SOURCE / "host/posix/darwin_wait_state.c"
        self.assertTrue(provider.is_file(), "canonical wait-state source must be integrated or supplied with --wait-provider")
        provider_inputs = [provider, provider.with_name("darwin_wait.h")]
        (out / "provider-inputs.json").write_text(json.dumps({str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                                            for p in provider_inputs}, indent=2) + "\n")
        run([cc] + hostflags + ["-c", provider, "-o", out / "wait-state.o"])
        metadata = load_file("build_flags_package", SOURCE / "nx2/darwin_package.py")
        for key, name in (("a", "library A, one.dylib"), ("b", "library B, two.dylib")):
            rsp = out / (key + ".rsp")
            rsp.write_text('"%s"\n' % (out / (key + ".o")))
            lib = out / name
            run(ns["renderer_link"](cc, cxx, str(lib), str(rsp)))
            load = run(["/usr/bin/otool", "-l", lib])
            self.assertIn("@rpath/" + name, load)
            self.assertIn("minos 14.4", load)
            self.assertIn("@loader_path", load)
            with mock.patch.dict(sys.modules, {"darwin_package": metadata}):
                self.assertTrue(ns["darwin_build"].post_link(str(lib)))
            if key == "b":
                imports = run(["/usr/bin/nm", "-u", lib])
                self.assertRegex(imports, r"(__1|Znwm|ZdlPv)", "C++ work was optimized away, not a runtime-library proof")
        rsp = out / "main.rsp"
        rsp.write_text('"%s"\n"%s"\n' % (out / "main.o", out / "wait-state.o"))
        exe = out / "host executable, test"
        run(ns["executable_link"](cc, str(exe), str(rsp)))
        self.assertTrue(Path(str(exe) + ".map").is_file())
        syms = run(["/usr/bin/nm", "-n", exe])
        self.assertLess(syms.index("_hos_posix_order_two"), syms.index("_hos_posix_order_one"))
        self.assertNotIn("_unused_fixture", syms)
        self.assertIn("_hos_posix_address_wait_sequences", syms)
        self.assertEqual((out / "hot_order.txt.macos").read_text(), "_hos_posix_order_two\n_hos_posix_order_one\n")
        load = run(["/usr/bin/otool", "-l", exe])
        self.assertIn("minos 14.4", load)
        result = run((["/usr/bin/arch", "-x86_64"] if arch == "x86_64" else []) + [exe])
        self.assertIn("binding own_a=31 own_b=42 host_export=99 distinct_data=1 result=PASS", result)
        self.assertIn("canonical_wait_export=PASS words=256 host_flags=production", result)
        bad = out / "undefined.c"
        bad.write_text("extern int missing_external(void); int exported(void) { return missing_external(); }\n")
        run([cc] + cflags + ["-c", bad, "-o", out / "undefined.o"])
        badrsp = out / "undefined.rsp"
        badrsp.write_text('"%s"\n' % (out / "undefined.o"))
        badresult = run(ns["renderer_link"](cc, cxx, str(out / "bad.dylib"), str(badrsp)), good=False)
        self.assertIn("missing_external", badresult)
        weak = out / "weak_undefined.c"
        weak.write_text("extern int optional_callback(void) __attribute__((weak));\n"
                        "int exported_weak(void) { return optional_callback ? optional_callback() : 21; }\n")
        run([cc] + cflags + ["-c", weak, "-o", out / "weak_undefined.o"])
        weakrsp = out / "weak_undefined.rsp"
        weakrsp.write_text('"%s"\n' % (out / "weak_undefined.o"))
        weakresult = run(ns["renderer_link"](cc, cxx, str(out / "weak_bad.dylib"), str(weakrsp)), good=False)
        self.assertIn("optional_callback", weakresult)
        print("MACHO compiler=%s arch=%s min=14.4 links=3 load=PASS exports=PASS order=PASS binding=PASS undefined=rejected weak_unbound=rejected" %
              ("LLVM23" if prefix else "Apple21", arch), flush=True)


def capture_main():
    routes = {}
    for label, system, name, mingw in ROUTES:
        for extras in (False, True):
            ns, tree = command_space(SOURCE, system, name, mingw, extras=extras)
            routes[label + ("-full" if extras else "-plain")] = snapshot(ns, tree, old=True, extras=extras)
    return {"base": BASE, "source_sha256": {n: hashlib.sha256((SOURCE / n).read_bytes()).hexdigest()
            for n in ("nx2/build.py", "third_party/a64-kit/tools/toolchain.py")}, "routes": routes}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--capture-main", action="store_true")
    parser.add_argument("--llvm-prefix", type=Path)
    parser.add_argument("--fixtures-out", type=Path)
    parser.add_argument("--wait-provider", type=Path, help="canonical provider source from an explicitly named integration input")
    parser.add_argument("--arch", action="append", choices=("arm64", "x86_64"),
                        help="real fixture target; repeat for both (default: current process architecture)")
    args, rest = parser.parse_known_args()
    SOURCE, LLVM_PREFIX, FIXTURES_OUT = args.source, args.llvm_prefix, args.fixtures_out
    ARCHES = args.arch
    WAIT_PROVIDER = args.wait_provider
    if args.capture_main:
        print(json.dumps(capture_main(), indent=2))
    else:
        unittest.main(argv=[sys.argv[0], *rest])
