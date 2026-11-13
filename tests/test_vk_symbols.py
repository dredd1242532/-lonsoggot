"""The ELF renderer guard keeps symbol versions and rejects strong host imports; no game or native tools needed."""
import ast
import contextlib
import io
import os
from pathlib import Path
import subprocess
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "nx2/build.py"

GNU = """Symbol table '.dynsym' contains 4 entries:
   Num:    Value          Size Type    Bind   Vis      Ndx Name
     0: 0000000000000000     0 NOTYPE  LOCAL  DEFAULT  UND
     1: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND abort@GLIBC_2.2.5 (2)
     2: 0000000000000000     0 NOTYPE  WEAK   DEFAULT  UND hos_optional
     3: 0000000000001000    16 FUNC    GLOBAL DEFAULT   12 own_definition
"""
LLVM = """Symbol table '.dynsym' contains 4 entries:
   Num:    Value          Size Type    Bind   Vis       Ndx Name
     0: 0000000000000000     0 NOTYPE  LOCAL  DEFAULT   UND
     1: 0000000000000000     0 FUNC    GLOBAL DEFAULT   UND abort@GLIBC_2.2.5
     2: 0000000000000000     0 NOTYPE  WEAK   DEFAULT   UND hos_optional
     3: 0000000000001000    16 FUNC    GLOBAL DEFAULT    12 own_definition
"""
NM_VERSIONLESS = "                 U abort\n                 w hos_optional\n"
NM_VERSIONED = "                 U abort@GLIBC_2.2.5\n                 w hos_optional\n"
MISSING = "     4: 0000000000000000     0 NOTYPE  GLOBAL DEFAULT  UND missing_host\n"


