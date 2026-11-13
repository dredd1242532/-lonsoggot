// Co-op, the other players' places: what a game keeps of the position messages (POS, coop_proto.h) the other members
// of its room send, and which of them it shows. No game and no socket here: host/oot_coop_players.c reads Link's place,
// sends it through the session (coop.h) and shows the others in the world; this file decides what is taken and shown.
//
// A position is taken only from a member the roster lists (never from this player's own id), in a message that parses
// (coop_pos_parse: its version, printable names, finite numbers), with a place within COOP_PLAYERS_FAR metres of the
// origin, and above the last one taken from that member in that session (coop_pos_fresh). A member is shown only
// while the player accepted it (the mask the caller passes: progress sync's accepted and identified members), it was
// heard within COOP_PLAYERS_SILENT_MS, its game sends at the full rate (COOP_POS_SLOW clear), its Link stands in a map,
// and that map is this game's. Not thread safe: the caller holds its lock.
//
// Every game in a room sends its POS while co-op is on, whether it shows the others or not: at the full rate
// (COOP_PLAYERS_SEND_MS) while it shows them and its Link stands in a map; otherwise (it shows nobody: COOP_POS_SLOW;
// or no save is played, or it loads, changes map, pauses or an event holds Link: COOP_POS_IN_WORLD clear) only every
// COOP_PLAYERS_SLOW_MS and at once when its map or its flags change, so that the Co-op tab of every game can say where
// each member is (coop_players_last).
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "coop_proto.h"

#define COOP_PLAYERS_HIST 8          // positions kept per member
#define COOP_PLAYERS_SILENT_MS 3000  // a member not heard for this long is hidden
#define COOP_PLAYERS_BEHIND_MS 100   // a member is shown where it was this long ago, between two positions received
#define COOP_PLAYERS_SEND_MS 66      // at most one position sent in this time (15 a second)
// The low rate: below COOP_POS_QUIET_MS (3 s), so a receiver keeps its counter between two of them and a recorded one is
// not taken again (coop_pos_fresh), and below COOP_PLAYERS_SILENT_MS, so a member at the low rate never reads as gone;
// one message in 2 s is a thirtieth of the full rate's
#define COOP_PLAYERS_SLOW_MS 2000
#define COOP_PLAYERS_FAR 100000.0f   // metres from the origin: no map of the game is this large

typedef struct {
    CoopPos pose[COOP_PLAYERS_HIST];        // the newest at head
    uint64_t recv_ms[COOP_PLAYERS_HIST];
    int n, head;
    int32_t offset;                         // this clock minus the sender's, the smallest seen (1 ms more a message, so it follows)
    CoopPosSeen seen;                       // the sender's counter
} CoopPlayerTrack;

typedef struct {
    CoopPlayerTrack r[COOP_MAX_MEMBERS + 1];   // by member id
    uint64_t session[COOP_MAX_MEMBERS + 1];
    uint8_t listed[COOP_MAX_MEMBERS + 1];      // the roster lists this id
    int self, others;                          // this player's id, the other members listed
    uint32_t pseq;                             // the counter of this player's own positions
    uint64_t sent_ms;
    int sent_any;
    uint8_t sent_flags;                        // the flags and area of the last one sent: a change goes at once
    char sent_area[COOP_POS_TEXT + 1];
    unsigned taken, refused;                   // positions taken and refused since the room was joined
} CoopPlayers;

// The room's members as the session's view lists them (n 0: not in a room: everything kept is dropped). What was kept
// of a member no longer listed, or listed under another session, is dropped.
void coop_players_roster(CoopPlayers *p, int self, const int *ids, const uint64_t *sessions, int n);
// A POS from member from: 1 when taken, 0 when refused. A sender that starts or stops showing the others (COOP_POS_SLOW
// changes) starts its kept places anew: its slow ones are too far apart to move between.
int coop_players_receive(CoopPlayers *p, int from, const uint8_t *bytes, size_t n, uint64_t now_ms);
// This player's own position me (flags, area, pos, yaw, slots) as a POS into out, with its counter and time: its
// length, or 0 when nobody else is in the room, it does not fit, or it is not yet time: one in COOP_PLAYERS_SEND_MS at
// the full rate (me in the world, COOP_POS_SLOW clear), else one in COOP_PLAYERS_SLOW_MS; a change of the area or the
// flags goes at once (but not within COOP_PLAYERS_SEND_MS of the last one).
size_t coop_players_send(CoopPlayers *p, uint8_t *out, size_t cap, CoopPos *me, uint64_t now_ms);

enum {
    COOP_PLAYER_SHOWN = 0,
    COOP_PLAYER_ABSENT,        // not a member of the room (or this player itself)
    COOP_PLAYER_NOT_ACCEPTED,  // not among the members this player accepted
    COOP_PLAYER_NOT_HEARD,     // no position within COOP_PLAYERS_SILENT_MS
    COOP_PLAYER_SLOW,          // its game shows nobody and sends only at the low rate (COOP_POS_SLOW)
    COOP_PLAYER_NOT_IN_MAP,    // its Link stands in no map (COOP_POS_IN_WORLD not set)
    COOP_PLAYER_OTHER_MAP,     // its Link is in another map than map
};
// Whether member id is shown in the map this game is in, and where: COOP_PLAYER_SHOWN and its position of
// COOP_PLAYERS_BEHIND_MS ago in out (between the two positions received around then; the newest one when that is
// older), or the reason it is not (out: the newest position for COOP_PLAYER_SLOW). accepted: bit id set for a member
// the player accepted. out may be NULL.
int coop_players_show(const CoopPlayers *p, int id, uint32_t accepted, const char *map, uint64_t now_ms, CoopPos *out);
const char *coop_players_why(int reason);
// For the Co-op tab: 1 when a position was taken from member id in its current session (since it joined or this game
// did), with the newest one in out (its area, flags: ADULT, IN_WORLD, LOADED, SLOW) and how long ago it arrived in
// age_ms; 0 when nothing was heard from it, or it is no member of the room (or this player). Whether the player
// accepted the member is the caller's to ask. out and age_ms may be NULL.
int coop_players_last(const CoopPlayers *p, int id, uint64_t now_ms, CoopPos *out, uint64_t *age_ms);
