"""nx2/split_giants.py: giant lifted functions cut at their labels into pieces and a dispatch loop, game-free.

Text checks: what is cut (pieces of about the size asked, every label in exactly one piece, the function's own name kept,
gotos into other pieces become returns of the label's id with their interrupt poll kept, guest returns and tail calls
become ids the dispatcher turns into `return` and TAILCALL), what is left whole and why (a top-level declaration, a label
inside a block, a literal, a goto to a label the function lacks, a function too small or below the size asked), and that
build.py's switch puts the flag on the lifted chunks only and its copy is what compile_one compiles.

Compiled check (with a C compiler: PATH, $CC, or the build's LONGSHOT_MINGW toolchain): random functions in the lifter's
shapes (blocks with their own locals, guest loads and stores, conditional gotos forward and back, loops whose back edges
poll, a jump-table switch with an indirect jump as its default, calls, tail calls, returns), each compiled whole and
split (1 KB pieces) into one program against the build's own guest model (nx2/rtgen.py's a64.h), run from the same random
states: the context, the guest memory, the page marks and the sequence of calls out (each with a hash of the context)
must be equal.

    python -I -B tests/test_split_giants.py [--cc clang]
"""
import argparse
import os
import random
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "nx2"))
import split_giants as SG  # noqa: E402

FAILS = []


def check(ok, what):
    if not ok:
        FAILS.append(what)
        print("FAIL: " + what)


def gen_function(name, rng, blocks=60):
    """A lifted-shaped function: blocks of statements, each led by a label; x28 counts down so every loop ends"""
    labels = ["L_%08x" % (0x1000 + 4 * i) for i in range(blocks)]
    out = ["void %s(Ctx *__restrict A64_NOESC c) {" % name]
    for i, lab in enumerate(labels):
        out.append("%s:;" % lab if rng.random() > 0.15 else "%s:; A64_POLL(c);" % lab)
        for _ in range(rng.randint(3, 12)):
            d, n, m = rng.randint(0, 27), rng.randint(0, 27), rng.randint(0, 27)
            k = rng.random()
            if k < 0.3:
                out.append("    { uint64_t _a=c->x[%d], _b=c->x[%d]; uint64_t _r=_a%s_b; c->x[%d]=_r; }" % (n, m, rng.choice("+-^&|"), d))
            elif k < 0.45:
                out.append("    { uint64_t _r = ((uint64_t)0 | ((c->x[%d]) << %d)); _r &= 0xFFFFFFFFULL; c->x[%d] = _r; }" % (n, rng.randint(0, 5), d))
            elif k < 0.6:
                out.append("    c->x[%d]=recomp_load32(c,(c->x[%d] & 0xFFF0) + %d);" % (d, n, 4 * rng.randint(0, 3)))
            elif k < 0.72:
                out.append("    { uint64_t _b=c->x[%d]; int64_t _o=%d; recomp_store64(c,((_b & 0xFFF0)+_o),c->x[%d]); }" % (n, 8 * rng.randint(0, 1), m))
            elif k < 0.8:
                out.append("    { uint32_t _n[4],_r[4]; memcpy(_n,c->vreg[%d],16); for(unsigned _i=0;_i<4;++_i) _r[_i]=_n[_i]*3u+%du; memcpy(c->vreg[%d],_r,16); }"
                           % (rng.randint(0, 7), rng.randint(0, 9), rng.randint(0, 7)))
            elif k < 0.87:
                out.append("    { uint64_t _a=c->x[%d], _b=c->x[%d]; uint64_t _r=_a-_b; c->n=(int64_t)_r<0; c->z=_r==0; c->c=_a>=_b; c->v=0; }" % (n, m))
            elif k < 0.89:   # (PR #38's fast float region: one statement with its own label and a goto to it)
                r_ = rng.randint(0, 27)
                lab_ff = "L_ff_%08x" % (0x5000 + 4 * i + 0x100 * _)
                out.append("    { if (c->x[%d] & 1) { const uint64_t _ffx%d = c->x[%d]; c->x[%d] += 7;\n"
                           "if (__builtin_expect((c->x[%d] & 2) != 0, 1)) goto %s;\nc->x[%d] = _ffx%d; }\n"
                           "    c->x[%d] ^= 5;\n%s:; }" % (n, r_, r_, r_, r_, lab_ff, r_, r_, r_, lab_ff))
            elif k < 0.92:
                out.append("    c->x[30] = 0x%xULL; f_callee(c);" % (0x2000 + i))
            else:
                out.append("    { uint64_t _t = c->x[%d]; c->x[30] = 0x%xULL; A64_ICALL(c, _t); }" % (n, 0x3000 + i))
        r = rng.random()
        if i + 1 == blocks:
            out.append("return;")
        elif r < 0.25 and i > 2:   # a loop back
            out.append("{ c->x[28] -= 1; }")
            out.append("if (c->x[28] < 0x10000 && c->x[28] != 0) { { A64_POLL(c); goto %s; } }" % labels[rng.randint(max(0, i - 8), i)])
        elif r < 0.45:
            out.append("if %s { goto %s; }" % (rng.choice(["(c->z)", "(!c->z)", "(c->c && !c->z)", "(c->n != c->v)"]),
                                               labels[rng.randint(i + 1, min(blocks - 1, i + 15))]))
        elif r < 0.52:
            tg = sorted(set(rng.randint(i + 1, blocks - 1) for _ in range(4)))
            out.append("switch (c->x[%d] & 7) { %s default: A64_IJUMP(c, c->x[%d]); return; }"
                       % (rng.randint(0, 27), " ".join("case 0x%xULL: goto %s;" % (j, labels[t]) for j, t in enumerate(tg)),
                          rng.randint(0, 27)))
        elif r < 0.56:
            out.append("if (c->x[%d] & 1) { TAILCALL(f_tail); }" % rng.randint(0, 27))
        elif r < 0.6:
            out.append("if ((uint32_t)c->x[%d] == 0) { return; }" % rng.randint(0, 27))
    out.append("}")
    return "\n".join(out)


