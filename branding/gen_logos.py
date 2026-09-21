#!/usr/bin/env python
"""Generate FloofyCrew logo candidates as SVG.

Visual language borrowed from the KiroCrew icon: a ghost-arch body, two pill
eyes, flat rounded-square background. FloofyCrew adds fluff (a scalloped
outline) and fox / wolf ears, and keeps everything else flat and simple.

Usage:  python branding/gen_logos.py            # writes branding/candidates/*.svg
        python branding/gen_logos.py --sheet    # also writes candidates/index.html
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

OUT = Path(__file__).parent / "candidates"
S = 512  # canvas


def fmt(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


# --------------------------------------------------------------------------
# palettes
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Palette:
    bg_a: str  # gradient start (top-left)
    bg_b: str  # gradient end (bottom-right)
    body: str  # fluff colour
    ink: str  # eyes
    inner: str  # inner ear
    tip: str = ""  # fox tail tip; empty = use ink. Must contrast with BOTH body and bg.


PALETTES = {
    # KiroCrew purple, kept for continuity
    "kiro": Palette("#7C55F5", "#6A3BEA", "#FFFFFF", "#101010", "#C9B8FF", "#2E1B6E"),
    # warm fox
    "ember": Palette("#FF8F3E", "#F05A28", "#FFF6EA", "#1A1210", "#FFB27A", "#8A2E12"),
    # Rimuru sky blue
    "sky": Palette("#63B9F7", "#2F86E3", "#FFFFFF", "#0F1A2B", "#B5DDFB", "#143A73"),
    # wolf at night
    "midnight": Palette("#2B3160", "#161A33", "#E9EDF8", "#0A0C18", "#8E96BD", "#8E96BD"),
    # deep forest
    "forest": Palette("#1F8A70", "#12604C", "#F2FBF6", "#0B1B16", "#8FD9C0", "#8FD9C0"),
    # sakura
    "sakura": Palette("#FF8CB0", "#F25A87", "#FFFFFF", "#2A0F18", "#FFC2D6", "#8C1F45"),
    # inverted: cream card, ember fox body
    "cream": Palette("#FFF4E4", "#FBE3C6", "#F26B2E", "#1A1210", "#FFD1A8", "#8A2E12"),
    # ember fox on charcoal (dark-mode sibling of cream)
    "charcoal": Palette("#2E2420", "#17110F", "#FF8F3E", "#1A1210", "#FFC79A", "#FFC79A"),
    # graphite card, white fox, ember inner ear as the only accent
    "graphite": Palette("#3B414B", "#1F2328", "#FFFFFF", "#101214", "#FF8F3E", "#FF8F3E"),
    # violet dusk
    "dusk": Palette("#5B3FA8", "#3B2A72", "#F3EEFF", "#120B22", "#B79CFF", "#24164A"),
    # sunset: pink -> orange gradient; peach body so the white tail tip reads as lighter
    "sunset": Palette("#FF6B8B", "#FF9A3D", "#FFE4CF", "#2A1014", "#FFBFAE", "#FFFFFF"),
    # mint
    "mint": Palette("#5ED4B5", "#26A98A", "#FFFFFF", "#0B211B", "#BDF0E2", "#0F5C48"),
    # gold
    "gold": Palette("#F7BE4A", "#E0901D", "#FFFBEF", "#1E1508", "#FFDD95", "#7A4A08"),
    # coral
    "coral": Palette("#FF8272", "#E9584A", "#FFFFFF", "#2A0F0C", "#FFC4BB", "#8A2A20"),
    # arctic: pale card, slate-blue fox (light-mode sibling of sky)
    "arctic": Palette("#EAF4FF", "#D3E5FA", "#4B76B8", "#0F1A2B", "#9FC0EA", "#1E3B66"),
}


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------
def arch_points(cx: float, r: float, top_cy: float, bottom: float, n: int):
    """Sample the ghost arch (left side up, over the semicircle, right side down)
    at n+1 evenly spaced points by arc length."""
    side = bottom - top_cy
    total = 2 * side + math.pi * r
    pts = []
    for i in range(n + 1):
        d = total * i / n
        if d <= side:
            pts.append((cx - r, bottom - d))
        elif d <= side + math.pi * r:
            a = math.pi + (d - side) / r
            pts.append((cx + r * math.cos(a), top_cy + r * math.sin(a)))
        else:
            pts.append((cx + r, top_cy + (d - side - math.pi * r)))
    return pts


def scallop_path(pts, bulge: float = 0.56) -> str:
    """Join consecutive points with small outward arcs (cloud edge). Points must
    run clockwise in screen coordinates. bulge = arc radius / chord length."""
    d = [f"M{fmt(pts[0][0])},{fmt(pts[0][1])}"]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        chord = math.hypot(x1 - x0, y1 - y0)
        rr = max(chord * bulge, chord / 2 + 0.01)
        d.append(f"A{fmt(rr)},{fmt(rr)} 0 0 1 {fmt(x1)},{fmt(y1)}")
    d.append("Z")
    return " ".join(d)


def fluffy_ghost(p: Palette, cx: float, r: float, top_cy: float, bottom: float, n: int = 11, bulge: float = 0.56) -> str:
    return f'<path d="{scallop_path(arch_points(cx, r, top_cy, bottom, n), bulge)}" fill="{p.body}"/>'


def eyes(p: Palette, cx: float, cy: float, gap: float = 44, rx: float = 20, ry: float = 30, tilt: float = 8) -> str:
    """Two pill eyes, slightly splayed like Kiro's."""
    return (
        f'<ellipse cx="{fmt(cx - gap)}" cy="{fmt(cy)}" rx="{fmt(rx)}" ry="{fmt(ry)}" '
        f'transform="rotate({-tilt} {fmt(cx - gap)} {fmt(cy)})" fill="{p.ink}"/>'
        f'<ellipse cx="{fmt(cx + gap)}" cy="{fmt(cy)}" rx="{fmt(rx)}" ry="{fmt(ry)}" '
        f'transform="rotate({tilt} {fmt(cx + gap)} {fmt(cy)})" fill="{p.ink}"/>'
    )


