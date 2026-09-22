"""Interactive ``floofy`` — the keyboard-driven terminal interface (Requirement 15.4, 15.6, 14.2).

Bare ``floofy`` on a terminal opens it: the banner, a host summary (edition,
version, channel, Loader state, consent) and menus for **Mods** (list with state
and source tier; enable, disable, update, uninstall, yeet/restore, install from
the registry or from a git reference), **Registries** (add, remove, refresh,
trust), **Profiles** (save, use/switch, export, import), **Doctor** and **Check
for updates**. Keyboard only — arrows or ``j``/``k``, Enter, Esc/``q`` back, ``/``
filter — so it works over ssh without a mouse.

One implementation of every action (the owner's rule for every surface): an
:class:`Action` names the subcommand ``argv`` it runs and the ``cmd_*`` handler
that subcommand dispatches to; :func:`run_action` calls
:func:`floofy_core.cli.main.execute` with that ``argv`` and ``actor="tui"``, so
the very same handler runs, writes the very same audit row (with ``actor: tui``)
and its output lands in a scrollable pane. The test suite checks, for every
action in :data:`ACTIONS`, that parsing its ``argv`` yields that handler.

Confirmations keep their grade (Requirement 11.1, 11.4, 15.3):

* ordinary yes/no → a two-item menu (``Console.confirm_fn``);
* the one-time consent → the very frame of :mod:`floofy_core.cli.consent_screen`
  (its :func:`~floofy_core.cli.consent_screen.present` loop on the interface's
  own terminal; ``Console.consent_fn``), recorded ``how="screen"`` by the same
  ``init`` step;
* a governance-altering target → a **text field** that must contain the exact
  path (``Console.typed_fn``) — never a button, never a menu;
* an unlisted source → the disclosure text and an explicit accept item that
  supplies the phrase the CLI would have you type;
* ``--allow-unsigned`` on a registry source → the same loosening warning and an
  explicit confirm item.

Standard library only, and **no curses** (Requirement 15.6): the screens are
drawn on :class:`floofy_core.cli.term.Terminal` — the ``termios``/ANSI/``select``
driver the consent screen uses — so the interface runs on an interpreter built
without ``_curses`` (the Python bundled with the host) and with ``TERM``
unset, whenever stdin and stdout are terminals. :func:`launch` returns ``None``
only when there is no such terminal or ``TERM`` is ``dumb``; ``floofy`` then
prints its usage plus a one-line hint, exactly as the plain subcommands would
(Requirement 15.6, 14.2). Colours come from the detected
:class:`~floofy_core.cli.style.Style`, so ``NO_COLOR`` and ``--no-color`` apply
here as everywhere.
"""
from __future__ import annotations

import argparse
import io
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..consent import ACCEPT_PHRASE, read_consent
from ..loaderapp import installed_meta
from . import cmd_doctor, cmd_init, cmd_mods, cmd_profile, cmd_registry, cmd_selfupdate, cmd_yeet
from .cmd_doctor import loader_state
from .console import Console
from .context import CliContext, build_context
from .style import Style
from .term import Canvas, Terminal, TerminalUnavailable, is_dumb

__all__ = ["ACTIONS", "Action", "FALLBACK_HINT", "TuiApp", "action", "global_argv", "host_summary", "launch", "run_action"]

#: What the fallback prints under the usage when the interface cannot open (Requirement 15.6).
FALLBACK_HINT = "floofy: the interactive mode needs a terminal (stdin and stdout TTYs; not TERM=dumb); the subcommands above work everywhere."
#: The explicit accept item of the unlisted-source disclosure: it supplies the phrase the CLI would have you type.
UNLISTED_ACCEPT_ITEM = f"{ACCEPT_PHRASE} — install from this unlisted source anyway"


# --- the action table -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Action:
    """One thing the interface can do: the subcommand it runs and the handler that subcommand dispatches to.

    ``argv`` is a template — ``{id}``, ``{ref}``, ``{query}``, ``{url}``, ``{trust}``,
    ``{name}``, ``{file}``, ``{hostver}`` are filled by :meth:`build`; every option
    is spelled out, so the table is static and a test can parse each entry.
    """

    key: str
    label: str
    handler: Callable[..., int]
    argv: tuple[str, ...]
    #: The action mutates on the user's consent: ``init`` runs first when no consent record exists.
    needs_consent: bool = False

    def build(self, **values: str) -> list[str]:
        return [part.format(**values) for part in self.argv]

    @property
    def placeholders(self) -> tuple[str, ...]:
        import string

        names: list[str] = []
        for part in self.argv:
            for _, name, _, _ in string.Formatter().parse(part):
                if name and name not in names:
                    names.append(name)
        return tuple(names)


