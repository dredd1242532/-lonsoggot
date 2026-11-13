<!-- Open as work in progress (a "WIP: " title; forge.py pr create does it) and mark it ready (forge.py pr ready) once
     every section and every For everyone box is done. Keep this description current, and post a comment for each new
     commit, measurement or test run: CONTRIBUTING.md, "Commits and pull requests". -->

## Why

<!-- The problem or goal a player would notice, not the implementation, in one or two sentences.
     E.g. "The menu's text is cut off at 4K." -->

<why this PR is needed>

## What changed

<!-- Bullets by area, naming the files and functions a reviewer should read.
     E.g. "- menu: `menu_font_scale()` in `gpu/vk/vk_menu.cpp` scales the font with the output". -->

- <area>: <what changed>

## Flags and settings

<!-- One row per environment variable (RX_VK_*, HOS_*, LONGSHOT_*), menu row or option, ini key, or build or
     command-line flag this PR adds or changes; "None" instead of the table when there is none. Goal: yes / no /
     after more testing, with a short reason. The last column is optional. Document each one where its kind is
     documented: docs/development.md, "Switches for testing and debugging" and "The options file", and the README's
     option table for a menu row. -->

| Flag | New or changed | What it does | Default now | Goal: on by default? | Helps on | Doesn't help on | Untested on | Untested, but should help on |
|---|---|---|---|---|---|---|---|---|
| <flag> | | | | | | | | |

## Measurements

<!-- What counts for each kind of change, and what makes the two sides comparable: CONTRIBUTING.md, "Measurements".
     Name the columns "main @ <hash>" and "this PR @ <hash>", never a bare Before and After. Comparing something else
     (a flag off and on, a nightly)? Name each column exactly ("this PR @ <hash>, RX_VK_X=0"), add a line
     "Baseline: <its column>" and one on why main wasn't the comparison. A Δ column is optional: the change against
     the baseline. Rebased onto a newer main: measure again, or say why the numbers still hold. -->

main: <commit> (<date>)
this PR: <branch> @ <commit>
Hardware: <GPU, driver, CPU, RAM, OS>
Scene: <area, time of day; resolution, upscaler, frame generation, frame rate cap>
Method: <duration, number of runs, and where the numbers come from: the FPS counter's Breakdown or Dev level, longshot_log.txt lines, RX_VK_DRAW_STATS, a test>
How to reproduce: <the save or spot, the settings and switches, the commands, so a tester can repeat the run>

| | main @ <commit> | this PR @ <commit> |
|---|---|---|
| <what was measured> | | |

```
<the log lines or test output the numbers come from>
```

## Not tested

<!-- Where it never ran (hardware, OS, driver, areas), with the test request opened for it (docs/test-requests.md);
     and where it ran without numbers. "Nothing" is an answer. -->

Not tested: <hardware, systems or areas>
Tested but not measured: <where it ran without numbers>

## Screenshots

<!-- If it shows on screen: the same spot on both, captioned "main @ <hash>" and "this PR @ <hash>". Otherwise:
     "Nothing on screen". -->

## Risk and rollback

<!-- What could break, and where; how to turn it off (a flag), or that reverting the commit is enough.
     E.g. "Draws could go missing on AMD's RADV; RX_VK_X=0 turns it off." -->

<what could break, and how to turn it off or revert>

## Reviewer notes

<!-- Where to look first, and what is left for a follow-up on purpose. Optional. -->

## Issues

<!-- Fixes #123 closes the issue when this merges (one line per issue); See #123 links it and keeps it open. -->

Fixes #

## Checklist

### For everyone

- [ ] One topic.
- [ ] The title is a Conventional Commit (`type(scope): description`).
- [ ] Rebased on current `main`.
- [ ] `python -I -B tests/run_all.py` run and passing (any failure that also fails on `main` is named above).
- [ ] Tested in the game, or what wasn't is named under Not tested.
- [ ] Measured: main and this PR at named commits, on the same PC, scene and settings, at least 2 interleaved runs each, the median reported.
- [ ] No game files, dumps, build output or generated files.
- [ ] `third_party/` untouched (a new folder with a `PROVENANCE.md` instead).
- [ ] The license is OK: GPL-3.0-or-later, nothing incompatible and nothing decompiled.

### If applicable

- [ ] AI assistance recorded with `Assisted-by:` / `Co-Authored-By:` trailers.
- [ ] Flags table filled in; README / docs/development.md switch reference updated.
- [ ] Screenshots for a change that shows on screen.
- [ ] A row in `docs/tested-configurations.md` for each measured run.
- [ ] `MKWVK_API_VERSION` bumped (`gpu/vk/vk_api.h`'s `MkwVkHost` or `MkwVkRenderer` changed).
- [ ] The SPIR-V headers regenerated with glslang (a shader changed).
- [ ] A game-free test added for the fix, failing on `main` and passing here.
- [ ] A check under `tests/` that reads changed source updated in this PR, not weakened.
- [ ] Host code builds on both Windows (llvm-mingw) and Linux, with UTF-8 paths.
- [ ] Docs updated.
