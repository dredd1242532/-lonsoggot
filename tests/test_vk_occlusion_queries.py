"""Real occlusion counts on Vulkan (RX_VK_OCC_QUERIES, gpu/vk/vk_replay.cpp): Ocarina of Time reads its samples-passed
reports back on the CPU and leaves out of its draw lists the objects whose count was 0 (docs/oot/culling.md).

The ring's code is cut out of vk_replay.cpp and run against a fake query pool and guest memory: a report gets the sum of
its draws' counts once they are all done; one whose draw never ran, or that took too many draws, stays as the replay wrote
it ("visible"); a slot reported into again keeps the newer report; reports wait in order until the GPU is past them; the
ring's queries go back to unused once read; a dry pass numbers queries as a worker does but attaches none. The rest is
checked in the source: every draw takes its query before it is recorded, a report closes its run before the workers
leave the case, the limit is set before a parallel replay copies its start state, and the query is begun inside the pass
and ended before the pass can end.

    python -I -B tests/test_vk_occlusion_queries.py [--cxx clang++]
"""
import argparse
from pathlib import Path
import re
import subprocess
import sys
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]
REPLAY = ROOT / "gpu" / "vk" / "vk_replay.cpp"
DRAW = ROOT / "gpu" / "vk" / "vk_draw.cpp"


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def ring_code(text):
    """vk_replay.cpp's ring: from its constants to occ_take, without occ_queries_on (the pool is the fixture's)"""
    start = text.index("constexpr uint64_t kOccPool")
    end = text.index("/// The next recording slot, begun")
    code = text[start:end]
    return code.replace(block(code, "bool occ_queries_on()"), "")


