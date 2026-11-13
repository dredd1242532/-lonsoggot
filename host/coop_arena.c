/* (longshot) No shipped map keys or coordinates. This codec describes locally surveyed data. */
#include "coop_arena.h"
#include "coop_proto.h"
#include <math.h>
#include <stdio.h>
#include <string.h>
#ifdef _WIN32
#include <windows.h>
#include <wchar.h>
#endif

static void put32(uint8_t *p, uint32_t v) { for (unsigned i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (8 * i)); }
static uint32_t get32(const uint8_t *p) { return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static void putf(uint8_t *p, float f) { uint32_t v; memcpy(&v, &f, 4); put32(p, v); }
static float getf(const uint8_t *p) { uint32_t v = get32(p); float f; memcpy(&f, &v, 4); return f; }
static int nonzero(const uint8_t *p, size_t n) { unsigned v = 0; for (size_t i = 0; i < n; i++) v |= p[i]; return v != 0; }
static int coordinate(float v) { return isfinite(v) && fabsf(v) <= 1000000.0f; }

int coop_arena_contains(const CoopArena *a, const float p[3]) {
    if (!a || !p || a->vertices < 3 || a->vertices > COOP_ARENA_VERTICES ||
        !coordinate(p[0]) || !coordinate(p[1]) || !coordinate(p[2]) ||
        !isfinite(a->margin) || a->margin <= 0 || p[1] < a->ymin || p[1] > a->ymax) return 0;
    for (unsigned i = 0; i < a->vertices; i++) {
        unsigned j = (i + 1) % a->vertices;
        double dx = (double)a->boundary[j][0] - a->boundary[i][0];
        double dz = (double)a->boundary[j][1] - a->boundary[i][1];
        double cross = dx * ((double)p[2] - a->boundary[i][1]) - dz * ((double)p[0] - a->boundary[i][0]);
        if (!isfinite(cross) || cross <= 0 || cross * cross <= (double)a->margin * a->margin * (dx * dx + dz * dz)) return 0;
    }
    return 1;
}

int coop_arena_valid(const CoopArena *a) {
    if (!a || a->version != COOP_ARENA_VERSION || a->id != 1 || a->age > 1 ||
        a->vertices < 3 || a->vertices > COOP_ARENA_VERTICES || a->environment != COOP_ARENA_CLEAR || a->time_policy ||
        !nonzero(a->build, 20) || !nonzero(a->variant, 32) || !nonzero(a->source, 32) || !nonzero(a->survey, 32) ||
        !coordinate(a->ymin) || !coordinate(a->ymax) || a->ymin >= a->ymax ||
        !isfinite(a->margin) || a->margin <= 0 || a->margin > 10) return 0;
    size_t len = 0;
    while (len < sizeof a->map && a->map[len]) {
        if ((unsigned char)a->map[len] < 32 || (unsigned char)a->map[len] > 126) return 0;
        len++;
    }
    if (!len || len == sizeof a->map) return 0;
    for (size_t i = len; i < sizeof a->map; i++) if (a->map[i]) return 0;
    for (unsigned i = 0; i < COOP_ARENA_VERTICES; i++) {
        if (!coordinate(a->boundary[i][0]) || !coordinate(a->boundary[i][1])) return 0;
        if (i >= a->vertices && (a->boundary[i][0] != 0 || a->boundary[i][1] != 0)) return 0;
    }
    /* Every nonadjacent vertex must lie strictly left of every directed edge: rejects concavity,
       duplicate/collinear points, reversed winding and self-intersections, not just successive turns. */
    for (unsigned i = 0; i < a->vertices; i++) {
        unsigned j = (i + 1) % a->vertices;
        double dx = (double)a->boundary[j][0] - a->boundary[i][0];
        double dz = (double)a->boundary[j][1] - a->boundary[i][1];
        for (unsigned k = 0; k < a->vertices; k++) {
            if (k == i || k == j) continue;
            if (dx * ((double)a->boundary[k][1] - a->boundary[i][1]) -
                dz * ((double)a->boundary[k][0] - a->boundary[i][0]) <= 0) return 0;
        }
    }
    for (unsigned i = 0; i < 2; i++) {
        if (!coop_arena_contains(a, a->spawn[i]) || !isfinite(a->spawn[i][3]) || fabsf(a->spawn[i][3]) > 3.141592654f) return 0;
        double dx = (double)a->spawn[1 - i][0] - a->spawn[i][0];
        double dz = (double)a->spawn[1 - i][2] - a->spawn[i][2];
        if (dx * dx + dz * dz <= 4.0 * a->margin * a->margin ||
            sin(a->spawn[i][3]) * dx + cos(a->spawn[i][3]) * dz <= 0) return 0;
    }
    return 1;
}

int coop_arena_matches(const CoopArena *a, const uint8_t build[20], const char *map,
                       unsigned age, const uint8_t variant[32]) {
    return build && map && variant && coop_arena_valid(a) && age == a->age &&
           !memcmp(a->build, build, 20) && !strcmp(a->map, map) && !memcmp(a->variant, variant, 32);
}

size_t coop_arena_pack(uint8_t *p, size_t cap, const CoopArena *a) {
    if (!p || cap < COOP_ARENA_BYTES || !coop_arena_valid(a)) return 0;
    memset(p, 0, COOP_ARENA_BYTES);
    memcpy(p, "LSARENA", 7); p[8] = COOP_ARENA_VERSION;
    p[10] = (uint8_t)COOP_ARENA_BYTES; p[11] = (uint8_t)(COOP_ARENA_BYTES >> 8);
    p[12] = (uint8_t)a->id; p[14] = a->age; p[15] = a->vertices;
    put32(p + 16, a->environment); put32(p + 20, a->time_policy);
    memcpy(p + 24, a->build, 20); memcpy(p + 44, a->map, 64);
    memcpy(p + 108, a->variant, 32); memcpy(p + 140, a->source, 32);
    for (unsigned i = 0; i < 8; i++) putf(p + 172 + i * 4, a->spawn[i / 4][i % 4]);
    putf(p + 204, a->ymin); putf(p + 208, a->ymax); putf(p + 212, a->margin);
    for (unsigned i = 0; i < 32; i++) putf(p + 216 + i * 4, a->boundary[i / 2][i % 2]);
    memcpy(p + 344, a->survey, 32);
    return COOP_ARENA_BYTES;
}

int coop_arena_parse(const uint8_t *p, size_t n, CoopArena *out) {
    if (!p || !out || n != COOP_ARENA_BYTES || memcmp(p, "LSARENA\0", 8) ||
        p[8] != COOP_ARENA_VERSION || p[9] || (p[10] | (unsigned)p[11] << 8) != COOP_ARENA_BYTES || p[13]) return 0;
    for (unsigned i = 376; i < COOP_ARENA_BYTES; i++) if (p[i]) return 0;
    CoopArena a = {0}; a.version = p[8]; a.id = p[12]; a.age = p[14]; a.vertices = p[15];
    a.environment = get32(p + 16); a.time_policy = get32(p + 20);
    memcpy(a.build, p + 24, 20); memcpy(a.map, p + 44, 64);
    memcpy(a.variant, p + 108, 32); memcpy(a.source, p + 140, 32);
    for (unsigned i = 0; i < 8; i++) a.spawn[i / 4][i % 4] = getf(p + 172 + i * 4);
    a.ymin = getf(p + 204); a.ymax = getf(p + 208); a.margin = getf(p + 212);
    for (unsigned i = 0; i < 32; i++) a.boundary[i / 2][i % 2] = getf(p + 216 + i * 4);
    memcpy(a.survey, p + 344, 32);
    if (!coop_arena_valid(&a)) return 0;
    *out = a; return 1;
}

int coop_arena_load(const char *path, CoopArena *out) {
    if (!path || !*path || !out) return 0;
#ifdef _WIN32
    wchar_t wide[32768];
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, wide, 32768)) return 0;
    FILE *f = _wfopen(wide, L"rb");
#else
    FILE *f = fopen(path, "rb");
#endif
    if (!f) return 0;
    uint8_t bytes[COOP_ARENA_BYTES + 1];
    const size_t n = fread(bytes, 1, sizeof bytes, f);
    const int ok = !ferror(f) && n == COOP_ARENA_BYTES && coop_arena_parse(bytes, n, out);
    fclose(f); return ok;
}

int coop_arena_digest(const CoopArena *a, uint8_t digest[32]) {
    uint8_t bytes[COOP_ARENA_BYTES];
    if (!digest || !coop_arena_pack(bytes, sizeof bytes, a)) return 0;
    coop_blake2b(digest, 32, NULL, 0, bytes, sizeof bytes); return 1;
}