ACTIONS: tuple[Action, ...] = (
    Action("setup.init", "Set up FloofyCrew (floofy init)", cmd_init.init, ("init",)),
    Action("doctor", "Doctor", cmd_doctor.doctor, ("doctor",)),
    Action("status", "Status", cmd_doctor.status, ("status",)),
    Action("mods.list", "List installed mods", cmd_mods.list_mods, ("list",)),
    Action("mods.info", "Info", cmd_mods.info, ("info", "{id}")),
    Action("mods.enable", "Enable", cmd_mods.enable, ("enable", "{id}"), needs_consent=True),
    Action("mods.disable", "Disable", cmd_mods.disable, ("disable", "{id}")),
    Action("mods.update", "Update this mod", cmd_mods.update, ("update", "{id}"), needs_consent=True),
    Action("mods.uninstall", "Uninstall", cmd_mods.uninstall, ("uninstall", "{id}")),
    Action("mods.yeet", "Yeet (park in the quarantine)", cmd_yeet.yeet_cmd, ("yeet", "{id}", "--reason", "manual yeet (interactive)")),
    Action("mods.yeet_restore", "Restore a quarantined set", cmd_yeet.yeet_cmd, ("yeet", "--restore", "{hostver}")),
    Action("mods.yeet_list", "Show the quarantine", cmd_yeet.yeet_cmd, ("yeet", "--list")),
    Action("mods.search", "Search the registry", cmd_mods.search, ("search", "{query}")),
    Action("mods.install", "Install", cmd_mods.install, ("install", "{ref}"), needs_consent=True),
    Action("registries.list", "List sources", cmd_registry.registry, ("registry", "list")),
    Action("registries.add", "Add a source", cmd_registry.registry, ("registry", "add", "{url}", "--trust", "{trust}")),
    Action("registries.add_unsigned", "Add a source, accepting unsigned indexes", cmd_registry.registry, ("registry", "add", "{url}", "--trust", "{trust}", "--allow-unsigned")),
    Action("registries.trust", "Change the trust level", cmd_registry.registry, ("registry", "add", "{url}", "--trust", "{trust}", "--no-refresh")),
    Action("registries.trust_unsigned", "Accept unsigned indexes from this source", cmd_registry.registry, ("registry", "add", "{url}", "--trust", "{trust}", "--allow-unsigned", "--no-refresh")),
    Action("registries.remove", "Remove", cmd_registry.registry, ("registry", "remove", "{url}")),
    Action("registries.refresh", "Refresh every source", cmd_registry.registry, ("registry", "refresh")),
    Action("registries.refresh_one", "Refresh this source", cmd_registry.registry, ("registry", "refresh", "{url}")),
    Action("registries.defaults", "Record the edition's default source", cmd_registry.registry, ("registry", "defaults")),
    Action("profiles.list", "List profiles", cmd_profile.profile, ("profile", "list")),
    Action("profiles.save", "Save the installed set as a profile", cmd_profile.profile, ("profile", "save", "{name}")),
    Action("profiles.use", "Switch to this profile (staged)", cmd_profile.profile, ("profile", "use", "{name}"), needs_consent=True),
    Action("profiles.use_now", "Switch to this profile now", cmd_profile.profile, ("profile", "use", "{name}", "--now"), needs_consent=True),
    Action("profiles.plan", "Show what switching would do", cmd_profile.profile, ("profile", "use", "{name}", "--check")),
    Action("profiles.export", "Export to a file", cmd_profile.profile, ("profile", "export", "{name}", "{file}")),
    Action("profiles.import", "Import from a file", cmd_profile.profile, ("profile", "import", "{file}")),
    Action("updates.check", "Check for updates", cmd_mods.update, ("update", "--all", "--check")),
    Action("updates.apply", "Update every registry mod", cmd_mods.update, ("update", "--all"), needs_consent=True),
    Action("selfupdate.check", "Check for a FloofyCrew release", cmd_selfupdate.self_update, ("self-update", "--check")),
    Action("selfupdate.install", "Update FloofyCrew itself (floofy self-update)", cmd_selfupdate.self_update, ("self-update",)),
)

_BY_KEY = {a.key: a for a in ACTIONS}


def action(key: str) -> Action:
    return _BY_KEY[key]


