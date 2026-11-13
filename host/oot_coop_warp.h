/* Local confirmed Go to friend. Native effects run only in Link's verified movement hook. */
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "a64.h"

typedef struct {
    char map[80];
    float pos[3];
    int event, loading, child, safe, travelling;
} OotCoopWarpLocal;

int hos_oot_coop_warp_ask(int member, const char *key);
void hos_oot_coop_warp_note(char *out, size_t cap);
void hos_oot_coop_warp_update(Ctx *c, uint64_t actor, uint64_t components);
void hos_oot_coop_warp_present(Ctx *c);
/* Native adapter in hos_main.c. No network text is a native event, entrance or actor identifier. */
int hos_oot_coop_warp_destination(const char *map, int child);
int hos_oot_coop_warp_local(Ctx *c, uint64_t actor, uint64_t components, OotCoopWarpLocal *out);
int hos_oot_coop_warp_event(Ctx *c);
int hos_oot_coop_warp_travel(Ctx *c, uint64_t actor, int destination);
int hos_oot_coop_warp_place(Ctx *c, uint64_t components, const float pos[3]);
