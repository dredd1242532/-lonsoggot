/* (longshot) Co-op, the other players in the world (oot_coop.h; docs/coop.md), for the supported build e0f3ccfa.

   A child game with the accepted signed presentation permission says where its Link is (POS, coop_proto.h).
   The local display switch chooses precise poses or low-rate map/state only (hos_oot_coop_players_last).
   A game that shows others sends its map, child position and facing about 15 times a second while being played,
   and shows each member whose child Link is in the same map as an actor at that place: made by the game's routine for
   making an actor by its file name, moved every frame, and removed when the member leaves the map or the room, is not
   heard for 3 s, or is not a member the player accepted (coop_players.h has the rules; this file is the game's side).
   A permitted game that shows nobody (the local display switch off) sends no place: only its map and play state
   (COOP_POS_SLOW), every 2 s and at once at a change; the title screen (no save: no map) and a load or map change
   (the last map) go out the same way from the present (hos_oot_coop_players_tick), as does an event that holds Link
   for 2.5 s. Nothing is made in the game then: what arrives is kept for the reader.

   Where it runs. Everything that calls the game runs in Link's own update (host/hos_main.c hos_oot_lift_player, the
   hook nx2/oot_patches.py patch_movement puts into f_82c415e0), on the guest thread that updates him, as the game's
   own actors ask for actors from their updates. Nothing is made, moved or removed unless progress sync's test says the
   game is being played (hos_oot_coop_playing: a save loaded, Link in a map, no load, no scene change) and no event is
   playing (a cutscene, a conversation, a map change's event: EventMgr +104). When the game stops being played (a map
   change begins, a save loads) every actor made here is removed at that update, before the map goes.

   The game's routines (their addresses are this build's):
   - The create request f_80392a70(manager [0x84eb1888], x1 the address of the actor file name's pointer, x2 a
     placement, x3 a cell the game puts its request into, w4 1, x5..x7 0; on the stack a byte 0, a pointer to an
     outcome word, 0). The game loads Pack/Actor/<name>.pack and makes the actor over the next frames. The placement:
     +0x00 position, +0x0c a 3x3 rotation (row-major), +0x30 scale, +0x40 a pointer to "", +0x98 a byte 1 (as the
     game's own node that makes an actor in play sets it).
   - The request's first word holds its state in the low 16 bits: 3 when the actor is made. The asker then takes the
     actor, f_8038fbd0(request, 1, 0): that puts it in the actor system's list, from where it enters the map (actor
     state 4 at +0x2c) and is updated and drawn. An actor never taken is never drawn. State 4: the game gave the
     request up (dropped with f_8038fde0); 5 and 6: it failed.
   - A request whose actor is not yet taken is cancelled: found in the queue of pending creates (f_80b4e020 over
     [[0x84eb0d90] + 0x458] with a matcher {vtable 0x84c795e0, the request cell's address}) and freed (f_80b4e6e0), or
     its actor deleted (f_80b4e2e0(request, 1)).
   - A taken actor is removed with the game's generic delete, f_800c6490(actor, 0).
   - An actor is placed with the engine's transform setter, f_80c8fd60(actor, a 3x4 row-major matrix, 0), which
     hands the transform to the actor's physics body or keeps it for the actor's next update; the actor's own copies
     (+0x31c position, +0x328 rotation, +0x2ec and +0x2f8 a second copy) are written too.
   - Link: position +0x31c, rotation +0x328 (a turn about the vertical axis: yaw = atan2(m[2], m[8]), 0 facing +z),
     the name of his actor file at [+0x260] (Player_Child or Player_Adult); the map is the String64 flag
     Sequence_CurrentBanc of GameDataMgr (**0x84c9e180: container +0x608, entries of 0x108 bytes, the text's pointer
     at +0x68).
   The memory the request's cells live in is the host's own block of the guest's system region (hos_sys_alloc), since
   the game keeps their addresses over frames. Every pointer read from the game is checked against the guest window
   before it is followed. On another build of the game than e0f3ccfa, or when one of the routines is not in the
   image, the other players are not shown and one line says so.

   Switches: HOS_COOP_PLAYERS=1, or coop_players=1 in longshot_options.ini (the environment wins; 0, off, no or false
   is off, gpu/vk/compat/longshot_env.h): the other players are shown. Off, a game on the known build still says its
   map and state only with signed permission and accepted context. Without that permission or with co-op off, a
   never-active update returns before guest reads; an active adapter only retires its own existing actors.
   HOS_COOP_PLAYERS_ACTOR=<child>[,<adult>] is a local diagnostic override; this family uses only the child name.
   Without animation assets it uses the standing PouchPlayer_Child model, not a walking/full-pose fallback.
   HOS_COOP_TRACE adds a line per request, take and removal. For
   tests from the environment only (longshot_env.txt sets no HOS_COOP name: host/hos_main.c env_line_allowed):
   HOS_COOP_PLAYERS_TEST=<file> supplies member8's positions through the same admission path. It still requires that
   member's current accepted presentation ticket; the diagnostic feed is never permission. Lines are:
     <seconds> actor <name>                          the actor file shown from then on
     <seconds> at <map> <right> <up> <ahead> <turn>  metres and degrees from where Link stood and faced when the test
                                                     began; "=" for the map Link is in; between two "at" lines of
                                                     one map the place moves evenly
     <seconds> abs <map> <x> <y> <z> <turn>          a place in the map's own coordinates, facing <turn> degrees; "="
                                                     and the even move between two "abs" lines as for "at"
     <seconds> shadow <right>                        Link's own place of a second ago, that far to his right
     <seconds> gone                                  nothing sent from then on (the member falls silent)
   Seconds count from the first update of Link in play. With HOS_COOP_TRACE the test also logs Link's own place
   twice a second. */
#include "coop_net.h"   /* (first: winsock2.h before windows.h) */
#include <windows.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "hos.h"
#include "coop.h"
#include "coop_players.h"
#include "oot_coop.h"
#ifndef LONGSHOT_ENV_H   /* (env_on: the switches' one rule) */
#include "../gpu/vk/compat/longshot_env.h"
#endif

int hos_ini_option(const char *key, int otherwise);  /* (nn_applet.c: the options file) */
int hos_coop_players_assets(void);   /* mods.c: generated files indexed without a conflicting user mod */

