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

`renhdrum.dll` itself is not in the repository: `.gitignore` excludes `*.dll`, as it does `SDL2.dll` and `SDL3.dll`,
which travel the same way (the release's `third_party` part carries the prebuilt files git does not). Nothing in the
game links against it: `host/haptic.c` loads it from the game's own folder by absolute path, and without it every pad
keeps the standard rumble and the Haptic status row says the backend is unavailable.

tree-sha256: 8353627eada15a8b0ead26dc824e5e96514954f30f1f7f75f245fbc8ccec4d45
