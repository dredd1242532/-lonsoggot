"""Live switches (gpu/vk/vk_live.h): environment switches the control bridge changes while the game runs, for an A/B in one
session (scripts/longshot_ctl.py ab).

vk_live.h, the present's live_tick (gpu/vk/vk_replay.cpp) and the bridge's side of the menu (live_fill and the env. keys of
menu_setting_set, gpu/vk/vk_menu.cpp) are cut out and run against stub counters: a switch starts as its variable says; an
ask is taken at the next frame, after the time since the last one is logged, its first half second left out; an ask for the
value a switch has changes nothing and still ends that time, as a mark (live.mark) does; one too short is said to be. The
lines are read back with longshot_ctl's own pattern. longshot_ctl.ab runs against a fake bridge: the order of its asks, the
halves it keeps, the medians and the spread, a half the scene changed in. ab --restart runs against fake games: A B then
B A, each launch's value and environment, its second mark counted, each one quit and waited for. The rest is checked in
the source.

    python -I -B tests/test_live_switches.py [--cxx clang++]
"""
import argparse
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import mock

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]
VK = ROOT / "gpu" / "vk"
sys.path.insert(0, str(ROOT / "scripts"))
import longshot_ctl  # noqa: E402


def block(text, marker):
    start = text.index(marker)
    assert text.count(marker) == 1, marker
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


FIXTURE = r"""
#include <algorithm>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>
#include "vk_api.h"
#include "vk_live.h"
static int64_t st_gpu_ns, draws_total;
static void draw_stats(int64_t* d, int64_t* sk, int64_t* di, int64_t* pl) { *d = draws_total, *sk = *di = *pl = 0; }
static void logf(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    printf("\n");
}
%LIVE_TICK%
%LIVE_FILL%
%MARK_FILL%
static int set_live(const char* key, int value, MkwVkSetting* out) {
    %SET_BRANCH%
    %MARK_BRANCH%
    return 0;
}
static int64_t now = 1000000000;   // (a steady clock: never 0)
static void frames(int n, double ms, int draws, double gpu_ms) {
    for (int i = 0; i < n; i++) {
        now += (int64_t)(ms * 1e6), draws_total += draws, st_gpu_ns += (int64_t)(gpu_ms * 1e6);
        live_tick(now, (int64_t)(ms * 1e6));
    }
}
int main(int argc, char**) {
    printf("start %d\n", (int)live_on(LIVE_OCC_QUERIES));
    if (argc > 1) return 0;
    MkwVkSetting s;
    if (set_live("env.RX_VK_NO_SUCH", 1, &s) != 0) return 10;
    live_tick(now, 0);
    frames(200, 10.0, 100, 5.0);                 // (2 s at 100 fps; the first 0.5 s left out)
    if (set_live("env.RX_VK_OCC_QUERIES", 1, &s) != 1) return 11;
    printf("listed %s %s %d %s\n", s.key, s.name, s.value, s.text);
    if (live_on(LIVE_OCC_QUERIES)) return 12;    // (not before the next frame)
    frames(1, 10.0, 100, 5.0);
    printf("after %d\n", (int)live_on(LIVE_OCC_QUERIES));
    frames(100, 20.0, 50, 12.0);                 // (2 s at 50 fps, two slow frames among them)
    frames(2, 100.0, 50, 12.0);
    set_live("env.RX_VK_OCC_QUERIES", 1, &s);    // (the value it has: a mark)
    frames(1, 20.0, 50, 12.0);
    printf("still %d\n", (int)live_on(LIVE_OCC_QUERIES));
    frames(10, 20.0, 50, 12.0);                  // (0.2 s: too short)
    set_live("env.RX_VK_OCC_QUERIES", 0, &s);
    frames(1, 20.0, 50, 12.0);
    printf("end %d\n", (int)live_on(LIVE_OCC_QUERIES));
    frames(60, 10.0, 100, 5.0);
    if (set_live("live.mark", 0, &s) != 1) return 13;   // (a mark: ends the time, changes nothing)
    printf("mark %s %s %d\n", s.key, s.name, s.value);
    frames(1, 10.0, 100, 5.0);
    printf("marked %d\n", (int)live_on(LIVE_OCC_QUERIES));
    return 0;
}
"""


