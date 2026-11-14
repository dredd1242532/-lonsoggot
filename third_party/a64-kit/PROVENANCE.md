# a64-kit (vendored subset)

From NotRexed's a64-kit (Rexcomp's AArch64 kit, `Porting Tools\Rexcomp\a64-kit`, a link to
`Ports\TOTK\TOTKPort\a64-kit`) at TOTKPort commit `60de030` (2026-10-30), **with changes**: Hookshot's, most of them
marked `(hookshot)`, and Longshot's, marked `(longshot)` (listed below). Only the parts the lifter pipeline needs:
`lift/` (lifter, suyu's translator and its ctypes binding), `rt/` (guest model), `tools/nso.py`, `tools/toolchain.py`,
`tools/survey.py`, `hos/nx_image.py`, `tests/insn_test.py`.

It is vendored, not referenced in place, so that building `a64emit.dll` and `lld-link.exe` (both written next to
their sources) never writes into the TOTKPort checkout, and so that this project is pinned to one version.
Most of the project's changes live outside this folder (`nx2/`): they wrap these modules instead of editing them.

Changes made here after the copy (against Hookshot's first import of it, commit `04407384`):

- Hookshot (NotRexed/Hookshot, before the fork): `lift/a64_lift.py` (game patches after an instruction, setjmp owners,
  page marks grouped per straight-line run of stores), `lift/build_emit.py`, `lift/suyu/arm64_to_c.h` (the FMUL/FMLA-family
  sticky-flag fast path, a64_fpx), `rt/a64.h` and `rt/a64_rt.c` (setjmp in lifted code, the BR hook, an exclusive store's
  page marked after the store), `tools/toolchain.py` (Windows builds with llvm-mingw, cross builds with
  RX_TARGET=windows), `hos/nx_image.py` (the addend of GLOB_DAT and JUMP_SLOT relocations), `README.a64-kit.md`. The
  history is Hookshot's: `git log 04407384.. -- third_party/a64-kit`.
- Longshot: `tools/toolchain.py` reads `LONGSHOT_MINGW`, as the rest of Longshot's build sets it, where Hookshot read
  `HOOKSHOT_MINGW` (marked `(longshot)`). The `(hookshot)` markers and comments stay as Hookshot wrote them.
- Longshot's macOS build-command work (2026-11-13): `tools/toolchain.py` distinguishes the output system and architecture
  from the Python host, finds LLVM tools via an explicit `EMSDK` prefix, PATH and `xcrun`, selects the Apple SDK and
  Mach-O linker, and sets a 14.4 deployment target. `lift/build_emit.py` names macOS helpers by the Python process's
  architecture and keys their cache by the compiler/flags as well as source. Windows/Linux command arrays and the
  existing upstream notices are retained. This is a deliberate local adaptation, not a refresh copied from upstream;
  it was authorized as an exception to the third-party rule and the digest was updated with
  `python -I scripts/check_third_party.py --update a64-kit`. New platform logic was written from Apple's `ld(1)`,
  `xcrun(1)` and Clang's documented driver options, not another port's code. The game-free command and link checks
  are `tests/test_macos_build_flags.py`; this does not claim a complete macOS LS build.

So the copy can no longer be refreshed by copying the files again: a refresh keeps these changes, and the tree-sha256
below is this folder's, not upstream's (python -I scripts/check_third_party.py).

Licensing: `lift/suyu/arm64_to_c.h` is suyu's (GPL-2.0-or-later, texts in `lift/suyu/`), so `a64emit.dll`, the
generated runtime and all lifted C are GPL. See `NOTICE.TOTKPort.md`.

tree-sha256: 0a7d1c5a94c1745cc59ac88a4a06b1ccb51b537cac19c793c13a53acff5736f9
