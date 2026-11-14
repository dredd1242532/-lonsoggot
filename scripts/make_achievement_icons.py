"""Draws the achievements' default icons (docs/achievements.md) into assets/achievements/: for each id of
gpu/vk/oot_achievements.h, <id>.png (unlocked) and <id>_locked.png, 128 x 128 RGBA.

Every icon is drawn here from plain shapes (discs, rings, polygons, lines) with 4 x 4 supersampling: no game file, no
picture from anywhere else. Run it again to remake them all; replace any single PNG with art of your own (same name,
8-bit RGBA, at most 256 x 256) and nx2/achievement_icons.py builds that one in instead.

    python -I scripts/make_achievement_icons.py [--out DIR] [--sheet SHEET.png]

Standard library only.
"""
import argparse
import math
import os
import re
import struct
import zlib

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
HEADER = os.path.join(ROOT, "gpu", "vk", "oot_achievements.h")
OUT = os.path.join(ROOT, "assets", "achievements")
SIZE, SS = 128, 4   # (the icon, and the samples per pixel along each axis)

# ---- shapes: each answers whether a point (in 0..1 icon units) is inside ----


def disc(cx, cy, r):
    return lambda x, y: (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def ring(cx, cy, r0, r1):
    return lambda x, y: r0 * r0 <= (x - cx) ** 2 + (y - cy) ** 2 <= r1 * r1


def ellipse(cx, cy, rx, ry, turn=0.0):
    c, s = math.cos(turn), math.sin(turn)
    def inside(x, y):
        dx, dy = x - cx, y - cy
        u, v = dx * c + dy * s, -dx * s + dy * c
        return (u / rx) ** 2 + (v / ry) ** 2 <= 1
    return inside


def poly(*pts):
    def inside(x, y):
        hit, j = False, len(pts) - 1
        for i in range(len(pts)):
            (xi, yi), (xj, yj) = pts[i], pts[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                hit = not hit
            j = i
        return hit
    return inside


def line(x0, y0, x1, y1, w):
    dx, dy = x1 - x0, y1 - y0
    n = dx * dx + dy * dy
    def inside(x, y):
        t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / n))
        return (x - x0 - t * dx) ** 2 + (y - y0 - t * dy) ** 2 <= (w / 2) ** 2
    return inside


def rect(x0, y0, x1, y1):
    return lambda x, y: x0 <= x <= x1 and y0 <= y <= y1


def star(cx, cy, r, points=5):
    pts = []
    for k in range(points * 2):
        a = -math.pi / 2 + k * math.pi / points
        rr = r if k % 2 == 0 else r * 0.45
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return poly(*pts)


def minus(a, b):
    return lambda x, y: a(x, y) and not b(x, y)


def union(*shapes):
    return lambda x, y: any(s(x, y) for s in shapes)

# ---- the picture: layers of (shape, colour), drawn in order ----


def render(layers):
    px = bytearray(SIZE * SIZE * 4)
    step = 1.0 / (SIZE * SS)
    for py in range(SIZE):
        for qx in range(SIZE):
            r = g = b = a = 0.0
            for sy in range(SS):
                y = (py * SS + sy + 0.5) * step
                for sx in range(SS):
                    x = (qx * SS + sx + 0.5) * step
                    cr = cg = cb = ca = 0.0
                    for shape, (lr, lg, lb, la) in layers:
                        if shape(x, y):   # (straight alpha "over")
                            k = la / 255.0
                            out = k + ca * (1 - k)
                            if out > 0:
                                cr = (lr * k + cr * ca * (1 - k)) / out
                                cg = (lg * k + cg * ca * (1 - k)) / out
                                cb = (lb * k + cb * ca * (1 - k)) / out
                            ca = out
                    r += cr * ca
                    g += cg * ca
                    b += cb * ca
                    a += ca
            n = SS * SS
            i = (py * SIZE + qx) * 4
            if a > 0:
                px[i:i + 4] = bytes((round(r / a), round(g / a), round(b / a), round(255 * a / n)))
    return bytes(px)


def png(w, h, rgba):
    raw = b"".join(b"\x00" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))

# ---- the emblems, in white on the badge, each a list of shapes ----

WHITE = (255, 250, 240, 255)
GOLD, GOLD_DARK = (238, 176, 64, 255), (150, 98, 30, 255)


