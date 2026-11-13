"""A crash report from a game folder, for the crash window (docs/crash-report-window.md): what the builders show the player
after a crash, and send only when they press Send Help. No UI here: the Linux and Windows builders and the tests share it.

The report is built from what is on disk, never from the crashed process:

- the log of the run that crashed (longshot_log.txt, or longshot_log.1.txt once the game started again: crash_pending.txt
  says when the crash was), its first line (the version and its source fingerprint), its report: lines (the system, the
  CPU, the GPU, the renderer: the game logs them at startup), its last map: line (before any, "Preparing shaders" or
  "Title and intro") and its CRASH: lines;
- longshot_options.ini: every setting, defaults included; longshot_defaults.ini, when the game wrote it, says which
  differ;
- longshot_version.txt's source= line (a release's fingerprint, from its signed VERSION file) for the release flag.

The launcher's "Always send crash reports" tick (longshot_crash.ini, always_send=1, in the game folder) is the player's
answer given once: the window still opens and still shows what was sent, but sends without waiting for Send Help.

What it never holds: the register and memory rows of the crash (the game's memory), the bindings, the microphone's name,
the hardware hashes, anything else from the game folder, and the user's name in paths (~, %USERPROFILE%, <user>).

    python -I nx2/crash_report.py <game folder>          prints the report the window would show, sends nothing
    python -I nx2/crash_report.py <game folder> --send   sends it (a test: the window is the way players send)
"""
import configparser
import json
import os
import platform
import re
import sys
import time
import urllib.error
import urllib.request



ADDRESSES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "addresses.ini")


def address(key, path=ADDRESSES):
    """A service's address from nx2/addresses.ini (kept out of the code, to edit in one place); "" when it isn't there"""
    ini = configparser.ConfigParser(interpolation=None)
    try:   # (utf-8-sig: an editor's BOM; a file that doesn't parse turns the uses off, it never stops the builders)
        ini.read(path, encoding="utf-8-sig")
        return ini.get("addresses", key, fallback="").strip()
    except (configparser.Error, UnicodeDecodeError):
        return ""


ENDPOINT = address("crash_reports")
ISSUE_URL = address("crash_issue")   # (where the reports are: comment_link)
MARKER = "crash_pending.txt"   # (the game's crash handler writes it: log=<file name>, time=<unix seconds>)
LOGS = ["longshot_log.txt"] + ["longshot_log.%d.txt" % k for k in range(1, 5)]
OPTIONS, DEFAULTS, VERSION_FILE = "longshot_options.ini", "longshot_defaults.ini", "longshot_version.txt"
CHOICE = "longshot_crash.ini"   # (the launcher's Always send crash reports tick: always_send=1)

# The relay's limits (Bean-Ops deploy/crash-relay/crash_relay.py): a report past them is cut there anyway
FIELD_LIMITS = {"version": 40, "os": 200, "renderer": 120, "gpu": 200, "cpu": 200, "map": 120, "options": 4000}
LINE_LIMIT, CRASH_LINES, TAIL_LINES = 300, 80, 150
VERSION_OK = re.compile(r"[^0-9A-Za-z.+_-]")

FIRST_LINE = re.compile(r"\blongshot (\S+)(?: \(source ([0-9a-f]+)\))?")
REPORT_LINE = re.compile(r"\breport: (os|cpu|gpu|renderer) (.+)")
MAP_LINE = re.compile(r"\bmap: (.+)")
# (before the first map: the shaders' preparation, from its first progress line to "the shaders took", then the title and
# intro; the same names as the performance summary's, #206)
PREPARING_SHADERS, TITLE_AND_INTRO = "Preparing shaders", "Title and intro"
PREP_LINE = re.compile(r"\bvk: (?:start-up: |preparation: |preparing program |shader preparation )")
PREP_END = re.compile(r"\bvk: (?:start-up: the shaders took |preparation: done|shader preparation stopped )")
CRASH_START = re.compile(r"CRASH: exception 0x[0-9a-fA-F]+ ")
CRASH_LINE = re.compile(r"(?:^|\] )(CRASH: .*)")
# (a place in a library is logged with its path on Linux, "at /usr/lib/libc.so.6+0xb5a22": the relay takes the module's name)
PLACE_PATH = re.compile(r"(?<= at )\S*[/\\](?=[^/\\\s]+\+0x)")
# (and a place in no module, "at ?+0x10" (a jump to a bad address): the relay takes [A-Za-z0-9_.-] names, so "unknown")
PLACE_MODULE = re.compile(r"(?<= at )([^\s+]*)(?=\+0x[0-9a-fA-F]+(?:\s|$))")


