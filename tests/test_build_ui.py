"""The build dashboard's events and screen: no game, no compiler, no terminal.

It feeds the @@ lines make_game.py documents (including the new @@work) to nx2/build_ui.py's model and checks the steps,
the compile counts, the files in flight and the error path; then it renders the screen as plain text. It also checks
that build.py still prints @@work and that make_game.py documents it, since the dashboard's file rows depend on them.
"""
import contextlib
import importlib.util
import io
import os
import pathlib
import tempfile
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_ui", ROOT / "nx2" / "build_ui.py")
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)

EVENTS = [
    "version: Longshot test",
    "  code_000.c  8 MB  2s  (1s)",
    "@@stage read Reading your game files",
    "@@progress 0.5",
    "@@stage lift Translating the game to PC code",
    "@@stage compile Compiling",
    "@@plan 2 4",
    "@@work code_001.c 8 ThreadPoolExecutor-0_1",
    "@@work code_002.c 4 ThreadPoolExecutor-0_2",
    "@@file code_001.c 0.0 cached",
    "@@eta 90",
    "@@work code_003.c 2 ThreadPoolExecutor-0_0",
    "@@file code_002.c 1.5 new",
]

state = ui.BuildState()
for line in EVENTS:
    state.feed(line)

assert state.plan_changed == 2 and state.plan_total == 4, state.plan_changed
assert state.files_done == 2 and state.files_cached == 1 and state.files_new == 1, (state.files_done,
                                                                                    state.files_cached)
assert state.files_failed == 0
assert list(state.in_flight) == ["ThreadPoolExecutor-0_0"], state.in_flight
assert state.in_flight["ThreadPoolExecutor-0_0"][0] == "code_003.c"
assert state.eta == 90
assert state.current == "compile"
assert state.by_id["read"]["end"] is not None and state.by_id["compile"]["end"] is None
assert state.status() == "running"

text = "\n".join(ui.render_lines(state, 100, 30, "build.py --standalone --jobs 6", "work/build_ui.log", colour=False))
for wanted in ("Longshot build", "Compile", "2 / 4", "cached 1", "new 1", "workers:", "#0", "code_003.c", "log:"):
    assert wanted in text, wanted
assert "\x1b[" not in text, "colour off must be plain"

state.feed("@@file code_003.c 0.8 new")
state.feed("@@times 30 20 -1")
state.feed("@@done ~/Games/Longshot")
assert state.status() == "done", state.status()
assert state.times == (30, 20, -1)
assert state.done == "~/Games/Longshot"
assert not state.in_flight

bad = ui.BuildState()
bad.feed("@@stage compile Compiling")
bad.feed("@@plan 1 2")
bad.feed("@@work code_004.c 9 ThreadPoolExecutor-0_0")
bad.feed("@@file code_004.c 2.0 failed")
bad.feed("@@error compiler ran out of memory")
assert bad.files_failed == 1 and bad.files_done == 1
assert bad.status() == "failed" and "out of memory" in bad.error
bad_text = "\n".join(ui.render_lines(bad, 100, 24, "build.py", "x.log", colour=False))
assert "failed" in bad_text and "out of memory" in bad_text

build = (ROOT / "nx2" / "build.py").read_text(encoding="utf-8")
assert '@@work %s %d %s' in build, "build.py must announce the file it starts compiling"
protocol = (ROOT / "nx2" / "make_game.py").read_text(encoding="utf-8")
assert "@@work <name> <MB> <worker>" in protocol, "the protocol must document @@work"

# (a full screen must not scroll: the last line has no newline after it)
text = ui.draw_text(["a", "b", "c"])
assert text.startswith("\x1b[H") and text.endswith("\x1b[J")
assert text.count("\n") == 2, text
assert not text.endswith("\n")

# (a screen whose log tail fills every row keeps the title and does not scroll)
full = ui.BuildState()
full.stage("compile", "Compiling")
for i in range(80):
    full.feed("  build line %d" % i)
full_lines = ui.render_lines(full, 80, 16, "demo (no game files)", "x.log", colour=False)
assert len(full_lines) == 16, len(full_lines)
assert full_lines[0].startswith("Longshot build"), full_lines[0]
assert ui.draw_text(full_lines).count("\n") == 15

