# Portable Longshot Builder on Windows

The Builder has no window: players use the [Longshot Launcher](https://longshot.magicbean.dev/download), whose
[portable zip](https://longshot.magicbean.dev/download/launcher/windows-portable) keeps its data beside it. This page
is for the Builder's command line (`--cli`, as RecompLauncher runs it) kept on an external SSD.

Put the release's `LongshotBuilder.exe` setup in a writable folder on your SSD, then run it with `--portable` before
`--cli` and make_game.py's options, for example:

```powershell
.\LongshotBuilder.exe --portable --cli --nup "E:\your-game.nup"
```

The setup creates a marked `LongshotBuilder` data folder beside itself. Keep this folder: it contains the installed
Builder, its build tools, SDK downloads you opt into, and the default build files. The normal setup continues to use
`%LOCALAPPDATA%\LongshotBuilder`; portable mode does not import that installation.

The default game folder is `Longshot` beside the data folder. Keep the game and Builder on the same volume. You can
move their containing folder or change that volume's drive letter: the game records a relative association to its
Builder.

The original setup finds its marked data folder beside it again on later `--cli` runs without the flag, and the
installed Builder finds its marked home when it is started directly. A run with `--cli --repair` checks every part
again and unpacks the version anew, in that same home. No user/system environment variables or registry configuration
are installed. Windows, .NET and third-party tools can still create their own temporary files and operating-system
caches; this is not a zero-write or sandbox mode.

Do not remove `.longshot-portable` from the data folder or `.longshot_builder_home` from a game built in portable
mode. An invalid association stops the setup rather than silently using another Builder. The game can still be played
without the Builder, using its game launcher (`longshot.exe` in the game folder). An interrupted installation keeps
staged files separate from the published version. During portable updates, unchanged parts are copied instead of moved
out of the installed Builder, so enough free space for both versions is needed. A repair publishes its complete staged
directory before discarding the replaced one. If Windows blocks restoration after a failed rename, the preserved old
directory remains beside it as `<version>.rollback-*`; close programs using these folders before recovering it.

Portable mode requires the packaged setup launcher; a source checkout without that launcher cannot produce a
relocatable portable game's association. Rebuilding or updating a portable game with the standard Builder removes
that association after successful output finalization.

For scripted child processes the setup supplies an absolute `LONGSHOT_SETUP_BASE` and
`LONGSHOT_BUILDER_PORTABLE=1` to that process tree only. A portable root must have the valid marker, be a dedicated
ordinary drive/UNC directory and fit the native setup's Windows path limits. Do not use the current directory, device
namespace paths or a volume root as a substitute. Portable support described here is Windows-only.

Never share a built game folder, extracted files, saves, caches or downloaded SDK packages. The contribution rules
in [CONTRIBUTING](../CONTRIBUTING.md) apply equally to portable installations.
