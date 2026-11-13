/* Authored native boundary: production kit policy, give wrapper and equipment
   calls execute unchanged. No game data or lifted code is compiled. */
#include <assert.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "a64.h"
#include "oot_coop_kit.h"
#include "coop_gameplay.h"
#include "coop_profile.h"
#include "oot_cheat_items.h"

static unsigned char image[0x400000], heap[0x40000];
static unsigned reads, gives, equips, capacity_calls, logs;
static int active = 1, safe = 1, grant_status = 2, delayed, equip_fail, missing_fn;
static int stock[4], capacities[4];
static char mount_path[1024], root_path[128], build[64] = "e0f3ccfaf50065ae4ea01eb5aeb13b1a45da3348";
#define hos_main_build_id build
static const uint64_t base = 0x10000000, mgr = 0x10001000, pouch = 0x10003000,
    player = 0x10008000, parts = 0x10009000, unit = 0x1000a000;
static void *hos_guest_range(uint64_t p, uint64_t n) {
    if (p >= 0x84c00000 && n <= sizeof image && p - 0x84c00000 <= sizeof image - n) return image + (p - 0x84c00000);
    if (p >= base && n <= sizeof heap && p - base <= sizeof heap - n) return heap + (p - base);
    return NULL;
}
static void *ptr(uint64_t p) { void *v = hos_guest_range(p, 1); assert(v); return v; }
#define GP(p) ptr(p)
#define A64_PTR(p) ptr(p)
static uint64_t ld(uint64_t p, unsigned n) { uint64_t v = 0; void *s = hos_guest_range(p, n); assert(s); memcpy(&v, s, n); reads++; return v; }
static void st(uint64_t p, uint64_t v, unsigned n) { void *s = hos_guest_range(p, n); assert(s); memcpy(s, &v, n); }
static uint64_t recomp_load64(Ctx *c, uint64_t p) { (void)c; return ld(p, 8); }
static uint64_t recomp_load32(Ctx *c, uint64_t p) { (void)c; return ld(p, 4); }
static void recomp_store64_sp(Ctx *c, uint64_t p, uint64_t v) { (void)c; st(p, v, 8); }
static void recomp_store32_sp(Ctx *c, uint64_t p, uint64_t v) { (void)c; st(p, v, 4); }
static void hos_log(const char *fmt, ...) { (void)fmt; logs++; }
int coop_profile_active(void) { return active; }
const char *coop_profile_root(void) { return root_path; }
int coop_profile_path_safe(const char *path) { assert(path); return safe; }
int hos_fs_save_root(char *out, size_t cap) { snprintf(out, cap, "%s", mount_path); return mount_path[0] != 0; }
static const char *names[] = {OOT_KOKIRI_SWORD, OOT_DEKU_SHIELD, OOT_MASTER_SWORD, OOT_HYLIAN_SHIELD};
static int item_at(uint64_t wrapper) {
    const char *name = ptr(ld(wrapper, 8));
    for (int i = 0; i < 4; i++) if (!strcmp(name, names[i])) return i;
    assert(!"packet must never choose a native item name"); return -1;
}
static void count_native(Ctx *c) {
    assert(c->x[0] == pouch && !c->x[2] && !c->x[3]);
    c->x[0] = (uint32_t)stock[item_at(c->x[1])]; c->x[31] -= 64;
}
static void capacity_native(Ctx *c) {
    assert(c->x[0] == pouch && !c->x[2] && !c->x[3]); capacity_calls++;
    c->x[0] = (uint32_t)capacities[item_at(c->x[1])]; c->x[31] -= 64;
}
static void give_native(Ctx *c) {
    const uint64_t event = c->x[0];
    const int i = item_at(ld(event + 0x20, 8));
    assert(ld(ld(event + 0x30, 8), 4) == 1);
    assert(c->x[31] == event); gives++;
    if (grant_status == 2 && !delayed) stock[i]++;
    st(event + 0x18, (uint32_t)grant_status << 30, 4);
    c->x[0] = 0xbad; c->x[31] -= 256;
}
static void wear(unsigned i) {
    const uint64_t holder = base + 0x10000 + i * 0x1000, piece = holder + 0x100;
    const unsigned slot = i & 1 ? 4 : 0;
    st(unit + 0x20 + 0x18 * slot + 8, holder, 8);
    st(unit + 0x20 + 0x18 * slot + 16, 123 + i, 4);
    st(holder + 0x40, piece, 8); st(holder + 0x48, 123 + i, 4);
    st(piece + 0x2c, 4, 4); st(piece + 0x260, holder + 0x600, 8);
    strcpy(ptr(holder + 0x600), names[i]);
}
static void equip_native(Ctx *c) {
    const int i = item_at(c->x[1]);
    assert(c->x[0] == pouch && c->x[2] == 3 && c->x[3] == 0 && c->x[30] == 0);
    equips++; if (!equip_fail) wear((unsigned)i); c->x[0] = !equip_fail; c->x[31] -= 100;
}
static A64Fn a64_lookup(uint64_t fn) {
    if (missing_fn) return NULL;
    return fn == 0x805b63e0 ? count_native : fn == 0x828d4a70 ? give_native :
           fn == 0x805b5cf0 ? capacity_native : fn == 0x80a9acd0 ? equip_native : NULL;
}
/* @@POUCH@@ */
#include "kit.c"