# (--plain prints the screen's events with its own prefix, and drops the ETA chatter)
assert ui.plain_event("@@eta 12") is None
assert ui.plain_event("@@work code.c 1 ThreadPoolExecutor-0_0") is None
assert ui.plain_event("@@file code.c 1.0 new") is None
assert ui.plain_event("@@stage compile Compiling") == "build_ui: stage compile Compiling"
assert ui.plain_event("@@done ~/Games/Longshot") == "build_ui: done ~/Games/Longshot"
assert ui.plain_event("build: macOS arm64 (baseline ISA)") == "build: macOS arm64 (baseline ISA)"
assert ui.plain_event("  code.c  1 MB  1s") == "  code.c  1 MB  1s"

# (build.py alone needs LONGSHOT_TIMES for @@plan/@@file/@@eta; make_game.py sets it itself)
with tempfile.TemporaryDirectory(prefix="longshot-ui-work-") as temporary:
    with mock.patch.dict(ui.os.environ, {"OOT_WORK": temporary}, clear=False):
        ui.os.environ.pop("LONGSHOT_TIMES", None)
        assert ui.work_dir() == temporary
        assert ui.build_env(False)["LONGSHOT_TIMES"] == os.path.join(temporary, "build_times.json")
        assert "LONGSHOT_TIMES" not in ui.build_env(True)

# (more than six workers are all named, or the rest are counted)
many = ui.BuildState()
many.stage("compile", "Compiling")
many.plan(0, 8)
for i in range(8):
    many.work("file_%d.c" % i, 1, "ThreadPoolExecutor-0_%d" % i)
wide = "\n".join(ui.render_lines(many, 110, 30, "build.py", "x.log", colour=False))
assert "#7" in wide, wide
narrow = "\n".join(ui.render_lines(many, 110, 16, "build.py", "x.log", colour=False))
assert "more" in narrow and "#7" not in narrow, narrow

# (two sources can share a base name: one @@file clears one row, not both)
dup = ui.BuildState()
dup.work("type.cpp", 2, "ThreadPoolExecutor-0_0")
dup.work("type.cpp", 2, "ThreadPoolExecutor-0_1")
dup.file("type.cpp", 1.0, "new")
assert len(dup.in_flight) == 1, dup.in_flight
dup.file("type.cpp", 1.0, "new")
assert not dup.in_flight, dup.in_flight

def state_with(**fields):
    state = ui.BuildState()
    for name, value in fields.items():
        setattr(state, name, value)
    return state


# (an attached log ends on @@done/@@error, and a build.py log ends on @@times)
assert ui.attach_done(state_with(error="x"))
assert ui.attach_done(state_with(done="x"))
assert ui.attach_done(state_with(times=(30, 20, -1)))
assert ui.attach_done(state_with(linked=True))
assert not ui.attach_done(state_with(times=(30, 20, -1), full=True))
for body, code, status in (("@@stage compile Compiling\n@@times 30 20 -1\n", 0, "done"),
                           ("@@done ~/Games/Longshot\n", 0, "done"),
                           ("@@error compiler ran out of memory\n", 1, "failed"),
                           ("compiled in 30s, 0 failed\nlinked x/longshot_vk.dll: 13 MB\n", 0, "done")):
    with tempfile.TemporaryDirectory(prefix="longshot-ui-attach-") as temporary:
        log = pathlib.Path(temporary) / "build.log"
        log.write_text(body, encoding="utf-8")
        attached = ui.BuildState()
        shown = io.StringIO()
        with contextlib.redirect_stdout(shown):
            got = ui.follow(str(log), attached, ui.Screen(plain=True), True)
        assert got == code, (body, got)
        assert attached.status() == status, (body, attached.status())
        if body.startswith("@@"):
            assert "build_ui: " in shown.getvalue(), (body, shown.getvalue())
        else:
            assert "compiled in 30s" in shown.getvalue(), (body, shown.getvalue())
        if body.startswith("compiled in 30s"):
            assert attached.compile_seconds() == 30, attached.compile_seconds()

attached = ui.BuildState()
attached.following = True
attached_text = "\n".join(ui.render_lines(attached, 100, 24, "attached to x.log", "x.log", colour=False))
assert "Ctrl+C stops following" in attached_text and "elapsed --:--" in attached_text, attached_text

print("PASS: the build dashboard reads the stages, the plan, the files in flight, the cache and the error path")
