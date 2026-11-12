// SPDX-License-Identifier: GPL-3.0-or-later
// (hookshot) A draw whose pipeline no list named (a state first seen in play) used to be left out while a worker made the
// pipeline, even where the driver's cache had it and answered in a fraction of a millisecond: a new game's opening drew 834
// such pipelines, all from the driver's cache, none compiled, and still left 1,906 draws out. Now the draw first asks the
// caches itself, without compiling, and goes on with the pipeline when they answer; only one that needs compiling goes to
// the worker and is left out, as before. This is how much of a frame those asks may take, on each recording thread.
// Nothing of Vulkan here: tests/test_first_ask.py.
#pragma once
#include <cstdint>

namespace first_ask {

// (the budget of a frame, on each recording thread. An ask answered by this process's own cache is about 0.27 ms (1,388 of them
// in a new game's opening; the slowest 6.2 ms), so what bounds a frame is their time, not their number.
// An ordinary frame keeps kBaseMs. A frame that is already long gets a share of its own length: new pipelines come in bursts
// when an area loads, in frames that take 100-370 ms for other reasons, and there a few ms served a few dozen of several
// hundred while the rest were left out for the frames it took the workers to make them. How long the frame is, is not known
// while it is drawn: the longer of the frame before it and of the time this one has taken so far stands for it.
// One ask is always allowed in a frame that has asked nothing yet.)
constexpr double kBaseMs = 3.0;    // (an ordinary frame's asks)
constexpr double kShare = 0.25;    // (of a long frame's length)
constexpr double kMostMs = 60.0;   // (however long the frame)

/// The time a frame's asks may take: frame_ms is the longer of the frame before and of this one so far
inline double limit_ms(double frame_ms) {
    const double share = frame_ms * kShare;
    return share < kBaseMs ? kBaseMs : share > kMostMs ? kMostMs : share;
}

struct Budget {
    uint64_t frame = ~0ull;
    int asks = 0;
    double ms = 0;
    /// Whether one more ask may begin in this frame
    bool allow(uint64_t now_frame, double frame_ms) {
        if (now_frame != frame) frame = now_frame, asks = 0, ms = 0;
        return asks == 0 || ms < limit_ms(frame_ms);
    }
    /// An ask that began took this long
    void spent(double took_ms) {
        asks++;
        ms += took_ms > 0 ? took_ms : 0;
    }
};

}   // namespace first_ask