#define PLAYERS_BUILD "e0f3ccfaf50065ae4ea01eb5aeb13b1a45da3348"
#define GAMEDATA_HOLDER 0x84c9e180ULL  /* GameDataMgr's holder (host/oot_coop.c GAMEDATA_SLOT), dereferenced twice */
#define EVENTMGR_HOLDER 0x84c9e090ULL  /* EventMgr's (host/hos_main.c OOT_EVENTMGR_SLOT): +104 an event is playing */
#define ACTOR_MGR 0x84eb1888ULL        /* [..]: the actor manager, the create request's x0 */
#define ACTOR_SYS 0x84eb0d90ULL        /* [..] + 0x458: the queue of pending creates */
#define MATCH_VTBL 0x84c795e0ULL       /* the matcher that finds a pending create by its request cell */
#define EMPTY_TEXT 0x83f04f8cULL       /* "" in the game's read-only data */
#define CREATE_FN 0x80392a70ULL
#define TAKE_FN 0x8038fbd0ULL
#define DROP_FN 0x8038fde0ULL
#define QUEUE_FIND_FN 0x80b4e020ULL
#define REQUEST_FREE_FN 0x80b4e6e0ULL
#define REQUEST_DELETE_FN 0x80b4e2e0ULL
#define ACTOR_DELETE_FN 0x800c6490ULL
#define SET_MTX_FN 0x80c8fd60ULL
#define AS_PLAY_FN 0x802222a0ULL       /* (a request block): a node by name on one of an actor's animation slots */
#define AS_FRAME_GET_FN 0x837d57c0ULL  /* (animation control, slot) -> s0 */
#define AS_FRAME_SET_FN 0x837d5d70ULL  /* (animation control, slot, s0) */
static const uint64_t game_fns[] = {CREATE_FN, TAKE_FN, DROP_FN, QUEUE_FIND_FN, REQUEST_FREE_FN, REQUEST_DELETE_FN, ACTOR_DELETE_FN, SET_MTX_FN,
                                    AS_PLAY_FN, AS_FRAME_GET_FN, AS_FRAME_SET_FN};

enum {
    A_ID = 0x18,            /* u32: the actor's number, another for each actor made */
    A_STATE = 0x2c,         /* u32: 3 made, 4 in the map */
    A_FILE = 0x260,         /* char *: the actor file's name */
    A_PARTS = 0x270,        /* -> +0x40 -> +0x18 -> +0x18: the animation control (+0x18 its slot count, +0x20 the slots, 0x48 apart) */
    A_POS2 = 0x2ec, A_ROT2 = 0x2f8, A_POS = 0x31c, A_ROT = 0x328,
    A_SIZE = 0x400,
    AS_SLOT_NAME = 0x10,    /* a slot: the name of the node it plays (char *) */
    S64_BASE = 0x608, S64_SIZE = 0x108, S64_TEXT = 0x68,   /* GameDataMgr's String64 container */
    CN_COUNT = 0x08, CN_ENTRIES = 0x10, CN_TABLE = 0x18, CN_CAP = 0x24,
    /* the host's block of guest memory for one request */
    B_NAME = 0x00,          /* char *: the create's x1 points here */
    B_REQUEST = 0x08,       /* the request: the create's x3 points here */
    B_OUTCOME = 0x10,       /* u32 */
    B_PLACE = 0x20,         /* 0xa0 bytes: the create's x2 */
    B_MATCHER = 0xc0,       /* {vtable, the request cell's address} */
    B_TEXT = 0xd0,          /* the name's bytes */
    B_SIZE = 0x120,
    SCRATCH_MTX = 0x00, SCRATCH_PLAY = 0x40, SCRATCH_NODE = 0x80, SCRATCH_SIZE = 0xc0,
    NAME_MAX_LEN = 63,
    ASK_MS = 20000,         /* a request not made after this long is cancelled */
    AGAIN_MS = 2000,        /* after a removal or a failure, the next request for that member waits this long */
    IDLE_MS = 2500,         /* in play, Link's place not read for this long (an event holds him): the present says he is in no map */
    TEST_MEMBER = COOP_MAX_MEMBERS,
    TEST_STEPS = 64,
    RING = 128,
};
#define F_CURRENT_MAP 0x1d6189dau   /* Sequence_CurrentBanc (String64) */

enum { PP_NONE, PP_ASKED, PP_SHOWN };
typedef struct {
    int state;
    uint64_t block;         /* guest: this member's request cells */
    uint64_t actor;
    uint32_t actor_id;
    char name[NAME_MAX_LEN + 1];    /* the actor file asked for */
    uint64_t asked_ms, again_ms;    /* when it was asked for; no new request before again_ms */
    char map[COOP_POS_TEXT + 1];    /* the member's map as last said in the log ("": not seen) */
    uint64_t said_ms;
    char node[COOP_POS_SLOTS][COOP_POS_TEXT + 1];   /* the node each animation slot was last asked to play */
    uint8_t plays[COOP_POS_SLOTS];                  /* the actor's animation set has that node */
} Puppet;

enum { T_ACTOR, T_AT, T_ABS, T_SHADOW, T_GONE };
typedef struct { float t; int kind; char text[NAME_MAX_LEN + 1]; float v[4]; } TestStep;

static struct {
    int on;                 /* registered with the session: Link's update and the present's tick run (atomic) */
    int show;               /* the switch: the other players are shown, and this game's place is sent at the full rate */
    int trace;
    int anim;               /* HOS_COOP_PLAYERS_ANIM: Link's animation is sent and played on the others' actors */
    char actor_name[2][NAME_MAX_LEN + 1];   /* shown for a child, an adult */

    SRWLOCK lock;           /* around store, last_me and me_ms (the co-op thread takes positions; the game's threads only try it) */
    CoopPlayers store;
    CoopGameplayTicket ticket[COOP_MAX_MEMBERS + 1];   /* admission at receive; checked again before native effects */
    CoopPos last_me;        /* Link's last place in play, as Link's update last read it: the present's tick sends from it */
    uint64_t me_ms;         /* when */

    unsigned view_version;  /* of the session's view the roster was last taken from */
    int view_any;
    uint32_t forced;        /* members shown without an acceptance: the test's made-up one */

    SRWLOCK game_lock;      /* Link's update: one guest worker at a time */
    uint64_t blocks, scratch;
    Puppet puppet[COOP_MAX_MEMBERS + 1];
    int was_playing, was_in_room, shown_now;
    unsigned made, deleted, taken_away, failed;   /* actors made; removed here; found gone (the game removed them); requests that failed */
    char own_map[COOP_POS_TEXT + 1];
    struct { int any; uint8_t flags; char area[COOP_POS_TEXT + 1]; } heard[COOP_MAX_MEMBERS + 1];   /* (HOS_COOP_TRACE: last said) */

