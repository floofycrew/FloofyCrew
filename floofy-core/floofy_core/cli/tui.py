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
* the one-time consent → the same full-screen ``[ I AGREE ]`` frame as
  :mod:`floofy_core.cli.consent_screen`, drawn in curses (``Console.consent_fn``),
  recorded ``how="screen"`` by the same ``init`` step;
* a governance-altering target → a **text field** that must contain the exact
  path (``Console.typed_fn``) — never a button, never a menu;
* an unlisted source → the disclosure text and an explicit accept item that
  supplies the phrase the CLI would have you type;
* ``--allow-unsigned`` on a registry source → the same loosening warning and an
  explicit confirm item.

Standard library only (``curses``). When curses cannot start — no terminal,
``TERM`` unset or ``dumb``, no terminfo, ``initscr`` failing — :func:`launch`
returns ``None`` and ``floofy`` prints its usage plus a one-line hint, exactly
as the plain subcommands would (Requirement 15.6, 14.2).
"""
from __future__ import annotations

import argparse
import io
import os
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..consent import ACCEPT_PHRASE, read_consent
from ..loaderapp import installed_meta
from . import cmd_doctor, cmd_init, cmd_mods, cmd_profile, cmd_registry, cmd_yeet
from .cmd_doctor import loader_state
from .console import Console
from .context import CliContext, build_context
from .style import Depth, Style, cube_index, strip_ansi

__all__ = ["ACTIONS", "Action", "TuiApp", "action", "global_argv", "host_summary", "launch", "run_action"]

#: What the fallback prints under the usage when curses cannot start (Requirement 15.6).
FALLBACK_HINT = "floofy: the interactive mode needs a terminal curses can drive (TERM is unset or dumb, or curses could not start here); the subcommands above work everywhere."
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
    return rows


# --- the curses application ---------------------------------------------------------------------------------------


def launch(args: argparse.Namespace, console: Console) -> int | None:
    """Open the interface; ``None`` when curses cannot start here (the caller prints the usage)."""
    if not console.screen_available:
        return None
    term = (os.environ.get("TERM") or "").strip().lower()
    if not term or term == "dumb":
        return None
    try:
        import curses  # noqa: PLC0415
    except ImportError:
        return None
    try:
        screen = curses.initscr()
    except Exception:  # noqa: BLE001 - curses.error, or a terminfo problem: fall back to the usage
        return None
    try:
        curses.noecho()
        curses.cbreak()
        screen.keypad(True)
        try:
            curses.set_escdelay(25)
        except AttributeError:  # pragma: no cover - Python < 3.9
            pass
        app = TuiApp(screen, args, console)
        return app.run()
    except KeyboardInterrupt:
        return 130
    finally:
        try:
            screen.keypad(False)
            curses.echo()
            curses.nocbreak()
            curses.endwin()
        except Exception:  # noqa: BLE001
            pass


@dataclass
class Palette:
    """curses attributes for the semantic tones, and colour pairs for the banner."""

    tones: dict[str, int] = field(default_factory=dict)
    banner_pairs: dict[tuple[int, int, int], int] = field(default_factory=dict)
    banner_mode: str = "off"  # off | single | cube

    def attr(self, tone: str) -> int:
        return self.tones.get(tone, 0)


class TuiApp:
    """The screens; every mutation goes through :func:`run_action`."""

    def __init__(self, screen: Any, args: argparse.Namespace, console: Console):
        import curses  # noqa: PLC0415

        self.curses = curses
        self.screen = screen
        self.args = args
        self.prefix = global_argv(args)
        self.plain = console
        self.palette = self._colours()
        self.ctx = self._context()

    # -- infrastructure --------------------------------------------------------------------------

    def _context(self) -> CliContext:
        return build_context(self.args, console=Console(out=io.StringIO(), err=io.StringIO(), non_interactive=True, style=Style.off()), actor="tui")

    def _console(self) -> Console:
        """The console an action runs with: captured, plain, with the interface's controls plugged in."""
        return Console(out=io.StringIO(), err=io.StringIO(), non_interactive=False, style=Style.off(), consent_fn=self.consent_screen, confirm_fn=self.confirm, typed_fn=self.typed_field)

    def _colours(self) -> Palette:
        curses = self.curses
        palette = Palette()
        try:
            curses.start_color()
            curses.use_default_colors()
        except curses.error:
            return palette
        if not curses.has_colors():
            return palette
        pairs = {"heading": curses.COLOR_YELLOW, "ok": curses.COLOR_GREEN, "warn": curses.COLOR_YELLOW, "danger": curses.COLOR_RED, "accent": curses.COLOR_MAGENTA}
        number = 1
        for tone, colour in pairs.items():
            try:
                curses.init_pair(number, colour, -1)
                palette.tones[tone] = curses.color_pair(number) | (curses.A_BOLD if tone in ("heading", "danger") else 0)
            except curses.error:
                break
            number += 1
        palette.tones["muted"] = curses.A_DIM
        palette.tones["bold"] = curses.A_BOLD
        from .banner import load  # noqa: PLC0415

        art = load()
        if curses.COLORS >= 256 and curses.COLOR_PAIRS > number + len(art.colours):
            for rgb in sorted(art.colours):
                try:
                    curses.init_pair(number, cube_index(*rgb), -1)
                except curses.error:
                    palette.banner_pairs.clear()
                    break
                palette.banner_pairs[rgb] = curses.color_pair(number)
                number += 1
            palette.banner_mode = "cube" if palette.banner_pairs else "single"
        else:
            palette.banner_mode = "single"
        return palette

    def size(self) -> tuple[int, int]:
        rows, columns = self.screen.getmaxyx()
        return rows, columns

    def put(self, row: int, column: int, text: str, attr: int = 0) -> None:
        rows, columns = self.size()
        if row < 0 or row >= rows or column >= columns:
            return
        text = text[: max(0, columns - column - 1)]
        if not text:
            return
        try:
            self.screen.addstr(row, column, text, attr)
        except self.curses.error:
            pass

    def banner_rows(self, top: int) -> int:
        """Draw the banner at ``top`` when colours and the width allow; returns the rows used."""
        from .banner import MARGIN, load  # noqa: PLC0415

        rows, columns = self.size()
        art = load()
        if self.palette.banner_mode == "off" or columns < art.width + MARGIN or rows < art.height + 12:
            return 0
        for index, row in enumerate(art.rows):
            if self.palette.banner_mode == "cube":
                column = MARGIN // 2
                for ch, rgb in row:
                    self.put(top + index, column, ch, self.palette.banner_pairs.get(rgb, 0))
                    column += 1
            else:
                self.put(top + index, MARGIN // 2, "".join(ch for ch, _ in row), self.palette.attr("accent"))
        return art.height + 1

    def footer(self, text: str) -> None:
        rows, _ = self.size()
        self.put(rows - 1, 0, text, self.palette.attr("muted"))

    # -- widgets -------------------------------------------------------------------------------

    def menu(self, title: str, items: list[str], *, header: list[tuple[str, int]] | None = None, footer: str = "↑/↓ or j/k move · Enter select · / filter · Esc back · q quit", banner: bool = False, default: int = 0, allow_filter: bool = True) -> int | None:
        """A list; returns the chosen index (into ``items``) or ``None`` for Esc/``q``."""
        curses = self.curses
        selected = min(default, max(0, len(items) - 1))
        needle = ""
        while True:
            visible = [(i, label) for i, label in enumerate(items) if needle.lower() in label.lower()] if needle else list(enumerate(items))
            if visible and not any(i == selected for i, _ in visible):
                selected = visible[0][0]
            self.screen.erase()
            top = 0
            if banner:
                top += self.banner_rows(0)
            self.put(top, 2, title, self.palette.attr("heading"))
            top += 1
            self.put(top, 2, "FloofyCrew is unofficial and not affiliated with Kiro or KiroCrew.", self.palette.attr("muted"))
            top += 2
            for text, attr in header or []:
                self.put(top, 2, text, attr)
                top += 1
            if header:
                top += 1
            rows, _ = self.size()
            window = max(1, rows - top - 3)
            position = next((n for n, (i, _) in enumerate(visible) if i == selected), 0)
            start = max(0, min(position - window // 2, len(visible) - window))
            for n, (i, label) in enumerate(visible[start : start + window]):
                marker = "▸ " if i == selected else "  "
                self.put(top + n, 2, marker + label, curses.A_REVERSE if i == selected else 0)
            if not visible:
                self.put(top, 4, "(nothing matches)", self.palette.attr("muted"))
            if needle:
                self.put(rows - 2, 2, f"filter: {needle}", self.palette.attr("accent"))
            self.footer(footer)
            self.screen.refresh()
            key = self.screen.getch()
            if key in (curses.KEY_UP, ord("k")) and visible:
                position = max(0, position - 1)
                selected = visible[position][0]
            elif key in (curses.KEY_DOWN, ord("j")) and visible:
                position = min(len(visible) - 1, position + 1)
                selected = visible[position][0]
            elif key == curses.KEY_PPAGE and visible:
                selected = visible[max(0, position - window)][0]
            elif key == curses.KEY_NPAGE and visible:
                selected = visible[min(len(visible) - 1, position + window)][0]
            elif key in (curses.KEY_HOME,) and visible:
                selected = visible[0][0]
            elif key in (curses.KEY_END,) and visible:
                selected = visible[-1][0]
            elif key in (10, 13, curses.KEY_ENTER):
                if visible:
                    return selected
            elif key == 27:
                if needle:
                    needle = ""
                else:
                    return None
            elif key == ord("q") and not needle:
                return None
            elif key == ord("/") and allow_filter:
                needle = self.text_field(["Filter the list (Esc clears):"], initial=needle) or ""
            elif key in (curses.KEY_BACKSPACE, 127, 8) and needle:
                needle = needle[:-1]
            elif key == curses.KEY_RESIZE:
                continue

    def text_field(self, prompt: list[str], *, initial: str = "") -> str | None:
        """A single-line text field under the wrapped ``prompt``; Enter submits, Esc cancels (``None``)."""
        curses = self.curses
        value = initial
        while True:
            self.screen.erase()
            rows, columns = self.size()
            row = 1
            for paragraph in prompt:
                for line in textwrap.wrap(paragraph, width=max(20, columns - 4)) or [""]:
                    self.put(row, 2, line, self.palette.attr("bold") if row == 1 else 0)
                    row += 1
            row += 1
            self.put(row, 2, "> " + value, curses.A_UNDERLINE)
            self.footer("type, then Enter · Esc cancels")
            try:
                self.screen.move(row, min(columns - 2, 4 + len(value)))
            except curses.error:
                pass
            self.screen.refresh()
            try:
                key = self.screen.get_wch()
            except curses.error:
                continue
            if key in ("\n", "\r", curses.KEY_ENTER):
                return value
            if key == "\x1b":
                return None
            if key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                value = value[:-1]
            elif key == "\x15":  # Ctrl-U clears
                value = ""
            elif isinstance(key, str) and key.isprintable():
                value += key

    def pager(self, title: str, lines: list[str], *, tone: str = "heading") -> None:
        """A scrollable output pane; Esc, ``q`` or Enter close it."""
        curses = self.curses
        scroll = 0
        while True:
            self.screen.erase()
            rows, columns = self.size()
            self.put(0, 2, title, self.palette.attr(tone))
            window = max(1, rows - 3)
            wrapped: list[str] = []
            for line in lines:
                wrapped.extend(textwrap.wrap(line, width=max(20, columns - 4), replace_whitespace=False, drop_whitespace=False) or [""])
            max_scroll = max(0, len(wrapped) - window)
            scroll = max(0, min(scroll, max_scroll))
            for n, line in enumerate(wrapped[scroll : scroll + window]):
                attr = self.palette.attr("warn") if line.startswith("WARNING") else self.palette.attr("danger") if line.startswith("ERROR") else 0
                self.put(2 + n, 2, line, attr)
            more = (" · ↑ more above" if scroll > 0 else "") + (" · ↓ more below" if scroll < max_scroll else "")
            self.footer("↑/↓ PgUp/PgDn scroll · Esc, q or Enter back" + more)
            self.screen.refresh()
            key = self.screen.getch()
            if key in (27, ord("q"), 10, 13, curses.KEY_ENTER):
                return
            if key in (curses.KEY_UP, ord("k")):
                scroll -= 1
            elif key in (curses.KEY_DOWN, ord("j")):
                scroll += 1
            elif key == curses.KEY_PPAGE:
                scroll -= window
            elif key == curses.KEY_NPAGE:
                scroll += window
            elif key == curses.KEY_HOME:
                scroll = 0
            elif key == curses.KEY_END:
                scroll = max_scroll

    def confirm(self, prompt: str, default: bool) -> bool:
        """``Console.confirm_fn``: an ordinary yes/no as a two-item menu."""
        choice = self.menu("Confirm", ["yes", "no"], header=[(line, 0) for line in textwrap.wrap(prompt, 90)], default=0 if default else 1, allow_filter=False, footer="↑/↓ choose · Enter confirm · Esc = no")
        return choice == 0  # Esc is a no

    def typed_field(self, prompt: str, what: str) -> str | None:
        """``Console.typed_fn``: a text field that must contain the exact answer — a governance-altering target stays typed.

        The unlisted-source line (Requirement 8.8) is the one typed answer that is a
        *phrase*, not a path: it is shown with its disclosure and an explicit accept
        item that supplies the phrase; declining returns nothing.
        """
        if what == "unlisted source":
            choice = self.menu("Unlisted source", [UNLISTED_ACCEPT_ITEM, "Cancel"], header=[(line, self.palette.attr("warn")) for line in textwrap.wrap(prompt, 90)], default=1, allow_filter=False, footer="↑/↓ choose · Enter confirm · Esc cancels")
            return ACCEPT_PHRASE if choice == 0 else None
        return self.text_field([prompt, f"Type the exact answer for: {what}. Nothing else — no shortcut, no button — satisfies this confirmation."])

    def consent_screen(self) -> bool | None:
        """``Console.consent_fn``: the Requirement 15.3 frame, drawn in curses; Enter agrees, Esc/q declines."""
        from .banner import MARGIN, load  # noqa: PLC0415
        from .consent_screen import FOOTER_ROWS, body_lines, compose  # noqa: PLC0415

        curses = self.curses
        art = load()
        scroll = 0
        while True:
            self.screen.erase()
            rows, columns = self.size()
            with_banner = self.palette.banner_mode != "off" and columns >= art.width + MARGIN and rows >= art.height + len(body_lines(columns)) + FOOTER_ROWS
            frame = compose(columns, rows, scroll=scroll, banner_lines=list(art.text_rows) if with_banner else None, style=Style.off())
            for n, line in enumerate(frame.body):
                source_index = frame.scroll + n
                if with_banner and source_index < art.height:
                    if self.palette.banner_mode == "cube":
                        column = MARGIN // 2
                        for ch, rgb in art.rows[source_index]:
                            self.put(n, column, ch, self.palette.banner_pairs.get(rgb, 0))
                            column += 1
                    else:
                        self.put(n, MARGIN // 2, line, self.palette.attr("accent"))
                    continue
                attr = self.palette.attr("heading") if source_index == (art.height + 1 if with_banner else 0) else 0
                self.put(n, 0, line, attr)
            control_row = len(frame.body) + 1
            pad = max(0, (columns - len("[ I AGREE ]")) // 2)
            self.put(control_row, pad, "[ I AGREE ]", curses.A_REVERSE | self.palette.attr("accent"))
            self.put(control_row + 1, 0, strip_ansi(frame.hint), self.palette.attr("muted"))
            self.screen.refresh()
            key = self.screen.getch()
            if key in (10, 13, curses.KEY_ENTER):
                return True
            if key in (27, ord("q"), ord("Q")):
                return False
            if key in (curses.KEY_DOWN, ord("j")):
                scroll = min(frame.max_scroll, scroll + 1)
            elif key in (curses.KEY_UP, ord("k")):
                scroll = max(0, scroll - 1)
            elif key == curses.KEY_NPAGE:
                scroll = min(frame.max_scroll, scroll + max(1, rows - FOOTER_ROWS - 1))
            elif key == curses.KEY_PPAGE:
                scroll = max(0, scroll - max(1, rows - FOOTER_ROWS - 1))

    # -- running actions -------------------------------------------------------------------------

    def act(self, key: str, **values: str) -> Outcome | None:
        """Run one action through the shared handler and show its output; ``None`` when the user backed out."""
        action_ = action(key)
        if action_.needs_consent and not read_consent(self.ctx.home.consent).ok:
            choice = self.menu("Consent needed", ["Run floofy init now (the one-time warning, the Loader app, the trigger)", "Cancel"], header=[("FloofyCrew is not set up on this host: no consent record exists, so nothing can be applied (Requirement 11.1).", 0)], allow_filter=False)
            if choice != 0:
                return None
            init_outcome = self.act("setup.init")
            if init_outcome is None or init_outcome.exit != 0 or not read_consent(self.ctx.home.consent).ok:
                return init_outcome
        self.screen.erase()
        self.put(0, 2, "Running: floofy " + " ".join(action_.build(**values)), self.palette.attr("accent"))
        self.put(1, 2, "please wait…", self.palette.attr("muted"))
        self.screen.refresh()
        outcome = run_action(action_, self.prefix, console=self._console(), **values)
        self.ctx = self._context()  # facts may have changed
        verdict = "ok" if outcome.exit == 0 else f"exit {outcome.exit}"
        self.pager(f"floofy {' '.join(action_.build(**values))} — {verdict}", outcome.lines or ["(no output)"], tone="heading" if outcome.exit == 0 else "danger")
        return outcome

    # -- screens ---------------------------------------------------------------------------------

    def run(self) -> int:
        while True:
            consent = read_consent(self.ctx.home.consent)
            items = ["Mods", "Registries", "Profiles", "Doctor", "Check for updates", "Set up FloofyCrew (floofy init)" if not consent.ok else "Re-run floofy init (Loader app, trigger)", "Quit"]
            header = [(f"{label:>10}  {value}", 0) for label, value in host_summary(self.ctx)]
            choice = self.menu("floofy — interactive", items, header=header, banner=True, footer="↑/↓ or j/k move · Enter open · q quit", allow_filter=False)
            if choice is None or choice == 6:
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
            extras = ["+ Install from the registry (search)…", "+ Install from a git reference (ssh://… or https://…@tag)…", "↺ Restore a quarantined set (yeet --restore)…", "≡ Show the quarantine", "≡ Status of every mod"]
            choice = self.menu("Mods", labels + extras, header=[("installed mods: state, version and source tier (unlisted / listed / tested)", self.palette.attr("muted"))])
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
        choice = self.menu(f"Registry: {len(hits)} match(es) for {query!r}", labels, header=[("Enter installs the newest version known to work here; the disclosure and its confirmations follow.", self.palette.attr("muted"))])
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
            choice = self.menu("Registries", labels + extras, header=[("sources and their trust; the merged cache feeds search/install/update", self.palette.attr("muted"))])
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
        choice = self.menu("Trust loosening", ["Accept unsigned indexes from this source (recorded in the audit log)", "Keep the signature requirement"], header=[(line, self.palette.attr("danger")) for line in textwrap.wrap(warning, 90)], default=1, allow_filter=False)
        return choice == 0

    def source_actions(self, source: Any) -> None:
        keys = ["registries.refresh_one", "registries.trust", "registries.trust_unsigned", "registries.remove"]
        labels = [action("registries.refresh_one").label, "Change the trust level (index / owner)", "Accept unsigned indexes from this source" if not source.allow_unsigned else "Require a verified signature again", action("registries.remove").label]
        choice = self.menu(f"Source {source.label}", labels, header=[(source.url, 0), (f"trust={source.trust} allowUnsigned={source.allow_unsigned} keyId={source.key_id or '-'}", self.palette.attr("muted"))], allow_filter=False)
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
            choice = self.menu("Profiles", labels + extras, header=[("named mod sets with pinned versions (lockfiles)", self.palette.attr("muted"))])
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

    def updates_screen(self) -> None:
        outcome = self.act("updates.check")
        if outcome is None or outcome.exit != 0:
            return
        plan = outcome.result.get("plan") or []
        due = [row for row in plan if row.get("update")]
        if not due:
            return
        choice = self.menu("Updates available", [f"Update all {len(due)} now (floofy update --all)", "Not now"], header=[(f"{row['id']}: {row['installed']} -> {row['candidate']}", 0) for row in due], allow_filter=False)
        if choice == 0:
            self.act("updates.apply")
