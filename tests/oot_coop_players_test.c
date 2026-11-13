/* Co-op's other players without the game. Part 1: host/coop_players.c as it is (which position messages are taken, who
   is shown where). Part 2: host/oot_coop_players.c from its first constant to its end, included by
   tests/test_coop_players.py (OOT_COOP_PLAYERS_BODY), run on a fake guest memory with a made-up Link, map flag, event
   manager and actor system: stand-ins for the game's create, take, cancel, delete and transform routines record what
   they are given. */
#ifndef _CRT_SECURE_NO_WARNINGS
#define _CRT_SECURE_NO_WARNINGS
#endif
#include <float.h>
#include <math.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
static int setenv(const char *name, const char *value, int overwrite) {
    return !overwrite && getenv(name) ? 0 : _putenv_s(name, value);
}
static int unsetenv(const char *name) { return _putenv_s(name, ""); }
#endif

#include "coop.h"
#include "coop_players.h"
#include "coop_gameplay.h"
#include "coop_state.h"
#include "../gpu/vk/vk_api.h"
#include "../gpu/vk/compat/longshot_env.h"   /* the runtime's shared environment parser */

static int failures;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); failures++; } } while (0)

/* ---- part 1: the position messages */

static size_t pos_bytes(uint8_t *out, uint32_t pseq, uint32_t t_ms, const char *map, float x, float y, float z, float yaw, uint8_t flags) {
    CoopPos p = {.pseq = pseq, .t_ms = t_ms, .flags = flags, .pos = {x, y, z}, .yaw = yaw};
    snprintf(p.area, sizeof p.area, "%s", map);
    return coop_pos_pack(out, COOP_MAX_MESSAGE, &p);
}

static void messages(void) {
    static CoopPlayers s;
    uint8_t b[COOP_MAX_MESSAGE];
    CoopPos at, me = {.flags = COOP_POS_IN_WORLD, .area = "Forest", .pos = {1, 2, 3}, .yaw = 0.5f};
    const uint32_t both = 1u << 2 | 1u << 3;
    uint64_t now = 500000;
    size_t n = pos_bytes(b, 1, 100, "Forest", 10, 0, 20, 1, COOP_POS_IN_WORLD);

    /* outside a room: nothing is sent, nothing is taken, nobody is shown */
    CHECK(!coop_players_send(&s, b, sizeof b, &me, now));
    CHECK(!coop_players_receive(&s, 2, b, n, now));
    CHECK(coop_players_show(&s, 2, both, "Forest", now, &at) == COOP_PLAYER_ABSENT);

    /* alone in a room: nothing is sent */
    const int alone[1] = {1};
    const uint64_t alone_s[1] = {11};
    coop_players_roster(&s, 1, alone, alone_s, 1);
    CHECK(!coop_players_send(&s, b, sizeof b, &me, now));

    /* a room of three: this player is 1 */
    const int ids[3] = {1, 2, 3};
    const uint64_t sessions[3] = {11, 22, 33};
    coop_players_roster(&s, 1, ids, sessions, 3);
    CHECK(s.others == 2 && s.self == 1);

    /* sending: at most one in COOP_PLAYERS_SEND_MS, the counter one higher each time, the place as given */
    uint8_t out[COOP_MAX_MESSAGE];
    CoopPos back;
    size_t k = coop_players_send(&s, out, sizeof out, &me, now);
    CHECK(k && coop_pos_parse(out, k, &back) && back.pseq == 1 && back.t_ms == (uint32_t)now && !strcmp(back.area, "Forest") &&
          back.pos[0] == 1 && back.pos[2] == 3 && back.yaw == 0.5f && back.flags == COOP_POS_IN_WORLD);
    CHECK(!coop_players_send(&s, out, sizeof out, &me, now + COOP_PLAYERS_SEND_MS - 1));
    k = coop_players_send(&s, out, sizeof out, &me, now + COOP_PLAYERS_SEND_MS);
    CHECK(k && coop_pos_parse(out, k, &back) && back.pseq == 2);
    CHECK(!coop_players_send(&s, out, 4, &me, now + 1000) && s.pseq == 2);   /* (no room for it: the counter stays) */

    /* taking: not from this player's own id, not from an id the roster does not list, not what does not parse */
    n = pos_bytes(b, 1, 100, "Forest", 10, 0, 20, 1, COOP_POS_IN_WORLD);
    CHECK(!coop_players_receive(&s, 1, b, n, now));
    CHECK(!coop_players_receive(&s, 4, b, n, now));
    CHECK(!coop_players_receive(&s, 0, b, n, now) && !coop_players_receive(&s, 9, b, n, now) && !coop_players_receive(&s, -1, b, n, now));
    CHECK(!coop_players_receive(&s, 2, b, n - 1, now));
    CHECK(!coop_players_receive(&s, 2, b, 0, now));
    uint8_t bad[COOP_MAX_MESSAGE];
    memcpy(bad, b, n), bad[0] ^= 1;                                   /* another version */
    CHECK(!coop_players_receive(&s, 2, bad, n, now));
    size_t m = pos_bytes(bad, 1, 100, "Forest", NAN, 0, 20, 1, COOP_POS_IN_WORLD);
    CHECK(!m || !coop_players_receive(&s, 2, bad, m, now));           /* a number that is not finite */
    m = pos_bytes(bad, 1, 100, "Forest", 10, INFINITY, 20, 1, COOP_POS_IN_WORLD);
    CHECK(!m || !coop_players_receive(&s, 2, bad, m, now));
    m = pos_bytes(bad, 1, 100, "Forest", 10, 0, 20, NAN, COOP_POS_IN_WORLD);
    CHECK(!m || !coop_players_receive(&s, 2, bad, m, now));
    m = pos_bytes(bad, 1, 100, "Forest", 10, 0, COOP_PLAYERS_FAR * 2, 1, COOP_POS_IN_WORLD);
    CHECK(m && !coop_players_receive(&s, 2, bad, m, now));            /* a place no map has */
    m = pos_bytes(bad, 1, 100, "For\x01st", 10, 0, 20, 1, COOP_POS_IN_WORLD);
    CHECK(!m || !coop_players_receive(&s, 2, bad, m, now));           /* a name that is not printable */
    CHECK(coop_players_show(&s, 2, both, "Forest", now, &at) == COOP_PLAYER_NOT_HEARD && !s.taken && s.refused >= 9);

    /* a good one is taken; the same again and an older one are not */
    CHECK(coop_players_receive(&s, 2, b, n, now) && s.taken == 1);
    CHECK(!coop_players_receive(&s, 2, b, n, now + 1));
    n = pos_bytes(b, 5, 500, "Forest", 14, 0, 20, 1, COOP_POS_IN_WORLD);
    CHECK(coop_players_receive(&s, 2, b, n, now + 400));
    n = pos_bytes(b, 4, 400, "Forest", 99, 0, 99, 1, COOP_POS_IN_WORLD);
    CHECK(!coop_players_receive(&s, 2, b, n, now + 401) && s.taken == 2);

    /* shown: only a member the player accepted, only in this game's map, only while its Link is in a map */
    CHECK(coop_players_show(&s, 2, 0, "Forest", now + 600, &at) == COOP_PLAYER_NOT_ACCEPTED);
    CHECK(coop_players_show(&s, 2, 1u << 3, "Forest", now + 600, &at) == COOP_PLAYER_NOT_ACCEPTED);
    CHECK(coop_players_show(&s, 2, both, "Forest", now + 600, &at) == COOP_PLAYER_SHOWN && at.pos[0] == 14 && at.pos[2] == 20 && at.yaw == 1);
    CHECK(coop_players_show(&s, 2, both, "Field", now + 600, &at) == COOP_PLAYER_OTHER_MAP && !strcmp(at.area, "Forest"));
    CHECK(coop_players_show(&s, 2, both, "", now + 600, NULL) == COOP_PLAYER_OTHER_MAP);
    CHECK(coop_players_show(&s, 3, both, "Forest", now + 600, &at) == COOP_PLAYER_NOT_HEARD);
    CHECK(coop_players_show(&s, 1, both | 2, "Forest", now + 600, &at) == COOP_PLAYER_ABSENT);   /* this player itself */
    CHECK(coop_players_show(&s, 0, ~0u, "Forest", now + 600, &at) == COOP_PLAYER_ABSENT && coop_players_show(&s, 9, ~0u, "Forest", now, &at) == COOP_PLAYER_ABSENT);
    n = pos_bytes(b, 6, 700, "Forest", 14, 0, 20, 1, 0);              /* its Link in no map (not COOP_POS_IN_WORLD) */
    CHECK(coop_players_receive(&s, 2, b, n, now + 600));
    CHECK(coop_players_show(&s, 2, both, "Forest", now + 800, &at) == COOP_PLAYER_NOT_IN_MAP);
    n = pos_bytes(b, 7, 900, "Field", 1, 0, 2, 1, COOP_POS_IN_WORLD | COOP_POS_ADULT);   /* it changed map, as an adult */
    CHECK(coop_players_receive(&s, 2, b, n, now + 800));
    CHECK(coop_players_show(&s, 2, both, "Forest", now + 1000, &at) == COOP_PLAYER_OTHER_MAP && !strcmp(at.area, "Field"));
    CHECK(coop_players_show(&s, 2, both, "Field", now + 1000, &at) == COOP_PLAYER_SHOWN && (at.flags & COOP_POS_ADULT) && at.pos[0] == 1);

    /* its age: hidden once nothing was heard for COOP_PLAYERS_SILENT_MS, not a millisecond before */
    CHECK(coop_players_show(&s, 2, both, "Field", now + 800 + COOP_PLAYERS_SILENT_MS - 1, &at) == COOP_PLAYER_SHOWN);
    CHECK(coop_players_show(&s, 2, both, "Field", now + 800 + COOP_PLAYERS_SILENT_MS, &at) == COOP_PLAYER_NOT_HEARD);

    /* between two places: shown where it was COOP_PLAYERS_BEHIND_MS ago, the short way round for the facing */
    now += 10000;
    n = pos_bytes(b, 20, 5000, "Field", 0, 0, 0, 3.0f, COOP_POS_IN_WORLD);
    CHECK(coop_players_receive(&s, 3, b, n, now));
    n = pos_bytes(b, 21, 5200, "Field", 2, 0, 4, -3.0f, COOP_POS_IN_WORLD);
    CHECK(coop_players_receive(&s, 3, b, n, now + 200));
    /* (the sender's clock is taken 1 ms later with each message that brings no smaller delay: 5099 here, half way) */
    CHECK(coop_players_show(&s, 3, both, "Field", now + 200, &at) == COOP_PLAYER_SHOWN);
    CHECK(fabsf(at.pos[0] - 1) < 0.05f && fabsf(at.pos[2] - 2) < 0.05f);
    CHECK(fabsf(fabsf(at.yaw) - 3.1416f) < 0.15f);                      /* through pi, not through 0 */
    CHECK(coop_players_show(&s, 3, both, "Field", now + 350, &at) == COOP_PLAYER_SHOWN && at.pos[0] == 2 && at.pos[2] == 4);   /* the newest */
    n = pos_bytes(b, 22, 5400, "Forest", 50, 0, 50, 0, COOP_POS_IN_WORLD);   /* no place between two maps */
    CHECK(coop_players_receive(&s, 3, b, n, now + 400));
    /* (the sender's 5300, half way between the two: one of them as it is, never a place between them) */
    CHECK(coop_players_show(&s, 3, both, "Field", now + 400, &at) == COOP_PLAYER_OTHER_MAP && at.pos[0] == 50 && at.pos[2] == 50);
    CHECK(coop_players_show(&s, 3, both, "Field", now + 550, &at) == COOP_PLAYER_OTHER_MAP && at.pos[0] == 50);

    /* many: the newest is kept */
    for (uint32_t i = 0; i < 40; i++) {
        n = pos_bytes(b, 100 + i, 6000 + 66 * i, "Forest", (float)i, 0, 0, 0, COOP_POS_IN_WORLD);
        CHECK(coop_players_receive(&s, 3, b, n, now + 1000 + 66 * i));
    }
    CHECK(coop_players_show(&s, 3, both, "Forest", now + 1000 + 66 * 39 + 500, &at) == COOP_PLAYER_SHOWN && at.pos[0] == 39);

    /* a member that left: what was kept of it is dropped, and stays dropped when the id is given again */
    const int two[2] = {1, 2};
    coop_players_roster(&s, 1, two, sessions, 2);
    CHECK(coop_players_show(&s, 3, both, "Forest", now + 1000 + 66 * 39 + 500, &at) == COOP_PLAYER_ABSENT);
    coop_players_roster(&s, 1, ids, sessions, 3);
    CHECK(coop_players_show(&s, 3, both, "Forest", now + 1000 + 66 * 39 + 500, &at) == COOP_PLAYER_NOT_HEARD);
    /* another session under a member's id: the same */
    n = pos_bytes(b, 300, 9000, "Forest", 5, 0, 5, 0, COOP_POS_IN_WORLD);
    CHECK(coop_players_receive(&s, 2, b, n, now + 20000));
    const uint64_t other[3] = {11, 99, 33};
    coop_players_roster(&s, 1, ids, other, 3);
    CHECK(coop_players_show(&s, 2, both, "Forest", now + 20001, &at) == COOP_PLAYER_NOT_HEARD);
    n = pos_bytes(b, 1, 9100, "Forest", 6, 0, 6, 0, COOP_POS_IN_WORLD);   /* its counter starts again */
    CHECK(coop_players_receive(&s, 2, b, n, now + 20100) && coop_players_show(&s, 2, both, "Forest", now + 20101, &at) == COOP_PLAYER_SHOWN);

    /* out of the room: everything is dropped */
    coop_players_roster(&s, 0, NULL, NULL, 0);
    CHECK(coop_players_show(&s, 2, both, "Forest", now + 20101, &at) == COOP_PLAYER_ABSENT && !s.others && !s.taken);
    CHECK(!coop_players_send(&s, out, sizeof out, &me, now + 30000));
    CHECK(!strcmp(coop_players_why(COOP_PLAYER_OTHER_MAP), "in another map") && !strcmp(coop_players_why(COOP_PLAYER_NOT_HEARD), "not heard"));
    printf("coop players: the position messages: taken only from a listed member, well formed, in order; shown only accepted, heard, in this map\n");
}

