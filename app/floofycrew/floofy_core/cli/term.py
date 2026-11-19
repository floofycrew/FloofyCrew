"""The terminal driver behind the full-screen surfaces (Requirement 15.6, 15.3, 14.2).

The interactive ``floofy`` (:mod:`floofy_core.cli.tui`) and the one-time consent
frame (:mod:`floofy_core.cli.consent_screen`) draw on the same
:class:`Terminal`: standard library only — ``termios``/``tty`` raw mode, ANSI
control sequences and ``select`` — so they need neither ``curses`` nor a terminfo
database. That matters on the host's own interpreter: the ``python3.12`` bundled
with the host, which the internal installer selects, ships without ``_curses`` (it
does have ``termios``, ``tty``, ``fcntl``, ``select`` and ``signal``), and a shell
that never exported ``TERM`` is still an ANSI terminal — ``kiro-cli`` runs its
interface in the same conditions, so ``floofy`` should too.

What the driver does, in one place:

* **modes** — :meth:`Terminal.enter` saves the ``termios`` attributes, switches
  to the alternate screen buffer (``ESC[?1049h``), hides the cursor and puts the
  line discipline in raw mode; :meth:`Terminal.leave` restores all of it in a
  ``finally`` chain that also runs on a ``KeyboardInterrupt`` (a SIGINT from the
  outside — Ctrl-C itself is a key in raw mode, see below);
* **a frame buffer** — :meth:`Terminal.paint` takes the rows of a whole screen,
  diffs them line by line against what is on the terminal and rewrites only the
  rows that changed, each with an absolute cursor move (``ESC[row;1H``) and a
  line erase, so a redraw does not flicker; a size change clears and repaints;
  a text field asks for the cursor at one cell, otherwise it stays hidden;
* **styling** — :class:`Canvas` is a grid of cells the interface writes with the
  palette tones of :class:`floofy_core.cli.style.Style` (``heading``, ``muted``,
  …), exact colours (the banner's cells) and reverse/underline; rendering goes
  through the detected :class:`Style`, so ``NO_COLOR``, ``--no-color`` and the
  colour depth apply here exactly as they do to the plain CLI;
* **size** — :meth:`Terminal.size` is ``os.get_terminal_size()`` on stdout,
  re-read when ``SIGWINCH`` arrives (a self-pipe wakes the key read) and on
  every 0.5 s timeout of :meth:`Terminal.read_key`, whose caller redraws;
* **keys** — :meth:`Terminal.read_key` decodes Enter, Esc (a lone Escape is told
  from the start of an arrow sequence by a short timeout), Tab, Backspace,
  Ctrl-C and the other control characters, the arrows (``ESC[A``–``ESC[D`` and the
  ``ESC O`` forms), Home/End, PgUp/PgDn, Insert/Delete, and returns every
  printable character (``q``, ``j``/``k``, ``/``, digits, UTF-8 text) as itself.
  Keys typed in a burst are queued, not dropped.

:func:`is_dumb` is the one terminal the surfaces refuse: ``TERM=dumb``. An unset
``TERM`` is not refused.
"""
from __future__ import annotations

import os
import select
import shutil
import signal
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import IO, Iterator, Mapping, Sequence

from .style import RESET, Style, strip_ansi

__all__ = ["Attr", "Canvas", "ENTER_ALTERNATE", "LEAVE_ALTERNATE", "PLAIN", "Terminal", "TerminalUnavailable", "clip", "is_dumb"]

ENTER_ALTERNATE = "\x1b[?1049h"
LEAVE_ALTERNATE = "\x1b[?1049l"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"
CLEAR = "\x1b[2J\x1b[H"
ERASE_LINE = "\x1b[2K"

#: How long a lone ``ESC`` waits for the rest of a sequence before it counts as the Escape key.
ESCAPE_DELAY = 0.05
#: The interval at which the size is re-read while waiting for a key (Requirement 15.6).
POLL_INTERVAL = 0.5


class TerminalUnavailable(RuntimeError):
    """The streams are not a terminal the driver can put in raw mode; the caller falls back."""


def is_dumb(env: Mapping[str, str] | None = None) -> bool:
    """``TERM=dumb`` — the one terminal the full-screen surfaces do not attempt (an unset ``TERM`` is fine)."""
    environment = os.environ if env is None else env
    return (environment.get("TERM") or "").strip().lower() == "dumb"


