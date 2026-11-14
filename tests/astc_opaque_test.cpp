// astc_opaque (gpu/vk/astc_opaque.h): which ASTC blocks are provably opaque. Hand-built blocks of each kind, the walk of a
// level's blocks in its block-linear storage, and random blocks against the tree's ASTC decoder: a block judged opaque
// must decode to alpha 255 in every texel. tests/test_astc_opaque.py builds it (the decoder's folders as system ones).
#include "../gpu/vk/astc_opaque.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

#include <video_core/textures/astc.cpp>   // (the tree's decoder, whole: gpu/third_party/suyu)

namespace fixture {

int failures = 0;
void expect(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        failures++;
    }
}

struct Block {
    uint8_t b[16] = {};
    /// bits [at, at + n) = v
    Block& set(uint32_t at, uint32_t n, uint32_t v) {
        for (uint32_t i = 0; i < n; i++) {
            const uint32_t bit = at + i;
            b[bit >> 3] = (uint8_t)((b[bit >> 3] & ~(1u << (bit & 7))) | (((v >> i) & 1u) << (bit & 7)));
        }
        return *this;
    }
};

bool opaque(const Block& k, uint32_t bw = 4, uint32_t bh = 4) {
    return astc_opaque::block(k.b, bw, bh);
}

/// The least alpha the tree's decoder gives the block's bw x bh texels
int decoded_alpha(const Block& k, uint32_t bw = 4, uint32_t bh = 4) {
    uint8_t out[12 * 12 * 4];
    std::memset(out, 0, sizeof out);
    Tegra::Texture::ASTC::DecompressSerial(std::span<const uint8_t>(k.b, 16), bw, bh, 1, bw, bh,
                                           std::span<uint8_t>(out, (size_t)bw * bh * 4));
    int least = 255;
    for (uint32_t i = 0; i < bw * bh; i++) least = std::min<int>(least, out[i * 4 + 3]);
    return least;
}

/// Opaque by the header, and by the decoder too
void expect_opaque(const Block& k, const char* what, uint32_t bw = 4, uint32_t bh = 4) {
    expect(opaque(k, bw, bh), what);
    if (opaque(k, bw, bh)) expect(decoded_alpha(k, bw, bh) == 255, what);
}

// The modes without alpha, from the specification's table of colour endpoint modes: 0-3 luminance (LDR, LDR, HDR, HDR),
// 4 and 5 luminance + alpha, 6 RGB scale, 7 HDR RGB scale, 8 and 9 RGB, 10 RGB scale + two alphas, 11 HDR RGB, 12 and 13
// RGBA, 14 HDR RGB + LDR alpha, 15 HDR RGB + HDR alpha
const bool kNoAlpha[16] = {true, true, true, true, false, false, true, true, true, true, false, true, false, false, false, false};

// Block mode 0x42: a 4 x 4 weight grid, weights of 2 bits, one plane (32 bits of weights). 0x442: the same with two
// planes (64 bits of weights, and the plane's 2-bit component selector below them)
constexpr uint32_t kMode = 0x42, kDual = 0x442;

Block one_part(uint32_t cem, uint32_t mode = kMode) {
    return Block().set(0, 11, mode).set(13, 4, cem);
}
/// 2 to 4 partitions of one mode: the partition index at 13, the 6-bit field at 23 with its low two bits 0
Block parts_same(uint32_t parts, uint32_t cem, uint32_t mode = kMode) {
    return Block().set(0, 11, mode).set(11, 2, parts - 1).set(13, 10, 0x155).set(23, 2, 0).set(25, 4, cem);
}
/// Two partitions of modes (cls + c0) * 4 + m0 and (cls + c1) * 4 + m1: the field holds the class, both c and m0; m1
/// is the two bits right below the weights (wbits of them at the block's top)
Block two_modes(uint32_t cls, uint32_t c0, uint32_t m0, uint32_t c1, uint32_t m1, uint32_t mode = kMode, uint32_t wbits = 32) {
    return Block().set(0, 11, mode).set(11, 2, 1).set(13, 10, 0x2AA).set(23, 2, cls + 1).set(25, 1, c0).set(26, 1, c1)
        .set(27, 2, m0).set(128 - wbits - 2, 2, m1);
}
/// Three partitions on mode 0x42: the class and the three c in the field with m0's low bit; m0's high bit, m1 and m2 in
/// the five bits below the weights (91 to 95)
Block three_modes(uint32_t cls, uint32_t c0, uint32_t c1, uint32_t c2, uint32_t m0, uint32_t m1, uint32_t m2) {
    const uint32_t all = (cls + 1) | c0 << 2 | c1 << 3 | c2 << 4 | m0 << 5 | m1 << 7 | m2 << 9;
    return Block().set(0, 11, kMode).set(11, 2, 2).set(13, 10, 0x0F3).set(23, 6, all & 63).set(91, 5, all >> 6);
}
/// A void-extent block (LDR): bits 0-8 0x1FC, bit 9 0, bits 10 and 11 ones; four 13-bit coordinates; R, G, B, A
Block void_extent(uint32_t alpha, uint32_t s0 = 0x1FFF, uint32_t s1 = 0x1FFF, uint32_t t0 = 0x1FFF, uint32_t t1 = 0x1FFF) {
    return Block().set(0, 12, 0xDFC).set(12, 13, s0).set(25, 13, s1).set(38, 13, t0).set(51, 13, t1)
        .set(64, 16, 0x1234).set(80, 16, 0xABCD).set(96, 16, 0x8000).set(112, 16, alpha);
}

