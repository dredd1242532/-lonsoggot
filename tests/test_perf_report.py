"""The performance statistics (docs/performance-statistics.md): the game's summary (gpu/vk/perf_report.h, compiled with
tests/test_perf_report.cpp: buckets, hitches, pacing, the JSON, a frame's cost), the wiring (on only with the tick, a
frame added at each present, the events where they happen), and the launcher's side (nx2/perf_report.py):

  - the tick: send=1 in longshot_perf.ini, off by default; off deletes what is waiting;
  - with the tick on, the waiting summaries are sent oldest first, with the version and the release flag filled in, and
    each one the relay took or refused is deleted; one it couldn't take now (not reached, 429, 5xx) stops the sending and
    stays; with the tick off nothing is sent;
  - at most 48 wait: the oldest past that go unsent;
  - the relay's address is perf_reports in nx2/addresses.ini (none in the code);
  - the summary's map before the first one: "Preparing shaders", then "Title and intro".

python -I -B tests/test_perf_report.py [--cxx g++]
"""
import argparse
import http.server
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
import addresses  # noqa: E402
import perf_report as pr  # noqa: E402

failures = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


class Relay:
    """A stand-in perf relay: answers each POST with the next code of `codes` (then 201), keeps the bodies"""
    def __init__(self, codes=()):
        self.codes, self.bodies = list(codes), []
        relay = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                relay.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                code = relay.codes.pop(0) if relay.codes else 201
                self.send_response(code)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass
        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d/perf" % self.server.server_port

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def game_folder(tmp, name, summaries=3, tick=True, version="v3.1.0", source="abcd1234", log_source="abcd1234"):
    f = Path(tmp, name)
    (f / pr.PENDING).mkdir(parents=True)
    for i in range(summaries):
        (f / pr.PENDING / ("17000000%02d-%d.json" % (i, i))).write_text(json.dumps({"v": 1, "version": "x", "n": i}))
    (f / pr.VERSION_FILE).write_text("version=%s\nsource=%s\n" % (version, source))
    (f / "longshot_log.txt").write_text("[hos host] longshot %s (source %s)\n" % (version, log_source))
    if tick:
        pr.set_sending(str(f), True)
    return f


def launcher_side(tmp):
    f = game_folder(tmp, "plain", tick=False)
    check(not pr.sending(str(f)), "the tick is off by default")
    pr.set_sending(str(f), True)
    check(pr.sending(str(f)) and (f / pr.CHOICE).read_text() == "send=1\n", "on: send=1 in longshot_perf.ini")

    relay = Relay()
    try:
        got = pr.send_waiting(str(f), url=relay.url)
        check(got == {"sent": 3, "refused": 0, "left": 0}, "on: every summary sent (%s)" % got)
        check([b["n"] for b in relay.bodies] == [0, 1, 2], "oldest first")
        check(all(b["version"] == "v3.1.0" and b["release"] is True for b in relay.bodies),
              "the version and the release flag filled in")
        check(not list((f / pr.PENDING).glob("*.json")), "each one sent is deleted")

        f2 = game_folder(tmp, "edited", log_source="ffff0000")
        relay.bodies.clear()
        pr.send_waiting(str(f2), url=relay.url)
        check(relay.bodies and all(b["release"] is False for b in relay.bodies), "built from other sources: not a release")

        f3 = game_folder(tmp, "busy", summaries=4)
        relay.codes[:] = [201, 400, 429]
        relay.bodies.clear()
        got = pr.send_waiting(str(f3), url=relay.url)
        check(got == {"sent": 1, "refused": 1, "left": 2}, "a 429 stops the sending (%s)" % got)
        check(len(list((f3 / pr.PENDING).glob("*.json"))) == 2, "what the relay couldn't take now stays")

        f4 = game_folder(tmp, "off", tick=False)
        relay.bodies.clear()
        got = pr.send_waiting(str(f4), url=relay.url)
        check(not relay.bodies and got["sent"] == 0, "the tick off: nothing sent")

        f5 = game_folder(tmp, "turned off")
        pr.set_sending(str(f5), False)
        check(not (f5 / pr.CHOICE).exists() and not list((f5 / pr.PENDING).glob("*.json")),
              "turning the tick off deletes the setting and what is waiting")

        f6 = game_folder(tmp, "many", summaries=pr.MAX_PENDING + 5)
        relay.bodies.clear()
        pr.send_waiting(str(f6), url=relay.url)
        check(len(relay.bodies) == pr.MAX_PENDING and relay.bodies[0]["n"] == 5, "at most 48: the oldest go unsent")
    finally:
        relay.close()

    f8 = game_folder(tmp, "bad bytes", summaries=1)
    (f8 / pr.PENDING / "1700000000-0.json").write_bytes(b'{"v": 1, "version": "x", "n": 0, "os": "Linu\xc3"}')
    relay2 = Relay()
    try:
        got = pr.send_waiting(str(f8), url=relay2.url)
    finally:
        relay2.close()
    check(got["sent"] == 1, "a summary with a broken character is still sent (%s)" % got)

    f9 = game_folder(tmp, "detached", summaries=1)
    stub = Path(tmp, "stub.py")
    stub.write_text("import sys, pathlib\npathlib.Path(sys.argv[-2], 'started.txt').write_text(' '.join(sys.argv[1:]))\n")
    p = pr.start_sending(str(f9), python=sys.executable, python_args=[str(stub)])
    p.wait(20)
    started = (f9 / "started.txt").read_text() if (f9 / "started.txt").exists() else ""
    check(started.endswith(" %s --send" % f9) and "perf_report.py" in started,
          "start_sending runs nx2/perf_report.py <folder> --send in a process of its own")
    check(pr.start_sending(str(game_folder(tmp, "nothing waits", summaries=0))) is None and
          pr.start_sending(str(game_folder(tmp, "off again", tick=False))) is None,
          "nothing started when nothing waits or the tick is off")

    f7 = game_folder(tmp, "unreachable")
    got = pr.send_waiting(str(f7), url="http://127.0.0.1:9/perf", timeout=2)
    check(got["left"] == 3 and len(list((f7 / pr.PENDING).glob("*.json"))) == 3, "not reached: everything stays")

    check(pr.ENDPOINT.startswith("https://") and pr.ENDPOINT.endswith("/perf"), "the relay's address from addresses.ini")
    check(not re.search(r"[\"']https?://", (ROOT / "nx2" / "perf_report.py").read_text(encoding="utf-8")),
          "no address in nx2/perf_report.py's code")
    ini = Path(tmp, "addresses.ini")
    ini.write_bytes(b"\xef\xbb\xbf" + Path(addresses.ADDRESSES).read_bytes())
    check(addresses.address("perf_reports", str(ini)) == pr.ENDPOINT, "addresses.ini with a BOM")
    ini.write_text("[addresses]\nperf_reports = a\nperf_reports = b\n")
    check(addresses.address("perf_reports", str(ini)) == "", "a broken addresses.ini turns the use off, never raises")


