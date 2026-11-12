## Why

<!-- One or two plain sentences a player could follow: what is wrong or missing without this PR. -->

## What changed

<!-- The change, in a few lines or a short list. -->

## Measurements

<!-- What counts as a measurement for each kind of change, and the rules that make before and after comparable:
     CONTRIBUTING.md, "Measurements". Before is main at a named commit, on the same PC, save, scene and settings. -->

Before: main @ <commit>
After: this branch @ <commit>
Hardware: <GPU, driver, CPU, RAM, OS>
Scene: <area, time of day; resolution, upscaler, frame generation, frame rate cap>
Method: <duration, number of runs, and where the numbers come from: the FPS counter's Breakdown or Dev level, longshot_log.txt lines, RX_VK_DRAW_STATS, a test>

| | Before | After |
|---|---|---|
| <what was measured> | | |

```
<the log lines or test output the numbers come from>
```

## Not tested

<!-- Hardware, systems or areas this was not run on, and the test request opened for them (docs/test-requests.md). -->

## Screenshots

<!-- If it shows on screen: before and after, from the same spot. Otherwise: "Nothing on screen". -->

## Issues

<!-- Fixes #123 closes the issue when this merges (one line per issue); See #123 links it and keeps it open. -->

Fixes #

## Checklist

### For everyone

- [ ] One topic.
- [ ] The title is a Conventional Commit (`type(scope): description`).
- [ ] Rebased on current `main`.
- [ ] `python -I -B tests/run_all.py` run and passing (any failure that also fails on `main` is named above).
- [ ] Tested in the game, or this PR is a draft.
- [ ] Measurements before and after, on the same PC, scene and settings, at least 2 interleaved runs each, the median reported.
- [ ] No game files, dumps, build output or generated files.
- [ ] `third_party/` untouched (a new folder with a `PROVENANCE.md` instead).
- [ ] AI assistance, if any, recorded with `Assisted-by:` / `Co-Authored-By:` trailers.
- [ ] The license is OK: GPL-3.0-or-later, nothing incompatible and nothing decompiled.

### If applicable

- [ ] Screenshots for a change that shows on screen.
- [ ] A row in `docs/tested-configurations.md` for each measured run.
- [ ] `MKWVK_API_VERSION` bumped (`gpu/vk/vk_api.h`'s `MkwVkHost` or `MkwVkRenderer` changed).
- [ ] The SPIR-V headers regenerated with glslang (a shader changed).
- [ ] A game-free test added for the fix, failing on `main` and passing here.
- [ ] A check under `tests/` that reads changed source updated in this PR, not weakened.
- [ ] Host code builds on both Windows (llvm-mingw) and Linux, with UTF-8 paths.
- [ ] Docs updated.