class VkSymbols(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"), str(SOURCE))
        cls.function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "check_vk_symbols")

    def invoke(self, available, output):
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            result = output[args[0]]
            if isinstance(result, Exception):
                raise result
            return subprocess.CompletedProcess(args, 0, result, "")

        namespace = {"os": os, "shutil": types.SimpleNamespace(which=lambda name: available.get(name)),
                     "subprocess": types.SimpleNamespace(run=run, CalledProcessError=subprocess.CalledProcessError)}
        exec(compile(ast.Module(body=[self.function], type_ignores=[]), str(SOURCE), "exec"), namespace)
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            accepted = namespace["check_vk_symbols"]("fixture.so")
        return accepted, printed.getvalue(), calls

    def test_gnu_metadata_preserves_runtime_version_when_nm_omits_it(self):
        accepted, text, calls = self.invoke({"readelf": "readelf", "nm": "nm"},
                                            {"readelf": GNU, "nm": NM_VERSIONLESS})
        self.assertTrue(accepted, text)
        self.assertEqual(calls, [["readelf", "--dyn-syms", "--wide", "fixture.so"]])

    def test_llvm_metadata_without_gnu_readelf(self):
        accepted, text, calls = self.invoke({"llvm-readelf": "llvm-readelf", "nm": "nm"},
                                            {"llvm-readelf": LLVM, "nm": NM_VERSIONLESS})
        self.assertTrue(accepted, text)
        self.assertEqual(calls[0][0], "llvm-readelf")
        self.assertEqual(len(calls), 1)

    def test_metadata_rejects_only_the_unknown_strong_import(self):
        for metadata in (GNU, LLVM):
            with self.subTest(metadata=metadata):
                accepted, text, calls = self.invoke({"readelf": "readelf", "nm": "nm"},
                                                    {"readelf": metadata + MISSING, "nm": NM_VERSIONLESS})
                self.assertFalse(accepted)
                self.assertIn("1 unresolved symbol(s)", text)
                self.assertIn("    missing_host\n", text)
                self.assertNotIn("    abort\n", text)
                self.assertNotIn("    hos_optional\n", text)
                self.assertEqual(len(calls), 1)

    def test_metadata_works_without_nm(self):
        accepted, text, _ = self.invoke({"readelf": "readelf"}, {"readelf": GNU + MISSING})
        self.assertFalse(accepted)
        self.assertIn("missing_host", text)

    def test_weak_import_and_defined_symbol_are_not_unresolved(self):
        metadata = GNU.replace("abort@GLIBC_2.2.5 (2)", "optional_runtime").replace("FUNC    GLOBAL DEFAULT  UND", "FUNC    WEAK   DEFAULT  UND")
        accepted, text, _ = self.invoke({"readelf": "readelf"}, {"readelf": metadata})
        self.assertTrue(accepted, text)

    def test_runtime_versions_keep_the_existing_exemptions(self):
        for version in ("GLIBC_2.2.5", "GLIBCXX_3.4", "CXXABI_1.3", "GCC_3.0"):
            with self.subTest(version=version):
                metadata = GNU.replace("abort@GLIBC_2.2.5", "runtime_import@" + version)
                accepted, text, _ = self.invoke({"readelf": "readelf"}, {"readelf": metadata})
                self.assertTrue(accepted, text)

    def test_empty_dynamic_table_is_valid(self):
        metadata = "     0: 0000000000000000     0 NOTYPE  LOCAL  DEFAULT  UND\n"
        accepted, text, calls = self.invoke({"readelf": "readelf", "nm": "nm"},
                                            {"readelf": metadata, "nm": ""})
        self.assertTrue(accepted, text)
        self.assertEqual(len(calls), 1)

    def test_missing_readelf_falls_back_to_nm(self):
        accepted, text, calls = self.invoke({"nm": "nm"}, {"nm": NM_VERSIONED})
        self.assertTrue(accepted, text)
        self.assertEqual(calls, [["nm", "-D", "--undefined-only", "fixture.so"]])

    def test_llvm_nm_fallback_retains_strong_and_weak_checks(self):
        accepted, text, calls = self.invoke({"llvm-nm": "llvm-nm"},
                                            {"llvm-nm": NM_VERSIONED + "                 U missing_host\n"})
        self.assertFalse(accepted)
        self.assertIn("1 unresolved symbol(s)", text)
        self.assertIn("missing_host", text)
        self.assertNotIn("hos_optional", text)
        self.assertEqual(calls[0][0], "llvm-nm")

    def test_readelf_failure_falls_back_without_accepting_a_strong_import(self):
        for error in (OSError("cannot start"), subprocess.CalledProcessError(1, ["readelf"])):
            with self.subTest(error=error):
                accepted, text, calls = self.invoke({"readelf": "readelf", "nm": "nm"},
                                                    {"readelf": error, "nm": "U missing_host\n"})
                self.assertFalse(accepted)
                self.assertIn("missing_host", text)
                self.assertEqual([c[0] for c in calls], ["readelf", "nm"])

    def test_unrecognized_readelf_output_falls_back(self):
        accepted, text, calls = self.invoke({"readelf": "readelf", "nm": "nm"},
                                            {"readelf": "no usable symbol rows\n", "nm": "U missing_host\n"})
        self.assertFalse(accepted)
        self.assertIn("missing_host", text)
        self.assertEqual([c[0] for c in calls], ["readelf", "nm"])

    def test_no_symbol_tool_remains_optional(self):
        self.assertEqual(self.invoke({}, {}), (True, "", []))

    def test_nm_failure_remains_optional(self):
        for error in (OSError("cannot start"), subprocess.CalledProcessError(1, ["nm"])):
            with self.subTest(error=error):
                accepted, text, _ = self.invoke({"nm": "nm"}, {"nm": error})
                self.assertTrue(accepted, text)

    def test_readelf_failure_without_nm_remains_optional(self):
        accepted, text, _ = self.invoke({"readelf": "readelf"}, {"readelf": OSError("cannot start")})
        self.assertTrue(accepted, text)


if __name__ == "__main__":
    unittest.main()
