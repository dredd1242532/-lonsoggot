"""macOS memory snapshots for the compile gate; no subprocess or third-party package.

sysctl(3) supplies unsigned 32- or 64-bit page counts; PROC_PIDTASKINFO uses
the fixed-width layout in <sys/proc_info.h>, the same on arm64 and x86_64.
Only library bindings and the immutable host page size are cached, never page
counts or the process tree. host_statistics64 can return a one-second-old cache
after a burst of gate reads, so the available-memory reader uses sysctl instead.
"""
import ctypes
import functools
import sys


class ProcessTaskInfo(ctypes.LittleEndianStructure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("virtual_size", "resident_size", "total_user", "total_system",
                                                    "threads_user", "threads_system")] + \
               [(name, ctypes.c_int32) for name in ("policy", "faults", "pageins", "cow_faults", "messages_sent",
                                                   "messages_received", "syscalls_mach", "syscalls_unix", "csw",
                                                   "threadnum", "numrunning", "priority")]


PROC_PIDTASKINFO = 4


@functools.lru_cache(maxsize=1)
def _sysctl():
    if sys.platform == "darwin":
        lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        lib.sysctlbyname.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                                    ctypes.c_void_p, ctypes.c_size_t]
        lib.sysctlbyname.restype = ctypes.c_int
        return lib
    raise OSError("Darwin memory statistics require macOS")


@functools.lru_cache(maxsize=1)
def _libproc():
    if sys.platform == "darwin":
        lib = ctypes.CDLL("/usr/lib/libproc.dylib")
        lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        lib.proc_pidinfo.restype = ctypes.c_int
        lib.proc_listchildpids.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        lib.proc_listchildpids.restype = ctypes.c_int
        return lib
    raise OSError("libproc memory statistics require macOS")


def decode_uint(data, size):
    """Decode a sysctl integer's returned bytes, not its buffer capacity (both Mac ABIs are little-endian)."""
    if size not in (4, 8) or len(data) < size:
        raise ValueError("unexpected sysctl integer size")
    return int.from_bytes(data[:size], "little")


def sysctl_uint(name):
    data = ctypes.create_string_buffer(8)
    size = ctypes.c_size_t(ctypes.sizeof(data))
    if _sysctl().sysctlbyname(name, data, ctypes.byref(size), None, 0):
        raise OSError("sysctlbyname failed: " + name.decode("ascii"))
    return decode_uint(data.raw, size.value)


def page_counts():
    """Free (without speculative), pageable file-backed (including speculative), and volatile purgeable pages."""
    return tuple(sysctl_uint(name) for name in (b"vm.page_free_count", b"vm.page_pageable_external_count",
                                               b"vm.page_purgeable_count"))


@functools.lru_cache(maxsize=1)
def _page_size():
    size = sysctl_uint(b"hw.pagesize")
    if not size:
        raise ValueError("hw.pagesize returned zero")
    return size


def available_bytes(counts, page_size):
    """Estimate RAM available without swapping anonymous pages, not an allocation guarantee.

    vm_statistics.h and Apple's host.c: free_count includes speculative pages;
    vm.page_free_count does not. Pageable file-backed pages include that cache,
    so page_counts counts it once. mmap(2): purgeable memory is anonymous and
    separate from file-backed pages. Apple's 'Caching and Purgeable Memory':
    volatile pages can be discarded, not paged out. Other anonymous, wired and
    compressor pages are not available. Inactive alone includes memory needing swap.
    File-backed pages can require writeback/reload; this is an estimate like
    MemAvailable, not its watermark calculation. The gate keeps its reserve.
    """
    return sum(counts) * page_size


def free_mb():
    return available_bytes(page_counts(), _page_size()) >> 20


def process_rss(pid):
    """Resident bytes of one process, or zero when it exited or cannot be read."""
    info = ProcessTaskInfo()
    size = ctypes.sizeof(info)
    if _libproc().proc_pidinfo(pid, PROC_PIDTASKINFO, 0, ctypes.byref(info), size) != size:
        return 0
    return info.resident_size


def child_pids(pid):
    """Direct children, retrying a full buffer rather than silently dropping children."""
    lib = _libproc()
    capacity = 16
    while True:
        pids = (ctypes.c_int32 * capacity)()
        count = lib.proc_listchildpids(pid, pids, ctypes.sizeof(pids))   # (returns PID count, not byte count)
        if count <= 0:
            return []
        if count < capacity:
            return [p for p in pids[:count] if p > 0]
        capacity *= 2


def tree_rss(pid):
    """Resident bytes of a process and all current descendants, each PID counted once.

    Like the other hosts' readers, this is not an atomic tree snapshot. An exited
    or unreadable process contributes zero; the gate still reserves its unmet need.
    """
    total, seen, todo = 0, set(), [pid]
    while todo:
        current = todo.pop()
        if current <= 0 or current in seen:
            continue
        seen.add(current)
        total += process_rss(current)
        todo.extend(child_pids(current))
    return total
