# Achievement icons

The icons the Insert menu's **Achievements** page and the unlock pop-up draw ([docs/achievements.md](../../docs/achievements.md)).
The build packs every PNG in this folder into one texture (an atlas) by itself: you only add or replace PNG files here,
then build the game again. You never make or edit the atlas.

## The files

Two per achievement, named after its **id** (the first column of `OOT_ACHIEVEMENTS` in
[gpu/vk/oot_achievements.h](../../gpu/vk/oot_achievements.h)):

| File | Shown |
|---|---|
| `<id>.png` | once the achievement is unlocked |
| `<id>_locked.png` | while it is locked |

For example `song_zelda.png` and `song_zelda_locked.png`, or `temple_forest.png` and `temple_forest_locked.png`.

Either file may be missing:

- no `<id>.png`: the menu draws its own gold medal instead;
- no `<id>_locked.png`: the unlocked icon is drawn darkened, or an empty ring if there is no unlocked icon either.

## The format

- **PNG, 8-bit RGBA** (or 8-bit RGB, read as opaque), **not interlaced**. Most programs' plain "Export PNG" writes this;
  turn off "interlaced" / "Adam7" if your program offers it. Palette (indexed) and 16-bit PNGs are not read.
- **At most 256 x 256 pixels.** 128 x 128 is plenty: the page draws an icon about 26 points across and the pop-up about
  44, scaled with the window. Square is best; a non-square icon is fitted into a square with its shape kept.
- **Transparency is kept**: draw on a transparent background for a round or irregular icon.
- The build stops with a message naming the file if one is larger than 256 x 256, or if its name is no achievement's id
  (a misspelled file is never left out quietly).

## Making and checking them

- `python -I scripts/make_achievement_icons.py` draws the default set into this folder again (it overwrites every icon).
  Add `--out <folder>` to draw them somewhere else, and `--sheet sheet.png` to see them all on one picture.
- `python -I nx2/achievement_icons.py out.h` builds the atlas's header on its own: a quick check that every file in the
  folder is read before a whole build.
- `python -I -B tests/test_oot_achievements.py` checks the folder: every achievement has both icons, none is too large.
  Remove a check there only if leaving an achievement without an icon is what you want.

## The rule for art in the repository

Icons committed here are part of Longshot and licensed with it (GPL-3.0-or-later). Only add art you made yourself or may
license that way: **nothing from the game** (no item sprites, icons or textures from a dump, traced or not) and nothing
from anyone else. Say who made a replaced icon in [PROVENANCE.txt](PROVENANCE.txt). The default set is drawn from plain
shapes by the script above.

Art you want only for yourself does not belong in the repository: keep your own build's copy of this folder, or wait for
icon packs from the game folder's `mods` folder (planned: see docs/achievements.md).
