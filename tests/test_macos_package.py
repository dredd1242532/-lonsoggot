"""Game-free macOS dependency admission and relocated-package behavior.

Synthetic metadata exercises rejection paths on every OS. On macOS, real small
C/C++ Mach-O products use the production command builders and packager. They are
not SDL/Vulkan/game implementations and no device is created. Repeat --arch for
native arm64 and Rosetta; --fixtures-out retains commands, exits and artifacts.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "nx2"), str(ROOT / "third_party/a64-kit/tools")]
darwin_build = importlib.import_module("darwin_build")
package = importlib.import_module("darwin_package")
archive_receipts = importlib.import_module("darwin_archive")


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


commands_fixture = load_file("macos_package_commands", ROOT / "tests/test_macos_build_flags.py")
ARCHES = None
FIXTURES_OUT = None


def string_command(kind, text):
    offset = 12 if kind == 0x8000001c else 24
    encoded = text.encode() + b"\0"
    size = (offset + len(encoded) + 7) & ~7
    return struct.pack("<III", kind, size, offset) + b"\0" * (offset - 12) + encoded + b"\0" * (size - offset - len(encoded))


def image(arch="arm64", floor: str | None = "14.4", kind=6, install_id="@rpath/libfixture.dylib", loads=(), rpaths=(), platform=1,
          twolevel=True):
    """Authored load-command bytes only; these metadata fixtures cannot execute."""
    commands = []
    if floor:
        a, b, c = package.version(floor)
        commands.append(struct.pack("<6I", 0x32, 24, platform, (a << 16) | (b << 8) | c, 0, 0))
    if kind == 6:
        commands.append(string_command(0xd, install_id))
    commands += [string_command(0xc, name) for name in loads]
    commands += [string_command(0x8000001c, name) for name in rpaths]
    body = b"".join(commands)
    return struct.pack("<8I", 0xfeedfacf, package.CPU[arch], 0 if arch == "arm64" else 3, kind, len(commands), len(body),
                       0x80 if twolevel else 0, 0) + body


def archive(*members):
    result = b"!<arch>\n"
    for i, body in enumerate(members):
        name = ("member%d.o/" % i).ljust(16)
        header = (name + "0".ljust(12) + "0".ljust(6) + "0".ljust(6) + "100644".ljust(8) + str(len(body)).ljust(10) + "`\n").encode()
        result += header + body + (b"\n" if len(body) & 1 else b"")
    return result


def symbol_object(defined="_entry", undefined=(), floor=None):
    """A hand-authored x86_64 Mach-O OBJECT with real symbol/section tables; never executed."""
    deployment = b""
    if floor:
        major, minor, patch = package.version(floor)
        deployment = struct.pack("<6I", 0x32, 24, 1, (major << 16) | (minor << 8) | patch, 0, 0)
    code_at = 32 + 152 + 24 + len(deployment)
    sym_at = (code_at + 8) & ~7
    strings, symbols = b"\0", []
    for name, kind, section in [(defined, 0x0f, 1)] + [(n, 1, 0) for n in undefined]:
        symbols.append(struct.pack("<IBBHQ", len(strings), kind, section, 0, 0))
        strings += name.encode() + b"\0"
    table = b"".join(symbols)
    segment = struct.pack("<II16sQQQQiiII", 0x19, 152, b"__TEXT", 0, 1, code_at, 1, 7, 5, 1, 0)
    section = struct.pack("<16s16sQQIIIIIIII", b"__text", b"__TEXT", 0, 1, code_at, 0, 0, 0, 0x80000400, 0, 0, 0)
    symtab = struct.pack("<6I", 2, 24, sym_at, len(symbols), sym_at + len(table), len(strings))
    commands = segment + section + symtab + deployment
    header = struct.pack("<8I", 0xfeedfacf, package.CPU["x86_64"], 3, 1, 2 + bool(deployment), len(commands), 0, 0)
    return header + commands + b"\xc3" + b"\0" * (sym_at - code_at - 1) + table + strings


def chained_imports(imports, form=1, header=None):
    """An authored LC_DYLD_CHAINED_FIXUPS payload: header, import table and names. Chain starts are not needed."""
    names, table = b"", b""
    for ordinal, name in imports:
        offset = len(names)
        names += name.encode() + b"\0"
        if form == 3:
            table += struct.pack("<QQ", (ordinal & 0xffff) | (offset << 32), 0)
        else:
            table += struct.pack("<I", (ordinal & 0xff) | (offset << 9)) + (struct.pack("<i", 0) if form == 2 else b"")
    values = header or (0, 0, 28, 28 + len(table), len(imports), form, 0)
    return struct.pack("<7I", *values) + table + names


def bind_opcodes(*binds, lazy=False):
    """Authored LC_DYLD_INFO bind opcodes for (ordinal, symbol) pairs; ordinal None sets none (weak coalescing)."""
    stream = b""
    for ordinal, symbol in binds:
        stream += b"\x72\x10"  # SET_SEGMENT_AND_OFFSET_ULEB: segment 2, offset 16
        if ordinal is not None and ordinal <= 0:
            stream += bytes([0x30 | (ordinal & 0x0f)])  # SET_DYLIB_SPECIAL_IMM
        elif ordinal is not None and ordinal < 16:
            stream += bytes([0x10 | ordinal])  # SET_DYLIB_ORDINAL_IMM
        elif ordinal is not None:
            stream += b"\x20" + bytes([ordinal & 0x7f | 0x80, ordinal >> 7])  # SET_DYLIB_ORDINAL_ULEB, two bytes
        stream += b"\x40" + symbol.encode() + b"\0\x51\x90"  # SET_SYMBOL, SET_TYPE_IMM pointer, DO_BIND
        if lazy:
            stream += b"\x00"  # each lazy entry ends with DONE
    return stream + b"\x00"


def bound_image(chained=None, streams=None, symbol_ordinal=None, loads=("/usr/lib/libSystem.B.dylib",)):
    """An authored dylib whose authoritative binding is chained-fixups imports or LC_DYLD_INFO_ONLY streams.

    symbol_ordinal adds one undefined LC_SYMTAB entry naming that library ordinal."""
    base = image(loads=list(loads))
    header = list(struct.unpack("<8I", base[:32]))
    commands, sizes = [], []
    if chained is not None:
        sizes.append(16)
    if streams is not None:
        sizes.append(48)
    if symbol_ordinal is not None:
        sizes.append(24)
    data_at = len(base) + sum(sizes)
    data = b""
    if chained is not None:
        commands.append(struct.pack("<4I", 0x80000034, 16, data_at, len(chained)))
        data += chained
    if streams is not None:
        fields = []
        for name in ("bind", "weak", "lazy"):
            stream = streams.get(name, b"")
            fields += [data_at + len(data) if stream else 0, len(stream)]
            data += stream
        commands.append(struct.pack("<12I", 0x80000022, 48, 0, 0, *fields, 0, 0))
    if symbol_ordinal is not None:
        names = b"\0_symbol_fixture\0"
        symbols = data_at + len(data)
        commands.append(struct.pack("<6I", 2, 24, symbols, 1, symbols + 16, len(names)))
        data += struct.pack("<IBBHQ", 1, 1, 0, symbol_ordinal << 8, 0) + names
    header[4] += len(commands)
    header[5] += sum(sizes)
    return struct.pack("<8I", *header) + base[32:] + b"".join(commands) + data


def with_command(content, command):
    """Append one load command to an authored image() that is only a header and load commands."""
    header = list(struct.unpack("<8I", content[:32]))
    header[4] += 1
    header[5] += len(command)
    return struct.pack("<8I", *header) + content[32:] + command


def symtab_ordinal(content, old, new):
    """A copy of a real linked image whose undefined LC_SYMTAB entries claim library ordinal `new` instead of `old`."""
    data = bytearray(content)
    ncmds = struct.unpack_from("<I", data, 16)[0]
    at, changed = 32, 0
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, at)
        if cmd == 2:
            symoff, nsyms = struct.unpack_from("<II", data, at + 8)
            for i in range(nsyms):
                entry = symoff + 16 * i
                kind, desc, value = data[entry + 4], struct.unpack_from("<H", data, entry + 6)[0], struct.unpack_from("<Q", data, entry + 8)[0]
                if kind & 0xe0 == 0 and kind & 0x0f == 1 and not value and desc >> 8 == old:
                    struct.pack_into("<H", data, entry + 6, (desc & 0xff) | (new << 8))
                    changed += 1
        at += size
    assert changed, "the real flat link has no undefined dynamic-lookup symbol to disguise"
    return bytes(data)


class PackageFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="longshot package, ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.sdk = self.root / "sdk"
        self.sdk.mkdir()

    def put(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path


class MetadataContracts(PackageFixture):
    def test_exact_architecture_and_floor_not_a_successful_link_warning(self):
        for target in ("arm64", "x86_64"):
            good = self.put(target + ".dylib", image(target))
            self.assertEqual(package.inspect(good, target)["minimum_os"], "14.4.0")
            for label, contents, error in (
                ("wrong-arch", image("arm64" if target == "x86_64" else "x86_64"), "slice"),
                ("new-os", image(target, "26.0"), "newer than target 14.4"),
                ("ios", image(target, platform=2), "not macOS"),
                ("unstamped", image(target, floor=None), "no declared macOS minimum"),
                ("flat", image(target, twolevel=False), "not two-level"),
                ("truncated", image(target)[:-1], "truncated")):
                with self.subTest(target=target, label=label):
                    path = self.put(label + ".dylib", contents)
                    with self.assertRaisesRegex(package.DependencyError, error):
                        package.inspect(path, target)

    def test_unbound_weak_or_strong_symbols_do_not_bypass_strict_imports(self):
        for weak in (False, True):
            for ordinal in (1, 0xfe, 0xff):
                content = image(loads=["/usr/lib/libSystem.B.dylib"])
                header = list(struct.unpack("<8I", content[:32]))
                header[4] += 1
                header[5] += 24
                symbols = 32 + header[5]
                names = b"\0_optional_fixture\0"
                command = struct.pack("<6I", 2, 24, symbols, 1, symbols + 16, len(names))
                symbol = struct.pack("<IBBHQ", 1, 1, 0, (ordinal << 8) | (0x40 if weak else 0), 0)
                path = self.put("symbol.dylib", struct.pack("<8I", *header) + content[32:] + command + symbol + names)
                if ordinal == 1:
                    package.inspect(path, "arm64")  # a known provider ordinal, not an unbound lookup
                else:
                    with self.assertRaisesRegex(package.DependencyError, "dynamic-lookup"):
                        package.inspect(path, "arm64")

    def test_authoritative_dyld_bindings_refuse_flat_executable_and_unresolved_imports(self):
        # Every authored symbol table here claims library 1; dyld binds from the chained imports or opcode streams.
        good = [(1, "_known"), (0, "_self"), (-3, "_weak_coalesced")]
        bad = [(-2, "dynamic-lookup.*flat lookup"), (-1, "dynamic-lookup.*main executable"), (2, "unresolved two-level"),
               (-4, "unresolved two-level")]
        for form in (1, 2, 3):
            package.inspect(self.put("chained.dylib", bound_image(chained_imports(good, form), symbol_ordinal=1)), "arm64")
            for ordinal, error in bad:
                with self.subTest(format="chained %d" % form, ordinal=ordinal):
                    path = self.put("chained.dylib", bound_image(chained_imports(good + [(ordinal, "_bad")], form), symbol_ordinal=1))
                    with self.assertRaisesRegex(package.DependencyError, error):
                        package.inspect(path, "arm64")
        for stream in ("bind", "weak", "lazy"):
            lazy = stream == "lazy"
            package.inspect(self.put("info.dylib", bound_image(streams={stream: bind_opcodes(*good, lazy=lazy)}, symbol_ordinal=1)), "arm64")
            for ordinal, error in bad + [(300, "unresolved two-level")]:
                with self.subTest(stream=stream, ordinal=ordinal):
                    path = self.put("info.dylib", bound_image(streams={stream: bind_opcodes(*good, (ordinal, "_bad"), lazy=lazy)},
                                                              symbol_ordinal=1))
                    with self.assertRaisesRegex(package.DependencyError, error):
                        package.inspect(path, "arm64")
        # A weak-bind entry that sets no ordinal coalesces by name (dyld's load-order search among weak definitions);
        # it is not a BIND_SPECIAL_DYLIB_FLAT_LOOKUP import.
        package.inspect(self.put("weak.dylib", bound_image(streams={"weak": bind_opcodes((None, "_coalesced"))})), "arm64")
        # The symbol table's own ordinal must name a loaded dylib too.
        with self.assertRaisesRegex(package.DependencyError, "unresolved two-level"):
            package.inspect(self.put("symbol.dylib", bound_image(symbol_ordinal=2)), "arm64")
        for label, content, error in (
                ("opcode", bound_image(streams={"bind": b"\xe0"}), "unknown Mach-O bind opcode"),
                ("uleb", bound_image(streams={"bind": b"\x20\x80"}), "truncated Mach-O binding"),
                ("no symbol", bound_image(streams={"bind": b"\x11\x90\x00"}), "no symbol"),
                ("chained header", bound_image(chained=b"\0" * 27), "truncated Mach-O chained-fixups"),
                ("chained format", bound_image(chained_imports(good, header=(0, 0, 28, 40, 3, 4, 0))), "unsupported"),
                ("compressed names", bound_image(chained_imports(good, header=(0, 0, 28, 40, 3, 1, 1))), "unsupported"),
                ("chained count", bound_image(chained_imports(good, header=(0, 0, 28, 40, 1000, 1, 0))), "extend past"),
                ("chained name", bound_image(struct.pack("<7I", 0, 0, 28, 32, 1, 1, 0) + struct.pack("<I", 1 | 500 << 9) + b"_known\0"),
                 "import name")):
            with self.subTest(malformed=label), self.assertRaisesRegex(package.DependencyError, error):
                package.inspect(self.put("malformed.dylib", content), "arm64")

    def test_lazy_done_keeps_the_library_ordinal_like_dyld(self):
        # dyld keeps the library ordinal across a lazy-stream DONE: _x below binds by flat lookup (-2), not ordinal 0.
        flat = b"\x3e\x00\x72\x10\x40_x\x00\x90\x00"
        with self.assertRaisesRegex(package.DependencyError, "_x, flat lookup"):
            package.inspect(self.put("lazy flat.dylib", bound_image(streams={"lazy": flat}, symbol_ordinal=1)), "arm64")
        # The same carry-over through a loaded dylib's ordinal stays admitted.
        known = b"\x11\x00\x72\x10\x40_x\x00\x90\x00"
        package.inspect(self.put("lazy known.dylib", bound_image(streams={"lazy": known}, symbol_ordinal=1)), "arm64")

    def test_sdl2_compat_requires_sdl3_whatever_its_rpath(self):
        names = ("avcodec", "swscale", "swresample", "avutil")
        lib, inc = self.root / "prefix/lib", self.root / "prefix/include"
        for name in names:
            self.put("prefix/lib/lib%s.dylib" % name, image(install_id="@rpath/lib%s.dylib" % name))
            self.put("prefix/include/lib%s/%s.h" % (name, name), b"/* authored codec API */\n")
        # As SDL2-compat's receipt shows: only an @loader_path rpath; SDL3 is named solely as dlopen data.
        classic = image(install_id="@rpath/libSDL2-2.0.0.dylib", rpaths=["@loader_path"])
        sdl = self.put("SDL/lib/libSDL2-2.0.0.dylib", classic + b"@loader_path/libSDL3.dylib\0")
        sdl3 = self.put("SDL/lib/libSDL3.0.dylib", image(install_id="@rpath/libSDL3.0.dylib"))
        molten = self.put("Vulkan/libMoltenVK.dylib", image(install_id="@rpath/libMoltenVK.dylib"))
        with mock.patch.object(darwin_build, "ffmpeg_flags", return_value=(["-I" + str(inc)], ["-L" + str(lib)] + ["-l" + n for n in names])), \
                mock.patch.object(darwin_build, "sdl_flags", return_value=([], [str(sdl)])), \
                mock.patch.dict(os.environ, {"LONGSHOT_MOLTENVK": str(molten), "LONGSHOT_SDL2": str(sdl), "LONGSHOT_SDL3": ""}), \
                mock.patch.object(darwin_build.toolchain, "architecture", return_value="arm64"), \
                mock.patch.object(darwin_build.toolchain, "darwin_sdk", return_value=str(self.sdk)), \
                mock.patch.object(darwin_build.toolchain, "need", side_effect=lambda p: p):
            missing = []
            self.assertIsNone(darwin_build.dependencies(names, missing=missing))
            self.assertTrue(any("LONGSHOT_SDL3" in m for m in missing), missing)
            with mock.patch.dict(os.environ, {"LONGSHOT_SDL3": str(sdl3)}):
                plan = darwin_build.dependencies(names)
                self.assertEqual(plan.aliases, {"libSDL3.dylib": "libSDL3.0.dylib"})
                self.assertEqual(plan.names["libSDL3.0.dylib"], str(sdl3.resolve()))
            sdl.write_bytes(classic)  # a classic SDL2 names no SDL3 and keeps working without one
            self.assertNotIn("libSDL3.0.dylib", darwin_build.dependencies(names).names)

    def test_every_archive_member_must_match_and_have_a_floor(self):
        for target in ("arm64", "x86_64"):
            obj = image(target, kind=1)
            good = self.put("good.a", archive(obj, obj))
            info = package.inspect(good, target)
            self.assertEqual((info["kind"], info["members"]), ("archive", 2))
            for bad in (image(target, "26.0", kind=1), image(target, floor=None, kind=1),
                        image("arm64" if target == "x86_64" else "x86_64", kind=1)):
                with self.assertRaises(package.DependencyError):
                    package.inspect(self.put("bad.a", archive(obj, bad)), target)

    def test_universal_slice_selection_does_not_admit_the_wrong_slice(self):
        arm, x86 = image("arm64", "26.0"), image("x86_64", "12.0")
        header = struct.pack(">II", 0xcafebabe, 2)
        header += struct.pack(">5I", package.CPU["arm64"], 0, 48, len(arm), 0)
        header += struct.pack(">5I", package.CPU["x86_64"], 3, 48 + len(arm), len(x86), 0)
        path = self.put("universal.dylib", header + arm + x86)
        self.assertEqual(package.inspect(path, "x86_64")["minimum_os"], "12.0.0")
        with self.assertRaisesRegex(package.DependencyError, "newer than target"):
            package.inspect(path, "arm64")

    def test_link_search_pins_real_files_and_rejects_dynamic_lookup_arguments(self):
        first = self.put("prefix one, sdk/lib/libcodec.a", archive(image(kind=1)))
        self.put("prefix two/lib/libcodec.dylib", image())
        stub = self.sdk / "usr/lib/libm.tbd"
        stub.parent.mkdir(parents=True)
        stub.write_text("authored system SDK stub sentinel\n")
        plan = package.Plan("arm64", self.sdk)
        flags, roots = plan.pin_link_flags(["-L" + str(first.parent), "-L", str(self.root / "prefix two/lib"), "-lcodec", "-lm"])
        self.assertEqual(flags, [str(first), str(stub)])
        self.assertEqual(roots, [str(first)])
        for argument in ("-flat_namespace", "-Wl,-undefined,dynamic_lookup", "-lmissing"):
            with self.assertRaises(package.DependencyError):
                plan.pin_link_flags([argument])

    def test_rpath_transitive_closure_preserves_inputs_and_rejects_collisions(self):
        leaf = self.put("deps/leaf/libsecond.dylib", image(install_id="@rpath/libsecond.dylib"))
        root = self.put("deps/root/libfirst.dylib", image(install_id="@rpath/libfirst.dylib", loads=["@rpath/libsecond.dylib"],
                                                        rpaths=["@loader_path/../leaf"]))
        plan = package.Plan("arm64", self.sdk)
        key = plan.add(root)
        self.assertEqual(plan.files[key]["dependencies"], {"@rpath/libsecond.dylib": str(leaf)})
        self.assertEqual(set(plan.names), {"libfirst.dylib", "libsecond.dylib"})
        plan.unchanged()
        other = self.put("different/libsecond.dylib", image(install_id="@rpath/libsecond.dylib"))
        with self.assertRaisesRegex(package.DependencyError, "colliding"):
            plan.add(other)
        leaf.write_bytes(image(floor="12.0", install_id="@rpath/libsecond.dylib"))
        with self.assertRaisesRegex(package.DependencyError, "changed after admission"):
            plan.unchanged()

    def test_unknown_or_ambiguous_transitive_dependency_refuses(self):
        root = self.put("root.dylib", image(loads=["@rpath/libleaf.dylib"]))
        plan = package.Plan("arm64", self.sdk)
        with self.assertRaisesRegex(package.DependencyError, "unresolved"):
            plan.add(root)
        for d in ("one", "two"):
            self.put(d + "/libleaf.dylib", image(install_id="@rpath/libleaf.dylib"))
        plan = package.Plan("arm64", self.sdk)
        plan.search = [str(self.root / d) for d in ("one", "two")]
        with self.assertRaisesRegex(package.DependencyError, "ambiguous"):
            plan.add(root)

    def test_preflight_uses_actual_dylibs_and_all_codec_headers(self):
        names = ("avcodec", "swscale", "swresample", "avutil")
        lib = self.root / "approved prefix/lib"
        inc = lib.parent / "include"
        for name in names:
            self.put("approved prefix/lib/lib%s.dylib" % name, image(install_id="@rpath/lib%s.dylib" % name))
            self.put("approved prefix/include/lib%s/%s.h" % (name, name), b"/* authored codec API */\n")
        sdl = self.put("SDL2/libSDL2.dylib", image(install_id="@rpath/libSDL2.dylib"))
        molten = self.put("Vulkan/libMoltenVK.dylib", image(install_id="@rpath/libMoltenVK.dylib"))
        with mock.patch.object(darwin_build, "ffmpeg_flags", return_value=(["-I" + str(inc)], ["-L" + str(lib)] + ["-l" + n for n in names])), \
                mock.patch.object(darwin_build, "sdl_flags", return_value=([], [str(sdl)])), \
                mock.patch.dict(os.environ, {"LONGSHOT_MOLTENVK": str(molten), "LONGSHOT_SDL2": str(sdl), "LONGSHOT_SDL3": ""}), \
                mock.patch.object(darwin_build.toolchain, "architecture", return_value="arm64"), \
                mock.patch.object(darwin_build.toolchain, "darwin_sdk", return_value=str(self.sdk)), \
                mock.patch.object(darwin_build.toolchain, "need", side_effect=lambda p: p):
            plan = darwin_build.dependencies(names)
            self.assertIn("libSDL2-2.0.0.dylib", plan.names)
            self.assertEqual(set(plan.ffmpeg[1]), {str(lib / ("lib" + n + ".dylib")) for n in names})
            old = plan.header_key
            (inc / "libavcodec/avcodec.h").write_text("/* changed API with unchanged version macros */\n")
            self.assertNotEqual(darwin_build.ffmpeg_headers_key(["-I" + str(inc)], names), old)
            sdl.write_bytes(image(install_id="@rpath/libSDL2.dylib", rpaths=["@loader_path/../sdl3/lib"]))
            missing = []
            self.assertIsNone(darwin_build.dependencies(names, missing=missing))
            self.assertTrue(any("LONGSHOT_SDL3" in m for m in missing), missing)
            sdl3 = self.put("SDL3/libSDL3.0.dylib", image(install_id="@rpath/libSDL3.0.dylib"))
            with mock.patch.dict(os.environ, {"LONGSHOT_SDL3": str(sdl3)}):
                compat = darwin_build.dependencies(names)
                self.assertEqual(compat.aliases, {"libSDL3.dylib": "libSDL3.0.dylib"})
            sdl.write_bytes(image(install_id="@rpath/libSDL2.dylib"))
            (lib / "libavcodec.dylib").write_bytes(image(floor="26.0"))
            missing = []
            self.assertIsNone(darwin_build.dependencies(names, missing=missing))
            self.assertTrue(any("newer than target 14.4" in m for m in missing), missing)
            with mock.patch.object(darwin_build.toolchain, "architecture", return_value="x86_64"):
                missing = []
                self.assertIsNone(darwin_build.dependencies(names, missing=missing))
                self.assertTrue(any("x86_64" in m for m in missing), missing)

    def test_staging_failure_never_rewrites_an_input(self):
        out = self.root / "out"
        out.mkdir()
        exe = self.put("out/longshot", image(kind=2))
        renderer = self.put("out/longshot_vk.dylib", image(install_id="@rpath/longshot_vk.dylib"))
        original = {p: package.digest(p) for p in (exe, renderer)}
        with mock.patch.object(package, "_run", side_effect=package.DependencyError("authored signing failure")):
            with self.assertRaisesRegex(package.DependencyError, "signing failure"):
                package.package(out, package.Plan("arm64", self.sdk, out))
        self.assertEqual({p: package.digest(p) for p in original}, original)
        self.assertFalse((out / package.MANIFEST).exists())
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["longshot", "longshot_vk.dylib"])


class ArchiveFramingContracts(PackageFixture):
    """ar framing is validated before any member is interpreted; a member's object is its own load-command extent."""

    def test_negative_or_out_of_container_member_sizes_refuse_before_special_members(self):
        valid = archive(image("x86_64", kind=1))
        real_read, reads = package._read, [0]

        def bounded(*args):  # a reader that never advances must fail here, not hang the suite
            reads[0] += 1
            if reads[0] > 10000:
                raise AssertionError("archive reader did not terminate")
            return real_read(*args)

        for name in ("/", "//", "#1/12", "__.SYMDEF/", "member.o/"):
            for size in ("-60", "-61", "+8", "1_0", " 8", "0x8", str(len(valid) + 1)):
                header = (name.ljust(16) + "0".ljust(12) + "0".ljust(6) + "0".ljust(6) + "100644".ljust(8) + size.ljust(10) + "`\n").encode()
                path = self.put("sizes.a", valid[:8] + header + b"\n" * 8 + valid[8:])
                reads[0] = 0
                with self.subTest(name=name, size=size), mock.patch.object(package, "_read", side_effect=bounded), \
                        self.assertRaisesRegex(package.DependencyError, "invalid archive member size|extends past its archive"):
                    package.inspect(path, "x86_64")
        name = ("#1/20".ljust(16) + "0".ljust(12) + "0".ljust(6) + "0".ljust(6) + "100644".ljust(8) + "8".ljust(10) + "`\n").encode()
        with self.assertRaisesRegex(package.DependencyError, "name extends past its member"):
            package.inspect(self.put("name.a", valid[:8] + name + b"x" * 8 + valid[8:]), "x86_64")

    def test_apple_ar_newline_padding_is_framing_not_object_bytes(self):
        unstamped, stamped = symbol_object(), symbol_object(floor="14.4")
        for obj in (unstamped, stamped):
            for padding in range(8):
                with self.subTest(stamped=obj is stamped, padding=padding):
                    member = package.raw_metadata(self.put("padded.a", archive(obj + b"\n" * padding)), "x86_64")["archive_members"][0]
                    self.assertEqual(member["sha256"], hashlib.sha256(obj).hexdigest(), "the digest covers only the object")
                    self.assertEqual((member.get("object_bytes"), member.get("padding_bytes")), (len(obj), padding))
        known = with_command(image("x86_64", kind=1), struct.pack("<III", 0x2d, 16, 0) + b"\0" * 4)  # LC_LINKER_OPTION
        self.assertEqual(package.raw_metadata(self.put("known.a", archive(known)), "x86_64")["archive_members"][0].get("object_bytes"),
                         len(known))
        fat = struct.pack(">II", 0xcafebabe, 1) + struct.pack(">5I", package.CPU["x86_64"], 3, 28, len(unstamped), 0) + unstamped
        for label, payload, error in (
                ("eight newlines", unstamped + b"\n" * 8, "not Apple ar newline padding"),
                ("other byte", unstamped + b"\n\0\n", "not Apple ar newline padding"),
                ("space", unstamped + b" ", "not Apple ar newline padding"),
                ("truncated object", unstamped[:-1], "extends past its archive member"),
                ("unknown command", with_command(image("x86_64", kind=1), struct.pack("<II", 0x7f, 8)), "cannot bound"),
                ("fat member", fat, "not a thin Mach-O object")):
            with self.subTest(label=label), self.assertRaisesRegex(package.DependencyError, error):
                package.raw_metadata(self.put("bad.a", archive(payload)), "x86_64")


