"""macOS memory snapshots for the compile gate; no subprocess or third-party package.

The layouts are the fixed-width HOST_VM_INFO64 revision 1 prefix in
<mach/vm_statistics.h>/<mach/host_info.h> and PROC_PIDTASKINFO in <sys/proc_info.h>.
They are the same on arm64 and x86_64; newer VM-statistics fields are not requested.
Only the library bindings are cached, never the available memory or process tree.
"""
import ctypes
import functools
import sys


class VMStatistics64(ctypes.LittleEndianStructure):
    _fields_ = [(name, ctypes.c_uint32) for name in ("free_count", "active_count", "inactive_count", "wire_count")] + \
               [(name, ctypes.c_uint64) for name in ("zero_fill_count", "reactivations", "pageins", "pageouts", "faults",
                                                    "cow_faults", "lookups", "hits", "purges")] + \
               [(name, ctypes.c_uint32) for name in ("purgeable_count", "speculative_count")] + \
               [(name, ctypes.c_uint64) for name in ("decompressions", "compressions", "swapins", "swapouts")] + \
               [(name, ctypes.c_uint32) for name in ("compressor_page_count", "throttled_count", "external_page_count",
                                                    "internal_page_count")] + \
               [("total_uncompressed_pages_in_compressor", ctypes.c_uint64)]


class ProcessTaskInfo(ctypes.LittleEndianStructure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("virtual_size", "resident_size", "total_user", "total_system",
                                                    "threads_user", "threads_system")] + \
               [(name, ctypes.c_int32) for name in ("policy", "faults", "pageins", "cow_faults", "messages_sent",
                                                   "messages_received", "syscalls_mach", "syscalls_unix", "csw",
                                                   "threadnum", "numrunning", "priority")]


HOST_VM_INFO64 = 4
HOST_VM_INFO64_REV1_COUNT = ctypes.sizeof(VMStatistics64) // ctypes.sizeof(ctypes.c_int32)
PROC_PIDTASKINFO = 4


@functools.lru_cache(maxsize=1)
def _mach():
    if sys.platform == "darwin":
        lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        lib.mach_host_self.argtypes = []
        lib.mach_host_self.restype = ctypes.c_uint32
        lib.host_page_size.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_size_t)]
        lib.host_page_size.restype = ctypes.c_int
        lib.host_statistics64.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        lib.host_statistics64.restype = ctypes.c_int
        lib.mach_port_deallocate.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        lib.mach_port_deallocate.restype = ctypes.c_int
        return lib, ctypes.c_uint32.in_dll(lib, "mach_task_self_")
    raise OSError("Mach memory statistics require macOS")


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


def vm_statistics():
    """One kernel snapshot and its host page size, in bytes; a failed/short read raises OSError."""
    lib, task = _mach()
    host = lib.mach_host_self()
    try:
        page_size = ctypes.c_size_t()
        if lib.host_page_size(host, ctypes.byref(page_size)) or not page_size.value:
            raise OSError("host_page_size failed")
        stats = VMStatistics64()
        count = ctypes.c_uint32(HOST_VM_INFO64_REV1_COUNT)
        if lib.host_statistics64(host, HOST_VM_INFO64, ctypes.byref(stats), ctypes.byref(count)) or \
                count.value != HOST_VM_INFO64_REV1_COUNT:
            raise OSError("host_statistics64 failed or returned a short snapshot")
        return stats, page_size.value
    finally:
        # mach_host_self acquires a send right: do not leak one on every gate poll.
        lib.mach_port_deallocate(task.value, host)


def available_bytes(stats, page_size):
    """Estimate RAM available without swapping anonymous pages, not an allocation guarantee.

    vm_statistics.h: free_count already includes speculative pages. File-backed
    (external) pages include that speculative cache too, so count it just once.
    mmap(2): purgeable memory is anonymous; it is separate from the file cache.
    Apple's 'Caching and Purgeable Memory': volatile pages can be discarded, not
    paged out. Other anonymous, wired and compressor pages are not available.
    Inactive alone is not reclaimable: it includes anonymous pages needing swap.
    File-backed pages can require writeback/reload; this is an estimate like
    MemAvailable, not its kernel watermark calculation. The gate keeps its reserve.
    """
    pages = stats.free_count + max(0, stats.external_page_count - stats.speculative_count) + stats.purgeable_count
    return pages * page_size


def free_mb():
    stats, page_size = vm_statistics()
    return available_bytes(stats, page_size) >> 20


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
