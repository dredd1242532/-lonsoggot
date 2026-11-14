// Whether ASTC blocks decode to alpha 1 in every texel, told from each block's mode and colour endpoint modes alone (no
// decode). vk_texture.cpp keeps a big ASTC texture as BC1, half the memory of BC3, only when every block it uploads is
// (RX_VK_ASTC_BC1). The rule is the ASTC specification's block layout (Khronos Data Format Specification, ASTC: block mode,
// partitions, colour endpoint modes, void-extent blocks), and it errs one way: a block not proved opaque is not opaque.
// A block the specification calls illegal is not opaque either: the decoders here write it as transparent black
// (host_shaders/astc_decoder.comp FillError), not as the specification's opaque error colour.
// tests/test_astc_opaque.py
#pragma once
#include <cstdint>

namespace astc_opaque {

/// Bits [at, at + n) of a 16-byte block (bit 0 is the first byte's lowest); n <= 16
inline uint32_t bits(const uint8_t* b, uint32_t at, uint32_t n) {
    uint32_t v = 0;
    for (uint32_t i = at >> 3, k = 0; i < 16 && k < 3; i++, k++) v |= (uint32_t)b[i] << (8 * k);
    return (v >> (at & 7)) & ((1u << n) - 1);
}

/// Whether a colour endpoint mode has no alpha of its own, so that the decoder gives its texels alpha 1: 0-3 (luminance,
/// LDR and HDR), 6-9 and 11 (RGB, LDR and HDR). The others carry one: 4 and 5 (luminance and alpha), 10 (RGB with a scale
/// and two alphas), 12 and 13 (RGBA), 14 and 15 (HDR RGB with an LDR or HDR alpha).
/// shortcut: a mode that carries alpha is never proved opaque, even with both its alpha endpoints at 1 (that needs the
/// endpoint values decoded); upgrade if the textures line shows few kept as BC1 where the picture has no transparency.
inline bool mode_without_alpha(uint32_t cem) {
    return cem < 4 || (cem >= 6 && cem <= 9) || cem == 11;
}

// Bits a weight takes by the weights' range (R - 2, + 6 with the precision bit H), and how the rest of it is packed:
// 0 nothing more, 1 trits (8 bits for 5 weights), 2 quints (7 bits for 3). Ranges 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 32
constexpr uint8_t kWeightBits[12] = {1, 0, 2, 0, 1, 3, 1, 2, 4, 2, 3, 5};
constexpr uint8_t kWeightPack[12] = {0, 1, 0, 2, 1, 0, 2, 1, 0, 2, 1, 0};

/// One 16-byte block of a texture of bw x bh texel blocks: true only when every texel it decodes to has alpha 1
inline bool block(const uint8_t* b, uint32_t bw, uint32_t bh) {
    const uint32_t mode = bits(b, 0, 11);
    if ((mode & 0x1FF) == 0x1FC) {
        // A void-extent block, one colour for all of it: bit 9 says HDR (floats: not taken), bits 10 and 11 are ones, then
        // four 13-bit coordinates (all ones: no extent; else low < high both ways), then R, G, B, A of 16 bits each
        if ((mode & 0x200) || bits(b, 10, 2) != 3) return false;
        const uint32_t s0 = bits(b, 12, 13), s1 = bits(b, 25, 13), t0 = bits(b, 38, 13), t1 = bits(b, 51, 13);
        if ((s0 & s1 & t0 & t1) != 0x1FFF && (s0 >= s1 || t0 >= t1)) return false;
        return bits(b, 112, 16) == 0xFFFF;
    }
    // The block mode: the weight grid w x h, the weights' range R (2 to 7) with its precision bit H, dual plane D
    uint32_t w = 0, h = 0, r = 0;
    bool dual = (mode & 0x400) != 0, high = (mode & 0x200) != 0;
    const uint32_t a = (mode >> 5) & 3, b78 = (mode >> 7) & 3;
    if (mode & 3) {
        r = (mode & 3) << 1 | ((mode >> 4) & 1);
        switch ((mode >> 2) & 3) {
        case 0: w = b78 + 4, h = a + 2; break;
        case 1: w = b78 + 8, h = a + 2; break;
        case 2: w = a + 2, h = b78 + 8; break;
        default:
            if (mode & 0x100) w = (b78 & 1) + 2, h = a + 2;
            else w = a + 2, h = (b78 & 1) + 6;
            break;
        }
    } else {
        if (!(mode & 0xC)) return false;   // (reserved: bits 0 to 3 all zero)
        r = ((mode >> 2) & 3) << 1 | ((mode >> 4) & 1);
        switch (b78) {
        case 0: w = 12, h = a + 2; break;
        case 1: w = a + 2, h = 12; break;
        case 2: w = a + 6, h = ((mode >> 9) & 3) + 6, dual = high = false; break;   // (bits 9 and 10 are its height)
        default:
            if (mode & 0x40) return false;   // (reserved: bits 6 to 8 all ones, and not a void-extent block)
            if (mode & 0x20) w = 10, h = 6;
            else w = 6, h = 10;
            break;
        }
    }
    if (w > bw || h > bh) return false;
    const uint32_t weights = w * h * (dual ? 2 : 1);
    if (weights > 64) return false;
    const uint32_t k = r - 2 + (high ? 6 : 0);
    const uint32_t wbits = weights * kWeightBits[k] +
                           (kWeightPack[k] == 1 ? (weights * 8 + 4) / 5 : kWeightPack[k] == 2 ? (weights * 7 + 2) / 3 : 0);
    if (wbits < 24 || wbits > 96) return false;
    // The partitions and each one's colour endpoint mode. One partition: 4 bits at 13. More: a 10-bit partition index at
    // 13, then 6 bits at 23: their low two 0, the next four are the mode of all; else they are a class (less 1), then one bit
    // a partition that adds 1 to its class and two bits a partition for the mode within it, the bits past the six lying
    // right below the weights (the weights fill the block's top)
    const uint32_t parts = bits(b, 11, 2) + 1;
    if (parts == 4 && dual) return false;
    uint32_t cem[4] = {}, config = 17, extra = 0;
    if (parts == 1) {
        cem[0] = bits(b, 13, 4);
    } else {
        config = 29;
        const uint32_t field = bits(b, 23, 6);
        if (!(field & 3)) {
            for (uint32_t i = 0; i < parts; i++) cem[i] = field >> 2;
        } else {
            extra = parts * 3 - 4;
            const uint32_t all = field | bits(b, 128 - wbits - extra, extra) << 6;
            for (uint32_t i = 0; i < parts; i++)
                cem[i] = ((field & 3) - 1 + ((all >> (2 + i)) & 1)) << 2 | ((all >> (2 + parts + 2 * i)) & 3);
        }
    }
    uint32_t values = 0;   // (the colour endpoint values the modes take: 2, 4, 6 or 8 each)
    for (uint32_t i = 0; i < parts; i++) {
        if (!mode_without_alpha(cem[i])) return false;
        values += 2 * ((cem[i] >> 2) + 1);
    }
    // (more than 18 values, or fewer bits left for them than their coarsest packing takes, 13 bits for 5: not a legal block)
    const uint32_t used = config + wbits + extra + (dual ? 2 : 0);
    return values <= 18 && used <= 128 && 128 - used >= (13 * values + 4) / 5;
}

/// Every block of one level of one layer where its storage holds it: tw x th blocks of 16 bytes, block linear in the Switch 2
/// GOB order (texture_read_texels' and the GPU decoder's addresses), GOB blocks of 8 << tile_h rows, in the avail bytes at p.
/// The bytes between the blocks (the tiling's padding) are not read: no decoder reads them. A block past avail is not opaque.
inline bool level(const uint8_t* p, uint64_t avail, uint32_t tw, uint32_t th, unsigned tile_h, uint32_t bw, uint32_t bh) {
    const uint64_t gobs_x = ((uint64_t)tw * 16 + 63) >> 6, step = 512ull << tile_h;
    for (uint32_t y = 0; y < th; y++) {
        const uint64_t row = (uint64_t)(y >> (3 + tile_h)) * gobs_x * step + ((uint64_t)((y >> 3) & ((1u << tile_h) - 1)) << 9) +
                             (((y & 7) >> 2) << 7 | ((y >> 1) & 1) << 5 | (y & 1) << 4);
        for (uint32_t x = 0; x < tw; x++) {
            const uint64_t at = row + (uint64_t)(x >> 2) * step + (((x >> 1) & 1) << 8 | (x & 1) << 6);
            if (at + 16 > avail || !block(p + at, bw, bh)) return false;
        }
    }
    return true;
}

} // namespace astc_opaque
