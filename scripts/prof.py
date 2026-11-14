"""Offline profile of a thread sampled with HOS_SAMPLE + HOS_SAMPLE_FILE (host/mkw_glue.c, mkw-recomp work/prof.py): each line is a stack of
module+offset frames, leaf first. Windows DLL/EXE frames use their linker maps; Linux ELF frames use nm. Darwin
longshot / longshot_vk.dylib frames use the matching thin Mach-O image's symbol table, relative to its preferred
header address, and c++filt. RX_MAPS=<folder> selects the matching build AND architecture's maps or binaries.

    python -I scripts/prof.py work/runs/<run>/stacks.txt [top] [skip] [only]
    (skip: the first fraction of the samples, e.g. 0.5; only: samples with that function in their stack, e.g.
    bench_replay)

Prints the functions by inclusive share (in the stack at all) and by self share (the leaf), and the leaf modules.
"""
import bisect, collections, itertools, os, re, stat, struct, subprocess, sys
from contextlib import ExitStack
from tempfile import TemporaryFile

BUILD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "work", "sa", "build")


def load_map(path, base):
    syms = []
    pat = re.compile(r"^\s*0001:[0-9a-f]+\s+(\S+)\s+([0-9a-f]{16})\s", re.I)
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = pat.match(line)
            if m:
                syms.append((int(m.group(2), 16) - base, m.group(1)))
    syms.sort()
    return [a for a, _ in syms], [n for _, n in syms]


# The modules whose frames are Longshot's own code (game/runtime and renderer), on all three platforms.
GAME_MODS = ("longshot.exe", "longshot")
VK_MODS = ("longshot_vk.dll", "longshot_vk.so", "longshot_vk.dylib")
RUN_MODS = GAME_MODS + VK_MODS
# A sample whose leaf is outside Longshot is a wait only when a wait is on its stack: libc's memcpy and the driver's own
# vkCmd* work are CPU time of the thread. These names (as the samples are named: Longshot's frames, leaf first) wait.
WAIT_FRAMES = ("SleepConditionVariableSRW", "WaitForSingleObject", "hos_cv_sleep", "condition_variable::wait",
               "hos_sleep_ns", "sleep_ns", "hos_thread_wait", "vc_acquire", "present_out", "vkWaitForFences",
               "vkWaitSemaphores", "wait_value", "vk_timeline_wait", "recording_wait", ":Sleep", ":SleepEx",
               "WaitForMultipleObjects", "WaitOnAddress")
# ELF and Mach-O demanglers retain arguments (including overloads and anonymous namespaces). Classifying
# a wait must accept that complete spelling without shortening the names displayed by the consumers.
_WAIT_CALL = re.compile(r"(?:" + "|".join(re.escape(name) for name in WAIT_FRAMES) + r")(?:\(.*\))?$")
_RUN_FRAME_PREFIXES = tuple(mod + ":" for mod in RUN_MODS) + ("longshot_vk:",)


def waiting(leaf_module, names):
    """whether a sample (its leaf's module, its frames named) waits: outside Longshot, under a wait"""
    if leaf_module in RUN_MODS:
        return False
    for n in names:
        if n.startswith(_RUN_FRAME_PREFIXES):
            return _WAIT_CALL.search(n) is not None
    return True   # (no frame of Longshot's at all: a thread blocked outside it)


def load_elf(path):
    """An ELF's function symbols by their offset in the file's address space, which is the offset from the load address
    that a sample of a position-independent executable or a shared object gives (binutils' nm, demangled)"""
    out = subprocess.run(["nm", "--defined-only", "-C", path], capture_output=True, text=True, encoding="utf-8",
                         errors="replace").stdout
    syms = []
    for line in out.splitlines():
        parts = line.split(" ", 2)
        if len(parts) == 3 and parts[1] in ("t", "T", "w", "W") and len(parts[0]) >= 8:
            # Parentheses also occur in anonymous namespaces and operator names.
            # Keep the complete demangled symbol so distinct hot functions and
            # overloads do not collapse into one profile entry.
            syms.append((int(parts[0], 16), parts[2]))
    syms.sort()
    return [a for a, _ in syms], [n for _, n in syms]


