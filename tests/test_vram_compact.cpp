// The compaction's rules (gpu/vk/vram_compact.h): tests/test_vram_compact.py compiles and runs this.
#include "../gpu/vk/vram_compact.h"

#include <cstdio>
#include <cstdlib>

#define CHECK(x) do { if (!(x)) { std::printf("FAIL line %d: %s\n", __LINE__, #x); std::exit(1); } } while (0)

int main() {
    using namespace vram_compact;
    const uint64_t MB = 1ull << 20;
    // on unless RX_VK_COMPACT says 0: not set is on (the default), and 1 still is
    CHECK(enabled(nullptr));
    CHECK(enabled("1") && enabled("2"));
    CHECK(!enabled("0") && !enabled(""));
    // pressed: the driver's count over 90% of what it allows; never without an allowance
    CHECK(!pressed(9000 * MB, 10900 * MB));
    CHECK(pressed(10023 * MB, 10900 * MB));   // (the uncompacted walk's count)
    CHECK(!pressed(10023 * MB, 0));
    // a run starts for RX_VK_COMPACT_MB of free space, for 192 MB while pressed, and never for more than was asked
    CHECK(run_from(512 * MB, false) == 512 * MB);
    CHECK(run_from(512 * MB, true) == 192 * MB);
    CHECK(run_from(64 * MB, true) == 64 * MB);
    CHECK(rest(false) == 600 && rest(true) == 120);
    // only the textures' pool's free space comes off the count
    CHECK(reclaimable(850 * MB, 215 * MB) == 215 * MB);    // (the rest is in the render targets' and the buffers' blocks)
    CHECK(reclaimable(100 * MB, 215 * MB) == 100 * MB);    // (never more than the blocks hold free in all)
    // the budget holds the count, less what a run gives back, at pct% of the allowance
    const uint64_t allowed = 10900 * MB, budget = 6144 * MB;
    CHECK(held_budget(budget, 5000 * MB, 9000 * MB, 200 * MB, allowed, 85) == budget);   // (8,800 of 9,265: under the line)
    CHECK(held_budget(budget, 5600 * MB, 10023 * MB, 215 * MB, allowed, 85) == 5600 * MB - (10023 - 215 - 9265) * MB);
    CHECK(held_budget(budget, 5600 * MB, 10023 * MB, 215 * MB, allowed, 90) == budget);   // (9,808 of 9,810: at 90 that count is let be)
    CHECK(held_budget(budget, 5600 * MB, 10323 * MB, 215 * MB, allowed, 90) == 5600 * MB - (10323 - 215 - 9810) * MB);
    CHECK(held_budget(4096 * MB, 5600 * MB, 10023 * MB, 215 * MB, allowed, 85) == 4096 * MB);   // (never more than it was)
    CHECK(held_budget(budget, 300 * MB, 10800 * MB, 0, allowed, 85) == 0);                      // (the excess is more than the textures hold)
    CHECK(held_budget(budget, 5600 * MB, 10023 * MB, 215 * MB, 0, 85) == budget);               // (no allowance known: left alone)
    CHECK(held_budget(budget, 5600 * MB, 100 * MB, 215 * MB, allowed, 85) == budget);           // (more to give back than is used)
    // the hold's pass keeps a live texture used in the last ten seconds: the eviction's own 60 presents is not enough for it
    CHECK(kHoldUnused == 600);
    CHECK(hold_keeps(0) && hold_keeps(60) && hold_keeps(599));   // (out of view for a second: a turning camera brings it back)
    CHECK(!hold_keeps(600) && !hold_keeps(3600));
    // the reserve: a line five points under the hold's, for the textures unused thirty seconds
    CHECK(reserve_pct(85) == 80 && reserve_pct(50) == 45 && reserve_pct(3) == 0);
    CHECK(kReserveUnused == 1800 && kReserveUnused > kHoldUnused);
    const uint64_t none = UINT64_MAX;   // (the other rules ask nothing: the hold's two lines alone)
    CHECK(hold_target(59, 60, none, 5000 * MB, 4500 * MB) == UINT64_MAX);   // (used a second ago: stays, whatever the lines ask)
    CHECK(hold_target(599, 60, none, 5000 * MB, 4500 * MB) == UINT64_MAX);
    CHECK(hold_target(600, 60, none, 5000 * MB, 4500 * MB) == 5000 * MB);   // (unused ten seconds: for the hold's own line)
    CHECK(hold_target(1799, 60, none, 5000 * MB, 4500 * MB) == 5000 * MB);
    CHECK(hold_target(1800, 60, none, 5000 * MB, 4500 * MB) == 4500 * MB);  // (unused thirty: down to the reserve's line)
    CHECK(hold_target(600, 60, none, 0, 4500 * MB) == UINT64_MAX);          // (the hold's line not crossed: only the long unused go)
    CHECK(hold_target(1800, 60, none, 0, 4500 * MB) == 4500 * MB);
    // the other rules' part: over the budget's size, over 90% of the allowance or at the driver's limit, a texture unused for
    // the eviction's own 60 presents goes for what those rules ask, as with the compaction off; the hold's patience is for its
    // own lines
    CHECK(rule_target(5600 * MB, 6144 * MB) == UINT64_MAX);                        // (under the budget: they ask nothing)
    CHECK(rule_target(5600 * MB, 5600 * MB) == UINT64_MAX);
    CHECK(rule_target(5600 * MB, 5300 * MB) == 5300 * MB / 8 * 7);                 // (over it: cut to 7/8 of it, as ever)
    // told apart: the budget's size still asks for its 7/8; the 90% rule's and the limit's budgets ask for their excess and
    // kExcessSlack more (the Market on an 8 GB card: 2,873 MB of textures, 62 MB over the line), but only for a small excess, up
    // to a sixteenth of their budget: a larger one is cut to 7/8 of it, as before (the Lake: 383 MB over 3,639)
    CHECK(rule_target(2873 * MB, 3285 * MB, 3285 * MB) == UINT64_MAX);             // (no rule asks anything)
    CHECK(rule_target(2873 * MB, 3285 * MB, 2811 * MB) == 2811 * MB - kExcessSlack);   // (the excess: 126 MB go, not 413)
    CHECK(rule_target(2873 * MB, 3285 * MB, 2811 * MB) > rule_target(2873 * MB, 2811 * MB));
    CHECK(rule_target(2873 * MB, 2560 * MB, 2560 * MB) == 2560 * MB / 8 * 7);      // (over its size: 7/8 of it, as before)
    CHECK(rule_target(2600 * MB, 2560 * MB, 2560 * MB) == 2560 * MB / 8 * 7);      // (a small excess over its size too)
    CHECK(rule_target(4022 * MB, 4096 * MB, 3639 * MB) == 3639 * MB / 8 * 7);      // (the Lake: cut, not trimmed to the line)
    CHECK(rule_target(2873 * MB, 2560 * MB, 2500 * MB) == 2500 * MB / 8 * 7);      // (both, 373 MB over the rule's: the lower)
    CHECK(rule_target(2873 * MB, 2880 * MB, 2200 * MB) == 2200 * MB / 8 * 7);      // (673 MB over: cut)
    CHECK(rule_target(100 * MB, 3285 * MB, 32 * MB) == 32 * MB / 8 * 7);
    CHECK(rule_target(3 * MB, 3285 * MB, 0) == 0 && rule_target(0, 3285 * MB, 0) == UINT64_MAX);   // (no budget: all of it)
    {   // a grid of rule budgets and excesses over them, the edge between small and large at each, with the budget's size far
        // above (it asks nothing), at the rule's budget (its 7/8 alone) and just above it (the lower of the two)
        const uint64_t budgets[] = {0, 1, 8, MB, 32 * MB, 256 * MB, 511 * MB, 512 * MB, 513 * MB, 1024 * MB, 2811 * MB,
                                    3639 * MB, 4096 * MB, 6144 * MB, 16384 * MB};
        for (const uint64_t b : budgets) {
            const uint64_t edge = b / kExcessSmall, far = 4 * b + 1024 * MB;   // (the largest small excess; a size never reached)
            uint64_t ex[] = {0, 1, MB, 62 * MB, edge ? edge - 1 : 0, edge, edge + 1, edge + MB, kExcessSlack, 383 * MB, b / 8, b,
                             2 * b + 1};
            std::sort(ex, ex + sizeof ex / sizeof *ex);
            CHECK(rule_target(b - std::min(b, MB), far, b) == UINT64_MAX);   // (under the budget: nothing)
            CHECK(rule_target(b - std::min(b, MB), b, b) == UINT64_MAX);
            uint64_t last = UINT64_MAX;
            bool last_small = false;
            for (const uint64_t e : ex) {
                const uint64_t t = b + e, to = rule_target(t, far, b), cut = rule_target(t, b);
                const bool small = e && e <= edge;
                CHECK(cut == (e ? b / 8 * 7 : UINT64_MAX));   // (RX_VK_EVICT_EXCESS=0: 7/8 at every point, as before)
                if (!e) CHECK(to == UINT64_MAX);
                else if (small) CHECK(to == b - std::min(b, kExcessSlack) && to < b);   // (small: the excess and 64 MB, as before)
                else CHECK(to == cut);                                                   // (large: cut to 7/8)
                if (!(last_small && !small)) CHECK(to <= last);   // (within each regime more excess never asks for less)
                last = to, last_small = small;
                CHECK(rule_target(t, b, b) == cut);                                   // (the size alone: its 7/8, small or large)
                CHECK(rule_target(t, b + MB, b) == std::min(rule_target(t, b + MB), to));
            }
            // at the edge the target drops (the trim takes less than the cut) where 64 MB is under an eighth of the budget; under
            // 512 MB the trim takes more and the target rises there
            if (edge) CHECK((rule_target(b + edge + 1, far, b) <= rule_target(b + edge, far, b)) == (b >= 8 * kExcessSlack));
        }
    }
    CHECK(hold_target(100, 60, 4637 * MB, 5000 * MB, 4500 * MB) == 4637 * MB);     // (the count over 90%: one unused 100 presents still goes)
    CHECK(hold_target(59, 60, 4637 * MB, 5000 * MB, 4500 * MB) == UINT64_MAX);     // (one used in the last 60: never)
    CHECK(hold_target(100, 120, 4637 * MB, 5000 * MB, 4500 * MB) == UINT64_MAX);   // (nor before the pass's own time, whatever that is)
    CHECK(hold_target(700, 60, 5200 * MB, 5000 * MB, 4500 * MB) == 5000 * MB);     // (the lowest of the lines that apply)
    CHECK(hold_target(700, 60, 4000 * MB, 5000 * MB, 4500 * MB) == 4000 * MB);
    CHECK(hold_target(2000, 60, 4000 * MB, 5000 * MB, 4500 * MB) == 4000 * MB);
    CHECK(hold_target(2000, 60, 5200 * MB, 5000 * MB, 4500 * MB) == 4500 * MB);
    CHECK(hold_target(700, 60, 4000 * MB, 0, 4500 * MB) == 4000 * MB);             // (the hold's line not crossed, the rules asking)
    {   // a count at 94% of a 7,000 MB allowance (6,600 MB used, 100 of it to give back; the textures hold 2,400): the 90% rule's
        // budget is 2,200 MB and a pass of its own goes to 1,925; the hold and the reserve ask for less still, so the pass is the
        // hold's. A texture unused 100 presents goes until the 90% rule's part is covered, and no further
        const uint64_t a = 7000 * MB, total = 2400 * MB;
        const uint64_t rule = total - (6500 - 6300) * MB;   // (the 90% rule: the excess over 6,300 MB off the textures)
        const uint64_t hold = held_budget(rule, total, 6600 * MB, 100 * MB, a, 85);
        const uint64_t res = held_budget(hold, total, 6600 * MB, 100 * MB, a, reserve_pct(85));
        CHECK(hold == total - (6500 - 5950) * MB && res == total - (6500 - 5600) * MB);
        const uint64_t to = hold_target(100, 60, rule_target(total, rule), hold, res);
        CHECK(to == 2200 * MB / 8 * 7);
        CHECK(!hold_met(total, 0, 0, to) && !hold_met(total, 400 * MB, 0, to) && hold_met(total, 475 * MB, 0, to));
        CHECK(hold_met(total, 100 * MB, 375 * MB, to));   // (the dead render targets count for it first, as for the other lines)
        CHECK(hold_target(700, 60, rule_target(total, rule), hold, res) == hold);    // (older ones go on to the hold's line)
        CHECK(hold_target(1800, 60, rule_target(total, rule), hold, res) == res);    // (and the long unused to the reserve's)
        CHECK(hold_target(100, 60, rule_target(2100 * MB, rule), hold, res) == UINT64_MAX);   // (under that budget: the hold's patience again)
    }
    // a count between the two lines: the reserve asks for less than the budget, the hold for nothing
    CHECK(held_budget(budget, 5000 * MB, 9000 * MB, 200 * MB, allowed, 85) == budget);
    CHECK(held_budget(budget, 5000 * MB, 9000 * MB, 200 * MB, allowed, reserve_pct(85)) == 5000 * MB - (9000 - 200 - 8720) * MB);
    // and it is done with live textures once those chosen and the dead render targets together cover the excess
    CHECK(!hold_met(5600 * MB, 0, 0, 5000 * MB));                 // (600 MB over, nothing chosen yet)
    CHECK(!hold_met(5600 * MB, 599 * MB, 0, 5000 * MB));
    CHECK(hold_met(5600 * MB, 600 * MB, 0, 5000 * MB));
    CHECK(hold_met(5600 * MB, 0, 600 * MB, 5000 * MB));           // (the dead targets alone cover it: no live texture goes)
    CHECK(hold_met(5600 * MB, 0, 2500 * MB, 5000 * MB));          // (the walk's 2.5 GB of dead targets)
    CHECK(!hold_met(5600 * MB, 100 * MB, 400 * MB, 5000 * MB));   // (100 MB still to go)
    CHECK(hold_met(5600 * MB, 200 * MB, 400 * MB, 5000 * MB));
    CHECK(hold_met(300 * MB, 200 * MB, 200 * MB, 0));             // (more chosen than the textures hold, their sum having drifted)
    std::printf("PASS: the compaction's rules\n");
    return 0;
}
