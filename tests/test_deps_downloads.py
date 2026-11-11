"""scripts/linux/deps.sh: every download goes through its CURL array (https only) and its SHA-256-checked fetch; no bare
curl call is left for a new download to copy.

    python -I -B tests/test_deps_downloads.py
"""
import re
import unittest
from pathlib import Path

RUN_ALL_SKIP = {"windows": "scripts/linux/deps.sh is bash: checked on Linux"}

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / "scripts/linux/deps.sh"


def code_lines(text):
    """the script's lines without comments (a # that starts a line or follows a space)"""
    return [re.sub(r"(^|\s)#.*$", "", line) for line in text.splitlines()]


class DepsDownloads(unittest.TestCase):
    def test_no_curl_but_the_checked_one(self):
        lines = code_lines(DEPS.read_text(encoding="utf-8"))
        bare = [(i + 1, l.strip()) for i, l in enumerate(lines) if re.search(r"(^|[\s;|&(])curl\s", l)
                and not l.strip().startswith("CURL=(curl ")]
        self.assertEqual(bare, [], "curl outside CURL=(...): use fetch (with a SHA-256) or \"${CURL[@]}\"")
        self.assertIn("--proto '=https'", next(l for l in lines if l.strip().startswith("CURL=(curl ")))


if __name__ == "__main__":
    unittest.main()
