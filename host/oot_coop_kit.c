/* Sword-practice family for the supported game. No remote name, address or path
   enters this adapter. Gives and worn-item selection use the native pouch paths,
   not flag writes or cosmetic actor attachment, followed by lasting-slot readback. */
#include "hos.h"
#include "oot_coop_kit.h"
#include "oot_inventory.h"
#include "coop_profile.h"
#include "../gpu/vk/oot_cheat_items.h"
#include <stdio.h>
#include <string.h>

int hos_fs_save_root(char *out, size_t cap);

typedef struct { const char *name; unsigned age, slot; } KitItem;
static const KitItem kit_items[OOT_KIT_ITEMS] = {
    {OOT_KOKIRI_SWORD, 0, 0}, {OOT_DEKU_SHIELD, 0, 4},
    {OOT_MASTER_SWORD, 1, 0}, {OOT_HYLIAN_SHIELD, 1, 4},
};

static int kit_range(uint64_t p, size_t n) { return p && hos_guest_range(p, n) != NULL; }
static uint64_t kit_ptr(Ctx *c, uint64_t p) { return kit_range(p, 8) ? recomp_load64(c, p) : 0; }
static int kit_text(Ctx *c, uint64_t p, const char *text) {
    (void)c;
    return kit_range(p, strlen(text) + 1) && !memcmp(GP(p), text, strlen(text) + 1);
}

/* The mount, not a profile label, is authority. Prefix lookalikes, parent
   components, an unmounted save and the normal root all refuse. */
static int kit_mount(void) {
    char mounted[COOP_PROFILE_PATH] = {0};
    if (!coop_profile_active() || !hos_fs_save_root(mounted, sizeof mounted)) return 0;
    const char *root = coop_profile_root();
    size_t i = 0;
    if (!root || !root[0]) return 0;
    for (; root[i]; i++) {
        char a = root[i] == '\\' ? '/' : root[i], b = mounted[i] == '\\' ? '/' : mounted[i];
        if (!b || a != b) return 0;
    }
    const char *rest = mounted + i;
    if ((*rest != '/' && *rest != '\\') || strncmp(rest + 1, "save", 4) ||
        (rest[5] != '/' && rest[5] != '\\') || !rest[6]) return 0;
    for (const char *p = rest + 6; *p; p++)
        if (*p == '.' || *p == ':' || *p == '/' || *p == '\\') return 0;
    return coop_profile_path_safe(mounted);
}

static int kit_refuse(OotCoopKitRun *r, const char *why) {
    r->state = OOT_KIT_REFUSED;
    for (unsigned i = 0; i < OOT_KIT_ITEMS; i++) if (r->kit.bits & (1u << i)) {
        r->result[i] = OOT_KIT_ENTRY_REFUSED;
        hos_log("coop: kit: %s refused: %s", kit_items[i].name, why);
    }
    return 0;
}

/* Native flag requests are queued. Reserve headroom and wait for the prior give
   to drain instead of issuing a kit-sized burst or treating queued flags as live. */
static int kit_queue_quiet(Ctx *c) {
    const uint64_t mgr = kit_ptr(c, kit_ptr(c, 0x84c9e180ULL));
    if (!kit_range(mgr, 0xd80)) return 0;
    static const unsigned container[] = {0x008, 0x0c8, 0x608}; /* Bool, Int, String64 equipment selection */
    for (unsigned i = 0; i < sizeof container / sizeof *container; i++) {
        const uint64_t k = mgr + container[i];
        const uint32_t cap = (uint32_t)recomp_load32(c, k + 0x28);
        const uint32_t n = (uint32_t)recomp_load32(c, k + 0x38) & 0xfffff;
        if (cap < 64 || cap > 1048576 || n || !kit_range(kit_ptr(c, k + 0x30), (size_t)cap * 12)) return 0;
    }
    return 1;
}