# --- key decoding ----------------------------------------------------------------------------------------------

#: Control characters with a name of their own; every other ``0x01``–``0x1a`` byte is ``ctrl-<letter>``.
_CONTROL: dict[int, str] = {0x0D: "enter", 0x0A: "enter", 0x09: "tab", 0x7F: "backspace", 0x08: "backspace", 0x03: "ctrl-c", 0x1B: "esc"}
#: ``CSI <params> <final letter>``: the final byte names the key (modifiers in the parameters are ignored).
_CSI_FINAL: dict[bytes, str] = {b"A": "up", b"B": "down", b"C": "right", b"D": "left", b"H": "home", b"F": "end", b"Z": "backtab"}
#: ``CSI <number> ~`` (vt220-style keys; ``7``/``8`` are rxvt's Home/End).
_CSI_TILDE: dict[bytes, str] = {b"1": "home", b"2": "insert", b"3": "delete", b"4": "end", b"5": "pageup", b"6": "pagedown", b"7": "home", b"8": "end"}
#: ``ESC O <letter>`` — the arrows and Home/End in application cursor mode.
_SS3: dict[bytes, str] = {b"A": "up", b"B": "down", b"C": "right", b"D": "left", b"H": "home", b"F": "end"}


def decode_sequence(data: bytes) -> str:
    """The key an escape sequence (``ESC`` and what followed it) names, or ``"other"``."""
    if data[:2] == b"\x1b[" and len(data) >= 3:
        final = data[-1:]
        if final == b"~":
            number = data[2:-1].split(b";")[0]
            return _CSI_TILDE.get(number, "other")
        return _CSI_FINAL.get(final, "other")
    if data[:2] == b"\x1bO" and len(data) == 3:
        return _SS3.get(data[2:3], "other")
    return "other"


def _utf8_length(lead: int) -> int:
    if lead < 0x80:
        return 1
    if lead >> 5 == 0b110:
        return 2
    if lead >> 4 == 0b1110:
        return 3
    if lead >> 3 == 0b11110:
        return 4
    return 1


# --- the driver ------------------------------------------------------------------------------------------------