void hand_built() {
    // each colour endpoint mode, in one partition and in several, with one plane and two
    for (uint32_t cem = 0; cem < 16; cem++) {
        char what[96];
        std::snprintf(what, sizeof what, "colour endpoint mode %u, one partition", cem);
        expect(opaque(one_part(cem)) == kNoAlpha[cem], what);
        if (kNoAlpha[cem]) expect_opaque(one_part(cem), what);
        std::snprintf(what, sizeof what, "colour endpoint mode %u, one partition, two planes", cem);
        expect(opaque(one_part(cem, kDual)) == kNoAlpha[cem], what);
        if (kNoAlpha[cem]) expect_opaque(one_part(cem, kDual), what);
        for (uint32_t parts = 2; parts <= 4; parts++) {
            // (four partitions of an RGB mode are 24 colour values, over the 18 a block may have: not a legal block)
            const bool want = kNoAlpha[cem] && (parts < 4 || cem < 8);
            std::snprintf(what, sizeof what, "colour endpoint mode %u in each of %u partitions", cem, parts);
            expect(opaque(parts_same(parts, cem)) == want, what);
            if (want) expect_opaque(parts_same(parts, cem), what);
        }
    }
    expect(decoded_alpha(one_part(12)) < 255, "the decoder gives an RGBA block with alpha endpoints 0 an alpha under 255");
    expect(decoded_alpha(Block()) < 255, "the decoder gives a block of zeros (a reserved mode) an alpha under 255");

    // partitions of different modes: one with alpha is enough
    expect_opaque(two_modes(2, 0, 0, 0, 0), "two partitions, RGB and RGB (8, 8)");
    expect_opaque(two_modes(2, 0, 1, 0, 3), "two partitions, RGB and HDR RGB (9, 11)");
    expect_opaque(two_modes(1, 0, 2, 0, 3), "two partitions, RGB scale and HDR RGB scale (6, 7)");
    expect_opaque(two_modes(0, 1, 2, 0, 3), "two partitions, RGB scale and HDR luminance (6, 3)");
    expect(!opaque(two_modes(2, 0, 0, 1, 0)), "two partitions, RGB and RGBA (8, 12)");
    expect(!opaque(two_modes(2, 1, 0, 0, 0)), "two partitions, RGBA and RGB (12, 8)");
    expect(!opaque(two_modes(2, 0, 0, 0, 2)), "two partitions, RGB and RGB scale + alphas (8, 10): the second's mode bits lie below the weights");
    expect(!opaque(two_modes(2, 0, 2, 0, 0)), "two partitions, RGB scale + alphas and RGB (10, 8)");
    expect(!opaque(two_modes(1, 0, 0, 0, 2)), "two partitions, luminance + alpha and RGB scale (4, 6)");
    expect(!opaque(two_modes(0, 0, 0, 1, 0)), "two partitions, luminance and luminance + alpha (0, 4)");
    expect(!opaque(two_modes(2, 0, 0, 1, 2)), "two partitions, RGB and HDR RGB + alpha (8, 14)");
    expect(!opaque(two_modes(2, 1, 3, 0, 0)), "two partitions, HDR RGB + HDR alpha and RGB (15, 8)");
    // (two planes: the second partition's mode bits are right below the 64 bits of weights, at 62; the plane's component
    // selector is below those, at 60, and is not read for them)
    expect_opaque(two_modes(1, 0, 2, 0, 2, kDual, 64), "two partitions on two planes, RGB scale twice (6, 6)");
    expect(!opaque(two_modes(1, 0, 2, 0, 0, kDual, 64)), "two partitions on two planes, RGB scale and luminance + alpha (6, 4)");
    expect(!opaque(two_modes(1, 0, 2, 0, 0, kDual, 64).set(60, 2, 2)), "two planes: the component selector is not the mode");
    expect_opaque(three_modes(1, 1, 1, 1, 0, 0, 0), "three partitions, RGB each (8, 8, 8)");
    expect_opaque(three_modes(1, 1, 1, 0, 0, 1, 2), "three partitions, RGB, RGB and RGB scale (8, 9, 6)");
    expect(!opaque(three_modes(1, 1, 1, 0, 0, 0, 0)), "three partitions, the third luminance + alpha (8, 8, 4)");
    expect(!opaque(three_modes(1, 0, 1, 1, 1, 0, 0)), "three partitions, the first luminance + alpha (5, 8, 8)");
    expect(!opaque(three_modes(1, 1, 1, 1, 0, 0, 2)), "three partitions, the third RGB scale + alphas (8, 8, 10)");
    expect(!opaque(three_modes(1, 1, 1, 1, 0, 2, 0)), "three partitions, the second RGB scale + alphas (8, 10, 8)");

    // void-extent blocks: opaque only with alpha at its maximum
    expect_opaque(void_extent(0xFFFF), "a void-extent block with alpha 0xFFFF");
    expect_opaque(void_extent(0xFFFF, 0, 1, 0, 1), "a void-extent block with an extent and alpha 0xFFFF");
    expect(!opaque(void_extent(0xFFFE)), "a void-extent block with alpha 0xFFFE");
    expect(!opaque(void_extent(0xFF00)), "a void-extent block with alpha 0xFF00");
    expect(!opaque(void_extent(0)), "a void-extent block with alpha 0");
    expect(!opaque(void_extent(0xFFFF).set(9, 1, 1)), "an HDR void-extent block");
    expect(!opaque(void_extent(0xFFFF).set(10, 1, 0)), "a void-extent block with reserved bit 10 clear");
    expect(!opaque(void_extent(0xFFFF).set(11, 1, 0)), "a void-extent block with reserved bit 11 clear");
    expect(!opaque(void_extent(0xFFFF, 5, 5, 0, 1)), "a void-extent block whose extent is empty in s");
    expect(!opaque(void_extent(0xFFFF, 0, 1, 7, 3)), "a void-extent block whose extent is backwards in t");

    // reserved modes and blocks that are not legal
    expect(!opaque(Block()), "a block of zeros (bits 0 to 3 zero: reserved)");
    expect(!opaque(one_part(8, 0x040)), "a reserved block mode (bits 0 to 3 zero)");
    expect(!opaque(one_part(8, 0x1C4)), "a reserved block mode (bits 6 to 8 ones, not a void extent)");
    expect(!opaque(one_part(8, 0xC2)), "a 5 x 4 weight grid in a 4 x 4 block");
    expect_opaque(one_part(8, 0xC2), "a 5 x 4 weight grid in a 5 x 4 block", 5, 4);
    expect_opaque(one_part(8, 0x064), "a 12 x 5 weight grid of 1-bit weights in a 12 x 12 block", 12, 12);
    expect(!opaque(one_part(8, 0x064), 10, 10), "a 12 x 5 weight grid in a 10 x 10 block");
    expect(!opaque(one_part(8, 0x464), 12, 12), "120 weights (a 12 x 5 grid, two planes)");
    expect(!opaque(one_part(8, 0x77), 8, 8), "120 bits of weights (an 8 x 5 grid of 3-bit weights)");
    expect(!opaque(one_part(8, 0x31F)), "20 bits of weights (a 2 x 2 grid of 5-bit weights)");
    expect(!opaque(parts_same(4, 0, kDual)), "four partitions with two planes");
    expect_opaque(parts_same(3, 0, kDual), "three partitions with two planes");
    // (a 4 x 4 grid of 3-bit weights on two planes: 96 bits of weights, 13 bits left for the colours)
    expect(!opaque(one_part(8, 0x453)), "an RGB mode's six values in 13 bits");
    expect_opaque(one_part(0, 0x453), "a luminance mode's two values in 13 bits");
}

