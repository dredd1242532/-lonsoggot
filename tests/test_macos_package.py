"""Game-free macOS dependency admission and relocated-package behavior.

Synthetic metadata exercises rejection paths on every OS. On macOS, real small
C/C++ Mach-O products use the production command builders and packager. They are
not SDL/Vulkan/game implementations and no device is created. Repeat --arch for
native arm64 and Rosetta; --fixtures-out retains commands, exits and artifacts.
"""
import argparse
import copy
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", action="append", choices=("arm64", "x86_64"))
    parser.add_argument("--fixtures-out", type=Path)
    args, rest = parser.parse_known_args()
    ARCHES, FIXTURES_OUT = args.arch, args.fixtures_out
    unittest.main(argv=[sys.argv[0], *rest])