/* the low rate (COOP_POS_SLOW, or Link in no map): one in COOP_PLAYERS_SLOW_MS and at once at a change of map or flags; a
   receiver shows no member whose game shows nobody, and keeps each member's last message for the Co-op tab */
static void slow(void) {
    static CoopPlayers s;
    uint8_t out[COOP_MAX_MESSAGE], b[COOP_MAX_MESSAGE];
    CoopPos back, at, me = {.flags = COOP_POS_IN_WORLD | COOP_POS_LOADED | COOP_POS_SLOW, .area = "Forest"};
    const int ids[3] = {1, 2, 3};
    const uint64_t sessions[3] = {11, 22, 33};
    const uint32_t two = 1u << 2 | 1u << 3;
    uint64_t now = 900000;
    coop_players_roster(&s, 1, ids, sessions, 3);

    /* sending: a game that shows nobody, in the world: the first at once, then one in 2 s, not one more */
    CHECK(coop_players_send(&s, out, sizeof out, &me, now));
    CHECK(!coop_players_send(&s, out, sizeof out, &me, now + COOP_PLAYERS_SEND_MS));
    CHECK(!coop_players_send(&s, out, sizeof out, &me, now + COOP_PLAYERS_SLOW_MS - 1));
    CHECK(coop_players_send(&s, out, sizeof out, &me, now + COOP_PLAYERS_SLOW_MS));
    now += COOP_PLAYERS_SLOW_MS;
    snprintf(me.area, sizeof me.area, "%s", "Field");                                       /* another map: at once, */
    CHECK(!coop_players_send(&s, out, sizeof out, &me, now + COOP_PLAYERS_SEND_MS - 1));    /* (not within 66 ms) */
    size_t k = coop_players_send(&s, out, sizeof out, &me, now + COOP_PLAYERS_SEND_MS);
    CHECK(k && coop_pos_parse(out, k, &back) && !strcmp(back.area, "Field") && back.pseq == 3 && back.flags == me.flags);
    now += COOP_PLAYERS_SEND_MS;
    me.flags = COOP_POS_LOADED | COOP_POS_SLOW;                                             /* a load (in no map): at once */
    CHECK(coop_players_send(&s, out, sizeof out, &me, now + COOP_PLAYERS_SEND_MS));
    now += COOP_PLAYERS_SEND_MS;
    me.flags = COOP_POS_LOADED;                       /* a game that shows the others, its Link in no map: at once, then slow */
    CHECK(coop_players_send(&s, out, sizeof out, &me, now + COOP_PLAYERS_SEND_MS));
    now += COOP_PLAYERS_SEND_MS;
    CHECK(!coop_players_send(&s, out, sizeof out, &me, now + COOP_PLAYERS_SLOW_MS - 1));
    CHECK(coop_players_send(&s, out, sizeof out, &me, now + COOP_PLAYERS_SLOW_MS));
    now += COOP_PLAYERS_SLOW_MS;
    me.flags = 0, me.area[0] = 0;                                                           /* the title screen: at once */
    CHECK(coop_players_send(&s, out, sizeof out, &me, now + COOP_PLAYERS_SEND_MS));
    now += COOP_PLAYERS_SEND_MS;
    snprintf(me.area, sizeof me.area, "%s", "Field");
    me.flags = COOP_POS_IN_WORLD | COOP_POS_LOADED;                                         /* in the world, shown: the full rate */
    CHECK(coop_players_send(&s, out, sizeof out, &me, now + COOP_PLAYERS_SEND_MS));
    CHECK(coop_players_send(&s, out, sizeof out, &me, now + 2 * COOP_PLAYERS_SEND_MS));
    now += 1000;

    /* the receiver: a member whose game shows nobody is not shown, in this game's map too; what it said is kept */
    CHECK(!coop_players_last(&s, 2, now, &at, NULL));                                       /* nothing heard yet */
    size_t n = pos_bytes(b, 1, 100, "Forest", 0, 0, 0, 0, COOP_POS_IN_WORLD | COOP_POS_LOADED | COOP_POS_SLOW);
    CHECK(coop_players_receive(&s, 2, b, n, now));
    CHECK(coop_players_show(&s, 2, two, "Forest", now + 10, &at) == COOP_PLAYER_SLOW && !strcmp(at.area, "Forest"));
    CHECK(!strcmp(coop_players_why(COOP_PLAYER_SLOW), "its game shows nobody"));
    uint64_t age = 0;
    CHECK(coop_players_last(&s, 2, now + 1500, &at, &age) == 1 && age == 1500 && !strcmp(at.area, "Forest") &&
          at.flags == (COOP_POS_IN_WORLD | COOP_POS_LOADED | COOP_POS_SLOW));
    CHECK(coop_players_last(&s, 2, now + 1500, NULL, NULL) == 1);
    CHECK(!coop_players_last(&s, 1, now, &at, &age) && !coop_players_last(&s, 3, now, &at, &age) && !coop_players_last(&s, 0, now, NULL, NULL) &&
          !coop_players_last(&s, 9, now, NULL, NULL));                                    /* itself, not heard, no member */
    /* its next one 2 s later keeps it heard: never "not heard" at the low rate */
    n = pos_bytes(b, 2, 100 + COOP_PLAYERS_SLOW_MS, "Forest", 0, 0, 0, 0, COOP_POS_IN_WORLD | COOP_POS_LOADED | COOP_POS_SLOW);
    CHECK(coop_players_receive(&s, 2, b, n, now + COOP_PLAYERS_SLOW_MS));
    now += COOP_PLAYERS_SLOW_MS;
    CHECK(coop_players_show(&s, 2, two, "Forest", now + COOP_PLAYERS_SLOW_MS + 1, &at) == COOP_PLAYER_SLOW);
    /* it switches showing on: its places start anew, not between the slow ones (which carry none) and its own */
    n = pos_bytes(b, 3, 200 + COOP_PLAYERS_SLOW_MS, "Forest", 8, 0, 8, 0, COOP_POS_IN_WORLD | COOP_POS_LOADED);
    CHECK(coop_players_receive(&s, 2, b, n, now + 100) && s.r[2].n == 1);
    CHECK(coop_players_show(&s, 2, two, "Forest", now + 101, &at) == COOP_PLAYER_SHOWN && at.pos[0] == 8 && at.pos[2] == 8);
    /* a member that shows the others loads (in no map, no place) and is back: no place between the two */
    n = pos_bytes(b, 1, 1000, "Forest", 10, 0, 10, 0, COOP_POS_IN_WORLD | COOP_POS_LOADED);
    CHECK(coop_players_receive(&s, 3, b, n, now));
    n = pos_bytes(b, 2, 1500, "Forest", 0, 0, 0, 0, COOP_POS_LOADED);
    CHECK(coop_players_receive(&s, 3, b, n, now + 500));
    CHECK(coop_players_show(&s, 3, two, "Forest", now + 600, &at) == COOP_PLAYER_NOT_IN_MAP);
    n = pos_bytes(b, 3, 1600, "Forest", 20, 0, 20, 0, COOP_POS_IN_WORLD | COOP_POS_LOADED);
    CHECK(coop_players_receive(&s, 3, b, n, now + 600));
    /* (shown 100 ms behind: the sender's 1575, three quarters of the way from the one in no map: its own place, not 15) */
    CHECK(coop_players_show(&s, 3, two, "Forest", now + 675, &at) == COOP_PLAYER_SHOWN && at.pos[0] == 20 && at.pos[2] == 20);
    /* the member leaves: nothing kept for the tab */
    coop_players_roster(&s, 1, ids, sessions, 2);
    CHECK(!coop_players_last(&s, 3, now + 700, &at, &age) && coop_players_last(&s, 2, now + 700, &at, &age));
    printf("coop players: the low rate: every 2 s and at once at a change of map or state; a game that shows nobody is never shown; the "
           "last message kept for the Co-op tab\n");
}

static void clock_bounds(void) {
    CoopPlayers p = {0};
    const int ids[] = {1, 2};
    const uint64_t sessions[] = {11, 22};
    coop_players_roster(&p, 1, ids, sessions, 2);
    uint8_t bytes[COOP_MAX_MESSAGE];
    for (uint32_t seq = 1; seq <= 2; seq++) {
        const size_t n = pos_bytes(bytes, seq, 1000u - (uint32_t)INT32_MAX, "Clock", 1, 2, 3, 0, COOP_POS_IN_WORLD);
        CHECK(coop_players_receive(&p, 2, bytes, n, 1000));
        CHECK(p.r[2].offset == INT32_MAX);
    }
    const size_t n = pos_bytes(bytes, 3, 1000u - (uint32_t)(INT32_MAX - 1), "Clock", 1, 2, 3, 0, COOP_POS_IN_WORLD);
    CHECK(coop_players_receive(&p, 2, bytes, n, 1000) && p.r[2].offset == INT32_MAX - 1);
}

static void interpolation_bounds(void) {
    CoopPlayers p = {0};
    const int ids[] = {1, 2};
    const uint64_t sessions[] = {11, 22};
    coop_players_roster(&p, 1, ids, sessions, 2);
    uint8_t bytes[COOP_MAX_MESSAGE];
    CoopPos a = {.pseq = 1, .t_ms = 1000, .flags = COOP_POS_IN_WORLD, .nslots = 1, .area = "Bounds",
                 .pos = {-100000, 0, 0}, .yaw = -FLT_MAX, .slot = {{"Pose", -FLT_MAX}}}, b = a, shown;
    CHECK(coop_players_receive(&p, 2, bytes, coop_pos_pack(bytes, sizeof bytes, &a), 2000));
    b.pseq = 2, b.t_ms = 1200, b.pos[0] = 100000, b.yaw = FLT_MAX, b.slot[0].frame = FLT_MAX;
    CHECK(coop_players_receive(&p, 2, bytes, coop_pos_pack(bytes, sizeof bytes, &b), 2200));
    CHECK(coop_players_show(&p, 2, 1u << 2, "Bounds", 2200, &shown) == COOP_PLAYER_SHOWN);
    CHECK(isfinite(shown.yaw) && fabsf(shown.yaw) < 7 && isfinite(shown.slot[0].frame));
    CHECK(fabsf(shown.pos[0]) <= COOP_PLAYERS_FAR);
    memset(&p, 0, sizeof p);
    coop_players_roster(&p, 1, ids, sessions, 2);
    const uint32_t times[] = {0x70000000u, 0, 0xe0000000u};
    const float positions[] = {0, -100000, 100000};
    for (unsigned i = 0; i < 3; i++) {
        const size_t n = pos_bytes(bytes, i + 1, times[i], "Bounds", positions[i], 0, 0, 0, COOP_POS_IN_WORLD);
        CHECK(coop_players_receive(&p, 2, bytes, n, 2000 + 10 * i));
    }
    CHECK(coop_players_show(&p, 2, 1u << 2, "Bounds", 2120, &shown) == COOP_PLAYER_SHOWN);
    CHECK(isfinite(shown.pos[0]) && fabsf(shown.pos[0]) <= COOP_PLAYERS_FAR);
}