def compile_fixture(cxx, tmp):
    replay = (VK / "vk_replay.cpp").read_text(encoding="utf-8")
    menu = (VK / "vk_menu.cpp").read_text(encoding="utf-8")
    code = (FIXTURE.replace("%LIVE_TICK%", block(replay, "void live_tick(int64_t now_ns, int64_t frame_ns) {"))
            .replace("%LIVE_FILL%", block(menu, "static void live_fill(const LiveSwitch& s, MkwVkSetting* out) {"))
            .replace("%SET_BRANCH%", block(menu, 'if (!strncmp(key, "env.", 4)) {'))
            .replace("%MARK_FILL%", block(menu, "static void live_mark_fill(MkwVkSetting* out) {"))
            .replace("%MARK_BRANCH%", block(menu, 'if (!strcmp(key, "live.mark")) {')))
    cpp, exe = Path(tmp) / "live.cpp", Path(tmp) / "live.exe"
    cpp.write_text(code, encoding="utf-8")
    r = subprocess.run([cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-Wno-format-security", "-I", str(VK), "-I",
                        str(VK / "compat"), str(cpp), "-o", str(exe)], capture_output=True, text=True)
    if r.returncode:
        sys.exit("the fixture did not compile:\n" + r.stdout + r.stderr)
    return exe


def run_fixture(exe, *args, env=None):
    e = {k: v for k, v in os.environ.items() if k != "RX_VK_OCC_QUERIES"}
    e.update(env or {})
    r = subprocess.run([str(exe), *args], capture_output=True, text=True, env=e)
    assert r.returncode == 0, "the fixture stopped with %d:\n%s" % (r.returncode, r.stdout)
    return r.stdout.splitlines()


def check_ticks(exe):
    # (the start value is the variable's, by the rule of longshot_env.h)
    assert run_fixture(exe, "start") == ["start 0"]
    assert run_fixture(exe, "start", env={"RX_VK_OCC_QUERIES": "1"}) == ["start 1"]
    assert run_fixture(exe, "start", env={"RX_VK_OCC_QUERIES": "off"}) == ["start 0"]
    out = run_fixture(exe)
    assert "listed env.RX_VK_OCC_QUERIES RX_VK_OCC_QUERIES 1 on: the GPU's counts in samples-passed reports" in out, out
    assert "after 1" in out and "still 1" in out and "end 0" in out, out
    changes = [l for l in out if " -> " in l]
    assert changes == ["vk: live: RX_VK_OCC_QUERIES 0 -> 1", "vk: live: RX_VK_OCC_QUERIES 1 -> 0"], changes
    halves = longshot_ctl.live_halves(out, "RX_VK_OCC_QUERIES")
    assert len(halves) == 4 and halves[2] is None, halves
    # the mark: listed as the bridge finds it, the time since the last change ended (its first 0.5 s left out: 11 frames of
    # the 61), nothing changed
    assert "mark live.mark live_mark 1" in out and "marked 0" in out, out
    assert halves[3]["value"] == 0 and halves[3]["frames"] == 11 and abs(halves[3]["fps"] - 100.0) < 0.5, halves[3]
    first, second = halves[0], halves[1]
    # 2 s at 100 fps, its first 0.5 s left out: 151 frames in 1.51 s (the frame the ask was taken at counted)
    assert first["value"] == 0 and first["frames"] == 151, first
    assert abs(first["fps"] - 100.0) < 0.5 and abs(first["low"] - 100.0) < 0.5, first
    assert abs(first["gpu_ms"] - 5.0) < 0.01 and first["draws"] == 100, first
    # the second: from the first ask's frame, 0.5 s left out; two 100 ms frames among 20 ms ones: the 1% low is theirs
    assert second["value"] == 1 and second["frames"] == 78, second
    assert abs(second["low"] - 10.0) < 0.1 and abs(second["gpu_ms"] - 12.0) < 0.01 and second["draws"] == 50, second
    assert abs(second["fps"] - 78 / 1.72) < 0.2, second