    /* the test feed */
    int test, nsteps, at;   /* at: the steps before this one were carried out */
    TestStep step[TEST_STEPS];
    int mode, seg;          /* T_AT or T_ABS (seg: the step of its line), T_SHADOW or T_GONE */
    float side;
    uint64_t began_ms, fed_ms, own_said_ms;
    uint32_t test_pseq;
    float base_pos[3], base_yaw;
    struct { uint64_t ms; float pos[3], yaw; } ring[RING];
    unsigned ring_at;
} P = {.lock = SRWLOCK_INIT, .game_lock = SRWLOCK_INIT};

/* ---- reading the game (a pointer the game holds is followed only inside the guest window, above its first MB) */

static int rd_ok(uint64_t a, uint64_t n) { return a >= 0x100000 && hos_guest_range(a, n) != NULL; }
static uint64_t rd64(Ctx *c, uint64_t a) { return rd_ok(a, 8) ? recomp_load64(c, a) : 0; }
static uint32_t rd32(Ctx *c, uint64_t a) { return rd_ok(a, 4) ? (uint32_t)recomp_load32(c, a) : 0; }

/* s into out (cap bytes), cut to fit. */
static void text_copy(char *out, size_t cap, const char *s) {
    const size_t n = strnlen(s, cap - 1);
    memcpy(out, s, n), out[n] = 0;
}

/* The text at a (at most cap - 1 bytes) when all of it is printable ASCII, else "". */
static const char *text_at(uint64_t a, char *out, size_t cap) {
    size_t i = 0;
    out[0] = 0;
    for (; i + 1 < cap && rd_ok(a + i, 1); i++) {
        const char ch = *(const char *)GP(a + i);
        if (!ch) break;
        if (ch < 0x20 || ch > 0x7e) return out[0] = 0, out;
        out[i] = ch;
    }
    out[i] = 0;
    return out;
}

static void floats_at(uint64_t a, float *out, int n) { memcpy(out, GP(a), sizeof(float) * (size_t)n); }

static void put_floats(Ctx *c, uint64_t a, const float *v, int n) {
    for (int i = 0; i < n; i++) {
        uint32_t bits;
        memcpy(&bits, &v[i], 4);
        recomp_store32(c, a + 4u * (unsigned)i, bits);
    }
}

/* The map Link is in (Sequence_CurrentBanc), "" when it cannot be read. */
static const char *map_now(Ctx *c, char *out, size_t cap) {
    out[0] = 0;
    const uint64_t mgr = rd64(c, rd64(c, GAMEDATA_HOLDER)), cn = mgr + S64_BASE;
    if (!rd_ok(cn, 0x28)) return out;
    const uint64_t table = recomp_load64(c, cn + CN_TABLE), entries = recomp_load64(c, cn + CN_ENTRIES);
    const uint32_t cap_n = (uint32_t)recomp_load32(c, cn + CN_CAP), count = (uint32_t)recomp_load32(c, cn + CN_COUNT);
    if (!cap_n || !count || !rd_ok(table, 8ull * cap_n) || !rd_ok(entries, (uint64_t)count * S64_SIZE)) return out;
    for (uint32_t i = F_CURRENT_MAP % cap_n, k = 0; k < cap_n; k++, i = i + 1 == cap_n ? 0 : i + 1) {
        const uint32_t h = (uint32_t)recomp_load32(c, table + 8ull * i);
        if (h == F_CURRENT_MAP) {
            const uint32_t idx = (uint32_t)recomp_load32(c, table + 8ull * i + 4);
            return idx < count ? text_at(recomp_load64(c, entries + (uint64_t)idx * S64_SIZE + S64_TEXT), out, cap) : out;
        }
        if (!h) return out;
    }
    return out;
}

static int event_playing(Ctx *c) {
    const uint64_t events = rd64(c, rd64(c, EVENTMGR_HOLDER));
    return !events || rd64(c, events + 104) != 0;   /* (no event manager: nothing is known, nothing is done) */
}

/* ---- calling the game from Link's update: the guest's registers as they were afterwards */

typedef struct { uint64_t x[8]; uint64_t stack[3]; int nstack; float s0; } Call;

/* s0_out (NULL: not wanted): the routine's float result. */
static uint64_t game_call(Ctx *c, uint64_t fn, const Call *k, float *s0_out) {
    A64Fn f = a64_lookup(fn);
    if (!f) return 0;
    const Ctx saved = *c;
    const uint64_t sp = (c->x[31] - 0x100) & ~0xfULL;   /* below what the hook's function holds under its stack pointer */
    uint32_t bits;
    for (int i = 0; i < k->nstack; i++) recomp_store64_sp(c, sp + 8u * (unsigned)i, k->stack[i]);
    for (int i = 0; i < 8; i++) c->x[i] = k->x[i];
    memcpy(&bits, &k->s0, 4);
    c->vreg[0][0] = bits;
    c->x[31] = sp, c->x[30] = 0;
    f(c);
    const uint64_t r = c->x[0];
    bits = (uint32_t)c->vreg[0][0];
    if (s0_out) memcpy(s0_out, &bits, 4);
    *c = saved;
    return r;
}

/* ---- an actor asked for, taken, placed and removed */

static void yaw_rot(float yaw, float rot[9]) {
    const float s = sinf(yaw), co = cosf(yaw);
    const float m[9] = {co, 0, s, 0, 1, 0, -s, 0, co};   /* a turn about +y: the third column (forward) is (sin, 0, cos) */
    memcpy(rot, m, sizeof m);
}

/* Asks the game for actor file name at pos, turned by yaw. 1 when the game took the request. */
static int actor_ask(Ctx *c, Puppet *p, const char *name, const float pos[3], float yaw) {
    const uint64_t b = p->block, mgr = rd64(c, ACTOR_MGR);
    if (!mgr || !rd_ok(b, B_SIZE)) return 0;
    memset(GP(b), 0, B_SIZE);
    text_copy(p->name, sizeof p->name, name);
    memcpy(GP(b + B_TEXT), p->name, strlen(p->name) + 1);
    recomp_store64(c, b + B_NAME, b + B_TEXT);
    float rot[9];
    const float one[3] = {1, 1, 1};
    yaw_rot(yaw, rot);
    put_floats(c, b + B_PLACE + 0x00, pos, 3);
    put_floats(c, b + B_PLACE + 0x0c, rot, 9);
    put_floats(c, b + B_PLACE + 0x30, one, 3);
    recomp_store64(c, b + B_PLACE + 0x40, EMPTY_TEXT);
    recomp_store8(c, b + B_PLACE + 0x98, 1);
    const Call k = {.x = {mgr, b + B_NAME, b + B_PLACE, b + B_REQUEST, 1, 0, 0, 0}, .stack = {0, b + B_OUTCOME, 0}, .nstack = 3};
    const int taken = (int)(game_call(c, CREATE_FN, &k, NULL) & 1);
    const uint64_t request = recomp_load64(c, b + B_REQUEST);
    if (P.trace)
        hos_log("coop: puppet: asked the game for %s at %.2f %.2f %.2f: %s (outcome %u, request %#llx)", name, pos[0], pos[1], pos[2],
                taken ? "taken" : "refused", (unsigned)recomp_load32(c, b + B_OUTCOME), (unsigned long long)request);
    return taken && request;
}

