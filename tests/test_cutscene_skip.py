"""Game-free checks of the authored skip permission hook; the game owns confirmation and completion."""
import argparse
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUN_ALL_ARGS = ["--cc", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nx2"))
import oot_patches


A64_HEADER = r'''
#include <stddef.h>
#include <stdint.h>
typedef struct { uint64_t x[32]; } Ctx;
#define A64_NOESC
#define A64_PTR(a) ((void *)(uintptr_t)(a))
uint64_t recomp_load64(Ctx *, uint64_t);
uint64_t recomp_load8(Ctx *, uint64_t);
'''


class CutsceneSkipPatchTests(unittest.TestCase):
    def source(self, body):
        return ('#include "a64.h"\nvoid before(Ctx *c) {\n' + body + '\n}\n'
                + oot_patches.CUTSCENE_HEAD + body + '\n}\n'
                + 'void after(Ctx *c) {\n' + body + '\n}\n')

    def setUp(self):
        self.body = (oot_patches.CUTSCENE_ANCHOR
                     + "    c->x[8]=recomp_load64(c,c->x[0] + 0);\n"
                     + "    c->x[8]=recomp_load64(c,c->x[8] + 40);\n"
                     + oot_patches.CUTSCENE_CALL
                     + "    if (runtime_lock) c->x[1] = 0;\n"
                     + "    native_update_and_confirm(c);\n")

    def test_changes_only_authored_permission_in_target_function(self):
        source = self.source(self.body)
        patched = oot_patches.patch_cutscene_skip(source)
        expected = source.replace(
            oot_patches.CUTSCENE_HEAD + self.body,
            oot_patches.CUTSCENE_HEAD + self.body.replace(
                oot_patches.CUTSCENE_ANCHOR,
                oot_patches.CUTSCENE_ANCHOR + oot_patches.CUTSCENE_HOOK), 1)
        expected = expected.replace(
            '#include "a64.h"\n',
            '#include "a64.h"\nextern uint64_t hos_oot_cutscene_skip_allowed(Ctx *c, uint64_t player, uint64_t native_allowed);\n', 1)
        self.assertEqual(patched, expected)
        self.assertEqual(oot_patches.patch_cutscene_skip(patched), patched)
        self.assertEqual(patched.count(oot_patches.CUTSCENE_HOOK), 1)

    def test_coexists_with_native_opening_hooks(self):
        source = (self.source(self.body)
                  + oot_patches.OPENING_REQUEST_HEAD + "\n}\n"
                  + oot_patches.OPENING_TIMELINE_HEAD + "\n}\n"
                  + oot_patches.OPENING_QUERY_HEAD + "\n}\n"
                  + oot_patches.CUTSCENE_TIMELINE_HEAD + oot_patches.CUTSCENE_TIMELINE_PREFIX
                  + oot_patches.CUTSCENE_TIMELINE_BRANCH + oot_patches.CUTSCENE_TIMELINE_READY + "\n}\n")
        for opening_first in (False, True):
            patched = oot_patches.patch_opening(source) if opening_first else source
            patched = oot_patches.patch_cutscene_skip(patched)
            patched = oot_patches.patch_cutscene_skip_timeline(patched)
            if not opening_first:
                patched = oot_patches.patch_opening(patched)
            self.assertEqual(patched.count(oot_patches.CUTSCENE_HOOK), 1)
            self.assertEqual(patched.count(oot_patches.CUTSCENE_TIMELINE_ALLOW), 1)
            for hook in (oot_patches.OPENING_REQUEST, oot_patches.OPENING_TIMELINE, oot_patches.OPENING_QUERY):
                self.assertEqual(patched.count(hook), 1)
            self.assertEqual(oot_patches.patch_opening(patched), patched)
            self.assertEqual(oot_patches.patch_cutscene_skip(patched), patched)
            self.assertEqual(oot_patches.patch_cutscene_skip_timeline(patched), patched)

    def test_rejects_missing_or_ambiguous_target_and_instruction_context(self):
        for anchor in (oot_patches.CUTSCENE_ANCHOR, oot_patches.CUTSCENE_CALL):
            for copies in (0, 2):
                with self.subTest(anchor=anchor, copies=copies):
                    self.assertIsNone(oot_patches.patch_cutscene_skip(
                        self.source(self.body.replace(anchor, anchor * copies))))
        source = self.source(self.body)
        self.assertIsNone(oot_patches.patch_cutscene_skip(source.replace(oot_patches.CUTSCENE_HEAD, "")))
        self.assertIsNone(oot_patches.patch_cutscene_skip(source + oot_patches.CUTSCENE_HEAD + "\n}\n"))
        self.assertIsNone(oot_patches.patch_cutscene_skip(oot_patches.CUTSCENE_HEAD + self.body))
        old = "    c->x[1] = hos_oot_cutscene_skip_allowed(c->x[1]);\n"
        self.assertIsNone(oot_patches.patch_cutscene_skip(self.source(
            self.body.replace(oot_patches.CUTSCENE_ANCHOR, oot_patches.CUTSCENE_ANCHOR + old))))
        self.assertIsNone(oot_patches.patch_cutscene_skip(self.source(
            self.body + oot_patches.CUTSCENE_HOOK)))


class TimelineCutsceneSkipPatchTests(unittest.TestCase):
    def source(self, body):
        return ('#include "a64.h"\nvoid before(Ctx *c) {\n' + body + '\n}\n'
                + oot_patches.CUTSCENE_TIMELINE_HEAD + body + '\n}\n'
                + 'void after(Ctx *c) {\n' + body + '\n}\n')

    def setUp(self):
        self.body = (oot_patches.CUTSCENE_TIMELINE_PREFIX + oot_patches.CUTSCENE_TIMELINE_BRANCH
                     + "    native_disabled_path(c);\n" + oot_patches.CUTSCENE_TIMELINE_READY
                     + "    native_readiness_and_completion(c);\n")

    def test_overrides_only_authored_branch_and_preserves_readiness(self):
        source = self.source(self.body)
        patched = oot_patches.patch_cutscene_skip_timeline(source)
        expected = source.replace(
            oot_patches.CUTSCENE_TIMELINE_HEAD + self.body,
            oot_patches.CUTSCENE_TIMELINE_HEAD + self.body.replace(
                oot_patches.CUTSCENE_TIMELINE_BRANCH, oot_patches.CUTSCENE_TIMELINE_ALLOW), 1)
        expected = expected.replace(
            '#include "a64.h"\n',
            '#include "a64.h"\nextern uint64_t hos_oot_cutscene_skip_allowed(Ctx *c, uint64_t player, uint64_t native_allowed);\n', 1)
        self.assertEqual(patched, expected)
        self.assertEqual(oot_patches.patch_cutscene_skip_timeline(patched), patched)

    def test_rejects_missing_ambiguous_or_partially_patched_context(self):
        for anchor in (oot_patches.CUTSCENE_TIMELINE_PREFIX, oot_patches.CUTSCENE_TIMELINE_BRANCH,
                       oot_patches.CUTSCENE_TIMELINE_READY):
            for copies in (0, 2):
                with self.subTest(anchor=anchor, copies=copies):
                    self.assertIsNone(oot_patches.patch_cutscene_skip_timeline(
                        self.source(self.body.replace(anchor, anchor * copies))))
        source = self.source(self.body)
        self.assertIsNone(oot_patches.patch_cutscene_skip_timeline(
            source.replace(oot_patches.CUTSCENE_TIMELINE_HEAD, "")))
        self.assertIsNone(oot_patches.patch_cutscene_skip_timeline(
            source + oot_patches.CUTSCENE_TIMELINE_HEAD + "\n}\n"))
        self.assertIsNone(oot_patches.patch_cutscene_skip_timeline(
            oot_patches.CUTSCENE_TIMELINE_HEAD + self.body))
        self.assertIsNone(oot_patches.patch_cutscene_skip_timeline(self.source(
            self.body + oot_patches.CUTSCENE_TIMELINE_ALLOW)))


class MenuWiringTests(unittest.TestCase):
    def test_off_by_default_and_native_skip_control(self):
        header = (ROOT / "gpu/vk/vk_menu.h").read_text(encoding="utf-8")
        menu = (ROOT / "gpu/vk/vk_menu.cpp").read_text(encoding="utf-8")
        replay = (ROOT / "gpu/vk/vk_replay.cpp").read_text(encoding="utf-8")
        self.assertIn("int allow_cutscene_skip = 0;", header)
        self.assertIn('{TAB_GAMEPLAY, "Skip cutscenes (Experimental)", &opts.allow_cutscene_skip, 0, 1, 1, tx_onoff,', menu)
        self.assertIn('"skip_unskippable_cutscenes"', menu)
        help_text = menu.split('"Skip cutscenes (Experimental)"', 1)[1].split("nullptr", 1)[0]
        self.assertIn("Experimental: opening movie and Navi waking Link only.", help_text)
        self.assertIn("Other scenes keep native skip permissions.", help_text)
        self.assertIn("Save first: can leave story progress incomplete or soft-lock a save.", help_text)
        self.assertIn("if (id == HOS_OPT_ALLOW_CUTSCENE_SKIP) return menu_allow_cutscene_skip();", replay)
        self.assertEqual(menu.count("cutscene_skip_now.store(opts.allow_cutscene_skip, std::memory_order_relaxed);"), 2)
        self.assertIn("int menu_allow_cutscene_skip() { return cutscene_skip_now.load(std::memory_order_relaxed); }", menu)


def check_host(cc):
    fixture = r'''
#include "a64.h"
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
static unsigned char mem[0x4000];
static char said[2048];
static int menu, logs, loads;
int rx_option(int id, int otherwise) {
    assert(id == 56 && otherwise == 0);
    return menu;
}
int hos_mem_region(uint64_t address, uint64_t size) {
    return address && address < sizeof mem && size <= sizeof mem - address;
}
uint64_t recomp_load64(Ctx *c, uint64_t address) {
    uint64_t value; (void)c; assert(hos_mem_region(address, 8)); ++loads;
    memcpy(&value, mem + address, 8); return value;
}
uint64_t recomp_load8(Ctx *c, uint64_t address) {
    (void)c; assert(hos_mem_region(address, 1)); ++loads; return mem[address];
}
static void put64(uint64_t address, uint64_t value) { memcpy(mem + address, &value, 8); }
void hos_log(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt); ++logs;
    vsnprintf(said, sizeof said, fmt, ap); va_end(ap);
}
uint64_t hos_oot_cutscene_skip_allowed(Ctx *c, uint64_t player, uint64_t native_allowed);
void hos_oot_cutscene_skip_begin(Ctx *c);
int main(void) {
    Ctx c = {0};
    enum { PLAYER=0x100, RESOURCE=0x200, HOLDER=0x400, NAME=0x500, OTHER=0x600 };
    put64(PLAYER + 16, RESOURCE); put64(RESOURCE + 8, NAME);
    memcpy(mem + NAME, "Dm_Kokiri_0000", sizeof "Dm_Kokiri_0000");
    memcpy(mem + OTHER, "Dm_Kokiri_0030", sizeof "Dm_Kokiri_0030");
    assert(hos_oot_cutscene_skip_allowed(&c, PLAYER, 0) == 0);
    assert(hos_oot_cutscene_skip_allowed(&c, PLAYER, 7) == 7);
    assert(loads == 0 && logs == 0);
    menu = 1;
    assert(hos_oot_cutscene_skip_allowed(&c, PLAYER, 0) == 1);
    assert(logs == 1 && strstr(said, "override allowed for timeline Dm_Kokiri_0000"));
    assert(hos_oot_cutscene_skip_allowed(&c, PLAYER, 0) == 1 && logs == 1);
    c.x[1] = HOLDER; put64(HOLDER, NAME);
    hos_oot_cutscene_skip_begin(&c);
    assert(hos_oot_cutscene_skip_allowed(&c, PLAYER, 0) == 1 && logs == 2);
    put64(RESOURCE + 8, OTHER); put64(HOLDER, OTHER);
    hos_oot_cutscene_skip_begin(&c);
    assert(hos_oot_cutscene_skip_allowed(&c, PLAYER, 0) == 1);
    assert(logs == 3 && strstr(said, "override allowed for timeline Dm_Kokiri_0030"));
    assert(hos_oot_cutscene_skip_allowed(&c, PLAYER, 0) == 1 && logs == 3);
    put64(RESOURCE + 8, NAME);
    assert(hos_oot_cutscene_skip_allowed(&c, PLAYER, 0) == 1 && logs == 3);
    const char *unknown[] = {"Meet_DekuTree", "ItemGet_Song", "Dm_Kokiri_0030_extra", "Dm_Kokiri_0070"};
    for (unsigned i = 0; i < sizeof unknown / sizeof unknown[0]; ++i) {
        memset(mem + NAME, 0, 64); strcpy((char *)mem + NAME, unknown[i]);
        assert(hos_oot_cutscene_skip_allowed(&c, PLAYER, 0) == 0);
        assert(hos_oot_cutscene_skip_allowed(&c, PLAYER, 9) == 9);
    }
    assert(logs == 3);
    memcpy(mem + NAME, "Dm_Kokiri_0000x", sizeof "Dm_Kokiri_0000");
    assert(hos_oot_cutscene_skip_allowed(&c, PLAYER, 0) == 0);
    assert(hos_oot_cutscene_skip_allowed(NULL, PLAYER, 0) == 0);
    assert(hos_oot_cutscene_skip_allowed(&c, 0, 0) == 0);
    assert(hos_oot_cutscene_skip_allowed(&c, UINT64_MAX - 8, 0) == 0);
    assert(hos_oot_cutscene_skip_allowed(&c, sizeof mem - 16, 0) == 0);
    put64(PLAYER + 16, UINT64_MAX - 8);
    assert(hos_oot_cutscene_skip_allowed(&c, PLAYER, 0) == 0);
    put64(PLAYER + 16, RESOURCE); put64(RESOURCE + 8, sizeof mem - 8);
    assert(hos_oot_cutscene_skip_allowed(&c, PLAYER, 0) == 0);
    put64(RESOURCE + 8, 0);
    assert(hos_oot_cutscene_skip_allowed(&c, PLAYER, 0) == 0);
    hos_oot_cutscene_skip_begin(NULL);
    c.x[1] = UINT64_MAX - 8; hos_oot_cutscene_skip_begin(&c);
    menu = 0;
    assert(hos_oot_cutscene_skip_allowed(&c, PLAYER, 0) == 0);
    menu = -1;
    assert(hos_oot_cutscene_skip_allowed(&c, PLAYER, 0) == 0);
    assert(logs == 3);
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix="cutscene-skip-") as tmp:
        source = Path(tmp) / "fixture.c"
        exe = Path(tmp) / ("fixture.exe" if sys.platform == "win32" else "fixture")
        (Path(tmp) / "a64.h").write_text(A64_HEADER, encoding="utf-8")
        source.write_text(fixture, encoding="utf-8")
        subprocess.run([cc, "-x", "c", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-I", tmp,
                        str(ROOT / "host" / "oot_cutscene_skip.c"), str(source), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


def check_timeline_branch(cc):
    # The lifted branch, join and readiness block, with native calls isolated from the rest of the player.
    body = ("    goto L_80ac87c8;\n" + oot_patches.CUTSCENE_TIMELINE_PREFIX + oot_patches.CUTSCENE_TIMELINE_BRANCH
            + "    { uint64_t _r = ((uint64_t)0 | ((uint64_t)(uint32_t)(((uint32_t)((uint64_t)0)) << 0))); _r &= 0xFFFFFFFFULL; c->x[8] = _r; }\n"
            + "L_80ac87dc:;\n"
            + "    c->x[9]=recomp_load64(c,c->x[0] + 0);\n"
            + "    { uint64_t _r = c->x[8] & 0x1ULL; _r &= 0xFFFFFFFFULL; c->x[1] = _r; }\n"
            + "    c->x[9]=recomp_load64(c,c->x[9] + 40);\n"
            + "{ uint64_t _t = c->x[9]; c->x[30] = 0x80ac87ecULL; A64_ICALL(c, _t); }\n"
            + "    return;\n" + oot_patches.CUTSCENE_TIMELINE_READY
            + "    { uint64_t _r = ((uint64_t)0 | ((uint64_t)(uint32_t)(((uint32_t)(c->x[0])) << 0))); _r &= 0xFFFFFFFFULL; c->x[8] = _r; }\n"
            + "    { uint64_t _r = ((uint64_t)0 | ((c->x[20]) << 0)); c->x[0] = _r; }\n"
            + "{ A64_POLL(c); goto L_80ac87dc; }\n")
    header = A64_HEADER + r'''
#include <assert.h>
#define A64_POLL(c) ((void)(c))
void f_83b60640(Ctx *);
void fixture_setter(Ctx *, uint64_t);
#define A64_ICALL(c, t) fixture_setter(c, t)
'''
    fixture = r'''
enum { EVENT=0x100, RESOURCE=0x500, CONTROLLER=0xa00, VTABLE=0xb00, NAME=0xc00 };
static int native_allowed, ready, option, known, readiness_calls, setter_calls, allowed;
int rx_option(int id, int otherwise) { assert(id==56 && otherwise==0); return option; }
int hos_mem_region(uint64_t address, uint64_t size) { return address && address < 0xd00 && size <= 0xd00 - address; }
void hos_log(const char *fmt, ...) { (void)fmt; }
uint64_t recomp_load64(Ctx *c, uint64_t a) {
    (void)c;
    if (a==EVENT+16) return RESOURCE;
    if (a==EVENT+32) return CONTROLLER;
    if (a==RESOURCE+8) return NAME;
    if (a==CONTROLLER) return VTABLE;
    assert(a==VTABLE+40); return 0x1234;
}
uint64_t recomp_load8(Ctx *c, uint64_t a) {
    (void)c;
    if (a>=NAME && a<NAME+sizeof "Dm_Kokiri_0000")
        return (known ? "Dm_Kokiri_0000" : "Dm_Kokiri_0070")[a-NAME];
    assert(a==RESOURCE+955); return 0xa1 | (native_allowed<<2);
}
void f_83b60640(Ctx *c) {
    assert(c->x[0]==CONTROLLER); ++readiness_calls; c->x[0]=(uint64_t)ready;
}
void fixture_setter(Ctx *c, uint64_t target) {
    assert(target==0x1234 && c->x[0]==CONTROLLER); ++setter_calls; allowed=(int)c->x[1];
}
int main(void) {
    int cases=0;
    for (option=-1; option<=1; ++option)
        for (native_allowed=0; native_allowed<=1; ++native_allowed)
            for (ready=0; ready<=1; ++ready)
              for (known=0; known<=1; ++known) {
                Ctx c={0}; c.x[19]=EVENT;
                readiness_calls=setter_calls=0; allowed=-1;
                f_80ac8650(&c);
                assert(readiness_calls==(native_allowed || (option>0 && known)));
                assert(setter_calls==1);
                assert(allowed==((native_allowed || (option>0 && known)) && ready));
                ++cases;
            }
    assert(cases==24); return 0;
}
'''
    source = '#include "a64.h"\n' + oot_patches.CUTSCENE_TIMELINE_HEAD + body + "\n}\n"
    patched = oot_patches.patch_cutscene_skip_timeline(source)
    assert patched is not None
    with tempfile.TemporaryDirectory(prefix="cutscene-timeline-") as tmp:
        folder = Path(tmp)
        (folder / "a64.h").write_text(header, encoding="utf-8")
        path = folder / "fixture.c"
        path.write_text(patched + fixture, encoding="utf-8")
        exe = folder / ("fixture.exe" if sys.platform == "win32" else "fixture")
        subprocess.run([cc, "-x", "c", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-I", tmp,
                        str(ROOT / "host" / "oot_cutscene_skip.c"), str(path), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cc", default="cc")
    args = parser.parse_args()
    check_host(args.cc)
    check_timeline_branch(args.cc)
    unittest.main(argv=[sys.argv[0]])