/// Where the GPU decoder reads block (x, y) of a level tw blocks wide: host_shaders/astc_decoder.comp's main, with the
/// push constants upload_astc_gpu gives it (native_tiling 1, x_shift 9 + tile_h, block_size gobs_x << x_shift)
uint64_t decoder_offset(uint32_t x, uint32_t y, uint32_t tw, unsigned tile_h) {
    const uint32_t px = x << 4;
    uint32_t swizzle = ((px & 32u) << 3) | ((y & 6u) << 5) | ((px & 16u) << 1) | ((y & 1u) << 4) | (px & 15u);
    const uint32_t difference = ((swizzle >> 5) ^ (swizzle >> 6)) & 1u;
    swizzle ^= (difference << 5) | (difference << 6);
    const uint32_t gobs_x = (tw * 16 + 63) >> 6, x_shift = 9 + tile_h, block_y = y >> 3;
    return (uint64_t)(block_y >> tile_h) * ((uint64_t)gobs_x << x_shift) + ((uint64_t)(block_y & ((1u << tile_h) - 1)) << 9) +
           ((uint64_t)(px >> 6) << x_shift) + swizzle;
}

void levels() {
    const Block plain = one_part(8), alpha = one_part(12);
    const struct { uint32_t tw, th; unsigned tile_h; } cases[] = {{1, 1, 0}, {5, 3, 0}, {7, 130, 1}, {86, 43, 2}, {64, 64, 4}, {171, 171, 4}};
    for (const auto& c : cases) {
        uint64_t size = 0;
        for (uint32_t y = 0; y < c.th; y++)
            for (uint32_t x = 0; x < c.tw; x++) size = std::max(size, decoder_offset(x, y, c.tw, c.tile_h) + 16);
        // (zeros between the blocks: a block of zeros is not opaque, so a walk that read the padding would say no)
        std::vector<uint8_t> storage(size, 0);
        for (uint32_t y = 0; y < c.th; y++)
            for (uint32_t x = 0; x < c.tw; x++) std::memcpy(storage.data() + decoder_offset(x, y, c.tw, c.tile_h), plain.b, 16);
        const auto walk = [&](uint64_t avail) { return astc_opaque::level(storage.data(), avail, c.tw, c.th, c.tile_h, 4, 4); };
        expect(walk(size), "a level of opaque blocks, zeros in its padding, is opaque");
        expect(!walk(size - 1), "a level whose last block the storage cuts short is not opaque");
        // (one block with alpha, wherever it is, is found: every block of the small levels, a sample of the large ones and
        // their last)
        const uint32_t count = c.tw * c.th, stride = count <= 1024 ? 1 : 97;
        const auto probe = [&](uint32_t i) {
            uint8_t* at = storage.data() + decoder_offset(i % c.tw, i / c.tw, c.tw, c.tile_h);
            std::memcpy(at, alpha.b, 16);
            if (walk(size)) {
                std::printf("FAIL: block (%u, %u) of a %u x %u level (tile height %u) with alpha is not found\n", i % c.tw,
                            i / c.tw, c.tw, c.th, c.tile_h);
                failures++;
            }
            std::memcpy(at, plain.b, 16);
        };
        for (uint32_t i = 0; i < count; i += stride) probe(i);
        probe(count - 1);
    }
}

