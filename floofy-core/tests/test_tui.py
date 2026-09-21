"""The interactive ``floofy`` (tasks 10.5/10.6/10.10; Requirement 15.4, 15.6, 14.2, 13.3).

* handler mapping — for every action of the interface, parsing the subcommand argv
  it runs yields the very ``cmd_*`` handler that action names, so the interface
  cannot drift from the CLI; a run through ``run_action`` writes the same audit row
  with ``actor: tui``;
* non-terminal behaviour — bare ``floofy`` prints the usage and exits 2 as before;
* the fallback — only ``TERM=dumb`` or no terminal on stdin/stdout → usage + one
  hint; an unset ``TERM`` opens the interface;
* the terminal driver (``floofy_core.cli.term``) — key decoding (Enter, Esc told
  from an arrow sequence, arrows, Home/End, PgUp/PgDn, Tab, Backspace, Ctrl-C,
  digits, UTF-8, bursts), the line-diffing frame buffer, the canvas through the
  style layer;
* the interface on a pseudo-terminal with ``TERM`` unset — every screen driven with
  the new key encoding; ``floofy init`` through the consent frame (recorded
  ``screen``); a governance-altering install whose target is still a typed text
  field; the same runs with ``curses`` blocked out of ``sys.modules``;
* no module of the package imports ``curses`` (the Python bundled with the host has
  no ``_curses``);
* the banner fallbacks and ``--json`` byte-identity, restated here from their own
  suites so this file is the package's test entry.

Scratch homes and fake payloads only; every pseudo-terminal run has a timeout.
"""
from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import pty
import re
import select
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

from floofy_core.audit import read_audit
from floofy_core.cli import banner, term, tui
from floofy_core.cli.console import Console
from floofy_core.cli.main import build_parser, execute, run
from floofy_core.cli.style import Depth, Style, cube_index, strip_ansi
from floofy_core.consent import read_consent, write_consent
from floofy_core.datahome import DataHome

from floofy_testing import REPO_ROOT, fake_payload

_CONTROL = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\(B|\x1b[=>]|\x1b\][^\x07]*\x07")

#: ``python -c`` code that runs the CLI with curses blocked out of ``sys.modules`` (an interpreter built without ``_curses``).
NO_CURSES_PROGRAM = "import sys; sys.modules['_curses'] = None; sys.modules['curses'] = None; import floofy_core.cli.tui, floofy_core.cli.consent_screen; from floofy_core.cli.main import main; sys.exit(main())"


def screen_text(raw: str) -> str:
    """Terminal output as words: every control sequence (colours, cursor moves, modes) becomes a space."""
    return " ".join(_CONTROL.sub(" ", raw).split())


SAMPLE = {"id": "alpha", "ref": "/tmp/some-mod", "query": "words", "url": "https://example.invalid/registry/", "trust": "owner", "name": "daily", "file": "/tmp/daily.json", "hostver": "0.7.0.5"}


# --- the action table ----------------------------------------------------------------------------------------


def test_every_action_reaches_the_same_handler_as_its_subcommand():
    parser = build_parser()
    assert len(tui.ACTIONS) >= 25 and len({a.key for a in tui.ACTIONS}) == len(tui.ACTIONS), "unique keys"
    for action in tui.ACTIONS:
        assert set(action.placeholders) <= set(SAMPLE), action.key
        argv = action.build(**SAMPLE)
        args = parser.parse_args(argv)
        assert args.handler is action.handler, f"{action.key}: floofy {' '.join(argv)} dispatches to {args.handler.__name__}, the interface names {action.handler.__name__}"
        assert callable(action.handler) and action.handler.__module__.startswith("floofy_core.cli.cmd_"), action.key
        if argv[0] == "registry":
            assert args.registry_action == argv[1]
        if argv[0] == "profile":
            assert args.profile_action == argv[1]


def test_the_table_covers_every_menu_of_requirement_15_4():
    keys = {a.key for a in tui.ACTIONS}
    for required in ("mods.enable", "mods.disable", "mods.update", "mods.uninstall", "mods.yeet", "mods.yeet_restore", "mods.install", "mods.search", "registries.add", "registries.remove", "registries.refresh", "registries.trust", "profiles.export", "profiles.import", "profiles.use", "doctor", "updates.check", "setup.init"):
        assert required in keys, required
    assert tui.action("mods.install").needs_consent and tui.action("mods.enable").needs_consent and tui.action("updates.apply").needs_consent
    assert not tui.action("mods.disable").needs_consent and not tui.action("doctor").needs_consent, "consent gates mutations that apply something, not reads or disables"
    assert "--allow-unsigned" in tui.action("registries.add_unsigned").argv and "--allow-unsigned" not in tui.action("registries.add").argv
    assert tui.action("mods.install").build(ref="ssh://host/path@v1") == ["install", "ssh://host/path@v1"]


def test_global_argv_respells_the_bare_invocation():
    args = build_parser().parse_args(["--home", "/h", "--root", "/r1", "--root", "/r2", "--no-adapters", "--kirocrew", "/k", "--port", "7", "--token", "t", "--yes"])
    assert tui.global_argv(args) == ["--home", "/h", "--root", "/r1", "--root", "/r2", "--no-adapters", "--kirocrew", "/k", "--port", "7", "--token", "t", "--yes"]
    assert tui.global_argv(build_parser().parse_args([])) == []


