/* (longshot) The Insert menu's Skip title screen row: the most recent save, and the presses that load it (oot_autoload.c). */
#pragma once
#include <stddef.h>
#include <stdint.h>

/* Input and player-update hooks read this one demand word. Only a request or an armed title/resume sequence wakes them.
   Read with __atomic_load_n(..., __ATOMIC_ACQUIRE); nonzero is active. Waking increments it so an older poll cannot
   clear a newer request. The input worker clears it after both jobs finish. */
extern unsigned hos_oot_menu_work;
void hos_oot_menu_work_arm(void);
/* Once the renderer's startup options are available, before controller reads need to load a save. */
void hos_oot_autoload_start(void);
int hos_oot_autoload_pending(void);

/* The newest progress.sav under user_dir/save/<profile>/slot_*, as its mtime in seconds since the epoch (0: none). path,
   when given, is set to the file found. */
uint64_t hos_oot_newest_save(const char *user_dir, char *path, size_t size);

/* Called at each controller read while the game starts (host/nn_hid.c, read_input). Reads the option once, and while it is
   on and a save exists, taps A through the title screen and its file list until the player is running. */
void hos_oot_autoload_input(void);

/* 1 while those presses run for a start that a restart in place made (host/oot_restart.c): the player's own buttons and
   sticks are then kept from the game. */
int hos_oot_autoload_alone(void);

/* (tests) forget the run's state, as after a restart. */
void hos_oot_autoload_reset(void);
