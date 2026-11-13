# Running the game from the command line

The built game is `bin\longshot.exe` on Windows and `bin/longshot` in a packaged Linux game folder. A build from a
source checkout has `longshot.exe` or `longshot` in its build folder, with no launcher beside it. The `longshot.exe`
or `longshot` launcher in the game folder does not pass these arguments to the game, so start the game directly to
use them.

| Argument | What it does |
|---|---|
| `image.bin` | The game image to run. Without it the game uses the `image.bin`, `romfs` and `user` in its folder |
| `--romfs <folder>` | The extracted RomFS folder |
| `--user <folder>` | Where the game keeps per-user data: saves, caches and temp files (and `mouse_sens.txt` when `HOS_MOUSE_SENS` is set; the menu keeps the sensitivity otherwise). Default: `user` in the game folder on Windows, `~/.local/share/longshot` on Linux; with an explicit image path, `user` in the current folder |
| `--trace` | Log every Horizon OS call (the same as `HOS_TRACE=1`) |
| `--prepare-shaders` | Prepare the shaders as the first start does, then exit without starting the game (no build step runs it) |
| `--compile-shaders all` or `most` | What a preparation of this run builds, over `compile_shaders` in `longshot_options.ini` (the same as `RX_VK_COMPILE_SHADERS`): `all` the predicted pipelines too, `most` without them. See [Compile shaders](install.md#compile-shaders) |

For example, keep saves somewhere else:

```powershell
cd "C:\Games\Longshot"
.\bin\longshot.exe --user "D:\Longshot data"
```

```sh
cd ~/Games/Longshot
./bin/longshot --user "$HOME/longshot-data"
```

`longshot_env.txt` and the other test switches are documented below.

<details>
<summary>AMD FSR 3.1 (every build)</summary>

**AMD FSR 3.1** in the Graphics tab's **Upscaler** row (`dlss=3` in `longshot_options.ini`; `RX_VK_FSR=1` for one run,
`RX_VK_FSR=0` never) upscales in place of the game's own network, and with **Frame generation** at 2x its frame
interpolation can make the frame in between with `RX_VK_FSR_FG=1` (opt-in: the game draws its HUD into the same picture
and FSR 3.1 has no HUD-less colour to keep it still, so the HUD smears; by default the renderer's own makes it). Both are AMD's FidelityFX
SDK 1.1.4 shaders (`third_party/fidelityfx-sdk`, MIT), compiled into the renderer: no AMD runtime or DLL is needed, and
the row offers FSR 3.1 on any Vulkan 1.3 GPU with push descriptors, extended storage image formats and storage writes
without a format (`vk: fsr3: ... can run on this GPU` in the log). It upscales on the same inputs as DLSS and FSR 4: the
scene's colour, its depth, motion from the camera with the skinned characters' own drawn over it (Link, his shield and
the people around him keep their outline while they move), and the same camera jitter (`RX_VK_FSR_JITTER=0` turns it
off). Things that move by themselves and are not skinned (Navi's body, props) can still trail. `RX_VK_FSR3_CHAIN=0` gives
it the camera's motion alone, as before.

</details>

<details>
<summary>NVIDIA DLSS (optional, NVIDIA's SDK under NVIDIA's license)</summary>

A game built with NVIDIA's DLSS SDK offers **NVIDIA DLSS** and **NVIDIA DLAA** in the Graphics tab's **Upscaler** row on
NVIDIA RTX cards (`dlss=` in `longshot_options.ini`, `RX_VK_DLSS=0/1/2` for one run): DLSS Super Resolution in place of
the game's own upscaler, with camera jitter (**NVIDIA Reflex** needs no SDK and is in
every build). NVIDIA's files are NVIDIA's, under its own license, the
[NVIDIA RTX SDKs license](https://github.com/NVIDIA/DLSS/blob/9981c6350dc58c0ce21f024fd65e31e851c31689/LICENSE.txt).
Longshot never includes or redistributes them: each build downloads them for itself, and only after you accept that
license. Never share a game folder built with them, and do not post builds made with them.

- **The Longshot Launcher, Windows and Linux:** turn on **DLSS** in the build's settings. It is off by default and shows
  NVIDIA's license when you turn it on. The build downloads the SDK into the launcher's folder and puts DLSS's runtime
  (`nvngx_dlss.dll`, `libnvidia-ngx-dlss.so.<version>` on Linux) and DLSS Frame Generation's (`nvngx_dlssg.dll`,
  `libnvidia-ngx-dlssg.so.<version>`) in the game folder's `ngx` folder. An update of that folder keeps DLSS.
- **A builder from the command line:** `--cli` with make_game.py's `--fetch-ngx-sdk --accept-nvidia-license`
  (below: what they do). The SDK goes to `work/ngx-sdk` in the Longshot folder, or to `LONGSHOT_NGX_SDK` when that is set
  (the Builder drops a variable set on the PC, so a launcher has none).
- **Windows, the command line:** `python -I scripts/ngx_sdk.py --platform windows <sdk folder>` asks you to accept
  NVIDIA's license (or pass `--accept-nvidia-license`) and fetches the headers, `nvsdk_ngx_s.lib`, `nvngx_dlss.dll` and
  `nvngx_dlssg.dll`, each checked against its SHA-256. Then build with `LONGSHOT_NGX_SDK=<sdk folder>`: `make_game.py`
  copies both runtimes into `<game folder>\ngx`. NGX is also pointed at `%LOCALAPPDATA%\Longshot\ngx`, and at
  `RX_VK_DLSS_DIR` when it is set, and uses the newest of each it finds across them (to try a given one, make sure no
  other folder has a newer one). Without `nvngx_dlssg.dll` there, frame generation takes the driver's own copy, which
  may be older: one that makes 1 frame at most, or refuses every set. Never copy NGX's core, `_nvngx.dll`, beside
  the game: it comes with NVIDIA's driver.
- **Linux, a build from source:** `scripts/linux/ngx-sdk.sh <sdk folder>` (it runs `scripts/ngx_sdk.py --platform
  linux`) asks you to accept NVIDIA's license and fetches `libnvsdk_ngx.a` for the build,
  `libnvidia-ngx-dlss.so.<version>`, DLSS's runtime, and `libnvidia-ngx-dlssg.so.<version>`, frame generation's. Then
  run the build with `LONGSHOT_NGX_SDK=<sdk folder>` set: `make_game.py` puts both runtimes in the game folder's `ngx`
  folder. NGX's core comes with NVIDIA's Linux driver.
- **Linux, a game folder with other than plain English letters in its path:** NVIDIA's NGX on Linux loads DLSS's
  runtimes only from a folder whose whole path is ASCII. A game in `~/Games/Zelda` needs nothing more. A game in a
  folder such as `~/Games/Zelda-Café` (the `é` is not ASCII) cannot use the runtimes in its own `ngx` folder, and the
  log and the Insert menu say so: copy `libnvidia-ngx-dlss.so.*` and `libnvidia-ngx-dlssg.so.*` from there into
  `$XDG_DATA_HOME/longshot/ngx`
  (`~/.local/share/longshot/ngx`), which the game also looks in, or into any other folder with an ASCII path and name
  that folder in `RX_VK_DLSS_DIR`.

The **DLSS model** row chooses DLSS's network, by NVIDIA's preset letter, for every mode: **Transformer (K)**, the
transformer model and the one tested most here (the default); **Transformer 2 (M)**, NVIDIA's second-generation
transformer as it picks it for Performance; **Transformer 2 (L)**, the same generation as tuned for Ultra Performance;
**NVIDIA default**, which lets DLSS's runtime pick for each mode, so it can change with a runtime update. The runtime
the SDK comes with (310.9.16) has all of them. A change applies at once, without a restart (`dlss_model=` in
`longshot_options.ini`, 0 to 3 in that order). `RX_VK_DLSS_PRESET=<letter>` (or `0` for NVIDIA's default) chooses
the preset for one run and wins over the row; the log's `vk: NVIDIA DLSS: ... preset <letter>` line says which one was
asked for, and with `RX_VK_DLSS_LOG=1` NGX's own `Using App hint Preset <letter>` lines say which it took.

**8 GB cards:** DLAA draws the 3D at full size, and DLSS and DLAA add images of their own beside the game's. In busy
areas that can pass what the driver allows the game, and the game then stutters: Quality or Balanced is safer there.
The log's `vk: status:` line every 600 frames shows the video memory in use of the allowance, with DLSS's details
(its images' size, the jitter and the actor motion) appended while it draws, and a `vk: over the driver's
video-memory allowance` line says when the game passed it.

The game's log says whether DLSS started, and why not when it did not (`vk: NVIDIA DLSS:` lines in `longshot_log.txt`).
NGX starts on demand: when the Insert menu first opens (to know whether the Upscaler can offer DLSS) or at the first
gameplay upscale with DLSS chosen, so on the title screen the log has no NGX lines yet.
With `RX_VK_DLSS_LOG=1` NGX's own lines go there too (`ngx:`). Some of them look like errors but are not:
`failed to load NGXCore: 126` (twice) is NVIDIA's library looking for `_nvngx.dll` and `nvngx.dll` beside the game, which
are not there on purpose, before it loads the driver's; `Feature ... failed to load from cache` is NGX finding no model
of its own downloads (`C:\ProgramData\NVIDIA\NGX\models`) before it uses the game folder's `nvngx_dlss.dll`.

</details>

<details>
<summary>AMD FSR 4 (optional, AMD's DLLs not included)</summary>

A game built with FSR 4 offers **AMD FSR 4** and **AMD FSR 4 Native AA** in the Graphics tab's **Upscaler** row: AMD's
machine-learned temporal upscaler in place of the game's own, on the same inputs as DLSS (the scene's colour and depth, the
camera's and the characters' motion, and the camera jitter). Native AA draws the 3D at full size and smooths its edges.
With HDR on **Native**, it takes the game's HDR colour as linear light, so highlights above the paper white are kept
(`RX_VK_FSR4_HDR=0`: cut at the paper white, as before).
Unlike FSR 3.1 (above), it runs AMD's own DLLs, so it needs them beside the game. Choose it in the menu (`dlss=4` or `5`
in `longshot_options.ini`, `RX_VK_FSR4=1` or `2` for one run, `RX_VK_FSR4=0` to leave it out).

- **Windows, the Longshot Launcher:** turn on **FSR 4** in the build's settings. It shows AMD's license first and stays
  on only if you accept; the build then fetches AMD's DLLs for you (below), into the launcher's folder and the game
  folder.
- **The command line:** `make_game.py --fsr` (or `LONGSHOT_FSR=1` for `nx2/build.py`). Rebuilding a folder built with
  it (`--update`) keeps it; `--no-fsr` takes it out.
- **AMD's files:** Longshot includes only AMD's MIT-licensed API headers (`third_party/fidelityfx`). Put
  `amd_fidelityfx_loader_vk.dll` and `amd_fidelityfx_upscaler_vk.dll` from the
  [FidelityFX SDK](https://github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK) 2.4 release (`Kits/FidelityFX/signedbin`)
  beside the game's exe (`bin\longshot.exe`) or in the game folder. (`RX_VK_FSR_DIR` names another folder, for testing:
  set it in the environment or a `HOS_ENV_FILE` file; `longshot_env.txt` takes no paths.) Without them the Upscaler row
  says so and offers the rest. On Linux, a Vulkan FSR 4 library (`amd_fidelityfx_upscaler_vk.so`, such as fsr4-vk's)
  takes their place: AMD publishes none for Linux, so you put it beside the game yourself. A Linux build with FSR 4
  (`--fsr`) compiles its own Vulkan back end, as with DLSS, since the AppImage's prebuilt one has no FSR 4: a few
  minutes longer.
- **Or fetched for you (Windows):** `make_game.py --fsr --fetch-fsr-runtime --accept-amd-license` downloads those two
  DLLs and AMD's license from AMD's FidelityFX SDK repository at the v2.4.0 tag, checks each against its SHA-256, and
  puts them in the game folder (`LONGSHOT_FFX_SDK=<folder>` keeps them between builds, default `workfx-sdk`).
  `python -I scripts/ffx_sdk.py --accept-amd-license --game <game folder>` does the same for a game built already.
  Without `--accept-amd-license` nothing is downloaded (the script asks when run by hand), as with NVIDIA's license for
  DLSS. AMD DLLs already in the game folder that are not v2.4.0's (a newer SDK put there by hand) are left alone, and
  the build says so. They are AMD's, under
  [AMD's FSR SDK license](https://github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK/blob/7539ae8b6568ad29ed78a753404351daf3b5cca7/Kits/FidelityFX/docs/license.md)
  (`LICENSE-AMD-FSR-SDK.md` beside them). On Linux the flag does nothing but say so.

The log's `vk: AMD FSR 4:` lines say which of AMD's upscalers it found for the card and which it uses (the newest FSR 4;
never an older FSR in its place), and why not when it did not start. Tested on an RX 6700 XT (RDNA 2).

</details>

<details>
<summary>Switches for testing (longshot_env.txt)</summary>

Switches to test (`RX_VK_NOOPT=1` and the others) go in `longshot_env.txt` in the game folder, one `NAME=VALUE` a
line, on Windows and Linux. The running game reads it, not the builder. A test run can name another file with
`HOS_ENV_FILE=<path>`; the game then reads that one instead and leaves `longshot_env.txt` alone.
`longshot_env.txt` sets only the game's own switches (names that start with `RX_`, `HOS_`, `LONGSHOT_`, `EDEN_`,
`NVN_` or `SDL_`, but not SDL's library names such as `SDL_VULKAN_LIBRARY`), and no value that is a path except
`RX_VK_DLSS_DIR`'s (a folder on this PC, not a network share), and no value at all for the switches that name a file
or folder (`HOS_AUDIO_DUMP`, `RX_VK_SHOTS` and the like) or press buttons on a timer (`HOS_INPUT_SCRIPT`,
`RX_VK_MENU_SCRIPT`); other lines are left out, and the log says so. Switches that need a path, or other variables, go
in a file named by `HOS_ENV_FILE`.
A switch set in the environment the game is started with (a shell's `HOS_FULLSCREEN=0 ./longshot`, a launcher's own)
goes over the same name in the file, and the log says so; within the file, a later line replaces an earlier one. A
switch that stands in for a menu row (`RX_VK_PACE`, `RX_VK_UPSCALE`, `RX_VK_FRAMEGEN_MULT`, `HOS_REALTIME_STEP`,
`HOS_DYNRES`, `HOS_OUTPUT_RES`) applies to that run only: `longshot_options.ini` keeps the value chosen in the menu.

To enable the debug tools, turn **Debug tools** on in the menu's Debug tab (or add `HOS_OOT_CHEATS=1` to
`longshot_env.txt` in the game folder). The tools then show below it on the same tab. **Damage taken** is in the
**Player** group beside **Heart protection**: Half, Normal (the default), Double or Quadruple (`damage_taken=0/1/2/3`
in `longshot_options.ini`). It applies only while Debug tools is on: off, Link takes Normal damage, and the saved
choice returns when the tools do. Heart protection takes priority while enabled. Half scales the full hit before life
is clamped to zero: with two quarter hearts left, a four-quarter hit still kills. The tab's Developer section, always
shown, has the Descriptor reuse, Sampler cache, Robust buffers and ASTC as RGBA8 rows, and on Intel graphics on
Windows the Intel shader compiles row.

A save whose minimap stopped showing after a Teleport on an earlier build (the area left had hidden it, and the flag
stayed set in the save) is mended by the Teleport itself: teleport to an outdoor area, wait until you can move, then
save from the game's System menu. The minimap then stays after a restart.

`RX_VK_UI_GAME_FONT=0` draws the menu with the included font (Libertinus) even when the game's files have the game's own
menu font. `RX_VK_UI_PROMPTS=pad` (or `keys`) shows the controller's (or the keyboard's) prompts whatever was used last,
for screenshots.

For a first run on a 4 GB card such as a GTX 1650, `RX_VK_LOW_SPEC=1` starts with the Minimum (4GB) preset, with
frame generation and upscaling off. It applies only when there is no `longshot_options.ini` yet; after that, change
the settings in the in-game menu.

On **Minimum (4GB)**, when the driver reports a nonzero VRAM budget of at most 4.5 GiB, sampled ASTC textures use
a 1024 dimension limit and an 8 MiB decoded-size threshold for leaving out their top mip levels. This lowers texture
detail to reduce the working set. Other presets and larger or unknown budgets keep the 2048 / 64 MiB defaults;
uncompressed render targets, depth images and the separate `RX_VK_TEX_MAX_MB` allocation limit keep their handling.
The driver budget is sampled at the first texture creation and kept for the session. It is a heuristic, not a physical
VRAM measurement: other applications can lower the budget even on a larger card.

`RX_VK_LOW_VRAM_MIPS=0` (also `off`, `false` or `no`) disables the automatic rule. `RX_VK_TEX_MAX_DIM` overrides only
the dimension limit, so setting it to 2048 still leaves the automatic 8 MiB threshold active on Minimum with a small
budget. `RX_VK_TEX_SKIP_MB` overrides that threshold using its existing parsing. For a more aggressive experiment,
use `RX_VK_TEX_MAX_DIM=512 RX_VK_TEX_SKIP_MB=2`. Changes to these environment switches take effect after a restart.

`RX_VK_MOVIE_UPSCALE=linear` replaces the movies' neural upscale with the plain linear one. The default, `auto`, keeps
the networks and falls back only when a movie frame takes the GPU more than 40 ms. That is on NVIDIA cards: on other
cards, or where the GPU cannot multiply the networks' matrices itself, the movies always get the linear upscale. It does
not change the gameplay upscaler, and movies can look softer. `RX_VK_NETWORK_KEEP=all` takes precedence and keeps the
movie networks.

A movie whose pictures take longer to decode than the time between them is shown, rather than skipped until the sound
has moved on: on a slow CPU the opening used to drop most of its pictures. How fast the decoder is counts from its recent
pictures, so a slow first picture does not stop a decoder that keeps up from catching up later, and catching up takes at
most one picture's time in a frame. With a decoder slower than the movie, the picture can fall behind the sound.
`HOS_VIDEO_NO_CATCHUP=1` skips nothing. Each picture is waited for for about a tenth of a second
(`HOS_VIDEO_ACQUIRE_TRIES=1`: a count of sleeps instead of the clock). After a movie stops or is flushed, and nothing new
has been submitted, there is no wait. The first picture can still be black on some PCs; why is not known yet.

A PC with 4 logical processors or fewer decodes the movies on 2 threads rather than one per processor, so the decoder
leaves the game's own threads their time. A 4-thread laptop with Intel UHD Graphics showed the title and the opening
movie at about 35 fps of 60, with the game's thread waiting 14 to 24 ms on most frames; that report was not run again
with this change. `HOS_DAV1D_THREADS=<n>` sets the count on any PC.

On Windows, Intel's compiler (UHD and Arc) is entered by one thread. A fault inside it leaves that shader out instead
of closing the game, and the program is written to `longshot_driver_faults.txt` so that later starts on the same
driver leave it out too (delete the file to try again). This covers the game's shaders and the renderer's own (texture
decoding, upscaling, frame generation). A draw that only asks whether its pipeline is ready never waits behind another
thread's compile: while one is compiling, the answer is "not yet", even for a pipeline already in a cache, and the
draw is left out until a worker has made it (the log's `vk: asked cold: Intel's gate busy` lines count these). The
renderer's own pipelines made at their first use (the movie's upscale, the texture decoders, a masked clear, the
write-back and late-page fixes) do wait for a compile under way. Whether the driver keeps working after a caught fault
has not been tested. The first scene can still pause while shaders compile, especially when they were not prepared at
the build. If a build's preparation stopped ("the GPU driver stopped responding"), the game leaves a
`longshot_prepare_skipped` file in its `cache` folder and every start after that builds shaders only when first drawn.
Build again with **Prepare shaders now** ticked, or delete that file, to prepare them with this fix.
The Debug tab's **Intel shader compiles** row (Developer section, shown on Intel graphics on Windows) chooses: **One at
a time**, the default and the safe choice on a CPU with 4 cores or fewer, or **Side by side**, faster builds on a CPU
with many cores, but Intel's driver can crash the game while building them. It applies after a restart.
`RX_VK_NO_INTEL_SERIAL=1` is Side by side for a run, whatever the row says. NVIDIA, AMD, and Intel on other systems are
unchanged.

`RX_VK_SKIP_CODE=<prefix,...>` leaves out the draws of every program with a stage whose native code key starts with one
of the prefixes (the keys the log prints after "its code:"). On a Steam Deck, `RX_VK_SKIP_CODE=2c56dc1006942f74`
avoids a GPU hang in Kokiri Forest and the Great Deku Tree: the GPU runs for seconds in the programs that share that
task shader (a culling pass) until the kernel resets it ([#817](https://github.com/NotRexed/Hookshot/issues/817)).
It is a workaround, not a fix: those are task + mesh programs, so the geometry they draw goes with them, and key models
are missing while it is set. Off unless set.

The switches for scripted test runs, screenshots, frame dumps and profiles are in the
[development guide](development.md#switches-for-testing-and-debugging).

`RX_VK_WB_MAX_KB=<KB>` (off by default; 1024 is the tested value) is the size from which a written GPU binding may go
without most of its write-back. OoT
writes tens of MB of culling lists and per-frame buffers through the GPU that the CPU never reads, and copying them back
sat on every submit's memory sync: in Hyrule Field on a 10 GB RTX 3080, about 66 MB and 4.4 ms a submit, holding the
scene at 45-48 fps. The CPU does read and write parts of some large ones, so a binding over the size is written back
whole for the first `RX_VK_WB_PROBATION` submissions that write it (default 8), and after that only the ranges the CPU
read or wrote meanwhile, and its first and last page when it shares them, are written back (`gpu/vk/wb_learn.h`). The
trade-off: a range the CPU first touches only after that (read more rarely than every 8 frames) gets the stale copy
once, and is written back from then on; the log names each one (`in a binding whose write-back was left out`).
Unset or `0`, every written binding is copied back whole. It is off by default because, measured so far (an RTX 3060 Ti
on Windows, an RX 580 on Linux with its mirror forced on), it copies back ~50 MB a frame less at the same frame rate:
results from laptops and other slow PCIe links are wanted. `RX_VK_LOG_WB_SKIPS=1` lists the bindings left out every
600 submits.

`RX_VK_PROGRAM_CACHE_ZSTD=1` keeps the translated shader programs in `longshot_programs.zst.bin`, each record
compressed with zstd and unpacked when it is read. The uncompressed cache is about 6.9 GB; in the reference cache
(46,587 programs) zstd's level 3 made it 1.83 GB (3.75x). It is a file of its own: an existing
`longshot_programs.bin` is left alone, and the first run translates the programs into the compressed cache.

</details>

