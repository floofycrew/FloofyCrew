#!/usr/bin/env python
"""Build the horizontal FloofyCrew wordmark: the chosen mark + "FloofyCrew" set
in Nunito ExtraBold, converted to outlines so the SVG needs no font at render
time.

Nunito is SIL OFL-1.1 (https://github.com/googlefonts/nunito); embedding
outlines in a logo is permitted, the font file itself is NOT committed — it is
downloaded into branding/.cache/ (gitignored) and pinned by sha256.

Run with the two build-time libraries (they never ship anywhere):

    uv run --no-project --with fonttools --with uharfbuzz python branding/build_wordmark.py

Outputs (branding/):
    wordmark.svg        dark text, for light surfaces
    wordmark-dark.svg   light text, for dark surfaces
    wordmark-text.svg   text only (no mark), dark
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gen_logos as g  # noqa: E402

HERE = Path(__file__).parent
CACHE = HERE / ".cache"
FONT_URL = "https://github.com/googlefonts/nunito/raw/main/fonts/variable/Nunito%5Bwght%5D.ttf"

FONT_FILE = CACHE / "Nunito[wght].ttf"
WEIGHT = 800
TEXT_A, TEXT_B = "Floofy", "Crew"
PALETTE = g.PALETTES["sunset"]

# lockup geometry (user units)
H = 128  # lockup height = mark size
GAP = 22
FONT_PX = 84


def fetch_font() -> Path:
    CACHE.mkdir(exist_ok=True)
    if not FONT_FILE.exists():
        print("downloading", FONT_URL)
        urllib.request.urlretrieve(FONT_URL, FONT_FILE)
    digest = hashlib.sha256(FONT_FILE.read_bytes()).hexdigest()
    pinned = (HERE / ".font-sha256").read_text().strip() if (HERE / ".font-sha256").exists() else ""
    if pinned and digest != pinned:
        raise SystemExit(f"font sha256 mismatch: {digest} != pinned {pinned}")
    if not pinned:
        (HERE / ".font-sha256").write_text(digest + "\n")
        print("pinned font sha256", digest)
    return FONT_FILE


def shape(font_path: Path, text: str, size: float):
    """Return (glyph paths, advance) for text at size px, kerned via HarfBuzz."""
    import uharfbuzz as hb
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    static = instancer.instantiateVariableFont(TTFont(font_path), {"wght": WEIGHT})
    upem = static["head"].unitsPerEm
    scale = size / upem
    glyph_set = static.getGlyphSet()
    order = static.getGlyphOrder()

    blob = hb.Blob(font_path.read_bytes())
    face = hb.Face(blob)
    hb_font = hb.Font(face)
    hb_font.set_variations({"wght": WEIGHT})
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(hb_font, buf, {"kern": True, "liga": True})

    paths, x = [], 0.0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        name = order[info.codepoint]
        pen = SVGPathPen(glyph_set)
        # font units -> px, flip y, translate to pen position
        tpen = TransformPen(pen, (scale, 0, 0, -scale, x + pos.x_offset * scale, -pos.y_offset * scale))
        glyph_set[name].draw(tpen)
        d = pen.getCommands()
        if d:
            paths.append(d)
        x += pos.x_advance * scale
    metrics = static["OS/2"]
    cap = getattr(metrics, "sCapHeight", 0) * scale or size * 0.7
    xh = getattr(metrics, "sxHeight", 0) * scale or size * 0.5
    return paths, x, cap, xh


def lockup(mark: bool, dark: bool, font: Path) -> str:
    p = PALETTE
    a_paths, a_adv, cap, _ = shape(font, TEXT_A, FONT_PX)
    b_paths, b_adv, _, _ = shape(font, TEXT_B, FONT_PX)
    text_w = a_adv + b_adv
    x0 = (H + GAP) if mark else 0
    width = x0 + text_w
    # baseline so the cap-height block is vertically centred on the mark
    baseline = H / 2 + cap / 2
    ink = "#FFE4CF" if dark else p.ink  # peach on dark, sunset ink on light
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {g.fmt(width)} {H}" width="{g.fmt(width)}" height="{H}" role="img" aria-label="FloofyCrew">',
        "<defs>",
        f'<linearGradient id="wm" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{p.bg_a}"/><stop offset="1" stop-color="{p.bg_b}"/></linearGradient>',
        "</defs>",
    ]
    if mark:
        # embed the mark scaled to H, stripping its outer <svg> wrapper
        inner = g.fox_mark(p)
        body = inner[inner.index(">") + 1 : inner.rindex("</svg>")]
        body = body.replace('id="bg"', 'id="bg-mark"').replace("url(#bg)", "url(#bg-mark)")
        parts.append(f'<g transform="scale({g.fmt(H / g.S)})">{body}</g>')
    parts.append(f'<g transform="translate({g.fmt(x0)} {g.fmt(baseline)})">')
    parts.append(f'<g fill="{ink}">' + "".join(f'<path d="{d}"/>' for d in a_paths) + "</g>")
    parts.append(f'<g fill="url(#wm)" transform="translate({g.fmt(a_adv)} 0)">' + "".join(f'<path d="{d}"/>' for d in b_paths) + "</g>")
    parts.append("</g></svg>")
    return "\n".join(parts)


def main() -> None:
    font = fetch_font()
    (HERE / "wordmark.svg").write_text(lockup(True, False, font) + "\n")
    (HERE / "wordmark-dark.svg").write_text(lockup(True, True, font) + "\n")
    (HERE / "wordmark-text.svg").write_text(lockup(False, False, font) + "\n")
    for n in ("wordmark.svg", "wordmark-dark.svg", "wordmark-text.svg"):
        print("wrote", HERE / n, (HERE / n).stat().st_size, "bytes")


if __name__ == "__main__":
    main()
