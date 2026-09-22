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

Standard library only: the frame is drawn by the shared terminal driver
:class:`floofy_core.cli.term.Terminal` (``termios`` raw mode, ANSI sequences,
``select``; no curses, no terminfo), so it composes with the plain CLI and runs
on an interpreter without ``_curses``. :func:`compose` is the pure layout and
:func:`present` the key loop; the interactive ``floofy`` calls :func:`present`
on its own, already open, :class:`Terminal`, so both surfaces show the very same
frame drawn by the very same driver.
"""
from __future__ import annotations

import sys
import textwrap
from dataclasses import dataclass, field
from typing import IO

from ..consent import WARNING_TEXT
from .style import Depth, Style
from .term import ENTER_ALTERNATE, LEAVE_ALTERNATE, Terminal, TerminalUnavailable

__all__ = ["CONTROL", "ConsentScreen", "ENTER_ALTERNATE", "Frame", "LEAVE_ALTERNATE", "TITLE", "banner_rows", "compose", "present", "show"]

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


def banner_rows(depth: Depth, width: int, height: int, *, text: str = WARNING_TEXT) -> list[str] | None:
    """The banner's rows at ``depth`` when colours are on and the screen has room for banner + text + footer."""
    if depth == Depth.OFF:
        return None
    from .banner import load, render_lines  # noqa: PLC0415

    art = load()
    text_rows = len(body_lines(width, text=text))
    if height < art.height + text_rows + FOOTER_ROWS:
        return None
    return render_lines(depth, width)


def present(terminal: Terminal, *, style: Style | None = None, text: str = WARNING_TEXT, banner_depth: Depth | None = None) -> bool:
    """Drive the frame on an open :class:`Terminal` until an answer: ``True`` on Enter, ``False`` on Esc/``q``/Ctrl-C/EOF.

    Arrows or ``j``/``k`` scroll by a line, PgUp/PgDn by a screen, Home/End to the
    ends; a resize (or the 0.5 s poll) recomposes at the new size. The frame is
    :func:`compose` at the terminal's size, painted through the driver's
    line-diffing buffer, so the standalone ``floofy init`` screen and the
    interactive ``floofy`` show the same thing.
    """
    style = style or Style.off()
    depth = style.depth if banner_depth is None else banner_depth
    scroll = 0
    while True:
        columns, rows = terminal.size()
        frame = compose(columns, rows, scroll=scroll, text=text, banner_lines=banner_rows(depth, columns, rows, text=text), style=style)
        terminal.paint(frame.rows)
        key = terminal.read_key()
        if key is None or key == "resize":
            continue
        if key == "enter":
            return True
        if key in ("esc", "q", "Q", "ctrl-c", "ctrl-d", "eof"):
            return False
        page = max(1, rows - FOOTER_ROWS - 1)
        if key in ("down", "j"):
            scroll = min(frame.max_scroll, scroll + 1)
        elif key in ("up", "k"):
            scroll = max(0, scroll - 1)
        elif key == "pagedown":
            scroll = min(frame.max_scroll, scroll + page)
        elif key == "pageup":
            scroll = max(0, scroll - page)
        elif key == "end":
            scroll = frame.max_scroll
        elif key == "home":
            scroll = 0


@dataclass
class ConsentScreen:
    """The interactive screen on ``stdin``/``stdout`` (both terminals), opened on its own :class:`Terminal`."""

    stdin: IO = field(default_factory=lambda: sys.stdin)
    stdout: IO = field(default_factory=lambda: sys.stdout)
    style: Style = field(default_factory=Style.off)
    text: str = WARNING_TEXT
    #: Colour depth for the banner (default: the style's); the banner is skipped when it does not fit.
    banner_depth: Depth | None = None

    @property
    def available(self) -> bool:
        return Terminal(self.stdin, self.stdout).available

    def size(self) -> tuple[int, int]:
        return Terminal(self.stdin, self.stdout).size()

    def banner_lines(self, width: int, height: int) -> list[str] | None:
        """The banner rows when colours are on and the screen has room for banner + text + footer."""
        return banner_rows(self.style.depth if self.banner_depth is None else self.banner_depth, width, height, text=self.text)

    def run(self) -> bool | None:
        """Show the screen; ``True`` accepted, ``False`` declined, ``None`` when it cannot run here."""
        if not self.available:
            return None
        columns, rows = self.size()
        if columns < MIN_COLUMNS or rows < MIN_ROWS:
            return None
        terminal = Terminal(self.stdin, self.stdout)
        try:
            terminal.enter()
        except TerminalUnavailable:
            return None
        try:
            return present(terminal, style=self.style, text=self.text, banner_depth=self.banner_depth)
        finally:
            terminal.leave()


def show(*, stdin: IO | None = None, stdout: IO | None = None, style: Style | None = None, text: str = WARNING_TEXT) -> bool | None:
    """Convenience: run a :class:`ConsentScreen` on the given streams (defaults: the process's)."""
    screen = ConsentScreen(stdin=stdin if stdin is not None else sys.stdin, stdout=stdout if stdout is not None else sys.stdout, style=style or Style.off(), text=text)
    return screen.run()
