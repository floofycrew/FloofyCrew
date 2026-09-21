#!/usr/bin/env python
"""Build the FloofyCrew terminal banner from the mark itself.

Pipeline (standard library + the headless Chromium already used by build_icons.py):

1. ``branding/logo-nobg.svg`` (the fox without the card, written by build_icons.py)
   is rendered to a small transparent PNG, ``COLS`` px wide and ``2 * ROWS`` px
   tall — one terminal cell is one pixel wide and two pixels tall, which is the
   usual glyph aspect, so the art keeps the mark's proportions.
2. The PNG is decoded here (8-bit RGBA, zlib + the five PNG filters; no Pillow).
3. Each cell averages its two pixels: alpha coverage picks the glyph from a
   density ramp (space, ``.``, ``+``, ``%``/``#``/``@``), the alpha-weighted colour
   is snapped to the nearest branding colour so the terminal shows crisp flats.
   Eyes (the ink colour) are drawn as ``.`` in a lifted maroon: the real ink is
   near-black and would vanish on a dark terminal.

Output (``branding/ascii/``): ``floofy.json`` — ``{"name", "width", "height",
"source", "rows": [[[char, [r, g, b]], …], …]}``, the document
``floofy_core/cli/banner.json`` must be a byte-identical copy of; ``floofy.ans``
(24-bit ANSI, for ``cat``); ``floofy.txt`` (glyphs only).

Usage: python branding/build_ascii.py [--cols N] [--chrome PATH]
"""
from __future__ import annotations

import argparse
import json
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_logos as g  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "ascii"
SOURCE = HERE / "logo-nobg.svg"
DEFAULT_CHROME = Path.home() / ".cache/ms-playwright/chromium-1244/chrome-linux64/chrome"
DEFAULT_COLS = 34

RGB = tuple[int, int, int]


def _hex(color: str) -> RGB:
    return (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))


PALETTE = g.PALETTES["sunset"]
#: (rgb, glyph for a fully covered cell) — the colours a cell may take.
CLASSES: tuple[tuple[RGB, str], ...] = (
    (_hex(PALETTE.body), "%"),  # peach fluff
    (_hex(PALETTE.inner), "#"),  # inner ear
    (_hex(PALETTE.tip or "#FFFFFF"), "@"),  # white tail tip
    (_hex(PALETTE.ink), "."),  # eyes
)
#: The eyes' terminal colour: the ink lifted so it survives a dark background.
EYE_COLOR: RGB = (0x8A, 0x3B, 0x4C)
#: Coverage thresholds for the partial-cell glyphs.
RAMP = ((0.12, " "), (0.38, "."), (0.68, "+"))