uint64_t rng = 0x9E3779B97F4A7C15ull;
uint64_t rnd() {
    rng ^= rng << 13, rng ^= rng >> 7, rng ^= rng << 17;
    return rng;
}

void random_blocks() {
    static const uint32_t dims[][2] = {{4, 4}, {5, 5}, {6, 6}, {8, 8}, {10, 5}, {10, 10}, {12, 12}};
    static const uint32_t plain_modes[9] = {0, 1, 2, 3, 6, 7, 8, 9, 11};
    long judged = 0, wrong = 0, total = 0;
    for (const auto& d : dims)
        for (int i = 0; i < 60000; i++) {
            Block k;
            const uint64_t lo = rnd(), hi = rnd();
            std::memcpy(k.b, &lo, 8), std::memcpy(k.b + 8, &hi, 8);
            // (a third as they come; a third with one partition of a mode without alpha; a third with the field of several
            // partitions' one mode set to such a mode: so that more of them are judged opaque and reach the decoder)
            if (i % 3 == 1) k.set(11, 2, 0).set(13, 4, plain_modes[rnd() % 9]);
            if (i % 3 == 2) k.set(23, 2, 0).set(25, 4, plain_modes[rnd() % 9]);
            total++;
            if (!opaque(k, d[0], d[1])) continue;
            judged++;
            if (decoded_alpha(k, d[0], d[1]) == 255) continue;
            if (wrong++ < 8) {
                std::printf("FAIL: judged opaque, decoded with alpha %d (%u x %u):", decoded_alpha(k, d[0], d[1]), d[0], d[1]);
                for (uint8_t byte : k.b) std::printf(" %02x", byte);
                std::printf("\n");
            }
        }
    expect(!wrong, "a random block judged opaque decodes to alpha 255 in every texel");
    expect(judged >= 10000, "at least 10000 random blocks are judged opaque and decoded (else the cross-check says little)");
    std::printf("astc opaque: %ld of %ld random blocks judged opaque, each decoded: alpha 255 in every texel of %ld\n", judged,
                total, judged - wrong);
}

} // namespace fixture

int main() {
    fixture::hand_built();
    fixture::levels();
    fixture::random_blocks();
    if (fixture::failures) return EXIT_FAILURE;
    std::printf("astc opaque: all checks passed\n");
    return EXIT_SUCCESS;
}