/* The actor is still the one made for the request and alive: its number and file name are the ones noted at the take
   (a removed actor's memory may hold another), its state made (3) or in the map (4). */
static int actor_alive(Ctx *c, const Puppet *p) {
    char n[NAME_MAX_LEN + 1];
    const uint64_t a = p->actor;
    if (!a || !rd_ok(a, A_SIZE)) return 0;
    const uint32_t st = (uint32_t)recomp_load32(c, a + A_STATE);
    return (st == 3 || st == 4) && (uint32_t)recomp_load32(c, a + A_ID) == p->actor_id &&
           !strcmp(text_at(recomp_load64(c, a + A_FILE), n, sizeof n), p->name);
}

/* Cancels a request whose actor was not taken: out of the pending queue and freed, or its actor deleted. */
static void request_cancel(Ctx *c, Puppet *p) {
    const uint64_t b = p->block, request = recomp_load64(c, b + B_REQUEST), queue = rd64(c, rd64(c, ACTOR_SYS) + 0x458);
    if (!request || !rd_ok(request, 0x30)) return;
    recomp_store64(c, b + B_MATCHER, MATCH_VTBL);
    recomp_store64(c, b + B_MATCHER + 8, b + B_REQUEST);
    const Call find = {.x = {queue, b + B_MATCHER}};
    const int pending = queue && (game_call(c, QUEUE_FIND_FN, &find, NULL) & 1);
    const Call end = {.x = {request, 1}};
    game_call(c, pending ? REQUEST_FREE_FN : REQUEST_DELETE_FN, &end, NULL);
    recomp_store64(c, b + B_REQUEST, 0);
    if (P.trace) hos_log("coop: puppet: the request for %s is cancelled (%s)", p->name, pending ? "it was still waiting" : "its actor is deleted");
}

enum { TAKE_FAILED = -1, TAKE_WAIT = 0, TAKE_MADE = 1 };

/* Follows a request: once the actor is made (state 3) it is taken, as the game's own askers take theirs. */
static int actor_take(Ctx *c, Puppet *p) {
    const uint64_t b = p->block, request = recomp_load64(c, b + B_REQUEST);
    if (!request || !rd_ok(request, 0x30)) return TAKE_FAILED;
    const uint32_t st = (uint32_t)recomp_load32(c, request) & 0xffff;
    if (st == 3) {
        const Call k = {.x = {request, 1, 0}};
        const uint64_t a = game_call(c, TAKE_FN, &k, NULL);
        recomp_store64(c, b + B_REQUEST, 0);
        if (!rd_ok(a, A_SIZE)) return TAKE_FAILED;
        p->actor = a, p->actor_id = (uint32_t)recomp_load32(c, a + A_ID);
        return TAKE_MADE;
    }
    if (st == 4) {   /* the game gave the request up */
        const Call k = {.x = {request}};
        game_call(c, DROP_FN, &k, NULL);
        recomp_store64(c, b + B_REQUEST, 0);
        return TAKE_FAILED;
    }
    if (st == 5 || st == 6) {
        request_cancel(c, p);
        return TAKE_FAILED;
    }
    return TAKE_WAIT;
}

static void actor_place(Ctx *c, const Puppet *p, const float pos[3], float yaw) {
    float rot[9], m[12];
    yaw_rot(yaw, rot);
    for (int r = 0; r < 3; r++) memcpy(m + 4 * r, rot + 3 * r, 12), m[4 * r + 3] = pos[r];
    put_floats(c, P.scratch + SCRATCH_MTX, m, 12);
    const Call k = {.x = {p->actor, P.scratch + SCRATCH_MTX, 0}};
    game_call(c, SET_MTX_FN, &k, NULL);
    put_floats(c, p->actor + A_POS, pos, 3), put_floats(c, p->actor + A_ROT, rot, 9);
    put_floats(c, p->actor + A_POS2, pos, 3), put_floats(c, p->actor + A_ROT2, rot, 9);
}

/* ---- animation: an actor's animation control and its slots, each playing a node of the actor's animation set by name */

static uint64_t anim_control(Ctx *c, uint64_t actor) {
    const uint64_t control = rd64(c, rd64(c, rd64(c, rd64(c, actor + A_PARTS) + 0x40) + 0x18) + 0x18);
    return rd_ok(control, 0x28) ? control : 0;
}

static int anim_slots(Ctx *c, uint64_t control) {
    const uint32_t n = control ? (uint32_t)recomp_load32(c, control + 0x18) : 0;
    return n <= 64 ? (int)n : 0;
}

static uint64_t anim_slot(Ctx *c, uint64_t control, int i) { return rd64(c, recomp_load64(c, control + 0x20) + 0x48ull * (unsigned)i); }

/* What Link plays, into the first slots of me: each slot's node and its frame. */
static void anim_read(Ctx *c, uint64_t actor, CoopPos *me) {
    const uint64_t control = anim_control(c, actor);
    const int n = anim_slots(c, control);
    me->nslots = 0;
    for (int i = 0; i < n && i < COOP_POS_SLOTS; i++) {
        const uint64_t slot = anim_slot(c, control, i);
        float frame = 0;
        text_at(rd64(c, slot + AS_SLOT_NAME), me->slot[i].node, sizeof me->slot[i].node);
        if (me->slot[i].node[0]) {
            const Call k = {.x = {control, (uint64_t)i}};
            game_call(c, AS_FRAME_GET_FN, &k, &frame);
        }
        me->slot[i].frame = isfinite(frame) ? frame : 0;
        me->nslots = (uint8_t)(i + 1);
    }
}

/* The member's animation on its actor: a slot is asked for a node when the member's changed (the game's own request:
   rate 1, no start frame, the node's own blend, nothing when the slot plays it already), and where the actor's
   animation set has the node, its frame is set every frame. */
