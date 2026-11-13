/* (longshot) Authored arena schema; measured coordinates stay outside the product. */
#ifndef COOP_ARENA_H
#define COOP_ARENA_H
#include <stddef.h>
#include <stdint.h>

enum { COOP_ARENA_VERSION = 1, COOP_ARENA_BYTES = 384, COOP_ARENA_VERTICES = 16 };
enum { COOP_ARENA_CLEAR = 15 }; /* enemies, interactions, recovery, progression all fenced */
typedef struct {
    uint16_t version, id;
    uint8_t age, vertices;
    uint32_t environment, time_policy;
    uint8_t build[20], variant[32], source[32], survey[32];
    char map[64];
    float spawn[2][4]; /* x, y, z, yaw radians (0 faces +z), native units */
    float ymin, ymax, margin;
    float boundary[COOP_ARENA_VERTICES][2]; /* x,z; strictly convex, counterclockwise */
} CoopArena;
/* Validators do not certify a survey, clear actors, place a player or interpret a native map. */
int coop_arena_valid(const CoopArena *arena);
int coop_arena_contains(const CoopArena *arena, const float position[3]);
int coop_arena_matches(const CoopArena *arena, const uint8_t build[20], const char *map,
                       unsigned age, const uint8_t variant[32]);
size_t coop_arena_pack(uint8_t *out, size_t cap, const CoopArena *arena);
int coop_arena_parse(const uint8_t *bytes, size_t n, CoopArena *out);
/* Explicit local operator-selected path only. Exact size, bounded read, no writes or network paths. */
int coop_arena_load(const char *path, CoopArena *out);
int coop_arena_digest(const CoopArena *arena, uint8_t digest[32]);
#endif
