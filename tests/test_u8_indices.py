"""On a card without 8-bit index buffers (RADV before GCN 3) a draw's 8-bit indices are widened to 16 bits, not left out.

python -I -B tests/test_u8_indices.py [--cxx g++]
"""
import argparse
import subprocess
import tempfile
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default="g++")
    a = p.parse_args()
    source = (ROOT / "gpu" / "vk" / "vk_draw.cpp").read_text(encoding="utf-8")
    draw = block(source, "void draw(")
    if "!dev.index_uint8) SKIP_HERE()" in draw:
        raise SystemExit("a draw with 8-bit indices is left out on a card without them")
    widen = draw.index("const bool widen_u8 =")
    if "!dev.index_uint8" not in draw[widen:widen + 200]:
        raise SystemExit("8-bit indices are widened on a card that has them")
    if draw.index("const VkIndexType itype =") < widen or "widen_u8 ? VK_INDEX_TYPE_UINT16" not in draw:
        raise SystemExit("widened indices are bound as 8-bit ones")
    if draw.count("widen_u8 ? u8_indices_widened(d, restart_on)") != 2:
        raise SystemExit("a direct or an indirect indexed draw does not take the widened indices")
    reads = block(source, "uint64_t u8_indices_read(")
    widened = block(source, "BufferRef u8_indices_widened(")
    fixture = f'''#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#define CHECK(x) do {{ if (!(x)) {{ std::fprintf(stderr, "FAIL:%d %s\\n", __LINE__, #x); std::exit(1); }} }} while (0)
typedef struct VkBuffer_T* VkBuffer;
typedef uint64_t VkDeviceAddress;
struct BufferRef {{
    VkBuffer buffer = nullptr;
    uint64_t offset = 0, size = 0;
    VkDeviceAddress address = 0;
    explicit operator bool() const {{ return buffer != nullptr; }}
}};
struct DrawArgs {{
    int prim = 0;
    bool indexed = false;
    int itype = 0;
    int count = 0, first = 0, base_vertex = 0, base_instance = 0, instances = 1;
    uint64_t ibuf = 0;
    uint64_t indirect = 0, count_addr = 0;
    int max = 0, stride = 0;
}};
static uint8_t guest[0x10000];
static int reads_failed_at = -1, reads;
bool memory_read_as_submitted(uint64_t gpu, void* dst, uint64_t n) {{
    if (reads++ == reads_failed_at || gpu + n > sizeof guest) return false;
    std::memcpy(dst, guest + gpu, n);
    return true;
}}
struct Rec {{ VkBuffer stream; uint8_t* mapped; uint64_t used, size; }};
static uint8_t stream_bytes[1 << 20];
static Rec rec{{(VkBuffer)0x1234, stream_bytes, 0, sizeof stream_bytes}};
static Rec* cur = &rec;
uint64_t stream_alloc(uint64_t n, uint64_t align) {{
    const uint64_t at = (cur->used + align - 1) & ~(align - 1);
    if (at + n > cur->size) return UINT64_MAX;
    cur->used = at + n;
    return at;
}}
static int logged;
void logf(const char*, ...) {{ logged++; }}
{reads}
{widened}
static void record(uint64_t at, uint32_t count, uint32_t instances, uint32_t first) {{
    const uint32_t w[5] = {{count, instances, first, 0, 0}};
    std::memcpy(guest + at, w, sizeof w);
}}
int main() {{
    for (int i = 0; i < 256; i++) guest[0x100 + i] = (uint8_t)i;
    DrawArgs d;
    d.indexed = true, d.ibuf = 0x100, d.count = 256;
    BufferRef r = u8_indices_widened(d, false);
    CHECK(r && r.buffer == rec.stream && r.size == 512 && r.offset % 16 == 0);
    const uint16_t* out = (const uint16_t*)(stream_bytes + r.offset);
    for (int i = 0; i < 256; i++) CHECK(out[i] == i);
    CHECK(logged == 1);
    r = u8_indices_widened(d, true);   // (0xFF a restart: the 16-bit one)
    out = (const uint16_t*)(stream_bytes + r.offset);
    CHECK(r && out[254] == 254 && out[255] == 0xFFFF);
    CHECK(logged == 1);
    d.count = 0;   // (a draw of none still binds one index, as the 8-bit bind did)
    r = u8_indices_widened(d, false);
    CHECK(r && r.size == 2);
    d.count = 4, reads = 0, reads_failed_at = 0;
    CHECK(!u8_indices_widened(d, false));
    reads_failed_at = -1;
    // (indirect: as far as the records reach, first index + count, records of no instance left aside; a count buffer limits them)
    d.indirect = 0x400, d.max = 3, d.stride = 20;
    record(0x400, 6, 1, 10);
    record(0x414, 4, 0, 200);
    record(0x428, 8, 2, 30);
    CHECK(u8_indices_read(d) == 38);
    r = u8_indices_widened(d, false);
    CHECK(r && r.size == 76);
    out = (const uint16_t*)(stream_bytes + r.offset);
    CHECK(out[10] == 10 && out[37] == 37);
    d.count_addr = 0x600;
    const uint32_t one = 1;
    std::memcpy(guest + 0x600, &one, 4);
    CHECK(u8_indices_read(d) == 16);
    const uint32_t none = 0;
    std::memcpy(guest + 0x600, &none, 4);
    CHECK(u8_indices_read(d) == 0 && !u8_indices_widened(d, false));
    d.count_addr = 0, d.stride = 32;   // (a record stride wider than the command)
    record(0x400, 5, 1, 2);
    record(0x420, 3, 1, 50);
    d.max = 2;
    CHECK(u8_indices_read(d) == 53);
    d.max = 0;
    CHECK(u8_indices_read(d) == 0);
    d.max = 1, d.stride = 20;
    record(0x400, 0x10, 1, 0xFFFFFF00u);   // (past 16M indices: not made)
    CHECK(u8_indices_read(d) == 0);
    std::printf("PASS: 8-bit indices widen to 16 bits, restarts kept, as far as the draw reads\\n");
}}
'''
    with tempfile.TemporaryDirectory(prefix="u8-indices-") as tmp:
        cpp = Path(tmp) / "fixture.cpp"
        exe = Path(tmp) / "fixture"
        cpp.write_text(fixture, encoding="utf-8")
        subprocess.run([a.cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", str(cpp), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
