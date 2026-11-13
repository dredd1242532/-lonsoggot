/* (longshot) Diagnostic-only presentation. A native observer is deliberately not installed. */
#ifndef COOP_DUEL_MENU_H
#define COOP_DUEL_MENU_H
#include "coop.h"
#include "coop_state.h"
#include "../gpu/vk/vk_api.h"
void hos_coop_duel_menu_view(MkwVkCoopView *out, const CoopView *view, const CoopOffer *offer);
int hos_coop_duel_menu_ready(const char *digest, uint32_t epoch);
#endif