def _tri_ear(l, r, tip, tip_round: float, fill: str) -> str:
    """Triangle from base points l, r to tip, with the apex softened by a quad curve."""
    lt = (l[0] + (tip[0] - l[0]) * (1 - tip_round), l[1] + (tip[1] - l[1]) * (1 - tip_round))
    rt = (r[0] + (tip[0] - r[0]) * (1 - tip_round), r[1] + (tip[1] - r[1]) * (1 - tip_round))
    return (
        f'<path d="M{fmt(l[0])},{fmt(l[1])} L{fmt(lt[0])},{fmt(lt[1])} '
        f'Q{fmt(tip[0])},{fmt(tip[1])} {fmt(rt[0])},{fmt(rt[1])} L{fmt(r[0])},{fmt(r[1])} Z" fill="{fill}"/>'
    )


def pointed_ear(p: Palette, bx: float, by: float, dir_: int, h: float, w: float, lean: float,
                tip_round: float = 0.14, inner_scale: float = 0.5, inner: bool = True) -> str:
    """Fox / wolf ear. (bx, by) is the base centre (sits inside the body so the
    fluff hides the seam). dir_=-1 left ear, +1 right ear. lean = outward tilt
    as a fraction of width. Inner ear is the same triangle shrunk toward a point
    ~40 % up the ear so it stays visible above the fluff."""
    tip = (bx + dir_ * w * lean, by - h)
    l, r = (bx - w / 2, by), (bx + w / 2, by)
    out = _tri_ear(l, r, tip, tip_round, p.body)
    if not inner:
        return out
    ox, oy = bx + dir_ * w * lean * 0.45, by - h * 0.42
    sh = lambda q: (ox + (q[0] - ox) * inner_scale, oy + (q[1] - oy) * inner_scale)
    return out + _tri_ear(sh(l), sh(r), sh(tip), tip_round, p.inner)


def fox_ears(p: Palette, cx: float, dx: float, by: float, h: float = 236, w: float = 150) -> str:
    """Tall, slim, leaning outward."""
    return pointed_ear(p, cx - dx, by, -1, h, w, lean=0.42) + pointed_ear(p, cx + dx, by, +1, h, w, lean=0.42)


def wolf_ears(p: Palette, cx: float, dx: float, by: float, h: float = 206, w: float = 178) -> str:
    """Wider base, more upright, slightly blunter tip."""
    return (
        pointed_ear(p, cx - dx, by, -1, h, w, lean=0.2, tip_round=0.2, inner_scale=0.46)
        + pointed_ear(p, cx + dx, by, +1, h, w, lean=0.2, tip_round=0.2, inner_scale=0.46)
    )