# Public mach-o/loader.h and nlist.h layouts. Fat images are recognized but cannot be selected from a
# module+offset capture alone: guessing the analyzer's architecture would mislabel a Rosetta capture.
_MACHO_MAGICS = (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce",
                 b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca")


def load_macho(path):
    """Thin 64-bit Mach-O function symbols as the sampler's PC - dladdr(...).dli_fbase offsets.

    A Mach-O n_value is a preferred VM address, not an RVA: executables commonly start at 0x100000000,
    while dylibs commonly start at zero. The segment mapping file offset zero contains the Mach header;
    __PAGEZERO has no file bytes and is not that base. Read headers/link-edit only, never the code body.
    """
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size

        def read(at, count):
            if at < 0 or count < 0 or at + count > size:
                raise OSError("truncated Mach-O metadata")
            f.seek(at)
            data = f.read(count)
            if len(data) != count:
                raise OSError("truncated Mach-O metadata")
            return data

        magic = read(0, 4)
        if magic not in _MACHO_MAGICS[:2]:
            raise OSError("need a thin 64-bit Mach-O binary matching the capture's architecture")
        order = "<" if magic == _MACHO_MAGICS[0] else ">"
        _, _, _, kind, commands, command_bytes, _, _ = struct.unpack(order + "8I", read(0, 32))
        if kind not in (2, 6, 8):   # MH_EXECUTE, MH_DYLIB, MH_BUNDLE; not a relocatable object
            raise OSError("Mach-O symbols require a linked executable or library")
        end = 32 + command_bytes
        if end > size or commands * 8 > command_bytes:
            raise OSError("invalid Mach-O load commands")
        at, section, base, table = 32, 0, None, None
        code_sections = {}
        for _ in range(commands):
            command, length = struct.unpack(order + "2I", read(at, 8))
            if length < 8 or at + length > end:
                raise OSError("invalid Mach-O load command size")
            if command == 0x19:   # LC_SEGMENT_64, followed by section_64 records
                if length < 72:
                    raise OSError("truncated Mach-O segment")
                _, _, _, vmaddr, vmbytes, fileoff, filebytes, _, _, sections, flags = struct.unpack(order + "2I16s4Q4I", read(at, 72))
                if 72 + sections * 80 > length:
                    raise OSError("truncated Mach-O sections")
                if fileoff == 0 and filebytes:
                    if base is not None:
                        raise OSError("ambiguous Mach-O header mapping")
                    if filebytes > vmbytes:
                        raise OSError("invalid Mach-O header mapping size")
                    base = vmaddr + (vmbytes - filebytes if flags & 1 else 0)   # SG_HIGHVM maps at the segment's high end
                for i in range(sections):
                    record = read(at + 72 + i * 80, 80)
                    address, length_bytes = struct.unpack_from(order + "2Q", record, 32)
                    flags = struct.unpack_from(order + "I", record, 64)[0]
                    section += 1
                    if flags & 0x80000400:   # S_ATTR_PURE_INSTRUCTIONS | S_ATTR_SOME_INSTRUCTIONS
                        code_sections[section] = (address, address + length_bytes)
            elif command == 2:   # LC_SYMTAB
                if length < 24:
                    raise OSError("truncated Mach-O symbol table command")
                table = struct.unpack(order + "4I", read(at + 8, 16))
            at += length
        if base is None or table is None:
            raise OSError("Mach-O header mapping or symbol table is absent")
        symoff, count, stroff, string_bytes = table
        strings = read(stroff, string_bytes)
        syms = []
        for name_at, flags, section, _, address in struct.iter_unpack(order + "IBBHQ", read(symoff, count * 16)):
            if flags & 0xE0 or flags & 0x0E != 0x0E or section not in code_sections or not name_at:
                continue             # no STAB, undefined, absolute or data symbols
            lo, hi = code_sections[section]
            if not lo <= address < hi or address < base:
                continue
            name_end = strings.find(b"\0", name_at)
            if name_at >= len(strings) or name_end < 0:
                raise OSError("invalid Mach-O symbol name")
            name = strings[name_at:name_end].decode("utf-8", "replace")
            syms.append((address - base, name[1:] if name.startswith("_") else name))
    if any(name.startswith("_Z") for _, name in syms):
        # Strip exactly Mach-O's ABI underscore above; -n preserves Itanium's own _Z prefix. stdin
        # avoids argv-size limits on a full runtime and keeps overloaded/anonymous names intact.
        result = subprocess.run(["c++filt", "-n"], input="\n".join(name for _, name in syms) + "\n",
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
        names = result.stdout.splitlines()
        if result.returncode or len(names) != len(syms):
            raise OSError("c++filt did not return every Mach-O function name")
        syms = [(address, name) for (address, _), name in zip(syms, names)]
    syms.sort()
    return [a for a, _ in syms], [n for _, n in syms]


def load_symbols(folder=None):
    """{module: (addresses, names)} from RX_MAPS (else BUILD): Windows maps, ELF or thin Mach-O binaries.
    Binary magic, not the analyzer's OS or the extensionless executable name, selects the address model."""
    folder = folder or os.environ.get("RX_MAPS", BUILD)
    maps = {}
    for mod in ("longshot_vk.dll", "longshot.exe"):
        mp = os.path.join(folder, mod[:-4] + ".map")
        if os.path.exists(mp):
            symbols = load_map(mp, 0x180000000 if mod.endswith(".dll") else 0x140000000)
            if symbols[0]:   # an Apple/GNU .map is not a Windows module and must not mask the native game in hotlist
                maps[mod] = symbols
    for mod in ("longshot_vk.so", "longshot_vk.dylib", "longshot"):
        b = os.path.join(folder, mod)
        if os.path.isfile(b):
            macho = mod.endswith(".dylib")
            try:
                with open(b, "rb") as f:
                    macho = macho or f.read(4) in _MACHO_MAGICS
                maps[mod] = load_macho(b) if macho else load_elf(b)
            except OSError as error:   # no symbols/tool: keep module+offset, but report a Darwin mismatch
                if macho:
                    print("prof: cannot load Mach-O symbols from %s: %s" % (b, error), file=sys.stderr)
    return maps


def module_name(mod):
    """longshot_vk / longshot, whichever system the sample came from"""
    return mod.split(".")[0]


def demangle(n):
    # ?name@ns@@... -> ns::name (enough to read)
    m = re.match(r"\?([^@]+)@([^@]*)@", n)
    if m and not n.startswith("??"):
        return "%s::%s" % (m.group(2), m.group(1)) if m.group(2) else m.group(1)
    m = re.match(r"\?\?\$?([^@]+)@", n)
    return m.group(1) if m else n


def profile_lines(path, skip):
    """Keep the old line-based slice without holding the capture in memory."""
    with open(path, encoding="utf-8", errors="replace") as f, ExitStack() as files:
        start = 0
        if skip:
            # Windows can report a pipe as seekable, even though it cannot be replayed.
            if stat.S_ISREG(os.fstat(f.fileno()).st_mode) and f.seekable():
                count = sum(1 for _ in f)
            else:
                # A pipe can only be read once. Save its lines on disk to keep memory bounded.
                spool = files.enter_context(TemporaryFile(mode="w+t", encoding="utf-8", newline="\n"))
                count = 0
                for count, line in enumerate(f, 1):
                    spool.write(line)
                f = spool
            start = int(count * skip)
            if start < 0:
                start = max(0, count + start)
            else:
                start = min(start, count)
            f.seek(0)
        yield from itertools.islice(f, start, None)


def main():
    path = sys.argv[1]
    top = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    skip = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    only = sys.argv[4] if len(sys.argv) > 4 else None
    maps = load_symbols()

    def name(frame):
        mod, _, off = frame.rpartition("+")
        if mod in maps:
            addrs, names = maps[mod]
            i = bisect.bisect_right(addrs, int(off, 16)) - 1
            if i >= 0:
                return module_name(mod) + ":" + demangle(names[i])
        return mod

    incl, self_, leafmod = collections.Counter(), collections.Counter(), collections.Counter()
    n = 0
    for line in profile_lines(path, skip):
        frames = line.strip().split(";")
        if not frames or not frames[0]:
            continue
        named = [name(f) for f in frames]
        if only and not any(only in f for f in named):
            continue
        n += 1
        self_[named[0]] += 1
        leafmod[frames[0].rpartition("+")[0]] += 1
        for f in set(named):
            incl[f] += 1
    print("%d samples" % n)
    print("-- leaf modules")
    for k, v in leafmod.most_common(10):
        print("%6.1f%%  %s" % (100.0 * v / n, k))
    print("-- inclusive")
    for k, v in incl.most_common(top):
        print("%6.1f%%  %s" % (100.0 * v / n, k))
    print("-- self")
    for k, v in self_.most_common(top):
        print("%6.1f%%  %s" % (100.0 * v / n, k))


if __name__ == "__main__":
    main()
