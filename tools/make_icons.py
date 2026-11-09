#!/usr/bin/env python3
"""
Generates the app icons into ../assets: a white cursor with a halftone dot trail on a
black rounded square. The whole composition (cursor + trail) is centred in the icon.
Pure Python (zlib + struct), no Pillow needed.

    python3 tools/make_icons.py
"""
import math
import struct
import sys
import zlib
from pathlib import Path

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "assets"
OUT.mkdir(parents=True, exist_ok=True)

CORNER = 40      # corner radius of the rounded variant (icon box is 180x180)
TARGET = 112     # the composition's longest side in the 180 box (smaller = more air)

# ── composition, built in free local units, then centred and scaled into the box ──
ARROW = [(0, 0), (0, 16.5), (4.2, 12.6), (7, 19.5), (9.9, 18.3), (7.1, 11.7), (12.6, 11.6)]
S0, TIP0 = 5.7, (34.0, 20.0)
CUR0 = [(TIP0[0] + x * S0, TIP0[1] + y * S0) for x, y in ARROW]
GRID, GOFF, RMAX = 12.5, 6.0, 6.0                  # dot lattice step, offset, max dot radius
P0, P1, P2 = (84, 92), (128, 100), (152, 152)      # trail axis (quadratic Bezier)


def bez(t):
    a, b, c = (1 - t) ** 2, 2 * (1 - t) * t, t * t
    return a * P0[0] + b * P1[0] + c * P2[0], a * P0[1] + b * P1[1] + c * P2[1]


def smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


SAMPLES = [(i / 80, bez(i / 80)) for i in range(81)]
DOTS0 = []
for i in range(-2, 20):
    for j in range(-2, 20):
        cx, cy = GOFF + i * GRID, GOFF + j * GRID
        t, d = min(((t, math.hypot(cx - p[0], cy - p[1])) for t, p in SAMPLES), key=lambda s: s[1])
        w = 32 * (1 - 0.45 * t) + 6
        if d >= w:
            continue
        r = RMAX * smooth(1 - d / w) * (1 - t) ** 0.7
        if r >= 0.9:
            DOTS0.append((cx, cy, r, t))

xs = [p[0] for p in CUR0] + [d[0] - d[2] for d in DOTS0] + [d[0] + d[2] for d in DOTS0]
ys = [p[1] for p in CUR0] + [d[1] - d[2] for d in DOTS0] + [d[1] + d[2] for d in DOTS0]
MID = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
F = TARGET / max(max(xs) - min(xs), max(ys) - min(ys))

CUR = [(90 + (x - MID[0]) * F, 90 + (y - MID[1]) * F) for x, y in CUR0]
DOTS = [(90 + (cx - MID[0]) * F, 90 + (cy - MID[1]) * F, r * F, t) for cx, cy, r, t in DOTS0]


def centre_of_mass(poly, dots):
    a2 = gx = gy = 0.0
    for k in range(len(poly)):
        x1, y1 = poly[k]
        x2, y2 = poly[(k + 1) % len(poly)]
        cr = x1 * y2 - x2 * y1
        a2 += cr
        gx += (x1 + x2) * cr
        gy += (y1 + y2) * cr
    a2 /= 2
    mass, mx, my = abs(a2), abs(a2) * gx / (6 * a2), abs(a2) * gy / (6 * a2)
    for cx, cy, r, _ in dots:
        m = math.pi * r * r
        mass, mx, my = mass + m, mx + m * cx, my + m * cy
    return mx / mass, my / mass


# The cursor is heavy and the trail is light, so a plain bounding-box centre looks shifted
# towards the cursor. Nudge the composition 30% of the way towards the centre of mass
# (optical centring): the bounding box stays near the middle, the visual weight too.
OPTICAL = 0.3
COM = centre_of_mass(CUR, DOTS)
SHIFT = (OPTICAL * (90 - COM[0]), OPTICAL * (90 - COM[1]))
CUR = [(x + SHIFT[0], y + SHIFT[1]) for x, y in CUR]
DOTS = [(cx + SHIFT[0], cy + SHIFT[1], r, t) for cx, cy, r, t in DOTS]
OUTLINE = 3.0 * F        # black gap that separates the cursor from the dots
LIMITS = (min(p[0] for p in CUR) - 1, max(p[0] for p in CUR) + 1)


