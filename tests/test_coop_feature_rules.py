"""Numbered feature rules: canonical wire bytes, signed relay, unknown rules, bounds and acceptance.

Compiles the real engine with two simulated registries: an empty one and two synthetic definitions (no game data).
The latter models a newer build; the former models an older build with the same wire format. Each can read and
re-emit the other's charter without changing its signature. --source-root selects the parent for the negative run.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cc", "gcc"]
ROOT = Path(__file__).resolve().parents[1]

FIXTURE = r'''
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include "engine.c"

static void sign_charter(CoopSync *s, Charter *c) {
    uint8_t m[sizeof SIG_RULES - 1 + CTX_LEN + CHARTER_FIELDS_MAX];
    memcpy(c->pk, s->pk, COOP_PK_LEN);
    c->kid = s->kid;
    coop_sign(c->sig, s->sk, m, charter_message(s, c, m));
}
static int verify(CoopSync *s, const Charter *c) {
    uint8_t m[sizeof SIG_RULES - 1 + CTX_LEN + CHARTER_FIELDS_MAX];
    size_t n = charter_message(s, c, m);
    return coop_sign_check(c->sig, c->pk, m, n);
}
static size_t pack(const Charter *c, uint8_t *out) {
    size_t n = charter_fields(c, out);
    memcpy(out + n, c->sig, COOP_SIG_LEN);
    return n + COOP_SIG_LEN;
}
static CoopSync *engine(int id) {
    CoopSync *s = coop_sync_new((uint64_t)id, 123);
    assert(s);
    uint8_t seed[32] = {0};
    seed[0] = (uint8_t)id;
    coop_sync_seed(s, seed);
    coop_sync_name(s, id == 1 ? "One" : "Two");
    const int ids[] = {1, 2};
    const uint64_t sessions[] = {101, 202};
    coop_sync_room(s, "K7XG2MRTB9DHW4PZ5N", id, ids, sessions, 2, 1, 1000);
    coop_sync_rebind(s);
    return s;
}
static uint8_t digest[COOP_MAX_MESSAGE];
static size_t digest_n;
static int capture(int to, uint8_t kind, const uint8_t *bytes, size_t n, void *user) {
    (void)to; (void)user;
    if (kind == COOP_K_DIGEST) {
        assert(n <= sizeof digest);
        memcpy(digest, bytes, n);
        digest_n = n;
    }
    return 1;
}
static void exchange(CoopSync *a, CoopSync *b, uint64_t now) {
    digest_n = 0;
    send_digest(a, b->self, capture, NULL);
    assert(digest_n && digest[0] == COOP_K_DIGEST_VER);
    coop_sync_receive(b, a->self, COOP_K_DIGEST, digest, digest_n, now);
}
static void defaults_and_bounds(void) {
    CoopRules r;
    coop_rules_default(&r);
    assert(coop_feature_at(-1) == NULL && coop_feature_definition(0) == NULL);
#if KNOWN_RULES
    assert(coop_feature_definition(250)->lo == -10 && coop_feature_definition(250)->hi == 100);
    assert(coop_feature_rule(&r, 250) == 7 && coop_feature_rule(&r, 251) == 1);
    CoopRules absent = {0};
    assert(coop_feature_rule(&absent, 250) == 7);
    CoopRules kept_rules = r;
    assert(!coop_feature_set(&r, 250, -11) && !coop_feature_set(&r, 250, 101));
    assert(!memcmp(&r, &kept_rules, sizeof r));
#else
    assert(coop_feature_at(0) == NULL && !r.features.count);
    assert(coop_feature_rule(&r, 250) == 0);
#endif
    assert(coop_feature_set(&r, 249, -32768));
    assert(coop_feature_rule(&r, 249) == -32768);
    assert(coop_feature_set(&r, 249, 32767));
    assert(coop_feature_rule(&r, 249) == 32767);
    assert(!coop_feature_set(&r, 249, 32768) && !coop_feature_set(&r, 249, -32769));
    assert(!coop_feature_set(&r, 0, 0) && !coop_feature_set(&r, 256, 0));
    assert(!coop_feature_set(&r, 2, INT_MAX) && !coop_feature_set(&r, 2, INT_MIN));
    assert(coop_feature_set(&r, 3, -17) && coop_feature_set(&r, 2, 23));
    assert(r.features.bytes[0] == 2 && r.features.bytes[3] == 3);
    char text[512];
    coop_rules_text(&r, text, sizeof text);
    assert(strstr(text, "rule 249 32767 (unknown)"));
#if KNOWN_RULES
    assert(strstr(text, "coop_test_amount 7"));
#endif
    memset(&r.features, 0, sizeof r.features);
    for (int i = 64; i > 0; i--) assert(coop_feature_set(&r, i, i));
    assert(r.features.count == COOP_FEATURE_MAX);
    CoopFeatures before = r.features;
    assert(!coop_feature_set(&r, 65, 0) && !memcmp(&before, &r.features, sizeof before));
    assert(coop_feature_set(&r, 64, 123));
    assert(coop_feature_rule(&r, 64) == 123);
}
static void wire_and_signature(CoopSync *a, const char *output, const char *input) {
    Charter c = {0}, parsed;
    CoopRules r;
    coop_rules_default(&r);
    assert(coop_feature_set(&r, 250, 9) && coop_feature_set(&r, 249, -1234));
    c.epoch = 9;
    memcpy(c.mode, r.mode, sizeof c.mode);
    c.features = r.features;
    sign_charter(a, &c);
    // Verify independently under Longshot's fixed domain, not the engine's own role constant.
    uint8_t signed_bytes[sizeof SIG_RULES - 1 + CTX_LEN + CHARTER_FIELDS_MAX];
    size_t signed_n = charter_message(a, &c, signed_bytes);
    memcpy(signed_bytes, "longshot-coop/5 rules", 21);
    assert(coop_sign_check(c.sig, c.pk, signed_bytes, signed_n));
    memcpy(signed_bytes, "hookshot-coop/5 rules", 21);
    assert(!coop_sign_check(c.sig, c.pk, signed_bytes, signed_n));
    uint8_t bytes[COOP_MAX_MESSAGE], again[COOP_MAX_MESSAGE];
    size_t n = pack(&c, bytes);
    assert(charter_parse(bytes, n, &parsed) == n && verify(a, &parsed));
    assert(pack(&parsed, again) == n && !memcmp(bytes, again, n));
    // Every truncation fails, including one inside an unknown value or its signature.
    for (size_t i = 0; i < n; i++) assert(!charter_parse(bytes, i, &parsed));
    const size_t f = n - COOP_SIG_LEN - (1 + 3u * c.features.count);
    bytes[f + 2] ^= 1;
    assert(charter_parse(bytes, n, &parsed) == n && !verify(a, &parsed));
    bytes[f + 2] ^= 1;
    bytes[f + 1] = 0;
    assert(!charter_parse(bytes, n, &parsed));
    pack(&c, bytes);
    bytes[f + 4] = bytes[f + 1];
    assert(!charter_parse(bytes, n, &parsed));
    pack(&c, bytes);
    bytes[f] = COOP_FEATURE_MAX + 1;
    assert(!charter_parse(bytes, n, &parsed));
#if KNOWN_RULES
    // The maker can sign an out-of-range value, but the reader still refuses it.
    c.features.bytes[4] = 101;  // sorted 249,250,...; 250's low value byte
    c.features.bytes[5] = 0;
    sign_charter(a, &c);
    n = pack(&c, bytes);
    assert(!charter_parse(bytes, n, &parsed));
    c.features = r.features;
    sign_charter(a, &c);
#endif
    n = pack(&c, bytes);
    if (output) {
        FILE *fp = fopen(output, "wb");
        assert(fp && fwrite(bytes, 1, n, fp) == n && !fclose(fp));
    }
    if (input) {
        FILE *fp = fopen(input, "rb");
        assert(fp);
        n = fread(bytes, 1, sizeof bytes, fp);
        assert(!ferror(fp) && !fclose(fp));
        assert(charter_parse(bytes, n, &parsed) == n && verify(a, &parsed));
        assert(pack(&parsed, again) == n && !memcmp(bytes, again, n));
        assert(feature_value(parsed.features.bytes) == -1234);
    }
    // Worst-case digest with every bounded list full stays within the datagram budget.
    memset(&c.features, 0, sizeof c.features);
    for (int i = 1; i <= COOP_FEATURE_MAX; i++) assert(feature_set(&c.features, i, i));
    c.nadmit = MAX_ADMIT, c.nremoved = MAX_REMOVED;
    for (int i = 0; i < MAX_ADMIT; i++) c.admit[i] = (uint64_t)i + 1;
    for (int i = 0; i < MAX_REMOVED; i++) c.removed[i] = (uint64_t)i + 100;
    sign_charter(a, &c);
    n = pack(&c, bytes);
    assert(n == CHARTER_FIELDS_MAX + COOP_SIG_LEN && charter_parse(bytes, n, &parsed) == n);
    a->ch = c, a->have_room = 1;
    send_digest(a, 2, capture, NULL);
    assert(digest_n <= COOP_MAX_MESSAGE);
}
static void room_rules(void) {
    CoopSync *a = engine(1), *b = engine(2);
    CoopRules r, got;
    coop_rules_default(&r);
    assert(coop_feature_set(&r, 249, 31) && coop_feature_set(&r, 250, 9));
    coop_sync_settings(a, &r, 1);
    originate(a);
    exchange(a, b, 1100);
    exchange(b, a, 1200);
    coop_sync_rules(b, &got);
    assert(got.epoch && coop_feature_rule(&got, 249) == 31 && coop_feature_rule(&got, 250) == 9);
    // A receiving peer forwards the original charter and signature, unknown values included.
    assert(features_equal(&a->ch.features, &b->ch.features) && !memcmp(a->ch.sig, b->ch.sig, COOP_SIG_LEN));
    CoopOffer old, changed;
    assert(coop_sync_offer(b, &old, 1300) && coop_sync_accept(b, old.token));
    assert(coop_sync_offer(b, &old, 1400) && old.accepted);
    assert(coop_feature_set(&r, 249, 32));
    coop_sync_settings(a, &r, 1);
    assert(charter_stale(a));
    originate(a);
    // A tampered feature must not change the receiver's rules, even with a genuine key and signature attached.
    Charter bad = a->ch;
    bad.features.bytes[1] ^= 1;
    consider_charter(b, 1, &bad);
    coop_sync_rules(b, &got);
    assert(coop_feature_rule(&got, 249) == 31);
    exchange(a, b, 1500);
    assert(coop_sync_offer(b, &changed, 1600) && !changed.accepted && changed.token != old.token);
    assert(!coop_sync_accept(b, old.token) && coop_sync_accept(b, changed.token));
    assert(coop_sync_offer(b, &changed, 1700) && changed.accepted);
    // A maker with an older registry/settings preserves unknown rules while changing its categories.
    coop_rules_default(&r);
    r.mode[COOP_CAT_PROGRESS] = COOP_MODE_OWN;
    coop_sync_settings(a, &r, 1);
    originate(a);
    assert(coop_feature_rule(&a->room, 249) == 32);
#if !KNOWN_RULES
    assert(coop_feature_rule(&a->room, 250) == 9);
#endif
    // Invalid local lists are not adopted or normalized into a different setting.
    r.features.count = COOP_FEATURE_MAX + 1;
    CoopRules kept = a->mine;
    coop_sync_settings(a, &r, 1);
    assert(!memcmp(&kept, &a->mine, sizeof kept));
    coop_sync_free(a), coop_sync_free(b);
}
int main(int argc, char **argv) {
    defaults_and_bounds();
    CoopSync *a = engine(1);
    wire_and_signature(a, argc > 1 ? argv[1] : NULL, argc > 2 ? argv[2] : NULL);
    coop_sync_free(a);
    room_rules();
    puts("PASS: feature rules: defaults, bounds, canonical packing, signatures, unknown relay, acceptance, full digest");
    return 0;
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cc", default="gcc")
    p.add_argument("--source-root", type=Path, default=ROOT)
    args = p.parse_args()
    root = args.source_root.resolve()
    state = (root / "host/coop_state.c").read_text(encoding="utf-8")
    marker = "    {0, NULL, 0, 0, 0},"
    if state.count(marker) != 1:
        raise SystemExit("FAIL: no numbered feature rule registry")
    begin = state.index("static const CoopFeatureRule feature_rules[] = {")
    end = state.index("\n};", begin) + 3
    with tempfile.TemporaryDirectory(prefix="coop-feature-rules-") as name:
        folder = Path(name)
        (folder / "fixture.c").write_text(FIXTURE, encoding="utf-8")
        mono = root / "third_party/monocypher/monocypher.c"
        subprocess.run([args.cc, "-std=c99", "-O1", "-w", "-c", str(mono), "-o", str(folder / "mono.o")], check=True)
        for known in (0, 1):
            table = "static const CoopFeatureRule feature_rules[] = {\n" + marker
            if known:
                table += '\n    {250, "coop_test_amount", -10, 100, 7},\n    {251, "coop_test_switch", 0, 1, 1},'
            source = state[:begin] + table + "\n};" + state[end:]
            (folder / "engine.c").write_text(source, encoding="utf-8")
            exe = folder / ("known" if known else "unknown")
            subprocess.run([args.cc, "-std=c11", "-D_CRT_SECURE_NO_WARNINGS", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                            "-DKNOWN_RULES=%d" % known, "-I" + str(root / "host"), str(folder / "fixture.c"),
                            str(root / "host/coop_proto.c"), str(folder / "mono.o"), "-o", str(exe)], check=True)
        subprocess.run([str(folder / "known"), str(folder / "new.bin")], check=True)
        subprocess.run([str(folder / "unknown"), str(folder / "old.bin"), str(folder / "new.bin")], check=True)
        subprocess.run([str(folder / "known"), str(folder / "new.bin"), str(folder / "old.bin")], check=True)


if __name__ == "__main__":
    main()