def sword_and_shield():
    blade = union(rect(0.47, 0.20, 0.53, 0.66), poly((0.47, 0.20), (0.53, 0.20), (0.50, 0.15)))
    guard = rect(0.38, 0.62, 0.62, 0.67)
    grip = rect(0.475, 0.67, 0.525, 0.78)
    shield = poly((0.30, 0.36), (0.56, 0.36), (0.56, 0.58), (0.43, 0.74), (0.30, 0.58))
    return [blade, guard, grip, ("dark", shield), minus(shield, poly((0.33, 0.39), (0.53, 0.39), (0.53, 0.57), (0.43, 0.70), (0.33, 0.57)))]


def ocarina():
    body = ellipse(0.50, 0.53, 0.25, 0.17, -0.35)
    mouth = line(0.68, 0.40, 0.78, 0.33, 0.07)
    holes = union(disc(0.40, 0.55, 0.03), disc(0.49, 0.50, 0.03), disc(0.58, 0.46, 0.03), disc(0.47, 0.62, 0.025))
    return [minus(union(body, mouth), holes)]


def gem():
    outer = poly((0.50, 0.20), (0.74, 0.40), (0.62, 0.78), (0.38, 0.78), (0.26, 0.40))
    facet = poly((0.50, 0.28), (0.64, 0.41), (0.50, 0.68), (0.36, 0.41))
    return [outer, ("shade", facet)]


def note():
    head = ellipse(0.42, 0.68, 0.10, 0.075, -0.4)
    stem = rect(0.495, 0.24, 0.535, 0.68)
    flag = poly((0.535, 0.24), (0.68, 0.36), (0.66, 0.46), (0.535, 0.36))
    return [head, stem, flag]


def temple():
    roof = poly((0.22, 0.42), (0.50, 0.22), (0.78, 0.42))
    beam = rect(0.24, 0.42, 0.76, 0.47)
    cols = union(*(rect(x, 0.50, x + 0.06, 0.70) for x in (0.28, 0.40, 0.54, 0.66)))
    base = rect(0.22, 0.72, 0.78, 0.78)
    return [roof, beam, cols, base]


def sack(kind, tier):
    bag = union(disc(0.50, 0.60, 0.22), poly((0.40, 0.42), (0.60, 0.42), (0.56, 0.32), (0.44, 0.32)))
    tie = rect(0.40, 0.30, 0.60, 0.34)
    out = [bag, ("dark", tie)]
    if kind == "bomb":
        out += [("dark", disc(0.50, 0.63, 0.09)), ("dark", line(0.55, 0.55, 0.60, 0.49, 0.025))]
    elif kind == "arrow":
        out += [("dark", line(0.38, 0.74, 0.62, 0.50, 0.035)), ("dark", poly((0.62, 0.50), (0.56, 0.50), (0.62, 0.56)))]
    else:
        out += [("dark", disc(x, y, 0.035)) for x, y in ((0.43, 0.60), (0.53, 0.58), (0.48, 0.68), (0.58, 0.67))]
    for k in range(tier):   # (one star for Big, two for Biggest)
        out.append(("star", star(0.70 - k * 0.15, 0.24, 0.07)))
    return out


SONG_COLOURS = {
    "song_zelda": (124, 72, 168), "song_epona": (176, 104, 52), "song_saria": (64, 150, 72), "song_sun": (222, 168, 40),
    "song_time": (52, 104, 190), "song_storms": (76, 92, 116), "song_minuet": (44, 140, 60), "song_bolero": (200, 64, 40),
    "song_serenade": (40, 120, 200), "song_nocturne": (110, 60, 150), "song_requiem": (214, 120, 40), "song_prelude": (220, 196, 80)}