static void anim_play(Ctx *c, Puppet *p, const CoopPos *at) {
    const uint64_t control = anim_control(c, p->actor);
    const int n = anim_slots(c, control);
    for (int i = 0; i < n && i < at->nslots && i < COOP_POS_SLOTS; i++) {
        if (!at->slot[i].node[0]) continue;
        if (strcmp(at->slot[i].node, p->node[i])) {
            const uint64_t req = P.scratch + SCRATCH_PLAY, text = P.scratch + SCRATCH_NODE;
            const float rate = 1, none = -1;
            text_copy(p->node[i], sizeof p->node[i], at->slot[i].node);
            memset(GP(req), 0, 0x30);
            memcpy(GP(text), p->node[i], strlen(p->node[i]) + 1);
            recomp_store64(c, req + 0x00, p->actor), recomp_store64(c, req + 0x08, text), recomp_store32(c, req + 0x10, (uint32_t)i);
            put_floats(c, req + 0x14, &rate, 1), put_floats(c, req + 0x18, &none, 1), put_floats(c, req + 0x1c, &none, 1);
            recomp_store8(c, req + 0x20, 1);
            const Call k = {.x = {req}};
            p->plays[i] = (uint8_t)(game_call(c, AS_PLAY_FN, &k, NULL) & 1);
            if (P.trace) hos_log("coop: puppet: %s slot %d: asked to play '%s': %s", p->name, i, p->node[i], p->plays[i] ? "played" : "its animation set has no such node");
        }
        if (p->plays[i]) {
            const Call k = {.x = {control, (uint64_t)i}, .s0 = at->slot[i].frame};
            game_call(c, AS_FRAME_SET_FN, &k, NULL);
        }
    }
}

/* ---- a member's actor */

static void say_counts(const char *when) {
    hos_log("coop: players: %s: made %u, deleted %u (%u by this game, %u gone with their map), %u still shown, %u requests failed", when, P.made,
            P.deleted + P.taken_away, P.deleted, P.taken_away, P.made - P.deleted - P.taken_away, P.failed);
}

/* Removes what was made or asked for member id: nothing of it is left in the game. */
static void puppet_remove(Ctx *c, int id, const char *why, uint64_t now) {
    Puppet *p = &P.puppet[id];
    if (p->state == PP_NONE) return;
    if (p->state == PP_ASKED) {
        request_cancel(c, p), P.failed++;
    } else if (actor_alive(c, p)) {
        const Call k = {.x = {p->actor, 0}};
        game_call(c, ACTOR_DELETE_FN, &k, NULL);
        P.deleted++;
    } else {
        P.taken_away++;   /* the game removed it (its map went) */
    }
    hos_log("coop: puppet: member %d is hidden (%s)", id, why);
    p->state = PP_NONE, p->actor = 0, p->again_ms = now + AGAIN_MS;
}

static void remove_all(Ctx *c, const char *why, uint64_t now) {
    int any = 0;
    for (int id = 1; id <= COOP_MAX_MEMBERS; id++) any |= P.puppet[id].state != PP_NONE, puppet_remove(c, id, why, now);
    if (any) say_counts(why);
}

/* Member id this frame: reason (coop_players_show) and, when it is heard, its place. */
static void puppet_step(Ctx *c, int id, int reason, const CoopPos *at, uint64_t now) {
    Puppet *p = &P.puppet[id];
    const int heard = reason == COOP_PLAYER_SHOWN || reason == COOP_PLAYER_OTHER_MAP;
    if (!heard) p->map[0] = 0;
    else if (strcmp(p->map, at->area)) {   /* the first sight, and each change of map */
        text_copy(p->map, sizeof p->map, at->area);
        hos_log("coop: puppet: member %d is in %s at %.2f %.2f %.2f, %s Link%s", id, at->area, at->pos[0], at->pos[1], at->pos[2],
                at->flags & COOP_POS_ADULT ? "adult" : "child", reason == COOP_PLAYER_SHOWN ? "" : " (another map than this game's: not shown)");
    }
    const char *name = P.actor_name[reason == COOP_PLAYER_SHOWN && (at->flags & COOP_POS_ADULT) ? 1 : 0];
    if (p->state != PP_NONE && (reason != COOP_PLAYER_SHOWN || strcmp(p->name, name)))
        puppet_remove(c, id, reason != COOP_PLAYER_SHOWN ? coop_players_why(reason) : "another actor is shown for it", now);
    if (reason != COOP_PLAYER_SHOWN) return;
    if (p->state == PP_NONE) {
        if (now < p->again_ms) return;
        if (!actor_ask(c, p, name, at->pos, at->yaw)) {
            P.failed++, p->again_ms = now + AGAIN_MS;
            return;
        }
        p->state = PP_ASKED, p->asked_ms = now;
    }
    if (p->state == PP_ASKED) {
        const int r = actor_take(c, p);
        if (r == TAKE_WAIT && now - p->asked_ms < ASK_MS) return;
        if (r != TAKE_MADE) {
            if (r == TAKE_WAIT) request_cancel(c, p);
            hos_log("coop: puppet: %s for member %d was not made (%s)", p->name, id, r == TAKE_WAIT ? "not after 20 s" : "the game gave it up");
            P.failed++, p->state = PP_NONE, p->again_ms = now + AGAIN_MS;
            return;
        }
        P.made++, p->state = PP_SHOWN, p->said_ms = now;
        memset(p->node, 0, sizeof p->node), memset(p->plays, 0, sizeof p->plays);
        hos_log("coop: puppet: member %d is shown as %s at %.2f %.2f %.2f (made in %u ms)", id, p->name, at->pos[0], at->pos[1], at->pos[2],
                (unsigned)(now - p->asked_ms));
    }
    if (!actor_alive(c, p)) {   /* the game removed it: no longer this file's to delete */
        P.taken_away++, p->state = PP_NONE, p->actor = 0, p->again_ms = now + AGAIN_MS;
        hos_log("coop: puppet: member %d's actor is gone (the game removed it)", id);
        return;
    }
    float was[3];   /* (where the game has the actor now, a frame after it was last placed) */
    floats_at(p->actor + A_POS, was, 3);
    actor_place(c, p, at->pos, at->yaw);
    if (P.anim) anim_play(c, p, at);
    if (P.trace && now - p->said_ms >= 2000) {
        p->said_ms = now;
        hos_log("coop: puppet: member %d's %s at %.2f %.2f %.2f, facing %.0f degrees (the actor: state %u, was at %.2f %.2f %.2f, %d animation slots)",
                id, p->name, at->pos[0], at->pos[1], at->pos[2], at->yaw * 57.29578f, (unsigned)recomp_load32(c, p->actor + A_STATE), was[0], was[1],
                was[2], anim_slots(c, anim_control(c, p->actor)));
    }
}
typedef struct { int member; uint64_t now; const CoopPos *pose; } BodyWrite;

