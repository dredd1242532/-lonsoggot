# Performance statistics

A player can tick **Send performance statistics** on the launcher's page (both builders). It is off by default. With it
on, the game counts how its frames went and the launcher sends the counts to the team's Grafana. These are the numbers
that say where Longshot stutters, plays its movies badly or loses its pacing, sliced by GPU, driver, CPU, OS, kernel,
renderer and map, each on its own.

## What the player sees

- The tick, on the launcher's page. Ticking it writes `send=1` to the game folder's `longshot_perf.ini`;
  unticking deletes the file and every summary that hasn't been sent yet.
- Nothing in the game. The log says `vk: performance statistics on (...)` at the first frame when it's on.

## What is sent

One summary every 5 minutes of play and one at exit, each the counts since the one before (the relay's version 1, JSON,
under 60 KiB):

- **The machine:** the GPU and its driver, VRAM, the CPU, RAM, the OS and kernel (`report:` lines, as the crash report
  has them), the renderer.
- **The settings that change the frame:** the upscaler, the preset, the frame rate cap.
- **The game:** its version and the release flag (a release's version built from the release's own sources), both
  filled in by the launcher.
- **Per map** (the scene and Link's age, as the `map:` log line has it):
  - frame times and the GPU's time as histograms (buckets around 240 to 20 fps, then stutters);
  - the FPS counter's parts (game, render, GPU wait, pacing);
  - hitches (a frame over twice its target and over 25 ms) by what they waited on most: pipelines, shader translation,
    texture uploads, memory sync, the GPU, read-backs, the game, pacing, other;
  - pacing misses (a frame more than a quarter of the target off the one before);
  - draws left out;
  - renderer events: program failures, device lost, swapchain recreated, movie upscaler dropped, DLSS recreated, VRAM
    over budget;
  - movies: pictures shown, dropped and late, decoding time, and decoders that didn't start (dav1d to Media Foundation).
- **Memory:** VRAM used and allowed, RAM used.

Never sent: paths, the user name, settings beyond the three above, the log, saves, anything from the game files. A
summary carries no ID, so two summaries can't be tied to one player.

## How the parts fit

- **The game** (`gpu/vk/perf_report.cpp`, `perf_report.h`): `perf_report_init()` reads `longshot_perf.ini` at the
  first frame. Off, nothing else runs. On, the FPS counter (`vk_fps.cpp`) times every frame's parts as its Breakdown does
  and hands each frame to `perf_frame()`, which adds it to the current map's totals (a few array increments). The
  summary is written to `perf_pending/<unix time>-<n>.json` every 5 minutes and from `perf_report_final()` at exit or
  device loss (written on a thread of its own, except at exit). The map and the movies' totals come from the host (`hos_perf_info`, `MkwVkHost::perf_info`, API 22).
- **The launcher** (`nx2/perf_report.py`, used by `builder/linux/longshot_builder.py` and
  `builder/py/longshot_builder/perf.py`): at its start, on a thread, sends what is waiting to `perf_reports` in
  `nx2/addresses.ini`, oldest first. A summary the relay took or refused is deleted; one it couldn't reach (or a 429 or
  5xx) stays for next time. At most 48 wait; older ones are dropped unsent.
- **The relay** (Bean-Ops `deploy/perf-relay`): checks each summary, caps every label's values (the rest become
  `other`), and exposes them as Prometheus metrics. Grafana's **Longshot performance** dashboard reads them, with a
  toggle between official builds, unofficial ones and both.

`python -I nx2/perf_report.py <game folder>` prints what would be sent; `--send` sends it.

## Cost

With the tick off, the game reads one small file at its first frame and nothing else. With it on, every frame takes the
Breakdown's timings (the host times its sleeps) and `add_frame()` (16 ns at `-O1` in `tests/test_perf_report.cpp`);
every 5 minutes the summary is made under a lock and written on a thread of its own. Sending happens in the launcher, never in the game.
