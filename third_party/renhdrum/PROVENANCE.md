# RenHDrum

RenHDrum is the HD Rumble and DualSense haptics research core the game's fine haptics come from: it turns the
console's own vibration values (a low and a high band, an amplitude and a frequency each, per side) into the
four-channel 48 kHz audio a DualSense's voice coils play on a USB cable.

- `renhdrum.h`: the C ABI, unmodified, from <https://github.com/trzz1214/RenHDrum> at the **v0.3.0** tag
  (`RENHDRUM_VERSION 0x000300`). `host/haptic.c` binds it at run time and also accepts `0x000301`, whose ABI it keeps;
  anything else is refused and the standard rumble plays.
- `NOTICE`: RenHDrum's own notice, as upstream ships it. Its core is MIT OR Apache-2.0, Copyright (c) 2026 Three-Dola,
  and it names the work it was built on. The GPL-3.0-or-later parts of that repository (`integrations/brontide`,
  `integrations/recomp-c/reference`) are **not** used here.
- `README.md`: how to build the DLL and how it reaches the game.
- `renhdrum.dll.sha256`: the pin, committed: the SHA-256 and size of the DLL this repository was tested with (the file
  itself is ignored, so a clean checkout checks a copy against this instead of trusting it). `scripts/renhdrum.py
  verify` reads it, `build --src <RenHDrum at v0.3.0>` makes a copy with the static C runtime, and `fetch` takes one
  from a url or a path and refuses it unless it is the pinned bytes.

`renhdrum.dll` itself is not in the repository: `.gitignore` excludes `*.dll`, as it does `SDL2.dll` and `SDL3.dll`,
which travel the same way (the release's `third_party` part carries the prebuilt files git does not). Nothing in the
game links against it: `host/haptic.c` loads it from the game's own folder by absolute path, and without it every pad
keeps the standard rumble and the Haptic status row says the backend is unavailable.

tree-sha256: 2b675f91adb668d790cb517ce2cfb7fe67db55d6f2aeb6f13bf5862a04be6e57