/* ---- part 2: the fake guest */
typedef struct { uint64_t x[32]; uint64_t pc; uint64_t vreg[32][2]; } Ctx;
typedef void (*A64Fn)(Ctx *);
typedef int SRWLOCK;
#define SRWLOCK_INIT 0
static int store_busy;   /* the co-op thread "holds" the store's lock */
static int locks_taken;
static int TryAcquireSRWLockExclusive(SRWLOCK *l) { if (*l == 7 && store_busy) return 0; locks_taken++; return 1; }
static void AcquireSRWLockExclusive(SRWLOCK *l) { (void)l; }
static void ReleaseSRWLockExclusive(SRWLOCK *l) { (void)l; }

static struct { uint64_t base, size; uint8_t *mem; } regions[2] = {{0x83f00000ull, 0x1000000, NULL}, {0x10000000ull, 4u << 20, NULL}};
static unsigned reads;   /* guest reads and writes of any kind */
static uint8_t *host_ptr(uint64_t a, uint64_t n) {
    for (int i = 0; i < 2; i++)
        if (a >= regions[i].base && n <= regions[i].size && a - regions[i].base <= regions[i].size - n) return regions[i].mem + (a - regions[i].base);
    return NULL;
}
static void *hos_guest_range(uint64_t a, uint64_t n) { reads++; return host_ptr(a, n); }
static void *gp(uint64_t a) {
    void *p = host_ptr(a, 1);
    if (!p) { fprintf(stderr, "FAIL: an access outside the fake guest at %#llx\n", (unsigned long long)a); exit(1); }
    return p;
}
#define GP(a) gp(a)
static uint64_t ld(uint64_t a, unsigned n) {
    uint64_t v = 0;
    const uint8_t *p = host_ptr(a, n);
    if (!p) { fprintf(stderr, "FAIL: a load outside the fake guest at %#llx\n", (unsigned long long)a); exit(1); }
    memcpy(&v, p, n);
    return v;
}
static void st(uint64_t a, uint64_t v, unsigned n) {
    uint8_t *p = host_ptr(a, n);
    if (!p) { fprintf(stderr, "FAIL: a store outside the fake guest at %#llx\n", (unsigned long long)a); exit(1); }
    memcpy(p, &v, n);
}
static uint64_t recomp_load32(Ctx *c, uint64_t a) { (void)c; reads++; return ld(a, 4); }
static uint64_t recomp_load64(Ctx *c, uint64_t a) { (void)c; reads++; return ld(a, 8); }
static void recomp_store8(Ctx *c, uint64_t a, uint64_t v) { (void)c; st(a, v, 1); }
static void recomp_store32(Ctx *c, uint64_t a, uint64_t v) { (void)c; st(a, v, 4); }
static void recomp_store64(Ctx *c, uint64_t a, uint64_t v) { (void)c; st(a, v, 8); }
static void recomp_store64_sp(Ctx *c, uint64_t a, uint64_t v) { (void)c; st(a, v, 8); }
static float ldf(uint64_t a) { float f; uint32_t v = (uint32_t)ld(a, 4); memcpy(&f, &v, 4); return f; }
static void stf(uint64_t a, float f) { uint32_t v; memcpy(&v, &f, 4); st(a, v, 4); }

static uint64_t heap_at = 0x10000000ull;
static uint64_t galloc(uint64_t n) {
    const uint64_t a = heap_at;
    heap_at += (n + 15) & ~15ull;
    if (!host_ptr(a, n)) { fprintf(stderr, "FAIL: the fake heap is full\n"); exit(1); }
    return a;
}
static uint64_t gtext(const char *s) {
    const uint64_t a = galloc(strlen(s) + 1);
    memcpy(gp(a), s, strlen(s) + 1);
    return a;
}
static int allocations, refuse_alloc;
static uint64_t hos_sys_alloc(uint64_t size, uint64_t align) {
    (void)align; allocations++; return refuse_alloc ? 0 : galloc(size);
}

static char logbuf[1 << 16];
static size_t logat;
static void hos_log(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    if (logat < sizeof logbuf) logat += (size_t)vsnprintf(logbuf + logat, sizeof logbuf - logat, fmt, ap);
    va_end(ap);
    if (logat < sizeof logbuf - 1) logbuf[logat++] = '\n', logbuf[logat] = 0;
}
static int logged(const char *text) { return strstr(logbuf, text) != NULL; }
static void log_clear(void) { logat = 0, logbuf[0] = 0; }

static uint64_t now_ms = 2000000;
static uint64_t coop_now_ms(void) { return now_ms; }
static char hos_main_build_id[64] = "e0f3ccfaf50065ae4ea01eb5aeb13b1a45da3348";
static int ini_players;
int hos_ini_option(const char *key, int otherwise) { return !strcmp(key, "coop_players") ? ini_players : otherwise; }
static int assets_ready = 1;
int hos_coop_players_assets(void) { return assets_ready; }

/* progress sync's two answers */
static int playing;
static uint32_t accepted;
static int hos_oot_coop_playing(void) { return playing; }
static uint32_t hos_oot_coop_shown(void) { return accepted; }

/* the session */
static CoopMsgFn pos_fn;
static CoopViewFn view_fn;
static CoopView view;
static CoopGameplay gameplay;
static int presence = 1;
static int hos_oot_coop_presence(void) { return presence; }
static int names_permission = 1;
int hos_oot_coop_offer(CoopOffer *out) {
    memset(out, 0, sizeof *out);
    out->accepted = accepted != 0, out->n = 1;
    out->member[0].id = 2;
    snprintf(out->member[0].name, sizeof out->member[0].name, "Friend");
    out->rules.epoch = 1;
    coop_feature_set(&out->rules, COOP_RULE_PRESENCE, presence);
    coop_feature_set(&out->rules, COOP_RULE_NAMES, names_permission);
    return out->accepted;
}
static void owner_context(void) {
    CoopGameplayContext c = {0};
    c.version = COOP_GAMEPLAY_VERSION, c.size = sizeof c;
    c.capabilities = presence ? COOP_GAMEPLAY_PRESENTATION : 0;
    c.offer = 1, c.rules_epoch = 1, c.room_session = 11, c.save_generation = 1, c.area_generation = 1;
    for (int i = 0; i < view.n; i++)
        if (accepted & (1u << view.member[i].id)) c.accepted_session[view.member[i].id] = view.member[i].session;
    coop_gameplay_publish(&gameplay, &c);
}
int hos_oot_coop_gameplay_register(const CoopGameplayWriter *writer) { return coop_gameplay_register(&gameplay, writer); }
int hos_oot_coop_gameplay_ticket(unsigned slot, unsigned member, CoopGameplayTicket *ticket) {
    owner_context();
    return coop_gameplay_ticket(&gameplay, slot, member, member <= 8 ? gameplay.context.accepted_session[member] : 0, ticket);
}
int hos_oot_coop_gameplay_dispatch(const CoopGameplayTicket *ticket, const void *payload, size_t size, void *game) {
    owner_context();
    return coop_gameplay_dispatch(&gameplay, ticket, payload, size, game);
}
static uint8_t sent[COOP_MAX_MESSAGE];
static size_t sent_n;
static int sends;
int coop_on_message(uint8_t kind, CoopMsgFn fn, void *user) { (void)user; if (kind == COOP_K_POS) pos_fn = fn; return 1; }
int coop_on_view(CoopViewFn fn, void *user) { (void)user; view_fn = fn; return 1; }
void coop_view_get(CoopView *out) { *out = view; }
int coop_send(uint8_t kind, const void *bytes, size_t n) {
    CHECK(kind == COOP_K_POS && n <= sizeof sent);
    memcpy(sent, bytes, n), sent_n = n, sends++;
    return 1;
}

/* the game's actor system: requests of 0x30 bytes (+0 state, +8 the actor), actors of 0x400 */
enum { FN_CREATE, FN_TAKE, FN_DROP, FN_FIND, FN_FREE, FN_REQ_DELETE, FN_DELETE, FN_MTX, FN_PLAY, FN_FRAME_GET, FN_FRAME_SET,
       FN_EQUIP, FN_GEAR_STATE, FN_HAND, FN_GEAR_REMOVE, FNS };
static int calls[FNS], missing_fn = -1, refuse_create;
static uint64_t the_mgr, the_queue, last_request, last_actor, next_id = 500;
static char made_name[64];
static float made_place[16];
static uint8_t made_mode;
static uint64_t created_stack[3], created_outcome;
static float mtx[12];
static uint64_t mtx_actor, deleted_actor;
static int pending;   /* the last request waits in the queue of pending creates */

