# Achievements

Status: **built**: the tracking, the unlock file, the pop-up and its row, the Insert menu's Achievements page and the
icons. Run in the game on an RTX 3060 Ti (Windows 11): see Testing. Still to come: **Hero of Time**, work in progress (WIP)
until its signal is found.

## Why

Players of the PC port have no record of milestones beyond the save itself. Achievements give a light, optional
sense of progress (the sword, the songs, each temple) with a short pop-up when one is earned and a list in the Insert
menu, and they let the Longshot Launcher show the same list later.

## What the player sees

- **A pop-up** at the top centre for four seconds when an achievement unlocks: a gold medal, "Achievement unlocked",
  the title, its description and "n of m". Drawn with the menu and the FPS counter (`gpu/vk/vk_menu.cpp`
  `draw_achievement_toast`), never in the game's own rendering. More than three at once (a late save loaded for the
  first time) show as one pop-up that counts them. Off with the Gameplay tab's **Achievement alerts** row
  (`achievement_alerts=` in longshot_options.ini, on by default); achievements are earned and kept either way.
- **An Achievements page** in the Insert menu (Gameplay tab, **Achievements**): every achievement as a card with its icon,
  title and what it asks for, the locked ones dimmed, and "n of m" in the banner; the help box shows the selected one's
  status, and says so while the Debug tools pause them. Up and Down, Page Up and Down, Home and End, the wheel and clicks
  move through it; Esc, B or a right click go back (the Teleport page's layout and keys, `draw_achievements`).
- **Icons**: `assets/achievements/<id>.png` (unlocked) and `<id>_locked.png` (locked), drawn by
  `scripts/make_achievement_icons.py` from plain shapes (see that folder's `PROVENANCE.txt`). `nx2/achievement_icons.py`
  builds them into a header of the build's own, as `nx2/logo_rgba.py` does the logo; `gpu/vk/vk_imgui.cpp` loads them once.
  A missing icon falls back to a medal drawn in code (a missing locked one to the unlocked one darkened); a PNG whose name
  is no achievement's id stops the build. Anyone can replace one with their own art under the same name.
- **Nothing unlocks for the rest of a run in which a Debug tool was used** that hands out items or moves Link: the
  inventory editor, teleport, the age switch, the clock (the game side sees each of their actions run,
  `hos_oot_achievements_cheated`). Turning the tools on, flight, the free camera or movement speed alone pause nothing.
  The pause lasts until the game restarts, and the Achievements page says so. It cannot tell what an earlier run's tools
  put in a save: a later run without them unlocks what that save holds.

## The list

Every signal below is one the game side already reads twice a second for the Inventory page
(`gpu/vk/oot_cheat_items.h`, `host/hos_main.c` `oot_inventory_refresh`), so tracking adds no new guest reads.

| Achievement | Unlocks when Link holds | Notes |
|---|---|---|
| **Ready for Adventure** | `Weapon_Sword_Kokiri` and `Weapon_Shield_Deku` | the sword and shield in Kokiri Forest |
| **Fairy Ocarina** | `Item_Ocarina_Fairy` | |
| **Ocarina of Time** | `Item_Ocarina_Time` | |
| **Kokiri's Emerald** | `Item_Stone_Forest` | also marks Inside the Deku Tree complete |
| **Goron's Ruby** | `Item_Stone_Fire` | also marks Dodongo's Cavern complete |
| **Zora's Sapphire** | `Item_Stone_Water` | also marks Jabu-Jabu's Belly complete |
| one per song (12) | `Item_Melody_ZeldaLullaby` ... `Item_Melody_LightPrelude` | Zelda's Lullaby, Epona's Song, Saria's Song, Sun's Song, Song of Time, Song of Storms, and the six warp songs |
| **Forest Temple** | `Item_Medal_Forest` | the medallion is given after the boss and the sage, so it marks the temple complete |
| **Fire Temple** | `Item_Medal_Fire` | |
| **Water Temple** | `Item_Medal_Water` | |
| **Shadow Temple** | `Item_Medal_Shadow` | |
| **Spirit Temple** | `Item_Medal_Spirit` | |
| **Big Bomb Bag** / **Biggest Bomb Bag** | `Item_BombBag_Medium` / `_Large` | a tier counts if that tier *or a larger one* is held, since the game may drop the smaller bag |
| **Big Quiver** / **Biggest Quiver** | `Item_Quiver_Medium` / `_Large` | as above |
| **Big Bullet Bag** / **Biggest Bullet Bag** | `Item_BulletBag_Medium` / `_Large` | as above |
| **Hero of Time** *(WIP)* | *not known yet* | defeating Ganon: not in the list until its signal is found (open questions) |

29 achievements now, 30 with **Hero of Time**. The Light Medallion is left out on purpose: it is given at the Temple of
Time, not for finishing a temple. It could be its own achievement if wanted.

## How it works

1. **One shared list.** `gpu/vk/oot_achievements.h` holds the list as an X-macro (id, title, description, condition),
   the same pattern as `oot_cheat_items.h`, so the game side and the menu can never disagree on order or names. The
   `id` is a stable short string (`song_zelda_lullaby`) used in the unlock file; titles can change freely.
2. **Checking.** `oot_inventory_refresh` already has every item's count each half second. After it fills them, it calls
   `hos_oot_achievements_check(names, counts, n, loaded)` (`host/oot_achievements.c`), which evaluates each locked
   achievement's condition. No game hook, no guest call, no extra memory reads.
3. **Unlocking.** A newly met condition (with a save loaded, and no debug tool used in this run: the game side decides
   that itself and tells the menu with `OOT_ACHIEVEMENTS_PAUSED_ID`; nothing is added to `vk_api.h`, which stamps the
   shader caches, so this change makes no player prepare shaders again) rewrites the unlock file and tells the menu through the existing `rx_option_set` channel:
   `OOT_ACHIEVEMENT_BASE + i` for its state and `OOT_ACHIEVEMENT_TOAST_ID` for the pop-up. Each unlock is logged
   (`achievements: unlocked song_zelda (Zelda's Lullaby)`), and the start logs `achievements: n of m unlocked`.
4. **The unlock file.** `user/achievements.txt` beside the saves (`hos_user_dir`), one line per unlock:
   `song_zelda 2026-11-14T18:03:22Z` (UTC). Written as a whole to `achievements.txt.tmp` and moved over it, so a crash
   never leaves half a file. Plain text on purpose: the C side needs no JSON parser, and the launcher can read it as
   easily. Unknown ids are kept (a newer build's), so going back a version loses nothing. A failed write is logged once;
   the unlocks stay for the run and the next write carries them all.
5. **Per install, not per save slot.** An achievement earned in any slot stays earned, as on consoles. Loading a save
   that already has items unlocks them at once (so imported saves and old adventures count).

## Open questions (for the maintainers)

- **Icon packs from the `mods` folder** (a follow-up): players' own icons read at run time from
  `<mods>/<name>/achievements/<id>.png`, over the built-in ones, as `host/mods.c` lays mods out. It needs a PNG decoder in
  the renderer (stb_image, as a new `third_party/` part), so it is left out of the first change; the build-time folder
  (`assets/achievements/README.md`) covers the repository's own icons.

- **Defeating Ganon.** No flag for it is known in the repository. Someone with the game can find it with the control
  bridge (`docs/control.md`): subscribe with `changes: true`, beat Ganon, and see which flag flips; or log the event
  name of the ending timeline the way `hos_oot_opening` logs the opening's. Until then **Hero of Time** stays out.
- **Item names.** `oot_cheat_items.h` says some pouch names are guesses; the log's `inventory probe` line confirms
  them. Every name used here must show a count there in a real run before this merges.
- **Per install vs per save.** Per install is simplest and matches consoles. Per profile (the `save/<id>` folder)
  would need the profile id from `host/nn_fs.c`.
- **Translations.** The titles and descriptions are English for now; the menu's own translations
  (`vk_menu_i18n`) can take them in a later change.

## Testing

- **Game-free** (`tests/test_oot_achievements.py`, in `tests/run_all.py`): compiles `host/oot_achievements.c` with stubs
  and feeds it item counts. It checks that conditions fire once, all-of and any-of (bag tiers), the Debug tools' pause,
  no unlock with no save loaded, the file round trip (an earlier unlock's time kept, unknown ids kept, torn lines and a
  crash's temporary file dropped), a folder that cannot be written, and the UTC stamps. Its source checks: every pouch
  name exists in `oot_cheat_items.h`, ids are unique, the option ids are free, and the menu row, the pause and the
  refresh's call are wired.
- **In the game** (RTX 3060 Ti, Windows 11, 2026-11-14, the first part): a new game; the Kokiri Sword and Deku Shield
  unlocked Ready for Adventure with its pop-up; with Achievement alerts off an unlock is kept without one; a save that
  already holds the items unlocks them when it loads; nothing unlocks once the Debug tools are on; the Light Medallion
  counts for nothing; eight at once (stones and medallions) showed as one "8 achievements unlocked" pop-up. The probe's
  counts confirm the sword, shield, stone and medallion names. **Not yet confirmed: the songs** (the Debug tools'
  inventory editor could not hand them out, so their names are unproven until one is learned in play) and the bag tiers.

## The launcher (a later, separate pull request)

The launcher's own pull request reads `user/achievements.txt` and shows the list with the same titles. The list
itself would be shared as data (a small `achievements.json` generated from `oot_achievements.h` and shipped with each
build) so the launcher never hard-codes it.