class FakeBridge:
    """The bridge as ab uses it: each ask logs the time since the last one, with the value it had"""

    def __init__(self, start=0, maps=None):
        self.value, self.asks, self.kept, self.maps, self.statuses = start, [], None, maps or {}, 0

    def request(self, cmd, **args):
        if cmd == "option_get":
            return {"ok": True, "option": {"key": "env.RX_VK_X", "name": "RX_VK_X", "tab": "live", "value": self.value}}
        if cmd == "status":
            self.statuses += 1
            return {"ok": True, "state": "play", "map": self.maps.get(self.statuses, "Market_Child")}
        if cmd == "option_set":
            fps = 60.0 + 2 * self.value + len(self.asks) % 3 * 0.1   # (on: 2 fps more, and some noise)
            self.kept.append({"event": "log", "line": "[hos] vk: live: 590 frames in 9.5 s with RX_VK_X=%d: %.1f fps (1%% low "
                              "%.1f), GPU %.2f ms a frame, %d draws a frame" % (self.value, fps, fps - 10, 8.0 - self.value,
                                                                                 1800 + 100 * self.value)})
            self.asks.append(args["value"])
            self.value = args["value"]
            return {"ok": True}
        if cmd == "subscribe":
            assert args == {"events": ["log", "state"], "log": "vk: live:"}, args
            return {"ok": True}
        raise AssertionError(cmd)

    def line(self, timeout):
        raise AssertionError("ab waited for a line it had")


def run_ab(bridge, rounds=3, every=10):
    a = mock.Mock(name="RX_VK_X", every=every, rounds=rounds, json=True)
    a.name = "RX_VK_X"
    out = io.StringIO()
    with mock.patch.object(longshot_ctl.time, "sleep"), contextlib.redirect_stdout(out):
        assert longshot_ctl.ab(a, bridge) == 0
    return __import__("json").loads(out.getvalue())


def check_ab():
    b = FakeBridge(start=0)
    r = run_ab(b)
    # from the value it has (a mark: the time before is not counted), each way three times, ending on it
    assert b.asks == [0, 1, 0, 1, 0, 1, 0], b.asks
    assert [h["value"] for h in r["halves"]] == [0, 1, 0, 1, 0, 1], r["halves"]
    s = r["summary"]
    assert s["0"]["halves"] == s["1"]["halves"] == 3
    assert abs(s["1"]["fps"] - s["0"]["fps"] - 2.0) < 0.25 and s["1"]["gpu_ms"] == 7.0 and s["0"]["draws"] == 1800, s
    assert all(v["spread"] <= 0.21 for v in s.values()), s
    assert r["flagged"] == [""] * 6, r["flagged"]
    # a start from on goes off first
    b = FakeBridge(start=1)
    run_ab(b, rounds=1)
    assert b.asks == [1, 0, 1], b.asks
    # the map changed during the third half (statuses: one before each ask, one after the last)
    r = run_ab(FakeBridge(maps={5: "Hyrule_Field"}))
    assert r["flagged"][2] == "the map changed" and r["flagged"][3] == "the map changed", r["flagged"]
    # the plain table says whether the difference is more than the halves of one value differ by
    a = mock.Mock(every=10, rounds=2, json=False)
    a.name = "RX_VK_X"
    out = io.StringIO()
    with mock.patch.object(longshot_ctl.time, "sleep"), contextlib.redirect_stdout(out):
        longshot_ctl.ab(a, FakeBridge())
    assert "RX_VK_X=1 against 0: fps +3." in out.getvalue(), out.getvalue()
    # a menu row is no live switch
    b = FakeBridge()
    b.request = lambda cmd, **k: {"ok": True, "option": {"key": "vsync", "name": "V-Sync", "value": 1}}
    try:
        run_ab(b)
    except longshot_ctl.Failed as e:
        assert "not a live switch" in str(e)
    else:
        raise AssertionError("a menu row was flipped")