# --- in-process runs ----------------------------------------------------------------------------------------


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FLOOFY_NO_ADAPTERS", "1")
    monkeypatch.delenv("KIROCREW_HOME", raising=False)
    monkeypatch.setenv("KIRO_HOME", str(tmp_path / "kiro"))
    for name in ("NO_COLOR", "FORCE_COLOR", "COLORTERM"):
        monkeypatch.delenv(name, raising=False)
    payload_root = tmp_path / "payload"
    fake_payload(payload_root, "0.7.0", build="0.7.0.5")
    home = tmp_path / "home"
    data = DataHome.for_host_home(home).ensure()
    (data.mods / "alpha").mkdir()
    (data.mods / "alpha" / "floofy.json").write_text(json.dumps({"schema": 1, "id": "alpha", "name": "A", "version": "1.0.0", "description": "d", "authors": ["a"], "license": "MIT", "kirocrew": {"version": ">=0.7.0 <0.9.0"}, "dependsOn": {"floofycrew": ">=0.0.0"}, "parts": [{"kind": "theme", "side": "gateway", "path": "theme/theme.json"}], "files": []}), encoding="utf-8")

    class Host:
        root = payload_root
        home_dir = home
        paths = data
        kiro = tmp_path / "kiro"

        def argv(self, *args: str) -> list[str]:
            return ["--home", str(home), "--root", str(payload_root), *args]

        def prefix(self) -> list[str]:
            return tui.global_argv(build_parser().parse_args(self.argv()))

    return Host()


def test_run_action_runs_the_handler_in_process_and_writes_the_tui_audit_row(host):
    write_consent(host.paths.consent, by="tests", how="test")
    console = Console(out=io.StringIO(), err=io.StringIO(), non_interactive=False, style=Style.off(), confirm_fn=lambda prompt, default: True, typed_fn=lambda prompt, what: None, consent_fn=lambda: True)
    listed = tui.run_action(tui.action("mods.list"), host.prefix(), console=console)
    assert listed.exit == 0 and listed.argv[-1] == "list" and any("alpha 1.0.0" in line for line in listed.lines)
    assert listed.result["mods"][0]["id"] == "alpha" and all("\x1b[" not in line for line in listed.lines), "plain transcript for the pane"
    disabled = tui.run_action(tui.action("mods.disable"), host.prefix(), console=console, id="alpha")
    assert disabled.exit == 0 and "alpha: disabled." in disabled.lines
    rows = read_audit(host.paths.audit)
    assert rows[-1]["op"] == "disable" and rows[-1]["actor"] == "tui" and rows[-1]["mod"] == "alpha" and rows[-1]["consentRef"]
    # the CLI's own row for the same operation has the same shape; only the actor (and the time) differ
    cli = run(host.argv("enable", "alpha"), non_interactive=True, actor="cli")
    assert cli.exit == 0
    cli_row = read_audit(host.paths.audit)[-1]
    assert set(cli_row) == set(rows[-1]) and cli_row["actor"] == "cli"
    # a yes/no confirmation is answered by the interface's control, never by a default
    answers: list[tuple[str, bool]] = []
    console.confirm_fn = lambda prompt, default: answers.append((prompt, default)) or False
    cancelled = tui.run_action(tui.action("mods.uninstall"), host.prefix(), console=console, id="alpha")
    assert cancelled.exit == 1 and answers and answers[0][1] is True and (host.paths.mods / "alpha").is_dir()


def test_host_summary_uses_the_doctor_facts(host):
    from floofy_core.cli.context import build_context

    ctx = build_context(build_parser().parse_args(host.argv()), console=Console(out=io.StringIO(), err=io.StringIO(), non_interactive=True, style=Style.off()), actor="tui")
    rows = dict(tui.host_summary(ctx))
    assert rows["edition"] == "internal" and rows["version"] == "0.7.0.5" and rows["loader"] == "not installed"
    assert rows["consent"].startswith("missing") and rows["data home"] == str(host.paths.data_home)
    write_consent(host.paths.consent, by="tests", how="screen")
    assert dict(tui.host_summary(ctx))["consent"] == "recorded (screen, v1)"


# --- bare floofy: usage without a terminal, the fallback ---------------------------------------------------------


def test_bare_floofy_without_a_terminal_prints_the_usage_and_exits_2(host):
    result = run(host.argv(), non_interactive=True, actor="test")
    assert result.exit == 2 and result.stdout.startswith("usage: floofy") and result.json == {"error": "no command"}
    assert tui.FALLBACK_HINT not in result.stdout, "no hint when there simply is no terminal"
    console = Console(out=io.StringIO(), err=io.StringIO())
    code, document = execute(host.argv(), console)
    assert code == 2 and console.out.getvalue().startswith("usage: floofy") and document == {"error": "no command"}
    quiet = run(host.argv("--json"), non_interactive=True)
    assert quiet.exit == 2 and quiet.stdout == ""


