# Transfer and protect your saves

Bring your adventure into Longshot, take it to another supported program, or keep a backup before an update.
Start with **Easy beans** for the launcher buttons. **Advanced beans** has folder locations, terminal commands and manual recovery.

**Before you copy:** save your game and close every game or emulator involved. A transfer replaces the destination's save slots;
it does not merge two adventures. The launcher and script back up existing destination saves before replacing them.
Keep your own backup too, and keep save files private.

## Easy beans

Use the [Longshot Launcher](https://git.magicbean.dev/MagicBeanDev/LongshotLauncher) for these steps.
Open **Saves** from the launcher's home page, below the default version. On Windows, check that you are working with the build you want.

### Before you start

1. **Save and quit.** Close Longshot and the other game or emulator using its own menu, so it finishes saving.
2. **Keep a spare copy.** Copy your existing save folder somewhere safe before replacing it. The [save locations](#where-longshot-keeps-them) below show where to find it.
3. **Check which adventure you want.** Note the save slot and when you last played; compare them with the saves the launcher finds.

### Bring a save into Longshot

1. **Choose where it comes from.** On the Saves page, select **Import from Hookshot…** or **Import from emulator**.
2. **Pick your save.** Check the listed slots and save times. If it is on another drive or in a portable installation, use **Choose a folder**.
3. **Check the destination before copying.** For an emulator import, start Longshot and save once first so its save folder exists, then close the game again. Hookshot import can create a new save folder.
4. **Complete the import.** Hookshot import asks before replacing an existing Longshot save. Keep the backup location the launcher reports.
5. **Check your adventure.** Start Longshot and load the expected slot. Keep the original and backup until you are happy the transfer worked.

The transfer copies files; it does not move or convert them. Importing from Hookshot leaves its original saves untouched.

### Take a save out of Longshot

1. **Prepare the destination.** Start the target emulator, make a save once so its folders exist, then quit it. Close Longshot too.
2. **Open Saves → Export to emulator.** Choose the target emulator, or **Choose a folder** for another location.
3. **Check the result.** The destination's existing save is backed up first. After copying, open the destination program and check the expected slot.

The launcher does not export to Hookshot. Use [the script's Hookshot transfer](#from-hookshot) for that direction.

### Moving between Longshot builds

- **Windows:** open the destination build's Saves page and use **Copy saves from another build**. Pick the build with the adventure you want to keep.
- **Linux and Steam Deck:** standard builds already share the same saves; you usually do not need to copy anything. A custom `--user` folder is an exception; see [save locations](#where-longshot-keeps-them).

### Need to undo a transfer?

Close the game first. Keep a copy of the current save folder, then restore the backup from before the transfer using
[Copy by hand](#copy-by-hand). Restore the whole set of slots rather than mixing old and new ones.

Automatic backups are in `%LOCALAPPDATA%\Longshot\oot-save-backups` on Windows, or
`~/.local/share/oot-save-backups` on Linux (`$XDG_DATA_HOME/oot-save-backups` if set).
Choose the backup for the transfer you want to undo; keep it until the restored save loads correctly.

If something looks wrong, stop before copying again. Check [Troubleshooting](#troubleshooting) or
[ask the beans on Discord](https://discord.gg/KX99srmQTe). Describe what happened without posting your save files.

## Advanced beans

For custom folders, portable installs, multiple profiles and command-line transfers. You can preview a script transfer
with `--dry-run` before making changes. The launcher steps above follow its [maintained saves guide](https://git.magicbean.dev/MagicBeanDev/LongshotLauncher/src/branch/main/docs/saves.md).

Longshot, Hookshot and the supported emulators use the same save-file layout. `scripts/oot_saves.py` copies the files without conversion.
See [What was tested](#what-was-tested) for the limits of the transfer checks.

### The files

Every program keeps one folder per user profile, holding:

```
account.sav
option.sav            (the game's own settings: camera, text speed, ...)
slot_00/ ... slot_NN/ (one per save slot)
  progress.sav
  caption.sav
```

Only the folder they are in differs.

### Where Longshot keeps them

| | Where |
|---|---|
| Linux and Steam Deck | `~/.local/share/longshot/save/<user id>/` (`$XDG_DATA_HOME/longshot` when it is set). Every build uses it, so a rebuilt game folder keeps its saves |
| Windows | `<game folder>\user\save\<user id>\`. The builder's game folder is `%USERPROFILE%\Games\Longshot` unless you chose another; each game folder has its own saves |

`<user id>` is `5a3c9e1f0b7d24680000000000000001`, the same in every Longshot and Hookshot build. Started with
`--user <folder>` ([command line arguments](../README.md#running-the-game-from-the-command-line)), Longshot keeps its
saves in `<folder>/save/<user id>/` instead.

### The script

`scripts/oot_saves.py` needs Python 3 and nothing else. Run it from the Longshot folder (or a clone):

```sh
python -I scripts/oot_saves.py list              # every save it finds, its slots and when each was saved
python -I scripts/oot_saves.py import hookshot   # Hookshot -> Longshot
python -I scripts/oot_saves.py import lichii     # Lichii -> Longshot
python -I scripts/oot_saves.py export lichii     # Longshot -> Lichii
python -I scripts/oot_saves.py import --from ~/Downloads/my-save   # any folder holding a save -> Longshot
python -I scripts/oot_saves.py export --to ~/Lichii/user           # Longshot -> a portable emulator
```

`import` copies into Longshot, `export` copies out of it. The names are `hookshot`, `lichii`, `frizti` (or `fritchii`),
`orchid`, `pokedinner`, `eden`, `eglantine` and `nx2navi`.

On Windows, a game folder other than `%USERPROFILE%\Games\Longshot` has to be named:

```powershell
python -I scripts\oot_saves.py import lichii --longshot "D:\Games\Longshot"
```

| Option | What it does |
|---|---|
| `--dry-run` | Says what it would copy and where, and changes nothing |
| `--keep-settings` | Leaves the target's `option.sav` alone, so each program keeps its own settings |
| `--profile <id>` | The emulator's profile to use. Needed when more than one profile has saves: the script lists them and stops rather than pick one |
| `--from <folder>`, `--to <folder>` | The other side: the save folder itself, or a folder above it to search (a data folder, a portable `user` folder, a game folder). With a name, it replaces where that program is looked for; without one, any folder works |
| `--longshot <folder>` | Longshot's data folder (Windows: the game folder), or its `save/<user id>` folder |
| `--hookshot <folder>` | The same for Hookshot, with `import hookshot` or `export hookshot` |
| `--backup-dir <folder>` | Where the backup goes, outside the target save folder |
| `--force` | Copies even if a game seems to be running, or NX2navi has a save waiting |

Every copy:

- **refuses to start while Longshot, Hookshot or one of the emulators runs**, since a running game can write its save
  again on exit and undo the copy;
- **backs up the side it overwrites first**, to `oot-save-backups/<import or export>-<name>-<date>-<time>/`: in
  `~/.local/share` on Linux, in `%LOCALAPPDATA%\Longshot` on Windows. To undo a copy, put the backup's contents back;
- replaces the target's `slot_*` folders, `account.sav` and `option.sav`, so a slot that only existed on that side does
  not stay mixed in with the new save;
- refuses a save that holds a link (it would copy whatever the link points to).

When Longshot (or, for `export hookshot`, Hookshot) has no save yet, the copy makes its save folder: on Linux in the data
folder, on Windows in the game folder, which has to exist. An emulator has to have been started and saved in once, so
that its own folder exists.

### From Hookshot

[Hookshot](https://github.com/NotRexed/Hookshot) is the project Longshot was forked from. Its game writes the same files
in the same layout, with the same user id, so a Hookshot save carries on in Longshot as it is: the files are copied byte
for byte.

```sh
python -I scripts/oot_saves.py import hookshot
```

It looks for Hookshot's save in:

| | Where |
|---|---|
| Linux | `~/.local/share/hookshot/save/<user id>/` (`$XDG_DATA_HOME/hookshot`) |
| Windows | `%USERPROFILE%\Games\Hookshot\user\save\<user id>\`, then the version folders Hookshot's launcher made under `%USERPROFILE%\Games\Hookshot` |

For a Hookshot game folder somewhere else, pass it: `import hookshot --hookshot "D:\Games\Hookshot"`.

Hookshot's folder is only read. Before anything is copied, the save is checked as the launcher checks it: only
`account.sav`, `option.sav` and `slot_NN` folders holding `progress.sav` and `caption.sav`, no link, no empty file and
none over 4 MB, at least one slot and a `progress.sav` in each. If it fails, the script says why and copies nothing.

`export hookshot` takes your Longshot save back to Hookshot the same way.

### From and to the emulators

| Program | Linux | Windows |
|---|---|---|
| [Lichii](https://github.com/zortos293/Lichii) | `~/.local/share/lichii/` + Eden's save path (before build 150: `~/.local/share/eden/`) | `%APPDATA%\lichii\` + Eden's save path (before build 150: `%APPDATA%\eden\`) |
| [Frizti (Fritchii)](https://github.com/Frizti/Lichii), a Lichii fork | (Windows only) | `%APPDATA%\eden\` + Eden's save path |
| [Orchid](https://github.com/MythosOfs/Orchid) | `~/.local/share/eden/` + Eden's save path | `%APPDATA%\eden\` + Eden's save path |
| [PokeDinner](https://git.eden-emu.dev/Franseven/PokeDinner-a-Eglantine-Fork) | `~/.local/share/eden/` + Eden's save path | `%APPDATA%\eden\` + Eden's save path |
| [Eglantine](https://github.com/Djihads80/Eglantine) | `~/.local/share/eglantine/` + Eden's save path | `%APPDATA%\eglantine\` + Eden's save path |
| [NX2navi](https://github.com/tangerineturtle1/NX2navi) | `<game folder>/profile/save/<account>/` (its AppImage builds into `~/Games/Ocarina`) | `<game folder>\profile\save\<account>\` (its launcher's game folder is `%LOCALAPPDATA%\Programs\NX2navi\game` unless you chose another) |

Eden's save path, under that data folder: `nand/user/save/0000000000000000/<profile id>/0400486024DEC000/`.
`0400486024DEC000` is the game's title id, and `<profile id>` the emulator's user profile you play with.

- **Frizti, Orchid and PokeDinner share Eden's folder**: a save there is the same save in all of them. Eglantine has its
  own, and so has Lichii since its build 150 (2026-11-10); the script looks in `lichii` first and uses `eden` when there
  is no save in it.
- **Portable installs.** The emulators use a `user` folder beside the program (Linux: in the folder you start it from)
  instead of the paths above when that folder exists. Point the script at it with `--from`/`--to`.
- Newer Eden code reads a save from `nand/user/save/account/<profile>/0400486024DEC000/0/` first when that folder exists.
  The script looks there too.
- **NX2navi** writes a save to `profile/save-tx` first and moves it into place after. While something is waiting there,
  that is the save the game loads next, so the script refuses to copy until NX2navi has been started and quit (or
  `--force`). NX2navi's launcher also has its own **Import Saves**.
- `$XDG_DATA_HOME` replaces `~/.local/share` when it is set.
- Ryujinx and its forks don't run Switch 2 games. Any other program that keeps these files works through `--from`/`--to`
  with the folder that holds them.

The emulator paths come from each one's source (`src/common/fs/fs_paths.h`, `path_util.cpp`,
`src/core/file_sys/savedata_factory.cpp`), NX2navi's from its documentation (`docs/launcher.md`, `docs/installer.md`,
`docs/README-BuildKit.md`). Official Eden does not run Switch 2 games; its name is accepted because the forks above use
its folder.

### Copy by hand

1. Close both programs.
2. Copy the target save folder somewhere safe.
3. Delete the target's `slot_*` folders, so a slot that only exists on that side does not stay mixed in.
4. Copy the **contents** of the save folder you are leaving (`account.sav`, `option.sav`, the `slot_*` folders) into the
   one you are going to. Skip `option.sav` to keep the target's settings.

### Troubleshooting

| What you see | What to do |
|---|---|
| `close ... first` | Quit the game or emulator it names (from its menu, so it saves), then copy again. `--force` only if it is wrongly seen as running |
| `no Longshot save found` (Windows) | Your game folder is not `%USERPROFILE%\Games\Longshot`: pass it with `--longshot <game folder>` |
| `no <program> save found` | Start the game in that program once and save, or pass its folder with `--from`/`--to` (a portable install, another drive) |
| `... saves in 2 profiles, so pick one` | Pass `--profile <id>` with the profile you play on, from the list it prints |
| `NX2navi still has a save waiting` | Start NX2navi, load and quit from the game, then copy |
| `is not a Hookshot save that can be imported` | The folder holds something besides save files: point `--hookshot` at the save folder itself or at the game folder |
| `is a link` | The save holds a symbolic link or a junction. Copy the real files into a plain folder and import from that |
| The save is wrong after a copy | Close the game, and put the backup's contents (the folder the script named) back into the save folder |
| The settings changed | `option.sav` came with the save. Copy again with `--keep-settings`, or restore `option.sav` from the backup |

Backups pile up, one per copy: delete old ones from `oot-save-backups` when you no longer need them.

### What was tested

- Linux (Arch-based), Hookshot v2.0.5 and Lichii built from source on the same day.
- A Hookshot save copied to Lichii loads in Lichii at the same spot (Kokiri Forest, the spawn by the flower patch).
- A Lichii save copied to Hookshot arrives identical, but has not been played through on the Hookshot side.
- The script's discovery for every program above, its copies, backups and checks are covered by
  `tests/test_oot_saves.py`, on temporary folders with made-up saves.
- **Not tested:** Windows, every emulator but Lichii, and a Hookshot save played on in Longshot. That one rests on the
  code: Longshot's save code is Hookshot's, unchanged by the rename (the same user id and folder layout, and the files
  are the game's own). The emulators' folders come from their source or, for NX2navi, its documentation.