def place_module(m):
    name = re.sub(r"[^A-Za-z0-9_.-]", "", m.group(1))[:64]
    return name or "unknown"
# (the rows that hold the game's memory: the registers, and the memory x0, x1 and x19 point to)
MEMORY_ROWS = re.compile(r"CRASH: +x\d|CRASH: \[x\d+\+")
# A release's version (docs/versioning.md): a stable, a release candidate, a nightly. Anything else is not a release
RELEASE_VERSION = re.compile(r"^v?\d+\.\d+\.\d+(?:-rc\.\d+)?$|^nightly-\d+-[0-9a-f]{8}$")
# The settings a report holds: the menu's rows (a number or a size) and these four; never the bindings, the microphone's
# name or the hardware hashes (auto_for, auto_tuned)
SETTING = re.compile(r"^([a-z0-9_]+)=(-?\d+|\d+x\d+|\d+,\d+)$")
NOT_SENT = re.compile(r"^(key_|pad_)|^(microphone_device|auto_for|auto_tuned)$")

HOMES = [(re.compile(r"/home/[^/\s]+"), "~"), (re.compile(r"/var/home/[^/\s]+"), "~"), (re.compile(r"/Users/[^/\s]+"), "~"),
         (re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s]+", re.I), "%USERPROFILE%")]
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


# Words of the report's own lines (the system, the CPU, the GPU, the renderer) that are also user names
COMMON_WORDS = {"deck", "steam", "arch", "linux", "ubuntu", "fedora", "debian", "mint", "manjaro", "bazzite", "nobara",
                "cachyos", "windows", "wine", "vulkan", "intel", "amd", "nvidia", "radeon", "game", "games", "gamer",
                "user", "admin", "player"}


def redact(text, home=None, user=None):
    """The user's home and name out of a line, as the game's bug report does (gpu/vk/vk_bug_report.cpp redact), then any
    home folder by its shape"""
    home = (os.path.expanduser("~") if home is None else home).rstrip("/\\")
    if user is None:
        user = os.path.basename(home)
    if len(home) > 1:
        text = re.sub(r"(?<![\w/\\])" + re.escape(home) + r"(?![\w])", "~", text)
    if user:
        text = re.sub(r"(?<=[/\\])" + re.escape(user) + r"(?![\w])", "<user>", text)
    if user and len(user) >= 3:   # (and as a word of its own anywhere, in any case, as in a folder named -home-alice- or
        # "Alice's PC": not inside a longer word, and not a name so short it is a common one. A user name that is also a
        # word the report's own lines use (a Steam Deck's user is deck) is matched in its own case only, so the system
        # line keeps its "Steam Deck" and "Arch Linux")
        flags = 0 if user.lower() in COMMON_WORDS else re.I
        text = re.sub(r"(?<![A-Za-z0-9])" + re.escape(user) + r"(?![A-Za-z0-9])", "<user>", text, flags=flags)
    for pattern, replacement in HOMES:
        text = pattern.sub(replacement, text)
    return text


# The names of the player's devices in the log (host/nn_audio.c: "audio: output on Jane's AirPods (3 devices), stereo"): a
# name the player gave them, so never sent. The microphone's is the options file's microphone_device (devices())
DEVICE_LINES = [(re.compile(r"(audio: output on ).*?( \(\d+ devices?\))"), r"\1<device>\2"),
                # (host/nn_audio_in.c: the microphone by name, "audio in: <name> open (48000 Hz, ...)", "... is back", "...
                # <why>: ..., tried again": a microphone picked earlier in the session is not the options file's now)
                (re.compile(r"(audio in: ).+?( open \(\d+ Hz)"), r"\1<device>\2"),
                (re.compile(r"(audio in: ).+?( is back)$"), r"\1<device>\2"),
                (re.compile(r"(audio in: ).+?(: .*, tried again from time to time)$"), r"\1<device>\2")]


def clean(text, limit, home=None, user=None, devices=()):
    text = CONTROL.sub("", text)
    for pattern, replacement in DEVICE_LINES:
        text = pattern.sub(replacement, text)
    for name in devices:
        text = text.replace(name, "<device>")
    return redact(text, home, user).strip()[:limit]


def devices(folder):
    """The device names the options file holds (microphone_device=<name>), to leave out of every line"""
    names = []
    for line in read_lines(os.path.join(folder, OPTIONS)) or []:
        if line.startswith("microphone_device="):
            name = line.split("=", 1)[1].strip()
            if len(name) >= 3:
                names.append(name)
    return names


def read_lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return None


def read_marker(folder):
    """crash_pending.txt's lines, None without it. A marker cut short (the disk full, the power lost while the crash handler
    wrote it) is still a marker: what can't be read is left out, and pending() decides from the logs"""
    try:
        with open(os.path.join(folder, MARKER), encoding="utf-8", errors="replace") as f:
            return dict(line.rstrip("\n").split("=", 1) for line in f if "=" in line)
    except OSError:
        return None


def crash_log(folder, marker):
    """The log of the run that crashed: the newest of longshot_log.txt and the rotated ones that has a crash in it and was
    last written around the marker's time (the game renames longshot_log.txt to .1 when it starts again)"""
    try:
        when = float(marker.get("time") or 0)
    except ValueError:   # (a marker cut short: any crash log will do)
        when = 0
    for name in LOGS:
        path = os.path.join(folder, name)
        try:
            written = os.path.getmtime(path)
        except OSError:
            continue
        if when and written < when - 120:
            continue
        lines = read_lines(path)
        if lines and any(CRASH_START.search(line) for line in lines):
            return path, lines
    return None, None


def pending(folder):
    """The crash waiting for the player's answer: (log path, its lines), or None (no marker, or its log has no crash: a
    process killed from outside, which has nothing to report, and the marker is dropped)"""
    marker = read_marker(folder)
    if marker is None:
        return None
    path, lines = crash_log(folder, marker)
    if path is None:
        clear(folder)
        return None
    return path, lines


def marker_time(folder):
    """Which crash the marker is about (its time= line), None without one: a window keeps it, to clear only that crash's"""
    marker = read_marker(folder)
    return marker.get("time") if marker else None


def clear(folder, when=None):
    """Drops the marker. With when (marker_time when the window opened), only that crash's: a window left open does not
    drop the marker of a crash that came after it"""
    if when is not None and marker_time(folder) != when:
        return
    try:
        os.remove(os.path.join(folder, MARKER))
    except OSError:
        pass


def always_send(folder):
    """The player ticked Always send crash reports in this folder's launcher"""
    return "always_send=1" in [line.strip() for line in read_lines(os.path.join(folder, CHOICE)) or []]


def set_always_send(folder, on):
    try:
        with open(os.path.join(folder, CHOICE), "w", encoding="utf-8", newline="\n") as f:
            f.write("always_send=%d\n" % bool(on))
        return True
    except OSError:
        return False


def read_settings(path):
    lines = read_lines(path) or []
    out = []
    for line in lines:
        m = SETTING.match(line.strip())
        if m and not NOT_SENT.search(m.group(1)):
            out.append((m.group(1), m.group(2)))
    return out


def settings(folder):
    """Every setting, defaults included: [(key, value, changed)], the changed ones first. Without an options file yet (a
    crash before the first save) the defaults are the settings"""
    current = read_settings(os.path.join(folder, OPTIONS))
    defaults = dict(read_settings(os.path.join(folder, DEFAULTS)))
    if not current:
        current = sorted(defaults.items())
    rows = [(k, v, k in defaults and defaults[k] != v) for k, v in current]
    return sorted(rows, key=lambda r: (not r[2], r[0]))


def this_os():
    """The system, as the game's report: line words it (gpu/vk/vk_bug_report.cpp os_text), for a run whose log has none (the
    Vulkan renderer never started: no driver, or WebGPU). The window runs on the PC that crashed"""
    if sys.platform == "win32":
        v = sys.getwindowsversion()
        name = ("Windows 11" if v.build >= 22000 else "Windows 10") if v.major == 10 else "Windows %d.%d" % (v.major, v.minor)
        text = "%s (build %d)" % (name, v.build)
        try:   # (", Wine x", as os_text says it)
            import ctypes
            wine = ctypes.WinDLL("ntdll").wine_get_version
            wine.restype = ctypes.c_char_p
            text += ", Wine " + wine().decode()
        except (AttributeError, OSError, ImportError):
            pass
        return text
    name = "Linux"
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        for line in read_lines(path) or []:
            if line.startswith("PRETTY_NAME="):
                name = line[12:].strip().strip("\"'") or name
        if name != "Linux":
            break
    text = "%s (Linux %s)" % (name, platform.release())
    desktop, session = os.environ.get("XDG_CURRENT_DESKTOP"), os.environ.get("XDG_SESSION_TYPE")
    if desktop:   # (the desktop and its display protocol, then the AppImage, as os_text)
        text += ", %s%s" % (desktop, " (%s)" % session if session else "")
    if os.environ.get("APPIMAGE"):
        text += ", the AppImage"
    return text


def this_cpu():
    """The processor's name, threads and RAM, as the report: line words it (cpu_text), for a run whose log has none"""
    name = ""
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                name = winreg.QueryValueEx(k, "ProcessorNameString")[0]
        except OSError:
            pass
    else:
        for line in read_lines("/proc/cpuinfo") or []:
            if line.startswith("model name"):
                name = line.split(":", 1)[1]
                break
    name = " ".join(name.split()) or "an unknown CPU"
    ram = ram_bytes()
    return "%s (%d threads)%s" % (name, os.cpu_count() or 1, ", %d GB" % ((ram + (1 << 29)) >> 30) if ram else "")


def ram_bytes():
    """The PC's RAM, 0 when it can't be read"""
    if sys.platform == "win32":
        try:
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong),
                            ("page_avail", ctypes.c_ulonglong), ("virtual_total", ctypes.c_ulonglong),
                            ("virtual_avail", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]
            s = Status()
            s.length = ctypes.sizeof(s)
            return s.total if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s)) else 0
        except (AttributeError, OSError):
            return 0
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return 0


