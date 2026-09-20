#!/usr/bin/env python
"""Build the FloofyCrew terminal banner from the coloured ASCII-art HTML export.

Input:  ``branding/ascii/source.html`` — a ``<pre>`` of ``<span style="color: rgb(r, g, b)">chars</span>``
        runs, one line per art row (the export ***REMOVED*** produced on 2026-09-20).
Output: ``branding/ascii/floofy.json`` — ``{"width", "height", "rows": [[[char, [r, g, b]], …], …]}``,
        one entry per cell, the machine-readable source the CLI renders from
        (Requirement 15.5: truecolor when available, 16-colour fallback, omitted when colours are off);
        ``branding/ascii/floofy.ans``  — the same art as 24-bit ANSI escapes, for a quick ``cat``;
        ``branding/ascii/floofy.txt``  — plain glyphs, no colour.

Standard library only, like everything else in the project. Re-run after replacing ``source.html``.
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent / "ascii"
SPAN = re.compile(r'<span style="color:\s*rgb\((\d+),\s*(\d+),\s*(\d+)\);?">([^<]*)</span>')


def parse(source: str) -> list[list[tuple[str, tuple[int, int, int]]]]:
    body = re.search(r"<code>(.*?)</code>", source, re.S)
    text = body.group(1) if body else source
    rows: list[list[tuple[str, tuple[int, int, int]]]] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip("\r")
        if not line.strip():
            continue
        cells: list[tuple[str, tuple[int, int, int]]] = []
        for r, g, b, chunk in SPAN.findall(line):
            for ch in html.unescape(chunk):
                cells.append((ch, (int(r), int(g), int(b))))
        if cells:
            rows.append(cells)
    return rows


def to_ansi(rows: list[list[tuple[str, tuple[int, int, int]]]]) -> str:
    out: list[str] = []
    for row in rows:
        parts: list[str] = []
        last: tuple[int, int, int] | None = None
        for ch, rgb in row:
            if rgb != last:
                parts.append("\x1b[38;2;%d;%d;%dm" % rgb)
                last = rgb
            parts.append(ch)
        parts.append("\x1b[0m")
        out.append("".join(parts))
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    src = Path(argv[1]) if len(argv) > 1 else HERE / "source.html"
    rows = parse(src.read_text(encoding="utf-8"))
    if not rows:
        print(f"no coloured spans found in {src}", file=sys.stderr)
        return 1
    width = max(len(r) for r in rows)
    doc = {
        "name": "floofycrew-banner",
        "width": width,
        "height": len(rows),
        "source": src.name,
        "rows": [[[ch, list(rgb)] for ch, rgb in row] for row in rows],
    }
    HERE.mkdir(parents=True, exist_ok=True)
    (HERE / "floofy.json").write_text(json.dumps(doc, separators=(",", ":")) + "\n", encoding="utf-8")
    (HERE / "floofy.ans").write_text(to_ansi(rows), encoding="utf-8")
    (HERE / "floofy.txt").write_text("\n".join("".join(ch for ch, _ in row) for row in rows) + "\n", encoding="utf-8")
    palette = sorted({tuple(rgb) for row in rows for _, rgb in row})
    print(f"{len(rows)} rows x {width} cols, {len(palette)} colours -> {HERE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