def test_bare_floofy_falls_back_only_under_term_dumb_or_without_a_terminal(host, monkeypatch: pytest.MonkeyPatch):
    """Requirement 15.6: an unset ``TERM`` is an ANSI terminal; only ``dumb`` (and no TTY) degrade to the usage."""
    attempts: list[str] = []

    def cannot_open(self):
        attempts.append(os.environ.get("TERM", "<unset>"))
        raise term.TerminalUnavailable("no tty in this test")

    monkeypatch.setattr(Console, "screen_available", property(lambda self: True))
    monkeypatch.setattr(term.Terminal, "enter", cannot_open)
    monkeypatch.delenv("TERM", raising=False)
    console = Console(out=io.StringIO(), err=io.StringIO())
    code, document = execute(host.argv(), console)
    out = console.out.getvalue()
    assert code == 2 and out.startswith("usage: floofy") and tui.FALLBACK_HINT in out and document == {"error": "no command"}
    assert attempts == ["<unset>"], "TERM unset: the interface is attempted (and here the streams are not terminals, so the hint follows)"
    assert "stdin and stdout TTYs" in tui.FALLBACK_HINT and "not TERM=dumb" in tui.FALLBACK_HINT and "curses" not in tui.FALLBACK_HINT
    monkeypatch.setenv("TERM", "xterm")
    assert tui.launch(build_parser().parse_args(host.argv()), console) is None and attempts == ["<unset>", "xterm"]
    # TERM=dumb never even tries
    monkeypatch.setenv("TERM", "dumb")
    assert tui.launch(build_parser().parse_args(host.argv()), console) is None and attempts == ["<unset>", "xterm"]
    assert term.is_dumb({"TERM": "dumb"}) and term.is_dumb({"TERM": " DUMB "}) and not term.is_dumb({}) and not term.is_dumb({"TERM": ""}) and not term.is_dumb({"TERM": "vt100"})
    # and a console that is not on a terminal never launches
    monkeypatch.undo()
    assert tui.launch(build_parser().parse_args(host.argv()), Console(out=io.StringIO())) is None


def test_no_module_of_the_package_imports_curses():
    """The Python bundled with the host has no ``_curses``: ``grep -r "import curses"`` over the package is empty."""
    package = REPO_ROOT / "floofy-core" / "floofy_core"
    offenders = [str(path.relative_to(package)) for path in package.rglob("*.py") if re.search(r"^\s*(import curses|from curses|import _curses|from _curses)", path.read_text(encoding="utf-8"), re.MULTILINE) or "import curses" in path.read_text(encoding="utf-8")]
    assert offenders == []


# --- the terminal driver -----------------------------------------------------------------------------------------


class Keys:
    """A ``Terminal`` reading from a pipe: no tty, no modes — the decoder alone."""

    def __init__(self):
        self.read_end, self.write_end = os.pipe()
        self.terminal = term.Terminal(stdin=io.StringIO(), stdout=io.StringIO(), escape_delay=0.03)
        self.terminal._in_fd = self.read_end

    def type(self, data: bytes) -> None:
        os.write(self.write_end, data)

    def key(self, timeout: float = 0.5) -> str | None:
        return self.terminal.read_key(timeout)

    def close(self) -> None:
        for fd in (self.read_end, self.write_end):
            try:
                os.close(fd)
            except OSError:
                pass


@pytest.fixture
def keys():
    reader = Keys()
    yield reader
    reader.close()


def test_the_driver_decodes_every_key_of_requirement_15_6(keys: Keys):
    keys.type(b"\r\n\t\x7f\x08\x03\x15q1/9\x1b[A\x1b[B\x1b[C\x1b[D\x1b[H\x1b[F\x1b[5~\x1b[6~\x1b[1~\x1b[4~\x1b[7~\x1b[8~\x1b[3~\x1b[2~\x1bOA\x1bOB\x1bOH\x1b[1;5A\x1b[Z")
    names = [keys.key() for _ in range(30)]
    assert names == ["enter", "enter", "tab", "backspace", "backspace", "ctrl-c", "ctrl-u", "q", "1", "/", "9", "up", "down", "right", "left", "home", "end", "pageup", "pagedown", "home", "end", "home", "end", "delete", "insert", "up", "down", "home", "up", "backtab"]
    assert keys.key(timeout=0.05) is None, "nothing pending: a timeout"
    # UTF-8 text arrives as characters, whether whole or split across reads
    keys.type("é▸ü".encode("utf-8"))
    assert [keys.key() for _ in range(3)] == ["é", "▸", "ü"]
    keys.type("▸".encode("utf-8")[:1])
    time.sleep(0.005)
    keys.type("▸".encode("utf-8")[1:])
    assert keys.key() == "▸"
    # a burst is queued in order, never dropped (the harness types "jjjjjj\r" in one write)
    keys.type(b"jjjjjj\r")
    assert [keys.key() for _ in range(7)] == ["j"] * 6 + ["enter"]
    # unknown sequences and Alt-<key> are "other" and do not swallow what follows
    keys.type(b"\x1b[99z\x1bxq")
    assert [keys.key() for _ in range(3)] == ["other", "other", "q"]


