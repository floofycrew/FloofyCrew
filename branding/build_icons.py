#!/usr/bin/env python
"""Build the FloofyCrew icon set from the chosen mark.

Writes branding/logo.svg (+ logo-mono.svg), renders PNGs at every icon size
with headless Chromium (no rsvg/Pillow on this box), and packs a multi-size
favicon.ico from the PNGs using the PNG-in-ICO container (stdlib only).

Usage: python branding/build_icons.py [--chrome /path/to/chrome]
"""
from __future__ import annotations

import argparse
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gen_logos as g  # noqa: E402

HERE = Path(__file__).parent
ICONS = HERE / "icons"
CHOSEN = "sunset"
SIZES = (16, 32, 48, 64, 128, 180, 192, 256, 512)
ICO_SIZES = (16, 32, 48, 64, 256)
DEFAULT_CHROME = Path.home() / ".cache/ms-playwright/chromium-1244/chrome-linux64/chrome"


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def render(chrome: Path, svg: Path, size: int, out: Path) -> None:
    """Screenshot the SVG scaled to exactly size×size."""
    with tempfile.TemporaryDirectory() as td:
        html = Path(td) / "wrap.html"
        html.write_text(
            "<!doctype html><html><head><meta charset='utf-8'><style>"
            "html,body{margin:0;padding:0;background:transparent;overflow:hidden}"
            f"img{{display:block;width:{size}px;height:{size}px}}"
            f"</style></head><body><img src='{svg.resolve().as_uri()}'></body></html>"
        )
        subprocess.run(
            [
                str(chrome), "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                "--default-background-color=00000000", f"--window-size={size},{size}",
                f"--screenshot={out}", html.as_uri(),
            ],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    got = png_size(out.read_bytes())
    if got != (size, size):
        raise SystemExit(f"{out.name}: expected {size}x{size}, got {got[0]}x{got[1]}")


def write_ico(pngs: dict[int, bytes], out: Path) -> None:
    """ICO container holding PNG-encoded images (supported by every modern browser/OS)."""
    entries = sorted(pngs.items())
    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = len(header) + 16 * len(entries)
    directory, blobs = b"", b""
    for size, data in entries:
        dim = 0 if size >= 256 else size  # 0 encodes 256 in the 1-byte field
        directory += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    out.write_bytes(header + directory + blobs)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chrome", type=Path, default=DEFAULT_CHROME)
    args = ap.parse_args()
    if not args.chrome.exists():
        raise SystemExit(f"chrome not found at {args.chrome}")

    ICONS.mkdir(exist_ok=True)
    palette = g.PALETTES[CHOSEN]

    logo = HERE / "logo.svg"
    logo.write_text(g.fox_mark(palette) + "\n")
    mono = HERE / "logo-mono.svg"
    mono.write_text(g.mono_mark("currentColor") + "\n")
    # the fox alone, transparent — for placing on coloured surfaces and as the ASCII-art source
    nobg = HERE / "logo-nobg.svg"
    nobg.write_text(g.fox_mark(palette, bg=False) + "\n")
    print("wrote", logo, mono, "and", nobg)

    pngs: dict[int, bytes] = {}
    for size in SIZES:
        out = ICONS / f"icon-{size}.png"
        render(args.chrome, logo, size, out)
        pngs[size] = out.read_bytes()
        print(f"wrote {out} ({len(pngs[size])} bytes)")

    # conventional names
    (ICONS / "apple-touch-icon.png").write_bytes(pngs[180])
    (ICONS / "favicon-32.png").write_bytes(pngs[32])
    (ICONS / "favicon-16.png").write_bytes(pngs[16])
    write_ico({s: pngs[s] for s in ICO_SIZES}, ICONS / "favicon.ico")
    print("wrote", ICONS / "favicon.ico", "sizes", ICO_SIZES)


if __name__ == "__main__":
    main()
