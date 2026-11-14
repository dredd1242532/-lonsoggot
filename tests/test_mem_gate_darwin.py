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


def vm_bytes():
    # HOST_VM_INFO64_REV1 is 38 32-bit words on both 64-bit Mac ABIs.
    data = bytearray(152)
    for offset, value in ((0, 100), (4, 20), (8, 30), (12, 40), (88, 7), (92, 11),
                          (128, 50), (132, 60), (136, 80), (140, 90)):
        struct.pack_into("<I", data, offset, value)
    for offset in (*range(16, 88, 8), *range(96, 128, 8), 144):
        struct.pack_into("<Q", data, offset, (1 << 40) + offset)
    return bytes(data)


def actual_build(source):
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {"free_mb", "rss_mb"}
    nodes: list[ast.stmt] = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes if isinstance(node, ast.FunctionDef)} == names
    scope: dict[str, Any] = dict(sys=SimpleNamespace(platform="darwin"), os=SimpleNamespace(name="posix"),
                                 open=mock.Mock(side_effect=OSError("no /proc on macOS")), tree_rss=mock.Mock(return_value=0))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), scope)
    return scope


class DarwinMemoryTests(unittest.TestCase):
    def test_vm_layout_and_64_bit_counters(self):
        self.assertEqual(ctypes.sizeof(memory.VMStatistics64), 152)
        self.assertEqual(ctypes.alignment(memory.VMStatistics64), 8)
        self.assertEqual(memory.HOST_VM_INFO64_REV1_COUNT, 38)
        stats = memory.VMStatistics64.from_buffer_copy(vm_bytes())
        self.assertEqual((stats.free_count, stats.purgeable_count, stats.speculative_count), (100, 7, 11))
        self.assertEqual((stats.external_page_count, stats.internal_page_count), (80, 90))
        self.assertEqual(stats.zero_fill_count, (1 << 40) + 16)
        self.assertEqual(stats.purges, (1 << 40) + 80)
        self.assertEqual(stats.decompressions, (1 << 40) + 96)
        self.assertEqual(stats.total_uncompressed_pages_in_compressor, (1 << 40) + 144)
        with self.assertRaises(ValueError):
            memory.VMStatistics64.from_buffer_copy(vm_bytes()[:-1])

    def test_available_classes_and_host_page_sizes(self):
        stats = memory.VMStatistics64.from_buffer_copy(vm_bytes())
        for page_size in (4096, 16384):
            with self.subTest(page_size=page_size):
                # 100 free (11 speculative included), 80 file-backed (same 11), 7 purgeable.
                self.assertEqual(memory.available_bytes(stats, page_size), 176 * page_size)
        for name in ("active_count", "inactive_count", "wire_count", "internal_page_count",
                     "compressor_page_count", "throttled_count"):
            setattr(stats, name, 0xffffffff)
        self.assertEqual(memory.available_bytes(stats, 16384), 176 * 16384)
        # A transient cross-counter skew must not subtract real free pages.
        stats.external_page_count = 0
        self.assertEqual(memory.available_bytes(stats, 4096), 107 * 4096)

    def test_empty_and_large_available_counts(self):
        stats = memory.VMStatistics64()
        self.assertEqual(memory.available_bytes(stats, 16384), 0)
        stats.free_count = 0xffffffff
        self.assertEqual(memory.available_bytes(stats, 16384), 0xffffffff * 16384)

    def mach_fixture(self, result=0, count=38, page_size=16384, page_result=0):
        def pages(host, output):
            self.assertEqual(host, 23)
            ctypes.cast(output, ctypes.POINTER(ctypes.c_size_t)).contents.value = page_size
            return page_result

        def snapshot(host, flavor, output, out_count):
            self.assertEqual((host, flavor), (23, 4))
            n = ctypes.cast(out_count, ctypes.POINTER(ctypes.c_uint32))
            self.assertEqual(n.contents.value, 38)
            ctypes.memmove(output, vm_bytes(), 152)
            n.contents.value = count
            return result

        return SimpleNamespace(mach_host_self=mock.Mock(return_value=23), host_page_size=pages,
                               host_statistics64=mock.Mock(side_effect=snapshot), mach_port_deallocate=mock.Mock())

    def test_kernel_snapshot_and_port_release(self):
        lib = self.mach_fixture()
        with mock.patch.object(memory, "_mach", return_value=(lib, ctypes.c_uint32(17))):
            stats, pages = memory.vm_statistics()
        self.assertEqual((stats.free_count, pages), (100, 16384))
        lib.mach_port_deallocate.assert_called_once_with(17, 23)

    def test_kernel_failures_release_port(self):
        for kwargs in ({"result": 5}, {"count": 37}, {"page_size": 0}, {"page_result": 5}):
            with self.subTest(**kwargs):
                lib = self.mach_fixture(**kwargs)
                with mock.patch.object(memory, "_mach", return_value=(lib, ctypes.c_uint32(17))):
                    with self.assertRaises(OSError):
                        memory.vm_statistics()
                lib.mach_port_deallocate.assert_called_once_with(17, 23)

    def test_free_mb_uses_fresh_kernel_reading(self):
        first, second = memory.VMStatistics64(), memory.VMStatistics64()
        first.free_count, second.free_count = 128, 64
        with mock.patch.object(memory, "vm_statistics", side_effect=[(first, 16384), (second, 16384)]) as read:
            self.assertEqual(memory.free_mb(), 2)
            self.assertEqual(memory.free_mb(), 1)
        self.assertEqual(read.call_count, 2)

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
        scope["tree_rss"].assert_not_called()
        with mock.patch.object(memory, "tree_rss", side_effect=OSError("libproc unavailable")):
            self.assertEqual(scope["rss_mb"](proc), 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    args, remaining = parser.parse_known_args()
    SOURCE = args.source.resolve()
    unittest.main(argv=[__file__, *remaining])