def test_a_lone_escape_is_told_from_an_arrow_by_the_short_timeout(keys: Keys):
    keys.type(b"\x1b")
    started = time.monotonic()
    assert keys.key() == "esc"
    assert time.monotonic() - started < 0.4, "the escape delay, not the poll interval"
    keys.type(b"\x1b")
    time.sleep(0.01)
    keys.type(b"[B")
    assert keys.key() == "down", "the rest of the sequence arrived within the delay"
    keys.type(b"\x1b[")
    time.sleep(0.01)
    keys.type(b"6~")
    assert keys.key() == "pagedown", "a CSI split after the bracket completes too"
    keys.type(b"\x1bq")
    assert [keys.key(), keys.key(timeout=0.05)] == ["other", None], "Escape followed by a letter is Alt-q, not Esc then q"
    os.close(keys.write_end)
    assert keys.key() == "eof"
    assert term.decode_sequence(b"\x1b[A") == "up" and term.decode_sequence(b"\x1b[1;2F") == "end" and term.decode_sequence(b"\x1b[5;3~") == "pageup" and term.decode_sequence(b"\x1bOD") == "left" and term.decode_sequence(b"\x1b[?") == "other"


def test_paint_diffs_line_by_line_with_cursor_moves(monkeypatch: pytest.MonkeyPatch):
    out = io.StringIO()
    terminal = term.Terminal(stdin=io.StringIO(), stdout=out)
    size = [(20, 4)]
    monkeypatch.setattr(term.Terminal, "size", lambda self: size[0])
    terminal.paint(["title", "", "item one", "footer"])
    first = out.getvalue()
    assert first.startswith(term.CLEAR) and "\x1b[1;1H\x1b[2Ktitle" in first and "\x1b[3;1H\x1b[2Kitem one" in first and "\x1b[4;1H\x1b[2Kfooter" in first
    assert "\x1b[2;1H" not in first, "an empty row on a cleared screen is not painted"
    out.seek(0), out.truncate()
    terminal.paint(["title", "", "item two", "footer"])
    second = out.getvalue()
    assert second == "\x1b[3;1H\x1b[2Kitem two", "only the changed row is rewritten: no clear, no flicker"
    out.seek(0), out.truncate()
    terminal.paint(["title", "", "item two", "footer"])
    assert out.getvalue() == "", "nothing changed, nothing written"
    # a text field shows the cursor at one cell; the next frame hides it again
    terminal.paint(["title", "> ab", "item two", "footer"], cursor=(1, 4))
    with_cursor = out.getvalue()
    assert with_cursor.endswith("\x1b[2;5H" + term.SHOW_CURSOR) and "\x1b[2;1H\x1b[2K> ab" in with_cursor
    out.seek(0), out.truncate()
    terminal.paint(["title", "", "item two", "footer"])
    assert out.getvalue() == "\x1b[2;1H\x1b[2K" + term.HIDE_CURSOR
    # too many rows are dropped, long rows clipped (SGR sequences not counted), a resize clears and repaints
    out.seek(0), out.truncate()
    terminal.paint(["\x1b[1m" + "x" * 30 + "\x1b[0m", "", "", "", "fifth"])
    clipped = out.getvalue()
    assert "\x1b[1;1H\x1b[2K\x1b[1m" + "x" * 20 + "\x1b[0m" in clipped and "fifth" not in clipped and "\x1b[5;1H" not in clipped
    size[0] = (30, 5)
    out.seek(0), out.truncate()
    terminal.paint(["title", "", "", "", "fifth"])
    resized = out.getvalue()
    assert resized.startswith(term.CLEAR) and "\x1b[5;1H\x1b[2Kfifth" in resized
    assert term.clip("abc", 5) == "abc" and term.clip("\x1b[32mabcdef\x1b[0m", 3) == "\x1b[32mabc\x1b[0m" and term.clip("abcdef", 3) == "abc"


def test_the_canvas_paints_through_the_style_layer_at_the_detected_depth():
    canvas = term.Canvas(12, 3)
    canvas.put(0, 0, "Title", "heading")
    canvas.put(1, 2, "▸ pick", reverse=True)
    canvas.put(2, 0, "a\tb\nc", None)
    canvas.put(1, 9, "much too long", "muted")
    canvas.put(5, 0, "off grid")
    canvas.put(0, -2, "xy", underline=True)
    plain = canvas.render(Style.off())
    assert plain == ["Title", "  ▸ pick muc", "a b c"], "off: the words, clipped to the grid, control characters neutralised, blanks trimmed"
    basic = canvas.render(Style(Depth.BASIC))
    assert basic[0] == "\x1b[1;33mTitle\x1b[0m" and basic[1] == "  \x1b[7m▸ pick\x1b[0m \x1b[2mmuc\x1b[0m" and basic[2] == "a b c"
    truecolor = canvas.render(Style(Depth.TRUECOLOR))
    assert truecolor[0] == "\x1b[1;38;2;255;154;61mTitle\x1b[0m"
    # the banner's cells: exact colours on truecolor, the cube on 256, one bright magenta run on 16 colours
    art = term.Canvas(10, 1)
    art.put(0, 0, "a", rgb=(255, 107, 139))
    art.put(0, 1, "b", rgb=(255, 154, 61))
    art.put(0, 2, "c", rgb=(255, 154, 61))
    assert art.render(Style(Depth.TRUECOLOR))[0] == "\x1b[38;2;255;107;139ma\x1b[0m\x1b[38;2;255;154;61mbc\x1b[0m"
    assert art.render(Style(Depth.EXTENDED))[0] == f"\x1b[38;5;{cube_index(255, 107, 139)}ma\x1b[0m\x1b[38;5;{cube_index(255, 154, 61)}mbc\x1b[0m"
    assert art.render(Style(Depth.BASIC))[0] == "\x1b[95mabc\x1b[0m", "one run: every cell paints with the same parameters"
    assert art.render(Style.off())[0] == "abc"
    field = term.Canvas(10, 1)
    field.put(0, 0, "> v", "accent", reverse=True, underline=True)
    assert field.render(Style(Depth.BASIC))[0] == "\x1b[7;4;95m> v\x1b[0m"
    canvas.erase()
    assert canvas.render(Style(Depth.BASIC)) == ["", "", ""]


