/* (longshot) Co-op progress sync, the game side: reads the game's flag database between frames, hands what changed to the
   merge engine (coop_state.c), and gives the game what the room has through the game's own set routines, so the game takes
   it as if the player had done it. Nothing is read or written while the game is on its title screen, loading or changing
   scene; what arrives meanwhile waits. docs/coop.md says what is shared. */
#ifndef OOT_COOP_H
#define OOT_COOP_H
#include <stdint.h>
#include "a64.h"
#include "coop_state.h"
#include "coop_proto.h"   /* (COOP_POS_TEXT, the POS flags: hos_oot_coop_players_last) */
#include "coop_gameplay.h"
#include "oot_coop_kit.h"

/* nvnQueuePresentTexture's end (host/nvn_native.c), between frames on the presenting game thread with its core held.
   With co-op off it reads one value and returns. */
void hos_oot_coop_present(Ctx *c);
/* host/nn_fs.c, at every open of a file of the game's data on any thread: notes the start of the title screen, after
   which sync waits for a save to be loaded. With co-op off it reads one value and returns. */
void hos_oot_coop_rom_open(const char *guest_path);

/* For the menu (any thread, never waiting on the game): the room's offer as the co-op thread last saw it (1 when there
   is one), the player's acceptance of the offer of that number (1 when taken or already accepted), the maker's remedies
   (1 when this game is the maker and the request was queued) and the pools' contributors (their count, at most max). */
int hos_oot_coop_offer(CoopOffer *out);
int hos_oot_coop_accept(uint32_t token);
int hos_oot_coop_presence_accept(uint32_t token);
int hos_oot_coop_is_maker(void);
int hos_oot_coop_reset_pools(void);
int hos_oot_coop_remove_key(const char *fingerprint);
int hos_oot_coop_contributors(CoopContributor *out, int max);
/* For the layer that shows the other players (host/oot_coop_players.c), from the game's threads, never waiting: 1 while
   co-op is on and the game is being played (a save loaded, Link in a map, the clock running, no load and no scene
   change, as the present last saw it); the members the player accepted, bit <member id> set (identified, not removed,
   their key accepted), as the co-op thread last saw them. */
int hos_oot_coop_playing(void);
uint32_t hos_oot_coop_shown(void);
int hos_oot_coop_presence(void);   /* accepted signed COOP_PRESENCE_* mask; 0 when context is closed */
/* Live presentation consent and its signed rules/member mask. No durable or download permission. Every adapter
   checks its own explicitly present rule; native effects additionally use owner tickets below. NULL outputs allowed. */
int hos_oot_coop_presentation(CoopRules *rules, uint32_t *members);
int hos_oot_coop_gameplay_register(const CoopGameplayWriter *writer);
int hos_oot_coop_gameplay_ticket(unsigned slot, unsigned member, CoopGameplayTicket *ticket);
int hos_oot_coop_gameplay_dispatch(const CoopGameplayTicket *ticket, const void *payload, size_t size, void *game);
/* Temporary-kit readback, never a duel permission. Current accepted context only. */
int hos_oot_coop_kit_ready(uint32_t offer, uint32_t epoch);
int hos_oot_coop_kit_token(char *out, size_t cap);
int hos_oot_coop_kit_accept(const char *stamp, uint32_t token);
int oot_coop_kit_step(Ctx *c, OotCoopKitRun *run, const OotCoopKitWork *work);
extern volatile int hos_oot_coop_kit_active;
void hos_oot_coop_kit_update(Ctx *c);

/* The other players in the world (host/oot_coop_players.c; docs/coop.md). _start: once, from co-op's start, whether
   co-op is on or not (the Co-op tab may switch it on later): registers the sole body writer on the supported build.
   The accepted signed presence permission gates all map/pose disclosure and native effects; no allocation happens
   before an authorized body effect. _update: each update of Link (host/hos_main.c
   hos_oot_lift_player), on the guest thread that runs it; until co-op is on and a save is played it reads two values
   and returns. _tick: each present of hos_oot_coop_present on the presenting thread, with whether a save was loaded
   since the title screen and whether the game is being played (gate): outside play, and in play while an event holds
   Link, it says the map and state to the room (the title screen: no map; a load or map change: the last map), every
   2 s and at once at a change; never waiting. _last: for the Co-op tab, any thread: 1 when member id was heard in its
   current session, with its last area (a map's name, "" on the title screen), its flags (COOP_POS_ADULT, _IN_WORLD,
   _LOADED, _SLOW: coop_proto.h) and the age of that message in ms; 0 when nothing was heard from it, it is no member
   of the room, or the layer is not running (another build of the game). area, flags and age_ms may be NULL. */
void hos_oot_coop_players_start(void);
void hos_oot_coop_players_update(Ctx *c, uint64_t actor);
void hos_oot_coop_players_tick(int loaded, int open);
int hos_oot_coop_players_last(int id, char area[COOP_POS_TEXT + 1], uint8_t *flags, uint64_t *age_ms);
/* Copied newest pose, only for the still-current accepted presentation ticket. Freshness is returned, not assumed. */
int hos_oot_coop_players_snapshot(int id, CoopPos *out, uint64_t *age_ms);
int hos_oot_coop_players_mismatch(int member, uint64_t session);

/* host/nn_fs.c: the save folder copied, under the lock the save mount's writes take, to <user>/<folder>/<prefix><UTC
   time>/ (the newest keep copies without a prefix are kept; prefixed ones stay until the player deletes them). 1 and
   the copy's path in out ("" when nothing was saved yet), 0 when it failed, -1 when a save file is being written: try
   again later. */
int hos_save_copy(const char *folder, const char *prefix, int keep, char *out, size_t cap);
#endif