static int body_write(void *game, const void *payload) {
    const BodyWrite *body = payload;
    if (body->member < 1 || body->member > COOP_MAX_MEMBERS || !body->pose || (body->pose->flags & COOP_POS_ADULT)) return 0;
    if (!P.blocks) {
        P.blocks = hos_sys_alloc((uint64_t)B_SIZE * (COOP_MAX_MEMBERS + 1) + SCRATCH_SIZE, 16);
        if (!P.blocks) return 0;
        P.scratch = P.blocks + (uint64_t)B_SIZE * (COOP_MAX_MEMBERS + 1);
        for (int id = 0; id <= COOP_MAX_MEMBERS; id++) P.puppet[id].block = P.blocks + (uint64_t)B_SIZE * (unsigned)id;
    }
    puppet_step(game, body->member, COOP_PLAYER_SHOWN, body->pose, body->now);
    return 1;
}


/* ---- the test feed (HOS_COOP_PLAYERS_TEST): a made-up member whose positions come from a file */

static void test_read(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) {
        hos_log("coop: players: HOS_COOP_PLAYERS_TEST: %s cannot be read", path);
        return;
    }
    char line[256];
    while (fgets(line, sizeof line, f) && P.nsteps < TEST_STEPS) {
        TestStep s = {0};
        char what[16] = "";
        int n = 0;
        if (sscanf(line, "%f %15s %n", &s.t, what, &n) < 2 || what[0] == '#') continue;
        const char *rest = line + n;
        if (!strcmp(what, "actor") && sscanf(rest, "%63s", s.text) == 1) s.kind = T_ACTOR;
        else if (!strcmp(what, "at") && sscanf(rest, "%47s %f %f %f %f", s.text, &s.v[0], &s.v[1], &s.v[2], &s.v[3]) == 5) s.kind = T_AT;
        else if (!strcmp(what, "abs") && sscanf(rest, "%47s %f %f %f %f", s.text, &s.v[0], &s.v[1], &s.v[2], &s.v[3]) == 5) s.kind = T_ABS;
        else if (!strcmp(what, "shadow") && sscanf(rest, "%f", &s.v[0]) == 1) s.kind = T_SHADOW;
        else if (!strcmp(what, "gone")) s.kind = T_GONE;
        else continue;
        P.step[P.nsteps++] = s;
    }
    fclose(f);
    P.test = P.nsteps > 0, P.mode = T_GONE;
    hos_log("coop: players: HOS_COOP_PLAYERS_TEST: %d steps from %s; member %d is made up", P.nsteps, path, TEST_MEMBER);
}

/* The made-up member's position now, sent through the message path (packed, parsed, checked, kept). me: Link's own. */
static void test_feed(const CoopPos *me, uint64_t now) {
    if (!P.began_ms) {
        P.began_ms = now, P.base_yaw = me->yaw;
        memcpy(P.base_pos, me->pos, sizeof P.base_pos);
    }
    const float t = (float)(now - P.began_ms) / 1000.0f;
    if (P.trace && now - P.own_said_ms >= 500) {
        P.own_said_ms = now;
        hos_log("coop: players: test: %.1f s: this game's Link at %.2f %.2f %.2f, facing %.0f degrees", t, me->pos[0], me->pos[1], me->pos[2],
                me->yaw * 57.29578f);
    }
    P.ring[P.ring_at % RING].ms = now, P.ring[P.ring_at % RING].yaw = me->yaw;
    memcpy(P.ring[P.ring_at++ % RING].pos, me->pos, sizeof me->pos);
    for (; P.at < P.nsteps && P.step[P.at].t <= t; P.at++) {
        const TestStep *s = &P.step[P.at];
        if (s->kind == T_ACTOR) {
            text_copy(P.actor_name[0], sizeof P.actor_name[0], s->text);
            text_copy(P.actor_name[1], sizeof P.actor_name[1], s->text);
            hos_log("coop: players: test: the actor shown is %s from %.1f s", s->text, t);
        } else {
            P.mode = s->kind, P.seg = P.at, P.side = s->v[0];
        }
    }
    if (P.mode == T_GONE || now - P.fed_ms < COOP_PLAYERS_SEND_MS) return;
    P.fed_ms = now;
    CoopPos pos = *me;
    if (P.mode != T_SHADOW) pos.nslots = 0;   /* (the shadow plays what Link plays now, where he stood a second ago) */
    if (P.mode == T_SHADOW) {
        for (unsigned k = 1; k <= RING && k <= P.ring_at; k++) {
            const unsigned i = (P.ring_at - k) % RING;
            pos.yaw = P.ring[i].yaw, memcpy(pos.pos, P.ring[i].pos, sizeof pos.pos);
            if (now - P.ring[i].ms >= 1000) break;
        }
        pos.pos[0] += -cosf(pos.yaw) * P.side, pos.pos[2] += sinf(pos.yaw) * P.side;   /* his right: (-cos, 0, sin) */
    } else {
        const TestStep *a = &P.step[P.seg], *b = P.seg + 1 < P.nsteps ? a + 1 : NULL;
        float v[4];
        memcpy(v, a->v, sizeof v);
        if (b && b->kind == a->kind && !strcmp(a->text, b->text) && b->t > a->t) {
            const float f = fminf(1.0f, fmaxf(0.0f, (t - a->t) / (b->t - a->t)));
            for (int i = 0; i < 4; i++) v[i] += (b->v[i] - a->v[i]) * f;
        }
        if (a->kind == T_ABS) {
            memcpy(pos.pos, v, sizeof pos.pos);
            pos.yaw = v[3] / 57.29578f;
        } else {
            const float s = sinf(P.base_yaw), co = cosf(P.base_yaw);
            pos.pos[0] = P.base_pos[0] - co * v[0] + s * v[2];
            pos.pos[1] = P.base_pos[1] + v[1];
            pos.pos[2] = P.base_pos[2] + s * v[0] + co * v[2];
            pos.yaw = P.base_yaw + v[3] / 57.29578f;
        }
        if (strcmp(a->text, "=")) text_copy(pos.area, sizeof pos.area, a->text);
    }
    pos.pseq = ++P.test_pseq, pos.t_ms = (uint32_t)now;
    uint8_t buf[COOP_MAX_MESSAGE];
    const size_t n = coop_pos_pack(buf, sizeof buf, &pos);
    CoopGameplayTicket ticket;
    if (!hos_oot_coop_gameplay_ticket(COOP_GAMEPLAY_BODIES, TEST_MEMBER, &ticket)) return;
    AcquireSRWLockExclusive(&P.lock);
    if (n && coop_players_receive(&P.store, TEST_MEMBER, buf, n, now)) P.ticket[TEST_MEMBER] = ticket;
    ReleaseSRWLockExclusive(&P.lock);
}

