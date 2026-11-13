"""A client for the game's control bridge (host/control.c, docs/control.md): start the game with HOS_CONTROL=<socket>, then
steer it and read its state from a shell, a test or a coding agent. Standard library only.

    python -I scripts/longshot_ctl.py [--sock S] [--json] COMMAND ...

    launch GAME_DIR [--data DIR] [--headless]   start the game with the bridge on; prints the socket's path
           [--save FOLDER [--slot NN]]          a copy of a save folder (one slot of it) as the run's saves
    status                                      frame, fps, draws a frame, state, window, version
    press A+B [--frames 8] [--exclusive]        buttons held for some frames (the reply once they are let go)
    stick L 0 1 [--frames 60]                   a stick at x, y (-1 to 1)
    sequence '0:A/8,60:A/8' [--hos]             steps, <at>:<buttons>[/<frames held>] (--hos: HOS_INPUT_SCRIPT's s and ms)
    release [--all]                             this client's queued inputs let go (and those of clients gone)
    shot [out.png] [--after FRAME]              a PNG of the next present; prints its path
    wait in_play [--timeout 120]                also: present --frames N, frame --frame N, state --state S,
                                                loading_start, loading_end, change, map [--map NAME]
    teleport [INDEX|MAP] [--arrive SECS]        (OoT) the debug teleport: sent or refused; none: the destinations
    age [child|adult] [--arrive SECS]           (OoT) the Debug tab's Switch age (none: the other age), reloading the map
    option get [NAME]  /  option set NAME VALUE the Insert menu's settings, and the live switches (gpu/vk/vk_live.h)
    ab NAME [--every 10] [--rounds 4]           a live switch flipped while the game runs; each value's fps, 1% low,
                                                GPU time and draws, and how much halves of the same value differ
    ab NAME --restart GAME_DIR [--values A,B]   a switch that needs a restart: one launch a value, A B then B A, each
           [--teleport 3] [--settle 15]         taken into play and to the scene, then measured the same way
    saves  /  slots [--thumbs DIR]              the save folder the game uses; its slots, newest first
    watch [--events present,state,log,map] [--every 60] [--log TEXT] [--count N]
    mem read ADDR SIZE [--format hex|u8|u16|u32|u64|f32]   read guest memory (HOS_CONTROL_MEM=ro or rw)
    mem write ADDR HEX                          write guest memory (HOS_CONTROL_MEM=rw); aliases: ro->r/read, rw->readwrite
    quit                                        the game exits
    raw '{"cmd": "status"}'                     any request

The socket defaults to $HOS_CONTROL. Plain lines by default, --json for the replies as they come. Exit status: 0 done,
1 an error reply, 2 no game or bad usage, 3 a timeout.
"""
import argparse
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time

WINDOWS = os.name == "nt"


