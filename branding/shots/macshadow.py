#!/usr/bin/env python
"""Give a flat screenshot the macOS window look: rounded corners + a soft drop shadow.

One input PNG becomes one output PNG on a TRANSPARENT canvas with generous
margins, so the result drops into a README on either GitHub theme. The window's
corners are rounded, a blurred black silhouette sits under it offset downwards,
and nothing else is touched.

    python branding/shots/macshadow.py .scratch/shots/raw/floofycrew-mods.png \
        --out branding/shots/floofycrew-mods.png

Every knob is in PIXELS OF THE INPUT IMAGE. The captures are taken at
``device_scale_factor=2``, so the defaults here are the 2x numbers (radius 24,
blur 34, offset 24, margin 80); halve them for a 1x capture.

``--bg`` flattens the result onto an opaque colour (e.g. ``#f2f2f4``) instead of
keeping it transparent; the transparent form is the primary one.

Stdlib + Pillow only.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

#: Corner-mask supersampling factor: the mask is drawn this much larger and
#: downscaled, which is what gives the corners their antialiased edge (Pillow's
#: rounded_rectangle is hard-edged).
SUPERSAMPLE = 4


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    """An antialiased 8-bit mask of a rounded rectangle filling ``size``."""
    width, height = size
    if radius <= 0:
        return Image.new("L", size, 255)
    big = Image.new("L", (width * SUPERSAMPLE, height * SUPERSAMPLE), 0)
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, width * SUPERSAMPLE - 1, height * SUPERSAMPLE - 1),
        radius=radius * SUPERSAMPLE,
        fill=255,
    )
    return big.resize(size, Image.LANCZOS)


def shadowed(
    image: Image.Image,
    *,
    radius: int = 24,
    blur: int = 34,
    offset: int = 24,
    margin: int = 80,
    alpha: float = 0.58,
    bg: str | None = None,
) -> Image.Image:
    """``image`` with rounded corners, on a canvas with ``margin`` around it and a soft shadow under it.

    The canvas gets ``offset`` extra room at the bottom on top of ``margin`` so
    the downward-offset shadow is never clipped.
    """
    window = image.convert("RGBA")
    width, height = window.size
    mask = rounded_mask(window.size, radius)
    window.putalpha(mask)

    canvas_size = (width + 2 * margin, height + 2 * margin + offset)
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))

    # the shadow: the window's own silhouette, dimmed, blurred, pushed down
    silhouette = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    tint = Image.new("RGBA", window.size, (0, 0, 0, 255))
    tint.putalpha(mask.point(lambda v: int(v * alpha)))
    silhouette.paste(tint, (margin, margin + offset), tint)
    canvas = Image.alpha_composite(canvas, silhouette.filter(ImageFilter.GaussianBlur(blur)))

    canvas.paste(window, (margin, margin), window)
    if bg:
        flat = Image.new("RGBA", canvas_size, bg)
        canvas = Image.alpha_composite(flat, canvas)
    return canvas


def save_within(image: Image.Image, out: Path, *, max_bytes: int, max_width: int) -> tuple[Path, int, tuple[int, int]]:
    """Write ``image`` to ``out`` under ``max_bytes``, downscaling (never re-encoding lossily) until it fits.

    RGBA is kept throughout: a palette PNG would lose the full alpha ramp the
    shadow needs, so the only lever is resolution.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    current = image
    if current.width > max_width:
        scale = max_width / current.width
        current = current.resize((max_width, max(1, round(current.height * scale))), Image.LANCZOS)
    for _ in range(24):
        current.save(out, format="PNG", optimize=True)
        size = out.stat().st_size
        if size <= max_bytes or current.width < 400:
            return out, size, current.size
        current = current.resize((int(current.width * 0.9), max(1, int(current.height * 0.9))), Image.LANCZOS)
    return out, out.stat().st_size, current.size


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inputs", nargs="+", type=Path, help="input PNG(s)")
    parser.add_argument("--out", type=Path, help="output PNG (one input) or output DIRECTORY (several inputs)")
    parser.add_argument("--radius", type=int, default=24, help="corner radius in input pixels (default 24 = 12 at 1x)")
    parser.add_argument("--blur", type=int, default=34, help="gaussian blur radius of the shadow (default 34)")
    parser.add_argument("--offset", type=int, default=24, help="downward shadow offset (default 24); no horizontal offset")
    parser.add_argument("--margin", type=int, default=80, help="transparent margin on every side (default 80)")
    parser.add_argument("--alpha", type=float, default=0.58, help="shadow opacity 0..1 (default 0.58)")
    parser.add_argument("--bg", default=None, help="flatten onto this colour (e.g. #f2f2f4) instead of transparent")
    parser.add_argument("--max-bytes", type=int, default=1_500_000, help="downscale until the file is this small (default 1.5 MB)")
    parser.add_argument("--max-width", type=int, default=2720, help="cap the output width (default 2720 = 2x1280 + 2x80 margin)")
    args = parser.parse_args(argv)

    many = len(args.inputs) > 1
    if many and (args.out is None or args.out.suffix):
        parser.error("several inputs need --out to be a directory")

    for source in args.inputs:
        if not source.is_file():
            print(f"missing: {source}", file=sys.stderr)
            return 2
        out = (args.out / source.name) if many else (args.out or source.with_name(source.stem + "-shadow.png"))
        with Image.open(source) as handle:
            result = shadowed(
                handle,
                radius=args.radius,
                blur=args.blur,
                offset=args.offset,
                margin=args.margin,
                alpha=args.alpha,
                bg=args.bg,
            )
        path, size, dimensions = save_within(result, out, max_bytes=args.max_bytes, max_width=args.max_width)
        print(f"{path}  {dimensions[0]}x{dimensions[1]}  {size:,} B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