/* ---- the session's side (the co-op thread) */

/* With HOS_COOP_TRACE, a line at each change of what a member says of itself: its map and state, as the Co-op tab reads
   them (hos_oot_coop_players_last). */
static void say_heard(int from, const CoopPos *p) {
    const char *state = !(p->flags & COOP_POS_LOADED) ? "the title screen" : p->flags & COOP_POS_IN_WORLD ? "in the world" : "a save is played, not in the world";
    hos_log("coop: players: member %d says: %s, %s, %s Link%s", from, p->area[0] ? p->area : "no map", state, p->flags & COOP_POS_ADULT ? "adult" : "child",
            p->flags & COOP_POS_SLOW ? ", its game shows nobody" : "");
}

static void on_pos(int from, const uint8_t *bytes, size_t n, void *user) {
    (void)user;
    CoopPos last;
    int say = 0;
    CoopGameplayTicket ticket;
    if (!hos_oot_coop_gameplay_ticket(COOP_GAMEPLAY_BODIES, (unsigned)from, &ticket)) return;
    AcquireSRWLockExclusive(&P.lock);
    const int taken = coop_players_receive(&P.store, from, bytes, n, coop_now_ms());
    if (taken) P.ticket[from] = ticket;
    if (taken && P.trace && coop_players_last(&P.store, from, 0, &last, NULL)) {
        say = !P.heard[from].any || last.flags != P.heard[from].flags || strcmp(last.area, P.heard[from].area);
        P.heard[from].any = 1, P.heard[from].flags = last.flags;
        memcpy(P.heard[from].area, last.area, sizeof last.area);
    }
    ReleaseSRWLockExclusive(&P.lock);
    if (say) say_heard(from, &last);
}

static void on_view(const CoopView *v, void *user) {
    (void)user;
    int ids[COOP_MAX_MEMBERS], n = 0;
    uint64_t sessions[COOP_MAX_MEMBERS];
    const int in_room = v->state == COOP_JOINED || v->state == COOP_RECONNECTING;
    for (int i = 0; in_room && i < v->n && i < COOP_MAX_MEMBERS; i++) ids[n] = v->member[i].id, sessions[n++] = v->member[i].session;
    AcquireSRWLockExclusive(&P.lock);
    if (!P.view_any || (int)(v->version - P.view_version) >= 0) {   /* (not an older view than the last one taken) */
        P.view_any = 1, P.view_version = v->version;
        coop_players_roster(&P.store, v->self, ids, sessions, n);
    }
    ReleaseSRWLockExclusive(&P.lock);
}

/* ---- Link's update */

/* The place this game sends: with the switch off (it shows nobody) no place and no animation, only the map and flags. */
static void place_hidden(CoopPos *me) {
    if (P.show) return;
    memset(me->pos, 0, sizeof me->pos), me->yaw = 0, me->nslots = 0;
    memset(me->slot, 0, sizeof me->slot);
}

static void update(Ctx *c, uint64_t actor) {
    const uint64_t now = coop_now_ms();
    if (!hos_oot_coop_playing() || !hos_oot_coop_presence()) {   /* context revoked: retire only this adapter's own actors */
        if (P.was_playing) remove_all(c, "this game stopped being played: a map change or a load", now);
        __atomic_store_n(&P.was_playing, 0, __ATOMIC_RELAXED);
        return;
    }
    __atomic_store_n(&P.was_playing, 1, __ATOMIC_RELAXED);
    if (event_playing(c) || !rd_ok(actor, A_SIZE)) return;

    CoopPos me = {.flags = COOP_POS_IN_WORLD | COOP_POS_LOADED | (P.show ? 0 : COOP_POS_SLOW)};
    float rot[9];
    char file[NAME_MAX_LEN + 1];
    floats_at(actor + A_POS, me.pos, 3), floats_at(actor + A_ROT, rot, 9);
    me.yaw = atan2f(rot[2], rot[8]);
    if (!isfinite(me.pos[0]) || !isfinite(me.pos[1]) || !isfinite(me.pos[2]) || !isfinite(me.yaw)) return;
    if (strstr(text_at(recomp_load64(c, actor + A_FILE), file, sizeof file), "Adult")) me.flags |= COOP_POS_ADULT;
    if (me.flags & COOP_POS_ADULT) {
        remove_all(c, "child-body adapter does not support the local adult age", now);
        return;
    }
    if (!map_now(c, me.area, sizeof me.area)[0]) return;
    if (strcmp(me.area, P.own_map)) {
        text_copy(P.own_map, sizeof P.own_map, me.area);
        if (P.trace) hos_log("coop: players: this game's Link (%s) is in %s at %.2f %.2f %.2f", file, me.area, me.pos[0], me.pos[1], me.pos[2]);
    }
    if (P.anim) anim_read(c, actor, &me);
    if (P.test) test_feed(&me, now);
    place_hidden(&me);

    uint8_t buf[COOP_MAX_MESSAGE];
    int reason[COOP_MAX_MEMBERS + 1], in_room;
    static CoopPos at[COOP_MAX_MEMBERS + 1];   /* (Link's update holds game_lock) */
    CoopGameplayTicket tickets[COOP_MAX_MEMBERS + 1];
    const uint32_t accepted = P.show ? hos_oot_coop_shown() | P.forced : 0;
    if (!TryAcquireSRWLockExclusive(&P.lock)) return;   /* the co-op thread is taking a position: the next frame */
    P.last_me = me, P.me_ms = now;
    const size_t n = coop_players_send(&P.store, buf, sizeof buf, &me, now);
    for (int id = 1; P.show && id <= COOP_MAX_MEMBERS; id++) reason[id] = coop_players_show(&P.store, id, accepted, me.area, now, &at[id]);
    in_room = P.store.others > 0;
    memcpy(tickets, P.ticket, sizeof tickets);
    ReleaseSRWLockExclusive(&P.lock);
    if (n && hos_oot_coop_presence()) coop_send(COOP_K_POS, buf, n);
    if (!P.show) return;   /* (nobody is shown: nothing is made in the game) */

    int shown = 0;
    for (int id = 1; id <= COOP_MAX_MEMBERS; id++) {
        const BodyWrite body = {id, now, &at[id]};
        if (reason[id] != COOP_PLAYER_SHOWN) {
            puppet_step(c, id, reason[id], &at[id], now);   /* retirement only; cannot create or move */
        } else {
            const int result = hos_oot_coop_gameplay_dispatch(&tickets[id], &body, sizeof body, c);
            if (!result) puppet_remove(c, id, "presentation permission or child pose expired", now);
        }
        shown += P.puppet[id].state != PP_NONE;
    }
    if ((P.was_in_room && !in_room) || (P.shown_now && !shown)) say_counts(in_room ? "nobody is shown now" : "left the room");
    P.was_in_room = in_room, P.shown_now = shown;
}

