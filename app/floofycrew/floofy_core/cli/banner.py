"""The FloofyCrew terminal banner (Requirement 15.5).

The art is ``branding/ascii/floofy.json`` — built by ``branding/build_ascii.py``
from the mark itself (``branding/logo-nobg.svg`` rasterised and quantised to the
branding palette) — shipped as the package data file
``floofy_core/cli/banner.json`` (a byte-identical copy, pinned by a test) and
read through :func:`floofy_core.resources.read_package_text` so the zipapp can
read it too. The document is ``{"name", "width", "height", "source", "rows":
[[[char, [r, g, b]], …], …]}``: one entry per cell; a space is a transparent
cell and carries no colour of its own.

The art is printed either on its own (:meth:`Console.show_banner`) or as a
left column with text beside it (:meth:`Console.open_banner_column`): ``floofy
doctor`` puts its header block to the right of the fox.

:func:`render` produces the banner for a colour depth:

* :attr:`Depth.TRUECOLOR` — per-cell ``38;2;r;g;b`` runs (consecutive cells of
  one colour share a sequence), exactly the art's colours;
* :attr:`Depth.EXTENDED` — the same, quantised to the xterm 6×6×6 cube;
* :attr:`Depth.BASIC` — a single-colour rendering in bright magenta
  (``ESC[95m``, the closest of the 16 ANSI colours to the sunset pink);
* :attr:`Depth.OFF` — ``None``: the banner is omitted when colours are off.

The banner is also omitted (``None``) when the terminal is narrower than the art
plus its margins (:data:`MARGIN` columns, half on each side). Callers that print
it (``floofy --version``, ``floofy doctor``, the interactive ``floofy``) do so
only on a terminal; ``--json`` never carries it. Standard library only.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from functools import lru_cache
from typing import Sequence

from ..resources import read_package_text
from .style import RESET, Depth, cube_index

__all__ = [
    "BASIC_COLOR", "Banner", "MARGIN", "WORDMARK_GAP", "WORDMARK_NAME_RGB", "WORDMARK_CREW_RGB",
    "fits", "load", "load_wordmark", "render", "render_lines", "wordmark_columns", "wordmark_fits", "wordmark_top",
]

#: Columns the banner needs beyond the art's width (half on each side).
MARGIN = 4
#: Columns between the fox and the wordmark drawn to its right (the interactive ``floofy``).
WORDMARK_GAP = 3
#: The plain-text wordmark used when the block wordmark does not fit: "Floofy" in the peach of
#: ``wordmark-dark.svg``, "Crew" in the sunset orange the gradient ends on.
WORDMARK_NAME_RGB = (0xFF, 0xE4, 0xCF)
WORDMARK_CREW_RGB = (0xFF, 0x9A, 0x3D)

#: The single colour of the 16-colour rendering: bright magenta, nearest the sunset pink.
BASIC_COLOR = "95"

Cell = tuple[str, tuple[int, int, int]]


@dataclass(frozen=True)
class Banner:
    """The parsed art: ``rows`` of ``(char, (r, g, b))`` cells."""

    name: str
    width: int
    height: int
    source: str
    rows: tuple[tuple[Cell, ...], ...]

    @classmethod
    def from_document(cls, document: dict) -> "Banner":
        rows = tuple(tuple((str(cell[0]), (int(cell[1][0]), int(cell[1][1]), int(cell[1][2]))) for cell in row) for row in document["rows"])
        return cls(str(document.get("name") or "banner"), int(document["width"]), int(document["height"]), str(document.get("source") or ""), rows)

    @property
    def text_rows(self) -> tuple[str, ...]:
        """The glyphs only (what ``floofy.txt`` holds)."""
        return tuple("".join(ch for ch, _ in row) for row in self.rows)

    @property
    def colours(self) -> frozenset[tuple[int, int, int]]:
        return frozenset(rgb for row in self.rows for _, rgb in row)


@lru_cache(maxsize=1)
def load() -> Banner:
    """The bundled art (read once)."""
    return Banner.from_document(json.loads(read_package_text(__package__, "banner.json")))


@lru_cache(maxsize=1)
def load_wordmark() -> Banner:
    """The bundled "FloofyCrew" wordmark (``branding/ascii/wordmark.json``, from ``wordmark-text.svg``): "Floofy" in
    peach, "Crew" in the pink→orange gradient, seven cells tall. Drawn to the right of the fox, vertically centred."""
    return Banner.from_document(json.loads(read_package_text(__package__, "wordmark.json")))


def fits(width: int | None = None, *, banner: Banner | None = None) -> bool:
    """Whether a terminal ``width`` columns wide (default: the current one) has room for the art plus margins."""
    art = banner or load()
    columns = shutil.get_terminal_size().columns if width is None else width
    return columns >= art.width + MARGIN


def wordmark_columns(banner: Banner | None = None, wordmark: Banner | None = None) -> int:
    """Columns the fox + gap + block wordmark occupy, including the left margin only: the wordmark's
    last column may be the terminal's last column (110 with the shipped arts)."""
    art, mark = banner or load(), wordmark or load_wordmark()
    return MARGIN // 2 + art.width + WORDMARK_GAP + mark.width


def wordmark_fits(width: int | None = None, *, banner: Banner | None = None, wordmark: Banner | None = None) -> bool:
    """Whether the block wordmark fits to the right of the art."""
    columns = shutil.get_terminal_size().columns if width is None else width
    return columns >= wordmark_columns(banner, wordmark)


def wordmark_top(banner: Banner | None = None, wordmark: Banner | None = None) -> int:
    """Row offset (from the art's top row) that centres the wordmark on the art's height."""
    art, mark = banner or load(), wordmark or load_wordmark()
    return max(0, (art.height - mark.height) // 2)


def render_lines(depth: Depth, width: int | None = None, *, banner: Banner | None = None, indent: int = MARGIN // 2) -> list[str] | None:
    """The banner's lines for ``depth``, or ``None`` when colours are off or the terminal is too narrow."""
    if depth == Depth.OFF:
        return None
    art = banner or load()
    if not fits(width, banner=art):
        return None
    pad = " " * indent
    if depth == Depth.BASIC:
        return [f"{pad}\x1b[{BASIC_COLOR}m{text}{RESET}" for text in art.text_rows]
    lines: list[str] = []
    for row in art.rows:
        parts: list[str] = [pad]
        last: tuple[int, int, int] | None = None
        for ch, rgb in row:
            if ch == " ":  # transparent cell: no colour of its own
                parts.append(" ")
                continue
            if rgb != last:
                parts.append(_colour_sequence(depth, rgb))
                last = rgb
            parts.append(ch)
        parts.append(RESET)
        lines.append("".join(parts))
    return lines


def render(depth: Depth, width: int | None = None, *, banner: Banner | None = None) -> str | None:
    """:func:`render_lines` joined with newlines (trailing newline included), or ``None``."""
    lines = render_lines(depth, width, banner=banner)
    return None if lines is None else "\n".join(lines) + "\n"


def _colour_sequence(depth: Depth, rgb: Sequence[int]) -> str:
    red, green, blue = rgb
    if depth == Depth.TRUECOLOR:
        return f"\x1b[38;2;{red};{green};{blue}m"
    return f"\x1b[38;5;{cube_index(red, green, blue)}m"