def text_checks():
    rng = random.Random(1)
    fn = gen_function("f_00001000", rng)
    src = '#include "a64.h"\nvoid f_callee(Ctx *A64_NOESC);\nvoid f_tail(Ctx *A64_NOESC);\n' + fn + "\n"
    out, st = SG.split_text(src, piece_kb=1, min_kb=0)
    check(st["functions_split"] == 1 and st["pieces"] >= 4, "a 60-block function cut into pieces: %s" % dict(st))
    check(out.count("\nvoid f_00001000(Ctx *__restrict A64_NOESC c) {") == 1, "the function keeps its name and ABI once")
    for i in range(60):
        check(out.count("L_%08x:;" % (0x1000 + 4 * i)) == 1, "label %d in exactly one piece" % i)
    check(("TAILCALL(f_tail);" in fn) == ("TAILCALL(f_tail);" in out.split("\nvoid f_00001000(")[1]),
          "a tail call leaves from the dispatcher")
    check("TAILCALL" not in out.split("\nvoid f_00001000(")[0], "no tail call left in a piece")
    check("return;" not in out.split("\nvoid f_00001000(")[0], "no bare return left in a piece")
    check("A64_POLL(c); return " in out or "A64_POLL(c); goto" in out, "polls kept on back edges")
    check("L_ff_" in fn and out.count(":; }") == fn.count(":; }"), "fast float regions kept whole, their labels inside them")
    whole, st2 = SG.split_text(src, piece_kb=1, min_kb=1024)
    check(whole == src and not st2["functions_split"], "a function below the size asked stays as it is")
    for bad, why in (("    uint64_t x8_ = c->x[8];", "a top-level declaration"),
                     ('    hos_log(c, "a{");', "literal"),
                     ("    { if (c->z) { L_0000dead:; } }", "label inside a block"),
                     ("    if (c->z) { goto L_ffffffff; }", "label the function does not have"),
                     ("    if (c->z) { goto *c; }", "goto of another shape"),
                     ("    if (c->z) { goto L_ff_00000001; }\n    { c->x[1] = 0; L_ff_00000001:; }", "goto of another shape")):
        lines = fn.split("\n")
        lines.insert(len(lines) // 2, bad)
        notes = []
        o, s = SG.split_text('#include "a64.h"\n' + "\n".join(lines) + "\n", piece_kb=1, min_kb=0, notes=notes)
        check(not s["functions_split"] and s["functions_left_whole"] == 1, "left whole: " + why)
        check(any(why in n for n in notes), "the note says why (%s): %s" % (why, notes))
    # build.py: the switch, the flag on the lifted chunks only, and the copy compile_one compiles
    import build
    old = os.environ.get("LONGSHOT_SPLIT_GIANTS")
    try:
        os.environ["LONGSHOT_SPLIT_GIANTS"] = "0"
        check(build.split_giants_flags() == [], "LONGSHOT_SPLIT_GIANTS=0: no flag")
        os.environ["LONGSHOT_SPLIT_GIANTS"] = "16"
        f = build.split_giants_flags()
        check(len(f) == 1 and f[0].startswith(SG.FLAG + "16_"), "LONGSHOT_SPLIT_GIANTS=16: the flag names the size: %s" % f)
    finally:
        if old is None:
            os.environ.pop("LONGSHOT_SPLIT_GIANTS", None)
        else:
            os.environ["LONGSHOT_SPLIT_GIANTS"] = old
    big = '#include "a64.h"\nvoid f_callee(Ctx *A64_NOESC);\nvoid f_tail(Ctx *A64_NOESC);\n' + gen_function("f_00002000", random.Random(2), 600) + "\n"
    check(len(big) > SG.MIN_KB * 1024, "the big test function is past MIN_KB")
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "code_000.c")
        open(p, "w", newline="\n").write(big)
        obj = os.path.join(d, "code_000.o")
        copy = SG.compile_copy(p, obj, ["-O1", SG.flag(1)])
        check(copy == obj[:-2] + ".split.c" and os.path.exists(copy), "compile_copy writes the split copy beside the object")
        check(SG.compile_copy(p, obj, ["-O1"]) == p, "no flag: the lifted file itself")
        small = os.path.join(d, "code_001.c")
        open(small, "w", newline="\n").write(src)
        check(SG.compile_copy(small, obj, ["-O1", SG.flag(8)]) == small, "no function past MIN_KB: the lifted file itself")
        check(build.mem_need(copy) < build.mem_need(p), "the memory estimate counts the pieces as functions")


