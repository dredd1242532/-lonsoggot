# Installing and updating Longshot

## On Windows

1. [Download the launcher for Windows](https://longshot.magicbean.dev/download/launcher/windows)
   (`LongshotLauncher-<version>-windows-x64-setup.exe`) and run it. It asks once: **Install** (for you, no administrator
   rights, with a Start menu shortcut) or **Run portable**. To skip the question, take
   [the portable zip](https://longshot.magicbean.dev/download/launcher/windows-portable), unzip it and run
   `LongshotLauncher.exe`.
2. Press **Build the game** and choose the **Stable** channel.
3. Pick your game. The launcher finds `.nup` files in your Downloads, Desktop and Documents folders; if yours isn't
   listed, point it at the file, or at the `romfs` and `exefs` folders. It checks the version while the builder downloads.
4. Choose where the game goes (`Games\Longshot` in your user folder by default), its language and the options.
5. Press **Build**, then **Play**. The launcher can also add desktop and Start menu shortcuts.

The build prepares the game's shaders at its end, which takes a while; later starts are faster. To keep everything on an
external SSD, run [the launcher's portable zip](https://longshot.magicbean.dev/download/launcher/windows-portable) from
there: its data stays in `LongshotLauncher-data` beside it.

### Compile shaders

**Compile shaders** (in the launcher's settings, or `make_game.py --compile-shaders`) chooses what that
preparation builds:

- **Most** (recommended, the default): every shader and the pipelines the game is known to use (the shipped list and
  what your own play noted).
- **All**: the predicted pipelines too, about 50,000 guesses at pipelines no list names. The preparation takes about 40%
  longer (415 s against 292 s on an RTX 3090 with a 12-core CPU), and play almost never uses them.

A preparation the game runs at a launch (after "Prepare shaders now" was unticked, or after an update or a new GPU driver
changed what the shaders are translated for) follows the same choice. A build whose preparation stopped ("the GPU
driver stopped responding") leaves `longshot_prepare_skipped` instead, and the game then prepares nothing at a launch,
building each shader when first drawn, until that file is deleted or a build prepares them. The choice is kept as
`compile_shaders` in `longshot_options.ini` (0 Most, 1 All). `--compile-shaders all` or `most` on the game's command line, or
`RX_VK_COMPILE_SHADERS=all` or `most`, sets it for one run.

## On Linux

1. [Download the launcher for Linux](https://longshot.magicbean.dev/download/launcher/linux)
   (`LongshotLauncher-<version>-x86_64.AppImage`).
2. Let it run as a program: in your file manager, open its **Properties** and turn on "Allow executing file as program"
   (the wording differs between desktops), or run `chmod +x LongshotLauncher-*-x86_64.AppImage`.
3. Start it and build the game as [on Windows](#on-windows). The game goes to `~/Games/Longshot` by default, and the
   launcher can add it to your applications menu as "Longshot".

Each version's builder is also released on its own, as `Longshot-<version>-x86_64.AppImage` (about 95 MB) on
[Longshot's releases](https://git.magicbean.dev/MagicBeanDev/Longshot/releases). It has no window: it is what the
launcher builds with, and it builds and starts the game from the command line, over SSH or from a script (see
[Linux, the AppImage from the command line](build-from-source.md#build-from-source)). Its first `--cli` downloads its build tools (the
compiler, the C library it links against, FFmpeg and SDL2: about 170 MB) into `~/.local/share/longshot/builder/toolchains`,
and later versions reuse the ones that didn't change; the launcher downloads the same tools itself.

### Advanced Linux setup

**Faster GPU memory on AMD and Intel (`/dev/udmabuf`).** Longshot imports GPU memory fastest through the kernel's
`udmabuf` device. Without access to it, it falls back to host-pointer imports, which can be much slower on AMD (RADV),
and warns about it on stderr and in `longshot_log.txt`. On NVIDIA's own driver you can ignore that warning: NVIDIA refuses
these imports, and host-pointer imports are not slow there (the check runs at start, before the game knows its GPU).

Check with `ls -l /dev/udmabuf` and `id`. If the device is missing, load the module first (`sudo modprobe udmabuf`).
Then give the game's user access, in one of these ways:

1. **On a desktop (systemd-logind):** a rule that grants it to the active local user. Put
   `SUBSYSTEM=="misc", KERNEL=="udmabuf", TAG+="uaccess"` in `/etc/udev/rules.d/70-longshot-udmabuf.rules` (it must come
   before `73-seat-late.rules`).
2. **Over SSH or as a service:** create a dedicated `udmabuf` group, add only the game's user, and use
   `SUBSYSTEM=="misc", KERNEL=="udmabuf", GROUP="udmabuf", MODE="0660"` in that file instead.
3. **The device's owning group:** add yourself to it, only if its other devices should be yours too (`kvm`, for one,
   also grants virtualization).

Apply a rule with `sudo udevadm control --reload-rules` and `sudo udevadm trigger --action=add /sys/class/misc/udmabuf`,
and log out and back in (or restart the service) after a group change. Never run the game as root or make the device
world-writable.

**How Play starts the game.** When the desktop gives the launcher its own systemd service, **Play** waits up to two
seconds for the game's separate scope before closing its window. If that hand-off fails, it starts the game directly and
records the fallback in `longshot_startup.log` once the game exits. A direct start still ends with the desktop's
service: a desktop that stops its children stops the game too.

## Updating

The launcher checks for updates when it starts. When a newer version is out on your channel, press **Update**: it
downloads the new builder (only the parts that changed) and builds the game again from the game files the first build
kept, so you don't need your `.nup` again. It updates itself the same way. What changed is on
[Longshot's releases](https://git.magicbean.dev/MagicBeanDev/Longshot/releases).

Before updating, copy your saves somewhere safe: the `user` folder in the game folder on Windows,
`~/.local/share/longshot` on Linux.

| An update keeps | An update replaces |
|---|---|
| Your saves | The game in `bin` and its files |
| `longshot_options.ini`: the menu's choices and your controls | `image.bin` and the shader list |
| `longshot_env.txt` | `language.txt`, written again with the game's language |
| `mods` | |

Without the launcher: from a clone, `git pull`, then `python -I nx2/make_game.py --update <game folder>`; a builder
does the same with `--cli --update <game folder>`.

## Release channels

The launcher builds from one of three channels:

| Channel | What it is | The launcher |
|---|---|---|
| **Stable** | The released version. Choose this to play. | [Windows](https://longshot.magicbean.dev/download/launcher/stable/windows) · [portable](https://longshot.magicbean.dev/download/launcher/stable/windows-portable) · [Linux](https://longshot.magicbean.dev/download/launcher/stable/linux) |
| **Release candidate** | The next version, before it is released. | [Windows](https://longshot.magicbean.dev/download/launcher/rc/windows) · [portable](https://longshot.magicbean.dev/download/launcher/rc/windows-portable) · [Linux](https://longshot.magicbean.dev/download/launcher/rc/linux) |
| **Nightly** | Built from `main` after every change; the newest five stay on the releases page. Untested: it may break your saves or settings, so copy them first. | [Windows](https://longshot.magicbean.dev/download/launcher/nightly/windows) · [portable](https://longshot.magicbean.dev/download/launcher/nightly/windows-portable) · [Linux](https://longshot.magicbean.dev/download/launcher/nightly/linux) |

A nightly launcher builds Longshot's nightlies by default; any launcher can build from any channel. To go back, build a
version from the **Stable** channel.

## Another way: RecompLauncher

Each version also ships the files [RecompLauncher](https://recomplauncher.com) installs (`recomplauncher.json`, in
`LongshotBuilder-<version>-windows-x64.zip` and in the Linux AppImage). RecompLauncher only finds releases on GitHub, and
Longshot's are on git.magicbean.dev, so for now add a downloaded file to it by hand
([docs/releases.md](releases.md#recomplauncher-longshot)).

## Where the files go

The build reads your game and never changes it.

| What | Windows | Linux |
|---|---|---|
| The game | The folder you chose (`Games\Longshot` by default) | The folder you chose (`~/Games/Longshot` by default) |
| Your saves | `user` in the game folder | `~/.local/share/longshot` |
| The launcher, each version's builder and its tools (`toolchains`), the build files (`work`) | `%LOCALAPPDATA%\LongshotLauncher` (the installed program in `%LOCALAPPDATA%\Programs\LongshotLauncher`; the portable one's data in `LongshotLauncher-data` beside it) | `~/.local/share/longshot/launcher` (the portable AppImage's in `<AppImage>.home` beside it) |
| A builder without the launcher (`--cli`) and its tools | `%LOCALAPPDATA%\LongshotBuilder` | `~/.local/share/longshot/builder` and `~/.cache/longshot-builder` |
| The in-game menu's choices | `longshot_options.ini` in the game folder | `longshot_options.ini` in the game folder |
| The shader and pipeline caches, and the logs | `cache` and `logs` in the game folder | `cache` and `logs` in the game folder |

- **Caches and logs:** the game keeps its shader and pipeline caches in the game folder's `cache` folder (several GB, made
  for this version and this graphics card) and its logs in `logs`. `cache` can be deleted at any time: the next start
  prepares the shaders again (minutes on some PCs). A game folder from an older build has them loose beside the game: the
  first start moves them into these folders.
- **Saves elsewhere:** start the game with `--user <folder>` ([command line arguments](command-line.md#running-the-game-from-the-command-line)).
  A `user` folder from an earlier Linux build is moved to `~/.local/share/longshot` once. To carry on with a save from
  Hookshot, an emulator (Lichii, Eglantine, Orchid, ...) or NX2navi, or to take yours there, see the
  [saves guide](saves.md).
- **Build files:** the translated code and compiled objects (about 4 GB) are deleted once the game is ready; none are
  needed to play. To keep them, so that an update compiles only what changed, set `LONGSHOT_KEEP_BUILD_FILES=1` for the
  build; **Free up space** in the launcher's settings deletes them later. Their folder can be moved there before a build
  (**Change**, **Default**). The romfs is hard-linked instead of copied where the disk allows it.
- **Uninstall:** uninstall the launcher (Windows: *Apps & features*), then delete its folder, the game folder and the
  shortcuts.

## What Longshot connects to

Longshot uploads nothing about you or your PC, and reads no browser data, passwords or session keys. This is
everything it does on the network or with other programs:

- **Downloads, from git.magicbean.dev.** The launcher reads the release lists of Longshot and of the launcher, downloads a
  version's files, and checks each one against the SHA-256 in the release's signed `SHA256SUMS`. The Windows builder's
  tools come from the links in `tools.lock` (their makers' releases, or Longshot's `tools` release), and its compiler
  from its makers' releases (llvm-mingw, Python), all checked by SHA-256. With **DLSS** on, NVIDIA's DLSS library comes
  from `github.com/NVIDIA/DLSS`. The game itself checks for nothing when it starts.
- **Your Discord status, through the Discord app on this PC.** While Discord runs, it shows "Playing Longshot" with the
  time played, through the Discord app's own local connection (a named pipe on Windows; the socket `discord-ipc-0` in
  `$XDG_RUNTIME_DIR`, `$TMPDIR` or `/tmp` on Linux). It sends "Longshot", the Longshot version, the start time and
  the icon's name. Turn it off with **Gameplay → Discord status** in the in-game menu.
- **Files outside the game folder:** the builder's folder, the settings and the saves (above), and shortcuts if you ask
  for them. **Bug report** (Gameplay tab) writes a folder in `bug-reports` in the game folder, with your home folder and
  user name taken out of its logs, and **Save my pipeline list** saves a zip in your Downloads folder: you decide whether
  to send them anywhere.

Only download Longshot from [the launcher's releases](https://git.magicbean.dev/MagicBeanDev/LongshotLauncher/releases)
and [Longshot's](https://git.magicbean.dev/MagicBeanDev/Longshot/releases) on git.magicbean.dev, or through
[longshot.magicbean.dev/download](https://longshot.magicbean.dev/download), which sends you there. Each version's files
are listed with their SHA-256 in its `SHA256SUMS`, signed by the release key, which the launcher checks. A copy from
anywhere else may not be ours.

