"""The one-time consent as a full-screen confirmation (Requirement 15.3, 11.1).

On a terminal (stdin *and* stdout are TTYs) ``floofy init`` shows the warning of
:data:`floofy_core.consent.WARNING_TEXT` on the **alternate screen buffer**
(``ESC[?1049h``): the banner when it fits, the text wrapped to the terminal's
width and scrollable when it is taller than the screen, and a single focused
``[ I AGREE ]`` control. **Enter** accepts; **Esc** or **q** declines. The
previous screen is restored afterwards, always (``ESC[?1049l`` in a ``finally``
that also covers a SIGINT/KeyboardInterrupt). The acknowledgement is then
recorded exactly as today — :func:`floofy_core.consent.write_consent` with
``how="screen"`` — so the record only differs from the typed path in that one
field; ``--i-accept-the-risk`` (``how="flag"``) is untouched, and without a
terminal the typed ``I ACCEPT`` prompt (``how="typed"``) is used as before.

Typed governance confirmations (Requirement 11.4) are **not** this screen and
never become a button: they stay :meth:`floofy_core.cli.console.Console.typed`.

Standard library only: ``termios``/``tty`` raw mode and ``select``; no curses,
so it needs no terminfo and composes with the plain CLI. :func:`compose` is the
pure layout the interactive ``floofy`` (curses) reuses for its own consent
screen so both show the same thing.
"""
from __future__ import annotations

import os
import select
import shutil
import sys
import textwrap
from dataclasses import dataclass, field
from typing import IO

from ..consent import WARNING_TEXT
from .style import Depth, Style, strip_ansi

__all__ = ["CONTROL", "ConsentScreen", "Frame", "TITLE", "compose", "show"]

ENTER_ALTERNATE = "\x1b[?1049h"
LEAVE_ALTERNATE = "\x1b[?1049l"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"
CLEAR = "\x1b[2J\x1b[H"

TITLE = "READ THIS ONCE — FloofyCrew consent"
CONTROL = "[ I AGREE ]"
HINT = "Enter agrees · Esc or q declines · ↑/↓ scroll"
#: Rows the footer takes: a blank line, the control, the hint.
FOOTER_ROWS = 3
#: Minimum terminal size for the screen; below it the caller falls back to the typed prompt.
MIN_COLUMNS, MIN_ROWS = 40, 8


@dataclass(frozen=True)
class Frame:
    """One drawing of the screen: ``body`` rows (already clipped to the scroll window) and the footer."""

    body: list[str]
    control: str
    hint: str
    scroll: int
    max_scroll: int
    more_above: bool
    more_below: bool

    @property
    def rows(self) -> list[str]:
        return [*self.body, "", self.control, self.hint]


def body_lines(width: int, *, text: str = WARNING_TEXT, banner_lines: list[str] | None = None) -> list[str]:
    """Everything above the footer at ``width`` columns: banner (optional), title, the wrapped text."""
    inner = max(MIN_COLUMNS - 4, width - 4)
    lines: list[str] = []
    if banner_lines:
        lines.extend(banner_lines)
        lines.append("")
    lines.append("  " + TITLE)
    lines.append("  " + "=" * min(len(TITLE), inner))
    lines.append("")
    for paragraph in text.rstrip().split("\n\n"):
        for wrapped in textwrap.wrap(" ".join(paragraph.split()), width=inner) or [""]:
            lines.append("  " + wrapped)
        lines.append("")
    return lines


