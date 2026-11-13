/* Bounded UTF-8 shared by the chat receiver and the existing menu text editor. */
#pragma once
#include <stddef.h>
#include <stdint.h>

static inline int coop_text_printable(uint32_t c) {
    return c >= 0x20 && c <= 0x10ffff && !(c >= 0x7f && c <= 0x9f) &&
           !(c >= 0xd800 && c <= 0xdfff) && !(c >= 0x200b && c <= 0x200f) &&
           !(c >= 0x2028 && c <= 0x202e) && !(c >= 0x2060 && c <= 0x206f) &&
           c != 0x061c && c != 0xfeff && !(c >= 0xfdd0 && c <= 0xfdef) &&
           (c & 0xffff) != 0xfffe && (c & 0xffff) != 0xffff;
}
static inline unsigned coop_text_encode(uint32_t c, char out[4]) {
    if (!coop_text_printable(c)) return 0;
    if (c < 0x80) { out[0] = (char)c; return 1; }
    if (c < 0x800) { out[0] = (char)(0xc0 | c >> 6); out[1] = (char)(0x80 | (c & 63)); return 2; }
    if (c < 0x10000) {
        out[0] = (char)(0xe0 | c >> 12); out[1] = (char)(0x80 | ((c >> 6) & 63)); out[2] = (char)(0x80 | (c & 63)); return 3;
    }
    out[0] = (char)(0xf0 | c >> 18); out[1] = (char)(0x80 | ((c >> 12) & 63));
    out[2] = (char)(0x80 | ((c >> 6) & 63)); out[3] = (char)(0x80 | (c & 63)); return 4;
}
/* One strictly decoded code point, or zero bytes on malformed/truncated input. */
static inline unsigned coop_text_next(const uint8_t *in, size_t n, uint32_t *point) {
    if (!n) return 0;
    uint32_t c = in[0], min = 0;
    unsigned more = 0;
    if (c >= 0xc2 && c <= 0xdf) { c &= 31; more = 1; min = 0x80; }
    else if (c >= 0xe0 && c <= 0xef) { c &= 15; more = 2; min = 0x800; }
    else if (c >= 0xf0 && c <= 0xf4) { c &= 7; more = 3; min = 0x10000; }
    else if (c >= 0x80) return 0;
    if (more >= n) return 0;
    for (unsigned k = 1; k <= more; k++) {
        if ((in[k] & 0xc0) != 0x80) return 0;
        c = (c << 6) | (in[k] & 63);
    }
    if (c < min || c > 0x10ffff || (c >= 0xd800 && c <= 0xdfff)) return 0;
    *point = c;
    return more + 1;
}
/* Malformed UTF-8 or an oversized input refuses the whole message; controls are removed.
   No truncation can split a code point. Empty/space-only messages refuse. */
static inline size_t coop_text_clean(char *out, size_t cap, const uint8_t *in, size_t n) {
    size_t used = 0;
    if (!out || !cap || !in || n >= cap) return 0;
    out[0] = 0;
    for (size_t i = 0; i < n;) {
        uint32_t c;
        const unsigned consumed = coop_text_next(in + i, n - i, &c);
        if (!consumed) { out[0] = 0; return 0; }
        i += consumed;
        char bytes[4];
        const unsigned len = coop_text_encode(c, bytes);
        for (unsigned k = 0; k < len; k++) out[used++] = bytes[k];
    }
    while (used && out[used - 1] == ' ') used--;
    size_t first = 0;
    while (first < used && out[first] == ' ') first++;
    if (first) { for (size_t i = first; i < used; i++) out[i - first] = out[i]; used -= first; }
    out[used] = 0;
    return used;
}
