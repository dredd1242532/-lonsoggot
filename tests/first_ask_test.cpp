// SPDX-License-Identifier: GPL-3.0-or-later
// gpu/vk/first_ask.h alone (tests/test_first_ask.py)
#include "../gpu/vk/first_ask.h"
#include <cstdio>
#include <cstdlib>
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "CHECK failed line %d: %s\n", __LINE__, #c); std::exit(1); } } while (0)

// how many asks of `each` ms a frame of `frame_ms` begins
static int asks_in(double frame_ms, double each) {
    first_ask::Budget b;
    int n = 0;
    while (b.allow(1, frame_ms) && n < 100000) b.spent(each), n++;
    return n;
}

int main() {
    using first_ask::Budget;
    using first_ask::limit_ms;
    // the limit: an ordinary frame's base, a share of a long frame, never more than the most
    CHECK(limit_ms(0) == 3.0 && limit_ms(8.3) == 3.0 && limit_ms(12.0) == 3.0);
    CHECK(limit_ms(16.7) > 4.1 && limit_ms(16.7) < 4.2 && limit_ms(33.3) > 8.3 && limit_ms(33.3) < 8.4);
    CHECK(limit_ms(100) == 25.0 && limit_ms(240) == 60.0 && limit_ms(376) == 60.0 && limit_ms(5000) == 60.0);
    // asks of the measured cost (0.27 ms from this process's cache): 12 in a short frame, as the fixed 3 ms gave; the frames
    // that hit that limit with 1 to 21 draws left out were 20-35 ms long and now begin 19 to 33 asks
    CHECK(asks_in(10.0, 0.27) == 12);
    CHECK(asks_in(20.0, 0.27) == 19 && asks_in(25.0, 0.27) == 24 && asks_in(35.0, 0.27) == 33);
    // the bursts at an area's load, in frames of 122 to 376 ms: 113 to 223 asks in the frame, where 12 began before
    CHECK(asks_in(122.0, 0.27) == 113 && asks_in(187.0, 0.27) == 174 && asks_in(376.0, 0.27) == 223);
    {   // a new frame begins anew; a frame that has asked nothing may always ask once, whatever it is told of its length
        Budget b;
        while (b.allow(7, 10.0)) b.spent(0.27);
        CHECK(!b.allow(7, 10.0) && b.allow(8, 10.0));
        b.spent(0.27);
        CHECK(b.asks == 1 && b.frame == 8);
        Budget c;
        CHECK(c.allow(3, 0.0) && c.allow(3, -5.0));
    }
    {   // slow asks: none begins once the frame's time is spent; the one that ran over is the last, so a frame's worst is its
        // limit and one ask (6.2 ms was the slowest of 1,388)
        Budget b;
        CHECK(b.allow(1, 16.7));
        b.spent(2.0);
        CHECK(b.allow(1, 16.7));
        b.spent(2.0);
        CHECK(b.allow(1, 16.7));
        b.spent(6.2);
        CHECK(!b.allow(1, 16.7) && b.asks == 3 && b.ms <= limit_ms(16.7) + 6.2);
        Budget c;
        CHECK(c.allow(3, 16.7));
        c.spent(40.0);
        CHECK(!c.allow(3, 16.7) && c.allow(3, 400.0));   // (the frame turned out long: its share is larger)
        Budget d;
        CHECK(d.allow(3, 16.7));
        d.spent(-1.0);   // (a clock that went backwards costs nothing and still counts)
        CHECK(d.asks == 1 && d.ms == 0 && d.allow(3, 16.7));
    }
    {   // misses: asks no cache answers stop the frame's asks after three in a row, however long the frame; a cold driver
        // cache costs a frame three asks, not its whole share
        Budget b;
        int n = 0;
        while (b.allow(1, 376.0) && n < 1000) b.spent(0.4, false), n++;
        CHECK(n == first_ask::kMissesInARow && n == 3 && b.ms < 1.3);
        CHECK(b.stopped_by_misses_first() && !b.stopped_by_misses_first());   // (said once a frame)
        CHECK(b.allow(2, 376.0) && !b.stopped_by_misses_first());             // (the next frame asks again)
        // an answer begins the count again: two misses, a hit, two misses, a hit go on; three in a row stop
        Budget c;
        const bool seq[] = {false, false, true, false, false, true, false, false, false};
        int began = 0;
        for (bool answered : seq) {
            if (!c.allow(5, 376.0)) break;
            c.spent(0.3, answered), began++;
        }
        CHECK(began == 9 && !c.allow(5, 376.0) && c.stopped_by_misses_first());
        // a frame stopped by its time, not by misses, is not counted as one
        Budget d;
        while (d.allow(1, 10.0)) d.spent(0.27, true);
        CHECK(!d.stopped_by_misses_first());
        // one ask is always allowed in a frame that has asked nothing, and the hits' counts above are unchanged
        Budget e;
        CHECK(e.allow(9, 0.0));
        e.spent(0.4, false);
        CHECK(e.allow(9, 16.7) && e.misses == 1);
    }
    std::printf("first_ask: ok\n");
    return 0;
}
