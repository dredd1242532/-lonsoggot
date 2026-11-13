"""(linux) The Vulkan back end's TLS segment against the static TLS room the game reserves for it.

longshot_vk.so is dlopen'ed and its thread_locals are reached through TLS descriptors (-mtls-dialect=gnu2). They are a constant
offset only when glibc placed the module's TLS segment in the static TLS block, in the room host/hos_main.c static_tls_room
reserves (HOS_STATIC_TLS_ROOM, through glibc.rtld.optional_static_tls). A segment that has grown past that room is placed in a block
of its own without an error, and every access then takes glibc's dynamic path, slower than the default TLS model: one new
thread_local array would quietly do that. So nx2/build.py reads the linked module's PT_TLS and fails when it doesn't fit.
"""
import re
import struct

PT_TLS = 7
# (kept free in the room for the drivers' own initial-exec TLS, which glibc places in the same optional static TLS: Mesa's, NVIDIA's)
MARGIN = 8192


def pt_tls(path):
    """(p_memsz, p_align) of the file's PT_TLS segment, (0, 1) when it has none; None when it is not a 64-bit little-endian ELF"""
    with open(path, "rb") as f:
        head = f.read(64)
        if len(head) < 64 or head[:4] != b"\x7fELF" or head[4] != 2 or head[5] != 1:
            return None
        phoff, = struct.unpack_from("<Q", head, 32)
        phentsize, phnum = struct.unpack_from("<HH", head, 54)
        f.seek(phoff)
        table = f.read(phentsize * phnum)
    for i in range(phnum):
        if len(table) < (i + 1) * phentsize:
            return None
        p_type, = struct.unpack_from("<I", table, i * phentsize)
        if p_type == PT_TLS:
            memsz, align = struct.unpack_from("<QQ", table, i * phentsize + 40)
            return memsz, max(align, 1)
    return 0, 1


def room(hos_main_c):
    """HOS_STATIC_TLS_ROOM in host/hos_main.c's text, None when it isn't there"""
    m = re.search(r"^#define HOS_STATIC_TLS_ROOM (\d+)\b", hos_main_c, re.M)
    return int(m.group(1)) if m else None


def check(so, room_bytes, margin=MARGIN):
    """(fits, the line to print)"""
    got = pt_tls(so)
    if got is None:
        return True, "%s: not a 64-bit ELF file: its TLS not checked" % so
    if room_bytes is None:
        return False, "%s: host/hos_main.c has no HOS_STATIC_TLS_ROOM to check the back end's TLS against" % so
    memsz, align = got
    need = (memsz + align - 1) // align * align
    if need + margin > room_bytes:
        return False, ("%s: its TLS segment is %d bytes (aligned to %d): with %d kept for the drivers' own, it does not fit the %d bytes of "
                       "static TLS room the game reserves (host/hos_main.c HOS_STATIC_TLS_ROOM), and every thread_local access of the back "
                       "end would take glibc's slow dynamic path. Put a big thread_local's data on the heap (TLS_OBJECT, gpu/vk/vk_draw.cpp) "
                       "or raise HOS_STATIC_TLS_ROOM (it adds to every thread's TLS); RX_VK_TLS=gd builds without TLS descriptors"
                       % (so, memsz, align, margin, room_bytes))
    return True, "%s: TLS segment %d bytes (aligned to %d): fits the %d bytes of static TLS room (%d to spare, %d kept for drivers')" % (
        so, memsz, align, room_bytes, room_bytes - need - margin, margin)
