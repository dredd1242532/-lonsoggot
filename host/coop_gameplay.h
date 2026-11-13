// One route-A gameplay owner. Callers serialize publication, ticketing and dispatch with their existing game lock.
// This is an in-process capability boundary for reviewed adapters, not a sandbox or a wire ABI.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

enum { COOP_GAMEPLAY_VERSION = 2, COOP_GAMEPLAY_MEMBERS = 8, COOP_GAMEPLAY_WRITERS = 5 };
enum { COOP_GAMEPLAY_PROGRESS, COOP_GAMEPLAY_BODIES, COOP_GAMEPLAY_WORLD, COOP_GAMEPLAY_CLOCK, COOP_GAMEPLAY_TRAVEL };
enum { COOP_GAMEPLAY_PRESENTATION = 1, COOP_GAMEPLAY_TEMPORARY = 2, COOP_GAMEPLAY_DURABLE = 4,
       COOP_GAMEPLAY_CAPS = 7 };
typedef struct {
    uint32_t version, size;
    uint32_t capabilities, offer, rules_epoch, reserved;
    uint64_t room_session, save_generation, area_generation;
    uint64_t accepted_session[COOP_GAMEPLAY_MEMBERS + 1];
} CoopGameplayContext;
typedef int (*CoopGameplayWrite)(void *game, const void *payload);
typedef struct {
    uint32_t version, size, slot, capability, payload_size;
    CoopGameplayWrite write;
} CoopGameplayWriter;
typedef struct {
    CoopGameplayContext context;
    CoopGameplayWriter writer[COOP_GAMEPLAY_WRITERS];
    uint64_t generation, travel_generation;
} CoopGameplay;
typedef struct {
    uint32_t version, size, slot, member;
    uint64_t generation, session;
    const CoopGameplay *owner;
} CoopGameplayTicket;

static inline int coop_gameplay_register(CoopGameplay *owner, const CoopGameplayWriter *writer) {
    if (!owner || !writer || writer->version != COOP_GAMEPLAY_VERSION || writer->size != sizeof *writer ||
        writer->slot >= COOP_GAMEPLAY_WRITERS || !writer->write || !writer->payload_size ||
        (writer->capability != COOP_GAMEPLAY_PRESENTATION && writer->capability != COOP_GAMEPLAY_TEMPORARY &&
         writer->capability != COOP_GAMEPLAY_DURABLE) || owner->writer[writer->slot].write) return 0;
    owner->writer[writer->slot] = *writer;
    return 1;
}

static inline void coop_gameplay_publish(CoopGameplay *owner, const CoopGameplayContext *context) {
    CoopGameplayContext closed = {0};
    if (!context || context->version != COOP_GAMEPLAY_VERSION || context->size != sizeof *context ||
        context->reserved || (context->capabilities & ~COOP_GAMEPLAY_CAPS) || !context->room_session ||
        !context->save_generation || !context->area_generation || !context->offer || !context->rules_epoch) context = &closed;
    if (memcmp(&owner->context, context, sizeof *context)) {
        // A locally confirmed journey crosses one area boundary. Only its ticket survives area-only changes;
        // consent, capabilities, save, room and accepted sessions still cancel it, including revoke/reaccept.
        CoopGameplayContext same_area = owner->context;
        same_area.area_generation = context->area_generation;
        if (memcmp(&same_area, context, sizeof *context) && owner->travel_generation != UINT64_MAX)
            owner->travel_generation++;
        owner->context = *context;
        owner->generation++;
        // Exhausted epochs never wrap and revive ancient queued work.
        if (!owner->generation) owner->generation = UINT64_MAX, memset(&owner->context, 0, sizeof owner->context);
    }
}

static inline int coop_gameplay_permitted(const CoopGameplay *owner, unsigned slot, unsigned member, uint64_t session) {
    if (!owner || owner->generation == UINT64_MAX || slot >= COOP_GAMEPLAY_WRITERS || member > COOP_GAMEPLAY_MEMBERS ||
        (slot == COOP_GAMEPLAY_TRAVEL && owner->travel_generation == UINT64_MAX)) return 0;
    const CoopGameplayWriter *writer = &owner->writer[slot];
    if (!writer->write || !(owner->context.capabilities & writer->capability)) return 0;
    return member ? session && owner->context.accepted_session[member] == session : session == owner->context.room_session;
}

static inline int coop_gameplay_ticket(const CoopGameplay *owner, unsigned slot, unsigned member, uint64_t session,
                                      CoopGameplayTicket *ticket) {
    if (!ticket) return 0;
    memset(ticket, 0, sizeof *ticket);
    if (!coop_gameplay_permitted(owner, slot, member, session)) return 0;
    const uint64_t generation = slot == COOP_GAMEPLAY_TRAVEL ? owner->travel_generation : owner->generation;
    *ticket = (CoopGameplayTicket){COOP_GAMEPLAY_VERSION, sizeof *ticket, slot, member, generation, session, owner};
    return 1;
}

static inline int coop_gameplay_dispatch(const CoopGameplay *owner, const CoopGameplayTicket *ticket,
                                        const void *payload, size_t size, void *game) {
    if (!owner || !ticket || !payload || ticket->version != COOP_GAMEPLAY_VERSION || ticket->size != sizeof *ticket ||
        ticket->owner != owner || ticket->generation !=
            (ticket->slot == COOP_GAMEPLAY_TRAVEL ? owner->travel_generation : owner->generation) ||
        !coop_gameplay_permitted(owner, ticket->slot, ticket->member, ticket->session)) return 0;
    const CoopGameplayWriter *writer = &owner->writer[ticket->slot];
    if (size != writer->payload_size) return 0;
    return writer->write(game, payload);
}