static void clobber(Ctx *c) { for (int i = 1; i < 18; i++) c->x[i] = 0xdead0000 + (unsigned)i; c->x[31] -= 0x200, c->x[30] = 0xbad; }
static void fake_create(Ctx *c) {
    calls[FN_CREATE]++;
    CHECK(c->x[0] == the_mgr && c->x[4] == 1 && !c->x[5] && !c->x[6] && !c->x[7] && !(c->x[31] & 15));
    snprintf(made_name, sizeof made_name, "%s", (const char *)gp(ld(c->x[1], 8)));
    for (int i = 0; i < 15; i++) made_place[i] = ldf(c->x[2] + 4u * (unsigned)i);
    CHECK(ld(c->x[2] + 0x40, 8) == 0x83f04f8cull && ld(c->x[2] + 0x48, 1) == 0);
    made_mode = (uint8_t)ld(c->x[2] + 0x98, 1);
    for (int i = 0; i < 3; i++) created_stack[i] = ld(c->x[31] + 8u * (unsigned)i, 8);
    created_outcome = created_stack[1];
    if (refuse_create) { c->x[0] = 0; clobber(c); return; }
    last_request = galloc(0x30);
    st(last_request, 1, 4);
    st(c->x[3], last_request, 8);
    pending = 1;
    c->x[0] = 1;
    clobber(c);
}
/* the game makes the actor of the last request: over the frames after the create */
static int made_has_anim;   /* the actors the game makes have an animation control of two slots */
static uint64_t give_anim(uint64_t actor, int n, const char *const *names) {
    const uint64_t parts = galloc(0x208), a = galloc(0x20), b = galloc(0x20), control = galloc(0x40), slots = galloc(0x48ull * (unsigned)n);
    st(actor + 0x270, parts, 8), st(parts + 0x40, a, 8), st(a + 0x18, b, 8), st(b + 0x18, control, 8);
    st(control + 0x18, (unsigned)n, 4), st(control + 0x20, slots, 8);
    for (int i = 0; i < n; i++) {
        const uint64_t slot = galloc(0x110);
        st(slots + 0x48ull * (unsigned)i, slot, 8), st(slot + 0x10, gtext(names[i]), 8);
        stf(slot + 0x10c, 12.5f + (float)i);
    }
    return control;
}
static void game_makes(void) {
    static const char *const idle[2] = {"", ""};
    last_actor = galloc(0x400);
    st(last_actor + 0x18, next_id++, 4), st(last_actor + 0x2c, 3, 4), st(last_actor + 0x260, gtext(made_name), 8);
    if (made_has_anim) give_anim(last_actor, 2, idle);
    st(last_request, 3, 4), st(last_request + 8, last_actor, 8);
    pending = 0;
}
static void fake_take(Ctx *c) {
    calls[FN_TAKE]++;
    CHECK(c->x[0] == last_request && c->x[1] == 1 && !c->x[2] && (ld(last_request, 4) & 0xffff) == 3);
    st(last_actor + 0x2c, 4, 4);
    c->x[0] = last_actor;
    clobber(c);
}
static void fake_drop(Ctx *c) { calls[FN_DROP]++; CHECK(c->x[0] == last_request); clobber(c); }
static void fake_find(Ctx *c) {
    calls[FN_FIND]++;
    CHECK(c->x[0] == the_queue && ld(c->x[1], 8) == 0x84c795e0ull && ld(ld(c->x[1] + 8, 8), 8) == last_request);
    c->x[0] = (uint64_t)pending;
    clobber(c);
}
static void fake_free(Ctx *c) { calls[FN_FREE]++; CHECK(c->x[0] == last_request && pending); pending = 0; clobber(c); }
static void fake_req_delete(Ctx *c) { calls[FN_REQ_DELETE]++; CHECK(c->x[0] == last_request && c->x[1] == 1 && !pending); clobber(c); }
static void fake_delete(Ctx *c) {
    calls[FN_DELETE]++;
    CHECK(!c->x[1]);
    deleted_actor = c->x[0];
    st(c->x[0] + 0x2c, 11, 4);
    c->x[0] = 1;
    clobber(c);
}
static void fake_mtx(Ctx *c) {
    calls[FN_MTX]++;
    CHECK(!c->x[2]);
    mtx_actor = c->x[0];
    for (int i = 0; i < 12; i++) mtx[i] = ldf(c->x[1] + 4u * (unsigned)i);
    clobber(c);
}
/* The current frame lives in the slot; f_837d57c0 returns duration, a different value. */
static uint64_t link_control;
static struct { uint64_t actor; char node[64]; uint32_t slot; float rate, start, blend; uint8_t same; } played[8];
static struct { uint64_t control; uint32_t slot; float frame; } frame_set;
static void fake_frame_get(Ctx *c) {
    calls[FN_FRAME_GET]++;
    CHECK(c->x[0] == link_control && c->x[1] < 4);
    uint32_t bits;
    const float duration = 90.0f;
    memcpy(&bits, &duration, 4);
    c->vreg[0][0] = bits, c->vreg[1][0] = 0xdead;
    clobber(c);
}
static void fake_play(Ctx *c) {
    const uint64_t r = c->x[0];
    const int i = calls[FN_PLAY]++ & 7;
    played[i].actor = ld(r, 8), played[i].slot = (uint32_t)ld(r + 0x10, 4);
    snprintf(played[i].node, sizeof played[i].node, "%s", (const char *)gp(ld(r + 8, 8)));
    played[i].rate = ldf(r + 0x14), played[i].start = ldf(r + 0x18), played[i].blend = ldf(r + 0x1c), played[i].same = (uint8_t)ld(r + 0x20, 1);
    CHECK(!ld(r + 0x21, 1) && !ld(r + 0x28, 8));
    c->x[0] = strcmp(played[i].node, "Unknown") != 0;   /* (the actor's animation set has every node but this one) */
    clobber(c);
}
static void fake_frame_set(Ctx *c) {
    calls[FN_FRAME_SET]++;
    uint32_t bits = (uint32_t)c->vreg[0][0];
    frame_set.control = c->x[0], frame_set.slot = (uint32_t)c->x[1];
    memcpy(&frame_set.frame, &bits, 4);
    clobber(c);
}
/* Equipment handles and lasting native state are independent from the transient request byte. */
static uint64_t give_equipment(uint64_t actor) {
    uint64_t parts = ld(actor + 0x270, 8);
    if (!parts) parts = galloc(0x208), st(actor + 0x270, parts, 8);
    const uint64_t eu = galloc(0x1400);
    st(parts + 0x200, eu, 8), st(eu + 0x18, actor, 8);
    for (unsigned slot = 0; slot < 8; slot++) st(eu + 0x30 + slot * 0x18, UINT32_MAX, 4);
    return eu;
}
static uint64_t give_piece(uint64_t eu, unsigned slot, const char *name, unsigned state) {
    const uint64_t holder = galloc(0x50), actor = galloc(0x400);
    const unsigned generation = (unsigned)next_id++;
    st(eu + 0x28 + slot * 0x18, holder, 8), st(eu + 0x30 + slot * 0x18, generation, 4);
    st(holder + 0x40, actor, 8), st(holder + 0x48, generation, 4);
    st(actor + 0x18, generation + 99, 4), st(actor + 0x2c, 4, 4), st(actor + 0x260, gtext(name), 8);
    st(eu + 0x1300 + slot * 4, state, 4);   /* authored stand-in for native model-bind state, not a product offset */
    st(eu + 0x13bc + slot, 0, 1);
    return actor;
}
static int equip_refuse;
static void fake_equip(Ctx *c) {
    calls[FN_EQUIP]++;
    CHECK((c->x[1] == 0 || c->x[1] == 6) && c->x[3] == 2 && c->x[4] == UINT32_MAX && c->x[5] == 1 && c->x[6] == 0);
    const char *name = (const char *)gp(ld(c->x[2], 8));
    CHECK(!strncmp(name, c->x[1] ? "Weapon_Shield_" : "Weapon_Sword_", c->x[1] ? 14 : 13));
    if (!equip_refuse) give_piece(c->x[0], c->x[1] ? 4 : 0, name, 2);
    clobber(c); c->x[0] = !equip_refuse;
}
static void fake_gear_state(Ctx *c) {
    calls[FN_GEAR_STATE]++;
    CHECK(c->x[1] == 0 || c->x[1] == 4);
    const unsigned state = (unsigned)ld(c->x[0] + 0x1300 + c->x[1] * 4, 4);
    clobber(c); c->x[0] = state;
}
static void fake_hand(Ctx *c) {
    calls[FN_HAND]++;
    CHECK((c->x[1] == 0 || c->x[1] == 4) && (c->x[2] == 1 || c->x[2] == 2));
    st(c->x[0] + 0x1300 + c->x[1] * 4, c->x[2], 4); /* request consumed immediately; lasting state survives */
    st(c->x[0] + 0x13bc + c->x[1], 0, 1);
    clobber(c);
}
static void fake_gear_remove(Ctx *c) {
    calls[FN_GEAR_REMOVE]++;
    CHECK((c->x[1] == 0 || c->x[1] == 4) && c->x[2] == 1 && !c->x[3] && !c->x[4] && !c->x[5] && !c->x[6] && c->x[7] == 1);
    st(c->x[0] + 0x30 + c->x[1] * 0x18, UINT32_MAX, 4);
    clobber(c);
}
static A64Fn a64_lookup(uint64_t addr) {
    static const struct { uint64_t at; A64Fn fn; } t[FNS] = {
        {0x80392a70ull, fake_create}, {0x8038fbd0ull, fake_take}, {0x8038fde0ull, fake_drop}, {0x80b4e020ull, fake_find},
        {0x80b4e6e0ull, fake_free}, {0x80b4e2e0ull, fake_req_delete}, {0x800c6490ull, fake_delete}, {0x80c8fd60ull, fake_mtx},
        {0x802222a0ull, fake_play}, {0x837d57c0ull, fake_frame_get}, {0x837d5d70ull, fake_frame_set},
        {0x82e17b20ull, fake_equip}, {0x80d07ab0ull, fake_gear_state}, {0x80e54c40ull, fake_hand}, {0x82e1d1b0ull, fake_gear_remove}};
    for (int i = 0; i < FNS; i++)
        if (t[i].at == addr) return i == missing_fn ? NULL : t[i].fn;
    return NULL;
}
static int total_calls(void) { int n = 0; for (int i = 0; i < FNS; i++) n += calls[i]; return n; }

#include OOT_COOP_PLAYERS_BODY

/* ---- the made-up game: Link, the map flag, the event manager */
static uint64_t link_actor, map_text, events;
static void world_init(void) {
    for (int i = 0; i < 2; i++) regions[i].mem = calloc(1, regions[i].size);
    const uint64_t gd_holder = galloc(8), gd = galloc(0xd80), ev_holder = galloc(8), sys = galloc(0x460);
    st(0x84c9e180ull, gd_holder, 8), st(gd_holder, gd, 8);
    const uint64_t entries = galloc(0x108 * 4), table = galloc(8 * 16);
    map_text = galloc(64);
    st(gd + 0x608 + 0x08, 4, 4), st(gd + 0x608 + 0x10, entries, 8), st(gd + 0x608 + 0x18, table, 8), st(gd + 0x608 + 0x24, 16, 4);
    st(table + 8ull * (0x1d6189dau % 16), 0x1d6189dau, 4), st(table + 8ull * (0x1d6189dau % 16) + 4, 2, 4);
    st(entries + 2 * 0x108 + 0x68, map_text, 8);
    events = galloc(0x100);
    st(0x84c9e090ull, ev_holder, 8), st(ev_holder, events, 8);
    the_mgr = galloc(0x100), the_queue = galloc(0x100);
    st(0x84eb1888ull, the_mgr, 8), st(0x84eb0d90ull, sys, 8), st(sys + 0x458, the_queue, 8);
    link_actor = galloc(0x800);
    st(link_actor + 0x260, gtext("Player_Child"), 8);
}
static void link_at(const char *map, float x, float y, float z, float yaw) {
    snprintf((char *)gp(map_text), 64, "%s", map);
    stf(link_actor + 0x31c, x), stf(link_actor + 0x320, y), stf(link_actor + 0x324, z);
    const float s = sinf(yaw), co = cosf(yaw), m[9] = {co, 0, s, 0, 1, 0, -s, 0, co};
    for (int i = 0; i < 9; i++) stf(link_actor + 0x328 + 4u * (unsigned)i, m[i]);
}
static Ctx ctx;
static void frame(uint64_t ms) {
    const Ctx before = ctx;
    now_ms += ms;
    hos_oot_coop_players_update(&ctx, link_actor);
    CHECK(!memcmp(&before, &ctx, sizeof ctx));   /* the guest's registers as they were */
}
static void fresh(void) {
    memset(&P, 0, sizeof P), P.lock = 7;   /* (7: the store's lock, for store_busy) */
    memset(&gameplay, 0, sizeof gameplay), presence = 1;
    allocations = refuse_alloc = 0;
    memset(calls, 0, sizeof calls);
    log_clear();
    sends = 0, sent_n = 0, playing = 1, accepted = 0, store_busy = 0, missing_fn = -1, refuse_create = 0, pos_fn = NULL, view_fn = NULL;
    memset(&view, 0, sizeof view);
    st(events + 104, 0, 8);
    unsetenv("HOS_COOP_PLAYERS"), unsetenv("HOS_COOP_PLAYERS_ACTOR"), unsetenv("HOS_COOP_PLAYERS_TEST"), unsetenv("HOS_COOP_TRACE");
    unsetenv("HOS_COOP_PLAYERS_ANIM");
    ini_players = 0;
    assets_ready = 0;   /* legacy lifetime cases deliberately use the standing fallback */
    equip_refuse = 0;
    snprintf(hos_main_build_id, sizeof hos_main_build_id, "%s", "e0f3ccfaf50065ae4ea01eb5aeb13b1a45da3348");
}
/* a room of this player (1) and member 2, as the session's view says it */
static void room(int version) {
    view.state = COOP_JOINED, view.version = (unsigned)version, view.self = 1, view.n = 2;
    view.member[0].id = 1, view.member[0].session = 11, view.member[1].id = 2, view.member[1].session = 22;
}
static uint32_t their_pseq;
static void they_are_at(const char *map, float x, float y, float z, float yaw, uint8_t flags) {
    uint8_t b[COOP_MAX_MESSAGE];
    const size_t n = pos_bytes(b, ++their_pseq, (uint32_t)now_ms, map, x, y, z, yaw, flags);
    pos_fn(2, b, n, NULL);
}

