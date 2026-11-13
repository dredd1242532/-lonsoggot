# In-game menu and controls

## In-game menu

By default, press <kbd>Insert</kbd>, <kbd>F1</kbd> or <kbd>Esc</kbd> in game. On a controller, press Home or Select and
Start together; on a DualShock 4 or DualSense, clicking the touchpad does the same as <kbd>F1</kbd>.
The same keys close it; with every Menu shortcut, <kbd>Esc</kbd> goes back a page or closes the open menu.
<kbd>F1</kbd> always works. **Controls → Menu shortcut** can leave <kbd>Insert</kbd> free for another overlay (F1 only).
With Insert or F1, or with F1 only, <kbd>Esc</kbd> does not open the menu: it only lets the mouse go. Options saved by
an older version on Insert or F1, the default then, move to Insert, F1 or Esc; F1 only stays.
The menu has five tabs, Graphics, Audio, Gameplay, Controls and Debug:
<kbd>Q</kbd> and <kbd>E</kbd> (or <kbd>Tab</kbd> and <kbd>Shift</kbd>+<kbd>Tab</kbd>) switch between them, LB and RB on
a controller (or the triggers). <kbd>Up</kbd> and <kbd>Down</kbd> choose a row, <kbd>Left</kbd> and <kbd>Right</kbd>
change it (<kbd>Enter</kbd> changes it too); beside the rows, the help says what the row is and, on a line of its own,
what the value chosen does. On a controller the D-pad chooses and changes, A changes too and opens a page (Motion,
Controls, the Teleport list, the inventory), and B goes back or closes the menu: A and B are the buttons your layout puts the Switch's A and B
on (with the Nintendo layout, A is the right face button). The mouse is free while the menu is open: click a tab, click a row's name to choose it (its
value stays), click the left or right half of a value to lower or raise it, drag a slider, turn the wheel to scroll the rows (it never changes a value), and right-click to go back. The
button prompts at the bottom show the keys or the controller's buttons, whichever you used last. Your choices are saved
in `longshot_options.ini` in the game folder.

The menu, the FPS counter and the start-up screen are drawn in the style of the game's own menus. They use the game's
own menu font, read into memory from your game files (the romfs folder the game runs from) the first time one of them is
drawn. It is never copied anywhere, and Longshot does not include it. Without it, or if it has no Latin letters, they use
Libertinus, which Longshot includes.

The rows marked "after a restart" below are read when the game starts. While one is changed, it is tagged "After
restart" and the help on the Graphics tab lists it; closing the menu then asks whether to restart now. **Later** keeps
the change for the next start (and is not asked again until you change one of those rows); **Restart now** saves your
choices and starts the game again, so progress since your last save is lost.

A row for something this PC cannot use is not shown: NVIDIA Reflex and AMD Anti-Lag 2 without a GPU and driver that
have them, the DLSS model where DLSS does not run. HDR paper white and
HDR peak brightness and HDR contrast show only while HDR is on, the HDR tone mapper only with HDR set to Native.

The Graphics tab groups its rows under section titles: Resolution and upscaling, Frame generation and latency, Screen,
HDR, Detail, and GPU and memory (last: the rows to change when something is wrong). The table lists them in that order.

