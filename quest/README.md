# Hookshot VR (Meta Quest, PCVR)

Plays Hookshot in a Meta Quest over Quest Link, Air Link or Virtual Desktop. The game still runs on the PC's GPU; only the
renderer DLL (`hookshot_vk.dll`) changes, so the game does not need to be rebuilt. The game appears on a large screen
fixed in place in front of you. The Touch controllers act as a gamepad, and the right controller's motion is the
gamepad's gyroscope (for the game's Motion controls).

The code is in `gpu/vk/vk_xr.cpp`, with small hooks in `vk_main.cpp`, `vk_replay.cpp` and `vk_menu.cpp`. The renderer's
Vulkan instance and device are created through OpenXR (`XR_KHR_vulkan_enable2`). Each frame the window shows, with the
Insert menu over it, is also copied into an OpenXR swapchain, and a separate thread submits it as a quad layer.

## Install

Needs an existing Hookshot v2.1.0 install made by the Hookshot Builder. This script uses the toolchain the builder
installed.

```
powershell -ExecutionPolicy Bypass -File quest\build_vk.ps1 -Install     # build the DLL, copy it and openxr_loader.dll into bin
powershell -ExecutionPolicy Bypass -File quest\build_vk.ps1 -Uninstall   # put the original renderer back
```

The settings are on the Insert menu's **VR** tab (F1 or Insert): VR on or off and the flat screen or stereo 3D (both
after a restart), first person, the world's scale, the eyes' separation, the HUD panel and the aiming reticle, the
desktop window, and the Touch controllers. All but the two that take a restart apply while you play. They are kept in
`hookshot_vr.ini` in the game folder, which can still be edited by hand and is read again when it changes; a few
developer lines (`sync_step_us`, `stereo_lead`, `sync_flip`) are only there. The environment variable
`RX_VR=0` or `1` overrides the `vr=` line.

## Play

1. Connect the Quest with Link or Air Link. The Meta Horizon Link app must be the active OpenXR runtime (its settings,
   General, OpenXR Runtime).
2. Start Hookshot as usual. The game shows in the headset once you put it on. Without a headset it plays on the desktop
   as before; the log says why (`xr:` lines in `hookshot_log.txt`).
3. Don't minimise the game's window, because the headset gets what the window shows. A larger window, or fullscreen,
   gives a sharper screen in the headset. HDR must be off.

| Touch | Gamepad (Switch) |
|---|---|
| Left stick / right stick | Left stick / right stick |
| A, B, X, Y | A, B, X, Y (by position, as an Xbox pad) |
| Triggers | ZL, ZR |
| Grips | L, R |
| Stick click | LS / RS |
| Right stick held in, then pushed | D-pad |
| Left stick held in, then pushed | Minus (-) |
| Menu (tap) | Plus (+) |
| Menu (hold half a second) | Hookshot's Insert menu |
| Both sticks held in for one second | Re-centre the screen (holding the Oculus button to recentre works too) |

Controls can be rebound on the Insert menu's Controls page, as for any pad. If aiming feels inverted, use the Motion
page's invert rows.

## Stereo 3D (`stereo=1`)

Instead of a flat screen, the game's own camera moves to your eyes, so the world has real depth: you stand where the
camera is, can lean and look around, and Link is life-size (`world_scale` makes the world smaller, like a diorama).
The game draws one eye per frame, alternating left and right (alternate-eye rendering), so each eye gets half the
game's frame rate. The headset's compositor turns each eye's picture to where your head is when it is shown.

How it works: the game calls `hos_oot_free_view` (in `host/hos_main.c`) for its gameplay camera every frame. That hook
asks the renderer (`mkwvk_xr_view` in `vk_xr.cpp`) for the camera of this frame's eye. Because the game itself draws and
culls with that camera, nothing is missing when you look around within its field of view. The renderer reads each frame's
camera back from the scene's uniform buffer to tell which eye the frame belongs to and its field of view, then submits
both eyes' latest pictures as a projection layer.

This needs the rebuilt game (`hookshot.exe`), not only the DLL. The game is rebuilt from a copy of the builder's tools
with this tree's files on top, then `make_game.py --update` runs on the game folder.

Limits:
- The view covers the game's own field of view, a window in the middle of the headset's. Outside it is black.
- The HUD is drawn into each eye at the same place, so it appears far away.
- The Insert menu, the start-up screen and anything that isn't the gameplay camera show on the flat screen instead.
- Temporal upscalers (DLSS, FSR) see the camera jump between the eyes every frame. The game's own upscaler may look
  cleaner in stereo.
- `hookshot_log.txt` logs how well each frame's camera matched (`xr: stereo: ... matched view N (eye E) to ERR`). If the
  error is large, the game moved the camera after the hook, and the eyes fall back to the flat screen.

## Notes

- Frame generation is optional in VR: the headset's compositor keeps the screen steady at its own refresh rate
  whatever the game's frame rate.
- `openxr_loader.dll` is Khronos' loader, release 1.1.65 (Apache-2.0). It is not in the repository: `build_vk.ps1 -Install`
  downloads Khronos' release package, checks its SHA-256 and copies the DLL into `bin`. Its headers are in
  `third_party/openxr`.