class ArchiveQualificationContracts(PackageFixture):
    """Authored receipt/metadata fixtures do not qualify the real dependency builds."""

    def setUp(self):
        super().setUp()
        self.source = self.put("source.asm", b"bits 64\nglobal entry\nentry: ret\nextern provider\n")
        self.evidence = self.put("evidence.txt", b"Authored fixture symbol/ISA mapping; no vendor qualification claim.\n")
        self.tool = self.put("nasm-fixture", b"Authored native-tool identity sentinel; never executed.\n")
        self.object = self.put("member0.o", symbol_object(undefined=["_provider"]))
        provider = symbol_object("_provider", floor="14.4")
        self.lib = self.put("libauthored.a", archive(self.object.read_bytes(), provider))
        facts = package.raw_metadata(self.lib, "x86_64")
        self.assertEqual(facts["archive_members"][0]["entry_symbols"], ["_entry"])
        self.assertEqual(facts["archive_members"][0]["undefined_symbols"], ["_provider"])
        member = {"ordinal": 0, "name": "member0.o", "sha256": package.digest(self.object), "source_id": "source",
                  "include_ids": [], "generator_ids": [],
                  "assembler": {"tool_id": "nasm", "cwd": str(self.root),
                                "argv": [str(self.tool), "-f", "macho64", str(self.source), "-o", str(self.object)],
                                "output": str(self.object)},
                  "undefined_symbols": ["_provider"],
                  "resolutions": [{"symbol": "_provider", "kind": "archive-member", "archive_sha256": package.digest(self.lib),
                                   "member_ordinal": 1, "member_sha256": facts["archive_members"][1]["sha256"]}],
                  "isa": {"baseline": "x86-64-v2", "entries": [{"symbol": "_entry", "classification": "baseline",
                          "features": ["x86-64-v2"], "dispatch_source_ids": [], "evidence_ids": ["evidence"]}]},
                  "qualification_evidence_ids": ["evidence"]}
        self.data = {"schema": archive_receipts.SCHEMA, "target": {"arch": "x86_64", "minimum_macos": "14.4"},
                     "inputs": [{"id": ident, "kind": kind, "path": str(path), "sha256": package.digest(path)}
                                for ident, kind, path in (("source", "source", self.source), ("evidence", "evidence", self.evidence))],
                     "tools": [{"id": "nasm", "path": str(self.tool), "sha256": package.digest(self.tool),
                                "version": "NASM version 3.02 (authored metadata fixture)", "process_arch": "arm64"}],
                     "generators": [], "archives": [{"path": str(self.lib), "sha256": package.digest(self.lib),
                                                        "arch": "x86_64", "members": [member]}]}

    def save(self, data=None):
        data = copy.deepcopy(self.data if data is None else data)
        review = {"schema": archive_receipts.REVIEW_SCHEMA, "target": data["target"], "qualifications": []}
        for lib in data["archives"]:
            for member in lib["members"]:
                review["qualifications"].append({"archive_sha256": lib["sha256"], "ordinal": member["ordinal"],
                                                "member_sha256": member["sha256"],
                                                "qualification_sha256": archive_receipts.qualification_digest(member)})
        path = self.root / "review.json"
        path.write_text(json.dumps(review))
        data["review"] = {"path": str(path), "sha256": package.digest(path)}
        receipt = self.root / "qualification.json"
        receipt.write_text(json.dumps(data))
        return receipt

    def load(self, data=None, arch="x86_64"):
        receipt = self.save(data)
        return archive_receipts.Qualifications(str(receipt), package.digest(receipt), arch)

    def test_only_exact_reviewed_member_set_qualifies_without_changing_bytes(self):
        before = package.digest(self.lib)
        with self.assertRaisesRegex(package.DependencyError, "no declared macOS minimum"):
            package.inspect(self.lib, "x86_64")
        qualifications = self.load()
        accepted = package.inspect(self.lib, "x86_64", qualifications)
        self.assertEqual(accepted["unversioned_qualified"], 1)
        self.assertIsNone(accepted["archive_members"][0]["minimum_os"])
        self.assertIn("receipt-qualified", accepted["deployment_evidence"])
        self.assertEqual(package.digest(self.lib), before)

    def test_missing_member_wrong_ordinal_or_approved_boolean_never_waives(self):
        for label in ("missing", "ordinal", "boolean"):
            changed = copy.deepcopy(self.data)
            if label == "missing":
                changed["archives"][0]["members"] = []
            elif label == "ordinal":
                changed["archives"][0]["members"][0]["ordinal"] = 1
            else:
                changed["archives"][0]["members"][0]["approved"] = True
            with self.subTest(label=label), self.assertRaises(package.DependencyError):
                self.load(changed)

    def test_exact_undefined_list_and_defining_provider_are_required(self):
        for label in ("missing", "provider", "new-api"):
            changed = copy.deepcopy(self.data)
            member = changed["archives"][0]["members"][0]
            if label == "missing":
                member["undefined_symbols"] = []
                member["resolutions"] = []
            elif label == "provider":
                member["resolutions"][0]["member_ordinal"] = 0
                member["resolutions"][0]["member_sha256"] = member["sha256"]
            else:
                member["resolutions"] = [{"symbol": "_provider", "kind": "system", "install_name": "/usr/lib/libSystem.B.dylib",
                                           "introduced_macos": "26.0", "evidence_ids": ["evidence"]}]
            with self.subTest(label=label), self.assertRaises(package.DependencyError):
                self.load(changed)

    def test_isa_entries_cannot_skip_exports_or_call_avx_baseline(self):
        for label in ("no-entry", "avx-baseline", "missing-dispatch"):
            changed = copy.deepcopy(self.data)
            isa = changed["archives"][0]["members"][0]["isa"]
            if label == "no-entry":
                isa["entries"] = []
            else:
                isa["entries"][0]["features"] = ["avx2"]
                if label == "missing-dispatch":
                    isa["entries"][0]["classification"] = "runtime-dispatched"
            with self.subTest(label=label), self.assertRaises(package.DependencyError):
                self.load(changed)
        changed["archives"][0]["members"][0]["isa"]["entries"][0]["dispatch_source_ids"] = ["source"]
        self.load(changed)  # supplied, hash-bound fixture mapping; semantics remain the reviewer's responsibility

    def test_generated_include_requires_generator_identity(self):
        generated = self.put("config.asm", b"%define AUTHORED 1\n")
        changed = copy.deepcopy(self.data)
        changed["inputs"].append({"id": "config", "kind": "generated", "path": str(generated), "sha256": package.digest(generated)})
        member = changed["archives"][0]["members"][0]
        member["include_ids"] = ["config"]
        with self.assertRaisesRegex(package.DependencyError, "no recorded generator"):
            self.load(changed)
        changed["generators"] = [{"id": "config-generator", "tool_id": "nasm", "cwd": str(self.root),
                                  "argv": [str(self.tool), str(self.source)], "input_ids": ["source"], "output_ids": ["config"]}]
        member["generator_ids"] = ["config-generator"]
        self.load(changed)

    def test_source_tool_target_and_review_drift_refuse(self):
        receipt = self.save()
        with self.assertRaisesRegex(package.DependencyError, "hash mismatch"):
            archive_receipts.Qualifications(str(receipt), "0" * 64, "x86_64")
        with self.assertRaisesRegex(package.DependencyError, "target/schema mismatch"):
            archive_receipts.Qualifications(str(receipt), package.digest(receipt), "arm64")
        qualifications = self.load()
        self.evidence.write_text("changed evidence after admission\n")
        with self.assertRaisesRegex(package.DependencyError, "changed after admission"):
            qualifications.unchanged()
        self.evidence.write_text("Authored fixture symbol/ISA mapping; no vendor qualification claim.\n")
        receipt = self.save()
        data = json.loads(receipt.read_text())
        review_path = Path(data["review"]["path"])
        review = json.loads(review_path.read_text())
        review["qualifications"][0]["qualification_sha256"] = "0" * 64
        review_path.write_text(json.dumps(review))
        data["review"]["sha256"] = package.digest(review_path)
        receipt.write_text(json.dumps(data))
        with self.assertRaisesRegex(package.DependencyError, "not bound"):
            archive_receipts.Qualifications(str(receipt), package.digest(receipt), "x86_64")

    def test_nasm_command_must_bind_source_output_and_native_tool(self):
        for label in ("source", "output", "format", "native-tool"):
            changed = copy.deepcopy(self.data)
            command = changed["archives"][0]["members"][0]["assembler"]["argv"]
            if label == "source":
                command[3] = str(self.evidence)
            elif label == "output":
                command[-1] = str(self.source)
            elif label == "format":
                command[2] = "elf64"
            else:
                changed["tools"][0]["process_arch"] = "x86_64"
            with self.subTest(label=label), self.assertRaises(package.DependencyError):
                self.load(changed)

    def test_apple_ar_padded_members_bind_their_original_objects(self):
        # Apple ar pads Mach-O members with newlines inside the member size; the receipt binds NASM's own output.
        padded = self.put("libpadded.a", archive(self.object.read_bytes() + b"\n" * 6,
                                                 symbol_object("_provider", floor="14.4") + b"\n" * 4))
        facts = package.raw_metadata(padded, "x86_64")
        self.assertEqual(facts["archive_members"][0]["sha256"], package.digest(self.object))
        self.assertEqual([m.get("padding_bytes") for m in facts["archive_members"]], [6, 4])
        changed = copy.deepcopy(self.data)
        lib = changed["archives"][0]
        lib.update(path=str(padded), sha256=package.digest(padded))
        lib["members"][0]["resolutions"][0].update(archive_sha256=lib["sha256"], member_sha256=facts["archive_members"][1]["sha256"])
        accepted = package.inspect(padded, "x86_64", self.load(changed))
        self.assertEqual(accepted["unversioned_qualified"], 1)


