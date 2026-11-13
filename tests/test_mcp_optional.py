"""The optional server fails once, helpfully, without site packages; its core stays stdlib-only."""
import ast
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OptionalMcp(unittest.TestCase):
    def test_missing_sdk_has_one_install_line_and_no_protocol(self):
        result = subprocess.run([sys.executable, "-I", "-S", str(ROOT / "scripts" / "longshot_mcp.py")],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.splitlines(), ["Install: python -m pip install -r scripts/mcp_requirements.txt"])

    def test_session_and_general_checks_do_not_import_sdk(self):
        for path in ("scripts/longshot_ctl.py", "tests/test_longshot_ctl.py", "tests/test_docs.py"):
            tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
            imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
            imports += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
            self.assertFalse(any(name and name.split(".")[0] in ("fastmcp", "mcp", "pydantic") for name in imports), path)
        self.assertEqual((ROOT / "scripts" / "mcp_requirements.txt").read_text().splitlines()[-1], "fastmcp==4.1.3")
        self.assertNotIn("fastmcp", (ROOT / "requirements.txt").read_text().lower())

    def test_forgejo_ci_installs_optional_sdk_before_source_checks(self):
        workflow = (ROOT / ".forgejo" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        source_job = workflow.split("\n  python:\n", 1)[1].split("\n  spirv-fixtures:\n", 1)[0]
        install = "python -m pip install --disable-pip-version-check -r scripts/mcp_requirements.txt"
        self.assertEqual(source_job.count(install), 1)
        step = source_job.split("- name: Python package for MCP protocol tests", 1)[1].split("\n      - ", 1)[0]
        self.assertNotIn("if:", step)
        self.assertLess(source_job.index(install), source_job.index("name: Every test"))
        self.assertIn("os: ubuntu-24.04", source_job)
        self.assertIn("os: windows-2022", source_job)
        self.assertFalse((ROOT / ".github" / "workflows" / "source-regressions.yml").exists())


if __name__ == "__main__":
    unittest.main()