# --- on a pseudo-terminal ------------------------------------------------------------------------------------


def _env(host, **extra: str) -> dict[str, str]:
    """The child's environment: ``TERM`` unset (Requirement 15.6 — an ANSI terminal without saying so), colours as given."""
    base = {k: v for k, v in os.environ.items() if k not in ("NO_COLOR", "FORCE_COLOR", "COLORTERM", "COLUMNS", "LINES", "TERM")}
    return {**base, "FLOOFY_NO_ADAPTERS": "1", "KIRO_HOME": str(host.kiro), "PYTHONPATH": str(REPO_ROOT / "floofy-core"), **extra}


def drive(argv: list[str], env: dict[str, str], steps: list[tuple[str, str]], *, rows: int = 45, columns: int = 120, timeout: float = 90, program: str | None = None) -> tuple[int, str]:
    """Run ``floofy`` on a pseudo-terminal; for each ``(wait_for, send)`` wait for the text on screen, then type.

    ``program`` runs the CLI through ``python -c <program>`` instead of ``python -m
    floofy_core.cli`` (the curses-blocked wrapper).
    """
    controller, follower = pty.openpty()
    fcntl.ioctl(follower, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
    command = [sys.executable, "-c", program, *argv] if program else [sys.executable, "-m", "floofy_core.cli", *argv]
    proc = subprocess.Popen(command, stdin=follower, stdout=follower, stderr=follower, env=env, close_fds=True)
    os.close(follower)
    transcript = b""
    mark = 0  # everything before it was on screen before the previous key was sent
    deadline = time.monotonic() + timeout

    def pump(until: str | None) -> None:
        nonlocal transcript, mark
        while time.monotonic() < deadline:
            if until is not None and until in screen_text(transcript[mark:].decode("utf-8", "replace")):
                mark = len(transcript)
                return
            ready, _, _ = select.select([controller], [], [], 0.2)
            if not ready:
                if until is None and proc.poll() is not None:
                    return
                continue
            try:
                chunk = os.read(controller, 65536)
            except OSError:
                return
            if not chunk:
                return
            transcript += chunk
        proc.kill()
        raise AssertionError(f"timed out waiting for {until!r}; screen so far:\n{screen_text(transcript.decode('utf-8', 'replace'))[-3000:]}")

    for wait_for, send in steps:
        pump(wait_for)
        os.write(controller, send.encode("utf-8"))
    pump(None)
    os.close(controller)
    return proc.wait(timeout=30), transcript.decode("utf-8", "replace")


def _alternate_screen_bracketed(out: str) -> bool:
    return term.ENTER_ALTERNATE in out and term.LEAVE_ALTERNATE in out and out.index(term.ENTER_ALTERNATE) < out.rindex(term.LEAVE_ALTERNATE) and out.rstrip().endswith(term.LEAVE_ALTERNATE)


def test_the_interface_opens_with_term_unset_and_quits_with_q(host):
    code, out = drive(host.argv(), _env(host), [("Quit", "q")])
    assert code == 0, screen_text(out)[-2000:]
    plain = screen_text(out)
    for text in ("floofy — interactive", "unofficial", "edition", "internal", "0.7.0.5", "Mods", "Registries", "Profiles", "Doctor", "Check for updates", "Quit"):
        assert text in plain, text
    assert _alternate_screen_bracketed(out), "drawn on the alternate screen, which is left at the end"
    assert term.HIDE_CURSOR in out and out.rstrip().endswith(term.SHOW_CURSOR + term.LEAVE_ALTERNATE), "the cursor is hidden while drawn and shown again"
    assert "\x1b[95m" in out and "38;5;" not in out and "38;2;" not in out, "TERM unset: 16 colours — the banner in the single bright magenta"
    assert "usage: floofy" not in plain and tui.FALLBACK_HINT not in plain


def _first_frame(raw: str) -> dict[int, str]:
    """The first frame as ``row index -> plain text``: the driver paints each changed row as ``ESC[n;1H ESC[2K <row>``,
    so the first occurrence of every row index is the first frame's content."""
    frame: dict[int, str] = {}
    for match in re.finditer(r"\x1b\[(\d+);1H\x1b\[2K(.*?)(?=\x1b\[\d+;1H|\x1b\[\?|\Z)", raw, re.S):
        index = int(match.group(1)) - 1
        frame.setdefault(index, strip_ansi(match.group(2)))
    return frame


def test_the_wordmark_sits_right_of_the_fox_centred_on_its_height(host):
    """Block "FloofyCrew" (7 rows) beside the 15-row fox on a wide terminal; the plain two-colour word when it does not fit."""
    art, mark = banner.load(), banner.load_wordmark()
    left = banner.MARGIN // 2 + art.width + banner.WORDMARK_GAP
    wide = banner.wordmark_columns(art, mark)
    assert wide <= 110, "the block letters fit a 110-column terminal"
    _, out = drive(host.argv(), _env(host, COLORTERM="truecolor"), [("Quit", "q")], columns=wide)
    frame = _first_frame(out)
    for index, text in enumerate(art.text_rows):
        assert frame[index][banner.MARGIN // 2 : banner.MARGIN // 2 + art.width].rstrip() == text.rstrip(), f"fox row {index}"
    top = banner.wordmark_top(art, mark)
    for index, text in enumerate(mark.text_rows):
        assert frame[top + index][left : left + mark.width].rstrip() == text.rstrip(), f"wordmark row {index} at column {left}, row {top + index}"
    assert all(not frame[i][left:].strip() for i in range(art.height) if not top <= i < top + mark.height), "only the wordmark shares the fox's rows"
    assert "\x1b[38;2;255;228;207m" in out, '"Floofy" in the peach of wordmark-dark.svg'
    _, narrow = drive(host.argv(), _env(host, COLORTERM="truecolor"), [("Quit", "q")], columns=wide - 1)
    frame = _first_frame(narrow)
    middle = art.height // 2
    assert frame[middle][left : left + len("FloofyCrew")] == "FloofyCrew", "one column short of the block letters: the plain word, on the fox's middle row"
    assert all(not frame[i][left:].strip() for i in range(art.height) if i != middle)
    assert "\x1b[38;2;255;228;207mFloofy" in narrow and "\x1b[38;2;255;154;61mCrew" in narrow, "peach Floofy, orange Crew"


def test_the_banner_follows_the_detected_colour_depth(host):
    _, extended = drive(host.argv(), _env(host, TERM="xterm-256color"), [("Quit", "q")])
    assert "\x1b[38;5;" in extended and "38;2;" not in extended, "TERM=xterm-256color: the 256-colour cube"
    _, truecolor = drive(host.argv(), _env(host, COLORTERM="truecolor"), [("Quit", "q")])
    assert "\x1b[38;2;" in truecolor, "COLORTERM=truecolor: the art's exact colours"
    _, none = drive(host.argv(), _env(host, NO_COLOR="1"), [("Quit", "q")])
    assert "\x1b[38;" not in none and "\x1b[7m" not in none and "\x1b[95m" not in none, "NO_COLOR: no colour, no banner, no reverse video — as everywhere in the CLI"
    assert "floofy — interactive" in screen_text(none) and "Quit" in screen_text(none)


def test_a_dumb_terminal_gets_the_usage_and_the_hint(host):
    code, out = drive(host.argv(), _env(host, TERM="dumb"), [])
    plain = screen_text(out)
    assert code == 2 and "usage: floofy" in plain and tui.FALLBACK_HINT in plain and term.ENTER_ALTERNATE not in out


def test_every_screen_answers_to_the_arrow_page_home_end_and_tab_keys(host):
    steps = [
        ("Quit", "\x1b[B\r"),  # ↓ Enter → Registries
        ("Add a source", "\x1b"),  # Esc back
        ("Quit", "\x1b[B\x1b[B\r"),  # ↓↓ Enter → Profiles
        ("Save the installed set as a profile", "\x1b"),
        ("Quit", "\x1b[F\x1b[A\x1b[A\x1b[A\x1b[A\r"),  # End, ↑×4 → Doctor
        ("floofy doctor", "\x1b[6~\x1b[5~\x1b[F\x1b[H\x1b"),  # PgDn, PgUp, End, Home in the pane; Esc closes it
        ("Quit", "\x1b[H\r"),  # Home → Mods
        ("Install from the registry", "\x1b[6~\r"),  # PgDn → the last item: Status of every mod
        ("floofy status", "\r"),  # Enter closes the pane
        ("Install from the registry", "/"),  # / opens the filter field
        ("Filter the list", "quarantine\r"),
        ("filter: quarantine", "\x1b"),  # Esc clears the filter
        ("Install from the registry", "\x1b"),  # Esc leaves Mods
        ("Quit", "\t\t\t\t\r"),  # Tab×4 → Check for updates
        ("floofy update --all --check", "q"),  # q closes the pane
        ("Quit", "\x1b[6~\r"),  # PgDn → Quit, Enter
    ]
    code, out = drive(host.argv(), _env(host), steps, timeout=150)
    plain = screen_text(out)
    assert code == 0, plain[-3000:]
    for text in ("Registries", "Profiles", "floofy doctor", "Status of every mod", "floofy status", "filter: quarantine", "Show the quarantine", "floofy update --all --check"):
        assert text in plain, text
    assert "(nothing matches)" not in plain, "the filter matched the quarantine items"
    rows = read_audit(host.paths.audit) if host.paths.audit.exists() else []
    assert all(row["actor"] == "tui" for row in rows), "every action ran as the interface"


def test_text_fields_take_digits_backspace_and_ctrl_u_and_ctrl_c_leaves_with_130(host):
    steps = [
        ("Quit", "\r"),  # Mods
        ("Install from a git reference", "\x1b[B\x1b[B\r"),  # ↓↓ Enter → the git reference field
        ("Git reference to install", "xyz1\x7f\x7f9"),  # type, Backspace×2, a digit
        ("> xy9", "\x15abc"),  # Ctrl-U clears, then abc
        ("> abc", "\x1b"),  # Esc cancels the field
        ("Install from a git reference", "\x1b"),  # Esc leaves Mods
        ("Quit", "\x03"),  # Ctrl-C at the home screen: exit 130, terminal restored
    ]
    code, out = drive(host.argv(), _env(host), steps)
    plain = screen_text(out)
    assert code == 130, plain[-2000:]
    assert "> xy9" in plain and "> abc" in plain and "> xyz1" in plain
    assert "\x1b[?25h" in out, "the cursor is shown while a field is edited"
    assert _alternate_screen_bracketed(out), "Ctrl-C: the alternate screen is still left and the cursor shown"
    assert not host.paths.audit.exists() or all(r["op"] != "install" for r in read_audit(host.paths.audit)), "the cancelled field ran nothing"


def test_init_through_the_interface_uses_the_consent_frame_and_records_screen(host, tmp_path: Path):
    # `--kirocrew` names a file that does not exist so no host launcher is ever run: init warns and prints the manual steps
    steps = [("Set up FloofyCrew (floofy init)", "jjjjjj\r"), ("[ I AGREE ]", "\x1b[B\x1b[A\r"), ("floofy init — ok", "\r"), ("Re-run floofy init", "q")]
    code, out = drive(host.argv("--kirocrew", str(tmp_path / "no-such-kirocrew")), _env(host), steps, timeout=150)
    assert code == 0, screen_text(out)[-3000:]
    status = read_consent(host.paths.consent)
    assert status.ok and status.how == "screen"
    rows = read_audit(host.paths.audit)
    assert rows[0]["op"] == "consent" and rows[0]["actor"] == "tui" and rows[0]["how"] == "screen" and rows[-1]["op"] == "init" and rows[-1]["actor"] == "tui"
    plain = screen_text(out)
    assert "READ THIS ONCE" in plain and "the risk of every mod you install is yours" in plain
    assert "Enter agrees · Esc or q declines" in plain, "the standalone screen's own frame (compose) and hint"
    assert out.count(term.ENTER_ALTERNATE) == 1, "the consent frame is drawn on the interface's own terminal, not a second one"
    assert "consent: acknowledged on the full-screen control" in plain, "the same init step ran, and its output landed in the pane"
    assert "cannot install automatically" in plain and not (host.home_dir / "apps").exists(), "no launcher was run"


def test_declining_the_consent_frame_in_the_interface_records_nothing(host, tmp_path: Path):
    steps = [("Set up FloofyCrew (floofy init)", "jjjjjj\r"), ("[ I AGREE ]", "\x1b"), ("floofy init — exit 3", "\r"), ("Set up FloofyCrew (floofy init)", "q")]
    code, out = drive(host.argv("--kirocrew", str(tmp_path / "no-such-kirocrew")), _env(host), steps, timeout=150)
    assert code == 0, screen_text(out)[-3000:]
    assert not host.paths.consent.exists() and "consent not given: declined on the full-screen control" in screen_text(out)


def test_a_governance_altering_install_keeps_the_typed_field(host, tmp_path: Path):
    write_consent(host.paths.consent, by="tests", how="test")
    mod = tmp_path / "govy"
    (mod / "agents").mkdir(parents=True)
    (mod / "agents" / "a.json").write_text(json.dumps({"name": "a"}), encoding="utf-8")
    (mod / "security_policy.json").write_text("{}", encoding="utf-8")
    files = [{"path": rel, "sha256": hashlib.sha256((mod / rel).read_bytes()).hexdigest()} for rel in ("agents/a.json", "security_policy.json")]
    (mod / "floofy.json").write_text(json.dumps({"schema": 1, "id": "govy", "name": "govy", "version": "1.0.0", "description": "d", "authors": ["t"], "license": "MIT", "kirocrew": {"version": ">=0.7.0 <0.9.0"}, "dependsOn": {"floofycrew": ">=0.0.0"}, "parts": [{"kind": "agent", "side": "gateway", "path": "agents/a.json"}], "files": files}), encoding="utf-8")
    # Mods → "Install from a git reference" (the field takes any reference the resolver takes; a path here) → the typed field
    # the validator flags the undeclared governance file: an ordinary yes/no (a two-item menu, default no → `k` then Enter says yes);
    # the governance-altering target itself is a text field: Enter alone refuses, the exact path confirms
    steps = [("Quit", "\r"), ("Install from a git reference", "jj\r"), ("Git reference to install", str(mod) + "\r"), ("Accept the 1 flag(s) above", "k\r"), ("Type the exact path to confirm this one file", "\r"), ("floofy install", "\r"), ("Install from a git reference", "jj\r"), ("Git reference to install", str(mod) + "\r"), ("Accept the 1 flag(s) above", "\x1b[A\r"), ("Type the exact path to confirm this one file", "security_policy.json\r"), ("floofy install", "\r"), ("[on ] govy 1.0.0", "\x1b"), ("Quit", "q")]
    code, out = drive(host.argv(), _env(host), steps, timeout=120)
    plain = screen_text(out)
    assert code == 0, plain[-3000:]
    assert "no shortcut, no button" in plain and "GOVERNANCE-ALTERING" in plain
    assert "was not confirmed by typing its path" in plain, "Enter alone (the consent screen's accept key) refused the first attempt"
    assert (host.paths.mods / "govy").is_dir()
    rows = read_audit(host.paths.audit)
    declined = [r for r in rows if r["op"] == "install" and r["result"] == "declined"]
    assert declined and declined[0]["actor"] == "tui"
    confirm = [r for r in rows if r["op"] == "governance-target-confirm"]
    assert len(confirm) == 1 and confirm[-1]["actor"] == "tui" and confirm[-1]["detail"] == "typed at the prompt" and confirm[-1]["files"] == ["security_policy.json"]
    assert rows[-1]["op"] == "install" and rows[-1]["result"] == "ok" and rows[-1]["actor"] == "tui" and "GovernanceAltering" in rows[-1]["governanceFlags"]


def test_the_interface_needs_no_installed_mods_and_the_registry_screens_open(host):
    steps = [("Quit", "j\r"), ("Add a source", "\x1b"), ("Quit", "jj\r"), ("Save the installed set as a profile", "\x1b"), ("Quit", "q")]
    code, out = drive(host.argv(), _env(host), steps)
    plain = screen_text(out)
    assert code == 0, plain[-2000:]
    assert "Registries" in plain and "Refresh every source" in plain and "Profiles" in plain and "Import a profile from a file" in plain


def test_the_interface_and_the_consent_frame_launch_without_curses(host, tmp_path: Path):
    """An interpreter without ``_curses`` (``sys.modules`` blocked): bare ``floofy`` opens, ``floofy init`` shows the frame."""
    code, out = drive(host.argv(), _env(host), [("Quit", "q")], program=NO_CURSES_PROGRAM)
    plain = screen_text(out)
    assert code == 0, plain[-2000:]
    assert "floofy — interactive" in plain and "Quit" in plain and "usage: floofy" not in plain and _alternate_screen_bracketed(out)
    code, out = drive(host.argv("init", "--no-loader-app", "--no-trigger"), _env(host), [("[ I AGREE ]", "\r")], program=NO_CURSES_PROGRAM)
    plain = screen_text(out)
    assert code == 0, plain[-2000:]
    assert "READ THIS ONCE" in plain and read_consent(host.paths.consent).how == "screen"
    assert term.ENTER_ALTERNATE in out and out.index(term.ENTER_ALTERNATE) < out.index(term.LEAVE_ALTERNATE) < out.index("consent: acknowledged"), "the frame on the alternate screen, left before init's summary prints"
    # the same in-process: the modules import and the driver opens on a pseudo-terminal with curses blocked
    check = subprocess.run([sys.executable, "-c", "import sys; sys.modules['_curses'] = None; sys.modules['curses'] = None\nimport floofy_core.cli.tui as t, floofy_core.cli.consent_screen as c, floofy_core.cli.term as d\nimport pty, os\ncontroller, follower = pty.openpty()\nterminal = d.Terminal(os.fdopen(follower, 'r', closefd=False), os.fdopen(follower, 'w', closefd=False))\nterminal.enter(); terminal.paint(['ok']); terminal.leave()\nprint('launched', callable(t.launch), callable(c.ConsentScreen.run))"], capture_output=True, text=True, env=_env(host), timeout=120, check=False)
    assert check.returncode == 0 and check.stdout.strip() == "launched True True", check.stderr


# --- restated guarantees --------------------------------------------------------------------------------------


def test_banner_fallbacks_restated():
    assert banner.render(Depth.OFF, width=200) is None
    assert banner.render(Depth.TRUECOLOR, width=banner.load().width + banner.MARGIN - 1) is None, "narrower than the art + its margin: omitted"
    truecolor = banner.render_lines(Depth.TRUECOLOR, width=100)
    basic = banner.render_lines(Depth.BASIC, width=100)
    extended = banner.render_lines(Depth.EXTENDED, width=100)
    assert truecolor and all("38;2;" in line for line in truecolor)
    assert extended and all("38;5;" in line and "38;2;" not in line for line in extended)
    assert basic and all(line.startswith("  \x1b[95m") and "38;" not in line for line in basic)


def test_json_output_is_byte_identical_restated(host, monkeypatch: pytest.MonkeyPatch):
    plain = subprocess.run([sys.executable, "-m", "floofy_core.cli", *host.argv("--json", "list")], capture_output=True, env={**_env(host), "NO_COLOR": "1"}, timeout=120, check=False)
    forced = subprocess.run([sys.executable, "-m", "floofy_core.cli", *host.argv("--json", "list")], capture_output=True, env={**_env(host), "FORCE_COLOR": "1", "COLORTERM": "truecolor"}, timeout=120, check=False)
    assert plain.returncode == forced.returncode == 0 and plain.stdout == forced.stdout and b"\x1b[" not in plain.stdout
    assert json.loads(plain.stdout)["mods"][0]["id"] == "alpha"
