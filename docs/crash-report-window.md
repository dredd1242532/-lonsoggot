# Crash report window (plan)

When Longshot crashes, a window of the Longshot Launcher opens, in the launcher's style, that shows the player **everything a crash
report would hold**, and two buttons: **Send Help** (send it) and **I am sorry** (don't). Nothing leaves the PC without
that click. This is the in-game half of [#81](https://git.magicbean.dev/MagicBeanDev/Longshot/issues/81); the server
half (the crash relay) is live.

## What the player sees

The icon is a crying bean in a hacker hoodie, `App/Assets/crash-bean.png` in the
[Longshot Launcher](https://git.magicbean.dev/MagicBeanDev/LongshotLauncher) (512×512, transparent): changing the art is replacing that file. After Send Help, the
*Sent. Thank you* page shows the happy purple bean instead, `App/Assets/magicbean-report-sent.png`. Both are Magic
Bean's own art.

The window has the launcher's palette and fonts. It's a window of its own, about 640×640, with the bean at 160 px on
the left of the heading:

```
┌──────────────────────────────────────────────────────────────────┐
│                                                                  │
│   ╭──────────╮   Sorry, Longshot crashed                         │
│   │  crying  │                                                   │
│   │   bean   │   Sending this report tells us where it happened  │
│   │   icon   │   and on what hardware. All of it is below.       │
│   ╰──────────╯                                                   │
│                                                                  │
│   ┌ What the report holds ────────────────────────────────────┐  │
│   │ Version           3.4.1                                   │  │
│   │ Release build     yes (stable)                            │  │
│   │ Operating system  Windows 11 (26100)                      │  │
│   │ Renderer          Vulkan                                  │  │
│   │ Graphics card     AMD Radeon RX 7800 XT, 26.0             │  │
│   │ CPU and RAM       Ryzen 7 7800X3D, 16 threads, 32 GB      │  │
│   │ Map               Kokiri Forest (child)                   │  │
│   │ Settings          66, 3 changed            (show)         │  │
│   │ Crash             0xc0000005 (read) at mkw.exe+0x1a2b3c   │  │
│   │ Log               150 lines before the crash (show)       │  │
│   └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│   Never sent: your game files or anything made from them, saves, │
│   keys, and your user name (folders show as ~).                  │
│   The report is posted publicly on git.magicbean.dev, issue #81, │
│   and counted in Magic Bean's crash statistics.                  │
│                                                                  │
│                 [ I am sorry ]        [  Send Help  ]            │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

- **The bean and the heading say sorry**, and the rest is plain: what happened, what would be sent, where it goes.
- **Where it goes is said before the click:** a comment on #81, public, and the crash statistics: the relay also counts
  each report in Prometheus (`longshot_crashes_total`, by signature, version, release, OS, renderer, GPU, CPU and map)
  for Magic Bean's Grafana. The settings and the log go only to #81.
- **The values are the report's, not a description of it.** What the window shows is the JSON that goes out, field by
  field; *show* opens the settings and the log lines exactly as they will be sent (after redaction). No hidden fields.
- **Send Help** (the Brand button, the default) sends it, then the same window shows *Sent. Thank you* with a link to
  the report's own comment on #81 (the one the relay posted or added it to; `crash_report.comment_link`, else
  https://git.magicbean.dev/MagicBeanDev/Longshot/issues/81) and a **Play again**
  button. On a failure (no network, 429 from the rate limit) it says so and keeps the report for the next start.
- **I am sorry** closes the window and drops the report. Nothing is kept.
- **Escape or closing the window is I am sorry**: nothing is sent.

## What a report holds, and what it never holds

The fields are the ones the relay takes (`Bean-Ops/deploy/crash-relay/crash_relay.py`), each capped there:

| Field | From |
|---|---|
| `version` | the release name from the log's first line, `longshot <version> (source <fingerprint>)` (`host/hos_main.c`): `3.4.1`, `3.5.0-rc.2`, `nightly-412-1a2b3c4d`, `v3.0.0-3-g1a2b3c4`. The relay takes letters, digits and `.+_-`, 40 at most |
| `release` | `true` only for an unmodified build of a published release (stable, rc or nightly): [below](#the-release-flag) |
| `os` | the OS name and build |
| `renderer` | Vulkan or WebGPU, and why when it fell back |
| `gpu` | the `vk: device` line of the device in use: name, driver |
| `cpu` | the CPU's name, threads and RAM |
| `map` | the last `map:` line before the crash; before any, "Preparing shaders" (from the shaders' first progress line to "the shaders took") or "Title and intro" |
| `options` | **every setting** in `longshot_options.ini`, defaults included, as `key=value` lines (below) |
| `crash` | the `CRASH:` lines: the exception, the host backtrace, the thread, the guest backtrace (80 at most) |
| `log_tail` | the 150 log lines before the first `CRASH:` line (the relay keeps 150, each up to 300 characters) |

**The settings, defaults included.** The game writes every row of the Insert menu to `longshot_options.ini` on
each save, set or not (`gpu/vk/vk_menu.cpp`, `save`), so the file already holds the defaults; the report sends all of
it, so a crash can be matched to a setting even when nobody changed it. From the file it keeps:

- the menu's rows (`ini_key`): a number, or a size such as `output_res=2560x1440`;
- `options_version`, `memory_budget`, `auto_output` and `auto_steps`.

That's about 66 lines and 2 KB, within the relay's 4000 characters for `options`. The window shows them all under
*show*, with the ones that differ from the defaults first and marked. The defaults come from the game: at startup,
after `menu_init`, it writes them to `longshot_defaults.ini` beside the options, in the same format, so the launcher
needs no copy of them that could drift. With no options file yet (a crash before the first save), the report sends
the defaults file's lines as the settings.

### The release flag

A report says whether it comes from an **actual release**: a stable, rc or nightly that CI built and signed, built
by the builder without changes. Anything else (a checkout, a build with local changes, `LONGSHOT_VERSION` set by
hand, a fork) is not a release, whatever its version says. The flag is what lets the team, and the agents, tell
crashes in what players run from crashes in someone's experiment.

How the report decides, with no network and no secret:

1. **CI writes the release's source fingerprint** into the `VERSION` file it packs (a second line,
   `source=<fingerprint>`, from `nx2/version.py`). `VERSION` is in `SHA256SUMS`, which the launcher checks against
   the release key before it installs the builder ([releases.md](releases.md)).
2. **`make_game.py` copies it** into the game folder's `longshot_version.txt` (`source=`), next to `version=`.
3. **The game's log says what was actually built**: its first line is `longshot <version> (source <fingerprint>)`,
   the fingerprint of the sources the build used.
4. **`release` is true when** the version is a channel's (`X.Y.Z`, `X.Y.Z-rc.N`, `nightly-N-<hex>`:
   [versioning.md](versioning.md)), and the log's fingerprint is the one in `longshot_version.txt`. A changed source
   file changes the fingerprint, and a checkout has no `source=` line, so both come out `false`.

It's a label for sorting, not a proof: anyone can post `"release": true` to the relay by hand. It keeps honest
reports from unofficial builds out of the way, which is the point.

### Keeping agents off crashes that aren't releases

A crash from a build that isn't a release is usually someone's own change, already gone, and an agent that
picks it from #81 spends an evening on it. So the relay marks it where an agent reads first, and the rules say what
the mark means:

- **The comment's heading starts with `[not a release]`**, and its first line says, in plain words:
  *Not from a Longshot release (a local or modified build). Don't investigate it unless it also happens on a
  release; it's here for its owner.* Release crashes keep their heading as is.
- **The relay keeps them apart.** Its signature already holds the version, so a crash seen in both gets two
  comments, and the release one is the one to work on. The hidden tally gets `"release": false`, so a script can
  filter without reading the prose.
- **[AGENTS.md](../AGENTS.md) gets one rule:** crash comments on #81 marked `[not a release]` are not work. Don't
  claim them, don't add them to the worklist, and don't open issues or pull requests for them, unless the person you
  work for asks, or the same crash shows on a release.
- **The window says it too**: *Release build: no*, and the report still goes (its owner may want it), so nothing
  about the flag stops a player from sending.

**Never sent:**

- The register rows (`CRASH:   x0 …`) and the memory rows (`CRASH: [x0+0x000] …`): they hold the game's memory, and
  nothing made from the game leaves the PC (CONTRIBUTING.md).
- From `longshot_options.ini`: the key and pad bindings (`key_*`, `pad_*`), the microphone's name
  (`microphone_device`), the hardware hashes (`auto_for`, `auto_tuned`) and lines the game doesn't write itself.
- Game files, saves, keys, `work/`, minidumps.
- User names: `/home/<name>`, `/Users/<name>` and `C:\Users\<name>` become `~` and `%USERPROFILE%` (the relay does it
  again, as a second line of defence), and the user's name anywhere else as a word of its own (three letters or more)
  becomes `<user>`.

## How it works

### 1. The game: open the window at the crash

`report_crash` (`host/mkw_glue.c`) already writes the `CRASH:` lines and flushes the log. On Linux the same function
runs, through the Win32 layer. After `hos_log_flush()`, it:

1. Writes `crash_pending.txt` in the game folder: the log's name and the time. This marks the crash for the fallback
   (step 4) and for the launcher to find the right log. On the next start, the game's log rotation renames
   `longshot_log.txt` to `longshot_log.1.txt`; the marker follows the rename.
2. Starts the window: `<launcher> --crash <game folder>`. The command is built **at startup**, not in the handler (no
   allocation, no path lookup in a crashing process), from the program the game folder's `longshot` starts too:
   `builder=` in the folder's `longshot_version.txt`, which `make_game.py` writes from `LONGSHOT_BUILDER_EXE`, the
   Longshot Launcher that built the folder (`builder_args=`, when there is one, goes before `--crash`). The handler
   only calls `CreateProcessW` (Windows) or `vfork` and `execve` (Linux), detached, then lets the game die as it does
   today. On Linux the child clears its signal mask first (the crash's signal is blocked in the handler and `execve`
   keeps it: the window, and a game it starts, would die on a fault without the handler) and closes the game's files
   (`close_range`), which the window would otherwise keep after the game is gone.
3. Does nothing when crash reports are turned off (`crash_reports=0`, step 5), when the game was started with
   `LONGSHOT_NO_CRASH_WINDOW=1` (tests, benchmarks, CI), or when `longshot_version.txt` has no `builder=` line (a
   folder built from a checkout: make_game.py writes the file only with a release's `VERSION` or `version.txt`).

This is `host/hos_crash_window.c`: `hos_crash_window_init` at startup (`host/hos_main.c`, after the log's first
line), `hos_crash_window_open` in `report_crash`.

The window is never drawn by the crashing process: it is another program, so a broken heap, a lost GPU or a stuck
thread in the game cannot take it down.

### 2. The window: the launcher's `--crash`

The window is the Longshot Launcher's (`LongshotLauncher --crash <folder>`, in [its repository](https://git.magicbean.dev/MagicBeanDev/LongshotLauncher)), on Windows and
Linux alike. The builders' own windows left Longshot (#171, #181).

- `--crash` opens only that window: no game-file check, no update check, no build, and it runs beside an open
  launcher.
- It runs this folder's version's `nx2/crash_report.py` with the launcher's Python for that version:
  `--window` for what to show (one line of JSON, nothing sent), then `--send --json --time <t>` after Send Help. The
  report's fields, redaction and limits stay here, in one place, with their tests.
- The send is a process of its own: the window can close while it runs (a slow network, DNS), and the send finishes
  without it. A report that couldn't reach the server is kept for the next start.
- Each window drops only the marker of the crash it opened for (`--time`, the marker's time when it opened): one left
  open doesn't drop the marker of a crash that came after it. *I am sorry* drops it.

### 3. The report: one module

`nx2/crash_report.py` builds the report from the game folder, with no UI, for the launcher's window and the tests:

- `pending(folder)`: the log of the crash the marker is about, `(path, lines)`, or `None`.
- `build(folder, lines) -> dict`: reads the log and `longshot_options.ini`, keeps the fields above, drops the
  register and memory rows, redacts user names, caps every field to the relay's limits. The crash's place is made one
  the relay takes: a library's path goes (`/usr/lib/libc.so.6+0xb5a22` is `libc.so.6+0xb5a22`), and a place in no
  module (`?+0x10`, a jump to a bad address) is `unknown+0x10`. Both were refused (400) before, from real crashes.
- `send(report) -> (ok, text, keep)`: POST to `crash_reports` in `nx2/addresses.ini` (now
  `https://data.magicbean.gmatte.xyz/crash`), 10 s timeout for each step of the connection, the JSON as is. With no address set, nothing is sent and the
  report is kept. 201 or 200 give the comment's URL; 400, 413 and 429 say why, and whether to keep the report.
- `comment_link(url)`: the Sent page's *See it on #81*: the report's own comment when the relay's answer is one on
  #81, else #81 itself (`crash_issue` in `nx2/addresses.ini`).
- `issue_place()`, `issue_number()`: where the windows say reports are posted ("git.magicbean.dev, on issue #81") and
  the Sent page's "See it on #81", both from `crash_issue`; without one the windows say no place and show no link.
- `card(report, settings)`, `settings_text(settings)`, `log_text(report)`: the window's rows and its two Show texts.
- `window(folder)`, `send_json(folder, when)`: `--window` and `--send --json`, what the launcher reads.
- `clear(folder, when=None)`: removes the marker, only that crash's with `when` (after Send Help, I am sorry, or a
  400: a report the relay refuses is never retried).
- `always_send(folder)`, `set_always_send(folder, on)`: step 6.

Some fields aren't in the log yet. The game logs them once, at startup, as lines the report reads (`report: os
Windows 11 (26100)`, `report: cpu …`, `report: renderer …`). The log is the single source, and a player who reads
it sees the same things.

### 4. The fallback: the next start

If the window couldn't start (the launcher moved, the process was killed by the OOM killer or a driver reset, with no
`CRASH:` line), the launcher checks for `crash_pending.txt` when it starts or plays the folder. If there's one and its
log has a `CRASH:` line, the same window opens before Play. A crash without `CRASH:` lines (a kill)
offers nothing: the marker is dropped.

### 5. The setting

`crash_reports=1|0` in `longshot_options.ini`, 1 (*Ask*) by default, with a row in the game's Insert menu and in
`recomplauncher.json`'s settings (*Crash reports: Ask after a crash / Never ask*). 0 starts no window and writes no
marker: the game reads it already (`host/hos_crash_window.c`); the menu and launcher rows aren't made yet.

### 6. Always send crash reports

A tick in the launcher: *Always send crash reports*, off by default. It's the player's
answer given once, kept per game folder in `longshot_crash.ini` (`always_send=1`; `crash_report.always_send` and
`set_always_send`), not in `longshot_options.ini`, so it's never one of the sent settings.

With it ticked, the window still opens after a crash and still shows the whole report, with "Always send crash
reports is on (in the launcher): sending...", and sends without waiting for *Send Help*. Then the *Sent* page, or,
when the report waited for the launcher (`--launch`, `--play`), straight on to the launcher's page or the game. A
report that can't be sent (no network, refused) stays on the report page with the reason and the usual buttons.

## Steps

What this branch does: steps 2 (the `report:` lines; not `longshot_defaults.ini` yet), 3, 4 and 5, and 6's game
side. Left: step 1, `longshot_defaults.ini`, step 6's menu and launcher rows, and step 7.

1. **The release flag's inputs:** CI's `source=` line in `VERSION`, and `make_game.py` copying it to
   `longshot_version.txt`.
2. **`report:` lines** in the game's startup log (OS, CPU and RAM, renderer), and `longshot_defaults.ini`.
3. **`nx2/crash_report.py`** and `tests/test_crash_report.py` (below). No UI yet.
4. **The crash window** in the Longshot Launcher, `--crash <folder>`, and the fallback at its start (its own pull
   request: Longshot keeps `--window` and `--send --json` for it).
5. **The game starts the window** from `report_crash`, with the command built at startup and the marker.
6. **The setting**, in the options, the Insert menu and `recomplauncher.json`.
7. **The agents' rule** in AGENTS.md. The relay's side (the `release` field, the `[not a release]` heading and line)
   is in Bean-Ops, and goes out before step 3 is released: the relay drops fields it doesn't know.

## Tests (no game needed)

`tests/test_crash_report.py`, on a made-up game folder and log:

- the report holds exactly the relay's fields, each within its cap;
- no register or memory row, no `/home/<name>`, `/Users/<name>`, `C:\Users\<name>` or Wine's `Z:\home\<name>`, no
  line from outside the log tail;
- `options` holds every menu row, defaults included, and no `key_`, `pad_`, `microphone_device`, `auto_for`,
  `auto_tuned` or unknown line, and stays under 4000 characters with every row at its longest value;
- a log without `CRASH:` lines gives no report, and the marker is cleared;
- the rotated log (`longshot_log.1.txt`) is found through the marker;
- what the window shows is `build()`'s output (the page reads the same dict it sends);
- `release` is true for a release's folder and log, and false with a changed fingerprint, no `source=` line, a
  version that isn't a channel's (`v3.0.0-3-g1a2b3c4`, `dev`, `from-env`), or no log first line;
- the report passes the relay's checks, mirrored in the test: the version format, the field limits, and the
  `CRASH: exception … at <module>+<offset>` line. The whole report stays under the relay's 128 KiB.

`tests/test_crash_report.py` also runs `--window` and `--send --json` as the launcher does. `tests/test_crash_window.py`
compiles `host/hos_crash_window.c` with a stand-in launcher: the marker, the arguments, once per process, nothing when
it's off, and the window started with the crash's signal unblocked and none of the game's files. The window itself is
tested in the launcher's repository.

And in the game: `report_crash` doesn't start the window when `crash_reports=0` or `LONGSHOT_NO_CRASH_WINDOW=1`
(the check reads `host/mkw_glue.c` as the other tests do).

### Tried on a real build

- Linux (the game built from this branch, OoT): `kill -SEGV` during the start's shader preparation opens the window
  in a few seconds with the real GPU, CPU and OS, and Send Help posts to #81.
- Windows (llvm-mingw, under Wine 11): an access violation in the game (a thread started at a bad address) opened the
  builders' window of the time, and Send Help posted to #81.
- A signal sent with `kill` isn't a fault: the handler reports and opens the window, then the game goes on. A real
  crash ends it.
- The game writes `longshot_log.txt` only when it has no terminal (a launcher, an icon). Started from a terminal it
  logs there, and the window finds no crash to report.

## Done when

- A crash in a test build opens the window within a few seconds, on Windows and on Linux, and *Send Help* posts to
  #81 (or adds to the comment of the same crash). *I am sorry* sends nothing (checked with the relay's
  `longshot_crash_reports_total`).
- Killing the window's start (a renamed launcher) offers the report at the next launch instead.
- With *Never ask*, a crash opens nothing and writes nothing.
- `python -I -B tests/run_all.py` passes with the new checks.

## Open questions

- **A comment box** ("what were you doing?"): useful, but it's free text posted publicly, and the relay doesn't take
  it yet. Left for later.
- **Reports from builds that aren't releases**: sent and marked (the release flag), not refused. If they turn out to be
  mostly noise, the relay could count them in its metrics only and post nothing.
- **The relay's host**: `data.magicbean.gmatte.xyz` for now (where reports, and the players' addresses, land first), set
  in `nx2/addresses.ini` only, so moving it to a magicbean.dev name is a one-line change, best made before the first
  release that sends.
