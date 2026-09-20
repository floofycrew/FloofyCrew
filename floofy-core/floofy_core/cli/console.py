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
* the one-time consent acknowledgement (``floofy init``): the user types
  :data:`floofy_core.consent.ACCEPT_PHRASE`, or automation passes
  ``--i-accept-the-risk`` — handled in the init command with :meth:`Console.typed`.

Text output goes to ``out`` (stdout by default); in ``--json`` mode the prose is
suppressed and the command's result document is printed once at the end by
:mod:`floofy_core.cli.main`. Warnings always go to ``err`` so a JSON consumer
still sees them.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import IO, Callable, Iterable

__all__ = ["Console"]


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
    #: Everything printed, for callers that capture (the UI's in-process run).
    transcript: list[str] = field(default_factory=list)

    def say(self, message: str = "") -> None:
        self.transcript.append(message)
        if not self.quiet:
            print(message, file=self.out)

    def lines(self, messages: Iterable[str]) -> None:
        for message in messages:
            self.say(message)

    def warn(self, message: str) -> None:
        self.transcript.append("WARNING " + message)
        print("WARNING " + message, file=self.err)

    def error(self, message: str) -> None:
        self.transcript.append("ERROR " + message)
        print("ERROR " + message, file=self.err)

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
        answer = self._read(prompt)
        ok = answer is not None and answer.strip() == expected
        self.transcript.append(f"{what}: {'typed' if ok else 'not confirmed'}")
        return ok