static Ctx reset(unsigned age) {
    memset(image, 0, sizeof image); memset(heap, 0, sizeof heap);
    memset(stock, 0, sizeof stock);
    for (int i = 0; i < 4; i++) capacities[i] = 1;
    active = safe = 1; grant_status = 2; delayed = equip_fail = missing_fn = 0;
    gives = equips = reads = capacity_calls = logs = 0;
    strcpy(root_path, "/owned/coop-sessions/0123456789abcdef0123456789abcdef/user");
    snprintf(mount_path, sizeof mount_path, "%s/save/account", root_path);
    st(0x84c9e180, base, 8); st(base, mgr, 8);
    st(0x84c9e090, base + 24, 8); st(base + 24, base + 0x200, 8);
    st(0x84c9e0d0, base + 8, 8); st(base + 8, pouch, 8);
    st(0x84c9e2a8, base + 16, 8); st(base + 16, base + 32, 8);
    st(0x84eb6888, base + 32, 8); st(0x84e8c060, base + 32, 8);
    st(0x84e98e28, pouch, 8); st(0x84e571d0, base + 64, 8); st(base + 64 + 0x480, player, 8);
    st(player + 0x260, base + 0x18000, 8); strcpy(ptr(base + 0x18000), age ? "Player_Adult" : "Player_Child");
    st(player + 0x2c, 4, 4); st(player + 0x270, parts, 8); st(parts + 0x200, unit, 8);
    st(mgr + 0x008 + 0x28, 128, 4); st(mgr + 0x008 + 0x30, base + 0x19000, 8);
    st(mgr + 0x0c8 + 0x28, 128, 4); st(mgr + 0x0c8 + 0x30, base + 0x1a000, 8);
    st(mgr + 0x608 + 0x28, 128, 4); st(mgr + 0x608 + 0x30, base + 0x1b000, 8);
    Ctx c = {{0}, {0}}; c.x[31] = base + sizeof heap - 16; c.x[0] = 0x123456; return c;
}
static OotCoopKitWork work(unsigned age) {
    OotCoopKitWork w = {{age ? 12 : 3, (uint8_t)age, OOT_KIT_VERSION}, 7, 8, 1}; return w;
}
static void finish(Ctx *c, OotCoopKitRun *r, OotCoopKitWork *w) {
    const Ctx saved = *c;
    for (int n = 0; n < 12 && r->state != OOT_KIT_READY && r->state != OOT_KIT_REFUSED; n++) oot_coop_kit_step(c, r, w);
    assert(!memcmp(c, &saved, sizeof saved));
}
static void rules(void) {
    CoopRules r; OotCoopKit k;
    coop_rules_default(&r); assert(oot_coop_kit_parse(&r, &k) == OOT_KIT_OFF);
    assert(coop_feature_set(&r, 83, 1)); assert(coop_feature_set(&r, 40, 3));
    assert(oot_coop_kit_parse(&r, &k) == OOT_KIT_VALID && k.bits == 3 && k.age == 0);
    assert(coop_feature_set(&r, 32, 1)); assert(oot_coop_kit_parse(&r, &k) == OOT_KIT_INCOMPATIBLE);
    assert(coop_feature_set(&r, 40, 12)); assert(oot_coop_kit_parse(&r, &k) == OOT_KIT_VALID && k.age == 1);
    for (int id = 40; id <= 47; id++) {
        CoopRules bad = r; assert(coop_feature_set(&bad, id, id == 40 ? 16 : 1));
        assert(oot_coop_kit_parse(&bad, &k) == OOT_KIT_UNKNOWN);
    }
    for (int bit = 0; bit < 15; bit++) {
        CoopRules bad = r; assert(coop_feature_set(&bad, 40, 12 | (1 << bit)));
        if (bit != 2 && bit != 3) assert(oot_coop_kit_parse(&bad, &k) < 0);
    }
    for (int id = 84; id <= 96; id++) {
        CoopRules bad = r; assert(coop_feature_set(&bad, id, 1)); assert(oot_coop_kit_parse(&bad, &k) == OOT_KIT_UNSUPPORTED);
    }
    assert(coop_feature_set(&r, 33, 1)); assert(oot_coop_kit_parse(&r, &k) == OOT_KIT_UNSUPPORTED);
    coop_rules_default(&r); r.features.count = 0; assert(coop_feature_set(&r, 83, 1)); assert(coop_feature_set(&r, 40, 3));
    assert(oot_coop_kit_parse(&r, &k) == OOT_KIT_BAD_AGE);
    assert(coop_feature_set(&r, 83, 0)); assert(oot_coop_kit_parse(&r, &k) == OOT_KIT_BAD_VERSION);
    assert(oot_coop_kit_parse(NULL, &k) < 0);
    r.features.count = 65; assert(oot_coop_kit_parse(&r, &k) < 0);
}
int main(void) {
    rules();
    for (unsigned age = 0; age < 2; age++) {
        Ctx c = reset(age); OotCoopKitRun r = {0}; OotCoopKitWork w = work(age);
        finish(&c, &r, &w); assert(r.state == OOT_KIT_READY && gives == 2 && equips == 2);
        assert(capacity_calls && stock[2 * age] == 1 && stock[2 * age + 1] == 1);
        assert(r.result[2 * age] == OOT_KIT_ENTRY_EQUIPPED && r.result[2 * age + 1] == OOT_KIT_ENTRY_EQUIPPED);
        finish(&c, &r, &w); assert(oot_coop_kit_step(&c, &r, &w) && gives == 2 && equips == 2);
        r.state = OOT_KIT_CANCELLED; assert(!oot_coop_kit_step(&c, &r, &w) && gives == 2);
        c = reset(age); memset(&r, 0, sizeof r); stock[2 * age] = stock[2 * age + 1] = 1;
        wear(2 * age); wear(2 * age + 1); finish(&c, &r, &w);
        assert(r.state == OOT_KIT_READY && !gives && equips == 2);
        c = reset(age); memset(&r, 0, sizeof r); capacities[2 * age + 1] = 0;
        finish(&c, &r, &w); assert(r.state == OOT_KIT_REFUSED && !gives && !equips);
        c = reset(age ^ 1); memset(&r, 0, sizeof r); finish(&c, &r, &w);
        assert(r.state == OOT_KIT_REFUSED && !gives);
    }
    for (int failure = 0; failure < 8; failure++) {
        Ctx c = reset(0); OotCoopKitRun r = {0}; OotCoopKitWork w = work(0);
        if (failure == 0) active = 0;
        if (failure == 1) strcpy(mount_path, "/owned/save/account");
        if (failure == 2) snprintf(mount_path, sizeof mount_path, "%sx/save/account", root_path);
        if (failure == 3) snprintf(mount_path, sizeof mount_path, "%s/save/../account", root_path);
        if (failure == 4) mount_path[0] = 0;
        if (failure == 5) safe = 0;
        if (failure == 6) missing_fn = 1;
        if (failure == 7) w.kit.bits |= 16;
        finish(&c, &r, &w); assert(r.state == OOT_KIT_REFUSED && !gives && !equips);
    }
    Ctx c = reset(0); OotCoopKitRun r = {0}; OotCoopKitWork w = work(0);
    st(mgr + 0x008 + 0x38, 1, 4); finish(&c, &r, &w); assert(!gives && !equips);
    st(mgr + 0x008 + 0x38, 0, 4); finish(&c, &r, &w); assert(r.state == OOT_KIT_READY);
    c = reset(0); memset(&r, 0, sizeof r); grant_status = 3; finish(&c, &r, &w);
    assert(r.state == OOT_KIT_REFUSED && gives == 1); finish(&c, &r, &w); assert(gives == 1);
    c = reset(0); memset(&r, 0, sizeof r); delayed = 1;
    for (int i = 0; i <= OOT_KIT_WAIT_FRAMES; i++) oot_coop_kit_step(&c, &r, &w);
    assert(r.state == OOT_KIT_REFUSED && gives == 1 && !equips);
    c = reset(0); memset(&r, 0, sizeof r); equip_fail = 1;
    for (int i = 0; i <= OOT_KIT_WAIT_FRAMES; i++) oot_coop_kit_step(&c, &r, &w);
    assert(r.state == OOT_KIT_REFUSED && gives == 1 && equips == 1);
    c = reset(0); memset(&r, 0, sizeof r); oot_coop_kit_step(&c, &r, &w); w.epoch++;
    assert(!oot_coop_kit_step(&c, &r, &w) && r.state == OOT_KIT_CANCELLED && gives == 1);
    c = reset(0); memset(&r, 0, sizeof r); w = work(0); w.kit.version = 0;
    assert(!oot_coop_kit_step(&c, &r, &w) && !reads && !gives && !equips && r.state == OOT_KIT_IDLE);
    puts("PASS: sword kit child/adult dependencies, native give/equip/readback, capacity, unknown/incompatible rules, age, mount, repeat/off, queue and cancellation");
    return 0;
}
