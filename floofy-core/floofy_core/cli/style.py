"""ANSI styling for the CLI's human output (Requirement 15.1).

A :class:`Style` carries the colour depth the terminal supports and paints text
with a small **semantic palette** — ``heading``, ``ok``, ``warn``, ``danger``,
``muted``, ``accent`` — plus the weights ``bold``, ``dim`` and ``italic``. Every
painter returns its input unchanged when styling is off, so callers can paint
freely without changing what the text *says*: the plain text of a line is the
same with and without colour, and the transcript the manager UI reads stays
plain (:meth:`floofy_core.cli.console.Console.say` strips the sequences).

Depth detection (:meth:`Style.detect`), in the order the switches win:

1. ``--no-color`` or ``--json`` → off (``--json`` output is byte-identical to a
   run without styling: the JSON document never goes through a painter);
2. ``NO_COLOR`` present in the environment, with any value → off
   (https://no-color.org, taken at its strictest: presence is enough);
3. ``TERM=dumb`` → off;
4. stdout not a terminal → off, unless ``FORCE_COLOR`` is set (tests, and users
   who pipe through a pager that understands colour);
5. ``COLORTERM`` ``truecolor``/``24bit`` → :attr:`Depth.TRUECOLOR`; else a
   ``TERM`` naming ``256color`` (or ``direct``) → :attr:`Depth.EXTENDED`; else
   :attr:`Depth.BASIC` (the 16 ANSI colours).

The palette's colours come from the FloofyCrew branding (the sunset card
``#FF6B8B → #FF9A3D``, ``branding/README.md``) where the depth allows, and
from the nearest of the 16 ANSI colours otherwise. Standard library only.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import IntEnum
from typing import Mapping

__all__ = ["Depth", "PALETTE", "Style", "strip_ansi"]

#: Matches every SGR sequence a :class:`Style` emits (and any other ``CSI … m``).
_SGR = re.compile(r"\x1b\[[0-9;]*m")

RESET = "\x1b[0m"


class Depth(IntEnum):
    """How many colours the terminal renders; ``OFF`` means no styling at all."""

    OFF = 0
    BASIC = 16
    EXTENDED = 256
    TRUECOLOR = 16_777_216


#: ``name -> (16-colour SGR, 256-colour SGR, 24-bit SGR)`` — the parameters between ``ESC[`` and ``m``.
PALETTE: dict[str, tuple[str, str, str]] = {
    # bold + the sunset orange
    "heading": ("1;33", "1;38;5;215", "1;38;2;255;154;61"),
    "ok": ("32", "38;5;78", "38;2;80;200;120"),
    "warn": ("33", "38;5;214", "38;2;255;179;71"),
    "danger": ("1;91", "1;38;5;203", "1;38;2;255;95;115"),
    # dim on 16 colours: "bright black" is invisible on some dark schemes
    "muted": ("2", "38;5;245", "38;2;160;160;170"),
    # the sunset pink
    "accent": ("95", "38;5;211", "38;2;255;107;139"),
}

WEIGHTS: dict[str, str] = {"bold": "1", "dim": "2", "italic": "3"}


def strip_ansi(text: str) -> str:
    """``text`` without any SGR sequence — what a line *says*."""
    return _SGR.sub("", text)


@dataclass(frozen=True)
class Style:
    """Painters for one output stream at one colour depth."""

    depth: Depth = Depth.OFF
    #: ``FORCE_COLOR`` was set: styling was requested even though the stream is not a terminal.
    forced: bool = False

    # -- construction ----------------------------------------------------------------------

    @classmethod
    def off(cls) -> "Style":
        return cls(Depth.OFF)

    @classmethod
    def detect(cls, *, env: Mapping[str, str] | None = None, is_tty: bool, no_color: bool = False) -> "Style":
        """The depth the switches allow (module docstring); ``is_tty`` is stdout's."""
        environment = os.environ if env is None else env
        if no_color or "NO_COLOR" in environment:
            return cls(Depth.OFF)
        term = (environment.get("TERM") or "").strip().lower()
        if term == "dumb":
            return cls(Depth.OFF)
        forced = bool((environment.get("FORCE_COLOR") or "").strip()) and environment.get("FORCE_COLOR", "").strip() != "0"
        if not is_tty and not forced:
            return cls(Depth.OFF)
        return cls(depth_from_terminal(environment), forced=forced and not is_tty)

    # -- painters ----------------------------------------------------------------------------

    @property
    def on(self) -> bool:
        return self.depth != Depth.OFF

    def sgr(self, params: str) -> str:
        """``ESC[<params>m`` at this depth, or ``""`` when off."""
        return f"\x1b[{params}m" if self.on else ""

    def params(self, tone: str) -> str:
        """The SGR parameters ``tone`` (a palette colour or a weight) paints with at this depth; ``""`` when off."""
        if not self.on:
            return ""
        if tone in PALETTE:
            return PALETTE[tone][_column(self.depth)]
        if tone in WEIGHTS:
            return WEIGHTS[tone]
        raise KeyError(f"unknown tone {tone!r}; palette: {', '.join(PALETTE)}; weights: {', '.join(WEIGHTS)}")

    def rgb_params(self, red: int, green: int, blue: int, *, basic: str = "95") -> str:
        """The SGR parameters for an exact colour at this depth: 24-bit as given, 256 as the nearest cube entry, 16 as ``basic``."""
        if not self.on:
            return ""
        if self.depth == Depth.TRUECOLOR:
            return f"38;2;{red};{green};{blue}"
        if self.depth == Depth.EXTENDED:
            return f"38;5;{cube_index(red, green, blue)}"
        return basic

    def paint(self, text: str, tone: str) -> str:
        """``text`` in the palette colour ``tone`` (or weight), unchanged when off or when ``text`` is empty."""
        if not self.on or not text:
            return text
        return f"\x1b[{self.params(tone)}m{text}{RESET}"

    def heading(self, text: str) -> str:
        return self.paint(text, "heading")

    def ok(self, text: str) -> str:
        return self.paint(text, "ok")

    def warn(self, text: str) -> str:
        return self.paint(text, "warn")

    def danger(self, text: str) -> str:
        return self.paint(text, "danger")

    def muted(self, text: str) -> str:
        return self.paint(text, "muted")

    def accent(self, text: str) -> str:
        return self.paint(text, "accent")

    def bold(self, text: str) -> str:
        return self.paint(text, "bold")

    def dim(self, text: str) -> str:
        return self.paint(text, "dim")

    def italic(self, text: str) -> str:
        return self.paint(text, "italic")

    def rgb(self, text: str, red: int, green: int, blue: int, *, basic: str = "95") -> str:
        """``text`` in an exact colour: 24-bit as given, 256 as the nearest cube entry, 16 as ``basic``."""
        if not self.on or not text:
            return text
        return f"\x1b[{self.rgb_params(red, green, blue, basic=basic)}m{text}{RESET}"


def depth_from_terminal(env: Mapping[str, str]) -> Depth:
    """``COLORTERM``/``TERM`` → the depth a terminal that *does* render colour supports."""
    colorterm = (env.get("COLORTERM") or "").strip().lower()
    if colorterm in ("truecolor", "24bit"):
        return Depth.TRUECOLOR
    term = (env.get("TERM") or "").strip().lower()
    if "256color" in term or "direct" in term:
        return Depth.EXTENDED
    return Depth.BASIC


def cube_index(red: int, green: int, blue: int) -> int:
    """The xterm 256-colour index nearest to an RGB triple (the 6×6×6 cube, 16–231)."""

    def level(value: int) -> int:
        return 0 if value < 48 else 1 if value < 115 else (value - 35) // 40

    return 16 + 36 * level(red) + 6 * level(green) + level(blue)


def _column(depth: Depth) -> int:
    if depth == Depth.TRUECOLOR:
        return 2
    if depth == Depth.EXTENDED:
        return 1
    return 0