def release_source(folder):
    for line in read_lines(os.path.join(folder, VERSION_FILE)) or []:
        if line.startswith("source="):
            return line[7:].strip()
    return ""


def build(folder, lines, home=None, user=None):
    """The report, as the relay takes it, from the crashed run's log lines and the game folder"""
    names = devices(folder)
    version, source = "", ""
    for line in lines[:5]:
        m = FIRST_LINE.search(line)
        if m:
            version, source = m.group(1), m.group(2) or ""
            break
    start = next(i for i, line in enumerate(lines) if CRASH_START.search(line))
    found = {}
    place = TITLE_AND_INTRO
    for line in lines[:start]:
        m = REPORT_LINE.search(line)
        if m:
            found[m.group(1)] = m.group(2)
        m = MAP_LINE.search(line)
        if m:
            place = m.group(1)
        elif PREP_END.search(line):
            place = TITLE_AND_INTRO
        elif PREP_LINE.search(line) and place == TITLE_AND_INTRO:
            place = PREPARING_SHADERS
    crash = []
    for line in lines[start:]:
        m = CRASH_LINE.search(line)
        if m and not MEMORY_ROWS.search(m.group(1)):
            line = PLACE_PATH.sub("", m.group(1))
            if CRASH_START.search(line):
                line = PLACE_MODULE.sub(place_module, line, count=1)
            crash.append(clean(line, LINE_LIMIT, home, user, names))
    rows = settings(folder)
    options = ""
    for key, value, _changed in rows:
        if len(options) + len(key) + len(value) + 2 > FIELD_LIMITS["options"]:
            break
        options += "%s=%s\n" % (key, value)
    expected = release_source(folder)
    report = {
        "version": (VERSION_OK.sub("_", version) or "unknown")[:FIELD_LIMITS["version"]],
        # (a release's version, and the sources it was built from are the release's own: an edit changes the fingerprint)
        "release": bool(RELEASE_VERSION.match(version) and expected and source == expected),
        "os": clean(found.get("os") or this_os(), FIELD_LIMITS["os"], home, user),
        "renderer": clean(found.get("renderer", ""), FIELD_LIMITS["renderer"], home, user),
        "gpu": clean(found.get("gpu", ""), FIELD_LIMITS["gpu"], home, user),
        "cpu": clean(found.get("cpu") or this_cpu(), FIELD_LIMITS["cpu"], home, user),
        "map": clean(place, FIELD_LIMITS["map"], home, user),
        "crash": crash[:CRASH_LINES],
        "log_tail": [clean(line, LINE_LIMIT, home, user, names) for line in lines[max(0, start - TAIL_LINES):start]],
        "options": options.rstrip("\n"),
    }
    return report