# --------------------------------------------------------------------------
# SVG assembly
# --------------------------------------------------------------------------
def svg(p: Palette, body: str, bg: bool = True, title: str = "FloofyCrew") -> str:
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {S} {S}" width="{S}" height="{S}" role="img" aria-label="{title}">',
        "<defs>",
        f'<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{p.bg_a}"/><stop offset="1" stop-color="{p.bg_b}"/></linearGradient>',
        "</defs>",
    ]
    if bg:
        parts.append(f'<rect width="{S}" height="{S}" rx="112" fill="url(#bg)"/>')
    parts.append(body)
    parts.append("</svg>")
    return "\n".join(parts)


# --------------------------------------------------------------------------
# the two marks
# --------------------------------------------------------------------------
def _qbez(b, c, t_, n: int):
    """Quadratic bezier b->t_ with control c, sampled at n+1 points, with unit normals."""
    pts, nrm = [], []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * b[0] + 2 * (1 - t) * t * c[0] + t * t * t_[0]
        y = (1 - t) ** 2 * b[1] + 2 * (1 - t) * t * c[1] + t * t * t_[1]
        dx = 2 * (1 - t) * (c[0] - b[0]) + 2 * t * (t_[0] - c[0])
        dy = 2 * (1 - t) * (c[1] - b[1]) + 2 * t * (t_[1] - c[1])
        L = math.hypot(dx, dy) or 1
        pts.append((x, y))
        nrm.append((-dy / L, dx / L))
    return pts, nrm


def fox_tail(p: Palette, base, ctrl, tip_pt, width: float = 108, tip_from: float = 0.7,
             tip: str | None = None, n: int = 40) -> str:
    """Bushy fox tail: a bent teardrop — thin where it leaves the body, fattest
    mid-way, tapering to a point — with the last stretch in the tip colour cut
    by a chevron. Centreline is a quadratic bezier base->tip_pt bent by ctrl."""
    tip = tip or p.tip or p.ink
    pts, nrm = _qbez(base, ctrl, tip_pt, n)

    def half(t: float) -> float:
        return width / 2 * math.sin(math.pi * t ** 0.8) ** 0.9

    left = [(x + nx * half(i / n), y + ny * half(i / n)) for i, ((x, y), (nx, ny)) in enumerate(zip(pts, nrm))]
    right = [(x - nx * half(i / n), y - ny * half(i / n)) for i, ((x, y), (nx, ny)) in enumerate(zip(pts, nrm))]

    def poly(seq) -> str:
        return "M" + " L".join(f"{fmt(x)},{fmt(y)}" for x, y in seq) + " Z"

    body = poly(left + right[::-1])
    k = int(round(tip_from * n))
    kc = int(round((tip_from - 0.12) * n))  # chevron notch reaches back toward the body
    tip_poly = poly(left[k:] + right[k:][::-1] + [pts[kc]])
    return f'<path d="{body}" fill="{p.body}"/><path d="{tip_poly}" fill="{tip}"/>'


#: Smallest circle enclosing the tailed fox as drawn below — (centre x, centre y, radius),
#: measured on a 512 px raster of the no-background mark (branding/build_ascii.py's decoder).
#: Re-measure if the fox geometry changes.
FOX_CIRCLE = (239.0, 282.0, 278.8)
#: Framing on the card. KiroCrew's sidebar masks app icons to a CIRCLE, so the fox is fitted
#: inside the card's inscribed circle with a margin (radius 240 of 256), not to the square.
CARD_FIT_RADIUS = 240.0
#: Where the fox's circle centre lands on the card: dead centre horizontally, a touch above
#: the middle vertically (reads better than centred; still inside the mask: 240 + 4 < 256).
CARD_FIT_CENTER = (256.0, 252.0)


def card_fit_transform() -> str:
    """SVG transform that fits the drawn fox's enclosing circle into the card's circle."""
    fx, fy, fr = FOX_CIRCLE
    scale = CARD_FIT_RADIUS / fr
    tx, ty = CARD_FIT_CENTER[0] - scale * fx, CARD_FIT_CENTER[1] - scale * fy
    return f"translate({fmt(tx)} {fmt(ty)}) scale({fmt(scale)})"


