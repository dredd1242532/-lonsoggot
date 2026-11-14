/* Reuse the recording actor/guest-memory fixture; no game data or game process. */
#define main existing_lifetime_main
#include "oot_coop_players_test.c"
#undef main

static struct {
    uint64_t mgr, clock_entry, loading_entry, transition_entry[4], clock_at, area_generation;
    uint32_t clock;
    int bound, loaded, looked, titles, player, gate, scene, on, playing, presence_play, probe;
} native_gate_state;
static uint64_t recomp_load8(Ctx *c, uint64_t address) { (void)c; return ld(address, 1); }
#define G native_gate_state
#define TRACE_ON 1
#define CN_COUNT GRACE_CN_COUNT
#define CN_ENTRIES GRACE_CN_ENTRIES
#define CN_TABLE GRACE_CN_TABLE
#define CN_CAP GRACE_CN_CAP
#include "gate-body.h"
#undef CN_COUNT
#undef CN_ENTRIES
#undef CN_TABLE
#undef CN_CAP
#undef TRACE_ON
#undef G

static uint64_t gd, play_entry, load_entry, transitions[4], player_holder;
static uint64_t add_flags(unsigned base, unsigned stride, const uint32_t *hashes, unsigned count) {
    const uint64_t entries = galloc((uint64_t)stride * count), table = galloc(32 * 8);
    st(gd + base + GRACE_CN_COUNT, count, 4); st(gd + base + GRACE_CN_ENTRIES, entries, 8);
    st(gd + base + GRACE_CN_TABLE, table, 8); st(gd + base + GRACE_CN_CAP, 32, 4);
    for (unsigned i = 0; i < count; i++) {
        unsigned at = hashes[i] % 32;
        while (ld(table + 8u * at, 4)) at = (at + 1) % 32;
        st(table + 8u * at, hashes[i], 4); st(table + 8u * at + 4, i, 4);
    }
    return entries;
}
static void init_gate_memory(void) {
    gd = ld(ld(GAMEDATA_HOLDER, 8), 8);
    const uint32_t clocks[] = {F_PLAY_CLOCK};
    const uint32_t bools[] = {F_LOADING, transition_flags[0], transition_flags[1], transition_flags[2], transition_flags[3]};
    play_entry = add_flags(C_UINT, INT_SIZE, clocks, 1);
    load_entry = add_flags(C_BOOL, BOOL_SIZE, bools, 5);
    for (int i = 0; i < 4; i++) transitions[i] = load_entry + BOOL_SIZE * (unsigned)(i + 1);
    player_holder = galloc(0x488);
    st(PLAYER_PTR, player_holder, 8); st(player_holder + 0x480, link_actor, 8);
    st(link_actor + 0x20, gtext("Player"), 8);
}
static int native_gate(void) {
    const int open = gate(&ctx, gd);
    owner_area = native_gate_state.area_generation;
    return open;
}
static uint64_t setup(int shown) {
    fresh(); room(1); ini_players = 1; accepted = 1u << 2;
    link_at("Forest", 12, 5, 20, 0);
    st(player_holder + 0x480, link_actor, 8); st(link_actor + 0x20, gtext("Player"), 8);
    st(load_entry + B_CURRENT, 0, 1);
    for (int i = 0; i < 4; i++) st(transitions[i] + B_CURRENT, 0, 1);
    memset(&native_gate_state, 0, sizeof native_gate_state);
    native_gate_state.mgr = gd; native_gate_state.on = native_gate_state.loaded = native_gate_state.looked = 1;
    native_gate_state.bound = 1; native_gate_state.titles = title_starts;
    native_gate_state.clock = 600; native_gate_state.clock_at = now_ms;
    st(play_entry + I_CURRENT, 601, 4);
    CHECK(native_gate());
    native_playing = &native_gate_state.playing;
    native_presence_play = &native_gate_state.presence_play;
    hos_oot_coop_players_start();
    frame(0);
    they_are_at("Forest", 13, 5, 25, 0, COOP_POS_IN_WORLD | COOP_POS_LOADED);
    if (shown) {
        frame(0); CHECK(P.puppet[2].state == PP_ASKED);
        game_makes(); frame(1); CHECK(P.puppet[2].state == PP_SHOWN && P.made == 1);
        they_are_at("Forest", 13, 5, 25, 0, COOP_POS_IN_WORLD | COOP_POS_LOADED);
        frame(0);
    }
    st(play_entry + I_CURRENT, native_gate_state.clock + 1, 4); CHECK(native_gate());
    log_clear();
    return now_ms;
}
static void live_time(uint64_t ms) {
    while (ms) {
        const uint64_t step = ms > 1000 ? 1000 : ms;
        now_ms += step; ms -= step;
        st(play_entry + I_CURRENT, native_gate_state.clock + 1, 4);
        CHECK(native_gate());
    }
}
static void new_peer_pose(void) {
    they_are_at("Forest", 99, 5, 25, 0, COOP_POS_IN_WORLD | COOP_POS_LOADED);
}
static void stalled(const char *kind, unsigned seconds) {
    const uint64_t start = setup(1), actor = P.puppet[2].actor, generation = gameplay.generation;
    const int made = calls[FN_CREATE], placed = calls[FN_MTX];
    const int clock_stall = !strcmp(kind, "clock"), pose_stall = !strcmp(kind, "pose");
    if (clock_stall) {
        now_ms = start + (uint64_t)seconds * 1000;
        CHECK(!native_gate()); /* progress/travel stay closed at their original 2.5-second limit */
        CHECK(!hos_oot_coop_playing());
        new_peer_pose();
    } else live_time((uint64_t)seconds * 1000);
    const int sent_before = sends;
    hos_oot_coop_players_tick(1, native_gate_state.gate);
    CHECK(sends == sent_before); /* neither a clock gap nor missing own poses announces a false map exit */
    frame(0);
    if (seconds < 10) {
        CHECK(P.puppet[2].state == PP_SHOWN && P.puppet[2].actor == actor && !calls[FN_DELETE]);
        CHECK(calls[FN_CREATE] == made && calls[FN_MTX] == placed && ldf(actor + A_POS) == 13);
        CHECK(gameplay.generation == generation && !logged("is hidden"));
    } else {
        CHECK(P.puppet[2].state == PP_NONE && calls[FN_DELETE] == 1 && P.puppet[2].again_ms <= now_ms);
        now_ms++;
        st(play_entry + I_CURRENT, native_gate_state.clock + 1, 4); CHECK(native_gate());
        new_peer_pose(); frame(0);
        CHECK(calls[FN_CREATE] == made + 1 && P.puppet[2].state == PP_ASKED);
        if (P.puppet[2].state == PP_ASKED) { game_makes(); frame(1); CHECK(P.puppet[2].state == PP_SHOWN); }
    }
    printf("%s grace %s %us: %s%s\n", failures ? "FAIL" : "PASS", kind, seconds,
           seconds < 10 ? "existing body held without creation/placement" : "expired body immediately re-requested on recovery",
           pose_stall ? "; unavailable sender pose uses the same receiver deadline" : "");
}
static void immediate(const char *action, unsigned held_seconds) {
    setup(1);
    if (held_seconds) {
        now_ms += (uint64_t)held_seconds * 1000;
        CHECK(!native_gate()); new_peer_pose(); frame(0);
        CHECK(P.puppet[2].state == PP_SHOWN && !calls[FN_DELETE]);
    }
    if (!strcmp(action, "loading")) st(load_entry + B_CURRENT, 1, 1);
    else if (!strncmp(action, "transition", 10)) st(transitions[action[10] - '0'] + B_CURRENT, 1, 1);
    else if (!strcmp(action, "save")) st(play_entry + I_CURRENT, 9000, 4);
    else if (!strcmp(action, "title")) hos_oot_coop_rom_open("rom:/Event/EventFlow/TitleEvent.bfevfl");
    else if (!strcmp(action, "unloaded")) native_gate_state.loaded = 0;
    else if (!strcmp(action, "player")) st(player_holder + 0x480, 0, 8);
    else if (!strcmp(action, "origin")) link_at("Forest", 0, 5, 0, 0);
    else if (!strcmp(action, "leave")) { view.state = COOP_LEFT; view.n = 0; view.version++; view_fn(&view, NULL); }
    else if (!strcmp(action, "member")) { view.n = 1; view.version++; view_fn(&view, NULL); }
    else if (!strcmp(action, "replacement")) { view.member[1].session++; view.version++; view_fn(&view, NULL); }
    else if (!strcmp(action, "rules")) { owner_rules++; owner_context(); }
    else if (!strcmp(action, "permission")) presence = 0;
    else if (!strcmp(action, "consent")) { accepted = 0; owner_context(); }
    else if (!strcmp(action, "copy") || !strcmp(action, "return")) { owner_save++; presence = 0; owner_context(); }
    else if (!strcmp(action, "remote-map")) they_are_at("Field", 13, 5, 25, 0, COOP_POS_IN_WORLD | COOP_POS_LOADED);
    else if (!strcmp(action, "remote-load")) they_are_at("Forest", 0, 0, 0, 0, COOP_POS_LOADED);
    else CHECK(0 && "unknown immediate case");
    native_gate(); frame(0);
    CHECK(P.puppet[2].state == PP_NONE && calls[FN_DELETE] == 1);
    CHECK(P.puppet[2].again_ms == now_ms + AGAIN_MS); /* meaningful removal was not relabelled a timeout */
    const int created = calls[FN_CREATE];
    frame(0); CHECK(calls[FN_CREATE] == created);
    printf("%s immediate %s during %us stall: no grace, no new body\n", failures ? "FAIL" : "PASS", action, held_seconds);
}
static void no_new(int clock_stall) {
    const uint64_t start = setup(0);
    if (clock_stall) {
        now_ms = start + 9000; CHECK(!native_gate()); new_peer_pose();
    } else live_time(9000);
    frame(0);
    CHECK(!calls[FN_CREATE] && !allocations && P.puppet[2].state == PP_NONE);
    printf("%s retain-only %s: never creates an unshown body\n", failures ? "FAIL" : "PASS", clock_stall ? "clock" : "peer");
}
static void failed_creation(void) {
    setup(0); refuse_create = 1; frame(0);
    CHECK(calls[FN_CREATE] == 1 && P.failed == 1);
    const uint64_t deadline = P.puppet[2].again_ms;
    CHECK(deadline == now_ms + AGAIN_MS);
    refuse_create = 0; live_time(AGAIN_MS - 1); new_peer_pose(); frame(0);
    CHECK(calls[FN_CREATE] == 1);
    live_time(1); new_peer_pose(); frame(0);
    CHECK(calls[FN_CREATE] == 2 && P.puppet[2].state == PP_ASKED);
    printf("%s failed creation: original two-second retry bound retained\n", failures ? "FAIL" : "PASS");
}
static void clock_jumps(void) {
    const unsigned deltas[] = {0, 1, 3, 5, 6, 9, 10};
    for (unsigned i = 0; i < sizeof deltas / sizeof deltas[0]; i++) {
        setup(1); now_ms += 9000;
        st(play_entry + I_CURRENT, native_gate_state.clock + deltas[i], 4);
        native_gate(); new_peer_pose(); frame(0);
        if (deltas[i] <= 5) CHECK(native_gate_state.bound && P.puppet[2].state == PP_SHOWN && !logged("another save was loaded"));
        else CHECK(!native_gate_state.bound && P.puppet[2].state == PP_NONE && logged("another save was loaded"));
    }
    printf("%s clock jumps after 9s: 0/1/3/5 retain; 6/9/10 still fail closed as possible save loads\n", failures ? "FAIL" : "PASS");
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    world_init(); init_gate_memory();
    ctx.x[31] = 0x10000000ull + (3u << 20);
    CHECK(COOP_PLAYERS_GRACE_MS == 10000);
    const char *arg = argv[1];
    if (!strncmp(arg, "immediate-", 10)) {
        char action[32]; snprintf(action, sizeof action, "%s", arg + 10);
        char *dash = strrchr(action, '-'); if (!dash) return 2;
        *dash++ = 0; immediate(action, (unsigned)atoi(dash));
    } else if (!strcmp(arg, "no-new-clock")) no_new(1);
    else if (!strcmp(arg, "no-new-peer")) no_new(0);
    else if (!strcmp(arg, "failed-create")) failed_creation();
    else if (!strcmp(arg, "clock-jumps")) clock_jumps();
    else {
        char kind[16]; unsigned seconds;
        if (sscanf(arg, "%15[^-]-%u", kind, &seconds) != 2) return 2;
        stalled(kind, seconds);
    }
    for (int i = 0; i < 2; i++) free(regions[i].mem);
    return failures ? 1 : 0;
}