FIXTURE = r'''
#include <algorithm>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <map>
#include <vector>
#define CHECK(ok) do { if (!(ok)) { std::fprintf(stderr, "failed line %d\n", __LINE__); std::exit(1); } } while (0)
typedef struct VkQueryPool_T* VkQueryPool;
typedef int VkResult;
typedef struct VkDevice_T* VkDevice;
#define VK_NULL_HANDLE nullptr
#define VK_SUCCESS 0
#define VK_NOT_READY 1
#define VK_QUERY_RESULT_64_BIT 1
#define VK_QUERY_RESULT_WITH_AVAILABILITY_BIT 4
struct { VkDevice device = nullptr; } dev;
struct NvnState {
    bool render_enabled = true, raster_discard = false;
    uint64_t occ_next = 0, occ_first = 0, occ_limit = 0;
    bool occ_on = false, occ_overflow = false;
};
struct DrawArgs { VkQueryPool occ_pool = VK_NULL_HANDLE; uint32_t occ_query = 0; };
// the GPU: each query's count and whether it is done; the guest's memory: 8-byte words
struct Q { uint64_t count = 0; bool avail = false; };
std::vector<Q> gpu(1 << 16);
std::map<uint64_t, uint64_t> guest;
uint64_t done_value = 0, rec_part_next = 0;
uint64_t completed() { return done_value; }
struct { void gpu_read(uint64_t a, void* d, size_t n) { uint64_t v = guest[a]; std::memcpy(d, &v, n); } } host_obj, *host = &host_obj;
void guest_cpu_write(uint64_t a, const void* s, size_t n) { uint64_t v = 0; std::memcpy(&v, s, n); guest[a] = v; }
void logf(const char*, ...) {}
bool env_on(const char*) { return false; }
bool occ_queries_on() { return true; }
void vkResetQueryPool(VkDevice, VkQueryPool, uint32_t first, uint32_t n) { for (uint32_t i = 0; i < n; i++) gpu[first + i] = {}; }
VkResult vkGetQueryPoolResults(VkDevice, VkQueryPool, uint32_t first, uint32_t n, size_t, void* data, uint64_t stride, int) {
    VkResult r = VK_SUCCESS;
    for (uint32_t i = 0; i < n; i++) {
        uint64_t* w = (uint64_t*)((char*)data + i * stride);
        w[1] = gpu[first + i].avail;
        if (gpu[first + i].avail) w[0] = gpu[first + i].count;
        else r = VK_NOT_READY;
    }
    return r;
}
''' + "%RING%" + r'''
// the replay's side of a run, as vk_replay.cpp's reset and report cases do it
NvnState S;
void reset() {
    if (S.occ_on && S.occ_next > S.occ_first) occ_pending.push_back({0, 0, S.occ_first, S.occ_next - S.occ_first, occ_value, rec_part_next});
    S.occ_on = S.occ_next < S.occ_limit, S.occ_first = S.occ_next, S.occ_overflow = false;
}
std::vector<uint32_t> draws(int n, bool dry = false) {
    std::vector<uint32_t> q;
    for (int i = 0; i < n; i++) {
        DrawArgs da;
        occ_take(S, da, dry);
        if (da.occ_pool) q.push_back(da.occ_query);
    }
    return q;
}
void report(uint64_t addr, uint64_t ts) {
    const bool run = S.occ_on;
    const uint64_t first = S.occ_first, n = run ? S.occ_next - S.occ_first : 0;
    const bool counted = run && !S.occ_overflow;
    if (run) S.occ_on = false, S.occ_overflow = false;
    guest[addr] = 4096, guest[addr + 8] = ts;   // (the replay's "visible", and its timestamp)
    if (n) occ_pending.push_back({counted ? addr : 0, ts, first, n, occ_value, rec_part_next});
}
void ran(const std::vector<uint32_t>& q, std::vector<uint64_t> counts) {
    for (size_t i = 0; i < q.size(); i++) gpu[q[i]] = {counts[i], true};
}
int main() {
    occ_pool = (VkQueryPool)1;
    S.occ_limit = occ_room(S) ? S.occ_next + kOccBuffer : 0;
    CHECK(S.occ_limit == kOccBuffer);
    occ_value = 10;
    // a run of three draws: its count is their sum, once all are done
    reset();
    auto a = draws(3);
    CHECK(a.size() == 3 && a[0] == 0 && a[2] == 2);
    report(0x100, 7);
    occ_collect();
    CHECK(guest[0x100] == 4096 && occ_pending.size() == 1);   // (not run: waits)
    ran(a, {0, 5, 0});
    occ_collect();
    CHECK(guest[0x100] == 5 && occ_pending.empty());
    CHECK(!gpu[0].avail && !gpu[1].avail);   // (the ring's queries reset once read)
    // hidden: every draw counted 0
    reset();
    auto b = draws(2);
    report(0x200, 8);
    ran(b, {0, 0});
    occ_collect();
    CHECK(guest[0x200] == 0);
    // a draw that never ran (its query never begun): once the GPU is past the submission, "visible" stands
    reset();
    auto c = draws(2);
    report(0x300, 9);
    gpu[c[0]] = {0, true};
    occ_collect();
    CHECK(occ_pending.size() == 1);   // (the GPU not past it yet: waits)
    done_value = 10;
    occ_collect();
    CHECK(guest[0x300] == 4096 && occ_pending.empty());
    // the slot reported into again before the count came: the newer report stands
    occ_value = 11;
    reset();
    auto d = draws(1);
    report(0x400, 20);
    guest[0x408] = 21;
    ran(d, {0});
    occ_collect();
    CHECK(guest[0x400] == 4096);
    // reports wait in order: a done one behind an undone one is written only after it
    reset();
    auto e1 = draws(1);
    report(0x500, 30);
    reset();
    auto e2 = draws(1);
    report(0x600, 31);
    ran(e2, {0});
    occ_collect();
    CHECK(guest[0x600] == 4096 && occ_pending.size() == 2);
    ran(e1, {3});
    occ_collect();
    CHECK(guest[0x500] == 3 && guest[0x600] == 0 && occ_pending.empty());
    // more draws than one report counts: it stays "visible", and its queries are still freed
    reset();
    auto f = draws((int)kOccRun + 5);
    CHECK(f.size() == kOccRun && S.occ_overflow);
    report(0x700, 40);
    std::vector<uint64_t> zeros(f.size(), 0);
    ran(f, zeros);
    occ_collect();
    CHECK(guest[0x700] == 4096 && occ_pending.empty() && !gpu[f[0]].avail);
    // a run with no report is freed at the next reset
    reset();
    auto g = draws(2);
    reset();
    CHECK(occ_pending.size() == 1 && occ_pending.front().addr == 0);
    ran(g, {1, 1});
    occ_collect();
    CHECK(occ_pending.empty());
    // a dry pass numbers its draws as a worker does, but attaches no query
    const NvnState before = S;
    CHECK(draws(4, true).empty() && S.occ_next == before.occ_next + 4);
    S = before;
    auto w = draws(4);
    CHECK(w.size() == 4 && w[0] == before.occ_next % kOccPool);
    S = before;
    // no draws counted while off: discarded rasterization, a false condition, or no limit
    S.raster_discard = true;
    CHECK(draws(2).empty() && S.occ_next == before.occ_next);
    S.raster_discard = false;
    S.occ_limit = 0;
    reset();
    CHECK(!S.occ_on && draws(2).empty());
    // the ring: no room once the queries since the oldest unread one come within a command buffer's worth of its size
    occ_pending.push_back({0, 0, 1000, 1, occ_value, rec_part_next});
    NvnState full;
    full.occ_next = 1000 + kOccPool - kOccBuffer + 1;
    CHECK(!occ_room(full));
    full.occ_next = 1000 + kOccPool - kOccBuffer;
    CHECK(occ_room(full));
    occ_pending.clear();
    std::puts("PASS: occlusion counts: summed when done, visible when unknown, overflowed or reported again, in order, the "
              "ring reset once read, the dry pass numbering as the workers do");
}
'''