C_MAIN = r'''#include <dlfcn.h>
#include <stdio.h>
extern int middle(void);
extern int from_archive(void);
int hos_posix_fixture(void) { return 1; }
static int value(const char *path, const char *symbol) {
    void *module = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!module) { fprintf(stderr, "%s\n", dlerror()); return -100; }
    int (*fn)(void) = (int (*)(void))dlsym(module, symbol);
    int result = fn ? fn() : -101;
    dlclose(module);
    return result;
}
int main(void) {
    int ok = middle() == 11 && from_archive() == 5 &&
        value("@rpath/longshot_vk.dylib", "renderer_value") == 12 &&
        value("@executable_path/libSDL2-2.0.0.dylib", "sdl_fixture") == 23 &&
        value("@executable_path/libMoltenVK.dylib", "molten_fixture") == 29;
    void *sdl3 = dlopen("@executable_path/libSDL3.0.dylib", RTLD_NOW | RTLD_LOCAL);
    void *alias = dlopen("@executable_path/libSDL3.dylib", RTLD_NOW | RTLD_LOCAL);
    ok = ok && sdl3 && alias == sdl3;
    if (alias) dlclose(alias);
    if (sdl3) dlclose(sdl3);
    puts(ok ? "RELOCATED_PACKAGE=PASS" : "RELOCATED_PACKAGE=FAIL");
    return ok ? 0 : 1;
}
'''