static void game_side(const char *steps_path) {
    world_init();
    ctx.x[31] = 0x10000000ull + (3u << 20), ctx.x[19] = 19, ctx.x[30] = 30;
    link_at("Forest", 10, 5, 20, 0);

    /* off (not switched on): registered all the same; until co-op is on and a save is played Link's update reads nothing */
    fresh();
    setenv("HOS_COOP_TRACE", "1", 1);
    room(3);
    hos_oot_coop_players_start();
    CHECK(P.on && !P.show && pos_fn && view_fn && P.store.others == 1 && !P.blocks && logged("the other players are not shown (coop_players is off)"));
    playing = 0;
    reads = 0, locks_taken = 0;
    for (int i = 0; i < 5; i++) frame(16);
    CHECK(!reads && !locks_taken && !total_calls() && !sends);
    /* in play it says this game's map and that a save is played, with no place, every 2 s, a change of map at once */
    playing = 1;
    link_at("Forest", 10, 5, 20, 0.5f);
    frame(16);
    CoopPos mine;
    CHECK(sends == 1 && coop_pos_parse(sent, sent_n, &mine) && !strcmp(mine.area, "Forest") &&
          mine.flags == (COOP_POS_IN_WORLD | COOP_POS_LOADED | COOP_POS_SLOW) && !mine.pos[0] && !mine.pos[1] && !mine.pos[2] && !mine.yaw && !mine.nslots);
    frame(1000), frame(COOP_PLAYERS_SLOW_MS - 1001);
    CHECK(sends == 1);
    frame(1);
    CHECK(sends == 2);
    link_at("Field", 1, 2, 3, 0);
    frame(16);
    CHECK(sends == 2);
    frame(COOP_PLAYERS_SEND_MS - 16);
    CHECK(sends == 3 && coop_pos_parse(sent, sent_n, &mine) && !strcmp(mine.area, "Field") && mine.pseq == 3 && !mine.pos[0]);
    /* a member says where it is: kept for the Co-op tab (map, flags, age), and nothing is made, accepted or not */
    accepted = 1u << 2;
    their_pseq = 0;
    they_are_at("Field", 12, 5, 24, 1.0f, COOP_POS_IN_WORLD | COOP_POS_LOADED);
    frame(16), frame(16);
    char area[COOP_POS_TEXT + 1] = "?";
    uint8_t flags = 0;
    uint64_t age = 0;
    CHECK(!total_calls() && P.puppet[2].state == PP_NONE && !logged("coop: puppet: member 2"));
    CHECK(logged("coop: players: member 2 says: Field, in the world, child Link\n"));   /* (HOS_COOP_TRACE: once, at a change) */
    CHECK(hos_oot_coop_players_last(2, area, &flags, &age) == 1 && !strcmp(area, "Field") && flags == (COOP_POS_IN_WORLD | COOP_POS_LOADED) && age == 32);
    CHECK(hos_oot_coop_players_last(2, NULL, NULL, NULL) == 1 && !hos_oot_coop_players_last(1, area, &flags, &age) && !hos_oot_coop_players_last(3, NULL, NULL, NULL));
    CoopPos snapshot;
    CHECK(hos_oot_coop_players_snapshot(2, &snapshot, &age) && !strcmp(snapshot.area, "Field"));
    P.ticket[2].generation++;
    CHECK(!hos_oot_coop_players_snapshot(2, &snapshot, &age));   /* old acceptance/area generation cannot feed travel */
    P.ticket[2].generation--;
    log_clear();
    they_are_at("Field", 12, 5, 25, 1.0f, COOP_POS_IN_WORLD | COOP_POS_LOADED);
    they_are_at("Field", 0, 0, 0, 0, COOP_POS_LOADED | COOP_POS_SLOW);
    CHECK(logged("member 2 says: Field, a save is played, not in the world, child Link, its game shows nobody") && !logged("in the world, child Link\n"));
    /* outside play the present's tick says so: a map change or a load (the last map), the title screen (no map), each at once */
    frame(100);
    CHECK(sends == 3);
    playing = 0;
    frame(16);
    CHECK(sends == 3 && !total_calls());
    hos_oot_coop_players_tick(1, 0);
    CHECK(sends == 4 && coop_pos_parse(sent, sent_n, &mine) && mine.flags == (COOP_POS_LOADED | COOP_POS_SLOW) && !strcmp(mine.area, "Field") && !mine.pos[0]);
    frame(1000), hos_oot_coop_players_tick(1, 0);
    CHECK(sends == 4);
    frame(COOP_PLAYERS_SLOW_MS - 1000), hos_oot_coop_players_tick(1, 0);
    CHECK(sends == 5);
    frame(COOP_PLAYERS_SEND_MS), hos_oot_coop_players_tick(0, 0);
    CHECK(sends == 6 && coop_pos_parse(sent, sent_n, &mine) && mine.flags == COOP_POS_SLOW && !mine.area[0]);
    /* in play again: Link's update sends at once; the tick sends nothing while it does */
    playing = 1;
    frame(COOP_PLAYERS_SEND_MS);
    CHECK(sends == 7 && coop_pos_parse(sent, sent_n, &mine) && mine.flags == (COOP_POS_IN_WORLD | COOP_POS_LOADED | COOP_POS_SLOW));
    frame(COOP_PLAYERS_SEND_MS), hos_oot_coop_players_tick(1, 1);
    CHECK(sends == 7);
    /* another build with the switch off: nothing runs, not a word */
    fresh();
    snprintf(hos_main_build_id, sizeof hos_main_build_id, "%s", "e0f3ccfaf50065ae4ea01eb5aeb13b1a45da3349");
    hos_oot_coop_players_start();
    frame(16), hos_oot_coop_players_tick(1, 0);
    CHECK(!P.on && !pos_fn && !logat && !sends && !hos_oot_coop_players_last(2, NULL, NULL, NULL));

    /* another build of the game, or a routine that is not in the image: off, with one line */
    fresh();
    setenv("HOS_COOP_PLAYERS", "1", 1);
    snprintf(hos_main_build_id, sizeof hos_main_build_id, "%s", "e0f3ccfaf50065ae4ea01eb5aeb13b1a45da3349");
    hos_oot_coop_players_start();
    frame(16);
    CHECK(!P.on && !pos_fn && logged("the other players are not shown") && !total_calls());
    fresh();
    setenv("HOS_COOP_PLAYERS", "1", 1);
    missing_fn = FN_TAKE;
    hos_oot_coop_players_start();
    CHECK(!P.on && logged("the other players are not shown"));
    fresh();
    setenv("HOS_COOP_PLAYERS", "0", 1);   /* the environment over the options file */
    ini_players = 1;
    hos_oot_coop_players_start();
    CHECK(P.on && !P.show);

    /* on (the options file): a room joined before the start is taken from the session's view */
    fresh();
    ini_players = 1;
    room(3);
    hos_oot_coop_players_start();
    CHECK(P.on && P.show && pos_fn && view_fn && P.store.others == 1 && logged("child/adult bodies configured as PouchPlayer_Child"));
    their_pseq = 0;

    /* this game's own place goes out: map, place, facing, child; at most 15 a second */
    link_at("Forest", 10, 5, 20, 0.5f);
    frame(16);
    CHECK(sends == 1 && coop_pos_parse(sent, sent_n, &mine) && !strcmp(mine.area, "Forest") && mine.pos[0] == 10 && mine.pos[1] == 5 &&
          mine.pos[2] == 20 && fabsf(mine.yaw - 0.5f) < 1e-5f && mine.flags == (COOP_POS_IN_WORLD | COOP_POS_LOADED) && mine.pseq == 1);
    frame(16), frame(16);
    CHECK(sends == 1);
    frame(40);
    CHECK(sends == 2 && !total_calls());   /* nobody to show yet: no call into the game */

    /* member 2 is in this map but not accepted: not made */
    they_are_at("Forest", 12, 5, 24, 1.0f, COOP_POS_IN_WORLD);
    frame(16);
    CHECK(!calls[FN_CREATE] && !logged("member 2 is in"));
    /* accepted: said in the log, asked for where it stands */
    accepted = 1u << 2;
    they_are_at("Forest", 12, 5, 24, 1.0f, COOP_POS_IN_WORLD);   /* fresh data after acceptance */
    frame(16);
    CHECK(calls[FN_CREATE] == 1 && !strcmp(made_name, "PouchPlayer_Child") && logged("coop: puppet: member 2 is in Forest at 12.00 5.00 24.00, child Link"));
    CHECK(made_place[0] == 12 && made_place[1] == 5 && made_place[2] == 24 && made_mode == 1);
    CHECK(fabsf(made_place[3] - cosf(1)) < 1e-5f && fabsf(made_place[5] - sinf(1)) < 1e-5f && made_place[7] == 1 &&
          fabsf(made_place[9] + sinf(1)) < 1e-5f && fabsf(made_place[11] - cosf(1)) < 1e-5f);                    /* the facing as a turn about +y */
    CHECK(made_place[12] == 1 && made_place[13] == 1 && made_place[14] == 1);                                 /* scale */
    CHECK(!created_stack[0] && !created_stack[2] && created_outcome == P.puppet[2].block + B_OUTCOME);
    CHECK(P.puppet[2].state == PP_ASKED);
    /* while the game makes it: no second request, nothing placed */
    frame(16), frame(16);
    CHECK(calls[FN_CREATE] == 1 && !calls[FN_TAKE] && !calls[FN_MTX]);
    /* made: taken once, then placed every frame where the member is */
    game_makes();
    they_are_at("Forest", 13, 5, 25, 1.0f, COOP_POS_IN_WORLD);
    frame(16);
    CHECK(calls[FN_TAKE] == 1 && P.puppet[2].state == PP_SHOWN && P.puppet[2].actor == last_actor && P.made == 1 && logged("member 2 is shown as PouchPlayer_Child"));
    CHECK(calls[FN_MTX] == 1 && mtx_actor == last_actor && ld(P.puppet[2].block + B_REQUEST, 8) == 0);
    frame(200);
    CHECK(calls[FN_MTX] == 2 && mtx[3] == 13 && mtx[7] == 5 && mtx[11] == 25 && ldf(last_actor + 0x31c) == 13 && ldf(last_actor + 0x324) == 25 &&
          ldf(last_actor + 0x2ec) == 13 && fabsf(ldf(last_actor + 0x328) - cosf(1)) < 1e-5f);
    they_are_at("Forest", 14, 5, 26, 1.0f, COOP_POS_IN_WORLD);
    frame(200);
    CHECK(calls[FN_MTX] == 3 && mtx[3] == 14 && calls[FN_CREATE] == 1 && calls[FN_TAKE] == 1);

    /* the co-op thread holds the positions: this frame does nothing, the next goes on */
    store_busy = 1;
    frame(16);
    CHECK(calls[FN_MTX] == 3);
    store_busy = 0;

    /* an event plays (a conversation, a cutscene): nothing is sent, made, moved or removed */
    st(events + 104, 0x1234, 8);
    const int sends_before = sends;
    they_are_at("Field", 0, 0, 0, 0, COOP_POS_IN_WORLD);
    frame(300), frame(300);
    CHECK(calls[FN_MTX] == 3 && !calls[FN_DELETE] && sends == sends_before);
    st(events + 104, 0, 8);

    /* the member is in another map now: its actor is deleted, and the log says where it went */
    frame(16);
    CHECK(calls[FN_DELETE] == 1 && deleted_actor == last_actor && P.puppet[2].state == PP_NONE && P.deleted == 1);
    CHECK(logged("member 2 is hidden (in another map)") && logged("member 2 is in Field at 0.00 0.00 0.00, child Link (another map than this game's: not shown)"));
    CHECK(logged("coop: players: nobody is shown now: made 1, deleted 1 (1 by this game, 0 gone with their map), 0 still shown, 0 requests failed"));
    frame(16);
    CHECK(calls[FN_DELETE] == 1 && calls[FN_CREATE] == 1);

    /* The child family refuses an adult pose without creating an adult body, then accepts a fresh child pose. */
    log_clear();
    they_are_at("Forest", 14, 5, 26, 0, COOP_POS_IN_WORLD | COOP_POS_ADULT);
    frame(200);
    CHECK(calls[FN_CREATE] == 1 && P.puppet[2].state == PP_NONE);
    they_are_at("Forest", 14, 5, 26, 0, COOP_POS_IN_WORLD | COOP_POS_ADULT);
    frame(AGAIN_MS);
    CHECK(calls[FN_CREATE] == 1 && P.puppet[2].state == PP_NONE);
    they_are_at("Forest", 14, 5, 26, 0, COOP_POS_IN_WORLD);
    frame(16);
    CHECK(calls[FN_CREATE] == 2 && !strcmp(made_name, "PouchPlayer_Child"));
    game_makes();
    frame(16);
    CHECK(calls[FN_TAKE] == 2 && P.made == 2 && P.puppet[2].state == PP_SHOWN);

    /* this game stops being played (its map changes, a save loads): what it made goes at that update, then nothing */
    const uint64_t second = last_actor;
    playing = 0;
    frame(16);
    CHECK(calls[FN_DELETE] == 2 && deleted_actor == second && P.deleted == 2 && P.puppet[2].state == PP_NONE);
    CHECK(logged("made 2, deleted 2 (2 by this game, 0 gone with their map), 0 still shown, 0 requests failed"));
    int before = total_calls();
    const int sent_before = sends;
    for (int i = 0; i < 10; i++) they_are_at("Forest", 14, 5, 26, 0, COOP_POS_IN_WORLD), frame(300);
    CHECK(total_calls() == before && sends == sent_before);
    playing = 1;

    /* silence: hidden after 3 s */
    they_are_at("Forest", 1, 1, 1, 0, COOP_POS_IN_WORLD);
    frame(16);
    CHECK(calls[FN_CREATE] == 3);
    game_makes();
    frame(16);
    CHECK(P.puppet[2].state == PP_SHOWN && P.made == 3);
    frame(COOP_PLAYERS_SILENT_MS - 100);
    CHECK(P.puppet[2].state == PP_SHOWN && calls[FN_DELETE] == 2);
    log_clear();
    frame(200);
    CHECK(P.puppet[2].state == PP_NONE && calls[FN_DELETE] == 3 && logged("member 2 is hidden (not heard)"));

    /* a request the game never makes: cancelled after 20 s out of the queue of pending creates, nothing left */
    they_are_at("Forest", 1, 1, 1, 0, COOP_POS_IN_WORLD);
    frame(AGAIN_MS);
    they_are_at("Forest", 1, 1, 1, 0, COOP_POS_IN_WORLD);
    frame(16);
    CHECK(calls[FN_CREATE] == 4 && P.puppet[2].state == PP_ASKED);
    for (int i = 0; i < 21; i++) they_are_at("Forest", 1, 1, 1, 0, COOP_POS_IN_WORLD), frame(1000);
    CHECK(calls[FN_FIND] == 1 && calls[FN_FREE] == 1 && !calls[FN_REQ_DELETE] && P.puppet[2].state == PP_NONE && P.failed == 1 && P.made == 3);
    CHECK(logged("was not made (not after 20 s)") && ld(P.puppet[2].block + B_REQUEST, 8) == 0);

    /* the member leaves while its actor is being made: the request is cancelled (its actor deleted when it was made already) */
    they_are_at("Forest", 1, 1, 1, 0, COOP_POS_IN_WORLD);
    frame(AGAIN_MS);
    they_are_at("Forest", 1, 1, 1, 0, COOP_POS_IN_WORLD);
    frame(16);
    CHECK(calls[FN_CREATE] == 5 && P.puppet[2].state == PP_ASKED);
    pending = 0;   /* (the create ran, the actor is not taken) */
    view.n = 1, view.version = 4;
    view_fn(&view, NULL);
    log_clear();
    frame(16);
    CHECK(calls[FN_FIND] == 2 && calls[FN_REQ_DELETE] == 1 && calls[FN_FREE] == 1 && P.puppet[2].state == PP_NONE && P.failed == 2);
    CHECK(logged("member 2 is hidden (left the room)") && logged("coop: players: left the room: made 3, deleted 3"));
    /* an older view arriving late does not bring the member back */
    view.n = 2, view.version = 3;
    view_fn(&view, NULL);
    CHECK(P.store.others == 0);

    /* the game refuses a request: counted, tried again later, nothing kept */
    room(5);
    view_fn(&view, NULL);
    refuse_create = 1;
    they_are_at("Forest", 1, 1, 1, 0, COOP_POS_IN_WORLD);
    frame(AGAIN_MS);
    they_are_at("Forest", 1, 1, 1, 0, COOP_POS_IN_WORLD);
    frame(16);
    CHECK(calls[FN_CREATE] == 6 && P.puppet[2].state == PP_NONE && P.failed == 3);
    frame(16);
    CHECK(calls[FN_CREATE] == 6);
    refuse_create = 0;

    /* the game removed the actor itself (another actor lives in its memory): not deleted again, counted as gone */
    they_are_at("Forest", 1, 1, 1, 0, COOP_POS_IN_WORLD);
    frame(AGAIN_MS);
    they_are_at("Forest", 1, 1, 1, 0, COOP_POS_IN_WORLD);
    frame(16);
    game_makes();
    frame(16);
    CHECK(P.puppet[2].state == PP_SHOWN && P.made == 4);
    st(last_actor + 0x18, 9999, 4);
    before = calls[FN_DELETE];
    const int placed = calls[FN_MTX];
    they_are_at("Forest", 1, 1, 1, 0, COOP_POS_IN_WORLD);
    frame(16);
    CHECK(P.puppet[2].state == PP_NONE && calls[FN_DELETE] == before && calls[FN_MTX] == placed && P.taken_away == 1 && logged("member 2's actor is gone"));
    /* the same when this game's map went with the actor in it (its memory gone: state 0): hidden, not deleted again */
    they_are_at("Forest", 1, 1, 1, 0, COOP_POS_IN_WORLD);
    frame(AGAIN_MS);
    they_are_at("Forest", 1, 1, 1, 0, COOP_POS_IN_WORLD);
    frame(16);
    game_makes();
    frame(16);
    CHECK(P.puppet[2].state == PP_SHOWN && P.made == 5);
    st(last_actor + 0x2c, 0, 4);
    playing = 0;
    log_clear();
    frame(16);
    CHECK(P.puppet[2].state == PP_NONE && calls[FN_DELETE] == before && P.taken_away == 2);
    CHECK(logged("made 5, deleted 5 (3 by this game, 2 gone with their map), 0 still shown"));
    playing = 1;

    /* on: while an event holds Link for 2.5 s the present says he stands in no map (no place), slowly; a member whose game
       shows nobody is never made */
    fresh();
    setenv("HOS_COOP_PLAYERS", "1", 1);
    room(1);
    hos_oot_coop_players_start();
    link_at("Forest", 10, 5, 20, 0);
    frame(16);
    CHECK(sends == 1 && coop_pos_parse(sent, sent_n, &mine) && mine.flags == (COOP_POS_IN_WORLD | COOP_POS_LOADED) && mine.pos[0] == 10);
    hos_oot_coop_players_tick(1, 1);
    CHECK(sends == 1);
    st(events + 104, 0x1234, 8);
    frame(IDLE_MS - 100), hos_oot_coop_players_tick(1, 1);
    CHECK(sends == 1);
    frame(100), hos_oot_coop_players_tick(1, 1);
    CHECK(sends == 2 && coop_pos_parse(sent, sent_n, &mine) && mine.flags == COOP_POS_LOADED && !strcmp(mine.area, "Forest") && !mine.pos[0]);
    frame(1000), hos_oot_coop_players_tick(1, 1);
    CHECK(sends == 2);
    frame(COOP_PLAYERS_SLOW_MS - 1000), hos_oot_coop_players_tick(1, 1);
    CHECK(sends == 3);
    st(events + 104, 0, 8);
    frame(COOP_PLAYERS_SEND_MS);
    CHECK(sends == 4 && coop_pos_parse(sent, sent_n, &mine) && mine.flags == (COOP_POS_IN_WORLD | COOP_POS_LOADED));
    accepted = 1u << 2;
    their_pseq = 0;
    they_are_at("Forest", 10, 5, 21, 0, COOP_POS_IN_WORLD | COOP_POS_LOADED | COOP_POS_SLOW);
    frame(16), frame(16);
    CHECK(!calls[FN_CREATE] && P.puppet[2].state == PP_NONE);

    /* a pointer of the game that leads nowhere: nothing is read past it, nothing is done */
    fresh();
    setenv("HOS_COOP_PLAYERS", "1", 1);
    setenv("HOS_COOP_PLAYERS_ACTOR", "Thing_Child", 1);
    room(1);
    hos_oot_coop_players_start();
    CHECK(P.on && !strcmp(P.actor_name[0], "Thing_Child"));
    accepted = 1u << 2;
    const uint64_t holder = ld(0x84c9e180ull, 8);
    st(0x84c9e180ull, 0x7000000000ull, 8);   /* GameDataMgr's holder outside the guest: no map, so nothing */
    frame(16);
    CHECK(!sends && !total_calls());
    st(0x84c9e180ull, holder, 8);
    st(0x84eb1888ull, 0, 8);                 /* no actor manager: no request */
    their_pseq = 0;
    they_are_at("Forest", 1, 1, 1, 0, COOP_POS_IN_WORLD);
    frame(16);
    CHECK(sends == 1 && !calls[FN_CREATE] && P.failed == 1);
    st(0x84eb1888ull, the_mgr, 8);
    they_are_at("Forest", 1, 1, 1, 0, COOP_POS_IN_WORLD);
    frame(AGAIN_MS);
    CHECK(calls[FN_CREATE] == 1 && !strcmp(made_name, "Thing_Child"));   /* same native guard with the supported child family */
    stf(link_actor + 0x31c, NAN);            /* Link's own place not a number: nothing goes out */
    const int out_before = sends;
    frame(100);
    CHECK(sends == out_before);
    link_at("Forest", 10, 5, 20, 0);
    CHECK(!calls[FN_PLAY] && !calls[FN_FRAME_GET] && !calls[FN_FRAME_SET]);   /* diagnostic actor did not opt into animation */

    /* Missing generated assets: even with native animation components, the standing fallback does not replay slots. */
    static const char *const link_plays[2] = {"Wait", ""};
    CoopPos theirs = {.flags = COOP_POS_IN_WORLD, .area = "Forest", .pos = {3, 5, 3}, .nslots = 2, .slot = {{"Move", 7.0f}, {"Unknown", 1.0f}}};
    uint8_t tb[COOP_MAX_MESSAGE];
    fresh();
    setenv("HOS_COOP_PLAYERS", "1", 1);
    room(1);
    hos_oot_coop_players_start();
    link_control = give_anim(link_actor, 2, link_plays);
    accepted = 1u << 2, made_has_anim = 1;
    theirs.pseq = 1, theirs.t_ms = (uint32_t)now_ms;
    pos_fn(2, tb, coop_pos_pack(tb, sizeof tb, &theirs), NULL);
    frame(16);
    game_makes();
    frame(16), frame(16);
    CHECK(P.puppet[2].state == PP_SHOWN && calls[FN_MTX] == 2 && !calls[FN_PLAY] && !calls[FN_FRAME_GET] && !calls[FN_FRAME_SET]);
    CHECK(sends == 1 && coop_pos_parse(sent, sent_n, &mine) && mine.nslots == 0);

    /* animation (HOS_COOP_PLAYERS_ANIM=1): what Link plays goes out with his place, and a member's is played on its actor */
    fresh();
    setenv("HOS_COOP_PLAYERS", "1", 1);
    setenv("HOS_COOP_PLAYERS_ANIM", "1", 1);
    assets_ready = 1;
    room(1);
    hos_oot_coop_players_start();
    CHECK(P.anim && !strcmp(P.actor_name[0], "CoopPuppet_Child"));
    frame(16);
    CHECK(sends == 1 && coop_pos_parse(sent, sent_n, &mine) && mine.nslots == 2 && !strcmp(mine.slot[0].node, "Wait") && mine.slot[0].frame == 12.5f &&
          !mine.slot[1].node[0] && calls[FN_FRAME_GET] == 0);
    const uint64_t sampled_slot = ld(ld(link_control + 0x20, 8), 8);
    for (int i = 0; i < 3; i++) {
        const float current = i == 0 ? 17.25f : i == 1 ? 1.0f : NAN;
        stf(sampled_slot + 0x10c, current);
        frame(COOP_PLAYERS_SEND_MS);
        CHECK(coop_pos_parse(sent, sent_n, &mine) && mine.nslots == 2);
        CHECK(mine.slot[0].frame == (isfinite(current) ? current : 0));
        CHECK(!calls[FN_FRAME_GET]);   /* duration must never become playback phase */
    }
    stf(sampled_slot + 0x10c, 12.5f);
    accepted = 1u << 2;
    made_has_anim = 1;
    {
        uint8_t b[COOP_MAX_MESSAGE];
        theirs.pseq = 1, theirs.t_ms = (uint32_t)now_ms;
        pos_fn(2, b, coop_pos_pack(b, sizeof b, &theirs), NULL);
        frame(16);
        game_makes();
        frame(16);
        CHECK(P.puppet[2].state == PP_SHOWN && calls[FN_PLAY] == 2);
        CHECK(played[0].actor == last_actor && !strcmp(played[0].node, "Move") && played[0].slot == 0 && played[0].rate == 1 && played[0].start == -1 &&
              played[0].blend == -1 && played[0].same == 1 && !strcmp(played[1].node, "Unknown") && played[1].slot == 1);
        CHECK(calls[FN_FRAME_SET] == 1 && frame_set.slot == 0 && frame_set.frame == 7.0f);   /* only the slot whose node the actor has */
        frame(16);
        CHECK(calls[FN_PLAY] == 2 && calls[FN_FRAME_SET] == 2);   /* the same nodes: not asked again, the frame set again */
        theirs.pseq = 2, theirs.t_ms = (uint32_t)now_ms, theirs.slot[0].frame = 9.0f;
        snprintf(theirs.slot[0].node, sizeof theirs.slot[0].node, "%s", "Wait");
        theirs.slot[1].node[0] = 0;   /* (a slot that plays nothing: not asked for) */
        pos_fn(2, b, coop_pos_pack(b, sizeof b, &theirs), NULL);
        frame(300);
        CHECK(calls[FN_PLAY] == 3 && !strcmp(played[2].node, "Wait") && frame_set.frame == 9.0f);
        /* another actor made for the member (it was hidden meanwhile): asked again for what the member plays */
        theirs.pseq = 3, theirs.t_ms = (uint32_t)now_ms;
        snprintf(theirs.area, sizeof theirs.area, "%s", "Field");
        pos_fn(2, b, coop_pos_pack(b, sizeof b, &theirs), NULL);
        frame(300);
        CHECK(P.puppet[2].state == PP_NONE);
        theirs.pseq = 4, theirs.t_ms = (uint32_t)now_ms;
        snprintf(theirs.area, sizeof theirs.area, "%s", "Forest");
        pos_fn(2, b, coop_pos_pack(b, sizeof b, &theirs), NULL);
        frame(AGAIN_MS);
        theirs.pseq = 5, theirs.t_ms = (uint32_t)now_ms;
        pos_fn(2, b, coop_pos_pack(b, sizeof b, &theirs), NULL);
        frame(16);
        game_makes();
        const int asked_before = calls[FN_PLAY];
        frame(16);
        CHECK(P.puppet[2].state == PP_SHOWN && calls[FN_PLAY] == asked_before + 1 && played[(asked_before) & 7].actor == last_actor);
    }

    /* An old build or a conflicting mod cannot silently request an unregistered actor. */
    fresh();
    assets_ready = 0;
    setenv("HOS_COOP_PLAYERS", "1", 1);
    setenv("HOS_COOP_PLAYERS_ANIM", "1", 1);
    hos_oot_coop_players_start();
    CHECK(!P.anim && !strcmp(P.actor_name[0], "PouchPlayer_Child") && !strcmp(P.actor_name[1], "PouchPlayer_Adult") && logged("animation unavailable"));
    fresh();
    assets_ready = 0;
    setenv("HOS_COOP_PLAYERS", "1", 1);
    setenv("HOS_COOP_PLAYERS_ANIM", "1", 1);
    setenv("HOS_COOP_PLAYERS_ACTOR", "LocalChild", 1);
    hos_oot_coop_players_start();
    CHECK(P.anim && !strcmp(P.actor_name[0], "LocalChild"));

    /* the test's made-up member: its places come from the file through the same message path (its lines of places carry
       no animation, whatever Link plays) */
    fresh();
    assets_ready = 1;
    setenv("HOS_COOP_PLAYERS_ANIM", "1", 1);
    setenv("HOS_COOP_PLAYERS", "1", 1);
    setenv("HOS_COOP_PLAYERS_TEST", steps_path, 1);
    room(1), view.member[1].id = 8, view.member[1].session = 2, accepted = 1u << 8;
    hos_oot_coop_players_start();
    CHECK(P.on && P.test && P.nsteps == 10 && !pos_fn && logged("member 8 is made up"));
    link_at("Forest", 10, 5, 20, 0);
    frame(16);
    CHECK(calls[FN_CREATE] == 1 && !strcmp(made_name, "Test_Actor") && made_place[0] == 9 && made_place[1] == 5 && made_place[2] == 22);
    CHECK(logged("coop: puppet: member 8 is in Forest at 9.00 5.00 22.00"));
    game_makes();
    frame(100);
    CHECK(P.puppet[8].state == PP_SHOWN && !calls[FN_PLAY] && !calls[FN_FRAME_GET]);
    frame(2900), frame(100), frame(100);       /* 3.2 s: between the lines at 2 s and 4 s, six tenths of the way less the 100 ms it is shown behind */
    CHECK(mtx[3] < 9 && mtx[3] > 7 && mtx[11] > 22 && mtx[11] < 24);
    frame(1000), frame(100), frame(100);       /* past 4 s: there */
    CHECK(fabsf(mtx[3] - 7) < 1e-3f && fabsf(mtx[11] - 24) < 1e-3f);
    frame(1000), frame(100);                   /* past 5 s: another map */
    CHECK(P.puppet[8].state == PP_NONE && calls[FN_DELETE] == 1 && logged("member 8 is in Elsewhere"));
    frame(1000), frame(100);                   /* past 6 s: gone: silent */
    frame(COOP_PLAYERS_SILENT_MS);
    CHECK(logged("made 1, deleted 1"));
    frame(3000), frame(100);                   /* past 12 s: a place in the map's own coordinates, wherever Link stood at the start */
    CHECK(calls[FN_CREATE] == 2 && made_place[0] == 30 && made_place[1] == 6 && made_place[2] == 40);
    game_makes();
    frame(100);
    CHECK(P.puppet[8].state == PP_SHOWN && fabsf(mtx[3] - 30) < 1e-3f && fabsf(mtx[7] - 6) < 1e-3f && fabsf(mtx[11] - 40) < 1e-3f);
    CHECK(fabsf(mtx[2] - 1) < 1e-3f && fabsf(mtx[0]) < 1e-3f);   /* facing 90 degrees: forward is +x */
    frame(1500), frame(100), frame(100);       /* past 14 s: at the last line's place */
    CHECK(fabsf(mtx[11] - 44) < 1e-3f);
    /* Revoke and restore permission between receipt and the game update: an old queued pose cannot create a body. */
    fresh();
    setenv("HOS_COOP_PLAYERS", "1", 1);
    room(1), accepted = 1u << 2;
    hos_oot_coop_players_start();
    presence = 0, reads = 0;
    for (int i = 0; i < 5; i++) frame(16);
    CHECK(!reads && !total_calls() && !sends && !P.blocks);
    presence = 1;
    they_are_at("Forest", 1, 2, 3, 0, COOP_POS_IN_WORLD);
    presence = 0; owner_context();
    presence = 1; owner_context();
    frame(16);
    CHECK(!calls[FN_CREATE] && !P.blocks);
    uint8_t replay[COOP_MAX_MESSAGE];
    const size_t replay_n = pos_bytes(replay, their_pseq, (uint32_t)now_ms, "Forest", 1, 2, 3, 0, COOP_POS_IN_WORLD);
    pos_fn(2, replay, replay_n, NULL);
    frame(16);
    CHECK(!calls[FN_CREATE] && !P.store.r[2].n);   /* a new grant is not a replay-window reset */
    they_are_at("Forest", 8, 2, 9, 0, COOP_POS_IN_WORLD);
    frame(16);
    CHECK(calls[FN_CREATE] == 1 && P.store.r[2].n == 1 && made_place[0] == 8 && made_place[2] == 9);
    const CoopGameplayWriter second_writer = gameplay.writer[COOP_GAMEPLAY_BODIES];
    CHECK(!hos_oot_coop_gameplay_register(&second_writer));
    /* Native animation storage is bounded independently from the packet's slot count. */
    CoopPos bounded = {0};
    const uint64_t control = anim_control(&ctx, link_actor), parts = ld(link_actor + A_PARTS, 8);
    const int gets_before = calls[FN_FRAME_GET];
    CHECK(control);
    st(control + 0x18, 65, 4);
    anim_read(&ctx, link_actor, &bounded);
    CHECK(!bounded.nslots && calls[FN_FRAME_GET] == gets_before);
    st(control + 0x18, 2, 4);
    st(link_actor + A_PARTS, 0, 8);
    anim_read(&ctx, link_actor, &bounded);
    CHECK(!bounded.nslots && calls[FN_FRAME_GET] == gets_before);
    st(link_actor + A_PARTS, parts, 8);
    made_has_anim = 0;
    st(link_actor + 0x270, 0, 8);
    /* Deferring storage until permission must not turn startup OOM into a per-frame allocation retry. */
    fresh();
    setenv("HOS_COOP_PLAYERS", "1", 1);
    room(1), accepted = 1u << 2;
    hos_oot_coop_players_start();
    refuse_alloc = 1;
    they_are_at("Forest", 1, 2, 3, 0, COOP_POS_IN_WORLD);
    for (int i = 0; i < 5; i++) frame(16);
    CHECK(allocations == 1 && !P.blocks && !calls[FN_CREATE] && P.storage_failed && logged("no bodies are created this start"));
    printf("coop players: the game's side: asked for, taken, placed, deleted; nothing while not played or in an event; counts say what was made and deleted\n");
}

