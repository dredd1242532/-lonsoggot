"""Instruction-checked keyboard action hooks for the supported game. No native executable patching."""
import oot_wide
# All hooks must match together: partial input separation would leave a buffered roll on E.
SITES = (
    (0x80594e80, 0xf9402a68, "{ extern uint64_t hos_pc_input(Ctx*,uint64_t,uint64_t); c->x[28]=hos_pc_input(c,c->x[19],c->x[28]); }"),
    (0x8081c388, 0x39400108, "{ extern uint32_t hos_pc_roll(Ctx*,uint64_t,uint32_t,int); c->x[8]=hos_pc_roll(c,c->x[20],(uint32_t)c->x[8],0); }"),
    (0x8081c390, 0x39795a88, "{ extern uint32_t hos_pc_roll(Ctx*,uint64_t,uint32_t,int); c->x[8]=hos_pc_roll(c,c->x[20],(uint32_t)c->x[8],1); }"),
)
def install(lifter,img):
    try:
        if any(img.word(pc)!=word for pc,word,_ in SITES):
            print("PC controls: input sites differ; stock actions retained")
            return 0
    except Exception:
        return 0
    for pc,_,code in SITES:
        lifter.after[pc]=(lifter.after.get(pc,"")+"\n"+code).lstrip()
    print("PC controls: all three input hooks installed")
    return len(SITES)