class Terminal:
    """Raw-mode, alternate-screen access to a pair of terminal streams (see the module docstring).

    Use as a context manager, or call :meth:`enter` and :meth:`leave` yourself
    (``leave`` in a ``finally``). :meth:`enter` raises :class:`TerminalUnavailable`
    — before writing anything — when the streams cannot be driven.
    """

    def __init__(self, stdin: IO | None = None, stdout: IO | None = None, *, escape_delay: float = ESCAPE_DELAY):
        self.stdin = stdin if stdin is not None else sys.stdin
        self.stdout = stdout if stdout is not None else sys.stdout
        self.escape_delay = escape_delay
        self._in_fd: int | None = None
        self._saved: list | None = None
        self._pending = b""
        self._painted: list[str] = []
        self._painted_size: tuple[int, int] | None = None
        self._cursor_shown = False
        self._resized = False
        self._wake: tuple[int, int] | None = None
        self._previous_wakeup_fd: int | None = None
        self._previous_winch = None
        self._entered = False

    # -- availability and size -------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """Both streams are terminals (the raw-mode switch is only attempted then)."""
        try:
            return bool(self.stdin.isatty()) and bool(self.stdout.isatty())
        except (AttributeError, ValueError):
            return False

    def size(self) -> tuple[int, int]:
        """``(columns, rows)`` of stdout, from the kernel; ``shutil`` when that is not possible."""
        try:
            size = os.get_terminal_size(self.stdout.fileno())
            columns, rows = size.columns, size.lines
        except (OSError, ValueError, AttributeError):
            fallback = shutil.get_terminal_size()
            columns, rows = fallback.columns, fallback.lines
        return (columns or 80), (rows or 24)

    # -- modes -----------------------------------------------------------------------------------

    def enter(self) -> "Terminal":
        """Switch to raw mode on the alternate screen; :class:`TerminalUnavailable` if the streams cannot be driven."""
        if self._entered:
            return self
        if not self.available:
            raise TerminalUnavailable("stdin and stdout must both be terminals")
        try:
            import termios  # noqa: PLC0415
            import tty  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - not a POSIX interpreter
            raise TerminalUnavailable(f"no termios here ({exc})") from exc
        try:
            fd = self.stdin.fileno()
            saved = termios.tcgetattr(fd)
        except (termios.error, OSError, ValueError, AttributeError) as exc:
            raise TerminalUnavailable(f"stdin cannot be put in raw mode ({exc})") from exc
        self._in_fd, self._saved = fd, saved
        self._entered = True
        try:
            self._install_winch()
            self.stdout.write(ENTER_ALTERNATE + HIDE_CURSOR)  # 1049 switches to a cleared buffer; the first paint clears again at its size
            self.stdout.flush()
            tty.setraw(fd)
        except BaseException:
            self.leave()
            raise
        self._painted, self._painted_size, self._cursor_shown, self._pending = [], None, False, b""
        return self

    def leave(self) -> None:
        """Restore the line discipline, show the cursor and leave the alternate screen — every step guarded."""
        if not self._entered:
            return
        self._entered = False
        try:
            if self._saved is not None and self._in_fd is not None:
                import termios  # noqa: PLC0415

                termios.tcsetattr(self._in_fd, termios.TCSADRAIN, self._saved)
        except Exception:  # noqa: BLE001 - the terminal may be gone; still leave the alternate screen
            pass
        finally:
            try:
                self.stdout.write(SHOW_CURSOR + LEAVE_ALTERNATE)  # every styled segment carries its own reset; nothing to undo here
                self.stdout.flush()
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._remove_winch()
                self._painted, self._painted_size, self._pending = [], None, b""

    def __enter__(self) -> "Terminal":
        return self.enter()

    def __exit__(self, *exc: object) -> None:
        self.leave()

    @contextmanager
    def interruptible(self) -> Iterator[None]:
        """Let Ctrl-C raise ``KeyboardInterrupt`` again while an action runs (``ISIG`` on; Ctrl-Z and Ctrl-\\ stay keys).

        Raw mode makes Ctrl-C an ordinary key so a screen can decide what it means;
        a long-running action, though, should still be interruptible the way the
        plain CLI is.
        """
        if not self._entered or self._in_fd is None:
            yield
            return
        import termios  # noqa: PLC0415

        raw = termios.tcgetattr(self._in_fd)
        attrs = termios.tcgetattr(self._in_fd)
        attrs[3] |= termios.ISIG
        attrs[6] = list(attrs[6])
        attrs[6][termios.VSUSP] = b"\x00"
        attrs[6][termios.VQUIT] = b"\x00"
        try:
            termios.tcsetattr(self._in_fd, termios.TCSANOW, attrs)
        except termios.error:
            yield
            return
        try:
            yield
        finally:
            try:
                termios.tcsetattr(self._in_fd, termios.TCSANOW, raw)
            except termios.error:
                pass

    # -- resize ----------------------------------------------------------------------------------

    def _install_winch(self) -> None:
        """A ``SIGWINCH`` handler that flags the resize and a wakeup pipe so the key read returns at once."""
        if not hasattr(signal, "SIGWINCH"):  # pragma: no cover - not a POSIX interpreter
            return
        try:
            self._previous_winch = signal.signal(signal.SIGWINCH, self._on_winch)
        except (ValueError, OSError):
            # not the main thread: no handler, but the 0.5 s poll still picks a resize up
            self._previous_winch = None
            return
        try:
            read_end, write_end = os.pipe()
        except OSError:
            return
        try:
            os.set_blocking(read_end, False)
            os.set_blocking(write_end, False)
            self._previous_wakeup_fd = signal.set_wakeup_fd(write_end, warn_on_full_buffer=False)
        except (ValueError, OSError):
            self._previous_wakeup_fd = None
            for fd in (read_end, write_end):
                os.close(fd)
            return
        self._wake = (read_end, write_end)

    def _remove_winch(self) -> None:
        if self._previous_winch is not None:
            try:
                signal.signal(signal.SIGWINCH, self._previous_winch)
            except (ValueError, OSError):
                pass
            self._previous_winch = None
        if self._previous_wakeup_fd is not None:
            try:
                signal.set_wakeup_fd(self._previous_wakeup_fd)
            except (ValueError, OSError):
                pass
            self._previous_wakeup_fd = None
        if self._wake is not None:
            for fd in self._wake:
                try:
                    os.close(fd)
                except OSError:
                    pass
            self._wake = None

    def _on_winch(self, *_args: object) -> None:
        self._resized = True

    # -- keys ------------------------------------------------------------------------------------

    def read_key(self, timeout: float | None = POLL_INTERVAL) -> str | None:
        """One key: a printable character as itself, or a name (module docstring); ``None`` on timeout.

        ``"resize"`` when the terminal changed size (the caller redraws), ``"eof"``
        when stdin was closed, ``"other"`` for a sequence the driver does not name.
        """
        assert self._in_fd is not None, "read_key() needs an entered Terminal"
        if self._pending:
            return self._decode()
        fds = [self._in_fd] if self._wake is None else [self._in_fd, self._wake[0]]
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            try:
                ready, _, _ = select.select(fds, [], [], remaining)
            except (OSError, ValueError):
                return "eof"
            woken = self._wake is not None and self._wake[0] in ready
            if woken:
                self._drain_wakeup()
            if self._in_fd in ready:
                try:
                    data = os.read(self._in_fd, 64)
                except OSError:
                    return "eof"
                if not data:
                    return "eof"
                self._pending += data
                return self._decode()
            if self._resized or (woken and self._size_changed()):
                self._resized = False
                return "resize"
            if deadline is not None and time.monotonic() >= deadline:
                return "resize" if self._size_changed() else None

    def _size_changed(self) -> bool:
        return self._painted_size is not None and self._painted_size != self.size()

    def _drain_wakeup(self) -> None:
        assert self._wake is not None
        try:
            while os.read(self._wake[0], 256):
                pass
        except (BlockingIOError, InterruptedError, OSError):
            pass

    def _fill(self, timeout: float) -> bool:
        """Append whatever arrives on stdin within ``timeout``; whether anything did."""
        assert self._in_fd is not None
        try:
            ready, _, _ = select.select([self._in_fd], [], [], timeout)
            if not ready:
                return False
            data = os.read(self._in_fd, 64)
        except (OSError, ValueError):
            return False
        if not data:
            return False
        self._pending += data
        return True

    def _decode(self) -> str:
        data = self._pending
        first = data[0]
        if first == 0x1B:
            if len(data) == 1:
                # a lone Escape, or the start of a sequence whose rest is still in flight
                if not self._fill(self.escape_delay):
                    self._pending = b""
                    return "esc"
                data = self._pending
            if data[1:2] == b"[":
                end = 2
                while True:
                    while end < len(data) and 0x30 <= data[end] <= 0x3F:  # parameter bytes: digits ; : < = > ?
                        end += 1
                    while end < len(data) and 0x20 <= data[end] <= 0x2F:  # intermediate bytes
                        end += 1
                    if end < len(data):
                        break
                    if not self._fill(self.escape_delay):
                        self._pending = b""
                        return "other"
                    data = self._pending
                sequence, self._pending = data[: end + 1], data[end + 1 :]
                return decode_sequence(sequence)
            if data[1:2] == b"O":
                if len(data) < 3 and not self._fill(self.escape_delay):
                    self._pending = b""
                    return "other"
                data = self._pending
                sequence, self._pending = data[:3], data[3:]
                return decode_sequence(sequence)
            # Alt-<key> (ESC then a character): not a key the surfaces use
            length = _utf8_length(data[1])
            self._pending = data[1 + length :]
            return "other"
        if first < 0x20 or first == 0x7F:
            self._pending = data[1:]
            if first in _CONTROL:
                return _CONTROL[first]
            return f"ctrl-{chr(first + 96)}" if 1 <= first <= 26 else "other"
        length = _utf8_length(first)
        while len(data) < length and self._fill(self.escape_delay):
            data = self._pending
        character, self._pending = data[:length], data[length:]
        text = character.decode("utf-8", "replace")
        return text if text.isprintable() else "other"

    # -- painting --------------------------------------------------------------------------------

    def paint(self, rows: Sequence[str], *, cursor: tuple[int, int] | None = None) -> None:
        """Bring the terminal to ``rows`` (styled strings, one per screen row) rewriting only what changed.

        ``cursor=(row, column)`` shows the cursor there (a text field); without it the
        cursor stays hidden. A size change since the last paint clears the screen first.
        """
        columns, height = self.size()
        parts: list[str] = []
        if self._painted_size != (columns, height):
            parts.append(CLEAR)
            self._painted = [""] * height  # a cleared screen is blank rows: only non-empty rows need painting
            self._painted_size = (columns, height)
        wanted = [clip(row, columns) for row in rows[:height]]
        wanted += [""] * (height - len(wanted))
        for index, row in enumerate(wanted):
            previous = self._painted[index] if index < len(self._painted) else None
            if row != previous:
                parts.append(f"\x1b[{index + 1};1H{ERASE_LINE}{row}")
        self._painted = wanted
        if cursor is None:
            if self._cursor_shown:
                parts.append(HIDE_CURSOR)
                self._cursor_shown = False
        else:
            row, column = cursor
            parts.append(f"\x1b[{max(1, min(height, row + 1))};{max(1, min(columns, column + 1))}H{SHOW_CURSOR}")
            self._cursor_shown = True
        if parts:
            self.stdout.write("".join(parts))
            self.stdout.flush()

    def repaint(self) -> None:
        """Forget what is on screen so the next :meth:`paint` redraws everything."""
        self._painted, self._painted_size = [], None