DRIVER = r"""
#include <stdio.h>
#include <stdlib.h>
#include "a64.h"
uint8_t guest_mem[1 << 16];
uint8_t a64_dirty[A64_DIRTY_PAGES];
void f_00000001(Ctx *A64_NOESC c);
void f_00000002(Ctx *A64_NOESC c);
static uint64_t log_[4096]; static int nlog;
static uint64_t h(const Ctx *c) { uint64_t x = 1469598103934665603ULL; for (int i = 0; i < 32; i++) x = (x ^ c->x[i]) * 1099511628211ULL;
    for (int i = 0; i < 8; i++) x = (x ^ c->vreg[i][0] ^ c->vreg[i][1]) * 1099511628211ULL; return x ^ c->n ^ c->z << 1 ^ c->c << 2 ^ c->v << 3; }
static void ev(Ctx *c, uint64_t kind) { if (nlog < 4096) log_[nlog++] = kind ^ h(c); c->x[0] = h(c) & 0xffff; }
void f_callee(Ctx *A64_NOESC c) { ev(c, 1); }
void f_tail(Ctx *A64_NOESC c) { ev(c, 2); }
void a64_icall_site(Ctx *A64_NOESC c, uint64_t t, uint64_t *s) { ev(c, 3 + (t << 8)); }
void a64_ijump_site(Ctx *A64_NOESC c, uint64_t t, uint64_t *s) { ev(c, 4 + (t << 8)); }
void a64_icall(Ctx *A64_NOESC c, uint64_t t) { ev(c, 3 + (t << 8)); }
void a64_ijump(Ctx *A64_NOESC c, uint64_t t) { ev(c, 4 + (t << 8)); }
A64Fn a64_lookup(uint64_t a) { return 0; }
static int polls;
void a64_poll_cur(void) { polls++; }
void a64_poll(Ctx *A64_NOESC c) { polls++; }
int main(void) {
    static uint8_t mem0[1 << 16], mem1[1 << 16]; static uint64_t log0[4096];
    int bad = 0;
    for (int s = 1; s <= 300; s++) {
        Ctx st; memset(&st, 0, sizeof st); srand(s);
        for (int i = 0; i < 32; i++) st.x[i] = ((uint64_t)rand() << 20) ^ rand();
        for (int i = 0; i < 32; i++) { st.vreg[i][0] = rand(); st.vreg[i][1] = (uint64_t)rand() << 33; }
        st.x[28] = 40 + rand() % 40; st.n = rand() & 1; st.z = rand() & 1; st.c = rand() & 1; st.v = rand() & 1; st.poll = s & 1;
        for (size_t i = 0; i < sizeof mem0; i++) mem0[i] = (uint8_t)rand();
        Ctx a = st, b = st; int na, pa, pb;
        memcpy(guest_mem, mem0, sizeof mem0); memset(a64_dirty, 0, 64); nlog = 0; polls = 0;
        f_00000001(&a); na = nlog; pa = polls; memcpy(log0, log_, sizeof log_); memcpy(mem1, guest_mem, sizeof mem1);
        uint8_t d0[64]; memcpy(d0, a64_dirty, 64);
        memcpy(guest_mem, mem0, sizeof mem0); memset(a64_dirty, 0, 64); nlog = 0; polls = 0;
        f_00000002(&b); pb = polls;
        if (memcmp(&a, &b, sizeof a) || na != nlog || memcmp(log0, log_, na * 8) || memcmp(mem1, guest_mem, sizeof mem1)
            || memcmp(d0, a64_dirty, 64) || pa != pb) { printf("state %d differs\n", s); bad++; }
    }
    printf("%d of 300 states differ\n", bad);
    return bad != 0;
}
"""