def compose(width: int, height: int, *, scroll: int = 0, text: str = WARNING_TEXT, banner_lines: list[str] | None = None, style: Style | None = None) -> Frame:
    """The frame for a ``width`` × ``height`` terminal at ``scroll`` (clamped); pure, so the TUI can reuse it."""
    style = style or Style.off()
    lines = body_lines(width, text=text, banner_lines=banner_lines)
    window = max(1, height - FOOTER_ROWS)
    max_scroll = max(0, len(lines) - window)
    scroll = max(0, min(scroll, max_scroll))
    visible = lines[scroll : scroll + window]
    visible += [""] * (window - len(visible))
    pad = max(0, (width - len(CONTROL)) // 2)
    control = " " * pad + (style.sgr("7") + style.accent(CONTROL) + style.sgr("0") if style.on else CONTROL)
    more = []
    if scroll > 0:
        more.append("↑ more above")
    if scroll < max_scroll:
        more.append("↓ more below")
    hint = "  " + style.muted((" · ".join(more) + "   " if more else "") + HINT)
    return Frame(visible, control, hint, scroll, max_scroll, scroll > 0, scroll < max_scroll)


@dataclass
class ConsentScreen:
    """The interactive screen on ``stdin``/``stdout`` (both terminals)."""

    stdin: IO = field(default_factory=lambda: sys.stdin)
    stdout: IO = field(default_factory=lambda: sys.stdout)
    style: Style = field(default_factory=Style.off)
    text: str = WARNING_TEXT
    #: Colour depth for the banner (default: the style's); the banner is skipped when it does not fit.
    banner_depth: Depth | None = None

    @property
    def available(self) -> bool:
        try:
            return bool(self.stdin.isatty()) and bool(self.stdout.isatty())
        except (AttributeError, ValueError):
            return False

    def size(self) -> tuple[int, int]:
        try:
            size = os.get_terminal_size(self.stdout.fileno())
            columns, rows = size.columns, size.lines
        except (OSError, ValueError, AttributeError):
            fallback = shutil.get_terminal_size()
            columns, rows = fallback.columns, fallback.lines
        return (columns or 80), (rows or 24)

    def banner_lines(self, width: int, height: int) -> list[str] | None:
        """The banner rows when colours are on and the screen has room for banner + text + footer."""
        depth = self.style.depth if self.banner_depth is None else self.banner_depth
        if depth == Depth.OFF:
            return None
        from .banner import load, render_lines  # noqa: PLC0415

        art = load()
        text_rows = len(body_lines(width, text=self.text))
        if height < art.height + text_rows + FOOTER_ROWS:
            return None
        return render_lines(depth, width)

    def run(self) -> bool | None:
        """Show the screen; ``True`` accepted, ``False`` declined, ``None`` when it cannot run here."""
        if not self.available:
            return None
        try:
            import termios  # noqa: PLC0415
            import tty  # noqa: PLC0415
        except ImportError:
            return None
        columns, rows = self.size()
        if columns < MIN_COLUMNS or rows < MIN_ROWS:
            return None
        fd = self.stdin.fileno()
        try:
            saved = termios.tcgetattr(fd)
        except termios.error:
            return None
        out = self.stdout
        scroll = 0
        try:
            out.write(ENTER_ALTERNATE + HIDE_CURSOR)
            out.flush()
            tty.setraw(fd)
            frame = self._draw(columns, rows, scroll)
            while True:
                key = self._read_key(fd, timeout=0.5)
                if key is None:  # timeout: redraw on resize
                    new_columns, new_rows = self.size()
                    if (new_columns, new_rows) != (columns, rows):
                        columns, rows = new_columns, new_rows
                        frame = self._draw(columns, rows, scroll)
                    continue
                if key in ("\r", "\n"):
                    return True
                if key in ("\x1b", "q", "Q", "\x03", "\x04"):
                    return False
                if key in ("down", "j"):
                    scroll = min(frame.max_scroll, scroll + 1)
                elif key in ("up", "k"):
                    scroll = max(0, scroll - 1)
                elif key == "pagedown":
                    scroll = min(frame.max_scroll, scroll + max(1, rows - FOOTER_ROWS - 1))
                elif key == "pageup":
                    scroll = max(0, scroll - max(1, rows - FOOTER_ROWS - 1))
                elif key == "end":
                    scroll = frame.max_scroll
                elif key == "home":
                    scroll = 0
                else:
                    continue
                frame = self._draw(columns, rows, scroll)
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, saved)
            finally:
                out.write(SHOW_CURSOR + LEAVE_ALTERNATE)
                out.flush()

    # -- drawing and keys ------------------------------------------------------------------

    def _draw(self, columns: int, rows: int, scroll: int) -> Frame:
        frame = compose(columns, rows, scroll=scroll, text=self.text, banner_lines=self.banner_lines(columns, rows), style=self.style)
        parts = [CLEAR]
        for index, row in enumerate(frame.rows[:rows], start=1):
            parts.append(f"\x1b[{index};1H\x1b[2K{_clip(row, columns)}")
        self.stdout.write("".join(parts))
        self.stdout.flush()
        return frame

    @staticmethod
    def _read_key(fd: int, *, timeout: float) -> str | None:
        """One key: a character, ``"\\x1b"`` for a lone Escape, or a name for an arrow/page key; ``None`` on timeout."""
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return None
        data = os.read(fd, 32)
        if not data:
            return "\x04"
        if data[:1] != b"\x1b":
            return data[:1].decode("utf-8", "replace")
        if len(data) == 1:
            # a lone Escape, or the start of a sequence whose rest is still in flight
            more, _, _ = select.select([fd], [], [], 0.05)
            if not more:
                return "\x1b"
            data += os.read(fd, 32)
        return _SEQUENCES.get(data[:3], _SEQUENCES.get(data[:4], "other"))


_SEQUENCES: dict[bytes, str] = {
    b"\x1b[A": "up",
    b"\x1b[B": "down",
    b"\x1bOA": "up",
    b"\x1bOB": "down",
    b"\x1b[5~": "pageup",
    b"\x1b[6~": "pagedown",
    b"\x1b[H": "home",
    b"\x1b[F": "end",
}


def _clip(row: str, columns: int) -> str:
    """Cut a row to ``columns`` visible characters (sequences are not counted)."""
    if len(strip_ansi(row)) <= columns:
        return row
    out: list[str] = []
    visible = 0
    index = 0
    while index < len(row) and visible < columns:
        if row.startswith("\x1b[", index):
            end = row.find("m", index)
            if end == -1:
                break
            out.append(row[index : end + 1])
            index = end + 1
            continue
        out.append(row[index])
        visible += 1
        index += 1
    return "".join(out) + ("\x1b[0m" if "\x1b[" in row else "")


def show(*, stdin: IO | None = None, stdout: IO | None = None, style: Style | None = None, text: str = WARNING_TEXT) -> bool | None:
    """Convenience: run a :class:`ConsentScreen` on the given streams (defaults: the process's)."""
    screen = ConsentScreen(stdin=stdin if stdin is not None else sys.stdin, stdout=stdout if stdout is not None else sys.stdout, style=style or Style.off(), text=text)
    return screen.run()