def design(id_):
    """(badge colour, emblem shapes) for an achievement id"""
    if id_ == "sword_and_shield":
        return (70, 110, 70), sword_and_shield()
    if id_ == "fairy_ocarina":
        return (140, 96, 56), ocarina()
    if id_ == "ocarina_of_time":
        return (52, 96, 184), ocarina()
    if id_.startswith("stone_"):
        return {"stone_forest": (40, 150, 80), "stone_fire": (196, 52, 56), "stone_water": (48, 120, 210)}[id_], gem()
    if id_.startswith("song_"):
        return SONG_COLOURS[id_], note()
    if id_.startswith("temple_"):
        return {"temple_forest": (48, 128, 64), "temple_fire": (192, 64, 40), "temple_water": (40, 104, 196),
                "temple_shadow": (96, 64, 140), "temple_spirit": (204, 120, 48)}[id_], temple()
    m = re.fullmatch(r"(bomb_bag|quiver|bullet_bag)_(big|biggest)", id_)
    if m:
        kind = {"bomb_bag": "bomb", "quiver": "arrow", "bullet_bag": "seeds"}[m.group(1)]
        return {"bomb": (88, 96, 120), "arrow": (128, 100, 60), "seeds": (110, 130, 60)}[kind], sack(kind, 1 if m.group(2) == "big" else 2)
    raise SystemExit("no design for %s: add one to scripts/make_achievement_icons.py" % id_)


def badge(colour, emblem, locked):
    r, g, b = colour
    if locked:   # (grey, the emblem faint, a padlock at the lower right)
        grey = (r * 30 + g * 59 + b * 11) // 100 // 2 + 30
        face, rim, ink, dark = (grey, grey, grey, 255), (96, 92, 86, 255), (150, 146, 140, 150), (grey - 18, grey - 18, grey - 18, 255)
    else:
        face, rim, ink, dark = (r, g, b, 255), GOLD, WHITE, (r * 6 // 10, g * 6 // 10, b * 6 // 10, 255)
    shade = (0, 0, 0, 50) if not locked else (0, 0, 0, 30)   # (a gem's facet: a soft shadow on the white)
    layers = [(disc(0.5, 0.5, 0.47), rim), (disc(0.5, 0.5, 0.41), face),
              (ring(0.5, 0.5, 0.41, 0.43), GOLD_DARK if not locked else (70, 68, 64, 255)),
              (ellipse(0.5, 0.30, 0.28, 0.12), (255, 255, 255, 26))]   # (a soft highlight at the top)
    for part in emblem:
        if isinstance(part, tuple):
            role, shape = part
            layers.append((shape, dark if role == "dark" else shade if role == "shade" else
                           ((255, 214, 90, 255) if not locked else (170, 166, 160, 200)) if role == "star" else ink))
        else:
            layers.append((part, ink))
    if locked:
        body = rect(0.66, 0.70, 0.86, 0.86)
        shackle = minus(ring(0.76, 0.70, 0.045, 0.075), rect(0.60, 0.70, 0.92, 0.95))
        layers += [(disc(0.76, 0.78, 0.15), (40, 38, 36, 230)), (shackle, (210, 206, 198, 255)), (body, (210, 206, 198, 255)),
                   (disc(0.76, 0.77, 0.018), (60, 58, 54, 255))]
    return render(layers)


def ids():
    text = open(HEADER, encoding="utf-8").read()
    return re.findall(r'^\s*X\("([a-z0-9_]+)"', text, re.M)


def sheet(icons, path, cols=8):
    """Every icon, unlocked over locked, on one picture (for looking at them, not used by the game)"""
    names = list(icons)
    rows = (len(names) + cols - 1) // cols * 2
    w, h = cols * SIZE, rows * SIZE
    out = bytearray(w * h * 4)
    for n, name in enumerate(names):
        for half, rgba in enumerate(icons[name]):
            x0, y0 = n % cols * SIZE, (n // cols * 2 + half) * SIZE
            for y in range(SIZE):
                out[((y0 + y) * w + x0) * 4:((y0 + y) * w + x0 + SIZE) * 4] = rgba[y * SIZE * 4:(y + 1) * SIZE * 4]
    with open(path, "wb") as f:
        f.write(png(w, h, bytes(out)))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default=OUT)
    p.add_argument("--sheet", help="also write every icon on one picture here")
    a = p.parse_args()
    os.makedirs(a.out, exist_ok=True)
    made = {}
    for id_ in ids():
        colour, emblem = design(id_)
        made[id_] = (badge(colour, emblem, False), badge(colour, emblem, True))
        for locked, rgba in enumerate(made[id_]):
            with open(os.path.join(a.out, id_ + ("_locked" if locked else "") + ".png"), "wb") as f:
                f.write(png(SIZE, SIZE, rgba))
        print("drew %s" % id_, flush=True)
    if a.sheet:
        sheet(made, a.sheet)
        print("sheet: %s" % a.sheet)


if __name__ == "__main__":
    main()