/* Native capacity query, the same manager/name wrapper as Pouch_Count. */
static int kit_capacity(Ctx *c, const char *name) {
    const uint64_t mgr = kit_ptr(c, 0x84e98e28ULL);
    A64Fn fn = a64_lookup(0x805b5cf0ULL);
    if (!kit_range(mgr, 0x3bd0) || !fn) return -1;
    const Ctx saved = *c;
    const uint64_t sp = (c->x[31] - 0x80) & ~0xfULL;
    if (!kit_range(sp, 0x80) || strlen(name) >= 0x60) return -1;
    memcpy(GP(sp + 0x10), name, strlen(name) + 1);
    recomp_store64_sp(c, sp, sp + 0x10);
    c->x[31] = sp;
    c->x[0] = mgr, c->x[1] = sp, c->x[2] = 0, c->x[3] = 0, c->x[30] = 0;
    fn(c);
    const int result = (int)(int32_t)c->x[0];
    *c = saved;
    return result;
}

static uint64_t kit_player(Ctx *c, unsigned age) {
    const uint64_t actor = kit_ptr(c, kit_ptr(c, 0x84e571d0ULL) + 0x480);
    if (!kit_range(actor, 0x400) || recomp_load32(c, actor + 0x2c) != 4 ||
        !kit_text(c, kit_ptr(c, actor + 0x260), age ? "Player_Adult" : "Player_Child")) return 0;
    return actor;
}
static uint64_t kit_equipment(Ctx *c, uint64_t actor) {
    const uint64_t unit = kit_ptr(c, kit_ptr(c, actor + 0x270) + 0x200);
    return kit_range(unit, 0x1400) ? unit : 0;
}
static int kit_equipped(Ctx *c, uint64_t unit, const KitItem *item) {
    const uint64_t link = unit + 0x20 + 0x18 * item->slot, holder = kit_ptr(c, link + 8);
    if (!kit_range(holder, 0x50) || recomp_load32(c, link + 16) == UINT32_MAX ||
        recomp_load32(c, link + 16) != recomp_load32(c, holder + 0x48)) return 0;
    const uint64_t piece = kit_ptr(c, holder + 0x40);
    return kit_range(piece, 0x270) && recomp_load32(c, piece + 0x2c) == 4 &&
           kit_text(c, kit_ptr(c, piece + 0x260), item->name);
}
/* Pouch_SelectEquipmentByActorName, also called by the native give routine:
   manager, &name, equipment-state policy 3, no forced UI animation. Unlike the
   cosmetic EquipmentUser request it updates the pouch's selected equipment. */
static int kit_equip(Ctx *c, const KitItem *item) {
    A64Fn fn = a64_lookup(0x80a9acd0ULL);
    const uint64_t mgr = kit_ptr(c, 0x84e98e28ULL), sp = (c->x[31] - 0x80) & ~0xfULL;
    if (!fn || !kit_range(mgr, 0x3bd0) || !kit_range(sp, 0x80)) return -1;
    const Ctx saved = *c;
    memcpy(GP(sp + 0x10), item->name, strlen(item->name) + 1);
    recomp_store64_sp(c, sp, sp + 0x10);
    c->x[31] = sp;
    c->x[0] = mgr, c->x[1] = sp, c->x[2] = 3, c->x[3] = 0, c->x[30] = 0;
    fn(c);
    const int accepted = (int)(c->x[0] & 1);
    *c = saved;
    return accepted; /* zero may be an unchanged selection; neither answer proves a live slot */
}