static void body_gear(void) {
    static const char *const nodes[2] = {"Move", ""};
    CoopPos pose = {.flags = COOP_POS_IN_WORLD | COOP_POS_ADULT, .area = "Forest", .pos = {7, 5, 9},
                    .nslots = 1, .slot = {{"Move", 4}}, .sword = COOP_SWORD_MASTER, .shield = COOP_SHIELD_HYLIAN, .hands = 3};
    uint8_t bytes[COOP_MAX_MESSAGE];
    fresh();
    assets_ready = 1, ini_players = 1, presence = COOP_PRESENCE_SUPPORTED;
    room(1), accepted = 1u << 2;
    st(link_actor + A_FILE, gtext("Player_Adult"), 8);
    st(link_actor + A_PARTS, 0, 8);
    link_control = give_anim(link_actor, 2, nodes);
    const uint64_t own_eu = give_equipment(link_actor);
    give_piece(own_eu, 0, "Weapon_Sword_Master", 1);
    give_piece(own_eu, 4, "Weapon_Shield_Hylian", 2);
    hos_oot_coop_players_start();
    CHECK(P.anim && P.gear && !getenv("HOS_COOP_PLAYERS_ANIM")); /* local switch alone */
    CHECK(!strcmp(P.actor_name[1], "CoopPuppet_Adult"));
    frame(100);
    CoopPos mine;
    CHECK(coop_pos_parse(sent, sent_n, &mine) && (mine.flags & COOP_POS_ADULT) && mine.sword == COOP_SWORD_MASTER &&
          mine.shield == COOP_SHIELD_HYLIAN && mine.hands == COOP_HAND_SWORD && mine.nslots == 2);
    CHECK(!ld(own_eu + 0x13bc, 1)); /* transient request cleared; not falsely sheathed */
    for (unsigned invalid = 0; invalid < 5; invalid++) {
        if (invalid == 1 || invalid == 2) continue;
        CoopPos unavailable = {.flags = COOP_POS_ADULT};
        st(own_eu + 0x1300, invalid, 4);
        equipment_read(&ctx, link_actor, &unavailable);
        CHECK(!unavailable.sword && !(unavailable.hands & COOP_HAND_SWORD));
    }
    st(own_eu + 0x1300, 1, 4);
    pose.pseq = ++their_pseq, pose.t_ms = (uint32_t)now_ms;
    pos_fn(2, bytes, coop_pos_pack(bytes, sizeof bytes, &pose), NULL);
    frame(16);
    CHECK(P.puppet[2].state == PP_ASKED && !strcmp(made_name, "CoopPuppet_Adult"));
    made_has_anim = 1; game_makes();
    const uint64_t remote_eu = give_equipment(last_actor);
    frame(16);
    CHECK(calls[FN_EQUIP] == 2 && !calls[FN_HAND] && calls[FN_PLAY] == 1);
    frame(16);
    CHECK(calls[FN_HAND] == 2 && calls[FN_EQUIP] == 2 && calls[FN_TAKE] == 1);
    frame(16);
    CHECK(calls[FN_HAND] == 2 && calls[FN_EQUIP] == 2); /* no duplicate updater or repeated consumed request */
    CHECK(ld(remote_eu + 0x1300, 4) == 1 && ld(remote_eu + 0x1310, 4) == 1);
    pose.hands = 0, pose.pseq++, pose.t_ms = (uint32_t)now_ms;
    pos_fn(2, bytes, coop_pos_pack(bytes, sizeof bytes, &pose), NULL);
    frame(200);
    CHECK(calls[FN_HAND] == 4 && ld(remote_eu + 0x1300, 4) == 2 && ld(remote_eu + 0x1310, 4) == 2);
    /* Unknown data removes prior gear; no arbitrary names or unsupported-age equipment. */
    pose.sword = pose.shield = 255, pose.pseq++, pose.t_ms = (uint32_t)now_ms;
    pos_fn(2, bytes, coop_pos_pack(bytes, sizeof bytes, &pose), NULL);
    frame(200);
    CHECK(calls[FN_GEAR_REMOVE] == 2 && calls[FN_EQUIP] == 2);
    CHECK(!equipment_actor(&ctx, remote_eu, 0) && !equipment_actor(&ctx, remote_eu, 1));
    CHECK(!gear_id(0, COOP_SWORD_MASTER, 0) && !gear_id(0, COOP_SWORD_KOKIRI, 1));
    CHECK(!gear_id(1, COOP_SHIELD_MIRROR, 0) && !gear_id(1, COOP_SHIELD_DEKU, 1));
    /* A recycled native holder cannot disclose the replacement's equipment. */
    give_piece(own_eu, 0, "Weapon_Sword_Master", 1);
    st(own_eu + 0x30, 1234567, 4);
    CoopPos sample = {.flags = COOP_POS_ADULT};
    equipment_read(&ctx, link_actor, &sample);
    CHECK(!sample.sword && !(sample.hands & COOP_HAND_SWORD));
    /* Missing components: no native equipment calls, including a component with the wrong owner. */
    int count = total_calls();
    const uint64_t parts = ld(last_actor + A_PARTS, 8);
    st(parts + 0x200, 0, 8);
    equipment_play(&ctx, &P.puppet[2], &pose, now_ms);
    CHECK(total_calls() == count);
    st(parts + 0x200, own_eu, 8);
    equipment_play(&ctx, &P.puppet[2], &pose, now_ms);
    CHECK(total_calls() == count);
    st(parts + 0x200, remote_eu, 8);
    /* A stale body generation may not move, animate, equip or delete an unrelated replacement. */
    st(last_actor + A_ID, 123456, 4);
    count = total_calls(); const int read_calls = calls[FN_FRAME_GET] + calls[FN_GEAR_STATE];
    frame(16);
    CHECK(P.puppet[2].state == PP_NONE && total_calls() - count == calls[FN_FRAME_GET] + calls[FN_GEAR_STATE] - read_calls);
    /* Adult-only permission is not child consent; no age interpolation survives the transition. */
    presence = COOP_PRESENCE_ADULT_BODY;
    pose.flags = COOP_POS_IN_WORLD, pose.sword = COOP_SWORD_KOKIRI, pose.shield = COOP_SHIELD_DEKU;
    pose.pseq++, pose.t_ms = (uint32_t)now_ms;
    pos_fn(2, bytes, coop_pos_pack(bytes, sizeof bytes, &pose), NULL);
    CHECK(P.store.r[2].n == 1);
    frame(AGAIN_MS);
    CHECK(P.puppet[2].state == PP_NONE);
    /* Permission off and co-op off: no guest reads/calls, locks, sending or allocation. */
    presence = 0; frame(16);
    reads = locks_taken = 0; count = total_calls(); const int sent_before = sends, allocated = allocations;
    frame(100), hos_oot_coop_players_tick(1, 1);
    CHECK(!reads && !locks_taken && total_calls() == count && sends == sent_before && allocations == allocated);
    P.on = 0; frame(100);
    CHECK(!reads && !locks_taken && total_calls() == count);
    /* Fallback is explicitly adult, once per start, never silently a child. */
    fresh(); ini_players = 1; presence = COOP_PRESENCE_SUPPORTED; room(1); accepted = 1u << 2;
    st(link_actor + A_PARTS, 0, 8);
    hos_oot_coop_players_start();
    CHECK(!P.anim && !strcmp(P.actor_name[1], "PouchPlayer_Adult") && logged("correct age"));
    const size_t logged_before = logat;
    hos_oot_coop_players_start(); CHECK(logat == logged_before);
    pose.flags = COOP_POS_IN_WORLD | COOP_POS_ADULT, pose.pseq++, pose.t_ms = (uint32_t)now_ms;
    pos_fn(2, bytes, coop_pos_pack(bytes, sizeof bytes, &pose), NULL);
    frame(16); CHECK(!strcmp(made_name, "PouchPlayer_Adult"));
    /* Child gear uses its own family; equipment permission off removes it and stops disclosure. */
    fresh(); ini_players = 1; presence = COOP_PRESENCE_SUPPORTED; room(1); accepted = 1u << 2;
    st(link_actor + A_FILE, gtext("Player_Child"), 8);
    hos_oot_coop_players_start();
    pose.flags = COOP_POS_IN_WORLD, pose.sword = COOP_SWORD_KOKIRI, pose.shield = COOP_SHIELD_DEKU, pose.hands = 3;
    pose.pseq++, pose.t_ms = (uint32_t)now_ms;
    pos_fn(2, bytes, coop_pos_pack(bytes, sizeof bytes, &pose), NULL);
    frame(16); game_makes(); give_equipment(last_actor); frame(16); frame(16);
    CHECK(calls[FN_EQUIP] == 2 && calls[FN_HAND] == 2 && !strcmp(P.puppet[2].name, "PouchPlayer_Child"));
    presence = COOP_PRESENCE_CHILD_BODY;
    pose.pseq++, pose.t_ms = (uint32_t)now_ms;
    pos_fn(2, bytes, coop_pos_pack(bytes, sizeof bytes, &pose), NULL);
    frame(100);
    CHECK(calls[FN_GEAR_REMOVE] == 2 && coop_pos_parse(sent, sent_n, &mine) && !mine.sword && !mine.shield && !mine.hands);
    /* Local animation override works with generated packs present. */
    fresh(); assets_ready = 1, ini_players = 1;
    setenv("HOS_COOP_PLAYERS_ANIM", "off", 1);
    hos_oot_coop_players_start();
    CHECK(!P.anim && !strcmp(P.actor_name[0], "PouchPlayer_Child"));
    /* Full local display off never samples native animation or equipment. */
    fresh(); assets_ready = 1; presence = COOP_PRESENCE_SUPPORTED; room(1);
    st(link_actor + A_FILE, gtext("Player_Child"), 8);
    give_anim(link_actor, 2, nodes); give_equipment(link_actor);
    hos_oot_coop_players_start(); frame(100);
    CHECK(!P.anim && !P.gear && !total_calls() && !P.blocks);
    puts("coop players: adult/default animation, bounded gear, lasting draw state, missing components, stale generations and off paths passed");
}

