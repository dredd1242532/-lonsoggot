"""scripts/dev_setup.py and the code graph's settings, game-free and offline:

- .graphifyignore leaves out every pattern of .gitignore's "never commit the game" section, and dev_setup.py's
  NEVER_GRAPHED is that same list, so the code graph can never take the game's files;
- .gitignore keeps graphify-out/ (each PC's graph) out of commits;
- with a .graphifyignore that lacks one of them, the graphify step stops before it runs anything;
- --check never asks, installs or runs anything; an unknown --skip step is refused.

    python -I -B tests/test_dev_setup.py
"""
import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("dev_setup", os.path.join(ROOT, "scripts", "dev_setup.py"))
dev_setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev_setup)


def lines(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return [l.strip() for l in f]


def game_section():
    """.gitignore's patterns under its "never commit the game" heading, up to the next heading"""
    out, inside = [], False
    for l in lines(".gitignore"):
        if l.startswith("# ----"):
            inside = "never commit the game" in l
        elif inside and l and not l.startswith("#"):
            out.append(l)
    return out


class Settings(unittest.TestCase):
    def test_the_game_is_never_graphed(self):
        game = game_section()
        self.assertGreater(len(game), 10, "found no game section in .gitignore")
        ignored = set(lines(".graphifyignore"))
        self.assertEqual([p for p in game if p not in ignored], [], ".graphifyignore lacks .gitignore's game patterns")
        self.assertEqual(sorted(dev_setup.NEVER_GRAPHED), sorted(game), "NEVER_GRAPHED is not .gitignore's game list")

    def test_the_graph_is_never_committed(self):
        self.assertIn("graphify-out/", lines(".gitignore"))


class Steps(unittest.TestCase):
    def quiet(self, fn, *a):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            r = fn(*a)
        return r, out.getvalue()

    def test_a_short_ignore_file_stops_the_graph(self):
        ran = []
        root, which, run = dev_setup.ROOT, dev_setup.shutil.which, dev_setup.Setup.run
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, ".graphifyignore"), "w", encoding="utf-8") as f:
                f.write("\n".join(p for p in dev_setup.NEVER_GRAPHED if p != "work/") + "\n")
            try:
                dev_setup.ROOT = d
                dev_setup.shutil.which = lambda *a: ran.append(a) or "/x/graphify"
                dev_setup.Setup.run = lambda self, args, **kw: ran.append(args) or True
                s = dev_setup.Setup(check=False, yes=True)
                _, out = self.quiet(dev_setup.graphify_step, s)
            finally:
                dev_setup.ROOT, dev_setup.shutil.which, dev_setup.Setup.run = root, which, run
        self.assertEqual(s.missing, ["graphify"])
        self.assertIn("work/", out)
        self.assertEqual(ran, [], "it looked for or ran graphify with the game's files not left out")

    def test_check_changes_nothing(self):
        import builtins
        asked, saved = [], builtins.input
        builtins.input = lambda *a: asked.append(a) or "y"
        try:
            s = dev_setup.Setup(check=True, yes=False)
            self.assertFalse(s.ask("anything?"))
            s = dev_setup.Setup(check=False, yes=True)
            self.assertTrue(s.ask("anything?"))
        finally:
            builtins.input = saved
        self.assertEqual(asked, [], "--check or --yes asked a question")

    def test_unknown_step(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
            dev_setup.main(["--check", "--skip", "graphfy"])
        self.assertEqual(e.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
