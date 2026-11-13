"""The launcher's side of the performance statistics (gpu/vk/perf_report.cpp is the game's): the tick, and sending what
the game left in the game folder's perf_pending/.

  - The launcher's "Send performance statistics" tick is send=1 in the game folder's longshot_perf.ini, off by default.
    The game reads it at its start: off, it measures nothing and writes nothing.
  - On, the game writes a summary (JSON, the relay's version 1) every 5 minutes and at exit into perf_pending/. The
    launcher sends them at its next start, in a process of its own (start_sending), to perf_reports in nx2/addresses.ini (Bean-Ops' perf relay),
    oldest first, and deletes each one the relay took or refused; one it couldn't reach stays for next time.
  - The sender fills in version and release (the release flag as the crash report has it: a release's version, built
    from the release's own sources), which the game can't know.
  - Each summary the relay took is also kept, as it was sent, in perf_sent/ (the newest 48): what went to the team, for the
    player or a tester to read.
  - Turning the tick off deletes what is waiting, and what perf_sent/ kept: the player said no.

python -I nx2/perf_report.py <game folder>            what would be sent, sends nothing
python -I nx2/perf_report.py <game folder> --send     sends it (what the launchers start: start_sending)
"""
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from addresses import address  # noqa: E402

ENDPOINT = address("perf_reports")
CHOICE = "longshot_perf.ini"   # (send=1: the launcher's tick; the game reads it, gpu/vk/perf_report.cpp)
PENDING = "perf_pending"
SENT = "perf_sent"   # (a copy of each summary the relay took, as sent: the newest MAX_PENDING)
VERSION_FILE = "longshot_version.txt"
LOGS = ["longshot_log.txt"] + ["longshot_log.%d.txt" % k for k in range(1, 5)]
MAX_PENDING = 48   # (the newest kept when the launcher hasn't been back for long: older ones go unsent)
MAX_BODY = 60 * 1024   # (the relay's limit is 64 KiB: a bigger one is refused there anyway)
# A release's version (docs/versioning.md), as nx2/crash_report.py has it
RELEASE_VERSION = re.compile(r"^v?\d+\.\d+\.\d+(?:-rc\.\d+)?$|^nightly-\d+-[0-9a-f]{8}$")
FIRST_LINE = re.compile(r"\blongshot (\S+)(?: \(source ([0-9a-f]+)\))?")


def sending(folder):
    """The tick: on only with send=1 in the game folder"""
    try:
        with open(os.path.join(folder, CHOICE), encoding="utf-8") as f:
            return any(line.strip() == "send=1" for line in f)
    except OSError:
        return False


def set_sending(folder, on):
    """The tick, saved for the game (it reads it at its start). Off deletes what is waiting"""
    path = os.path.join(folder, CHOICE)
    if on:
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            f.write("send=1\n")
        os.replace(path + ".tmp", path)
        return
    try:
        os.remove(path)
    except OSError:
        pass
    for sub in (PENDING, SENT):   # (and a summary the game was writing when it was cut short: its .tmp)
        try:
            names = [n for n in os.listdir(os.path.join(folder, sub)) if n.endswith((".json", ".tmp"))]
        except OSError:
            names = []
        for name in names:
            try:
                os.remove(os.path.join(folder, sub, name))
            except OSError:
                pass


def order(name):
    """A summary's place, oldest first: its name is <unix time>-<n>.json"""
    m = re.match(r"(\d+)-(\d+)\.json$", name)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def waiting(folder):
    """The summaries waiting, oldest first"""
    try:
        names = [n for n in os.listdir(os.path.join(folder, PENDING)) if n.endswith(".json")]
    except OSError:
        return []
    return sorted(names, key=order)


