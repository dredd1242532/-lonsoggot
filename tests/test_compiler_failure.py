"""A failed compiler with no stderr must remain a failure, not a missing-object link error."""
import argparse
import ast
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'nx2/build.py'


class CompilerFailureTests(unittest.TestCase):
    def test_actual_compile_one_releases_the_gate_when_stdout_is_gone(self):
        source = ast.parse(BUILD.read_text(encoding='utf-8'))
        functions = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == 'compile_one']
        self.assertEqual(len(functions), 1)
        with tempfile.TemporaryDirectory(prefix='longshot-broken-pipe-') as temporary:
            folder = Path(temporary)

            class Gate:
                def __init__(self):
                    self.leases = 0

                def enter(self, src):
                    self.leases += 1
                    return object()

                def started(self, lease, proc):
                    pass

                def leave(self, lease):
                    self.leases -= 1

            gate = Gate()
            env = dict(os=os, subprocess=subprocess, time=time, threading=threading, GATE=gate, PLANNED={},
                       OBJ_CACHE=str(folder / 'cache'), no_throttle=lambda handle: None,
                       object_key=lambda src, flags, deps: 'authored-key')
            exec(compile(ast.Module(body=functions, type_ignores=[]), str(BUILD), 'exec'), env)
            source_file = folder / 'authored.cpp'
            source_file.write_text('/* authored */\n', encoding='ascii')
            job = (sys.executable, str(source_file), str(folder / 'authored.o'), [], b'authored deps')

            class Broken:
                def write(self, text):
                    raise BrokenPipeError()

                def flush(self):
                    pass

            saved = sys.stdout
            raised = False
            sys.stdout = Broken()
            try:
                env['compile_one'](job)
            except BrokenPipeError:
                raised = True
            finally:
                sys.stdout = saved
            self.assertTrue(raised, 'the @@work print must reach the closed stdout')
            self.assertEqual(gate.leases, 0, 'the memory gate lease must be released when stdout is gone')

    def test_actual_compile_one_reports_failures_without_stamping_or_caching(self):
        source = ast.parse(BUILD.read_text(encoding='utf-8'))
        functions = [node for node in source.body if isinstance(node, ast.FunctionDef)
                     and node.name in ('compile_one', 'cache_put')]
        self.assertEqual(len(functions), 2)
        with tempfile.TemporaryDirectory(prefix='longshot-compiler-failure-') as temporary:
            folder = Path(temporary)
            compiler = folder / 'compiler.py'
            compiler.write_text('''import os, signal, sys
from pathlib import Path
mode = sys.argv[1]
if mode == 'success':
    Path(sys.argv[-1]).write_bytes(b'authored object fixture')
    raise SystemExit(0)
if mode == 'stderr':
    print('authored stderr diagnostic', file=sys.stderr)
elif mode == 'stdout':
    print('authored stdout diagnostic')
elif mode == 'killed':
    os.kill(os.getpid(), signal.SIGKILL)
raise SystemExit(7)
''', encoding='utf-8')
            env = dict(os=os, subprocess=subprocess, time=time, threading=threading, GATE=None, PLANNED={},
                       OBJ_CACHE=str(folder / 'cache'), no_throttle=lambda handle: None,
                       object_key=lambda src, flags, deps: hashlib.sha1(repr((src, flags, deps)).encode()).hexdigest())
            exec(compile(ast.Module(body=functions, type_ignores=[]), str(BUILD), 'exec'), env)
            authored = folder / 'authored.c'
            authored.write_text('/* no game code or native compiler needed */\n', encoding='ascii')
            modes = ['quiet', 'stderr', 'stdout'] + (['killed'] if os.name != 'nt' else [])
            for mode in modes:
                with self.subTest(mode=mode):
                    obj = folder / (mode + '.o')
                    job = (sys.executable, str(authored), str(obj), [str(compiler), mode], b'authored deps')
                    for attempt in range(2):  # The build retries failed jobs once, alone.
                        name, elapsed, error = env['compile_one'](job)
                        self.assertEqual(name, str(authored))
                        self.assertGreaterEqual(elapsed, 0)
                        self.assertTrue(error, 'A failed compiler was classified as successful')
                        if mode in ('stdout', 'stderr'):
                            self.assertIn('authored ' + mode + ' diagnostic', error)
                        self.assertFalse(obj.exists())
                        self.assertFalse(Path(str(obj) + '.sha1').exists())
                        self.assertFalse(Path(env['OBJ_CACHE']).exists())
            obj = folder / 'success.o'
            job = (sys.executable, str(authored), str(obj), [str(compiler), 'success'], b'authored deps')
            _, _, error = env['compile_one'](job)
            self.assertIsNone(error)
            self.assertEqual(obj.read_bytes(), b'authored object fixture')
            self.assertTrue(Path(str(obj) + '.sha1').is_file())
            self.assertEqual(len(list(Path(env['OBJ_CACHE']).glob('*.o'))), 1)
            _, elapsed, error = env['compile_one'](job)
            self.assertEqual(elapsed, 0)
            self.assertIsNone(error)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, default=BUILD)
    args, rest = parser.parse_known_args()
    BUILD = args.build
    unittest.main(argv=[sys.argv[0], *rest])
