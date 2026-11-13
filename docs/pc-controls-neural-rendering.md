# PC controls and experimental Neural Rendering

## Credit and scope

The original DLSS 5 Neural Rendering integration, model controls, comparison UI, lifecycle policy, translations and tests are by **HypeZ85**, known as **valerio85** on Discord, in [Hookshot PR 1590](https://github.com/NotRexed/Hookshot/pull/1590). This contribution starts from revision `a1f730de8c8e7b2adfbca4e0b8ac4cfbdf72834c`, under the project's GPL-3.0-or-later license. The Longshot port adds Native HDR composition, the optional local runtime bridge, and independent NR resolution scaling. Keyboard action separation, item wheels and the 30 FPS choice are additional local work.

## Keyboard and mouse

Enable **Controls > PC keyboard controls** to separate keyboard gameplay actions and enable mouse item wheels and keyboard D-pad labels. It is off by default; controllers keep the original game actions in either mode.

**Keyboard & Mouse** and **Controller bindings** are separate pages. Sprint hold, sprint toggle and roll have independent keyboard bindings. Defaults are hold unbound, toggle **T**, roll **Mouse 4**. Interaction keeps the keyboard binding for A (E by default). Setting a special action's key removes that key from conflicting ordinary keyboard bindings; controllers are never rebound by this. To use Shift for sprint, bind Shift on this page; the ordinary B/cancel key can be rebound to Backspace.

A toggle changes only on the key's rising edge. Opening the settings menu, leaving the window or using a controller cancels it. Controller A retains the original interaction, sprint, and buffered roll behavior. Both controller binding slots are checked.

Hold the keyboard keys for L or R (Q and R by default) to open a mouse item wheel. Move the pointer into a segment; release the key to equip. Scroll changes pages when more than eight entries exist. The controller selection UI remains unchanged. The wheel uses item and menu artwork decoded locally from the player's romfs by `nx2/pc_item_icons.py`; no artwork is distributed. Text remains available if artwork preparation fails.

The lift validates all three input instructions together before installing the hooks. No native executable addresses, injected binary sections or private .pcctrl records are used. Host/renderer API 22 supplies guest calls, input state and the game-thread menu callback. Guest callbacks preserve the complete guest context and never run on the replay thread.

## DLSS 5 Neural Rendering

The NR menu is available in NGX-enabled builds. Neural Rendering defaults off, requires NVIDIA hardware and a compatible, user-supplied `ngx/nvngx_dlssnr.dll`, and adds GPU work; it is not frame generation. Ordinary DLSS/DLAA remain usable when NR is unavailable.

Native HDR uses an HDR-safe bounded scene proxy for the neural pass, then applies its linear-light residual to the preserved full-resolution HDR scene before the original HDR output stage. The HUD is composited afterward. This retains highlights and alpha outside the model's bounded input domain. Native HDR uses the After upscaler path; SDR can choose Before or After.

**NR resolution scale** ranges from 25 to 100 percent, default 100. It scales only the model pass; game output, HUD and the original scene remain full resolution. Depth and motion are resampled into that pass's pixel dimensions. Resource changes reset history and use the existing deferred-retirement policy.

`RX_VK_DLSS_NR_DIRECT=1` opts into the Windows-only local bridge for a user-provided patched runtime. It is off by default. Normal NR uses the existing NGX route. The bridge is built from source as `longshot_nr_nvngx.dll`, kept separate from the renderer because the runtime inspects its caller-module filename. No driver, system DLL, signature policy or proprietary runtime is modified or installed by Longshot.

This is an experimental, community-documented Vulkan feature-18 contract, not a public NVIDIA Neural Rendering SDK. The local Windows tests used a user-supplied patched runtime 310.9.16 on an RTX 4070 SUPER. Unmodified runtimes, Linux NR and other GPUs have not been validated by this contribution.

## 30 FPS

**Graphics > Game frame rate cap > 30** uses saved value -1; existing values 0 through 6 retain their meanings. It takes effect live on the game presentation thread, before renderer locks, and forces real-time game stepping. The FPS graph targets 30 too. Generated frames are additional. A cap cannot guarantee 30 FPS in a scene that renders more slowly.

## Rollback

Disable PC keyboard controls or Neural Rendering independently in the menu. Unset `RX_VK_DLSS_NR_DIRECT` to use normal NGX only. Select 60 or another existing cap to remove the 30 FPS limit. Reverting the source commits restores the stock implementation; saves are not changed by the feature integration.