def clip(row: str, columns: int) -> str:
    """Cut a styled row to ``columns`` visible characters (SGR sequences are not counted; a reset closes it)."""
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
    return "".join(out) + (RESET if "\x1b[" in row else "")


# --- the cell grid ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Attr:
    """How a cell is painted: a palette tone or weight, an exact colour, reverse video, underline."""

    tone: str | None = None
    rgb: tuple[int, int, int] | None = None
    reverse: bool = False
    underline: bool = False


PLAIN = Attr()


class Canvas:
    """A ``rows × columns`` grid of styled cells that :meth:`render` turns into the rows :meth:`Terminal.paint` takes."""

    def __init__(self, columns: int, rows: int):
        self.columns = max(1, columns)
        self.rows = max(1, rows)
        self._cells: list[list[tuple[str, Attr]] | None] = [None] * self.rows

    def erase(self) -> None:
        self._cells = [None] * self.rows

    def put(self, row: int, column: int, text: str, tone: str | None = None, *, reverse: bool = False, underline: bool = False, rgb: tuple[int, int, int] | None = None) -> None:
        """Write ``text`` at ``(row, column)``, clipped to the grid; off-grid rows and columns are ignored.

        Control characters (a tab, a newline inside a transcript line) become spaces
        so a row can never move the cursor.
        """
        if row < 0 or row >= self.rows or column >= self.columns or not text:
            return
        if column < 0:
            text, column = text[-column:], 0
        text = "".join(character if character == " " or character.isprintable() else " " for character in text[: self.columns - column])
        if not text:
            return
        line = self._cells[row]
        if line is None:
            line = [(" ", PLAIN)] * self.columns
            self._cells[row] = line
        attr = Attr(tone, rgb, reverse, underline)
        for offset, character in enumerate(text):
            line[column + offset] = (character, attr)

    def render(self, style: Style) -> list[str]:
        """One styled string per row (trailing blank cells dropped), painted through ``style`` at its depth.

        Consecutive cells that paint with the same SGR parameters share one sequence,
        so a 16-colour banner row is a single run even though every cell names its
        own colour.
        """
        out: list[str] = []
        for line in self._cells:
            if line is None:
                out.append("")
                continue
            end = len(line)
            while end > 0 and line[end - 1] == (" ", PLAIN):
                end -= 1
            segments: list[str] = []
            start = 0
            params = [_params(style, attr) for _, attr in line[:end]]
            for index in range(1, end + 1):
                if index == end or params[index] != params[start]:
                    text = "".join(character for character, _ in line[start:index])
                    segments.append(f"\x1b[{params[start]}m{text}{RESET}" if params[start] else text)
                    start = index
            out.append("".join(segments))
        return out


def _params(style: Style, attr: Attr) -> str:
    """The SGR parameters a cell paints with through the style layer — ``""`` when plain or when styling is off."""
    if attr == PLAIN or not style.on:
        return ""
    parts: list[str] = []
    if attr.reverse:
        parts.append("7")
    if attr.underline:
        parts.append("4")
    if attr.rgb is not None:
        parts.append(style.rgb_params(*attr.rgb))
    elif attr.tone:
        parts.append(style.params(attr.tone))
    return ";".join(part for part in parts if part)