class FakeGame:
    """ab --restart's games: each launch's switch value from its environment, its bridge logging a line at each mark (the
    first for the time since the start), quit, and the process waited for"""

    def __init__(self, maps=None, loads=(), refuse=()):
        self.launches, self.quits, self.waited, self.maps, self.loads = [], 0, 0, maps or {}, set(loads)
        self.refuse, self.teleports = set(refuse), []

    def start_game(self, ns):
        value = dict(kv.split("=", 1) for kv in ns.env)["RX_VK_Y"]
        self.launches.append((value, ns.env, os.path.basename(ns.run)))
        game = self

        class Process:
            def wait(self, timeout):
                game.waited += 1

        return {"socket": "sock-%d" % len(self.launches)}, Process()

    def bridge(self, sock, timeout):
        game, value, n = self, self.launches[-1][0], len(self.launches)

        class B:
            kept, marks, played = None, 0, 0

            def request(self, cmd, **args):
                if cmd == "status":
                    self.played += 1
                    state = "play" if self.played > 2 else "title"   # (A pressed twice to get into play)
                    return {"ok": True, "state": state, "map": game.maps.get((n, self.played), "Market_Child"),
                            "frame": 100 * self.played}
                if cmd == "option_set":
                    assert args == {"name": "live_mark", "value": 0}, args
                    self.marks += 1
                    if self.marks == 1 and n in game.loads:   # (a load after the first mark: between its statuses' frames)
                        self.kept.append({"event": "state", "frame": 100 * self.played + 50, "state": "loading"})
                    fps = 30.0 if self.marks == 1 else 50.0 + 5 * (value == "1") + n % 2 * 0.5
                    self.kept.append({"event": "log", "line": "vk: live: 300 frames in 9.5 s with RX_VK_OCC_QUERIES=0: %.1f fps "
                                      "(1%% low 40.0), GPU %.2f ms a frame, 2000 draws a frame" % (fps, 9.0 - (value == "1"))})
                    return {"ok": True}
                if cmd == "quit":
                    game.quits += 1
                if cmd == "teleport":
                    game.teleports.append(n)
                    if n in game.refuse and game.teleports.count(n) == 1:   # (an event playing: refused once)
                        return {"ok": False, "error": "refused", "message": "an event is playing"}
                if cmd == "press" and self.played == 1:   # (the intro reads no controller: the bridge's timeout)
                    return {"ok": False, "error": "timeout", "message": "the game did not read the controller"}
                return {"ok": True, "map": "Market_Child"}

            def line(self, timeout):
                raise AssertionError("waited for a line it had")

            def close(self):
                pass

        return B()


def run_restart(game, **over):
    a = argparse.Namespace(name="RX_VK_Y", restart="game", values="0,1", rounds=2, every=10, teleport="3", settle=1,
                           start_timeout=60, headless=True, env=["HOS_AUDIO=0"], save=None, slot=None, json=True, run=None)
    for k, v in over.items():
        setattr(a, k, v)
    out = io.StringIO()
    with mock.patch.object(longshot_ctl, "start_game", game.start_game), mock.patch.object(longshot_ctl, "Bridge", game.bridge), \
            mock.patch.object(longshot_ctl.time, "sleep"), contextlib.redirect_stdout(out), \
            tempfile.TemporaryDirectory() as tmp:
        a.run = tmp
        assert longshot_ctl.ab_restart(a) == 0
    return out.getvalue()