static void gear_codec(void) {
    CoopPos p = {.flags = COOP_POS_IN_WORLD | COOP_POS_ADULT, .sword = COOP_SWORD_BIGGORON,
                 .shield = COOP_SHIELD_MIRROR, .hands = 3, .nslots = 4};
    CoopPos got; uint8_t bytes[COOP_MAX_MESSAGE];
    size_t n = coop_pos_pack(bytes, sizeof bytes, &p);
    CHECK(n && coop_pos_parse(bytes, n, &got) && got.sword == p.sword && got.shield == p.shield && got.hands == 3 && got.nslots == 4);
    CHECK(!coop_pos_parse(bytes, n - 1, &got) && !coop_pos_parse(bytes, n + 1, &got));
    bytes[n - 5] = 4; CHECK(!coop_pos_parse(bytes, n, &got));
    p.hands = 4; CHECK(!coop_pos_pack(bytes, sizeof bytes, &p));
    p.hands = 0, p.nslots = 5; CHECK(!coop_pos_pack(bytes, sizeof bytes, &p));
    p.nslots = 4, p.sword = 255, p.shield = 255;
    n = coop_pos_pack(bytes, sizeof bytes, &p);
    CHECK(n && coop_pos_parse(bytes, n, &got) && got.sword == 255 && !gear_id(0, got.sword, 1) && !gear_id(1, got.shield, 1));
    bytes[0] = 2; CHECK(!coop_pos_parse(bytes, n, &got));
}