def global_argv(args: argparse.Namespace) -> list[str]:
    """The global options of the bare ``floofy`` invocation, re-spelled for every action it runs."""
    out: list[str] = []
    if getattr(args, "home", None):
        out += ["--home", str(args.home)]
    for root in getattr(args, "root", None) or []:
        out += ["--root", str(root)]
    if getattr(args, "no_adapters", False):
        out.append("--no-adapters")
    if getattr(args, "kirocrew", None):
        out += ["--kirocrew", str(args.kirocrew)]
    if getattr(args, "port", None) is not None:
        out += ["--port", str(args.port)]
    if getattr(args, "token", None):
        out += ["--token", str(args.token)]
    if getattr(args, "yes", False):
        out.append("--yes")
    if getattr(args, "offline", False):
        out.append("--offline")
    return out


@dataclass
class Outcome:
    """What one action produced: the exit code, the result document and the plain transcript."""

    argv: list[str]
    exit: int
    result: dict[str, Any]
    lines: list[str]


def run_action(action_: Action, prefix: list[str], *, console: Console, **values: str) -> Outcome:
    """Run ``action_`` exactly as the subcommand would: same parser, same handler, ``actor="tui"``."""
    from .main import execute  # noqa: PLC0415

    argv = [*prefix, *action_.build(**values)]
    console.transcript.clear()
    code, result = execute(argv, console, actor="tui")
    return Outcome(argv, code, result, list(console.transcript))


def host_summary(ctx: CliContext) -> list[tuple[str, str]]:
    """The home screen's facts, from the same code ``doctor``/``status`` use."""
    rows: list[tuple[str, str]] = []
    try:
        current = ctx.current_payload()
        rows.append(("edition", ctx.edition()))
        rows.append(("version", current.host_version.text if current else "no payload found"))
        rows.append(("channel", (current.channel if current else None) or "?"))
    except Exception as exc:  # noqa: BLE001 - the home screen must always draw
        rows.append(("host", f"unavailable ({type(exc).__name__}: {exc})"))
    try:
        meta = installed_meta(ctx.host_home)
        state, source = loader_state(ctx, live=True)
        loader = "not installed" if not meta["installed"] else ("installed, disabled" if meta["enabled"] is False else f"installed v{meta['version'] or '?'}")
        if state:
            loader += f"; last boot {state.get('loader')} ({source})"
        rows.append(("loader", loader))
    except Exception as exc:  # noqa: BLE001
        rows.append(("loader", f"unavailable ({type(exc).__name__}: {exc})"))
    consent = read_consent(ctx.home.consent)
    rows.append(("consent", f"recorded ({consent.how or 'unknown'}, v{consent.warning_version})" if consent.ok else consent.status + " — " + consent.detail))
    rows.append(("data home", str(ctx.data_home)))
    try:
        # Requirement 7.7: the one-line notice, from the same cached daily check doctor/status use
        summary = cmd_selfupdate.summary_for(ctx)
        if summary.get("available") and summary.get("notice"):
            rows.append(("update", str(summary["notice"])))
        stage = summary.get("staged") or {}
        if stage and not stage.get("applied") and stage.get("present"):
            rows.append(("update", f"Loader app update to FloofyCrew {stage.get('version')} staged (floofy apply installs it when no gateway runs)"))
    except Exception as exc:  # noqa: BLE001 - the home screen must always draw
        rows.append(("update", f"check unavailable ({type(exc).__name__}: {exc})"))
    return rows


# --- the terminal application --------------------------------------------------------------------------------------


def launch(args: argparse.Namespace, console: Console) -> int | None:
    """Open the interface; ``None`` only without a terminal on stdin and stdout, or under ``TERM=dumb`` (the caller prints the usage)."""
    if not console.screen_available or is_dumb():
        return None
    terminal = Terminal(sys.stdin, console.out)
    try:
        terminal.enter()
    except TerminalUnavailable:
        return None
    try:
        return TuiApp(terminal, args, console).run()
    except (KeyboardInterrupt, EOFError):
        return 130
    finally:
        terminal.leave()