# --------------------------------------------------------------------------
# PNG decoding (8-bit RGBA, non-interlaced — what Chromium writes)
# --------------------------------------------------------------------------
def decode_png(data: bytes) -> tuple[int, int, list[list[tuple[int, int, int, int]]]]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, idat, width = 8, b"", 0
    height = bit_depth = color_type = interlace = 0
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        if kind == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
        pos += 12 + length
    if bit_depth != 8 or color_type not in (6, 2) or interlace:
        raise SystemExit(f"unsupported PNG (depth {bit_depth}, type {color_type}, interlace {interlace})")
    channels = 4 if color_type == 6 else 3
    stride = width * channels
    raw = zlib.decompress(idat)
    rows: list[bytearray] = []
    prev = bytearray(stride)
    offset = 0
    for _ in range(height):
        filter_type = raw[offset]
        line = bytearray(raw[offset + 1 : offset + 1 + stride])
        offset += 1 + stride
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if filter_type == 1:
                line[i] = (line[i] + a) & 0xFF
            elif filter_type == 2:
                line[i] = (line[i] + b) & 0xFF
            elif filter_type == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif filter_type == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        rows.append(line)
        prev = line
    pixels = [[(r[x * channels], r[x * channels + 1], r[x * channels + 2], r[x * channels + 3] if channels == 4 else 255) for x in range(width)] for r in rows]
    return width, height, pixels


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def render_png(chrome: Path, svg: Path, width: int, height: int) -> bytes:
    """Rasterise ``svg`` to exactly width×height with a transparent background."""
    with tempfile.TemporaryDirectory() as td:
        html = Path(td) / "wrap.html"
        out = Path(td) / "art.png"
        html.write_text(
            "<!doctype html><html><head><meta charset='utf-8'><style>"
            "html,body{margin:0;padding:0;background:transparent;overflow:hidden}"
            f"img{{display:block;width:{width}px;height:{height}px}}"
            f"</style></head><body><img src='{svg.resolve().as_uri()}'></body></html>"
        )
        subprocess.run(
            [str(chrome), "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
             "--default-background-color=00000000", f"--window-size={width},{height}", f"--screenshot={out}", html.as_uri()],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return out.read_bytes()


def nearest_class(rgb: RGB) -> int:
    return min(range(len(CLASSES)), key=lambda i: sum((a - b) ** 2 for a, b in zip(CLASSES[i][0], rgb)))


def cells_from_pixels(pixels: list[list[tuple[int, int, int, int]]], cols: int, rows: int) -> list[list[tuple[str, RGB]]]:
    art: list[list[tuple[str, RGB]]] = []
    for row in range(rows):
        cells: list[tuple[str, RGB]] = []
        for col in range(cols):
            pair = (pixels[2 * row][col], pixels[2 * row + 1][col])
            alpha = sum(p[3] for p in pair) / (2 * 255)
            if alpha < RAMP[0][0]:
                cells.append((" ", (0, 0, 0)))
                continue
            # classify each pixel on its own colour (a cell straddling the eye edge would
            # otherwise average ink and peach into something that snaps to the inner-ear tint)
            classes = [nearest_class(p[:3]) for p in pair if p[3] >= 64]
            if any(CLASSES[k][1] == "." for k in classes):  # eyes: always the dot, in the lifted colour
                cells.append((".", EYE_COLOR))
                continue
            top, bottom = pair
            klass = classes[0] if len(classes) == 1 else nearest_class((top if top[3] >= bottom[3] else bottom)[:3])
            colour, full_glyph = CLASSES[klass]
            glyph = full_glyph
            for threshold, partial in RAMP[1:]:
                if alpha < threshold:
                    glyph = partial
                    break
            cells.append((glyph, colour))
        art.append(cells)
    # trim blank rows top and bottom, keep the width (columns are the layout contract)
    while art and all(ch == " " for ch, _ in art[0]):
        art.pop(0)
    while art and all(ch == " " for ch, _ in art[-1]):
        art.pop()
    return art


def to_ansi(rows: list[list[tuple[str, RGB]]]) -> str:
    out: list[str] = []
    for row in rows:
        parts: list[str] = []
        last: RGB | None = None
        for ch, rgb in row:
            if ch == " ":
                parts.append(" ")
                continue
            if rgb != last:
                parts.append("\x1b[38;2;%d;%d;%dm" % rgb)
                last = rgb
            parts.append(ch)
        parts.append("\x1b[0m")
        out.append("".join(parts))
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# the wordmark ("FloofyCrew" from wordmark-text.svg), for the interactive banner
# --------------------------------------------------------------------------
WORDMARK_SOURCE = HERE / "wordmark-text.svg"
DEFAULT_WORDMARK_ROWS = 6
#: "Floofy" on a terminal: the peach of the dark wordmark (the ink of the light one is near-black).
WORDMARK_NAME_COLOR: RGB = _hex("#FFE4CF")


def _svg_size(svg: Path) -> tuple[float, float]:
    import re

    m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg.read_text(encoding="utf-8"))
    assert m, f"{svg}: no viewBox"
    return float(m.group(1)), float(m.group(2))


def _ink_bbox(pixels: list[list[tuple[int, int, int, int]]], threshold: int = 40) -> tuple[int, int, int, int]:
    xs = [x for row in pixels for x, p in enumerate(row) if p[3] > threshold]
    ys = [y for y, row in enumerate(pixels) if any(p[3] > threshold for p in row)]
    return min(xs), min(ys), max(xs), max(ys)


def wordmark_cells(chrome: Path, svg: Path, rows: int) -> list[list[tuple[str, RGB]]]:
    """The wordmark text as ``rows`` cells tall: "Floofy" in peach, "Crew" in the pink→orange gradient.

    Two renders: a coarse one to measure the ink's bounding box in SVG units, then one
    at the scale that makes the ink exactly ``2 * rows`` px tall, cropped to the ink.
    Cells are classified per pixel like the fox: dark pixels are the "Floofy" ink and
    take the peach; anything else is the gradient and keeps its own (averaged) colour.
    """
    units_w, units_h = _svg_size(svg)
    coarse = 4.0  # px per unit for the measuring pass
    _, _, probe = decode_png(render_png(chrome, svg, round(units_w * coarse), round(units_h * coarse)))
    x0, y0, x1, y1 = _ink_bbox(probe)
    ink_h_units = (y1 - y0 + 1) / coarse
    scale = (2 * rows) / ink_h_units
    width, height, pixels = decode_png(render_png(chrome, svg, round(units_w * scale), round(units_h * scale)))
    bx0, by0, bx1, by1 = _ink_bbox(pixels)
    top = by0 - (by0 % 2)  # cells are pixel pairs; start on an even row
    cropped = [row[bx0 : bx1 + 1] for row in pixels[top : top + 2 * rows]]
    while len(cropped) < 2 * rows:
        cropped.append([(0, 0, 0, 0)] * len(cropped[0]))
    cols = len(cropped[0])
    art: list[list[tuple[str, RGB]]] = []
    for r in range(rows):
        cells: list[tuple[str, RGB]] = []
        for c in range(cols):
            pair = (cropped[2 * r][c], cropped[2 * r + 1][c])
            alpha = sum(p[3] for p in pair) / 510
            if alpha < RAMP[0][0]:
                cells.append((" ", (0, 0, 0)))
                continue
            covered = [p for p in pair if p[3] >= 64] or list(pair)
            if all(p[0] < 120 for p in covered):  # the "Floofy" ink
                colour: RGB = WORDMARK_NAME_COLOR
            else:  # the "Crew" gradient: alpha-weighted mean of the covered pixels
                weight = sum(p[3] for p in covered) or 1
                colour = tuple(round(sum(p[i] * p[3] for p in covered) / weight) for i in range(3))  # type: ignore[assignment]
            glyph = "#"
            for threshold, partial in RAMP[1:]:
                if alpha < threshold:
                    glyph = partial
                    break
            cells.append((glyph, colour))
        art.append(cells)
    while art and all(ch == " " for ch, _ in art[-1]):
        art.pop()
    return art


