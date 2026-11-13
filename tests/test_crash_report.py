"""The crash report the crash window shows and sends (nx2/crash_report.py, docs/crash-report-window.md). A made-up game folder
in a made-up home, no game:

  - the report holds exactly the relay's fields, each within the relay's limits, and the whole stays under its 128 KiB;
  - nothing from the game's memory: no register row, no memory row of the crash;
  - no home folder and no user name in any field (~, %USERPROFILE%, <user>), on Linux and Windows paths;
  - the settings: every one, defaults included, the changed ones first; never the bindings, the microphone's name, the
    hardware hashes or lines the game doesn't write itself; within 4000 characters with every row at its longest;
  - the release flag: true only for a release's version built from the release's own sources (the fingerprint in
    longshot_version.txt), false for a checkout, an edited build, a hand-set version or no first line;
  - the crashed run's log is found after the game started again (longshot_log.1.txt), a log without a crash gives no
    report and drops the marker, and no marker means no report;
  - Always send crash reports: off until the launcher ticks it, per game folder, and never one of the sent settings;
  - the device names the options file holds (the microphone's) and the audio log line's output device never reach the log
    lines; a user name word is redacted only as written (alice, not Alice in "Alice's");
  - a marker cut short or with a bad time is still a marker; an answer from the server that can't be read keeps the report;
  - the addresses come from nx2/addresses.ini (none in the code), and with none set nothing is sent and the report kept;
  - the window's rows (card), the settings' and the log's Show text: the same for the Tk and the Qt builders;
  - the report passes the relay's checks (Bean-Ops deploy/crash-relay/crash_relay.py parse, mirrored here: the version's
    characters, the CRASH: exception line with a module+offset place).

python -I -B tests/test_crash_report.py
"""
import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
import crash_report as cr  # noqa: E402

HOME = "/home/alice"
SOURCE = "763271ad"

LOG = """[hos host] longshot 3.4.1 (source %s)
[hos host] report: os Arch Linux (Linux 7.2.15)
[hos host] report: cpu AMD Ryzen 7 7800X3D (16 threads), 32 GB
[hos host] report: gpu AMD Radeon RX 7800 XT, RADV 26.0
[hos host] report: renderer Vulkan
[hos host] fs: saves in /home/alice/.local/share/longshot
[hos Main] map: Hyrule Field
[hos Main] map: Kokiri Forest (child)
[hos Main] opened /run/media/alice/Games/Longshot/user/save.dat
[hos Main] CRASH: exception 0xc0000005 (read 0x8, guest 0x8) at mkw.exe+0x1a2b3c
[hos Main] CRASH: host backtrace: mkw.exe+0x1a2b3c mkw.exe+0x100 libc.so.6+0x2a1ca
[hos Main] CRASH: thread Main, sp 0x7ffe0000
[hos Main] CRASH:   x0  0000000000000008  x1  00000000deadbeef  x2  0000000000000000  x3  0000000000000000
[hos Main] CRASH:   x28 0000000000000000  x29 0000000000001000  x30 0000000000002000  x31 0000000000000000
[hos Main] CRASH: guest backtrace: lr 0x2000 0x3000 0x4000
[hos Main] CRASH: [x0+0x000] 0000000000000001 0000000000000002 0000000000000003 0000000000000004
[hos Main] CRASH: [x19+0x020] 0000000000000005 0000000000000006 0000000000000007 0000000000000008
"""

OPTIONS = """output_res=2560x1440
vsync=1
resolution=3
frame_rate_cap=60
upscaling=2
microphone_device=Alice's Blue Yeti
key_a=4,0
pad_a=0
auto_for=1a2b3c4d5e6f7a8b
auto_tuned=1a2b3c4d5e6f7a8b
auto_output=2560x1440
auto_steps=2,0
memory_budget=0
options_version=6
my_note=/home/alice/somewhere
"""
DEFAULTS = """output_res=1920x1080
vsync=1
resolution=2
frame_rate_cap=60
upscaling=2
"""

# The relay's checks (crash_relay.py parse), mirrored
RELAY_LIMITS = {"version": 40, "os": 200, "renderer": 120, "gpu": 200, "cpu": 200, "map": 120, "options": 4000}
RELAY_VERSION = re.compile(r"^[0-9A-Za-z.+_-]{1,40}$")
RELAY_CRASH = re.compile(r"CRASH: exception (0x[0-9a-fA-F]+) \((read|write) [^)]*\) at "
                         r"([A-Za-z0-9_.-]{1,64}(?:\+0x[0-9a-fA-F]{1,16})?)(?=\s|$)")