class Failed(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


class Bridge:
    """One connection: requests with ids, replies matched to them, events kept for watch"""

    def __init__(self, path, timeout=10.0):
        self.path, self.next_id, self.pending = path, 0, b""
        self.kept = None   # (a list: the events that come during a request are kept there, not dropped)
        try:
            if WINDOWS:
                name = path if path.startswith("\\\\.\\pipe\\") else "\\\\.\\pipe\\" + path
                self.sock = None
                deadline = time.monotonic() + timeout
                while True:   # (the bridge keeps one instance waiting: a busy pipe is waited for)
                    try:
                        self.pipe = open(name, "r+b", buffering=0)
                        break
                    except OSError as e:
                        if getattr(e, "winerror", 0) != 231 or time.monotonic() > deadline:   # ERROR_PIPE_BUSY
                            raise
                        time.sleep(0.02)
            else:
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.settimeout(timeout)
                self.sock.connect(path)
        except OSError as e:
            raise Failed("no game listens on %s (%s)" % (path, e.strerror or e), 2)

    def close(self):
        (self.sock or self.pipe).close()

    def send(self, obj):
        data = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        if self.sock:
            self.sock.sendall(data)
        else:
            self.pipe.write(data)

    def line(self, timeout):
        """The next line as an object; None when the game went away"""
        deadline = time.monotonic() + timeout
        while b"\n" not in self.pending:
            left = deadline - time.monotonic()
            if left <= 0:
                raise Failed("no answer from the game in %.0f s" % timeout, 3)
            try:
                if self.sock:
                    self.sock.settimeout(left)
                    chunk = self.sock.recv(65536)
                else:
                    chunk = self._pipe_read(left)
                    if chunk is None:
                        continue
            except socket.timeout:
                raise Failed("no answer from the game in %.0f s" % timeout, 3)
            except OSError:
                chunk = b""
            if not chunk:
                return None
            self.pending += chunk
        text, self.pending = self.pending.split(b"\n", 1)
        return json.loads(text.decode("utf-8", "replace"))

    def _pipe_read(self, left):
        """(Windows) what the pipe holds, after waiting up to `left` seconds for some: None for nothing yet, b"" when the
        bridge's end closed. PeekNamedPipe and then a read of only what is there: a read that blocks would hold the
        handle's writes too (one synchronous handle)"""
        import ctypes
        import msvcrt
        handle = msvcrt.get_osfhandle(self.pipe.fileno())
        avail = ctypes.c_ulong(0)
        end = time.monotonic() + min(left, 0.05)
        while True:
            if not ctypes.windll.kernel32.PeekNamedPipe(ctypes.c_void_p(handle), None, 0, None, ctypes.byref(avail), None):
                return b""
            if avail.value:
                return self.pipe.read(avail.value)
            if time.monotonic() >= end:
                return None
            time.sleep(0.002)

    def request(self, cmd, timeout=None, **args):
        """The reply to one request (events that come meanwhile are dropped). Without a timeout the bridge's own default
        holds (for an input: 30 s plus its frames at 10 fps, so a long one can take hours): the client then waits as
        long as the bridge can (an hour at most), unless the connection ends"""
        self.next_id += 1
        rid = self.next_id
        req = {"id": rid, "cmd": cmd}
        req.update({k: v for k, v in args.items() if v is not None})
        if timeout is not None:
            req["timeout"] = timeout
        self.send(req)
        wait = (timeout if timeout is not None else 3600 if cmd in ("press", "stick", "sequence") else 60) + 10
        while True:
            msg = self.line(wait)
            if msg is None:
                if cmd == "quit":
                    return {"id": rid, "ok": True, "quitting": True}
                raise Failed("the game closed the connection", 2)
            if msg.get("id") == rid and "ok" in msg:
                return msg
            if self.kept is not None and "event" in msg:
                self.kept.append(msg)


def plain_text(text):
    """Text from the game for a terminal: its control characters (an escape sequence in a log line) shown as '?'"""
    return "".join("?" if ord(ch) < 0x20 or 0x7f <= ord(ch) < 0xa0 else ch for ch in str(text))


def when(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else "-"


def saves_text(r):
    lines = ["%s  (account.sav %s, option.sav %s)" % (plain_text(r["path"]), "yes" if r["account"] else "no",
                                                        "yes" if r["option"] else "no")]
    for sl in r["slots"]:
        lines.append("slot_%s  %s%s" % (plain_text(sl["slot"]), when(sl["mtime"]), "  (newest)" if sl["newest"] else ""))
        for f in sorted(sl["files"], key=lambda f: f["name"]):
            lines.append("    %-14s %9d  %s" % (plain_text(f["name"]), f["size"], when(f["mtime"])))
    return "\n".join(lines)


def slots_text(r):
    rows = ["   slot  last written          progress   caption  scene"]
    for sl in r["slots"]:
        size = {f["name"]: f["size"] for f in sl["files"]}
        rows.append("%s  %-4s  %-19s %9s %9s  %s" % ("*" if sl["newest"] else " ", plain_text(sl["slot"]), when(sl["mtime"]),
                                                   size.get("progress.sav", "-"), size.get("caption.sav", "-"),
                                                   plain_text(sl.get("caption", {}).get("scene", ""))))
    rows.append("(* the newest: the one Continue loads)")
    return "\n".join(rows)


def write_thumbs(r, folder):
    """Each slot's file-select picture, as the JPEG caption.sav holds, read from the save folder (this PC's). What the game
    says is checked first: this client may run outside a sandbox the game is in, so a slot's name is only digits, the
    picture a bounded slice of that slot's caption.sav, and nothing is written outside folder"""
    os.makedirs(folder, exist_ok=True)
    for sl in r["slots"]:
        th = sl.get("caption", {}).get("thumbnail")
        if not th:
            continue
        slot, off, size = sl.get("slot"), th.get("offset"), th.get("size")
        if not (isinstance(slot, str) and re.fullmatch(r"[0-9]{1,3}", slot) and isinstance(off, int) and
                isinstance(size, int) and 0 <= off < (1 << 24) and 0 < size <= (4 << 20)):
            raise Failed("slots --thumbs: the game's reply does not look like a save listing: %r" % (sl,), 1)
        with open(os.path.join(r["path"], "slot_" + slot, "caption.sav"), "rb") as f:
            f.seek(off)
            data = f.read(size)
        if data[:2] == b"\xff\xd8":
            with open(os.path.join(folder, "slot_%s.jpg" % sl["slot"]), "wb") as out:
                out.write(data)


def addr_of(text):
    try:
        return int(text, 0)
    except ValueError:
        raise Failed("ADDR: a number, or 0x...", 2)


def format_bytes(data, fmt):
    import struct
    if fmt in (None, "hex"):
        return data.hex()
    if fmt == "base64":
        import base64
        return base64.b64encode(data).decode()
    sizes = {"u8": ("B", 1), "u16": ("<H", 2), "u32": ("<I", 4), "u64": ("<Q", 8), "f32": ("<f", 4)}
    code, n = sizes[fmt]
    out = []
    for i in range(0, len(data) - n + 1, n):
        out.append(struct.unpack(code, data[i:i + n])[0])
    return " ".join(("%g" if fmt == "f32" else "%d") % v for v in out)


def connect(a, timeout=10.0):
    path = a.sock or os.environ.get("HOS_CONTROL")
    if not path:
        raise Failed("no socket: give --sock PATH or set HOS_CONTROL (longshot_ctl.py launch GAME_DIR starts a game "
                     "with the bridge on and prints its socket)", 2)
    return Bridge(path, timeout)


def check(reply):
    if not reply.get("ok"):
        code = 3 if reply.get("error") == "timeout" else 1
        raise Failed("%s: %s" % (reply.get("error", "error"), reply.get("message", "")), code)
    return reply


def show(a, reply, plain):
    if a.json:
        print(json.dumps(reply))
    else:
        print(plain(reply))


# (the game's line for the time since a live switch's last change, gpu/vk/vk_replay.cpp live_tick)
LIVE_LINE = re.compile(r"vk: live: (\d+) frames in ([\d.]+) s with (.*?): ([\d.]+) fps \(1% low ([\d.]+)\), GPU ([\d.]+) ms a "
                       r"frame, ([\d.]+) draws a frame")
LIVE_SHORT = "vk: live: under half a second with "


def live_halves(lines, name):
    """The game's lines for each time between two asks, in order: its numbers and the value name had, or None for one too
    short to count"""
    out = []
    for line in lines:
        m = LIVE_LINE.search(line)
        if m:
            values = dict(v.split("=", 1) for v in m.group(3).split() if "=" in v)
            out.append({"value": int(values.get(name, -1)), "frames": int(m.group(1)), "secs": float(m.group(2)),
                        "fps": float(m.group(4)), "low": float(m.group(5)), "gpu_ms": float(m.group(6)),
                        "draws": float(m.group(7))})
        elif LIVE_SHORT in line:
            out.append(None)
    return out


def median(xs):
    s = sorted(xs)
    return (s[len(s) // 2] + s[(len(s) - 1) // 2]) / 2


def ab_summary(halves):
    """Each value's medians, and its fps spread from half to half (what the same value varies by: the noise)"""
    out = {}
    for v in sorted({h["value"] for h in halves}):
        hs = [h for h in halves if h["value"] == v]
        out[v] = {k: median([h[k] for h in hs]) for k in ("fps", "low", "gpu_ms", "draws")}
        out[v].update(halves=len(hs), spread=max(h["fps"] for h in hs) - min(h["fps"] for h in hs))
    return out


def ab_text(name, every, halves, summary, flagged, what="half", pair=None):
    """The table: each half (what: a half of one session, or a launch), each value's medians over the halves not flagged,
    B against A (pair: the values in that order; by default as summary has them)"""
    whats = what + ("es" if what.endswith("ch") else "s")
    lines = ["%-6s %-8s %7s %7s %8s %8s %8s" % (what, "value", "frames", "fps", "1% low", "GPU ms", "draws")]
    for i, h in enumerate(halves):
        lines.append("%-6d %-8s %7d %7.1f %8.1f %8.2f %8.0f%s" % (i + 1, h["value"], h["frames"], h["fps"], h["low"], h["gpu_ms"],
                                                              h["draws"], "  (left out: %s)" % flagged[i] if flagged[i] else ""))
    lines.append("")
    lines.append("%-8s %6s %7s %7s %8s %8s %8s" % ("value", whats, "fps", "spread", "1% low", "GPU ms", "draws"))
    for v, s in summary.items():
        lines.append("%-8s %6d %7.1f %7.1f %8.1f %8.2f %8.0f" % (v, s["halves"], s["fps"], s["spread"], s["low"], s["gpu_ms"],
                                                             s["draws"]))
    va, vb = pair if pair and all(v in summary for v in pair) else list(summary)[:2] if len(summary) == 2 else (None, None)
    if va is not None:
        a, b = summary[va], summary[vb]
        pct = lambda x, y: 100.0 * (y - x) / x if x else 0.0   # noqa: E731
        noise = max(a["spread"], b["spread"])
        lines.append("%s=%s against %s: fps %+.1f%%, 1%% low %+.1f%%, GPU ms %+.1f%%, draws %+.1f%%; the fps moved %.1f between "
                     "%s of the same value%s" % (name, vb, va, pct(a["fps"], b["fps"]), pct(a["low"], b["low"]),
                                                 pct(a["gpu_ms"], b["gpu_ms"]), pct(a["draws"], b["draws"]), noise, whats,
                                                 " (the difference is within it)" if abs(b["fps"] - a["fps"]) <= noise else ""))
    lines.append("(medians of %d s %s; the first 0.5 s after each change left out)" % (every, whats))
    return "\n".join(lines)


def live_lines(b, want, name, timeout=15):
    """The game's vk: live: lines kept on b (subscribed to them), read until want halves are in or timeout seconds pass; its
    state events (a load, a menu) go to b.states, by frame"""
    deadline = time.monotonic() + timeout   # (each ask's line comes with the next frame)
    lines = []
    while True:
        lines += [m.get("line", "") for m in b.kept if m.get("event") == "log"]
        b.states = getattr(b, "states", []) + [m.get("frame") for m in b.kept if m.get("event") == "state"]
        b.kept = []
        if len(live_halves(lines, name)) >= want or time.monotonic() > deadline:
            return live_halves(lines, name)
        msg = b.line(max(0.1, deadline - time.monotonic()))
        if msg is None:
            raise Failed("the game closed the connection", 2)
        b.kept.append(msg)


def scene(b):
    st = check(b.request("status"))
    return st.get("state"), st.get("map"), st.get("frame")


def scene_flag(before, after, half, states=()):
    """Why a half is not counted: the game out of play at either end, another map, a change of state between (a load,
    a menu: the state events' frames, after the first status's and up to the second's), or too short; "" for none"""
    (s0, m0, f0), (s1, m1, f1) = before, after
    moved = f0 is not None and f1 is not None and any(f is not None and f0 < f <= f1 for f in states)
    return ("not in play" if s0 != "play" or s1 != "play" else "the map changed" if m0 != m1 else
            "a load or a menu in it" if moved else "too short" if half is None else "")


def ab_counted(halves, flagged):
    """The halves the medians are taken over: measured and not flagged"""
    counted = [h for h, f in zip(halves, flagged) if h is not None and not f]
    if not counted:
        raise Failed("every half was flagged or too short: %s" % "; ".join(f or "too short" for f in flagged), 1)
    return counted


def teleport_to(b, to, timeout):
    """(OoT) the debug teleport, asked again while the game refuses it (an event playing: the one Continue starts, a map
    change's) for up to timeout seconds, then until the game is in that map"""
    deadline = time.monotonic() + timeout
    while True:
        r = b.request("teleport", to=to, timeout=30)
        if r.get("ok"):
            break
        if time.monotonic() > deadline:
            check(r)
        b.request("wait", timeout=60, event="present", frames=120)
    w = b.request("wait", timeout=max(10, deadline - time.monotonic()), event="map", map=r.get("map"))
    if not w.get("ok"):
        raise Failed("the teleport to %s was sent, and the game is not there after %.0f s" % (r.get("map") or to, timeout), 3)


def to_play(b, timeout):
    """Through the title and the file select (A, as Continue) until the game is in play. A press the game does not take
    (the boot logos and the intro read no controller: the bridge's timeout) is tried again after a second"""
    deadline = time.monotonic() + timeout
    while scene(b)[0] != "play":
        if time.monotonic() > deadline:
            raise Failed("not in play after %.0f s" % timeout, 3)
        b.request("press", buttons="A", frames=8, timeout=10)
        b.request("wait", timeout=60, event="present", frames=60)


def ab_restart(a):
    """A switch that needs a restart: the game in a.restart started once a value (A B, then B A, a.rounds launches each
    way, so neither value always comes second to warm caches), each time taken into play (Continue), to a.teleport, left
    a.settle seconds, then a.every seconds measured between two marks (live.mark: the game's own numbers), and quit"""
    values = a.values.split(",")
    if len(values) != 2:
        raise Failed("--values: two values, A,B", 2)
    order = [v for r in range(a.rounds) for v in (values if r % 2 == 0 else values[::-1])]
    base = os.path.abspath(a.run) if a.run else tempfile.mkdtemp(prefix="longshot-ab-")
    halves, flagged = [], []
    for i, v in enumerate(order):
        ns = argparse.Namespace(game_dir=a.restart, data=None, save=a.save, slot=a.slot, headless=a.headless,
                                run=os.path.join(base, "%02d-%s" % (i + 1, re.sub(r"[^\w.-]", "_", v))), sock=None,
                                env=list(a.env) + ["%s=%s" % (a.name, v)], timeout=a.start_timeout, json=False)
        info, p = start_game(ns)
        b = Bridge(info["socket"], 10)
        try:
            to_play(b, a.start_timeout)
            if a.teleport is not None:
                teleport_to(b, int(a.teleport) if a.teleport.isdigit() else a.teleport, 120)
            time.sleep(a.settle)
            check(b.request("subscribe", events=["log", "state"], log="vk: live:"))
            b.kept, b.states = [], []
            before = scene(b)
            check(b.request("option_set", name="live_mark", value=0))   # (the start: the time before is not counted)
            time.sleep(a.every)
            after = scene(b)
            check(b.request("option_set", name="live_mark", value=0))
            got = live_lines(b, 2, a.name)
            if len(got) < 2:
                raise Failed("launch %d logged %d of its 2 marks (is the renderer one with live.mark?)" % (i + 1, len(got)), 1)
            half = dict(got[1], value=v) if got[1] else None
            halves.append(half)
            flagged.append(scene_flag(before, after, half, b.states))
            b.request("quit")
        finally:
            b.close()
            try:
                p.wait(60)
            except subprocess.TimeoutExpired:
                p.kill()
        if not a.json:
            h = halves[-1]
            print("launch %d, %s=%s: %s" % (i + 1, a.name, v, "%.1f fps, GPU %.2f ms, %.0f draws" % (h["fps"], h["gpu_ms"], h["draws"])
                                            if h else "too short"), flush=True)
    summary = ab_summary(ab_counted(halves, flagged))
    shown = [(h, f) for h, f in zip(halves, flagged) if h is not None]
    show(a, {"ok": True, "switch": a.name, "restart": True, "every": a.every, "halves": halves, "flagged": flagged,
             "summary": {str(k): v for k, v in summary.items()}},
         lambda r: ab_text(a.name, a.every, [h for h, _ in shown], summary, [f for _, f in shown], what="launch", pair=values))
    return 0


def ab(a, b):
    """A live switch flipped every a.every seconds while the game runs, a.rounds times each way, starting from the value it
    has and ending on it; each half's numbers are the game's (vk: live: lines), and a half the scene changed in is marked"""
    o = check(b.request("option_get", name=a.name))["option"]
    if not o["key"].startswith("env."):
        raise Failed("%s is not a live switch (option get lists them, tab live)" % a.name, 2)
    name, start = o["name"], o["value"]
    order = [start, 1 - (start != 0)] * a.rounds + [start]   # (the first ask ends the time before the run: not counted)
    check(b.request("subscribe", events=["log", "state"], log="vk: live:"))
    b.kept, b.states, scenes = [], [], []
    for i, v in enumerate(order):
        scenes.append(scene(b))
        check(b.request("option_set", name=name, value=v))
        if i + 1 < len(order):
            time.sleep(a.every)
    scenes.append(scene(b))
    got = live_lines(b, len(order), name)
    if len(got) < len(order):
        raise Failed("the game logged %d of the %d halves (is the renderer one with live switches?)" % (len(got), len(order)), 1)
    got = got[1:len(order)]
    # (half i: from ask i + 1 to ask i + 2)
    flagged = [scene_flag(scenes[i + 1], scenes[i + 2], got[i], b.states) for i in range(len(got))]
    summary = ab_summary(ab_counted(got, flagged))
    shown = [(h, f) for h, f in zip(got, flagged) if h is not None]
    show(a, {"ok": True, "switch": name, "every": a.every, "halves": got, "flagged": flagged,
             "summary": {str(k): v for k, v in summary.items()}},
         lambda r: ab_text(name, a.every, [h for h, _ in shown], summary, [f for _, f in shown]))
    return 0


def run(a):
    if a.command == "launch":
        return launch(a)
    if a.command == "ab" and a.restart:
        return ab_restart(a)
    if a.command == "mem":
        a.addr = addr_of(a.addr)
        if a.action == "read":
            if a.size is None:
                raise Failed("mem read ADDR SIZE", 2)
            a.size = addr_of(str(a.size))
        else:
            a.hex = a.hex if a.hex is not None else a.size   # write ADDR HEX
            if not a.hex:
                raise Failed("mem write ADDR HEX", 2)
    b = connect(a)
    try:
        if a.command == "status":
            r = check(b.request("status"))
            win = r.get("window") or {}
            g = r.get("game") or {}
            flags = " ".join(n for n, on in (("event", g.get("event")), ("loading", g.get("loading"))) if on)
            show(a, r, lambda r: "frame %d  fps %.1f  state %s  draws %s  window %s  map %s%s  %s" % (
                r["frame"], r["fps"], r["state"], "-" if r.get("draws") is None else "%.0f" % r["draws"],
                "%dx%d" % (win["width"], win["height"]) if win else "-", plain_text(r.get("map") or "-"),
                " (%s%s)" % (plain_text(g.get("age", "?")), ", " + flags if flags else "") if g else "",
                plain_text(r.get("version", ""))))
        elif a.command in ("press", "stick", "sequence"):
            args = {"exclusive": True if a.exclusive else None, "wait": False if a.no_wait else None}
            if a.command == "press":
                args.update(buttons=a.buttons, frames=a.frames, at=a.at)
            elif a.command == "stick":
                args.update(stick=a.which.upper(), x=a.x, y=a.y, frames=a.frames, at=a.at)
            else:
                args.update(script=a.script.strip(), units="hos" if a.hos else None)
            r = check(b.request(a.command, timeout=a.timeout, **args))
            show(a, r, lambda r: "queued %d input(s) at frame %d" % (r["queued"], r["frame"]) if "queued" in r
                 else "frames %d to %d (now %d)" % (r["start"], r["end"], r["frame"]))
        elif a.command == "release":
            show(a, check(b.request("release", all=True if a.all else None)),
                 lambda r: "released %d%s" % (r["released"], " (every client's)" if a.all else ""))
        elif a.command == "shot":
            path = os.path.abspath(a.path) if a.path else None   # (the bridge would take it as the socket folder's)
            r = check(b.request("shot", timeout=a.timeout, path=path, after=a.after))
            show(a, r, lambda r: r["path"])
        elif a.command == "wait":
            r = check(b.request("wait", timeout=a.timeout, event=a.event, frames=a.frames, frame=a.frame, state=a.state,
                                map=a.map))
            show(a, r, lambda r: "%s after %d frames (frame %d)%s" % (r["state"], r["waited"], r["frame"],
                                                                     "  map %s" % plain_text(r["map"]) if "map" in r else ""))
        elif a.command == "teleport":
            to = None if a.to is None else int(a.to) if a.to.isdigit() else a.to
            if to is None and (a.arrive or a.no_wait):
                raise Failed("teleport: --arrive and --no-wait go with a destination", 2)
            r = check(b.request("teleport", to=to, wait=False if a.no_wait else None,
                                timeout=None if to is None else a.timeout))
            if "destinations" in r:
                show(a, r, lambda r: "\n".join("%3d  %-28s %s" % (d["index"], plain_text(d["child"] or "-"),
                                                                  plain_text(d["adult"] or "-")) for d in r["destinations"]))
            elif not r.get("sent"):
                show(a, r, lambda r: "teleport %d asked for (child %s, adult %s): wait map to see it arrive" % (
                    r["teleport"], plain_text(r["child"] or "-"), plain_text(r["adult"] or "-")))
            elif not a.arrive:
                show(a, r, lambda r: "teleport %d sent: to %s%s" % (
                    r["teleport"], plain_text(r.get("map") or "-"),
                    "" if r.get("accepted", True) else " (but the game's event manager did not take it)"))
            else:   # (and until the game is in that map: the arrival)
                w = check(b.request("wait", timeout=a.arrive, event="map", map=r.get("map")))
                show(a, dict(r, arrived=w), lambda r: "teleport %d: in %s after %d frames (frame %d)" % (
                    r["teleport"], plain_text(w["map"]), w["waited"], w["frame"]))
        elif a.command == "age":
            r = check(b.request("age", to=a.to, wait=False if a.no_wait else None, timeout=a.timeout))
            if r.get("sent") is False:
                show(a, r, lambda r: "Link is the %s already" % plain_text(r["age"]))
            elif r.get("sent") is None:
                show(a, r, lambda r: "age switch to the %s asked for: wait loading_end to see it reload" % plain_text(r["age"]))
            elif not a.arrive:
                show(a, r, lambda r: "age switch sent: the %s, reloading %s%s" % (
                    plain_text(r["age"]), plain_text(r.get("map") or "-"),
                    "" if r.get("accepted", True) else " (but the game's event manager did not take it)"))
            else:   # (and until the reload's load has ended: in a dungeon the map has the same name for both ages, so
                    # a wait map would be met before the reload; loading_end counts from the ask, so a load already
                    # started when it is asked is still waited out)
                w = check(b.request("wait", timeout=a.arrive, event="loading_end"))
                show(a, dict(r, arrived=w), lambda r: "age switch: the %s, in %s after %d frames (frame %d)" % (
                    plain_text(r["age"]), plain_text(r.get("map") or "-"), w["waited"], w["frame"]))
        elif a.command == "option":
            if a.action == "get":
                r = check(b.request("option_get", name=a.name))
                rows = [r["option"]] if "option" in r else r["options"]
                show(a, r, lambda r: "\n".join("%-28s %4d  %s%s" % (o["key"], o["value"], plain_text(o["text"]),
                                                                    "  (restart)" if o.get("restart") else "") for o in rows))
            else:
                if a.value is None:
                    raise Failed("option set NAME VALUE", 2)
                v = {"on": 1, "true": 1, "off": 0, "false": 0}.get(a.value.lower())
                if v is None:
                    try:
                        v = int(a.value)
                    except ValueError:
                        raise Failed("VALUE: a number, on or off", 2)
                r = check(b.request("option_set", name=a.name, value=v))
                show(a, r, lambda r: "%s %d -> %d (%s)" % (r["option"]["key"], r["was"], r["option"]["value"], r["option"]["text"]))
        elif a.command == "mem":
            if a.action == "read":
                fmt = a.format if a.format in ("hex", "base64") else "hex"
                r = check(b.request("mem_read", addr=a.addr, size=a.size, format=fmt))
                data = bytes.fromhex(r["data"]) if r["format"] == "hex" else __import__("base64").b64decode(r["data"])
                show(a, r, lambda r: format_bytes(data, a.format))
            else:
                raw = a.hex.lower().replace("0x", "")
                try:
                    bytes.fromhex(raw)
                except ValueError:
                    raise Failed("HEX: an even number of hex digits", 2)
                r = check(b.request("mem_write", addr=a.addr, hex=raw))
                show(a, r, lambda r: "wrote %d bytes at %d" % (r["wrote"], r["addr"]))
        elif a.command == "saves":
            r = check(b.request("saves"))
            show(a, r, lambda r: saves_text(r))
        elif a.command == "slots":
            r = check(b.request("saves"))
            if a.thumbs:
                write_thumbs(r, a.thumbs)
            if a.json:
                print(json.dumps(r["slots"]))
            else:
                print(slots_text(r))
        elif a.command == "quit":
            show(a, check(b.request("quit")), lambda r: "quitting")
        elif a.command == "raw":
            req = json.loads(a.request)
            b.next_id += 1
            req.setdefault("id", b.next_id)
            b.send(req)
            while True:
                msg = b.line(float(req.get("timeout", 60)) + 10)
                if msg is None:
                    raise Failed("the game closed the connection", 2)
                if msg.get("id") == req["id"] and "ok" in msg:
                    print(json.dumps(msg))
                    return 0 if msg["ok"] else 1
        elif a.command == "ab":
            return ab(a, b)
        elif a.command == "watch":
            # (--log alone: the log lines only; with --events, those and the log lines)
            events = [e for e in (a.events or ("" if a.log else "present,state")).split(",") if e]
            if a.log and "log" not in events:
                events.append("log")
            check(b.request("subscribe", events=events, every=a.every, log=a.log))
            n = 0
            while a.count is None or n < a.count:
                msg = b.line(3600 * 24)
                if msg is None:
                    return 0
                if "event" not in msg:
                    continue
                n += 1
                if a.json:
                    print(json.dumps(msg), flush=True)
                elif msg["event"] == "log":
                    print("log    %s" % plain_text(msg["line"]), flush=True)
                elif msg["event"] == "state":
                    print("state  frame %d: %s -> %s" % (msg["frame"], msg["from"], msg["state"]), flush=True)
                elif msg["event"] == "present":
                    print("frame  %d  fps %.1f  %s" % (msg["frame"], msg["fps"], msg["state"]), flush=True)
                elif msg["event"] == "map":
                    print("map    frame %d: %s -> %s" % (msg["frame"], plain_text(msg["from"] or "-"), plain_text(msg["map"])),
                          flush=True)
                else:
                    print(json.dumps(msg), flush=True)
        return 0
    finally:
        b.close()


# (the one account the runtime has, host/nn_applet.c user_uid: its saves are <data>/longshot/save/<this>)
SAVE_USER = "5a3c9e1f0b7d24680000000000000001"


def slot_name(text):
    """03, 3 or slot_03 -> slot_03"""
    t = text[5:] if text.lower().startswith("slot_") else text
    if not t.isdigit() or len(t) > 2:
        raise Failed("--slot: a slot number, 03 or slot_03", 2)
    return "slot_%02d" % int(t)


def plain_tree(path):
    """Refused (Failed) when path or anything under it is a link or not a plain file or folder: a save from elsewhere could
    hold a link to one of this user's files, which the copy would read and the game then write through"""
    for top, dirs, files in os.walk(path):
        for n in [top] + [os.path.join(top, d) for d in dirs] + [os.path.join(top, f) for f in files]:
            st = os.lstat(n)
            if not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
                raise Failed("--save: %s is a link or not a plain file: refused" % n, 2)


def copy_save(src, data, slot=None):
    """A save folder (account.sav, option.sav, slot_NN/) copied to where the game reads it in the data folder; with slot,
    that slot alone (so Continue loads it). The source is only read, and holds plain files and folders only (no links).
    Returns the destination"""
    import shutil
    src = os.path.abspath(src)
    if os.path.islink(src) or not os.path.isdir(src):
        raise Failed("--save: %s is not a folder" % src, 2)
    plain_tree(src)
    names = os.listdir(src)
    slots = sorted(n for n in names if n.startswith("slot_") and os.path.isdir(os.path.join(src, n)))
    if not slots or not ({"account.sav", "option.sav"} & set(names)):
        raise Failed("--save: %s does not look like a save folder (account.sav or option.sav, and slot_NN folders): "
                     "it is <data>/longshot/save/<user id>" % src, 2)
    if slot is not None:
        slot = slot_name(slot)
        if slot not in slots:
            raise Failed("--save: %s has no %s (it has %s)" % (src, slot, ", ".join(slots)), 2)
    dest = os.path.join(os.path.abspath(data), "longshot", "save", SAVE_USER)
    if os.path.exists(dest) and os.listdir(dest):
        raise Failed("--save: %s already holds saves: give an empty --data (or none: the run folder's own)" % dest, 2)
    os.makedirs(dest, exist_ok=True)
    for n in names:
        p = os.path.join(src, n)
        if n.startswith("slot_") and os.path.isdir(p):
            if slot is None or n == slot:
                shutil.copytree(p, os.path.join(dest, n), symlinks=True)   # (none: plain_tree; never followed)
        elif os.path.isfile(p):
            shutil.copy2(p, os.path.join(dest, n), follow_symlinks=False)
    return dest


def launch(a):
    """The game in GAME_DIR with HOS_CONTROL set, its socket in a run folder of its own; waits until it answers"""
    info, _ = start_game(a)
    print(json.dumps(info) if a.json else info["socket"])
    return 0


def start_game(a):
    """launch's work: the game started and answering; its socket, pid, run folder and log, and the process"""
    game = os.path.abspath(a.game_dir)
    exe = os.path.join(game, "longshot.exe" if WINDOWS else "longshot")
    if not os.path.isfile(exe):
        raise Failed("no game at %s" % exe, 2)
    run_dir = os.path.abspath(a.run) if a.run else tempfile.mkdtemp(prefix="longshot-control-",
                                                                     dir=os.environ.get("XDG_RUNTIME_DIR") or None)
    os.makedirs(run_dir, mode=0o700, exist_ok=True)
    sock = a.sock or ("longshot-control-%d" % os.getpid() if WINDOWS else os.path.join(run_dir, "control.sock"))
    if not WINDOWS:
        sock = os.path.abspath(sock)   # (the game runs in its own folder: a relative path would be bound there)
    env = dict(os.environ, HOS_CONTROL=sock)
    if a.slot and not a.save:
        raise Failed("--slot goes with --save", 2)
    data = a.data or (os.path.join(run_dir, "data") if a.save else None)
    if a.save:
        if WINDOWS:
            raise Failed("--save: Linux only (on Windows the game keeps its saves in its own folder)", 2)
        copy_save(a.save, data, a.slot)
    if data:
        env["XDG_DATA_HOME"] = os.path.abspath(data)
    if a.headless:
        env["HOS_HEADLESS"] = "1"
    for kv in a.env:
        if "=" not in kv:
            raise Failed("--env NAME=VALUE", 2)
        k, v = kv.split("=", 1)
        env[k] = v
    log = os.path.join(run_dir, "log.txt")
    with open(log, "wb") as out:
        kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS else {"start_new_session": True}
        p = subprocess.Popen([exe], cwd=game, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, **kw)
    deadline = time.monotonic() + a.timeout
    while True:
        if p.poll() is not None:
            raise Failed("the game ended (%d) before its bridge answered: %s" % (p.returncode, log), 2)
        try:
            b = Bridge(sock, 5)
            r = b.request("status")
            b.close()
            if r.get("ok"):
                break
        except Failed:
            pass
        if time.monotonic() > deadline:
            raise Failed("the game's bridge did not answer in %.0f s: %s" % (a.timeout, log), 3)
        time.sleep(0.2)
    return {"socket": sock, "pid": p.pid, "run": run_dir, "log": log}, p


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sock", help="the bridge's socket (default $HOS_CONTROL)")
    p.add_argument("--json", action="store_true", help="the replies as JSON lines")
    sub = p.add_subparsers(dest="command", required=True)

    def inputs(q):
        q.add_argument("--frames", type=int, default=8)
        q.add_argument("--at", type=int, default=0, help="frames from the next controller read")
        q.add_argument("--exclusive", action="store_true", help="the player's own devices left out meanwhile")
        q.add_argument("--no-wait", action="store_true", help="reply as soon as it is queued")
        q.add_argument("--timeout", type=float)

    q = sub.add_parser("launch", help="start a game folder with the bridge on")
    q.add_argument("game_dir")
    q.add_argument("--data", help="XDG_DATA_HOME for the run (its saves)")
    q.add_argument("--save", help="a save folder (<data>/longshot/save/<user id>) copied in first (Linux); the source is "
                                  "only read")
    q.add_argument("--slot", help="with --save: that slot alone (03 or slot_03), so Continue loads it")
    q.add_argument("--run", help="the run folder (socket, log, shots); default a new one")
    q.add_argument("--headless", action="store_true")
    q.add_argument("--env", action="append", default=[], metavar="NAME=VALUE")
    q.add_argument("--timeout", type=float, default=600, help="seconds to wait for the bridge (shaders may be prepared first)")
    sub.add_parser("status")
    q = sub.add_parser("press")
    q.add_argument("buttons", help="A, A+B, ZL+LUP, LX=-16000 ...")
    inputs(q)
    q = sub.add_parser("stick")
    q.add_argument("which", choices=["L", "R", "l", "r"])
    q.add_argument("x", type=float)
    q.add_argument("y", type=float)
    inputs(q)
    q = sub.add_parser("sequence")
    q.add_argument("script", help="<at>:<buttons>[/<frames>],...")
    q.add_argument("--hos", action="store_true", help="times in seconds and holds in ms, as HOS_INPUT_SCRIPT")
    q.add_argument("--exclusive", action="store_true")
    q.add_argument("--no-wait", action="store_true")
    q.add_argument("--timeout", type=float)
    q = sub.add_parser("release", help="let go of this client's queued inputs and those of clients gone")
    q.add_argument("--all", action="store_true", help="every client's, also those still connected")
    q = sub.add_parser("shot")
    q.add_argument("path", nargs="?")
    q.add_argument("--after", type=int, help="the first present after this frame")
    q.add_argument("--timeout", type=float)
    q = sub.add_parser("wait")
    q.add_argument("event", choices=["present", "frame", "in_play", "state", "loading_start", "loading_end", "change", "map"])
    q.add_argument("--frames", type=int)
    q.add_argument("--frame", type=int)
    q.add_argument("--state")
    q.add_argument("--map", help="map: until the game is in this map (default: any other map than now)")
    q.add_argument("--timeout", type=float, default=60)
    q = sub.add_parser("option")
    q.add_argument("action", choices=["get", "set"])
    q.add_argument("name", nargs="?")
    q.add_argument("value", nargs="?")
    q = sub.add_parser("ab", help="a live switch flipped while the game runs, and each value's numbers")
    q.add_argument("name", help="a live switch (option get lists them, tab live), such as RX_VK_OCC_QUERIES")
    q.add_argument("--every", type=int, default=10, help="seconds each way (measured, in each launch with --restart)")
    q.add_argument("--rounds", type=int, default=4, help="halves each way (launches each way with --restart)")
    q.add_argument("--restart", metavar="GAME_DIR", help="a switch that needs a restart: this game folder started once a "
                                                         "value, A B then B A, each launch measured between two marks")
    q.add_argument("--values", default="0,1", help="--restart: the switch's two values, A,B")
    q.add_argument("--teleport", help="--restart: (OoT) a destination's index or map, once in play")
    q.add_argument("--settle", type=float, default=15, help="--restart: seconds in the scene before measuring")
    q.add_argument("--start-timeout", type=float, default=600, help="--restart: seconds for a launch to answer, and to reach play")
    q.add_argument("--headless", action="store_true")
    q.add_argument("--env", action="append", default=[], metavar="NAME=VALUE", help="--restart: more for each launch")
    q.add_argument("--save", help="--restart: as launch's --save (Linux)")
    q.add_argument("--slot", help="--restart: as launch's --slot")
    q.add_argument("--run", help="--restart: the folder for the launches' run folders (default a new one)")
    q = sub.add_parser("watch")
    q.add_argument("--events", help="present,state,log,map (default present,state; with --log alone, log)")
    q.add_argument("--every", type=int, default=60)
    q.add_argument("--log", help="log lines that contain this text (adds log to the events)")
    q.add_argument("--count", type=int, help="stop after this many events")
    q = sub.add_parser("mem")
    q.add_argument("action", choices=["read", "write"])
    q.add_argument("addr", help="a guest address, decimal or 0x...")
    q.add_argument("size", nargs="?", help="read: how many bytes")
    q.add_argument("hex", nargs="?", help="write: the bytes as hex")
    q.add_argument("--format", default="hex", choices=["hex", "base64", "u8", "u16", "u32", "u64", "f32"],
                   help="read: how to print the bytes")
    q = sub.add_parser("teleport", help="(OoT) the debug teleport: a destination's index or map; none lists them")
    q.add_argument("to", nargs="?")
    q.add_argument("--timeout", type=float, default=10, help="seconds for the game to take it (it does in play)")
    q.add_argument("--arrive", type=float, metavar="SECS", help="then wait up to SECS until the game is in the map")
    q.add_argument("--no-wait", action="store_true", help="reply once asked, without whether the game took it")
    q = sub.add_parser("age", help="(OoT) the Debug tab's Switch age: child or adult (none: the other age)")
    q.add_argument("to", nargs="?", choices=["child", "adult"])
    q.add_argument("--timeout", type=float, default=10, help="seconds for the game to take it (it does in play)")
    q.add_argument("--arrive", type=float, metavar="SECS", help="then wait up to SECS until the map has reloaded (its "
                   "load ended)")
    q.add_argument("--no-wait", action="store_true", help="reply once asked, without whether the game took it")
    sub.add_parser("saves", help="the save folder the game uses: its files, newest slot first")
    q = sub.add_parser("slots", help="the save slots: last written, sizes, scene; * the newest (Continue's)")
    q.add_argument("--thumbs", metavar="DIR", help="each slot's file-select picture written there as slot_NN.jpg")
    sub.add_parser("quit")
    q = sub.add_parser("raw")
    q.add_argument("request", help="a JSON request object")
    a = p.parse_args(argv)
    try:
        return run(a)
    except Failed as e:
        print("longshot_ctl: %s" % e, file=sys.stderr)
        return e.code
    except (BrokenPipeError, ConnectionResetError):
        print("longshot_ctl: the game closed the connection", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print("longshot_ctl: not JSON: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