def wiring():
    vk = ROOT / "gpu" / "vk"
    fps = (vk / "vk_fps.cpp").read_text(encoding="utf-8")
    check("perf_report_init()" in fps and "perf_frame(" in fps, "vk_fps.cpp starts the summary and adds each frame")
    check("perf_measure" in (vk / "vk_fps.h").read_text(encoding="utf-8"), "the Breakdown is measured while the summary is on")
    cpp = (vk / "perf_report.cpp").read_text(encoding="utf-8")
    check('"longshot_perf.ini"' in cpp and '"send="' in cpp, "the game reads the tick from longshot_perf.ini")
    check('"perf_pending"' in cpp and "rename(" in cpp, "summaries are written whole into perf_pending")
    for f, use in (("vk_replay.cpp", "perf_report_final(perf_report::kDeviceLost)"), ("vk_replay.cpp", "kVramOverBudget"),
                   ("vk_replay.cpp", "kMovieUpscalerDropped"), ("vk_dlss.cpp", "kDlssRecreated")):
        check(use in (vk / f).read_text(encoding="utf-8"), "%s reports %s" % (f, use))
    main_cpp = (vk / "vk_main.cpp").read_text(encoding="utf-8")
    close = main_cpp[main_cpp.index("void mkwvk_close_asked(void) {"):]
    check("perf_report_final(-1)" in close[:close.index("\n}\n")],
          "the window's close writes the last summary (ExitProcess is _exit on Linux: no atexit)")
    api = (vk / "vk_api.h").read_text(encoding="utf-8")
    check("perf_info" in api, "the host gives the renderer the map and the movie totals (perf_info)")
    check("hos_perf_info" in (ROOT / "host" / "rx_backend.c").read_text(encoding="utf-8"), "rx_backend.c fills perf_info")
    check("call_once(once, [] {\n        perf_report_final(perf_report::kDeviceLost);" in
          (vk / "vk_replay.cpp").read_text(encoding="utf-8"), "a lost device's summary is written once")
    check("if (!ticked(folder))" in cpp[cpp.index("void summarize("):], "unticked while the game runs: it stops")
    check("shaders_preparing() ? pr::kPreparingShaders : pr::kTitleAndIntro" in cpp and
          "bool shaders_preparing()" in main_cpp, "before a map: Preparing shaders, then Title and intro (not 'not known')")


def unit(cxx):
    with tempfile.TemporaryDirectory() as tmp:
        exe = Path(tmp, "perf_report_test")
        subprocess.run([cxx, "-std=c++20", "-O2", "-include", str(ROOT / "gpu" / "vk" / "compat" / "longshot_env.h"),
                        "-I", str(ROOT / "gpu" / "vk"), str(ROOT / "tests" / "test_perf_report.cpp"), "-o", str(exe)],
                       check=True)
        out = subprocess.run([str(exe)], capture_output=True, text=True)
        print(out.stdout.strip())
        check(out.returncode == 0, "tests/test_perf_report.cpp" + (": " + out.stderr.strip() if out.stderr else ""))
        m = re.search(r"add_frame: ([\d.]+) ns", out.stdout)
        check(m is not None and float(m.group(1)) < 200, "a frame costs well under a microsecond to add")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cxx", default="g++")
    a = p.parse_args()
    unit(a.cxx)
    wiring()
    with tempfile.TemporaryDirectory() as tmp:
        launcher_side(tmp)
    print("%d failed" % len(failures) if failures else "all passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