@unittest.skipUnless(sys.platform == "darwin", "real relocation requires macOS; metadata cases run everywhere")
class RealPackage(unittest.TestCase):
    def test_real_command_routes_and_moved_dependency_closure(self):
        if FIXTURES_OUT:
            root = FIXTURES_OUT
            root.mkdir(parents=True, exist_ok=False)
        else:
            temp = tempfile.TemporaryDirectory(prefix="longshot relocated, ")
            self.addCleanup(temp.cleanup)
            root = Path(temp.name).resolve()
        for arch in commands_fixture.real_architectures(ARCHES):
            with self.subTest(arch=arch):
                self.fixture(root / arch, arch)

    def fixture(self, root, arch):
        root.mkdir()
        vendor, out = root / "vendor, original", root / "build, output"
        vendor.mkdir()
        out.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("RX_", "HOS_", "LONGSHOT_", "DYLD_")) and
               k not in ("EMSDK", "SDKROOT", "LIBRARY_PATH", "CPATH", "CPLUS_INCLUDE_PATH")}
        env.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", RX_ARCH=arch)
        with mock.patch.dict(os.environ, env, clear=True):
            ns, _ = commands_fixture.command_space(ROOT, arch=arch, real=True, work=str(root))
        tc = ns["toolchain"]
        cc, cxx = tc.need(tc.find("clang")), tc.need(tc.find("clang++"))
        records = []
        raw_run = subprocess.run

        def run(command, **kwargs):
            command = list(map(str, command))
            kwargs.setdefault("capture_output", True)
            kwargs.setdefault("text", True)
            kwargs.setdefault("timeout", 60)
            kwargs.setdefault("env", env)
            result = raw_run(command, **kwargs)
            records.append({"command": command, "exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
            (root / "commands.json").write_text(json.dumps(records, indent=2) + "\n")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result

        def compile_c(name, text):
            source = root / (name + ".c")
            source.write_text(text)
            obj = root / (name + ".o")
            run([cc] + ns["host_flags"](str(root / "gen"), str(root / "rt64")) + ["-c", source, "-o", obj])
            return obj

        def dylib(name, obj, extra=()):
            path = vendor / name
            rsp = root / (name + ".rsp")
            rsp.write_text('"%s"\n' % obj)
            run(ns["renderer_link"](cc, cxx, str(path), str(rsp)) + list(extra))
            return path

        leaf = dylib("libleaf.dylib", compile_c("leaf", "int leaf(void) { return 7; }\n"))
        # Absolute ID on the leaf, @rpath on the middle: both must be rewritten in owned copies.
        run(["/usr/bin/install_name_tool", "-id", leaf, leaf])
        middle = dylib("libmiddle.dylib", compile_c("middle", "extern int leaf(void); int middle(void) { return leaf() + 4; }\n"), [str(leaf)])
        sdl = dylib("libauthoredSDL.dylib", compile_c("sdl", "int sdl_fixture(void) { return 23; }\n"))
        sdl3 = dylib("libauthoredSDL3.dylib", compile_c("sdl3", "int sdl3_fixture(void) { return 37; }\n"))
        molten = dylib("libauthoredMolten.dylib", compile_c("molten", "int molten_fixture(void) { return 29; }\n"))
        member = compile_c("archive", "int from_archive(void) { return 5; }\n")
        static = vendor / "libauthored.a"
        run(["/usr/bin/ar", "rcs", static, member])
        plan = package.Plan(arch, tc.darwin_sdk(), out)
        pinned, _ = plan.pin_link_flags(["-L" + str(vendor), "-lmiddle", str(static)])
        plan.add(sdl, "libSDL2-2.0.0.dylib")
        plan.add(sdl3, "libSDL3.0.dylib")
        plan.alias("libSDL3.dylib", "libSDL3.0.dylib")
        plan.add(molten, "libMoltenVK.dylib")
        ns["FFMPEG_MAC"] = ([], pinned)
        renderer = compile_c("renderer", "extern int middle(void); int renderer_value(void) { return middle() + 1; }\n")
        rsp = root / "renderer.rsp"
        rsp.write_text('"%s"\n' % renderer)
        run(ns["renderer_link"](cc, cxx, str(out / "longshot_vk.dylib"), str(rsp)) + [str(middle)])
        main = compile_c("main", C_MAIN)
        rsp = root / "main.rsp"
        rsp.write_text('"%s"\n' % main)
        run(ns["executable_link"](cc, str(out / "longshot"), str(rsp)))
        original = {p: package.digest(p) for p in vendor.iterdir() if p.is_file()}
        with mock.patch.dict(sys.modules, {"toolchain": tc}), mock.patch.object(package.subprocess, "run", side_effect=run):
            manifest = package.package(out, plan)
        self.assertEqual({p: package.digest(p) for p in original}, original)
        self.assertEqual(set(manifest["files"]), {"longshot", "longshot_vk.dylib", "libleaf.dylib", "libmiddle.dylib",
                                                "libSDL2-2.0.0.dylib", "libSDL3.0.dylib", "libSDL3.dylib", "libMoltenVK.dylib"})
        self.assertNotIn("libauthored.a", package.package_files(out))
        self.assertTrue((out / "libSDL3.dylib").is_symlink())
        self.assertEqual(os.readlink(out / "libSDL3.dylib"), "libSDL3.0.dylib")
        for name in manifest["files"]:
            load = run(["/usr/bin/otool", "-l", out / name]).stdout
            self.assertIn("minos 14.4", load)
            self.assertNotIn(str(vendor), load)
        moved = root / "relocated, standalone"
        out.rename(moved)
        vendor.rename(root / "vendor unavailable")
        result = run((["/usr/bin/arch", "-x86_64"] if arch == "x86_64" else []) + [moved / "longshot"], cwd=root)
        self.assertIn("RELOCATED_PACKAGE=PASS", result.stdout)
        self.assertEqual(package.package_files(moved), sorted(manifest["files"]) + [package.MANIFEST])
        print("MACHO_PACKAGE arch=%s relocated=PASS private_closure=PASS original_inputs=unchanged device=not-created" % arch, flush=True)


CXX_BINDINGS = r'''#include <new>
#include <stdexcept>
#include <string>
extern "C" int cxx_entry(int n) {
    std::string text(n > 0 ? n : 1, 'x');
    if (n < 0) throw std::length_error("authored fixture");
    int *value = new int(3);
    int result = (int)text.size() + *value;
    delete value;
    return result;
}
'''


@unittest.skipUnless(sys.platform == "darwin", "real links require macOS; authored binding metadata runs everywhere")
class RealBindingFormats(unittest.TestCase):
    """Real ld output in both binding formats. Lazy and weak binds and C++ weak-definition lookups (-3: dyld's load-order
    search among weak definitions, which ld emits only for weak definitions it resolved) are admitted; a flat
    (dynamic_lookup) import is refused from the binding data even when the symbol table claims a library."""

    def test_chained_fixups_and_opcode_streams_from_real_links(self):
        if FIXTURES_OUT:
            root = FIXTURES_OUT.with_name(FIXTURES_OUT.name + "-bindings")
            root.mkdir(parents=True, exist_ok=False)
        else:
            temp = tempfile.TemporaryDirectory(prefix="longshot bindings, ")
            self.addCleanup(temp.cleanup)
            root = Path(temp.name).resolve()
        for arch in commands_fixture.real_architectures(ARCHES):
            with self.subTest(arch=arch):
                self.fixture(root / arch, arch)

    def fixture(self, root, arch):
        root.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("RX_", "HOS_", "LONGSHOT_", "DYLD_")) and
               k not in ("EMSDK", "SDKROOT", "LIBRARY_PATH", "CPATH", "CPLUS_INCLUDE_PATH")}
        env.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", RX_ARCH=arch)
        with mock.patch.dict(os.environ, env, clear=True):
            ns, _ = commands_fixture.command_space(ROOT, arch=arch, real=True, work=str(root))
        tc = ns["toolchain"]
        cc, cxx = tc.need(tc.find("clang")), tc.need(tc.find("clang++"))
        records = []

        def run(command):
            command = list(map(str, command))
            result = subprocess.run(command, capture_output=True, text=True, timeout=60, env=env)
            records.append({"command": command, "exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
            (root / "commands.json").write_text(json.dumps(records, indent=2) + "\n")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout

        (root / "bindings.cpp").write_text(CXX_BINDINGS)
        run([cxx] + ns["renderer_flags"](str(ROOT / "gpu/vk"), *(str(root / d) for d in ("spirv", "vulkan", "vma", "generated"))) +
            ["-c", root / "bindings.cpp", "-o", root / "bindings.o"])
        (root / "bindings.rsp").write_text('"%s"\n' % (root / "bindings.o"))
        (root / "flat.c").write_text("extern int missing_external(void);\nint exported(void) { return missing_external(); }\n")
        run([cc] + tc.native_target() + ["-O1", "-c", root / "flat.c", "-o", root / "flat.o"])
        for label, flag, command in (("chained", "-Wl,-fixup_chains", "LC_DYLD_CHAINED_FIXUPS"),
                                     ("opcodes", "-Wl,-no_fixup_chains", "LC_DYLD_INFO_ONLY")):
            strict = root / ("strict %s.dylib" % label)
            run(ns["renderer_link"](cc, cxx, str(strict), str(root / "bindings.rsp")) + [flag])
            load = run(["/usr/bin/otool", "-l", strict])
            fixups = run(["/usr/bin/dyld_info", "-fixups", strict])
            self.assertIn(command, load)
            if label == "chained":
                self.assertIn("<weak-def-coalesce>/", fixups)  # library ordinal -3
            else:
                self.assertIn("lazy-bind", fixups)
                self.assertRegex(load, r"weak_bind_size [1-9]")
            package.inspect(strict, arch)
            flat = root / ("flat %s.dylib" % label)
            run([cc] + tc.native_target() + ["-dynamiclib", flag, "-Wl,-undefined,dynamic_lookup", root / "flat.o", "-o", flat])
            self.assertIn("<flat-namespace>/_missing_external", run(["/usr/bin/dyld_info", "-fixups", flat]))
            # Bytes from the real flat link whose symbol table claims libSystem: only the binding data shows the lookup.
            disguised = root / ("disguised flat %s.dylib" % label)
            disguised.write_bytes(symtab_ordinal(flat.read_bytes(), 0xfe, 1))
            with self.assertRaisesRegex(package.DependencyError, "_missing_external, flat lookup"):
                package.inspect(disguised, arch)
            with self.assertRaisesRegex(package.DependencyError, "flat lookup"):
                package.inspect(flat, arch)
        print("MACHO_BINDINGS arch=%s chained=PASS opcodes=PASS weak_coalesce=accepted lazy_weak=accepted flat=rejected "
              "disguised_flat=rejected" % arch, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", action="append", choices=("arm64", "x86_64"))
    parser.add_argument("--fixtures-out", type=Path)
    args, rest = parser.parse_known_args()
    ARCHES, FIXTURES_OUT = args.arch, args.fixtures_out
    unittest.main(argv=[sys.argv[0], *rest])