def check_restart():
    game = FakeGame()
    r = __import__("json").loads(run_restart(game))
    # A B, then B A: neither value always second; each launch the switch's value and the extra environment, a run folder each
    assert [v for v, _, _ in game.launches] == ["0", "1", "1", "0"], game.launches
    assert all(env[0] == "HOS_AUDIO=0" for _, env, _ in game.launches)
    assert [d for _, _, d in game.launches] == ["01-0", "02-1", "03-1", "04-0"], game.launches
    assert game.quits == game.waited == 4
    # each launch's second mark is its number (the first: the time since the start, not counted), by the launch's value
    assert [h["value"] for h in r["halves"]] == ["0", "1", "1", "0"] and r["flagged"] == [""] * 4, r
    s = r["summary"]
    assert s["0"]["halves"] == s["1"]["halves"] == 2 and abs(s["1"]["fps"] - s["0"]["fps"] - 5.0) < 0.6, s
    assert s["1"]["gpu_ms"] == 8.0 and s["0"]["gpu_ms"] == 9.0, s
    # the map left during launch 3's measuring (its statuses: three to get into play, then before and after)
    r = __import__("json").loads(run_restart(FakeGame(maps={(3, 4): "Hyrule_Field"})))
    assert r["flagged"] == ["", "", "the map changed", ""], r["flagged"]
    # the game refuses launch 2's first teleport (an event playing): asked again, and the launch measured as the others
    game = FakeGame(refuse={2})
    r = __import__("json").loads(run_restart(game))
    assert game.teleports == [1, 2, 2, 3, 4] and r["flagged"] == [""] * 4, (game.teleports, r["flagged"])
    # a load during launch 3's measuring, at the same map at both ends: flagged, and left out of its value's medians
    r = __import__("json").loads(run_restart(FakeGame(loads={3})))
    assert r["flagged"] == ["", "", "a load or a menu in it", ""], r["flagged"]
    assert r["summary"]["1"]["halves"] == 1 and r["summary"]["0"]["halves"] == 2, r["summary"]
    text = run_restart(FakeGame(loads={3}), json=False)
    assert "(left out: a load or a menu in it)" in text, text
    try:
        run_restart(FakeGame(loads={1, 2, 3, 4}))
    except longshot_ctl.Failed as e:
        assert "every half was flagged" in str(e)
    else:
        raise AssertionError("nothing counted, and a summary all the same")
    # the plain table: launches, and B against A in the order given
    text = run_restart(FakeGame(), json=False, values="1,0")
    assert "launch 1, RX_VK_Y=1: 55." in text and "RX_VK_Y=0 against 1: fps -9." in text and "launches" in text, text
    try:
        run_restart(FakeGame(), values="0")
    except longshot_ctl.Failed as e:
        assert "two values" in str(e)
    else:
        raise AssertionError("one value was taken")


def check_source():
    replay = (VK / "vk_replay.cpp").read_text(encoding="utf-8")
    menu = (VK / "vk_menu.cpp").read_text(encoding="utf-8")
    # taken once a frame, as the present begins
    assert "live_tick(now_ns, last_ns ? now_ns - last_ns : 0);" in block(replay, "void status_tick() {")
    assert "status_tick();" in block(replay, "void do_present(")
    # the bridge's listing has the live switches after the rows; its ask is neither saved nor held up by the menu's lock
    listing = block(menu, "int menu_setting(int index, MkwVkSetting* out) {")
    assert (listing.index("bridge_fill(i, out)") < listing.index("live_fill(live_switches()[index - n], out)")
            < listing.index("live_mark_fill(out)"))
    setter = block(menu, "int menu_setting_set(const char* key, int value, MkwVkSetting* out) {")
    branch = block(setter, 'if (!strncmp(key, "env.", 4)) {')
    mark = block(setter, 'if (!strcmp(key, "live.mark")) {')
    assert setter.index(branch) < setter.index(mark) < setter.index("bridge_probe_first();") < setter.index("std::lock_guard lk(menu_mu);")
    assert "save()" not in branch and "save()" not in mark
    # what the readers take is written in live_tick alone
    tick = block(replay, "void live_tick(int64_t now_ns, int64_t frame_ns) {")
    for f in sorted(VK.glob("*.cpp")) + sorted(VK.glob("*.h")):
        text = f.read_text(encoding="utf-8", errors="replace")
        if f.name == "vk_replay.cpp":
            text = text.replace(tick, "")
        assert ".value.exchange(" not in text and ".value.store(" not in text, f.name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    check_source()
    check_ab()
    check_restart()
    with tempfile.TemporaryDirectory(prefix="live-switches-") as tmp:
        check_ticks(compile_fixture(args.cxx, tmp))
    print("live switches: ok")


if __name__ == "__main__":
    main()