def check_wiring(replay, draw):
    """What runs inside replay() and draw(), checked in the source"""
    # every draw takes its query before it is recorded (the dry pass too)
    for case in ("case NOP_DRAW: {", "case NOP_DRAW_IDX: {", "case NOP_DRAW_INDIRECT: {", "case NOP_DRAW_MESH: {"):
        body = block(replay, case)
        take = body.index("occ_take(s, da, dry);")
        assert take < body.index("if (s.render_enabled && !s.raster_discard && !dry) {"), case
        assert "DrawArgs da;" not in body[take:], "%s: a second DrawArgs after the query was taken" % case
    # a report closes its run in every mode, before the workers leave the case
    report = block(replay, "case NOP_REPORT_COUNTER: {")
    assert report.index("if (occ_run) s.occ_on = false") < report.index("if (worker) break;")
    assert "occ_pending.push_back" in report
    reset = block(replay, "case NOP_RESET_COUNTER: {")
    assert "s.occ_on = s.occ_next < s.occ_limit;" in reset and "!worker" in reset
    # the limit is set before a parallel replay copies its start state
    handle = block(replay, "const uint8_t* replay_handle(")
    assert handle.index("state.occ_limit =") < handle.index("const NvnState start = state;")
    # the harvest runs as each recording begins
    assert "occ_collect();" in block(replay, "Recording& begin_recording(uint64_t value)")
    # the query: begun after the pass is, and its guard made after each_guard (so it ends before the pass can)
    fn = block(draw, "void draw(VkCommandBuffer cmd, NvnState& s, const DrawArgs& d) {")
    assert fn.index("} each_guard;") < fn.index("begin_pass(cmd, at);") < fn.index("} occ_guard;")
    assert fn.index("} occ_guard;") < fn.index("vkCmdBeginQuery(cmd, d.occ_pool, d.occ_query")
    assert re.search(r"~OccGuard\(\) \{ if \(pool\) vkCmdEndQuery\(cmd, pool, q\); \}", fn)
    # off unless asked for
    assert 'env_on("RX_VK_OCC_QUERIES")' in replay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    replay = REPLAY.read_text(encoding="utf-8")
    draw = DRAW.read_text(encoding="utf-8")
    check_wiring(replay, draw)
    with tempfile.TemporaryDirectory(prefix="occ-queries-") as tmp:
        cpp = Path(tmp) / "fixture.cpp"
        exe = Path(tmp) / "fixture.exe"
        cpp.write_text(FIXTURE.replace("%RING%", ring_code(replay)), encoding="utf-8")
        r = subprocess.run([args.cxx, "-std=c++20", "-Wall", "-Wextra", "-Wno-unused-parameter", "-Werror", str(cpp), "-o",
                            str(exe)], capture_output=True, text=True)
        if r.returncode:
            sys.exit("the fixture did not compile:\n" + r.stdout + r.stderr)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    main()
