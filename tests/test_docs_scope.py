"""Keep fictional PR-description test data separate from checked project documentation."""
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("checked_docs", Path(__file__).with_name("test_docs.py"))
docs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(docs)


class DocumentationScope(unittest.TestCase):
    def test_only_dedicated_fixture_and_vendor_directories_are_excluded(self):
        names = ["docs/control.md", "tests/guide.md", "tests/pr_description/good.md",
                 "tests/pr_description_extra/guide.md", "third_party/library/README.md"]
        result = SimpleNamespace(stdout=("\0".join(names) + "\0").encode())
        with mock.patch.object(docs.subprocess, "run", return_value=result):
            self.assertEqual(docs.tracked_markdown(), [docs.ROOT / names[i] for i in (0, 1, 3)])

    def test_real_documentation_keeps_missing_link_and_command_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            guide = root / "guide.md"
            guide.write_text("[Missing](missing.md)\npython -I scripts/missing.py\n", encoding="utf-8")
            with mock.patch.object(docs, "ROOT", root):
                self.assertEqual(len(docs.check_links([guide])), 1)
                self.assertEqual(len(docs.check_commands([guide])), 1)

    def test_website_pages_link_by_site_route_and_keep_relative_link_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = root / "website" / "src" / "content" / "docs" / "help.md"
            page.parent.mkdir(parents=True)
            page.write_text("[Install](/install/) [Search](/progress/#search?q=x) [Missing](missing.md)\n",
                            encoding="utf-8")
            guide = root / "guide.md"
            guide.write_text("[Install](/install/)\n", encoding="utf-8")
            with mock.patch.object(docs, "ROOT", root):
                self.assertEqual([problem.split(": ", 1)[1] for problem in docs.check_links([page])],
                                 ["link does not resolve: missing.md"])
                self.assertEqual(len(docs.check_links([guide])), 1)

    def test_links_with_a_scheme_are_not_repository_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            guide = root / "guide.md"
            guide.write_text("![shot](attach:main.png) ![any](attach:<file>) [mail](mailto:a@example.org) "
                             "[site](https://example.org/x.md) [Missing](missing.md)\n", encoding="utf-8")
            with mock.patch.object(docs, "ROOT", root):
                self.assertEqual([problem.split(": ", 1)[1] for problem in docs.check_links([guide])],
                                 ["link does not resolve: missing.md"])


if __name__ == "__main__":
    unittest.main()