def fox_mark(p: Palette, bg: bool = True, tail: bool = True) -> str:
    # body shifted left when the tail is on so the tail has room on the right
    cx, r, top, bot = (210, 152, 342, 490) if tail else (256, 172, 328, 490)
    body = ""
    if tail:
        body += fox_tail(p, (cx + 70, 472), (cx + 312, 452), (cx + 222, 168))
    body += fox_ears(p, cx, 102, 276, h=216, w=136) + fluffy_ghost(p, cx, r, top, bot, n=9, bulge=0.54) + eyes(p, cx, 388)
    if bg and tail:
        # on the card the fox is framed for the circular sidebar mask; the bare (no-bg) mark stays tight
        body = f'<g transform="{card_fit_transform()}">{body}</g>'
    return svg(p, body, bg=bg, title="FloofyCrew - fox")


def wolf_mark(p: Palette, bg: bool = True) -> str:
    cx, r, top, bot = 256, 176, 330, 490
    body = wolf_ears(p, cx, 118, 268) + fluffy_ghost(p, cx, r, top, bot, n=11, bulge=0.56) + eyes(p, cx, 380)
    return svg(p, body, bg=bg, title="FloofyCrew - wolf")


def mono_mark(color: str = "#101010") -> str:
    """Single-colour silhouette (fox ears), no background, eyes punched white."""
    p = Palette("", "", color, "#FFFFFF", color)
    cx, r, top, bot = 210, 156, 342, 496
    body = (
        fox_tail(p, (cx + 70, 476), (cx + 316, 456), (cx + 226, 168), tip=color)
        + pointed_ear(p, cx - 104, 276, -1, 220, 140, lean=0.42, inner=False)
        + pointed_ear(p, cx + 104, 276, +1, 220, 140, lean=0.42, inner=False)
        + fluffy_ghost(p, cx, r, top, bot, n=9, bulge=0.54)
        + eyes(p, cx, 384)
    )
    return svg(p, body, bg=False, title="FloofyCrew - monochrome mark")


CANDIDATES = {
    **{f"fox-{k}": (lambda k=k: fox_mark(PALETTES[k])) for k in
       ("ember", "charcoal", "cream", "sky", "arctic", "sunset", "sakura", "coral", "gold", "mint", "dusk", "graphite")},
    "wolf-midnight": lambda: wolf_mark(PALETTES["midnight"]),
    "wolf-forest": lambda: wolf_mark(PALETTES["forest"]),
    "wolf-kiro": lambda: wolf_mark(PALETTES["kiro"]),
    "fox-mono-mark": mono_mark,
}


def write_sheet(names: list[str]) -> None:
    cards = "\n".join(
        f'<figure><div class="pair"><img src="{n}.svg" alt="{n}"><img src="{n}.svg" class="light" alt="{n} on light">'
        f'<img src="{n}.svg" class="tiny" alt="{n} small"></div><figcaption>{n}</figcaption></figure>'
        for n in names
    )
    html = f"""<!doctype html><meta charset="utf-8"><title>FloofyCrew logo candidates</title>
<style>
body{{margin:0;padding:24px;background:#1b1830;color:#eee;font:14px/1.4 system-ui,sans-serif}}
.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:24px}}
figure{{margin:0;background:#26223d;border-radius:16px;padding:16px}}
.pair{{display:flex;gap:16px;align-items:center}}
.pair img{{width:200px;height:200px;border-radius:24px}}
.pair img.light{{background:#f4f2fb;padding:8px;box-sizing:border-box}}
.pair img.tiny{{width:48px;height:48px;border-radius:8px}}
figcaption{{margin-top:10px;opacity:.8}}
</style>
<h1 style="margin:0 0 16px;font-size:18px">FloofyCrew logo candidates</h1>
<div class="grid">{cards}</div>"""
    (OUT / "index.html").write_text(html)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in CANDIDATES.items():
        (OUT / f"{name}.svg").write_text(fn() + "\n")
        print("wrote", OUT / f"{name}.svg")
    if "--sheet" in sys.argv:
        write_sheet(list(CANDIDATES))
        print("wrote", OUT / "index.html")


if __name__ == "__main__":
    main()