def write_art(name: str, stem: str, source: Path, art: list[list[tuple[str, RGB]]]) -> None:
    doc = {
        "name": name,
        "width": len(art[0]) if art else 0,
        "height": len(art),
        "source": source.name,
        "rows": [[[ch, list(rgb)] for ch, rgb in row] for row in art],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{stem}.json").write_text(json.dumps(doc, separators=(",", ":")) + "\n", encoding="utf-8")
    (OUT / f"{stem}.ans").write_text(to_ansi(art), encoding="utf-8")
    (OUT / f"{stem}.txt").write_text("\n".join("".join(ch for ch, _ in row) for row in art) + "\n", encoding="utf-8")
    palette = sorted({rgb for row in art for ch, rgb in row if ch != " "})
    print(f"{stem}: {len(art)} rows x {doc['width']} cols, {len(palette)} colours -> {OUT}")
    print("\n".join("".join(ch for ch, _ in row) for row in art))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cols", type=int, default=DEFAULT_COLS, help=f"fox art width in cells (default {DEFAULT_COLS})")
    ap.add_argument("--wordmark-rows", type=int, default=DEFAULT_WORDMARK_ROWS, help=f"wordmark height in cells (default {DEFAULT_WORDMARK_ROWS})")
    ap.add_argument("--chrome", type=Path, default=DEFAULT_CHROME)
    ap.add_argument("--source", type=Path, default=SOURCE)
    ap.add_argument("--wordmark-source", type=Path, default=WORDMARK_SOURCE)
    args = ap.parse_args(argv)
    if not args.chrome.exists():
        print(f"chrome not found at {args.chrome}", file=sys.stderr)
        return 2
    for needed, hint in ((args.source, "branding/build_icons.py"), (args.wordmark_source, "branding/build_wordmark.py")):
        if not needed.exists():
            print(f"{needed} missing — run {hint} first", file=sys.stderr)
            return 2
    cols = args.cols
    rows = cols // 2  # the mark is square; a cell is 1×2 px
    width, height, pixels = decode_png(render_png(args.chrome, args.source, cols, rows * 2))
    assert (width, height) == (cols, rows * 2), (width, height)
    fox = cells_from_pixels(pixels, cols, rows)
    # the fox keeps the full requested width (columns are the layout contract)
    write_art("floofycrew-banner", "floofy", args.source, fox)
    mark = wordmark_cells(args.chrome, args.wordmark_source, args.wordmark_rows)
    write_art("floofycrew-wordmark", "wordmark", args.wordmark_source, mark)
    # the two side by side, as the installers print them on a terminal of >= 110 columns
    write_art("floofycrew-banner-wide", "floofy-wide", args.source, compose(fox, mark, WIDE_GAP))
    return 0


#: Columns between the fox and the wordmark in the composed wide banner (matches the CLI's WORDMARK_GAP).
WIDE_GAP = 3


def compose(fox: list[list[tuple[str, RGB]]], mark: list[list[tuple[str, RGB]]], gap: int) -> list[list[tuple[str, RGB]]]:
    """Fox rows with the wordmark to their right, centred on the fox's height (the interactive layout)."""
    top = max(0, (len(fox) - len(mark)) // 2)
    fox_width = len(fox[0])
    blank: tuple[str, RGB] = (" ", (0, 0, 0))
    rows: list[list[tuple[str, RGB]]] = []
    for index in range(max(len(fox), top + len(mark))):
        row = list(fox[index]) if index < len(fox) else [blank] * fox_width
        if top <= index < top + len(mark):
            row += [blank] * gap + list(mark[index - top])
        rows.append(row)
    width = max(len(r) for r in rows)
    return [r + [blank] * (width - len(r)) for r in rows]


if __name__ == "__main__":
    raise SystemExit(main())