| Tab | Option | Choices | Default |
|---|---|---|---|
| Graphics | Quality preset | Custom, Minimum (4GB), Low (6GB), Balanced (8GB), High (12GB), Ultra (16GB+), Auto (this PC) | Auto (this PC) on a first start; Custom in an options file from before it |
| | Aspect ratio | The shapes your screen's resolutions have, narrowest first (5:4, 4:3, 16:10, 16:9, 21:9, 32:9, ...), your monitor's own marked "(monitor's)" (after a restart) | Your monitor's |
| | Output resolution | The Aspect ratio's sizes: your screen's resolutions and the common ones (1440p, 4K, 5K at 16:9; 1920x1200, 2560x1600 at 16:10; 3440x1440 at 21:9; ...), and 2048x1152 (the game's own reduced size, 80% of its 2560x1440, marked "(reduced)") at 16:9; your screen's own size is marked "(native)" (after a restart) | 2560x1440, the game's own picture, or your screen's size where it is smaller (a Steam Deck's 1280x800, a 1080p monitor), at its own shape (a 2560x1080 ultrawide starts at 2560x1080, a 2256x1504 laptop at 2256x1504); Auto (this PC) leaves it as it is |
| | Custom output size | A size of your own, typed as `<width>x<height>` (after a restart) | None |
| | Profile | Docked, Handheld (after a restart) | Docked; a quality preset sets it |
| | Upscaler | Game's own, NVIDIA DLSS (only in builds with NVIDIA's DLSS SDK, below, on NVIDIA RTX GPUs), AMD FSR 3.1 (every build, on GPUs it runs on, below), AMD FSR 4 (only in builds with FSR 4, below, on GPUs AMD's files cover), Off (no upscaling). NVIDIA DLAA and AMD FSR 4 Native AA are DLSS's and FSR 4's Native, chosen on the Render resolution row | Game's own; Auto (this PC) chooses DLSS or FSR 4 where they run |
| | DLSS model | Transformer (K), Transformer 2 (M), Transformer 2 (L), NVIDIA default (shown where DLSS runs; used by DLSS and DLAA) | Transformer (K) |
| | Render resolution | Auto (the game's own dynamic resolution), 100%, 75%, 67%, 50% (each with its size). With DLSS or FSR 4: Auto, Native (DLAA or FSR 4 Native AA: the 3D at full size), 92%, 83%, 75%, Quality (67%), Balanced (58%), Performance (50%), Ultra Performance (33%). With FSR 3.1: Auto, 100% (its native AA), then the same as DLSS from 92%. With Off: Auto, 100%, 75%, 67%, 50%, 33% | Auto; a quality preset sets it |
| | Sharpness | 0 to 100%, in steps of 10 | 0%; a quality preset sets it |
| | Frame generation | Off, Auto, 2x to 8x | Off |
| | Frame generation by | Auto (NVIDIA DLSS Frame Generation where it runs: RTX 40 and newer, with a temporal Upscaler; else the built-in one; with frame generation on, the row shows who makes the frames, as Auto: DLSS FG, and why not DLSS FG when another does), Built-in FG. Changes at once | Auto |
| | Frame pacing | Off, On | Off; a quality preset turns it on |
| | Game frame rate cap | 60 (the console's), 90, 120, 144, 165, 240, Uncapped (changes at once) | 60 |
| | NVIDIA Reflex | Off, On, On + Boost (shown only on NVIDIA GPUs with `VK_NV_low_latency2`) | Off |
| | AMD Anti-Lag 2 | Off, On (shown only on AMD GPUs with `VK_AMD_anti_lag`) | Off |
| | Fullscreen | Off, On (the same as <kbd>F11</kbd>) | Off |
| | V-Sync | Auto, On, Off | Auto |
| | Window scaling filter | Off, Catmull-Rom, Lanczos (shows "Not needed (window = output)" while the window is the Output resolution's size) | Off; a quality preset sets it |
| | Ultrawide menus | Full width; Centred, blurred sides; Centred, black bars (with the Aspect ratio at the monitor's shape) | Centred, blurred sides |
| | HDR | Off, Expanded, Native (the game's) | Off |
| | HDR paper white | 80 to 400 nits, in steps of 10 (while HDR is on) | 200 nits |
| | HDR peak brightness | 400 to 4000 nits, in steps of 100 (while HDR is on) | 1000 nits |
| | HDR contrast | 50% to 200% around mid grey, in steps of 5 (while HDR is on) | 100% |
| | HDR tone mapper | Vanilla (the game's curve), PsychoV35 (RenoDX's; with HDR set to Native. DLSS/DLAA with their NVIDIA frame-generation inputs, and AMD FSR 4, can be tried with `RX_VK_PSYCHOV35_DLSS=1`; FSR 3.1, TAAU and Upscaler Off still fall back to the game's network) | Vanilla |
| | Anisotropic filtering | Game's, 2x, 4x, 8x, 16x | 16x |
| | GPU device | Auto, or each graphics card by name (after a restart) | Auto |
| | VRAM budget | Auto, 3.6, 4.8, 6, 7.5, 9, 11 GB (2.2 GB only from the Minimum (4GB) preset); a quality preset sets it | Auto |
| Audio | Volume | 0 to 100%, in steps of 5 | 100% |
| | Mute in background | Off, On | Off |
| | Audio output | Auto, Stereo, 5.1, Mono (layout changes after a restart; Mono/Stereo live on stereo output) | Auto |
| Gameplay | FPS counter | Off, FPS, Frame time, Graph, Breakdown, Dev | Off |
| | FPS counter position | Top right, Top left, Bottom right, Bottom left | Top right |
| | Menu size | Small, Medium, Large | Medium |
| | Game speed | Per frame, Real time | Real time |
| | Field of view | 50% to 150%, in steps of 5; applies immediately during gameplay | 100% (original) |
| | Text speed | Normal, 2x, 4x, Instant | Normal |
| | Text size | Original, 110%, 125%, 150%, 175% (dialogue, Navi and signs) | Original |
| | Skip title screen | Off, On (after a restart) | Off |
| | Microphone | Off, Default, or each microphone by name | Default |
| | Game language | The game's 16 languages, each by its own name (after a restart) | `language.txt`'s, else English (US) |
| | Menu language | Auto (the game's language), or English, Português (Brasil), Español (Latinoamérica), Español (España), Français, Deutsch, Italiano, Nederlands, Polski, Русский, 日本語, 한국어, 简体中文, 繁體中文 | Auto |
| | Discord status | Off, On ("Playing Longshot" and the time played in your Discord status, while Discord runs on this PC) | On |
| | Skip opening | Off, On (skips the new-game opening: the story movie and Navi waking Link; later cutscenes and the title screen stay) | Off |
| | Bug report | Saves a report into a new folder in `bug-reports` (below) | |
| Controls | Controller in background | Off, On | Off |
| | Mouse camera | As the stick, Native, Off | As the stick |
| | Disable camera centering | Off, On | Off |
| | Mouse sensitivity | 0.10 to 5.00, in steps of 0.10 | 1.00 |
| | Invert mouse vertical | Off, On | Off |
| | Invert camera X | Off, On | Off |
| | Invert camera Y | Off, On | Off |
| | Camera speed | 50% to 400%, in steps of 25 | 100% |
| | Stick dead zone | Default (24%), 5%, 10%, 15%, 20%, 30% | Default (24%) |
| | Rumble strength | Off, 25% to 150%, in steps of 25 | 100% |
| | Layout preset | Nintendo (Def), Xbox / PC, Swap Face; Custom once you bind a button yourself (on the Controls page) | Nintendo (Def) |
| | Button prompts | Auto, Switch, Xbox, PlayStation 4, PlayStation 5, Steam Deck, Keyboard (on the Controls page; changes at once where the game allows it, else after a restart) | Auto |
| | Motion | Opens the Motion page: Motion controls (Off, On; On), Motion sensitivity X (10% to 600%) and Y (10% to 300%, in steps of 10; 100%), Invert motion X and Y (Off, On; Off), Motion deadzone (Low, Standard, High; Standard) | |
| | Walk mode | Hold, Toggle | Hold |
| | Walk speed | 15% to 90%, in steps of 5 | 45% |
| | Controls | Opens the Controls page | |
| | Menu shortcut | Insert or F1; F1 only; Insert, F1 or Esc | Insert, F1 or Esc |
| | Compact menu key | Right Shift, Right Ctrl | Right Shift |
| Debug | Debug tools | Off, On (while on, the tools below it: Heart protection, No-clip flight, Mark position, Return to mark, Set time, Inventory item, Change quantity, Free camera, Teleport, Switch age, Inventory, Movement speed) | Off |
| | Player: Damage taken | Half, Normal, Double, Quadruple (live, while Debug tools is on; Half rounds damage up to a quarter heart; Heart protection wins) | Normal |
| | Developer: Save my pipeline list | Saves a zip of the pipelines your play built into Downloads ([Send your pipeline list](pipeline-list.md#send-your-pipeline-list)) | |
| | Developer: Descriptor reuse | Off, On (changes at once) | On |
| | Developer: Sampler cache | Off, On (changes at once) | Off |
| | Developer: Robust buffers | Auto, Off, On (after a restart) | Auto |
| | Developer: ASTC as RGBA8 | Off, On (after a restart) | Off |
| | Developer: Intel shader compiles | One at a time, Side by side (after a restart; Intel graphics on Windows only) | One at a time |

- **Quality preset** sets Render resolution, Window scaling filter, Sharpness, Frame pacing, Game speed, Profile
  and the video memory limits for a card with that much memory. It leaves the Upscaler and Frame generation as
  you chose them: with DLSS, FSR 3.1 or FSR 4, the preset's resolution becomes their nearest mode. Changing most other
  rows yourself sets it back to Custom.
- **Auto (this PC)**, which a first start begins on, chooses for the PC it runs on: the memory tier from the card's
  video memory (an integrated GPU, a Steam Deck or a card without mesh shaders at Minimum), DLSS on an NVIDIA RTX card
  or FSR 4 on an AMD RX 9000 where the build has them (else the game's own upscaler). It leaves the Output resolution as
  it is: on a first start the game's own 2560x1440, or the screen's size where that is smaller. In the first minute of play it then measures the GPU: while
  frames are missed with the GPU over 90% busy it lowers the Render resolution a step, and while the frame rate cap holds
  with the GPU under half busy it raises it a step, up to the best mode that still upscales (DLSS or FSR Quality, or 100%
  with the game's own; never DLAA or native AA). At most two steps; a PC that holds its cap keeps its picture. It chooses again when the card or the screen changes. `longshot_log.txt`'s
  `Auto (this PC)` lines say what it chose and why.
- **Menu size** is how large the menu, the FPS counter and the start-up screen are drawn: Medium unless you choose Small
  (a desk) or Large (a TV across the room). It stays the same whether you use a controller or the keyboard and mouse.
- **Field of view** changes the gameplay camera's vertical angle: 100% keeps the game's view, higher shows more of the
  world and lower brings it closer. The game's zoom changes remain relative to your choice.
  Aiming has been tested in the 50% to 150% range, including with ultrawide enabled.
  Cutscenes, the HUD and movies keep their own view. The setting is saved as `field_of_view` in `longshot_options.ini`.
- **Skip title screen** starts the game with your most recent save: Longshot presses A through the title screen and its
  file list as soon as the game has a save. Until you have saved once, it leaves the title screen alone, so a new game can
  still be started. It applies after a restart; turn it off to watch the intro and use the title.
- **GPU device** matters on a PC with more than one graphics card (a laptop's integrated and dedicated GPUs, or a
  second card for Lossless Scaling). Auto prefers a dedicated one, and only cards with Vulkan 1.3 are listed.
  `longshot_log.txt` lists every card as `vk: device <n>`; `RX_VK_DEVICE=<n>` chooses one for a single run and wins
  over the menu.
- **Frame generation** makes extra frames between the game's own. It adds a little input delay, and fast motion can
  show artifacts. The renderer's own interpolation makes the frames; at 2x, AMD FSR 3.1's frame interpolation can make
  it instead with `RX_VK_FSR_FG=1` (below: it moves the HUD with the scene, so it is not the default). **Frame pacing** shows frames at an even interval: less stutter, up to one frame of delay. It paces
  the frames that frame generation shows, so it has no effect while Frame generation is Off.
- **Game frame rate cap** limits the frames the game renders. With a cap of 60 and Frame generation at 2x, the output
  can reach 120 FPS. Choose **Auto** in Frame generation to target the display's refresh rate instead of a fixed multiplier.
  A cap **past 60 is experimental**: the game keeps real time, but some cutscenes and bosses still count frames and can
  misbehave (Gohma running into the wall, the intro's narration running ahead: #1742). 60 is the safe choice.
  Scarecrow's Song keeps the timing of its eight-note recording and playback above 60. Saved songs are not retimed;
  re-record a song after updating if its recorded rhythm was lost.
  **Uncapped** renders as fast as the PC can (the game still keeps real time). A display that waits for its refresh still
  holds the frames to its refresh rate: V-Sync on, or the driver's FIFO mode. `HOS_FPS_MAX=<61-1000>` sets a cap for one
  run.
- **Bug report** writes a folder `bug-reports/<date>_<time>` in the game folder with what an issue needs: `report.txt`,
  which answers the [game problem form](https://github.com/NotRexed/Hookshot/issues/new?template=1-game-problem.yml)'s
  questions under its own headings (the version, the map you are in, your graphics card and driver, CPU, RAM and
  system, the menu options and the `RX_`/`HOS_` switches you changed) and ends with a link that opens the form with them
  filled in (the switches by name only); `longshot_log.txt` (this run's log so far) and `longshot_log.1.txt` (the run
  before's, which has the crash if the game crashed last time); and `picture.png`, the game's picture without the menu.
  Settings whose defaults depend on the display, upscaler, Auto preset or other run-time context are listed after the
  changes, in the same answer, under a **Depends on this PC** line, with their current value and “default depends on this
  PC”. They are not counted as changed settings; the report does not invent a default from a run-time list index. The rows
  a preset sets keep their fixed defaults on Custom while Auto (this PC) has never chosen for the options file; with a
  preset, or once Auto has chosen, they depend on this PC.
  Fixed defaults, including GPU Auto, the system-default microphone and Frame generation by's Auto, still report actual
  changes; the Aspect ratio's default is the monitor's shape, and the Game language's `language.txt`'s.
  Your home folder is written `~` (`%USERPROFILE%` on Windows) and your user name `<user>` in the logs' paths. A switch's
  value is written only when it is a number, a size or on/off, and never for a name with `TOKEN`, `KEY`, `ROOM`,
  `SECRET`, `PASS`, `SERVER` or `USER` in it; other values read `(value left out)`. Nothing else goes in (no saves, no
  game files, not `longshot_options.ini` or `longshot_env.txt` themselves), and nothing is sent: open the form, write
  what happened, and drag the folder's files into it (from File Explorer on Windows, a file manager on Linux; on a Steam
  Deck, in Desktop Mode). The row then shows the folder's name. A game that stops before its menu can be used leaves its
  log as `longshot_log.1.txt`: start it again, or attach that file yourself.
- **The scaling rows**, in the picture's order: **Output resolution** is the size of the game's whole frame, HUD
  included. The **Upscaler** fills it from the 3D: the game's own network, NVIDIA DLSS or DLAA (the 3D at full size,
  edges smoothed), AMD FSR 3.1, or AMD FSR 4 or its Native AA (below). DLSS and DLAA look softer than the game's own: set
  **Sharpness** to around 80% with them. **Off** is no upscaler at all: the game's own network is left out too, and the 3D
  is stretched to the output with a plain linear filter (the fastest choice, below). **DLSS model**
  chooses their network (below). **Render
  resolution** is the 3D's share of the output. The **Window scaling filter** then scales the frame to your window,
  and **Sharpness** sharpens it, by the picture's pixels: when the window is much larger than the picture (the Handheld
  Profile's 1536x864 on a 4K screen), a Sharpness kept from an older version is stronger than it was, so a lower one may
  suit.
  Changing the Upscaler keeps your chosen render scale, including across a restart. An engine without that scale
  temporarily uses and shows its nearest supported size: DLSS Balanced (58%) shows 50% with the game's own, then 58%
  again on returning to DLSS. Changing Render resolution or choosing a Quality preset replaces that saved scale.
- **Aspect ratio** and **Output resolution:** the menu lists the resolutions your screen offers (its display modes, as
  Windows lists them, DSR's included), with the common sizes of each of those shapes your screen does not list (1440p, 4K
  and 5K at 16:9, 1920x1200 and 2560x1600 at 16:10, 3440x1440 at 21:9, ...), grouped by shape. **Aspect ratio** lists
  the shapes, your monitor's own marked "(monitor's)" (the default: 21:9 on an ultrawide, 16:10 on a 16:10 screen);
  **Output resolution** then lists that shape's sizes, your screen's own marked "(native)". A size larger than your
  screen is drawn at that size and scaled down to the window. The marks are for the monitor the game's window is on, and
  follow it: moved to another monitor, or the desktop's resolution changed while playing, the menu adds that screen's
  sizes. On Linux the screen's size comes from SDL (the primary display). A portrait screen has no shape the game can
  draw, so the row shows Monitor's shape as a value of its own there. Docked only. A shape other than 16:9 widens the cameras (21:9, 32:9) or narrows the
  picture (16:10, 4:3, 5:4) and keeps the HUD and movies in the middle 16:9. At 16:9 on a 5120x1440 (32:9) monitor, the
  game's 16:9 picture is stretched across the 32:9 output, sharp but wide. Options saved by an older version at 16:9,
  the default then, move to Monitor's shape on a screen narrower than 16:9 (16:10 such as the Steam Deck's, or 4:3),
  where 16:9 only adds bars above and below; an ultrawide keeps its 16:9. The output's rows below show the sizes for the
  output you chose. At your screen's own size the HUD and text map pixel for pixel. Above 2560x1440 the game's render
  targets get a bigger GPU pool, so a full-size 3D fits. Where the screen's modes are not known (Linux), the menu offers
  1920x1080, 2560x1440, 3840x2160, 5120x1440 and 5120x2880. For a size the menu does not offer, type it in
  **Custom output size** (Enter or the pad's confirm button to edit and set it, Esc or the back button to cancel).
  On a controller, D-pad Left/Right moves the caret, Up/Down cycles its digit or separator, the right shoulder inserts
  a digit and the left shoulder removes a character. At the end, Up/Down adds a character. The shown Paste button
  still pastes; with a keyboard, Ctrl+V or Shift+Insert does the same. Full-width digits and `ｘ` become ASCII.
  Or set `output_res=<width>x<height>` in `longshot_options.ini`: each side a multiple of 8, at least 640x360,
  at most 5120x2880's pixels, and between 5:4 and 48:9 in shape. The menu says why it refuses a size; in the file,
  anything else uses the game's reduced 2048x1152. The Output resolution row chooses the typed size from its list when it is
  already there, including sizes found after a monitor change, or offers it as your own choice and selects its shape.
  Typing a 16:9 size keeps Aspect ratio on the monitor's shape when it already follows the monitor.
  The size this run started with stays available until the next start, so returning to the starting shape still
  restores that size even after a custom-size edit.
  A picture wider or narrower than 16:9 is kept to 5120x2160's pixels, so above that a size of another shape is
  not a native frame. A size's own shape sets the camera's, even when the OS reports a single 16:9 primary monitor. For
  three 16:9 monitors, use `5760x1080` or `7680x1440` and a window or logical display
  that spans all three. Both render at full size: 7680x1440 has 5120x2160's pixels, the most a wide picture takes.
  For one run, set `HOS_OUTPUT_RES` to a size, such as `HOS_OUTPUT_RES=3840x2160`.
- **Window scaling filter** matters only when your window's size differs from the Output resolution: for the sharpest
  picture, set the Output resolution to your screen's, and the row shows "Not needed (window = output)". It and
  **Sharpness** also work when Frame generation is Off.
- **Anisotropic filtering** keeps textures sharp on surfaces seen at a slant: the paths and floors ahead of Link. The game
  asks for 1x or 2x, which at a PC's resolutions smears the ground a few metres off; 16x costs little on most cards, and
  Game's is the console's own. `RX_VK_ANISO=<n>` sets it for one run (0: the game's).
- **Upscaler Off** (`dlss=6` in `longshot_options.ini`) is for slow GPUs and for measuring: no DLSS, no FSR, and not the
  game's own network either, which otherwise runs on every frame (at the game's usual 3/4 share it scales the 3D up and
  smooths its edges; at 100% a pass of the same kind still smooths them). In its place the 3D is stretched to the output
  with a linear filter, and the HUD is drawn over it at the output's size as always. Nothing is kept from frame to frame, so
  there is no ghosting, but no anti-aliasing either: edges are jagged, and below 100% the picture is soft. The Render
  resolution then sets the 3D's size alone (Auto, 100%, 75%, 67%, 50%, 33%): lower is faster. RX_VK_DLSS, RX_VK_FSR4 and
  RX_VK_FSR set for a run go over it, as over the row's other choices.
- **Texture detail with DLSS, FSR 3.1 or FSR 4:** DLSS and DLAA sample the world's textures as NVIDIA's DLSS guide asks:
  at the output's size and one mip step finer (the game's own bias + log2(3D / output) - 1), which DLSS resolves over its
  frames, so they keep the detail of the full-size picture instead of the 3D's smaller size. FSR 3.1 and FSR 4 sample them
  one finer mip step for each halving of the 3D below 75% (the game already does so for its own 75%).
  `RX_VK_UPSCALE_LOD_BIAS=0` turns it off; `RX_VK_DLSS_LOD_OFFSET=<steps>` sets DLSS's extra step for a run (-1 by default,
  0 for none, down to -3).
- **NVIDIA Reflex** Off is the game as without it. `RX_VK_REFLEX_LOG=1` logs the driver's latency timings every 300
  frames while it is on.
- **AMD Anti-Lag 2** Off is the game as without it. It works with Frame generation too: each game frame's present
  stage is given when that frame itself is shown, not at the made frames before it.
- **Profile** runs the game as the console docked (at the Output resolution) or Handheld (1536x864): lighter on the GPU.
- **Mesh shaders** (how the game's mesh programs, much of the scenery, run) is no longer a menu row: changing it made
  the next start translate every shader again. The game uses the GPU's own mesh shaders where it has them, and the
  compute path where it has not and on Linux AMD cards (issue 2245). For troubleshooting, `RX_VK_MESH_SHADERS=compute`
  in `longshot_env.txt` uses the compute path on every GPU (try it if your card crashes or leaves objects out; usually a
  little slower), and `RX_VK_MESH_SHADERS=native` the GPU's own for every draw. A `mesh_shaders=` line left in
  `longshot_options.ini` is no longer read.
- **Ultrawide menus:** with the Aspect ratio at the monitor's shape, the pause, item and map menus stay in the middle
  16:9, with what the game draws beside them out of focus, or black; Full width stretches them over the whole screen,
  as before. Loading screens and pre-rendered movies get the same sides. It changes at once.
- **V-Sync:** Auto is mailbox where the driver has it (no tearing, no wait for the display); otherwise On, or on Windows
  Off. On waits for each refresh (FIFO): no tearing, at most the display's rate. Off shows each frame at once and can
  tear (where the driver cannot, it is Auto). It changes at once. `RX_VK_FIFO=1` (On) or `RX_VK_IMMEDIATE=1` (Off) chooses
  for one run.
- **Robust buffers** (Debug tab, Developer): a read past a buffer's end is dropped instead of stopping the GPU. Auto is
  on, on every card; Off can be faster on some NVIDIA cards.
- **ASTC as RGBA8** (Debug tab, Developer): the [AMD texture workaround](troubleshooting.md#amd-texture-workaround).
- **Intel shader compiles** (Debug tab, Developer; Intel graphics on Windows only): One at a time, the default, lets one
  thread at a time into Intel's shader compiler; Side by side is faster on many cores but can crash in the driver. See
  [Intel laptops](troubleshooting.md#intel-laptops). It applies after a restart.
- **Game speed:** with Per frame, the game runs slower whenever it is below 60 fps. Real time keeps it at normal speed
  down to 15 fps.
- **FPS counter** shows in a corner (**FPS counter position**; at the top right while the menu is open), at one of
  five levels. **FPS** is the number alone (an options file's old On); with frame generation on, it also shows the
  game's own rate and who makes the frames: `Base 60, DLSS FG` (NVIDIA DLSS Frame Generation), `FSR 3.1 FG` or `Built-in FG`. **Frame time** adds the game's frame time and its 1% low (the rate of the slowest 1% of frames over
  the last 10 seconds). From this level on, the number and the frame time are green at the Game frame rate cap's rate,
  amber under it and red under two thirds of it. (The cap's rate is the display's refresh instead with V-Sync On when
  that is slower; with Uncapped and V-Sync not On there is no line, and the colours are against 60.) **Graph** adds
  the frame times of the last 4 seconds (each point the slowest frame of its span, so no stutter is missed), with a
  dashed line at the Game frame rate cap's frame time and a red mark over frames that took more than twice it; its
  scale follows the usual frame, so a spike is drawn at the top rather than flattening the rest. **Breakdown** adds
  where the frame's time went on the game's presenting thread, as a bar and in numbers: the game's own code (Game),
  recording and submitting the GPU's work (Renderer), waiting for the GPU (GPU wait), and waiting for the frame's turn
  (Pacing: the frame rate cap, vsync, Reflex); then the GPU's own time per frame, and the shaders built and draws left
  out in the last 4 seconds (red marks on the graph where they happened, the usual cause of a stutter). **Dev** adds the
  renderer's own numbers, for debugging and bug reports: video memory used of what the driver allows (red past it), the
  textures held and their memory, draws, dispatches and render passes a frame, draws skipped a frame (red when any),
  texture uploads a second and the pipelines built since the start.
- **Volume** scales the game's whole sound, on top of the game's own sound settings and Windows' volume. Its steps
  follow a curve so they sound about even: 50% is a quarter of the level (-12 dB). **Mute in background** silences the
  game while another window has the focus; an overlay such as Steam's or Discord's does not count as another window
  while the game was in front.
- **Audio output** is the speakers the game mixes its sound for. Auto is 5.1 when your default output device has 5.1
  or more speakers, else stereo; Stereo or 5.1 chooses one whatever the device has (5.1 on stereo speakers is mixed
  down). Mono averages the stereo channels into both ears, for one-sided hearing or one earbud. Mono/Stereo
  changes apply live while output is stereo; changing the channel layout (from or to 5.1) needs a restart.
  Auto never applies the mono average. Saved `audio_output` values remain 0 Auto, 1 Stereo, 2 5.1; Mono is 3.
  For one run, `HOS_AUDIO_CHANNELS=2` or `6` chooses the channel layout over the menu (6 leaves Mono inactive).
- **Microphone** is what the ocarina hears when the game's own microphone option is on (an instrument, or humming).
  Default follows Windows' default recording device; the list shows each microphone by name, and a change applies at
  once. A chosen microphone that is unplugged stays chosen (shown as not connected): Default stands in until it is
  back. On Linux (SDL2) two microphones with the same name cannot be told apart: the first is used. Windows must allow desktop apps to use the microphone
  (Settings, Privacy & security, Microphone). `HOS_MIC_LOG=1` logs what the game reads, a line a second.
- **Game language** is the game's own text, `language.txt` in the game folder: the row writes it once you leave the row,
  and the game reads it at its next start (`HOS_LANGUAGE`, when set, wins over the file, as for the game). Where the
  folder cannot be written the row goes back to the file's language. **Menu language** is this menu's: Auto follows the
  language the game runs in until the restart (English (UK) and French (Canada) take English and French). The
  translations are machine-made and want checking by speakers of each language: each is
  one line in `gpu/vk/menu_i18n.h`. Place and item names stay English for now. Japanese, Chinese and Korean letters come
  from the game's own fonts for them, with Windows' (Yu Gothic, Microsoft YaHei, Microsoft JhengHei, Malgun Gothic) or
  Linux's Noto CJK where a game font lacks one.
- **Discord status** shows "Playing Longshot" and the time played in your Discord status. It is on by default and does
  nothing unless a Discord client is running on this PC; with the row Off nothing is sent to it.
- **Controller in background:** On keeps the controller working while another window has the focus. The keyboard and
  mouse still need the game's window.
- **Mouse camera:** Native turns the camera with the mouse one to one, as in a PC game. As the stick uses the mouse in
  place of the right stick. The <kbd>-</kbd> and <kbd>=</kbd> keys change the sensitivity in game too.
  When another camera takes over, Native leaves it alone; on returning, mouse look starts from the game's current
  pitch and yaw rather than restoring the old view. Off leaves the mouse out of play: a click in the window doesn't
  capture it, so it neither turns the camera nor presses buttons. Play with a controller or the keyboard.
- **Disable camera centering:** On stops the game gradually turning the camera toward your movement, so it does not fight
  native mouse look. Off keeps the game's standard behavior. Changes apply immediately with either Mouse camera mode.
  Manual controls, explicit recentering and scripted camera headings still work; automatic pitch adjustment is unchanged.
- **Camera speed** is how fast the right stick turns the camera, in percent of the game's own speed. The camera keys
  follow it, and so does the mouse with Mouse camera on As the stick; Native mouse look has its own sensitivity. The
  game's own camera speed option still picks the speed this multiplies.
- **Stick dead zone:** how far a stick moves from center in any direction before it counts. Default is about 24%. Use
  a lower value for a precise stick or a higher one for a stick that drifts. The game also applies its own dead zone.
- **Rumble strength** scales the controller's rumble: 100% is the game's own, Off turns it off. The game's HD rumble
  goes to a controller's heavy and light motors by its frequency.
- **Motion controls** (Controls tab, Motion page): the controller's gyroscope aims where the game uses motion, as on the Switch. It works with
  Switch Pro controllers and pads in Switch mode, DualSense and DualShock 4 (through SDL). Its row
  says whether the connected pad has a gyroscope; Off leaves the game seeing a pad lying still. **Motion sensitivity X/Y**
  scale the left-right and up-down aim (100% is the console's), **Invert motion X/Y** turn either direction around. The game gets the motion
  state as a Switch (and Eden) builds it, so how you hold the pad when motion starts does not matter. Motion follows the pad used
  last. The gyroscope's drift is learned while the pad rests on a table, so set it down for a second if the aim
  creeps. `HOS_MOTION_DEBUG=1` (in `longshot_env.txt`) logs what the game gets twice a second.
- **Layout preset:** Nintendo has A on the right face button, Xbox / PC and Swap Face put A on the bottom one. Changing
  a button yourself sets it to Custom.
- **Button prompts** (on the Controls page): the button icons in the game's texts, hints and menus. Auto shows the first
  controller's; with no controller connected, the keyboard's. Keyboard shows the key or mouse button your controls put each
  action on (the first of its two), and WASD and the mouse for the sticks; the ocarina's notes and the pause menu's
  controller keep the Switch's buttons.
- **Save my pipeline list** (Debug tab, under Developer): see [Send your pipeline list](pipeline-list.md#send-your-pipeline-list).
- **Walk mode** and **Walk speed:** on the keyboard the movement keys push the stick all the way, so Link runs. While the
  Walk key is held (<kbd>Caps Lock</kbd> unless you change it on the Controls page), they tilt it only Walk speed of the
  way, and he walks; with Walk mode on Toggle, one press walks and the next runs again. 15% is his slowest walk, 45% to
  50% his fastest; from 55% up he jogs, then runs. Controllers, the mouse and the camera keys are not changed. Caps Lock
  still switches capitals on and off in other programs at each press: bind another key if that bothers you.
  Walk mode is separate from the Controls page's Toggle column, which holds Switch buttons such as ZL, not Walk.
  They can be used together without toggling twice. In Toggle mode, walking stays selected across Insert and focus
  changes, but a Walk key held when play resumes must be released before a fresh press changes that selection.

### Controls

Use a controller, or the keyboard and mouse. On the keyboard, <kbd>W</kbd> <kbd>A</kbd> <kbd>S</kbd> <kbd>D</kbd> move
(hold <kbd>Caps Lock</kbd> to walk) and the arrow keys (or <kbd>I</kbd> <kbd>J</kbd> <kbd>K</kbd> <kbd>L</kbd>) turn the
camera.
With the default keyboard bindings, press <kbd>E</kbd> to select **New Game** on the title screen.

**Mouse in the game's menus (Windows):** the title screen and its file list, the pause menu, the Key Items, Threads of
Time and Map pages, the options and the save dialogs free the mouse while they are shown. Point at an entry to select
it, left-click to choose it (A) and right-click to go back (B); the tabs, the L and R signs and the Back prompt can be
clicked too. The wheel turns the pause menu's pages and scrolls lists; on the map it changes floors and zooms. Drag
with the left button to pan the map and the tapestry or turn a stone on Key Items. The cursor stays hidden while you
play on a controller or the keyboard, until the mouse moves.

**Rumble:** a Nintendo Switch Pro Controller or Joy-Con plays the game's HD rumble as the Switch does, each side and
both frequency bands as the game asks, with SDL3 (the Windows game brings it; on Linux the system's SDL3, where it has
one). Other controllers, Switch 2 controllers, a pair of Joy-Cons used as one controller, and SDL2's own library get
the game's rumble on their two motors. The menu's Rumble strength scales it too. `HOS_HD_RUMBLE=0` sends the Switch
controllers the motor rumble too. On Linux, SDL talks to the Switch controller itself through its hidraw device, which
needs read and write access for your user; Steam's udev rules (the `steam-devices` package on most distributions) give
it. Without them the kernel's hid-nintendo driver runs the controller, and it gets the motor rumble. HD rumble goes to
anything SDL reports as a Switch Pro Controller or Joy-Con, and that includes third-party controllers in Switch mode
(8BitDo, GameSir and others; SDL gives them Nintendo's ids). Some of them may turn it into weak rumble or none: set
`HOS_HD_RUMBLE=0` to give them the plain motor rumble.

To change which gamepad button or key presses a Switch button, or which keys move, walk and turn the camera, open the
Controls tab's Controls page. Choose a row and a column (Gamepad or Keyboard), press <kbd>Enter</kbd> (or the bottom
face button), then the new button or key. <kbd>Esc</kbd> cancels, <kbd>Delete</kbd> clears it, and Reset to defaults
puts them all back. A gamepad button taken from another Switch button swaps with it. The page saves them in
`longshot_options.ini`.

**Toggle:** on the Controls page, set a Switch button's Toggle column to On to hold it with one press and release it
with the next. Both gamepad inputs, bound keys and mouse buttons share the latch. Every Toggle starts Off.
Plus, Minus, stick directions, Walk and ocarina note rows have no Toggle. Opening Insert, pressing Plus or Minus,
losing focus, rebinding or Reset to defaults releases latched buttons. With Controller in background On, a new
press can latch again behind another window. On Linux, other game dialogs do not necessarily clear the latch;
press Insert to release it. There is no separate on-screen latch indicator.

**Ocarina notes:** the page gains five rows, `Ocarina note (L)`, `(R)`, `(A)`, `(Y)`, `(X)`, unset by default: the game's own
buttons play until you bind a note to a key or gamepad input of your own, and a bound note then answers only that input while
the game waits for it. A dialogue or cutscene that takes the buttons ends the wait, and a song lesson keeps the game's own too.

<details>
<summary>Writing the controls in longshot_options.ini yourself</summary>

```ini
pad_a=0
key_a=69,13
toggle_a=0
```

`pad_<button>` is a gamepad input by position: 0 bottom face button, 1 right, 2 left, 3 top, 4 Select or Back, 5 Home,
6 Start, 7 and 8 the stick presses, 9 and 10 the shoulder buttons, 11 to 14 the D-pad (up, down, left, right), 15 and
16 the triggers, -1 none. `key_<button>` is one or two
[Windows key codes](https://learn.microsoft.com/windows/win32/inputdev/virtual-key-codes) in decimal (69 is
<kbd>E</kbd>, 13 <kbd>Enter</kbd>), 0 none; the mouse's buttons are 1 (left), 2 (right), 4 (middle), 5 and 6 (side
buttons), and count while the mouse is captured. The buttons are `a` `b` `x` `y` `l` `r` `zl` `zr` `plus` `minus` `ls`
`rs` `left` `up` `right` `down`, for keys only the left stick's and the camera's directions and walking: `move_up`
`move_down` `move_left` `move_right` `cam_up` `cam_down` `cam_left` `cam_right` `walk`, and the ocarina's five notes,
which also take a `pad_` line: `note_l` `note_r` `note_a` `note_y` `note_x`. A button the file leaves out keeps
its default. `toggle_<button>=0|1` sets that button's Toggle (0 Off, the default; 1 On). It accepts the Switch
buttons above except `plus` and `minus`; stick directions, Walk and note rows do not accept it. Other values mean Off.
The file saves enabled flags, not held state; a restart starts with no button latched.

</details>