static void hud_privacy(void) {
    fresh(); room(1); accepted = 1u << 2;
    setenv("HOS_COOP_PLAYERS", "1", 1);
    hos_oot_coop_players_start();
    presence = COOP_PRESENCE_SUPPORTED;
    link_at("Forest", 0, 0, 0, 0);
    they_are_at("Forest", 1, 2, 3, 0, COOP_POS_IN_WORLD);
    frame(16); game_makes(); frame(16);
    MkwVkCoopOverlay overlay;
    CHECK(hos_coop_overlay_snapshot(&overlay, 1) && overlay.nmarks == 1 && overlay.mark[0].flags == HOS_COOP_MARK_NAME);
    CHECK(hos_coop_overlay_snapshot(&overlay, 2) && (overlay.mark[0].flags & HOS_COOP_MARK_ARROW));
    reads = 0; CHECK(!hos_coop_overlay_snapshot(&overlay, 0) && !reads);
    names_permission = 0;
    CHECK(!hos_coop_overlay_snapshot(&overlay, 1));
    CHECK(hos_coop_overlay_snapshot(&overlay, 2) && overlay.mark[0].flags == HOS_COOP_MARK_ARROW);
    presence &= ~COOP_PRESENCE_ARROWS;
    CHECK(!hos_coop_overlay_snapshot(&overlay, 2));
    names_permission = 1;
    CHECK(hos_coop_overlay_snapshot(&overlay, 2) && overlay.mark[0].flags == HOS_COOP_MARK_NAME);
    CoopPos pose; uint64_t age;
    CHECK(hos_oot_coop_players_snapshot(2, &pose, &age) && age < 3000);
    accepted = 0;
    CHECK(!hos_oot_coop_players_snapshot(2, &pose, &age) && !hos_coop_overlay_snapshot(&overlay, 2));
    const unsigned taken = P.store.taken;
    they_are_at("Forest", 1, 2, 3, 0, COOP_POS_IN_WORLD);
    CHECK(P.store.taken == taken);
    accepted = 1u << 2;
    CHECK(!hos_oot_coop_players_snapshot(2, &pose, &age));
    they_are_at("Elsewhere", 1, 2, 3, 0, COOP_POS_IN_WORLD);
    CHECK(!hos_coop_overlay_snapshot(&overlay, 2));
    they_are_at("Forest", 1, 2, 3, 0, COOP_POS_IN_WORLD);
    now_ms += COOP_PLAYERS_SILENT_MS;
    CHECK(!hos_coop_overlay_snapshot(&overlay, 2));

    const uint64_t gd = ld(ld(GAMEDATA_HOLDER, 8), 8), cn = gd + 0xc8;
    const uint64_t entries = galloc(0x50), table = galloc(8 * 16);
    st(cn + CN_COUNT, 2, 4); st(cn + CN_CAP, 16, 4);
    st(cn + CN_ENTRIES, entries, 8); st(cn + CN_TABLE, table, 8);
    st(table + 8 * (0x31ab5580u % 16), 0x31ab5580u, 4);
    st(table + 8 * (0xfbe01da1u % 16), 0xfbe01da1u, 4);
    st(table + 8 * (0xfbe01da1u % 16) + 4, 1, 4);
    st(entries + 0x1c, 9, 4); st(entries + 0x28 + 0x1c, 12, 4);
    presence = COOP_PRESENCE_HEALTH; pose = (CoopPos){0}; health_read(&ctx, &pose);
    CHECK(pose.health_q == 9 && pose.max_health_q == 12);
    st(entries + 0x1c, 0, 4); pose = (CoopPos){0}; health_read(&ctx, &pose);
    CHECK(pose.health_q == 0 && pose.max_health_q == 12);
    st(entries + 0x28 + 0x1c, 161, 4); pose = (CoopPos){0}; health_read(&ctx, &pose);
    CHECK(!pose.max_health_q);
    st(cn + CN_TABLE, 0, 8); pose = (CoopPos){0}; health_read(&ctx, &pose);
    CHECK(!pose.max_health_q);
    presence = 0; reads = 0; health_read(&ctx, &pose); CHECK(!reads);
    for (int mask = 0; mask <= COOP_PRESENCE_SUPPORTED; mask++) {
        if (mask & ~COOP_PRESENCE_SUPPORTED) continue;
        presence = mask; P.show = 0;
        pose = (CoopPos){.flags=COOP_POS_IN_WORLD,.pos={1,2,3},.health_q=9,.max_health_q=12,.nslots=1,.area="Forest"};
        place_hidden(&pose);
        CHECK((pose.max_health_q != 0) == !!(mask & COOP_PRESENCE_HEALTH));
        CHECK((pose.pos[0] != 0) == !!(mask & COOP_PRESENCE_ARROWS));
        CHECK(!pose.nslots);
        CHECK((pose.area[0] != 0) == !!(mask & (COOP_PRESENCE_PLACE | COOP_PRESENCE_ARROWS | COOP_PRESENCE_BODIES)));
    }
    presence = 0; playing = 0; P.was_playing = 0;
    reads = locks_taken = 0; frame(16);
    CHECK(!reads && !locks_taken && !hos_coop_overlay_snapshot(&overlay, 2));
    puts("PASS HUD: native health unknown/zero/bounds, each privacy bit, names/off/arrows, stale/map/acceptance/ticket/off paths");
}

int main(int argc, char **argv) {
    messages();
    slow();
    clock_bounds();
    interpolation_bounds();
    if (argc < 2) { fprintf(stderr, "usage: %s <steps file>\n", argv[0]); return 2; }
    game_side(argv[1]);
    body_gear();
    gear_codec();
    hud_privacy();
    if (failures) { fprintf(stderr, "%d checks failed\n", failures); return 1; }
    printf("coop players: all checks passed\n");
    return 0;
}