def summary(report):
    """The crash in one line, as the window's Crash row and the relay's heading say it"""
    m = re.search(r"CRASH: exception (0x[0-9a-fA-F]+) \((read|write) [^)]*\) at (\S+)", report["crash"][0] if report["crash"] else "")
    return "%s (%s) at %s" % m.groups() if m else "unknown"


def card(report, rows):
    """The window's "What the report holds" rows, the same in both builders: (name, value, what Show opens: None,
    "settings" or "log"). rows are settings()'s."""
    changed = sum(1 for _k, _v, c in rows if c)
    return [("Version", report["version"], None),
            ("Release build", "Yes" if report["release"] else "No (a local or modified build)", None),
            ("Operating system", report["os"] or "not known", None),
            ("Renderer", report["renderer"] or "not known", None),
            ("Graphics card", report["gpu"] or "not known", None),
            ("CPU and RAM", report["cpu"] or "not known", None),
            ("Map", report["map"] or "not known", None),
            ("Settings", "%d, %d changed from the defaults" % (len(rows), changed), "settings"),
            ("Crash", summary(report), "log"),
            ("Log", "%d lines before the crash" % len(report["log_tail"]), "log")]


def settings_text(rows):
    """The settings' Show: one key=value a line, the changed ones marked"""
    return "\n".join("%s=%s%s" % (k, v, "    (changed)" if changed else "") for k, v, changed in rows) or "(none)"