class Folder:
    def __init__(self, log=LOG % SOURCE, options=OPTIONS, defaults=DEFAULTS, version="version=3.4.1\nsource=%s\n" % SOURCE,
                 marker=True, log_name="longshot_log.txt"):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = self.tmp.name
        for name, text in ((log_name, log), (cr.OPTIONS, options), (cr.DEFAULTS, defaults), (cr.VERSION_FILE, version)):
            if text is not None:
                Path(self.path, name).write_text(text, encoding="utf-8")
        if marker:
            Path(self.path, cr.MARKER).write_text("log=longshot_log.txt\ntime=%d\n" % time.time(), encoding="utf-8")

    def report(self):
        found = cr.pending(self.path)
        return None if found is None else cr.build(self.path, found[1], home=HOME, user="alice")

    def close(self):
        self.tmp.cleanup()


class CrashReportTest(unittest.TestCase):
    def setUp(self):
        self.folders = []

    def tearDown(self):
        for f in self.folders:
            f.close()

    def make(self, **kw):
        f = Folder(**kw)
        self.folders.append(f)
        return f

    def test_the_report_holds_the_relays_fields_within_its_limits(self):
        r = self.make().report()
        self.assertEqual(set(r), set(RELAY_LIMITS) | {"release", "crash", "log_tail"})
        for key, limit in RELAY_LIMITS.items():
            self.assertIsInstance(r[key], str)
            self.assertLessEqual(len(r[key]), limit, key)
        self.assertRegex(r["version"], RELAY_VERSION)
        self.assertTrue(any(RELAY_CRASH.search(line) for line in r["crash"]))
        self.assertLess(len(json.dumps(r).encode()), 128 * 1024)
        self.assertEqual((r["version"], r["os"], r["renderer"], r["map"]),
                         ("3.4.1", "Arch Linux (Linux 7.2.15)", "Vulkan", "Kokiri Forest (child)"))
        self.assertEqual(r["gpu"], "AMD Radeon RX 7800 XT, RADV 26.0")
        self.assertEqual(r["cpu"], "AMD Ryzen 7 7800X3D (16 threads), 32 GB")
        self.assertEqual(cr.summary(r), "0xc0000005 (read) at mkw.exe+0x1a2b3c")

    def test_nothing_from_the_games_memory(self):
        r = self.make().report()
        self.assertEqual(r["crash"], [
            "CRASH: exception 0xc0000005 (read 0x8, guest 0x8) at mkw.exe+0x1a2b3c",
            "CRASH: host backtrace: mkw.exe+0x1a2b3c mkw.exe+0x100 libc.so.6+0x2a1ca",
            "CRASH: thread Main, sp 0x7ffe0000",
            "CRASH: guest backtrace: lr 0x2000 0x3000 0x4000"])
        text = json.dumps(r)
        self.assertNotIn("deadbeef", text)
        self.assertNotIn("[x0+", text)
        self.assertNotIn("[x19+", text)

    def test_no_home_folder_or_user_name(self):
        r = self.make().report()
        text = json.dumps(r)
        self.assertNotIn("alice", text.lower())
        self.assertIn("~/.local/share/longshot", text)
        self.assertIn("/run/media/<user>/Games", text)
        self.assertEqual(cr.redact(r"C:\Users\Bob\AppData\Roaming\Longshot", home="", user=""), r"%USERPROFILE%\AppData\Roaming\Longshot")
        self.assertEqual(cr.redact("/Users/carol/Library", home="", user=""), "~/Library")
        # (a user name anywhere as a word of its own, as in a temporary folder named after the home: not inside a word)
        self.assertEqual(cr.redact("/tmp/x-1000/-home-alice-Desktop/tools/longshot", home=HOME, user="alice"),
                         "/tmp/x-1000/-home-<user>-Desktop/tools/longshot")
        self.assertEqual(cr.redact("Alice's malice", home=HOME, user="alice"), "<user>'s malice")   # (in any case)
        # (a name that is also a word of the report's lines, in its own case only: the Steam Deck's user is deck, and its
        # report must still say Steam Deck)
        self.assertEqual(cr.redact("Steam Deck, deck's", home="/home/deck", user="deck"), "Steam Deck, <user>'s")
        self.assertEqual(cr.redact("bob ran", home="/home/bo", user="bo"), "bob ran")   # (too short to tell)

    def test_the_log_tail_is_what_came_before_the_crash(self):
        lines = (LOG % SOURCE).splitlines()
        many = lines[:1] + ["[hos Main] line %d" % k for k in range(400)] + lines[1:]
        r = self.make(log="\n".join(many)).report()
        self.assertEqual(len(r["log_tail"]), cr.TAIL_LINES)
        self.assertFalse(any("CRASH:" in line for line in r["log_tail"]))
        self.assertTrue(r["log_tail"][-1].endswith("save.dat"))

    def test_every_setting_defaults_included_changed_first(self):
        f = self.make()
        rows = cr.settings(f.path)
        self.assertEqual([k for k, _, changed in rows if changed], ["output_res", "resolution"])
        self.assertEqual(rows[0][:2], ("output_res", "2560x1440"))
        options = f.report()["options"].splitlines()
        self.assertIn("vsync=1", options)   # (a default: sent too)
        self.assertIn("frame_rate_cap=60", options)
        self.assertIn("auto_steps=2,0", options)
        self.assertIn("options_version=6", options)
        for left_out in ("microphone_device", "key_a", "pad_a", "auto_for", "auto_tuned", "my_note"):
            self.assertFalse(any(o.startswith(left_out + "=") for o in options), left_out)

    def test_without_an_options_file_the_defaults_are_sent(self):
        options = self.make(options=None).report()["options"].splitlines()
        self.assertEqual(sorted(options), sorted(DEFAULTS.split()))

    def test_the_settings_fit_with_every_row_at_its_longest(self):
        many = "\n".join("a_setting_with_a_long_name_%03d=%s" % (k, "65535x65535" if k % 2 else "-2147483648")
                         for k in range(140))
        options = self.make(options=many).report()["options"]
        self.assertLessEqual(len(options), cr.FIELD_LIMITS["options"])
        self.assertTrue(options.endswith(("x65535", "648")))   # (whole lines only)

    def test_the_release_flag(self):
        self.assertTrue(self.make().report()["release"])
        edited = LOG % "0badc0de"   # (a source file changed: another fingerprint)
        self.assertFalse(self.make(log=edited).report()["release"])
        self.assertFalse(self.make(version="version=3.4.1\n").report()["release"])   # (no source=: a checkout)
        for name in ("v3.0.0-3-g1a2b3c4", "dev", "from-env", "3.4.1-dirty"):
            log = (LOG % SOURCE).replace("longshot 3.4.1 ", "longshot %s " % name)
            self.assertFalse(self.make(log=log).report()["release"], name)
        for name in ("v3.4.1", "3.5.0-rc.2", "nightly-412-1a2b3c4d"):
            log = (LOG % SOURCE).replace("longshot 3.4.1 ", "longshot %s " % name)
            self.assertTrue(self.make(log=log).report()["release"], name)
        no_first_line = "\n".join((LOG % SOURCE).splitlines()[1:])
        r = self.make(log=no_first_line).report()
        self.assertFalse(r["release"])
        self.assertEqual(r["version"], "unknown")

    def test_a_library_place_is_the_modules_name(self):
        # (Linux logs the library's path: the relay takes module+offset, and a path would have the report refused)
        for logged in ("/usr/lib/libc.so.6+0xb5a22", "C:\\Windows\\System32\\ntdll.dll+0x9f2c4", "/home/alice/x/libSDL3.so.0+0x55bc5"):
            log = (LOG % SOURCE).replace("at mkw.exe+0x1a2b3c\n", "at %s\n" % logged, 1)
            r = self.make(log=log).report()
            self.assertTrue(any(RELAY_CRASH.search(line) for line in r["crash"]), logged)
            self.assertTrue(cr.summary(r).endswith(" at " + re.split(r"[/\\]", logged)[-1]), cr.summary(r))

    def test_a_place_in_no_module_is_unknown(self):
        # (a jump to a bad address: Windows logs "at ?+0x10", which the relay would refuse, 400 no CRASH: exception line)
        for logged, sent in (("?+0x10", "unknown+0x10"), ("<anon>+0x7f00", "anon+0x7f00"), ("mkw.exe+0x1a2b3c", "mkw.exe+0x1a2b3c")):
            log = (LOG % SOURCE).replace("at mkw.exe+0x1a2b3c\n", "at %s\n" % logged, 1)
            r = self.make(log=log).report()
            self.assertTrue(any(RELAY_CRASH.search(line) for line in r["crash"]), logged)
            self.assertTrue(cr.summary(r).endswith(" at " + sent), cr.summary(r))

    def test_a_version_the_relay_would_refuse_is_made_safe(self):
        log = (LOG % SOURCE).replace("longshot 3.4.1 ", "longshot v1;rm&x%PATH% ")
        self.assertRegex(self.make(log=log).report()["version"], RELAY_VERSION)

    def test_the_crashed_runs_log_after_the_game_started_again(self):
        f = self.make(log_name="longshot_log.1.txt")
        Path(f.path, "longshot_log.txt").write_text("[hos host] longshot 3.4.1 (source %s)\n[hos Main] map: Lost Woods\n" % SOURCE)
        r = f.report()
        self.assertEqual(r["map"], "Kokiri Forest (child)")

    def test_no_crash_in_the_log_no_report_and_the_marker_goes(self):
        f = self.make(log="[hos host] longshot 3.4.1 (source %s)\n[hos Main] map: Lost Woods\n" % SOURCE)
        self.assertIsNone(f.report())
        self.assertFalse(Path(f.path, cr.MARKER).exists())

    def test_no_marker_no_report(self):
        self.assertIsNone(self.make(marker=False).report())

    def test_always_send_is_the_players_choice_in_that_folder(self):
        f = self.make()
        self.assertFalse(cr.always_send(f.path))   # (off until ticked)
        self.assertTrue(cr.set_always_send(f.path, True))
        self.assertTrue(cr.always_send(f.path))
        self.assertTrue(cr.set_always_send(f.path, False))
        self.assertFalse(cr.always_send(f.path))
        self.assertNotIn("always_send", f.report()["options"])   # (not one of the game's settings)

    def test_the_sent_pages_link_is_the_reports_comment(self):
        mine = cr.ISSUE_URL + "#issuecomment-12524"
        self.assertEqual(cr.comment_link(mine), mine)   # (the comment the relay posted or added this crash to)
        for other in ("", None, "https://example.com/x", cr.ISSUE_URL + "#issuecomment-1x", "javascript:alert(1)",
                      "https://git.magicbean.dev/MagicBeanDev/Longshot/issues/8#issuecomment-1"):
            self.assertEqual(cr.comment_link(other), cr.ISSUE_URL, other)

    def test_clear_drops_only_that_crashs_marker(self):
        f = self.make()
        when = cr.marker_time(f.path)
        Path(f.path, cr.MARKER).write_text("log=longshot_log.txt\ntime=%d\n" % (int(when) + 60), encoding="utf-8")
        cr.clear(f.path, when)   # (a window opened for the crash before)
        self.assertTrue(Path(f.path, cr.MARKER).exists())
        cr.clear(f.path, cr.marker_time(f.path))
        self.assertFalse(Path(f.path, cr.MARKER).exists())

    def test_an_old_log_is_not_this_crash(self):
        f = self.make()
        old = time.time() - 3600
        os.utime(Path(f.path, "longshot_log.txt"), (old, old))
        self.assertIsNone(f.report())


    def test_device_names_never_reach_the_log(self):
        log = LOG % SOURCE
        log = log.replace("[hos Main] map: Hyrule Field\n", "[hos Main] map: Hyrule Field\n"
                          "[hos audio] audio: output on Speakers (Realtek(R) Audio) (2 devices)\n"
                          "[hos audio] mic: Alice's Blue Yeti opened\n")
        r = self.make(log=log).report()
        text = "\n".join(r["log_tail"] + r["crash"])
        self.assertNotIn("Blue Yeti", text)
        self.assertNotIn("Realtek", text)
        self.assertIn("audio: output on <device> (2 devices)", text)
        self.assertIn("mic: <device> opened", text)

    def test_a_marker_cut_short_is_still_a_marker(self):
        f = self.make()
        Path(f.path, cr.MARKER).write_bytes(b"log=longshot_log.txt\ntime=17\xff")
        self.assertIsNotNone(cr.read_marker(f.path))
        self.assertIsNotNone(f.report())

    def test_what_went_wrong_before_an_answer_keeps_the_report(self):
        import urllib.request
        real = urllib.request.urlopen

        def broken(*a, **kw):
            raise ValueError("not an answer")
        urllib.request.urlopen = broken
        try:
            ok, said, keep = cr.send({"version": "3.4.1"})
        finally:
            urllib.request.urlopen = real
        self.assertFalse(ok)
        self.assertTrue(keep)
        self.assertIn("kept", said)

    def test_a_posted_report_with_an_answer_that_cant_be_read_is_sent(self):
        """2xx: the relay posted it, so it is never offered (or sent by Always send) again, whatever the body"""
        import io
        import urllib.request
        real = urllib.request.urlopen
        for body in (b"<html>cut", b"[1, 2]", b'{"comment": "https://x/issues/81#issuecomment-5"}'):
            class Answer(io.BytesIO):
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False
            urllib.request.urlopen = lambda *a, b=body, **kw: Answer(b)
            try:
                ok, said, keep = cr.send({"version": "3.4.1"}, url="https://relay.invalid/crash")
            finally:
                urllib.request.urlopen = real
            self.assertEqual((ok, keep), (True, False), body)

    def test_a_microphone_picked_earlier_never_reaches_the_log(self):
        for line in ("audio in: Bob's Blue Yeti open (48000 Hz, 1 channel)", "audio in: Bob's Blue Yeti is back",
                     "audio in: Bob's Blue Yeti not found: no such device, tried again from time to time"):
            self.assertNotIn("Yeti", cr.clean("[hos audio] " + line, cr.LINE_LIMIT), line)

    def test_addresses_ini_with_a_bom_or_broken_never_stops_the_builders(self):
        good = Path(cr.ADDRESSES).read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp, "addresses.ini")
            ini.write_bytes(b"\xef\xbb\xbf" + good)
            self.assertEqual(cr.address("crash_reports", str(ini)), cr.ENDPOINT)
            ini.write_bytes(good + b"\ncrash_reports = again\n")   # (a key twice: configparser refuses the file)
            self.assertEqual(cr.address("crash_reports", str(ini)), "")
            ini.write_bytes(b"\xff\xfe not text")
            self.assertEqual(cr.address("crash_reports", str(ini)), "")

    def test_the_windows_say_where_from_crash_issue(self):
        self.assertEqual(cr.issue_place(), "git.magicbean.dev, on issue #81")
        self.assertEqual(cr.issue_number(), "81")

    def test_the_cpu_row_has_the_ram_as_the_game_words_it(self):
        self.assertRegex(cr.this_cpu(), r"\(\d+ threads\), \d+ GB$")

    def test_the_windows_rows_and_show_texts(self):
        f = self.make()
        r = f.report()
        rows = cr.settings(f.path)
        card = cr.card(r, rows)
        self.assertEqual([k for k, _v, _s in card], ["Version", "Release build", "Operating system", "Renderer",
                                                     "Graphics card", "CPU and RAM", "Map", "Settings", "Crash", "Log"])
        self.assertEqual({k: s for k, _v, s in card if s}, {"Settings": "settings", "Crash": "log", "Log": "log"})
        changed = sum(1 for _k, _v, c in rows if c)
        self.assertIn("%d, %d changed" % (len(rows), changed), dict((k, v) for k, v, _s in card)["Settings"])
        text = cr.settings_text(rows)
        self.assertEqual(len(text.splitlines()), len(rows))
        self.assertIn("output_res=2560x1440    (changed)", text)
        self.assertEqual(cr.settings_text([]), "(none)")
        log = cr.log_text(r)
        self.assertTrue(log.startswith(r["crash"][0]))
        self.assertIn("--- the %d log lines before it ---" % len(r["log_tail"]), log)

    def test_the_addresses_are_in_addresses_ini(self):
        self.assertTrue(cr.ENDPOINT.startswith("https://") and cr.ENDPOINT.endswith("/crash"))
        self.assertTrue(cr.ISSUE_URL.startswith("https://") and cr.ISSUE_URL.endswith("/issues/81"))
        source = (ROOT / "nx2" / "crash_report.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"[\"']https?://", "an address in the code: it goes in nx2/addresses.ini")
        self.assertEqual(cr.address("no_such_service"), "")
        ok, said, keep = cr.send({"version": "3.4.1"}, url="")
        self.assertEqual((ok, keep), (False, True))
        self.assertIn("addresses.ini", said)

if __name__ == "__main__":
    unittest.main()
