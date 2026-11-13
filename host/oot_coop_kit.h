/* Temporary, receiver-owned kit setup. Semantic bits are not debug-menu indexes.
   Call the native adapter only through the gameplay owner on the game thread. */
#pragma once
#include "coop_proto.h"
#include "coop_state.h"
#include <string.h>

enum { OOT_KIT_VERSION = 1, OOT_KIT_ITEMS = 4, OOT_KIT_WAIT_FRAMES = 300 };
enum { OOT_KIT_KOKIRI, OOT_KIT_DEKU, OOT_KIT_MASTER, OOT_KIT_HYLIAN };
enum { OOT_KIT_OFF, OOT_KIT_VALID, OOT_KIT_BAD_VERSION = -1, OOT_KIT_BAD_AGE = -2,
       OOT_KIT_UNKNOWN = -3, OOT_KIT_INCOMPATIBLE = -4, OOT_KIT_UNSUPPORTED = -5 };
typedef struct { uint16_t bits; uint8_t age, version; } OotCoopKit;

/* This family's complete dependency table: one sword and shield, no stock or
   upgrade dependency. Capacity must still be resolved from the native pouch. */
static inline int oot_coop_kit_parse(const CoopRules *rules, OotCoopKit *out) {
    OotCoopKit kit = {0};
    int version = 0, age = -1, value = 0, requested = 0;
    if (!rules || !out || rules->features.count > COOP_FEATURE_MAX) return OOT_KIT_UNKNOWN;
    if (rules->features.count && !coop_feature_present(rules, rules->features.bytes[0], NULL)) return OOT_KIT_UNKNOWN;
    for (int id = 40; id <= 47; id++) {
        if (!coop_feature_present(rules, id, &value)) continue;
        if (value < 0 || value > 32767 || (id == 40 ? (value & ~15) : value)) return OOT_KIT_UNKNOWN;
        if (id == 40) kit.bits = (uint16_t)value;
        requested |= value;
    }
    for (int id = 84; id <= 96; id++)
        if (coop_feature_present(rules, id, &value) && value) return OOT_KIT_UNSUPPORTED;
    if (coop_feature_present(rules, 33, &value) && value) return OOT_KIT_UNSUPPORTED;
    if (!coop_feature_present(rules, 83, &version) || !version) {
        if (requested) return OOT_KIT_BAD_VERSION;
        *out = kit;
        return OOT_KIT_OFF;
    }
    if (version != OOT_KIT_VERSION) return OOT_KIT_BAD_VERSION;
    if (!coop_feature_present(rules, 32, &age) || (age != 0 && age != 1)) return OOT_KIT_BAD_AGE;
    if (kit.bits != (age ? 12 : 3)) return OOT_KIT_INCOMPATIBLE;
    if (coop_feature_present(rules, 98, &value) && value != 0 && value != 1) return OOT_KIT_UNSUPPORTED;
    kit.age = (uint8_t)age, kit.version = (uint8_t)version;
    *out = kit;
    return OOT_KIT_VALID;
}

/* State is retained after revocation: accepting again never repeats a give.
   A new setup requires restarting a temporary baseline, not a refill operation. */
enum { OOT_KIT_IDLE, OOT_KIT_APPLYING, OOT_KIT_READY, OOT_KIT_REFUSED, OOT_KIT_CANCELLED };
enum { OOT_KIT_ENTRY_NONE, OOT_KIT_ENTRY_OWNED, OOT_KIT_ENTRY_GIVEN,
       OOT_KIT_ENTRY_EQUIPPED, OOT_KIT_ENTRY_REFUSED };
typedef struct {
    uint32_t offer, epoch;
    uint64_t generation;
    OotCoopKit kit;
    int state, step, waited, result[OOT_KIT_ITEMS], gave[OOT_KIT_ITEMS];
} OotCoopKitRun;
typedef struct {
    OotCoopKit kit;
    uint32_t offer, epoch;
    uint64_t generation;
} OotCoopKitWork;