def log_text(report):
    """The crash's and the log's Show: the crash lines, then the log lines before it"""
    return "\n".join(report["crash"] + ["", "--- the %d log lines before it ---" % len(report["log_tail"])] +
                     report["log_tail"])


def issue_place():
    """Where the reports are posted, for the windows' text: "git.magicbean.dev, on issue #81" from crash_issue, "" without
    one"""
    m = re.match(r"https?://([^/]+)/.*/issues/(\d+)$", ISSUE_URL)
    return "%s, on issue #%s" % m.groups() if m else ""


def issue_number():
    """crash_issue's number, as the Sent page's link says it ("See it on #81"); "" without one"""
    m = re.search(r"/issues/(\d+)$", ISSUE_URL)
    return m.group(1) if m else ""


def comment_link(link):
    """Where the Sent page's link goes: the report's own comment on #81 (the relay's answer, the comment it posted or added
    this crash to), else #81 itself; never anywhere else, whatever the answer says"""
    if ISSUE_URL and link and link.startswith(ISSUE_URL + "#issuecomment-") and link[len(ISSUE_URL) + 14:].isdigit():
        return link
    return ISSUE_URL


def send(report, url=ENDPOINT, timeout=10):
    """Posts the report: (True, the comment's link, False), or (False, why, for the player, whether to keep it for next
    time: yes when the server could not be reached or was busy, no when it refused the report, which sending again
    would not change)"""
    if not url:   # (no crash_reports in nx2/addresses.ini)
        return False, "No crash server is set (nx2/addresses.ini). Your report is kept, and offered again next time.", True
    req = urllib.request.Request(url, data=json.dumps(report).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "longshot-crash-report"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            try:   # (2xx: the relay posted it. An answer cut short or not its JSON still means sent: no link, never again)
                answer = json.loads(resp.read() or "{}")
                return True, answer.get("comment", "") if isinstance(answer, dict) else "", False
            except Exception:
                return True, "", False
    except urllib.error.HTTPError as e:
        try:
            why = json.loads(e.read() or "{}").get("error", "")
        except ValueError:
            why = ""
        if e.code == 429:
            return False, "The crash server is busy. Your report is kept, and offered again next time.", True
        return False, "The crash server refused the report (%d%s)." % (e.code, ": " + why if why else ""), False
    except (urllib.error.URLError, OSError) as e:
        return False, "Couldn't reach the crash server (%s). Your report is kept, and offered again next time." % (
            getattr(e, "reason", e)), True
    except Exception as e:   # (before any answer, anything else that went wrong: the windows wait for an answer, never a
        # thread that ends without saying)
        return False, "The report could not be sent (%s). Your report is kept, and offered again next time." % (
            type(e).__name__), True


def main():
    folder = next((a for a in sys.argv[1:] if not a.startswith("--")), None)
    if not folder:
        sys.exit(__doc__)
    found = pending(folder)
    if not found:
        sys.exit("no crash waiting in %s (%s)" % (folder, MARKER))
    report = build(folder, found[1])
    print(json.dumps(report, indent=2))
    if "--send" in sys.argv[1:]:
        ok, said, keep = send(report)
        print(("sent: " if ok else "not sent: ") + said)
        if not keep:
            clear(folder)


if __name__ == "__main__":
    main()
