/* The active event's authored permission only: the game's controller still handles input, confirmation and completion. */
#include "hos.h"
#include "../gpu/vk/vk_api.h"
#include <stdatomic.h>
#include <string.h>

int rx_option(int id, int otherwise);

/* shortcut: only the two reviewed opening timelines; expand after skip and save/reload validation. */
static const char *const timelines[] = {"Dm_Kokiri_0000", "Dm_Kokiri_0030"};
static atomic_uint logged;

static int readable(uint64_t address, uint64_t size) {
    return address && hos_guest_range(address, size) && hos_mem_region(address, size);
}

static int timeline_index(Ctx *c, uint64_t text) {
    char name[sizeof "Dm_Kokiri_0000"];
    if (!readable(text, sizeof name)) return -1;
    for (size_t i = 0; i < sizeof name; ++i) name[i] = (char)recomp_load8(c, text + i);
    for (unsigned i = 0; i < sizeof timelines / sizeof timelines[0]; ++i)
        if (!memcmp(name, timelines[i], sizeof name)) return (int)i;
    return -1;
}

void hos_oot_cutscene_skip_begin(Ctx *c) {
    if (!c || !readable(c->x[1], 8)) return;
    const int index = timeline_index(c, recomp_load64(c, c->x[1]));
    /* The normal timeline-start hook resets this diagnostic even with Skip opening off. */
    if (index >= 0) atomic_fetch_and_explicit(&logged, ~(1u << index), memory_order_relaxed);
}

uint64_t hos_oot_cutscene_skip_allowed(Ctx *c, uint64_t player, uint64_t native_allowed) {
    if (native_allowed || rx_option(HOS_OPT_ALLOW_CUTSCENE_SKIP, 0) <= 0 || !c || !readable(player, 24))
        return native_allowed;
    const uint64_t resource = recomp_load64(c, player + 16);
    if (!readable(resource, 16)) return native_allowed;
    /* The supported build's timeline lookup reads the resource's own name at +8. */
    const int index = timeline_index(c, recomp_load64(c, resource + 8));
    if (index < 0) return native_allowed;
    if (!(atomic_fetch_or_explicit(&logged, 1u << index, memory_order_relaxed) & (1u << index)))
        hos_log("cutscene skip: override allowed for timeline %s (player %#llx, resource %#llx)", timelines[index],
                (unsigned long long)player, (unsigned long long)resource);
    return 1;
}
