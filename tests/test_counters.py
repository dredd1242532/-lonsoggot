"""HOS_COUNTERS=<seconds> (gpu/vk/vk_replay.cpp counters_tick, the dev-loop proposal's E3): every so many seconds one JSON
line of the memory and cache counters, the runtime's among them (MkwVkHost.host_counters, host/rx_backend.c).

counters_tick is cut out and run against stub counters through a clock: unset, it writes nothing and makes no file; set,
its first line names the format, the build and the interval, and then one line per interval (not per present), each a
JSON object with the runtime's members in it. host_counters is cut out and run: its members, wrapped in braces, are
JSON. scripts/counters.py reads such a file: a table, two lines' difference, the levels that only grew (not the
counters "so far"), and refuses a file of another format. In the source: status_tick calls it, MkwVkHost has the member
and MKWVK_API_VERSION is 22, and the runtime's table gives host_counters last. No game or GPU is needed.

    python -I -B tests/test_counters.py [--cxx clang++]
"""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import counters  # noqa: E402


def function(text, marker):
    start = text.index(marker)
    assert text.count(marker) == 1, marker
    end = text.index("\n}\n", start) + 3
    return text[start:end]


TICK = r"""
#include <atomic>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#include "vk_api.h"
typedef unsigned long DWORD;
#define MAX_PATH 260
static DWORD game_module_path(char* buf, DWORD size) { return (DWORD)snprintf(buf, size, "%s", getenv("GAME_EXE")); }
static const char* build_version() { return "v9.9.9 (source test)"; }
static void logf(const char*, ...) {}
static int host_part(char* out, int cap) { return snprintf(out, cap, "\"heap_mb\":64.0,\"private_mb\":900.5"); }
static MkwVkHost host_table;
static const MkwVkHost* host = &host_table;
static int64_t presents;
static std::atomic<int64_t> st_cache_hits{5};
static bool vram_usage(uint64_t* u, uint64_t* b, uint64_t* = nullptr) { *u = 2ull << 30, *b = 8ull << 30; return true; }
static bool system_heap_usage(uint64_t* u, uint64_t* b) { *u = 1ull << 30, *b = 16ull << 30; return true; }
static void memory_stats(int64_t* m, uint64_t* mb, int64_t* s, int64_t* c, int64_t* w) { *m = 3, *mb = 96, *s = presents << 20, *c = 1, *w = 0; }
static void draw_stats(int64_t* d, int64_t* sk, int64_t* di, int64_t* pl) { *d = presents * 2000, *sk = *di = 0, *pl = 700; }
static size_t texture_count() { return 1234; }
static uint64_t texture_bytes() { return (uint64_t)presents << 20; }
%TICK%
int main(int argc, char**) {
    host_table.host_counters = argc > 1 ? nullptr : host_part;   // (an argument: a runtime that gives none)
    for (int64_t ms = 1000; ms <= 13000; ms += 100) {   // (a present every 100 ms for 12 s)
        presents++;
        counters_tick(ms * 1000000);
    }
    return 0;
}
"""

HOST = r"""
#include <stdint.h>
#include <stdio.h>
uint64_t hos_heap_size = 512ull << 20, hos_heap_used = 300ull << 20;
static int hos_mem_regions(void) { return 42; }
static void hos_process_memory(uint64_t *p, uint64_t *w) { *p = 1500ull << 20, *w = 1200ull << 20; }
%HOST%
int main(void) {
    char out[512];
    if (host_counters(out, sizeof out) <= 0) return 1;
    printf("{%s}\n", out);
    char small[16];
    printf("%d\n", host_counters(small, sizeof small));   /* (too small: 0, nothing half written counted) */
    return 0;
}
"""