def find_cc(asked):
    if asked:
        return asked
    for c in (os.environ.get("CC"), "clang", "gcc"):
        if c and shutil.which(c):
            return c
    m = os.environ.get("LONGSHOT_MINGW")
    if m:
        for n in ("x86_64-w64-mingw32-clang.exe", "clang.exe"):
            p = os.path.join(m, "bin", n)
            if os.path.exists(p):
                return p
    return None


def compiled_checks(cc):
    import rtgen
    import kit
    with tempfile.TemporaryDirectory() as d:
        rt = rtgen.write(os.path.join(d, "rt64"))
        for h in ("a64_suyu.h", "a64_fpx.h"):   # (the generated FP helpers: none of the shapes here call them)
            open(os.path.join(d, h), "w").write("")
        open(os.path.join(d, "pre.h"), "w").write("#include <stdint.h>\nextern uint8_t guest_mem[];\n")
        for k in range(4):
            rng = random.Random(100 + k)
            fn = gen_function("f_00000001", rng, blocks=50 + 20 * k)
            head = '#include "a64.h"\nvoid f_callee(Ctx *A64_NOESC);\nvoid f_tail(Ctx *A64_NOESC);\n'
            open(os.path.join(d, "orig.c"), "w", newline="\n").write(head + fn + "\n")
            out, st = SG.split_text(head + fn + "\n", piece_kb=1, min_kb=0)
            check(st["functions_split"] == 1, "program %d split" % k)
            open(os.path.join(d, "split.c"), "w", newline="\n").write(out.replace("f_00000001(", "f_00000002(").replace("s_00000001_", "s_00000002_"))
            open(os.path.join(d, "driver.c"), "w", newline="\n").write(DRIVER)
            exe = os.path.join(d, "t%d.exe" % k)
            fl = ["-O1", "-ffp-contract=off", "-fno-strict-aliasing", "-I" + rt, "-I" + d, "-I" + os.path.join(kit.KIT, "rt"),
                  "-DA64_POLLING", "-DA64_WRITE_TRACK", "-DA64_HOST_BASE=((uintptr_t)guest_mem)", "-include", os.path.join(d, "pre.h"), "-Wno-everything",
                  "-mllvm", "-rotation-max-header-size=0"]
            if "mingw" in cc:
                fl = ["--target=x86_64-w64-mingw32"] + fl
            r = subprocess.run([cc] + fl + [os.path.join(d, f) for f in ("orig.c", "split.c", "driver.c")] + ["-o", exe],
                               capture_output=True, text=True)
            check(r.returncode == 0, "program %d compiles: %s" % (k, (r.stdout + r.stderr)[-2000:]))
            if r.returncode:
                continue
            r = subprocess.run([exe], capture_output=True, text=True, timeout=120)
            print("program %d (%d pieces): %s" % (k, st["pieces"], r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.returncode))
            check(r.returncode == 0, "program %d: the split function runs as the whole one" % k)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cc")
    a = ap.parse_args()
    text_checks()
    cc = find_cc(a.cc)
    if cc and sys.maxsize > 2 ** 32:
        compiled_checks(cc)
    else:
        print("compiled checks skipped: no C compiler")
    print("split_giants: %s" % ("FAILED (%d)" % len(FAILS) if FAILS else "OK"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
