"""Console I/O for the CLI: printing, confirmations and the two non-automatable answers.

Three grades of question, deliberately distinct (Requirement 11.1, 11.4, 11.7):

* :meth:`Console.confirm` — an ordinary yes/no (install a ``python-hook`` mod,
  record the ``agent.apps_trusted`` grant, apply despite the survival warning).
  ``--yes`` answers it, and a non-interactive run without ``--yes`` answers
  ``no`` so nothing mutates on a silent default.
* :meth:`Console.typed` — the per-file typed confirmation for a
  governance-altering target: the user types the exact path. **Never** satisfied
  by ``--yes``; automation supplies each path with
  ``--confirm-governance-target <path>`` (recorded in the audit row as such).
* the one-time consent acknowledgement (``floofy init``): on a terminal the
  full-screen ``[ I AGREE ]`` control (:meth:`Console.ask_consent_screen`,
  Requirement 15.3; ``how="screen"``); otherwise the user types
  :data:`floofy_core.consent.ACCEPT_PHRASE` (``how="typed"``), or automation
  passes ``--i-accept-the-risk`` (``how="flag"``) — handled in the init command.
  ``--yes`` never answers it.

Text output goes to ``out`` (stdout by default); in ``--json`` mode the prose is
suppressed and the command's result document is printed once at the end by
:mod:`floofy_core.cli.main`. Warnings always go to ``err`` so a JSON consumer
still sees them.

Styling (Requirement 15.1): :attr:`Console.style` is the :class:`Style` of
``out`` — detected by :func:`floofy_core.cli.main.execute` from the terminal and
the switches, off for the in-process ``run()`` the manager UI uses. Painted
fragments (:meth:`Console.paint`, ``say(tone=…)``) reach ``out`` with their
sequences and the :attr:`transcript` without them, so what a line *says* never
depends on colour; ``--json`` turns styling off altogether.

Surfaces without a keyboard (the manager App's Loader routes, Requirement 16.3)
plug their answers in through the same three hooks and raise
:class:`ConfirmationNeeded` from a hook when the request carried no answer for
the question being asked: the command unwinds **before** it mutates anything,
:func:`floofy_core.cli.main.execute` turns the exception into exit code
:data:`EXIT_CONFIRMATION_NEEDED` with the question in the result document, and
the route answers ``409`` with it. The question is never answered by default
and never by ``--yes`` (the App has no such flag).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import IO, Any, Callable, Iterable

from .style import Style, strip_ansi

__all__ = ["CONFIRMATION_KINDS", "ConfirmationNeeded", "Console", "EXIT_CONFIRMATION_NEEDED"]

#: ``execute()``'s exit code when a hook raised :class:`ConfirmationNeeded` (the route maps it to HTTP 409).
EXIT_CONFIRMATION_NEEDED = 4
#: Columns between the banner art and the text printed beside it.
BANNER_GAP = "  "

#: ``kind`` → what the client must present and send back (design "Manager App — confirmation protocol").
CONFIRMATION_KINDS: dict[str, str] = {
    "consent": "agree",  # the one-time warning: the [ I AGREE ] control → confirmations.consent = true
    "governance-target": "typed-path",  # a governance-altering file: the exact path typed → confirmations.governanceTargets[]
    "unlisted-source": "accept",  # a git reference: the disclosure + I ACCEPT → confirmations.unlistedSource = "I ACCEPT"
    "unsigned-index": "confirm",  # --allow-unsigned on a registry source: the loosening warning → confirmations.unsignedIndex = true
    "yes-no": "confirm",  # an ordinary confirmation → confirmations.yes = true (or the exact prompt in a list)
}


class ConfirmationNeeded(Exception):
    """A question the surface cannot answer from what it was given; carries what the client must present.

    ``kind`` is one of :data:`CONFIRMATION_KINDS`, ``text`` the prompt (or the
    warning text) as the CLI would print it, ``expects`` the answer shape,
    ``targets`` the governance paths a typed answer must equal byte for byte,
    ``detail`` a machine-readable note (``missing`` / ``mismatch``).
    """

    def __init__(self, kind: str, text: str, *, targets: Iterable[str] = (), detail: str = "", expects: str | None = None):
        super().__init__(f"{kind}: {text}")
        self.kind = kind
        self.text = text
        self.expects = expects or CONFIRMATION_KINDS.get(kind, "confirm")
        self.targets = list(targets)
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "text": self.text, "expects": self.expects, "targets": list(self.targets), "detail": self.detail}


@dataclass
class Console:
    out: IO[str] = field(default_factory=lambda: sys.stdout)
    err: IO[str] = field(default_factory=lambda: sys.stderr)
    input_fn: Callable[[str], str] | None = None
    #: ``--yes``: ordinary confirmations answer yes.
    assume_yes: bool = False
    #: No terminal to ask on (``--json``, the UI's in-process call, a timer): questions get their default.
    non_interactive: bool = False
    #: ``--json``: prose suppressed.
    quiet: bool = False
    #: Pre-supplied typed answers (``--confirm-governance-target``), consumed by :meth:`typed`.
    pre_typed: set[str] = field(default_factory=set)
    #: Everything printed, for callers that capture (the UI's in-process run). Always plain text.
    transcript: list[str] = field(default_factory=list)
    #: The styling of ``out``; ``None`` until :func:`floofy_core.cli.main.execute` detects it (off by default).
    style: Style | None = None
    #: A host-supplied consent screen (the interactive ``floofy`` draws it on its own terminal): ``() -> accepted | None``.
    consent_fn: Callable[[], bool | None] | None = None
    #: A host-supplied yes/no control for :meth:`confirm` (the interactive ``floofy``): ``(prompt, default) -> answer``.
    confirm_fn: Callable[[str, bool], bool | None] | None = None
    #: A host-supplied text field for :meth:`typed` (the interactive ``floofy``): ``(prompt, what) -> text``. Never a button.
    typed_fn: Callable[[str, str], str | None] | None = None
    #: What ``consent.json`` records as ``how`` when :attr:`consent_fn` accepts: ``screen`` (a full-screen frame —
    #: the standalone and the interactive-``floofy`` controls) or ``app`` (the manager App's modal, Requirement 16.3).
    consent_how: str = "screen"
    #: Art rows still to be printed beside :meth:`say` lines while a banner column is open (``None`` = closed).
    _banner_rows: list[str] | None = field(default=None, init=False, repr=False)
    #: Blank prefix used for lines that outlast the art while the column is open.
    _banner_pad: str = field(default="", init=False, repr=False)

    # -- the full-screen consent (Requirement 15.3) --------------------------------------------

    @property
    def has_controls(self) -> bool:
        """Something can answer questions here: a scripted ``input_fn`` or a host's controls (the interactive ``floofy``)."""
        return self.input_fn is not None or self.confirm_fn is not None or self.typed_fn is not None

    @property
    def screen_available(self) -> bool:
        """Both stdin and ``out`` are terminals and nothing asked for a plain run: a full-screen control may be drawn."""
        if self.non_interactive or self.quiet or self.input_fn is not None:
            return False
        try:
            stdin_tty = sys.stdin is not None and sys.stdin.isatty()
        except (AttributeError, ValueError):
            stdin_tty = False
        return stdin_tty and self.is_terminal

    def ask_consent_screen(self) -> bool | None:
        """Show the one-time warning as a full-screen ``[ I AGREE ]`` control.

        ``True``/``False`` is the user's answer; ``None`` means the screen cannot be
        shown here (no terminal, ``--json``, a scripted console) and the caller
        falls back to the typed ``I ACCEPT`` prompt, exactly as before.
        """
        if self.consent_fn is not None:
            return self.consent_fn()
        if not self.screen_available:
            return None
        from .consent_screen import ConsentScreen  # noqa: PLC0415

        return ConsentScreen(stdin=sys.stdin, stdout=self.out, style=self.styling).run()

    # -- styling -----------------------------------------------------------------------------

    @property
    def styling(self) -> Style:
        return self.style if self.style is not None else Style.off()

    @property
    def err_styling(self) -> Style:
        """``err`` is painted only when it is a terminal too (or styling was forced)."""
        style = self.styling
        if style.forced or _isatty(self.err):
            return style
        return Style.off()

    def paint(self, text: str, tone: str) -> str:
        """A fragment of ``out`` in the palette colour ``tone`` (plain when styling is off)."""
        return self.styling.paint(text, tone)

    @property
    def is_terminal(self) -> bool:
        """``out`` is a real terminal (styling may be on for a pipe under ``FORCE_COLOR``; this is stricter)."""
        return _isatty(self.out)

    def _banner_lines(self, width: int | None) -> list[str] | None:
        """The banner's rendered lines when it may be shown here (styling on, a real terminal, wide enough), else ``None``.

        Decoration only: it never enters the transcript, is omitted when colours
        are off or the terminal is narrower than the art plus margins, and never
        appears in ``--json`` output (styling is off there).
        """
        if self.quiet or not self.styling.on or self.styling.forced or not self.is_terminal:
            return None
        from .banner import render_lines  # noqa: PLC0415

        return render_lines(self.styling.depth, width)

    def show_banner(self, width: int | None = None) -> bool:
        """Print the FloofyCrew banner (Requirement 15.5) on its own, above whatever follows. Returns whether it printed."""
        lines = self._banner_lines(width)
        if lines is None:
            return False
        self.out.write("\n".join(lines) + "\n")
        self.out.flush()
        return True

    def open_banner_column(self, width: int | None = None, *, wordmark: bool = True) -> bool:
        """Print the banner *beside* the lines that follow (Requirement 15.5).

        When the terminal is wide enough the block wordmark ("FloofyCrew", from
        ``branding/wordmark-text.svg``) is printed first, to the right of the art's
        top rows. Then, until :meth:`close_banner_column`, every :meth:`say` line is
        prefixed with the next row of the art (blank padding once the art is used
        up), so the header — ``floofy doctor — FloofyCrew …`` — sits right under the
        wordmark, beside the fox, and the block reads like a card. Rows the text did
        not reach are flushed by ``close``, so the art is always whole. Same gates
        as :meth:`show_banner`; when it returns ``False`` nothing changes and the
        lines print flush-left as usual. The transcript never sees the art.
        """
        lines = self._banner_lines(width)
        if lines is None:
            return False
        from .banner import MARGIN, WORDMARK_GAP, load, load_wordmark, render_lines, wordmark_fits  # noqa: PLC0415

        rows = list(lines)
        if wordmark and wordmark_fits(width):
            mark = load_wordmark()
            mark_rows = render_lines(self.styling.depth, width, banner=mark, indent=0) or []
            for mark_row in mark_rows:
                art_row = rows.pop(0) if rows else " " * (MARGIN // 2 + load().width)
                print(art_row + " " * WORDMARK_GAP + mark_row, file=self.out)
        self._banner_rows = rows
        self._banner_pad = " " * (MARGIN // 2 + load().width) + BANNER_GAP
        return True

    def close_banner_column(self) -> None:
        """Stop prefixing lines; print any art rows the text did not reach."""
        rows, self._banner_rows = self._banner_rows, None
        for row in rows or ():
            print(row, file=self.out)

    # -- output ------------------------------------------------------------------------------

    def say(self, message: str = "", *, tone: str | None = None) -> None:
        """Print one line; ``tone`` paints the whole line. The transcript keeps the plain text."""
        self.transcript.append(strip_ansi(message))
        if not self.quiet:
            text = self.styling.paint(message, tone) if tone else message
            if self._banner_rows is not None:
                text = (self._banner_rows.pop(0) + BANNER_GAP if self._banner_rows else self._banner_pad) + text
            print(text, file=self.out)

    def heading(self, message: str) -> None:
        self.say(message, tone="heading")

    def lines(self, messages: Iterable[str]) -> None:
        for message in messages:
            self.say(message)

    def warn(self, message: str) -> None:
        self.transcript.append("WARNING " + strip_ansi(message))
        print(self.err_styling.warn("WARNING") + " " + message, file=self.err)

    def error(self, message: str) -> None:
        self.transcript.append("ERROR " + strip_ansi(message))
        print(self.err_styling.danger("ERROR") + " " + message, file=self.err)

    # -- questions ---------------------------------------------------------------------------

    def _read(self, prompt: str) -> str | None:
        reader = self.input_fn or input
        try:
            return reader(prompt)
        except (EOFError, KeyboardInterrupt):
            return None

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        """Yes/no; ``--yes`` says yes, a non-interactive run says ``default``."""
        if self.assume_yes:
            self.transcript.append(f"{prompt} -> yes (--yes)")
            return True
        if self.non_interactive:
            self.transcript.append(f"{prompt} -> {'yes' if default else 'no'} (non-interactive default)")
            return default
        if self.confirm_fn is not None:
            chosen = self.confirm_fn(prompt, default)
            answer_value = default if chosen is None else bool(chosen)
            self.transcript.append(f"{prompt} -> {'yes' if answer_value else 'no'}")
            return answer_value
        suffix = " [Y/n] " if default else " [y/N] "
        answer = self._read(prompt + suffix)
        if answer is None:
            return default
        answer = answer.strip().lower()
        if not answer:
            return default
        return answer in ("y", "yes")

    def typed(self, prompt: str, expected: str, *, what: str) -> bool:
        """The user must type ``expected`` exactly. ``--yes`` never satisfies this."""
        if expected in self.pre_typed:
            self.transcript.append(f"{what}: pre-confirmed by flag")
            return True
        if self.non_interactive:
            self.transcript.append(f"{what}: needs a typed confirmation; refused in non-interactive mode")
            return False
        answer = self.typed_fn(prompt, what) if self.typed_fn is not None else self._read(prompt)
        ok = answer is not None and answer.strip() == expected
        self.transcript.append(f"{what}: {'typed' if ok else 'not confirmed'}")
        return ok


def _isatty(stream: IO[str]) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False
