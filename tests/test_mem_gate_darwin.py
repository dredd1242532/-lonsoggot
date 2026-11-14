"""Darwin memory layouts, page accounting and process trees from canned input on every OS.

No Apple library is loaded: the live macOS path is covered by test_mem_gate.py and
test_mem_gate_children.py. --source selects a build.py for the dispatch regression.
"""
import argparse
import ast
import ctypes
import importlib
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
from typing import Any
import unittest
from unittest import mock

RUN_ALL_ARGS = []
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "nx2/build.py"
sys.path.insert(0, str(ROOT / "nx2"))
memory = importlib.import_module("darwin_memory")


def actual_build(source):
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {"free_mb", "rss_mb", "tree_rss"}
    nodes: list[ast.stmt] = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes if isinstance(node, ast.FunctionDef)} == names
    scope: dict[str, Any] = dict(sys=SimpleNamespace(platform="darwin"), os=SimpleNamespace(name="posix"),
                                 open=mock.Mock(side_effect=OSError("no /proc on macOS")))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), scope)
    return scope


class DarwinMemoryTests(unittest.TestCase):
    def test_unsigned_32_and_64_bit_counter_layouts(self):
        for size, value in ((4, 0xffffffff), (8, (1 << 40) + 12345)):
            with self.subTest(size=size):
                data = value.to_bytes(size, "little")
                self.assertEqual(memory.decode_uint(data, size), value)
                self.assertEqual(memory.decode_uint(data + b"\xff" * 8, size), value)
        for size in (0, 1, 2, 3, 5, 7, 9):
            with self.subTest(size=size), self.assertRaises(ValueError):
                memory.decode_uint(b"\0" * 16, size)
        with self.assertRaises(ValueError):
            memory.decode_uint(b"\0" * 7, 8)

    def sysctl_fixture(self, size, value, result=0):
        def read(name, buffer, length, new_value, new_length):
            self.assertEqual(name, b"vm.page_free_count")
            self.assertIsNone(new_value)
            self.assertEqual(new_length, 0)   # Read-only: never write a kernel setting.
            n = ctypes.cast(length, ctypes.POINTER(ctypes.c_size_t))
            self.assertEqual(n.contents.value, 8)
            ctypes.memmove(buffer, value.to_bytes(8, "little"), 8)
            n.contents.value = size
            return result
        return SimpleNamespace(sysctlbyname=read)

    def test_sysctl_uses_returned_size_not_buffer_capacity(self):
        for size, value, expected in ((4, 0xaabbccdd12345678, 0x12345678), (8, (1 << 40) + 12345, (1 << 40) + 12345)):
            with self.subTest(size=size):
                with mock.patch.object(memory, "_sysctl", return_value=self.sysctl_fixture(size, value)):
                    self.assertEqual(memory.sysctl_uint(b"vm.page_free_count"), expected)

    def test_failed_or_short_sysctl_is_not_available_memory(self):
        for size, result, error in ((8, -1, OSError), (3, 0, ValueError), (9, 0, ValueError)):
            with self.subTest(size=size, result=result):
                with mock.patch.object(memory, "_sysctl", return_value=self.sysctl_fixture(size, 123, result)):
                    with self.assertRaises(error):
                        memory.sysctl_uint(b"vm.page_free_count")

    def test_available_classes_and_host_page_sizes(self):
        values = {b"vm.page_free_count": 89, b"vm.page_pageable_external_count": 80, b"vm.page_purgeable_count": 7}
        with mock.patch.object(memory, "sysctl_uint", side_effect=values.__getitem__) as read:
            counts = memory.page_counts()
        self.assertEqual(counts, (89, 80, 7))
        self.assertEqual(read.call_args_list, [mock.call(name) for name in values])
        for page_size in (4096, 16384):
            with self.subTest(page_size=page_size):
                # Free excludes speculative; those pages are counted in the file cache, once.
                self.assertEqual(memory.available_bytes(counts, page_size), 176 * page_size)
        # No inactive, anonymous, wired, compressor or swap counter was requested.
        self.assertEqual(memory.available_bytes((0, 0, 0), 16384), 0)
        self.assertEqual(memory.available_bytes((1 << 40, 0, 0), 16384), (1 << 40) * 16384)

    def test_only_the_immutable_page_size_is_cached(self):
        memory._page_size.cache_clear()
        self.addCleanup(memory._page_size.cache_clear)
        with mock.patch.object(memory, "sysctl_uint", return_value=16384) as read:
            self.assertEqual(memory._page_size(), 16384)
            self.assertEqual(memory._page_size(), 16384)
        read.assert_called_once_with(b"hw.pagesize")
        memory._page_size.cache_clear()
        with mock.patch.object(memory, "sysctl_uint", return_value=0):
            with self.assertRaises(ValueError):
                memory._page_size()

    def test_free_mb_uses_fresh_kernel_reading(self):
        with mock.patch.object(memory, "sysctl_uint", side_effect=[128, 0, 0, 64, 0, 0]) as read, \
                mock.patch.object(memory, "_page_size", return_value=16384):
            self.assertEqual(memory.free_mb(), 2)
            self.assertEqual(memory.free_mb(), 1)
        self.assertEqual(read.call_count, 6)

    def test_process_layout_and_exact_size(self):
        self.assertEqual(ctypes.sizeof(memory.ProcessTaskInfo), 96)
        self.assertEqual(memory.ProcessTaskInfo.resident_size.offset, 8)
        rss = (1 << 40) + 12345
        data = struct.pack("<6Q12i", 1 << 44, rss, 3, 4, 5, 6, *range(-6, 6))
        info = memory.ProcessTaskInfo.from_buffer_copy(data)
        self.assertEqual((info.resident_size, info.policy, info.priority), (rss, -6, 5))
        for returned, expected in ((96, rss), (95, 0), (0, 0), (-1, 0)):
            with self.subTest(returned=returned):
                def pidinfo(pid, flavor, arg, buffer, size):
                    self.assertEqual((pid, flavor, arg, size), (123, 4, 0, 96))
                    ctypes.memmove(buffer, data, len(data))
                    return returned
                with mock.patch.object(memory, "_libproc", return_value=SimpleNamespace(proc_pidinfo=pidinfo)):
                    self.assertEqual(memory.process_rss(123), expected)

    def test_child_counts_are_pids_not_bytes(self):
        def children(pid, buffer, size):
            self.assertEqual(pid, 123)
            self.assertEqual(size, 16 * 4)
            buffer[0], buffer[1], buffer[2], buffer[3] = 42, 43, 0, 99
            return 3   # The last slot is outside the returned count.
        with mock.patch.object(memory, "_libproc", return_value=SimpleNamespace(proc_listchildpids=children)):
            self.assertEqual(memory.child_pids(123), [42, 43])

    def test_full_child_buffer_grows(self):
        capacities = []
        children = list(range(100, 133))

        def listing(pid, buffer, size):
            capacity = size // 4
            capacities.append(capacity)
            found = children[:capacity]
            for i, child in enumerate(found):
                buffer[i] = child
            return len(found)
        with mock.patch.object(memory, "_libproc", return_value=SimpleNamespace(proc_listchildpids=listing)):
            self.assertEqual(memory.child_pids(123), children)
        self.assertEqual(capacities, [16, 32, 64])

    def test_no_children_or_failed_enumeration(self):
        for count in (0, -1):
            with self.subTest(count=count):
                lib = SimpleNamespace(proc_listchildpids=mock.Mock(return_value=count))
                with mock.patch.object(memory, "_libproc", return_value=lib):
                    self.assertEqual(memory.child_pids(123), [])

    def test_tree_includes_grandchildren_without_duplicates_or_loops(self):
        resident = {1: 64, 2: 96, 3: 0, 4: 128}   # 3 exited or could not be read.
        children = {1: [2, 3, 2, 0, -1], 2: [4, 1], 3: [4], 4: []}
        with mock.patch.object(memory, "process_rss", side_effect=resident.__getitem__) as rss, \
                mock.patch.object(memory, "child_pids", side_effect=children.__getitem__):
            self.assertEqual(memory.tree_rss(1), 288)
        self.assertCountEqual([call.args[0] for call in rss.call_args_list], [1, 2, 3, 4])

    def test_build_free_memory_dispatch(self):
        scope = actual_build(SOURCE)
        with mock.patch.object(memory, "free_mb", return_value=4321) as read:
            self.assertEqual(scope["free_mb"](), 4321)
        read.assert_called_once_with()
        scope["open"].assert_not_called()
        with mock.patch.object(memory, "free_mb", side_effect=OSError("unreadable snapshot")):
            self.assertIsNone(scope["free_mb"]())

    def test_build_resident_dispatch_and_lifetime(self):
        scope = actual_build(SOURCE)
        proc = SimpleNamespace(pid=123, poll=lambda: None)
        with mock.patch.object(memory, "tree_rss", return_value=(321 << 20) + 15) as read:
            self.assertEqual(scope["rss_mb"](proc), 321)
            self.assertEqual(scope["rss_mb"](None), 0)
            self.assertEqual(scope["rss_mb"](SimpleNamespace(pid=123, poll=lambda: 0)), 0)
        read.assert_called_once_with(123)
        scope["open"].assert_not_called()
        with mock.patch.object(memory, "tree_rss", side_effect=OSError("libproc unavailable")):
            self.assertEqual(scope["rss_mb"](proc), 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    args, remaining = parser.parse_known_args()
    SOURCE = args.source.resolve()
    unittest.main(argv=[__file__, *remaining])
