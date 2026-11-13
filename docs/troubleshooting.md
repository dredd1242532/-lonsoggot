# If something goes wrong

**Start with the log:** `longshot_log.txt` in the game folder's `logs` folder holds the last run, and `longshot_log.1.txt` to `.4.txt`
the four before it (started from a terminal, the game prints there instead). Every 600 frames it writes a `vk: status:`
line with the video memory in use against what the driver allows, and DLSS's details while it draws;
`vk: over the driver's video-memory allowance` says when the game went past it.

| Problem | What to do |
|---|---|
| **Building** | |
| "Longshot supports build e0f3ccfa" or "This version isn't supported" | Check that you chose the right game, with its update installed. |
| "Your antivirus removed part of the build tools" | A false alarm on unsigned programs. Restore the files from Windows Security's Protection history, and add `%LOCALAPPDATA%\LongshotLauncher` and `%LOCALAPPDATA%\Programs\LongshotLauncher` to its exclusions, then build again ([common problems](common-issues.md#your-antivirus-removed-part-of-the-build-tools)). |
| **Starting** | |
| The game opens and closes, or the log says "device lost" | Update your graphics driver. AMD cards have known crashes ([#708](https://github.com/NotRexed/Hookshot/issues/708), [#614](https://github.com/NotRexed/Hookshot/issues/614)). |
| Windows, Intel: the game closes in the first scene, and the log names `igvk64.dll` | Keep the Debug tab's **Intel shader compiles** row on **One at a time** (the default) and update the graphics driver. See [Intel laptops](#intel-laptops). |
| The build said "the GPU driver stopped responding", or the log says `the shaders' preparation was skipped at the build` | The game then builds every shader when it is first drawn, and stutters or leaves things out for a long time. Delete `longshot_prepare_skipped` from the game folder's `cache` folder so that the next start prepares them, or build again with **Prepare shaders now** ticked. |
| Linux: the game doesn't start, or the picture is broken | The game needs a Vulkan 1.3 driver, ideally with mesh shaders: `vulkaninfo \| grep mesh` should list `VK_EXT_mesh_shader`. Update Mesa or the NVIDIA driver. |
| Linux: low-memory warning, or the whole desktop becomes unresponsive while loading | Close other programs and check `free -h`: the `Mem` and `Swap` rows are separate. Below 10 GiB of physical RAM the game warns even with plenty of swap; absence of that warning does not mean a run will fit. Do not keep retrying a load that locks up the PC. After restarting, keep the failed session's log before launching again, and check `journalctl -k -b -1` for an out-of-memory kill or a GPU reset (if the previous boot's journal was saved). |
| **Graphics** | |
| Parts of the picture are missing, or a message says your card has no mesh shader support | The card or its driver has no mesh shaders, so Longshot draws through its compute fallback, which leaves some draws out. Update the driver; a card with mesh shaders draws everything ([#640](https://github.com/NotRexed/Hookshot/issues/640)). |
| A place has a wrong texture, or a floor or wall is missing | Look for `longshot_damaged_files.txt` in the game folder. The build writes it when your dump is incomplete (about 23.3 GB instead of 23.8 GB), listing each damaged file and what was put in its place. Dump the game again and build from the new dump; the file is then removed. |
| AMD: blocky textures, solid rectangles in the sky or unusually dark courtyard surfaces | Try the [AMD texture workaround](#amd-texture-workaround). It uses more graphics memory. |
| **Performance** | |
| Stutter the first time you see something | Normal for now: shaders are being prepared, and it is smooth the next time you pass. You can help remove it for everyone: [send your pipeline list](pipeline-list.md#send-your-pipeline-list). |
| Windows, NVIDIA: stutter on every start, in places you have seen | The driver's shader cache is too small to keep them all (the first start builds about 2 GB of pipelines). In the NVIDIA Control Panel, under Manage 3D settings, set **Shader Cache Size** to **Unlimited** (or 10 GB). On Linux the game raises the driver's limit itself. |
| Low FPS | See [Low FPS or stutter](#low-fps-or-stutter) below. |
| A movie stutters, falls behind its sound, or starts on a black picture | On a CPU with 4 logical processors or fewer, the decoder uses 2 threads; `HOS_DAV1D_THREADS=<n>` in `longshot_env.txt` sets another count. Run once with `HOS_VIDEO_LOG=1` and attach the log: its `video:` lines say how long each picture took to decode and how far the picture was behind the sound. |
| Steam Deck: the game freezes and closes in Kokiri Forest or the Great Deku Tree (`gfx_0.0.0 timeout` in `journalctl -k`) | Add `RX_VK_MESH_NOCULL=2c56dc1006942f74` to `longshot_env.txt`: it gets past it with every model drawn, by turning off the culling pass that runs for seconds. `RX_VK_SKIP_CODE=2c56dc1006942f74` also gets past it, but the models those draws make go missing ([development guide](development.md#switches-for-testing-and-debugging), [#817](https://github.com/NotRexed/Hookshot/issues/817)). |
| **Other** | |
| The game is in the wrong language | Change the code in `language.txt` in the game folder. The file lists them all, and an update keeps the one you set. |

Still stuck? [Report it](https://git.magicbean.dev/MagicBeanDev/Longshot/issues/new) with the version, where you were in
the game, your graphics card, `longshot_log.txt` and a screenshot or video, and never game files. **Gameplay → Bug
report** in the [in-game menu](menu.md#in-game-menu) gathers all of it in one press.

## Low FPS or stutter

1. After the shaders are prepared, walk through the same spot twice. Note whether the slowdown clears the second time,
   or lasts until you restart the game.
2. Turn off **Frame generation**, so the FPS counts the game's own frames, and keep the resolution, upscaler and frame
   rate cap the same between runs. For a slowdown after an update, compare the same spot and settings on both versions
   if you still have the earlier build.
3. Attach the log from the slow run (and from the earlier build, if you have it). Say where the FPS dropped, and keep the
   lines around the drop that show `presents`, `GPU time`, `waits for shaders or pipelines`, `VRAM:` and `memory heaps`.
   Compare the total and per-heap memory with the driver's allowances; the texture cache has its own budget on the
   `VRAM:` line.
4. If the GPU time stays high, run once more with `RX_VK_PASS_TIMING=1` in `longshot_env.txt`: every 600 presents it
   reports which GPU passes take the time. That timing adds work itself, so keep the first log for the FPS comparison,
   and remove the switch afterwards. The [development guide](development.md#time-and-profiles) has more switches.

## Intel laptops

What [#252](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/252) changes was made from the logs of a laptop with
Intel UHD Graphics, a 4-thread CPU, 16 GB and Windows. There the game closed in Intel's driver (`igvk64.dll`) in the
background save of the pipeline cache while other threads built pipelines into the same cache; main now fetches a
cache's data with no build under way on Intel ([#190](https://git.magicbean.dev/MagicBeanDev/Longshot/pulls/190)).
The build's shader preparation had stopped, so every run built its shaders in play, and the title ran at about 35 fps.
It has not been tested on an Intel GPU yet. On such a PC:

1. Build with **Prepare shaders now** ticked. If the game folder's `cache` folder has a `longshot_prepare_skipped` file
   from an earlier build, delete it first.
2. Keep the Debug tab's **Intel shader compiles** row on **One at a time** (the default). Side by side builds faster on a
   CPU with many cores, but Intel's driver can crash the game then.
3. A shader the driver faulted on is listed in `longshot_driver_faults.txt` (in the `cache` folder) and left out, with
   its draws, on later starts on the same driver. After a driver update the list starts again; delete the file to try
   them all again sooner.
4. Leave **ASTC as RGBA8** off: it is the [AMD texture workaround](#amd-texture-workaround), and its larger textures
   come out of the memory that integrated graphics share with the rest of the PC.
5. If it still crashes or runs slowly, [report it](https://git.magicbean.dev/MagicBeanDev/Longshot/issues/new) with
   `longshot_log.txt` from a run with `HOS_VIDEO_LOG=1` in `longshot_env.txt`, the build's details, and the FPS on the
   title and in the first scene.

## AMD texture workaround

Some AMD texture artifacts go away when decoded ASTC textures are kept as RGBA8 instead of being re-encoded to BC3
([#57](https://github.com/NotRexed/Hookshot/issues/57), [#1006](https://github.com/NotRexed/Hookshot/issues/1006)):
turn on **ASTC as RGBA8** in the menu's Debug tab, under Developer, and restart the game. It is a workaround for these
artifacts, not a lighting fix, and it can use much more graphics memory, causing stalls when the card runs out. To undo
it, turn the row off and restart.

`RX_VK_ASTC_RGBA8=1` in `longshot_env.txt` does the same, as earlier versions of this readme said; while that line is
there, the menu row shows its value and can't be changed, so remove the line to choose in the menu.
`RX_VK_ASTC_BC7=1` keeps those textures as BC7 instead of BC3: the same memory (8 bits a texel), but BC7's mode 6 keeps
gradients and alpha much better, so textures look cleaner where BC3 shows bands or blocks. It is off unless set, and
`RX_VK_ASTC_RGBA8=1` wins when both are.