void hos_oot_coop_players_update(Ctx *c, uint64_t actor) {
    if (!__atomic_load_n(&P.on, __ATOMIC_ACQUIRE)) return;
    /* co-op off, the title screen, a load: nothing to send from here and nothing made to remove */
    if ((!hos_oot_coop_playing() || !hos_oot_coop_presence()) && !__atomic_load_n(&P.was_playing, __ATOMIC_RELAXED)) return;
    if (!TryAcquireSRWLockExclusive(&P.game_lock)) return;
    update(c, actor);
    ReleaseSRWLockExclusive(&P.game_lock);
}

void hos_oot_coop_players_tick(int loaded, int open) {
    if (!__atomic_load_n(&P.on, __ATOMIC_ACQUIRE) || !hos_oot_coop_presence()) return;
    const uint64_t now = coop_now_ms();
    uint8_t buf[COOP_MAX_MESSAGE];
    size_t n = 0;
    if (!TryAcquireSRWLockExclusive(&P.lock)) return;   /* (the next present) */
    /* in play Link's update sends; here only when it has not read Link's place for IDLE_MS (an event holds him) */
    if (P.store.others && (!open || now - P.me_ms >= IDLE_MS)) {
        CoopPos me = {.flags = P.show ? 0 : COOP_POS_SLOW};   /* no place: Link stands in no map now */
        if (loaded) {   /* a save is played: the last map; else the title screen, no map */
            me.flags |= COOP_POS_LOADED | (P.last_me.flags & COOP_POS_ADULT);
            memcpy(me.area, P.last_me.area, sizeof me.area);
        }
        n = coop_players_send(&P.store, buf, sizeof buf, &me, now);
    }
    ReleaseSRWLockExclusive(&P.lock);
    if (n) coop_send(COOP_K_POS, buf, n);
}

int hos_oot_coop_players_last(int id, char area[COOP_POS_TEXT + 1], uint8_t *flags, uint64_t *age_ms) {
    if (!__atomic_load_n(&P.on, __ATOMIC_ACQUIRE)) return 0;
    CoopPos at;
    AcquireSRWLockExclusive(&P.lock);
    const int has = coop_players_last(&P.store, id, coop_now_ms(), &at, age_ms);
    ReleaseSRWLockExclusive(&P.lock);
    if (has && area) memcpy(area, at.area, COOP_POS_TEXT + 1);
    if (has && flags) *flags = at.flags;
    return has;
}

void hos_oot_coop_players_start(void) {
    if (__atomic_load_n(&P.on, __ATOMIC_ACQUIRE)) return;
    const char *e = getenv("HOS_COOP_PLAYERS"), *names = getenv("HOS_COOP_PLAYERS_ACTOR"), *test = getenv("HOS_COOP_PLAYERS_TEST");
    const int show = e ? !env_value_off(e) : hos_ini_option("coop_players", 0) != 0;   /* (as host/oot_coop.c reads HOS_COOP) */
    int known = !strcmp(hos_main_build_id, PLAYERS_BUILD);
    for (size_t i = 0; known && i < sizeof game_fns / sizeof game_fns[0]; i++) known = a64_lookup(game_fns[i]) != NULL;
    if (!known) {
        if (show) hos_log("coop: players: the other players are not shown: this is not the build of the game (%.8s) their actors are known for", PLAYERS_BUILD);
        return;
    }
    P.trace = env_on("HOS_COOP_TRACE");
    const CoopGameplayWriter writer = {COOP_GAMEPLAY_VERSION, sizeof writer, COOP_GAMEPLAY_BODIES,
                                      COOP_GAMEPLAY_PRESENTATION, sizeof(BodyWrite), body_write};
    if (!hos_oot_coop_gameplay_register(&writer)) {
        hos_log("coop: players: another body writer already owns presentation; this adapter stays off");
        return;
    }
    if (show) {
        P.show = 1;
        P.anim = env_on("HOS_COOP_PLAYERS_ANIM") && hos_coop_players_assets();
        if (env_on("HOS_COOP_PLAYERS_ANIM") && !P.anim && !(names && *names))
            hos_log("coop: players: animation unavailable: rebuild this game to make its local actor assets (or remove the conflicting mod)");
        text_copy(P.actor_name[0], sizeof P.actor_name[0], P.anim ? "CoopPuppet_Child" : "PouchPlayer_Child");
        text_copy(P.actor_name[1], sizeof P.actor_name[1], P.anim ? "CoopPuppet_Adult" : "PouchPlayer_Adult");
        if (names && *names) {
            char a[NAME_MAX_LEN + 1] = "", b[NAME_MAX_LEN + 1] = "";
            sscanf(names, "%63[^,],%63s", a, b);
            if (a[0]) text_copy(P.actor_name[0], sizeof P.actor_name[0], a), text_copy(P.actor_name[1], sizeof P.actor_name[1], b[0] ? b : a);
            P.anim = env_on("HOS_COOP_PLAYERS_ANIM");   /* explicit test actors may bring their own animation set */
        }
        if (test && *test) test_read(test);
    }
    if (P.test) {   /* a room of this player (1) and the made-up member, whatever the session says */
        const int ids[2] = {1, TEST_MEMBER};
        const uint64_t sessions[2] = {1, 2};
        coop_players_roster(&P.store, 1, ids, sessions, 2);
        P.forced = 1u << TEST_MEMBER;
    } else {
        coop_on_message(COOP_K_POS, on_pos, NULL);
        coop_on_view(on_view, NULL);
        CoopView v;   /* a room joined before the hooks were there */
        coop_view_get(&v);
        on_view(&v, NULL);
    }
    if (show)
        hos_log("coop: players: the other players are shown in the world as %s (child) and %s (adult)", P.actor_name[0], P.actor_name[1]);
    else
        hos_log("coop: players: the other players are not shown (coop_players is off): a room hears only this game's map and whether a save "
                "is played, every 2 s and at each change");
    __atomic_store_n(&P.on, 1, __ATOMIC_RELEASE);
}