def build_facts(folder):
    """(version, release) of the game in that folder: longshot_version.txt's version, and the release flag (a release's
    version and the log's source fingerprint the same as the release's own source= line)"""
    version, expected = "", ""
    try:
        with open(os.path.join(folder, VERSION_FILE), encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("version="):
                    version = line[8:].strip()
                elif line.startswith("source="):
                    expected = line[7:].strip()
    except OSError:
        pass
    source = ""
    for name in LOGS:
        try:
            with open(os.path.join(folder, name), encoding="utf-8", errors="replace") as f:
                m = FIRST_LINE.search(f.readline())
        except OSError:
            continue
        if m:
            version = version or m.group(1)
            source = m.group(2) or ""
            break
    return version, bool(RELEASE_VERSION.match(version) and expected and source == expected)


def post(body, url, timeout=10):
    """'sent' (2xx), 'refused' (4xx but 429: never better next time) or 'later' (not reached, busy, the server's error)"""
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "longshot-perf-report"})
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return "sent"
    except urllib.error.HTTPError as e:
        return "later" if e.code == 429 or e.code >= 500 else "refused"
    except Exception:   # (not reached, cut short: kept for next time)
        return "later"


def send_waiting(folder, url=None, timeout=10):
    """Sends what is waiting, when the tick is on: {"sent": n, "refused": n, "left": n}. Stops at the first one the relay
    couldn't take now"""
    url = ENDPOINT if url is None else url
    counts = {"sent": 0, "refused": 0, "left": 0}
    if not folder or not sending(folder):
        return counts
    names = waiting(folder)
    for old in names[:-MAX_PENDING]:   # (too many: the oldest go unsent)
        try:
            os.remove(os.path.join(folder, PENDING, old))
        except OSError:
            pass
    names = names[-MAX_PENDING:]
    if not url:
        counts["left"] = len(names)
        return counts
    version, release = build_facts(folder)
    for i, name in enumerate(names):
        path = os.path.join(folder, PENDING, name)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:   # (a bad byte: one field's text, not the summary)
                summary = json.load(f)
            if not isinstance(summary, dict):
                raise ValueError
        except (OSError, ValueError):
            result = "refused"   # (unreadable: dropped)
        else:
            # (the game's own version, of the build that wrote it: the folder may have been updated since. A release only
            # when that is the folder's build, whose fingerprint was checked)
            own = summary.get("version") if isinstance(summary.get("version"), str) else ""
            summary["version"] = own or version
            summary["release"] = release and summary["version"] == version
            body = json.dumps(summary, separators=(",", ":")).encode()
            result = "refused" if len(body) > MAX_BODY else post(body, url, timeout)
        if result == "later":
            counts["left"] = len(names) - i
            break
        counts[result] += 1
        if result == "sent":
            keep_sent(folder, name, body)
        try:
            os.remove(path)
        except OSError:
            pass
    return counts


def keep_sent(folder, name, body):
    """A copy of what the relay took, as sent, in perf_sent/: the newest MAX_PENDING. Best effort: the summary is sent"""
    sent = os.path.join(folder, SENT)
    try:
        os.makedirs(sent, exist_ok=True)
        with open(os.path.join(sent, name), "wb") as f:
            f.write(body)
        names = sorted((n for n in os.listdir(sent) if n.endswith(".json")), key=order)
        for old in names[:-MAX_PENDING]:
            os.remove(os.path.join(sent, old))
    except OSError:
        pass


def start_sending(folder, python=None, python_args=()):
    """send_waiting in a process of its own, left running: Play closes the launcher at once, and a summary the relay took
    must still be deleted after (or it would be sent, and counted, again). Nothing when the tick is off or nothing waits"""
    if not folder or not sending(folder) or not waiting(folder):
        return None
    cmd = [python or sys.executable, *python_args, "-I", os.path.abspath(__file__), folder, "--send"]
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
    try:
        return subprocess.Popen(cmd, **kw)
    except OSError:
        return None


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    folder = sys.argv[1]
    if "--send" in sys.argv[2:]:
        print(send_waiting(folder))
        return
    version, release = build_facts(folder)
    try:
        kept = sum(n.endswith(".json") for n in os.listdir(os.path.join(folder, SENT)))
    except OSError:
        kept = 0
    print("tick: %s, waiting: %d, version %s, release %s, to %s; %d sent ones kept in %s" % (
        "on" if sending(folder) else "off", len(waiting(folder)), version or "?", release, ENDPOINT or "nowhere", kept,
        os.path.join(folder, SENT)))


if __name__ == "__main__":
    main()