def inside_poly(x, y):
    inside = False
    n = len(CUR)
    for k in range(n):
        x1, y1 = CUR[k]
        x2, y2 = CUR[(k + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def dist_poly(x, y):
    best = 1e9
    n = len(CUR)
    for k in range(n):
        x1, y1 = CUR[k]
        x2, y2 = CUR[(k + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        u = max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(x - (x1 + u * dx), y - (y1 + u * dy)))
    return best


def white(x, y):
    """1 where the icon is white, 0 where it is black (box coordinates 0..180)."""
    if x < 20 or x > 160 or y < 20 or y > 160:
        return 0
    if inside_poly(x, y):
        return 1
    for cx, cy, r, _ in DOTS:
        if abs(x - cx) <= r and abs(y - cy) <= r and math.hypot(x - cx, y - cy) <= r:
            return 1 if dist_poly(x, y) >= OUTLINE else 0
    return 0


def in_round_rect(x, y):
    if not (0 <= x <= 180 and 0 <= y <= 180):
        return 0
    cx = min(max(x, CORNER), 180 - CORNER)
    cy = min(max(y, CORNER), 180 - CORNER)
    return 1 if (x - cx) ** 2 + (y - cy) ** 2 <= CORNER ** 2 else 0


def write_png(path, size, ss, rounded):
    rows = []
    k = 180.0 / (size * ss)
    for py in range(size):
        row = bytearray()
        for px in range(size):
            lit = alpha = 0
            for sy in range(ss):
                for sx in range(ss):
                    x, y = (px * ss + sx + 0.5) * k, (py * ss + sy + 0.5) * k
                    lit += white(x, y)
                    if rounded:
                        alpha += in_round_rect(x, y)
            n = ss * ss
            v = round(255 * lit / n)
            row += bytes((v, v, v, round(255 * alpha / n))) if rounded else bytes((v, v, v))
        rows.append(b"\x00" + bytes(row))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 6 if rounded else 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                     + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def svg():
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in CUR)
    dots = "\n".join(
        f'  <circle class="d" cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.2f}" style="animation-delay:{t * 0.9:.2f}s"/>'
        for cx, cy, r, t in sorted(DOTS, key=lambda d: d[3])
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 180" width="180" height="180">
<style>
  .d {{ fill:#fff; animation:pulse 2.2s ease-in-out infinite; }}
  .cur {{ animation:nudge 2.2s ease-in-out infinite; }}
  @keyframes pulse {{ 0%,100% {{ opacity:.22; }} 35% {{ opacity:1; }} }}
  @keyframes nudge {{ 0%,100% {{ transform:translate(0,0); }} 45% {{ transform:translate(-5px,-5px); }} }}
  @media (prefers-reduced-motion: reduce) {{ .d, .cur {{ animation:none; }} }}
</style>
<rect width="180" height="180" rx="{CORNER}" fill="#000"/>
<g>
{dots}
</g>
<polygon class="cur" points="{pts}" fill="#fff" stroke="#000" stroke-width="{OUTLINE * 2:.1f}" stroke-linejoin="round" paint-order="stroke"/>
</svg>
"""


FONT = "-apple-system, 'SF Pro Display', 'Segoe UI', Helvetica, Arial, sans-serif"


def banner(title, tagline, note):
    """README banner: the icon composition on the left, text on the right, rounded corners."""
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in CUR)
    dots = "\n".join(
        f'    <circle class="d" cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.2f}" style="animation-delay:{t * 0.9:.2f}s"/>'
        for cx, cy, r, t in sorted(DOTS, key=lambda d: d[3])
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 360" width="1200" height="360" role="img" aria-label="{title}">
<style>
  .d {{ fill:#fff; animation:pulse 2.2s ease-in-out infinite; }}
  .cur {{ animation:nudge 2.2s ease-in-out infinite; }}
  @keyframes pulse {{ 0%,100% {{ opacity:.22; }} 35% {{ opacity:1; }} }}
  @keyframes nudge {{ 0%,100% {{ transform:translate(0,0); }} 45% {{ transform:translate(-5px,-5px); }} }}
  @media (prefers-reduced-motion: reduce) {{ .d, .cur {{ animation:none; }} }}
</style>
<rect width="1200" height="360" rx="36" fill="#000"/>
<g transform="translate(60 0) scale(2)">
{dots}
    <polygon class="cur" points="{pts}" fill="#fff" stroke="#000" stroke-width="{OUTLINE * 2:.1f}" stroke-linejoin="round" paint-order="stroke"/>
</g>
<text x="420" y="176" fill="#fff" font-family="{FONT}" font-size="84" font-weight="700" letter-spacing="-1">{title}</text>
<text x="420" y="232" fill="#8b8b95" font-family="{FONT}" font-size="30">{tagline}</text>
<text x="420" y="282" fill="#5f5f69" font-family="{FONT}" font-size="22">{note}</text>
</svg>
"""


(OUT / "icon.svg").write_text(svg())
DOCS = OUT.parent / "docs"
DOCS.mkdir(exist_ok=True)
(DOCS / "banner.svg").write_text(banner(
    "Touchpad", "Turn your iPhone into a trackpad for your Mac.",
    "No app to install  ·  Safari + Wi-Fi  ·  one Python file"))
(DOCS / "banner.ru.svg").write_text(banner(
    "Тачпад", "iPhone вместо трекпада для твоего Mac.",
    "Без приложения  ·  Safari и Wi-Fi  ·  один файл на Python"))
write_png(OUT / "favicon-32.png", 32, 8, True)           # rounded, transparent corners
write_png(OUT / "apple-touch-icon.png", 180, 4, False)   # square: iOS rounds it itself
write_png(OUT / "icon-192.png", 192, 4, False)
write_png(OUT / "icon-512.png", 512, 3, False)
write_png(OUT / "icon-rounded-512.png", 512, 3, True)    # source of the menu bar app's .icns

# centring check: bounding box of everything drawn
bx = [p[0] for p in CUR] + [d[0] - d[2] for d in DOTS] + [d[0] + d[2] for d in DOTS]
by = [p[1] for p in CUR] + [d[1] - d[2] for d in DOTS] + [d[1] + d[2] for d in DOTS]
print(f"icons written to {OUT}; composition box x {min(bx):.1f}..{max(bx):.1f}, y {min(by):.1f}..{max(by):.1f} "
      f"(centre {(min(bx) + max(bx)) / 2:.1f}, {(min(by) + max(by)) / 2:.1f} of 180)")