def run_tick(cxx, tmp):
    src = (ROOT / "gpu" / "vk" / "vk_replay.cpp").read_text(encoding="utf-8")
    cpp, exe = Path(tmp) / "tick.cpp", Path(tmp) / "tick.exe"
    cpp.write_text(TICK.replace("%TICK%", function(src, "void counters_tick(int64_t now_ns) {")), encoding="utf-8")
    r = subprocess.run([cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-I", str(ROOT / "gpu" / "vk"),
                        str(cpp), "-o", str(exe)], capture_output=True, text=True)
    assert r.returncode == 0, "counters_tick did not compile:\n" + r.stdout + r.stderr
    game = Path(tmp) / "game"
    game.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("HOS_COUNTERS")}
    env["GAME_EXE"] = str(game / "longshot.exe")
    # unset: nothing written, no file made
    subprocess.run([str(exe)], env=env, check=True)
    assert not list(game.iterdir()), list(game.iterdir())
    # every 5 s beside the game: the head, then a line at 0, 5 and 10 s (12 s of presents), not one per present
    subprocess.run([str(exe)], env=dict(env, HOS_COUNTERS="5"), check=True)
    lines = (game / "longshot_counters.jsonl").read_text(encoding="utf-8").splitlines()
    head, rows = json.loads(lines[0]), [json.loads(l) for l in lines[1:]]
    assert head == {"counters": 1, "version": "v9.9.9 (source test)", "every_s": 5}, head
    assert [r["t"] for r in rows] == [0.0, 5.0, 10.0], [r["t"] for r in rows]
    assert rows[0]["heap_mb"] == 64.0 and rows[0]["private_mb"] == 900.5, rows[0]
    assert rows[0]["vram_mb"] == 2048.0 and rows[0]["vram_budget_mb"] == 8192.0 and rows[0]["system_budget_mb"] == 16384.0
    assert rows[0]["textures"] == 1234 and rows[0]["mirrors"] == 3 and rows[0]["mirror_mb"] == 96, rows[0]
    assert rows[0]["pipelines"] == 700 and rows[0]["program_cache_hits"] == 5, rows[0]
    assert rows[1]["presents"] - rows[0]["presents"] == 50 and rows[1]["draws"] - rows[0]["draws"] == 100000, rows
    # its own file (HOS_COUNTERS_FILE), and a runtime that gives no counters: the line is JSON all the same
    own = Path(tmp) / "own.jsonl"
    subprocess.run([str(exe), "nohost"], env=dict(env, HOS_COUNTERS="2.5", HOS_COUNTERS_FILE=str(own)), check=True)
    rows = [json.loads(l) for l in own.read_text(encoding="utf-8").splitlines()[1:]]
    assert len(rows) == 5 and "heap_mb" not in rows[0] and rows[0]["textures"] == 1234, rows
    return game / "longshot_counters.jsonl"


def run_host(cxx, tmp):
    src = (ROOT / "host" / "rx_backend.c").read_text(encoding="utf-8")
    c, exe = Path(tmp) / "host.c", Path(tmp) / "host.exe"
    c.write_text(HOST.replace("%HOST%", function(src, "static int host_counters(char *out, int cap) {")), encoding="utf-8")
    r = subprocess.run([cxx, "-x", "c", "-std=c11", "-Wall", "-Wextra", "-Werror", str(c), "-o", str(exe)], capture_output=True,
                       text=True)
    assert r.returncode == 0, "host_counters did not compile:\n" + r.stdout + r.stderr
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout.splitlines()
    assert json.loads(out[0]) == {"heap_mb": 512.0, "heap_used_mb": 300.0, "regions": 42, "private_mb": 1500.0,
                                  "working_set_mb": 1200.0}, out[0]
    assert out[1] == "0", out


def check_reader(path, tmp):
    head, lines = counters.load(str(path))
    assert head["counters"] == 1 and len(lines) == 3
    assert "texture_mb" in counters.table(lines).splitlines()[0]
    d = counters.diff(lines)
    assert d["presents"][2] == 100 and d["texture_mb"][2] == 100.0, d
    # (texture_mb rose at each line; presents and draws, counters so far, are not levels)
    assert set(counters.grew(lines)) == {"texture_mb"}, counters.grew(lines)
    other = Path(tmp) / "other.jsonl"
    other.write_text('{"t": 0}\n', encoding="utf-8")
    try:
        counters.load(str(other))
    except SystemExit as e:
        assert "not a HOS_COUNTERS file" in str(e)
    else:
        raise AssertionError("a file of another format was read")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert counters.main([str(path), "--grew"]) == 0
    assert "texture_mb" in out.getvalue()


def check_source():
    replay = (ROOT / "gpu" / "vk" / "vk_replay.cpp").read_text(encoding="utf-8")
    assert "counters_tick(now_ns);" in function(replay, "void status_tick() {")
    api = (ROOT / "gpu" / "vk" / "vk_api.h").read_text(encoding="utf-8")
    host_struct = api[api.index("typedef struct MkwVkHost {"):api.index("} MkwVkHost;")]
    assert "int (*host_counters)(char *out, int cap);" in host_struct.rstrip().splitlines()[-1]
    assert "#define MKWVK_API_VERSION 22" in api
    backend = (ROOT / "host" / "rx_backend.c").read_text(encoding="utf-8")
    table = backend[backend.index("static const MkwVkHost vk_host = {"):]
    assert table[:table.index("};")].rstrip().endswith("host_counters,"), "host_counters is not the table's last entry"


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--cxx", default=shutil.which("clang++") or shutil.which("g++") or "c++")
    a = p.parse_args()
    check_source()
    with tempfile.TemporaryDirectory(prefix="counters-") as tmp:
        run_host(a.cxx, tmp)
        path = run_tick(a.cxx, tmp)
        check_reader(path, tmp)
    print("HOS_COUNTERS: nothing unset; a line per interval, JSON, the runtime's part in it; counters.py reads it")


if __name__ == "__main__":
    main()