class TuiApp:
    """The screens; every mutation goes through :func:`run_action`.

    Drawing: each widget composes a fresh :class:`Canvas` per frame with the
    palette tones of the console's :class:`Style` and hands it to
    :meth:`Terminal.paint`, which rewrites only the rows that changed. Keys come
    from :meth:`Terminal.read_key` (names for the special keys, characters as
    themselves); ``None``/``"resize"`` redraws, Ctrl-C leaves the interface with
    exit 130 and EOF on stdin does the same.
    """

    def __init__(self, terminal: Terminal, args: argparse.Namespace, console: Console):
        self.term = terminal
        self.args = args
        self.prefix = global_argv(args)
        self.style: Style = console.styling
        self.canvas = Canvas(*terminal.size())
        self.ctx = self._context()

    # -- infrastructure --------------------------------------------------------------------------

    def _context(self) -> CliContext:
        return build_context(self.args, console=Console(out=io.StringIO(), err=io.StringIO(), non_interactive=True, style=Style.off()), actor="tui")

    def _console(self) -> Console:
        """The console an action runs with: captured, plain, with the interface's controls plugged in."""
        return Console(out=io.StringIO(), err=io.StringIO(), non_interactive=False, style=Style.off(), consent_fn=self.consent_screen, confirm_fn=self.confirm, typed_fn=self.typed_field)

    def size(self) -> tuple[int, int]:
        """``(rows, columns)`` — the order the screens were written in (the driver says ``(columns, rows)``)."""
        columns, rows = self.term.size()
        return rows, columns

    def begin(self) -> None:
        """Start a frame: a blank canvas at the terminal's current size."""
        self.canvas = Canvas(*self.term.size())

    def flush(self, *, cursor: tuple[int, int] | None = None) -> None:
        """Paint the frame (only the rows that changed reach the terminal)."""
        self.term.paint(self.canvas.render(self.style), cursor=cursor)

    def key(self) -> str | None:
        """The next key; ``None`` or ``"resize"`` means redraw. Ctrl-C raises ``KeyboardInterrupt`` (exit 130), EOF ``EOFError``."""
        key = self.term.read_key()
        if key == "eof":
            raise EOFError("stdin closed")
        if key == "ctrl-c":
            raise KeyboardInterrupt
        return key

    def put(self, row: int, column: int, text: str, tone: str | None = None, *, reverse: bool = False, underline: bool = False) -> None:
        self.canvas.put(row, column, text, tone, reverse=reverse, underline=underline)

    def banner_rows(self, top: int) -> int:
        """Draw the banner at ``top`` when colours and the size allow; returns the rows used.

        Cell by cell in the art's own colours through :meth:`Style.rgb`, so the depth
        rule is the plain CLI's: exact on a truecolor terminal, the 256-colour cube on a
        256-colour one, the single bright magenta on 16 colours, nothing when off.
        """
        from .banner import MARGIN, WORDMARK_CREW_RGB, WORDMARK_GAP, WORDMARK_NAME_RGB, load, load_wordmark, wordmark_fits, wordmark_top  # noqa: PLC0415

        rows, columns = self.size()
        art = load()
        if not self.style.on or columns < art.width + MARGIN or rows < art.height + 12:
            return 0
        for index, row in enumerate(art.rows):
            column = MARGIN // 2
            for ch, rgb in row:
                if ch != " ":  # transparent cells carry no colour of their own (as in banner.render_lines)
                    self.canvas.put(top + index, column, ch, rgb=rgb)
                column += 1
        # The wordmark sits to the RIGHT of the fox, centred on its height: the block letters
        # (branding/ascii/wordmark.json) when the terminal is wide enough, else the plain word in
        # the same two colours, so the name is always beside the art.
        left = MARGIN // 2 + art.width + WORDMARK_GAP
        mark = load_wordmark()
        if wordmark_fits(columns, banner=art, wordmark=mark):
            for index, row in enumerate(mark.rows):
                column = left
                for ch, rgb in row:
                    if ch != " ":
                        self.canvas.put(top + wordmark_top(art, mark) + index, column, ch, rgb=rgb)
                    column += 1
        elif columns >= left + len("FloofyCrew") + MARGIN // 2:
            middle = top + art.height // 2
            self.canvas.put(middle, left, "Floofy", rgb=WORDMARK_NAME_RGB)
            self.canvas.put(middle, left + len("Floofy"), "Crew", rgb=WORDMARK_CREW_RGB)
        return art.height + 1

    def footer(self, text: str) -> None:
        rows, _ = self.size()
        self.put(rows - 1, 0, text, "muted")

    # -- widgets -------------------------------------------------------------------------------

    def menu(self, title: str, items: list[str], *, header: list[tuple[str, str | None]] | None = None, footer: str = "↑/↓ or j/k move · Enter select · / filter · Esc back · q quit", banner: bool = False, default: int = 0, allow_filter: bool = True) -> int | None:
        """A list; returns the chosen index (into ``items``) or ``None`` for Esc/``q``."""
        selected = min(default, max(0, len(items) - 1))
        needle = ""
        while True:
            visible = [(i, label) for i, label in enumerate(items) if needle.lower() in label.lower()] if needle else list(enumerate(items))
            if visible and not any(i == selected for i, _ in visible):
                selected = visible[0][0]
            self.begin()
            top = 0
            if banner:
                top += self.banner_rows(0)
            self.put(top, 2, title, "heading")
            top += 1
            self.put(top, 2, "FloofyCrew is unofficial and not affiliated with Kiro or KiroCrew.", "muted")
            top += 2
            for text, tone in header or []:
                self.put(top, 2, text, tone)
                top += 1
            if header:
                top += 1
            rows, _ = self.size()
            window = max(1, rows - top - 3)
            position = next((n for n, (i, _) in enumerate(visible) if i == selected), 0)
            start = max(0, min(position - window // 2, len(visible) - window))
            for n, (i, label) in enumerate(visible[start : start + window]):
                marker = "▸ " if i == selected else "  "
                self.put(top + n, 2, marker + label, reverse=i == selected)
            if not visible:
                self.put(top, 4, "(nothing matches)", "muted")
            if needle:
                self.put(rows - 2, 2, f"filter: {needle}", "accent")
            self.footer(footer)
            self.flush()
            key = self.key()
            if key is None or key == "resize":
                continue
            if key in ("up", "k") and visible:
                position = max(0, position - 1)
                selected = visible[position][0]
            elif key in ("down", "j", "tab") and visible:
                position = min(len(visible) - 1, position + 1)
                selected = visible[position][0]
            elif key == "pageup" and visible:
                selected = visible[max(0, position - window)][0]
            elif key == "pagedown" and visible:
                selected = visible[min(len(visible) - 1, position + window)][0]
            elif key == "home" and visible:
                selected = visible[0][0]
            elif key == "end" and visible:
                selected = visible[-1][0]
            elif key == "enter":
                if visible:
                    return selected
            elif key == "esc":
                if needle:
                    needle = ""
                else:
                    return None
            elif key == "q" and not needle:
                return None
            elif key == "/" and allow_filter:
                needle = self.text_field(["Filter the list (Esc clears):"], initial=needle) or ""
            elif key == "backspace" and needle:
                needle = needle[:-1]

    def text_field(self, prompt: list[str], *, initial: str = "") -> str | None:
        """A single-line text field under the wrapped ``prompt``; Enter submits, Esc cancels (``None``)."""
        value = initial
        while True:
            self.begin()
            rows, columns = self.size()
            row = 1
            for paragraph in prompt:
                for line in textwrap.wrap(paragraph, width=max(20, columns - 4)) or [""]:
                    self.put(row, 2, line, "bold" if row == 1 else None)
                    row += 1
            row += 1
            self.put(row, 2, "> " + value, underline=True)
            self.footer("type, then Enter · Esc cancels")
            self.flush(cursor=(row, min(columns - 1, 4 + len(value))))
            key = self.key()
            if key is None or key == "resize":
                continue
            if key == "enter":
                return value
            if key == "esc":
                return None
            if key == "backspace":
                value = value[:-1]
            elif key == "ctrl-u":  # Ctrl-U clears
                value = ""
            elif len(key) == 1 and key.isprintable():
                value += key

    def pager(self, title: str, lines: list[str], *, tone: str = "heading") -> None:
        """A scrollable output pane; Esc, ``q`` or Enter close it."""
        scroll = 0
        while True:
            self.begin()
            rows, columns = self.size()
            self.put(0, 2, title, tone)
            window = max(1, rows - 3)
            wrapped: list[str] = []
            for line in lines:
                wrapped.extend(textwrap.wrap(line, width=max(20, columns - 4), replace_whitespace=False, drop_whitespace=False) or [""])
            max_scroll = max(0, len(wrapped) - window)
            scroll = max(0, min(scroll, max_scroll))
            for n, line in enumerate(wrapped[scroll : scroll + window]):
                line_tone = "warn" if line.startswith("WARNING") else "danger" if line.startswith("ERROR") else None
                self.put(2 + n, 2, line, line_tone)
            more = (" · ↑ more above" if scroll > 0 else "") + (" · ↓ more below" if scroll < max_scroll else "")
            self.footer("↑/↓ PgUp/PgDn scroll · Esc, q or Enter back" + more)
            self.flush()
            key = self.key()
            if key is None or key == "resize":
                continue
            if key in ("esc", "q", "enter"):
                return
            if key in ("up", "k"):
                scroll -= 1
            elif key in ("down", "j", "tab"):
                scroll += 1
            elif key == "pageup":
                scroll -= window
            elif key == "pagedown":
                scroll += window
            elif key == "home":
                scroll = 0
            elif key == "end":
                scroll = max_scroll

    def confirm(self, prompt: str, default: bool) -> bool:
        """``Console.confirm_fn``: an ordinary yes/no as a two-item menu."""
        choice = self.menu("Confirm", ["yes", "no"], header=[(line, None) for line in textwrap.wrap(prompt, 90)], default=0 if default else 1, allow_filter=False, footer="↑/↓ choose · Enter confirm · Esc = no")
        return choice == 0  # Esc is a no

    def typed_field(self, prompt: str, what: str) -> str | None:
        """``Console.typed_fn``: a text field that must contain the exact answer — a governance-altering target stays typed.

        The unlisted-source line (Requirement 8.8) is the one typed answer that is a
        *phrase*, not a path: it is shown with its disclosure and an explicit accept
        item that supplies the phrase; declining returns nothing.
        """
        if what == "unlisted source":
            choice = self.menu("Unlisted source", [UNLISTED_ACCEPT_ITEM, "Cancel"], header=[(line, "warn") for line in textwrap.wrap(prompt, 90)], default=1, allow_filter=False, footer="↑/↓ choose · Enter confirm · Esc cancels")
            return ACCEPT_PHRASE if choice == 0 else None
        return self.text_field([prompt, f"Type the exact answer for: {what}. Nothing else — no shortcut, no button — satisfies this confirmation."])

    def consent_screen(self) -> bool | None:
        """``Console.consent_fn``: the Requirement 15.3 frame — :func:`consent_screen.present` on the interface's own terminal.

        The same ``compose()`` at the same size, painted by the same driver as the
        standalone ``floofy init`` screen; Enter agrees, Esc/``q``/Ctrl-C decline.
        """
        from .consent_screen import present  # noqa: PLC0415

        return present(self.term, style=self.style)

    # -- running actions -------------------------------------------------------------------------

    def act(self, key: str, **values: str) -> Outcome | None:
        """Run one action through the shared handler and show its output; ``None`` when the user backed out."""
        action_ = action(key)
        if action_.needs_consent and not read_consent(self.ctx.home.consent).ok:
            choice = self.menu("Consent needed", ["Run floofy init now (the one-time warning, the Loader app, the trigger)", "Cancel"], header=[("FloofyCrew is not set up on this host: no consent record exists, so nothing can be applied (Requirement 11.1).", None)], allow_filter=False)
            if choice != 0:
                return None
            init_outcome = self.act("setup.init")
            if init_outcome is None or init_outcome.exit != 0 or not read_consent(self.ctx.home.consent).ok:
                return init_outcome
        self.begin()
        self.put(0, 2, "Running: floofy " + " ".join(action_.build(**values)), "accent")
        self.put(1, 2, "please wait…", "muted")
        self.flush()
        with self.term.interruptible():  # Ctrl-C interrupts a running action the way it does at the plain CLI
            outcome = run_action(action_, self.prefix, console=self._console(), **values)
        self.ctx = self._context()  # facts may have changed
        verdict = "ok" if outcome.exit == 0 else f"exit {outcome.exit}"
        self.pager(f"floofy {' '.join(action_.build(**values))} — {verdict}", outcome.lines or ["(no output)"], tone="heading" if outcome.exit == 0 else "danger")
        return outcome

    # -- screens ---------------------------------------------------------------------------------

    def run(self) -> int:
        while True:
            consent = read_consent(self.ctx.home.consent)
            items = ["Mods", "Registries", "Profiles", "Doctor", "Check for updates", "Update FloofyCrew itself", "Set up FloofyCrew (floofy init)" if not consent.ok else "Re-run floofy init (Loader app, trigger)", "Quit"]
            header = [(f"{label:>10}  {value}", None) for label, value in host_summary(self.ctx)]
            choice = self.menu("floofy — interactive", items, header=header, banner=True, footer="↑/↓ or j/k move · Enter open · q quit", allow_filter=False)
            if choice is None or choice == 7:
                return 0
            if choice == 0:
                self.mods_screen()
            elif choice == 1:
                self.registries_screen()
            elif choice == 2:
                self.profiles_screen()
            elif choice == 3:
                self.act("doctor")
            elif choice == 4:
                self.updates_screen()
            elif choice == 5:
                self.self_update_screen()
            elif choice == 6:
                self.act("setup.init")

    def mods_screen(self) -> None:
        while True:
            row = None
            try:
                row = self.ctx.current_compat_row()
            except Exception:  # noqa: BLE001
                row = None
            mods = self.ctx.mods()
            enabled = self.ctx.enabled()
            labels: list[str] = []
            for mod in mods:
                flag = enabled.get(mod.id, not mod.has_code)
                try:
                    tier = self.ctx.tier_of(mod, row)
                except Exception:  # noqa: BLE001
                    tier = "?"
                labels.append(f"[{'on ' if flag else 'off'}] {mod.id} {mod.version}  tier={tier}" + (f"  PROBLEM: {mod.problem}" if mod.problem else "") + ("  (dev link)" if mod.is_link else ""))
            extras = ["+ Install from the registry (search)…", "+ Install from a git reference (ssh://… or https://…, @tag optional)…", "↺ Restore a quarantined set (yeet --restore)…", "≡ Show the quarantine", "≡ Status of every mod"]
            choice = self.menu("Mods", labels + extras, header=[("installed mods: state, version and source tier (unlisted / listed / tested)", "muted")])
            if choice is None:
                return
            if choice < len(mods):
                self.mod_actions(mods[choice].id)
            elif choice == len(mods):
                self.install_from_registry()
            elif choice == len(mods) + 1:
                ref = self.text_field(["Git reference to install:", "ssh://<host>/<path>@<tag>  or  https://<host>/<owner>/<repo>[.git]@<tag>  (optional #<subdir>). The clone uses your own git credentials; the unlisted-source line is confirmed on the next screen."])
                if ref:
                    self.act("mods.install", ref=ref.strip())
            elif choice == len(mods) + 2:
                self.restore_quarantine()
            elif choice == len(mods) + 3:
                self.act("mods.yeet_list")
            else:
                self.act("status")

    def mod_actions(self, mod_id: str) -> None:
        enabled = self.ctx.enabled()
        mod = next((m for m in self.ctx.mods() if m.id == mod_id), None)
        if mod is None:
            return
        keys = ["mods.info", "mods.disable" if enabled.get(mod.id, not mod.has_code) else "mods.enable", "mods.update", "mods.uninstall", "mods.yeet"]
        choice = self.menu(f"Mod {mod_id} {mod.version}", [action(k).label for k in keys], allow_filter=False)
        if choice is None:
            return
        self.act(keys[choice], id=mod_id)

    def install_from_registry(self) -> None:
        query = self.text_field(["Search the registry cache (floofy registry refresh fills it). Words to match in id, name, description and tags; empty lists everything:"])
        if query is None:
            return
        outcome = run_action(action("mods.search"), self.prefix, console=self._console(), query=query.strip())
        hits = outcome.result.get("mods") or []
        if not hits:
            self.pager("floofy search — nothing found", outcome.lines or ["(no output)"], tone="warn")
            return
        labels = [f"{h.get('key')}  newest {h.get('newest')}  works here: {h.get('worksHere') or 'nothing'}  tier {h.get('tier')}" + (f"  (installed {h['installed']})" if h.get("installed") else "") for h in hits]
        choice = self.menu(f"Registry: {len(hits)} match(es) for {query!r}", labels, header=[("Enter installs the newest version known to work here; the disclosure and its confirmations follow.", "muted")])
        if choice is None:
            return
        self.act("mods.install", ref=str(hits[choice].get("key")))

    def restore_quarantine(self) -> None:
        summary = cmd_doctor._quarantine_summary(self.ctx)
        versions = [v for v in summary if v != "requests"]
        if not versions:
            self.pager("Quarantine", ["nothing is quarantined"], tone="warn")
            return
        choice = self.menu("Restore a quarantined set", [f"{v}: {', '.join(summary[v]) or '-'}" for v in versions], allow_filter=False)
        if choice is None:
            return
        self.act("mods.yeet_restore", hostver=versions[choice])

    def registries_screen(self) -> None:
        from ..registry_sources import SourceStore  # noqa: PLC0415

        while True:
            try:
                sources = SourceStore.load(self.ctx.home).sources
            except Exception as exc:  # noqa: BLE001
                self.pager("Registries", [f"registries.json unreadable: {exc}"], tone="danger")
                return
            labels = [f"{s.label}  trust={s.trust}" + ("  UNSIGNED ACCEPTED" if s.allow_unsigned else "") + f"  {s.url}" for s in sources]
            extras = ["+ Add a source…", "↻ Refresh every source", "≡ Record the edition's default source", "≡ List sources (cache state)"]
            choice = self.menu("Registries", labels + extras, header=[("sources and their trust; the merged cache feeds search/install/update", "muted")])
            if choice is None:
                return
            if choice < len(sources):
                self.source_actions(sources[choice])
            elif choice == len(sources):
                self.add_source()
            elif choice == len(sources) + 1:
                self.act("registries.refresh")
            elif choice == len(sources) + 2:
                self.act("registries.defaults")
            else:
                self.act("registries.list")

    def add_source(self) -> None:
        url = self.text_field(["https URL of the registry directory (index.json, compat.json and .sig files live there):"])
        if not url:
            return
        trust_choice = self.menu("Trust level", ["index — trust the signed index (default)", "owner — also trust the source for what it says about ownership"], allow_filter=False)
        if trust_choice is None:
            return
        trust = ("index", "owner")[trust_choice]
        unsigned = self.menu("Signature requirement", ["Require a verified index signature (default)", "Accept this source's index WITHOUT a verified signature (warned, audited)"], allow_filter=False)
        if unsigned is None:
            return
        if unsigned == 1 and not self.confirm_loosening(url):
            return
        self.act("registries.add_unsigned" if unsigned == 1 else "registries.add", url=url.strip(), trust=trust)

    def confirm_loosening(self, url: str) -> bool:
        warning = cmd_registry.LOOSENING_WARNING.format(url=url)
        choice = self.menu("Trust loosening", ["Accept unsigned indexes from this source (recorded in the audit log)", "Keep the signature requirement"], header=[(line, "danger") for line in textwrap.wrap(warning, 90)], default=1, allow_filter=False)
        return choice == 0

    def source_actions(self, source: Any) -> None:
        keys = ["registries.refresh_one", "registries.trust", "registries.trust_unsigned", "registries.remove"]
        labels = [action("registries.refresh_one").label, "Change the trust level (index / owner)", "Accept unsigned indexes from this source" if not source.allow_unsigned else "Require a verified signature again", action("registries.remove").label]
        choice = self.menu(f"Source {source.label}", labels, header=[(source.url, None), (f"trust={source.trust} allowUnsigned={source.allow_unsigned} keyId={source.key_id or '-'}", "muted")], allow_filter=False)
        if choice is None:
            return
        key = keys[choice]
        if key == "registries.trust":
            level = self.menu("Trust level", ["index", "owner"], allow_filter=False, default=0 if source.trust == "index" else 1)
            if level is None:
                return
            self.act("registries.trust", url=source.url, trust=("index", "owner")[level])
        elif key == "registries.trust_unsigned":
            if source.allow_unsigned:
                self.act("registries.trust", url=source.url, trust=source.trust)  # re-adding without --allow-unsigned restores the default
            elif self.confirm_loosening(source.url):
                self.act("registries.trust_unsigned", url=source.url, trust=source.trust)
        else:
            self.act(key, url=source.url)

    def profiles_screen(self) -> None:
        from ..profiles import list_profiles  # noqa: PLC0415

        while True:
            profiles = list_profiles(self.ctx.home)
            labels = [f"{p['name']}  {p.get('mods', '?')} mod(s)  saved {p.get('savedAt') or '?'}" + (f"  ERROR {p['error']}" if p.get("error") else "") for p in profiles]
            extras = ["+ Save the installed set as a profile…", "+ Import a profile from a file…"]
            choice = self.menu("Profiles", labels + extras, header=[("named mod sets with pinned versions (lockfiles)", "muted")])
            if choice is None:
                return
            if choice < len(profiles):
                self.profile_actions(profiles[choice]["name"])
            elif choice == len(profiles):
                name = self.text_field(["Profile name (letters, digits, - and _):"])
                if name:
                    self.act("profiles.save", name=name.strip())
            else:
                file = self.text_field(["Path of the profile file to import:"])
                if file:
                    self.act("profiles.import", file=file.strip())

    def profile_actions(self, name: str) -> None:
        keys = ["profiles.plan", "profiles.use", "profiles.use_now", "profiles.export"]
        choice = self.menu(f"Profile {name}", [action(k).label for k in keys], allow_filter=False)
        if choice is None:
            return
        if keys[choice] == "profiles.export":
            file = self.text_field([f"Write profile {name} to this file:"], initial=str(Path.home() / f"{name}.floofy-profile.json"))
            if file:
                self.act("profiles.export", name=name, file=file.strip())
            return
        self.act(keys[choice], name=name)

    def self_update_screen(self) -> None:
        """Requirement 7.7: a fresh check, then the explicit `floofy self-update` behind its own yes/no."""
        outcome = self.act("selfupdate.check")
        if outcome is None or outcome.exit != 0:
            return
        summary = outcome.result.get("selfUpdate") or {}
        if not summary.get("available"):
            return
        choice = self.menu("A FloofyCrew release for this host", [f"Update FloofyCrew to {summary.get('version')} now (floofy self-update)", "Not now"], header=[(str(summary.get("notice") or ""), None)], allow_filter=False)
        if choice == 0:
            self.act("selfupdate.install")

    def updates_screen(self) -> None:
        outcome = self.act("updates.check")
        if outcome is None or outcome.exit != 0:
            return
        plan = outcome.result.get("plan") or []
        due = [row for row in plan if row.get("update")]
        if not due:
            return
        choice = self.menu("Updates available", [f"Update all {len(due)} now (floofy update --all)", "Not now"], header=[(f"{row['id']}: {row['installed']} -> {row['candidate']}", None) for row in due], allow_filter=False)
        if choice == 0:
            self.act("updates.apply")
