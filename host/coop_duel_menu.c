/* (longshot) All runtime entry points are here. Until a proven native receiver adapter is installed,
   Ready is a diagnostic refusal, never a synthetic collision or a success inferred from a pose. The
   game-free state machine has no frame hook at all: co-op off runs none of this per game frame. */
#include "coop_duel_menu.h"
#include "coop_duel.h"
#include "coop_profile.h"
#include "../gpu/vk/compat/longshot_env.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int hos_oot_coop_offer(CoopOffer *out);
void hos_log(const char *fmt, ...);

void hos_coop_duel_menu_view(MkwVkCoopView *out, const CoopView *v, const CoopOffer *o) {
    int mode = 0;
    if (!out || !v || !o || v->state != COOP_JOINED ||
        !coop_feature_present(&o->rules, COOP_RULE_ROOM_MODE, &mode) || mode != COOP_ROOM_PVP_SETUP ||
        !env_on("HOS_COOP_HITS_TRACE")) return;
    out->duel_diagnostic = 1;
    snprintf(out->duel_note, sizeof out->duel_note, "This build cannot start a fight: native contact and damage are not proven.");
    int hearts = 0, rounds = 0, arena = 0, age = -1, kit = 0;
    coop_feature_present(&o->rules, 31, &hearts); coop_feature_present(&o->rules, 34, &rounds);
    coop_feature_present(&o->rules, 37, &arena); coop_feature_present(&o->rules, 32, &age);
    coop_feature_present(&o->rules, 40, &kit);
    snprintf(out->duel_terms, sizeof out->duel_terms, "Arena %d; %d duel hearts; best of %d; %s kit mask %d (see signed Room rules)",
             arena, hearts, rounds, age == 0 ? "child" : age == 1 ? "adult" : "unspecified", kit);
    if (!o->accepted) {
        snprintf(out->duel_note, sizeof out->duel_note, "Accept these signed terms first. This build cannot start a fight."); return;
    }
    if (!coop_profile_active()) {
        snprintf(out->duel_note, sizeof out->duel_note, "Use a temporary save copy. This build cannot start a fight."); return;
    }
    if (v->n != 2 || v->member[0].away || v->member[1].away) {
        snprintf(out->duel_note, sizeof out->duel_note, "Exactly two present accepted players are required. This build cannot start a fight."); return;
    }
    /* File IO is explicit, local and diagnostic, on an attempted Ready only (never menu drawing).
       Preview binds the complete signed rules and participant sessions without leaking coordinates. */
    uint8_t bytes[256] = {0}, digest[32];
    memcpy(bytes, "longshot-coop/1 duel-preview", 27);
    for (unsigned i = 0; i < 4; i++) bytes[28 + i] = (uint8_t)(o->rules.epoch >> (8 * i));
    if (o->rules.features.count > COOP_FEATURE_MAX) return;
    memcpy(bytes + 32, o->rules.mode, COOP_CAT_N); bytes[39] = o->rules.features.count;
    memcpy(bytes + 40, o->rules.features.bytes, 3u * o->rules.features.count);
    for (unsigned p = 0; p < 2; p++) for (unsigned i = 0; i < 8; i++) bytes[232 + p * 8 + i] = (uint8_t)(v->member[p].session >> (8 * i));
    coop_blake2b(digest, sizeof digest, o->maker_key, strlen(o->maker_key), bytes, sizeof bytes);
    for (unsigned i = 0; i < 32; i++) snprintf(out->duel_digest + 2 * i, 3, "%02x", digest[i]);
    snprintf(out->duel_score, sizeof out->duel_score, "Not ready | score 0-0 | no duel hearts have changed");
}

int hos_coop_duel_menu_ready(const char *digest, uint32_t epoch) {
    if (!env_on("HOS_COOP_HITS_TRACE")) return 0;
    CoopView view; CoopOffer offer = {0}; MkwVkCoopView menu = {0};
    coop_view_get(&view); hos_oot_coop_offer(&offer);
    hos_coop_duel_menu_view(&menu, &view, &offer);
    if (!menu.duel_diagnostic || !digest || !*digest || epoch != offer.rules.epoch ||
        strcmp(digest, menu.duel_digest)) {
        hos_log("coop: duel: Ready refused: terms, consent or participant sessions changed"); return 0;
    }
    const char *path = getenv("HOS_COOP_ARENA_FILE");
    CoopArena arena;
    if (!path || !coop_arena_load(path, &arena)) {
        hos_log("coop: duel: Ready refused: no valid local surveyed arena descriptor; no fight started"); return 0;
    }
    unsigned first = view.member[0].id < view.member[1].id ? 0 : 1;
    uint8_t members[2] = {(uint8_t)view.member[first].id, (uint8_t)view.member[1 - first].id};
    uint64_t sessions[2] = {view.member[first].session, view.member[1 - first].session}, maker = 0;
    CoopDuelTerms terms;
    if (!coop_sync_key_parse(offer.maker_key, &maker) ||
        !coop_duel_terms(&terms, &offer.rules, &arena, offer.rules.epoch, maker, members, sessions)) {
        hos_log("coop: duel: Ready refused: unsupported sword-kit, arena or duel terms; no fight started"); return 0;
    }
    hos_log("coop: duel: Ready refused: native sword contact, damage, shield and arena safety are not proven; no fight started");
    return 0;
}
