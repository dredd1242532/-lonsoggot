// Full host-import backing arithmetic. Guest ownership and pin counts live in the executable, not in this helper.
#pragma once
#include <cstdint>

namespace mkwvk::darwin_import {
struct Range { uint64_t lo = 0, hi = 0; };

inline bool rounded(uint64_t lo, uint64_t hi, uint64_t alignment, uint64_t end, Range& out) {
    if (!alignment || (alignment & (alignment - 1)) || hi <= lo || hi > end || hi > UINT64_MAX - (alignment - 1)) return false;
    out = {lo & ~(alignment - 1), (hi + alignment - 1) & ~(alignment - 1)};
    return out.hi <= end;
}
inline bool overlaps(Range a, Range b) { return a.lo < b.hi && b.lo < a.hi; }
} // namespace mkwvk::darwin_import