int oot_coop_kit_step(Ctx *c, OotCoopKitRun *r, const OotCoopKitWork *w) {
    if (!w || !w->offer || !w->epoch || !w->generation || !w->kit.version) return 0;
    if (r->state && (r->offer != w->offer || r->epoch != w->epoch || r->generation != w->generation ||
                    memcmp(&r->kit, &w->kit, sizeof r->kit))) {
        if (r->state != OOT_KIT_CANCELLED) hos_log("coop: kit: setup cancelled; restart a temporary baseline for another kit");
        r->state = OOT_KIT_CANCELLED;
        return 0;
    }
    if (r->state == OOT_KIT_CANCELLED || r->state == OOT_KIT_REFUSED) return 0;
    if (!r->state) {
        r->offer = w->offer, r->epoch = w->epoch, r->generation = w->generation, r->kit = w->kit;
        r->state = OOT_KIT_APPLYING;
    }
    if (!kit_mount()) return kit_refuse(r, "the mounted save is not the active temporary copy");
    if (strcmp(hos_main_build_id, "e0f3ccfaf50065ae4ea01eb5aeb13b1a45da3348")) return kit_refuse(r, "unsupported game build");
    if (r->kit.version != OOT_KIT_VERSION || r->kit.age > 1 || r->kit.bits != (r->kit.age ? 12 : 3))
        return kit_refuse(r, "unknown or incompatible kit");
    const uint64_t events = kit_ptr(c, kit_ptr(c, 0x84c9e090ULL));
    if (!kit_range(events, 112) || recomp_load64(c, events + 104)) return 0;
    const uint64_t actor = kit_player(c, r->kit.age), unit = actor ? kit_equipment(c, actor) : 0;
    if (!actor) return kit_refuse(r, "the loaded player has the wrong age or is unavailable");
    if (!unit || !a64_lookup(0x828d4a70ULL) || !a64_lookup(0x805b63e0ULL) || !a64_lookup(0x80a9acd0ULL))
        return kit_refuse(r, "native item or equipment support is unavailable");
    const unsigned first = r->kit.age ? OOT_KIT_MASTER : OOT_KIT_KOKIRI;
    /* Validate both entries before giving either one. No partial unknown kit. */
    int counts[2];
    for (unsigned n = 0; n < 2; n++) {
        const KitItem *item = &kit_items[first + n];
        counts[n] = oot_pouch_count(c, item->name);
        if (item->age != r->kit.age || counts[n] < 0 || counts[n] > 1 || kit_capacity(c, item->name) < 1)
            return kit_refuse(r, "native ownership or capacity cannot support this item");
    }
    if (r->state == OOT_KIT_READY) {
        for (unsigned n = 0; n < 2; n++)
            if (counts[n] != 1 || !kit_equipped(c, unit, &kit_items[first + n])) return kit_refuse(r, "kit changed after setup");
        return 1;
    }
    if (++r->waited > OOT_KIT_WAIT_FRAMES) return kit_refuse(r, "native setup did not complete; no give is retried");
    if (!kit_queue_quiet(c)) return 0;
    for (unsigned n = 0; n < 2; n++) {
        const unsigned i = first + n;
        const KitItem *item = &kit_items[i];
        if (!counts[n]) {
            if (r->gave[i]) return 0;
            r->gave[i] = 1; /* an uncertain call never becomes a second give */
            if (oot_pouch_change(c, item->name, 1) != 2) return kit_refuse(r, "the native give refused");
            r->result[i] = OOT_KIT_ENTRY_GIVEN;
            hos_log("coop: kit: %s given through native pouch; waiting for ownership", item->name);
            return 0;
        }
        if (!r->result[i]) {
            r->result[i] = OOT_KIT_ENTRY_OWNED;
            hos_log("coop: kit: %s already owned; no duplicate give", item->name);
        }
        if (!(r->step & (1 << i))) {
            r->step |= 1 << i;
            const int selected = kit_equip(c, item);
            if (selected < 0) return kit_refuse(r, "native equipment selection unavailable");
            hos_log("coop: kit: %s pouch selection returned %d; waiting for live slot", item->name, selected);
            return 0; /* even an already worn piece must agree with the pouch's selected equipment */
        }
        if (!kit_equipped(c, unit, item)) return 0;
        if (r->result[i] != OOT_KIT_ENTRY_EQUIPPED) {
            r->result[i] = OOT_KIT_ENTRY_EQUIPPED;
            hos_log("coop: kit: %s usable: owned, native capacity, age and live equipment slot verified", item->name);
        }
    }
    r->state = OOT_KIT_READY;
    hos_log("coop: kit: sword practice ready (offer %08x, epoch %u); not a duel permission", r->offer, r->epoch);
    return 1;
}
