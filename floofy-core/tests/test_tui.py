"""The interactive ``floofy`` (tasks 10.5/10.6; Requirement 15.4, 15.6, 14.2, 13.3).

* handler mapping — for every action of the interface, parsing the subcommand argv
  it runs yields the very ``cmd_*`` handler that action names, so the interface
  cannot drift from the CLI; a run through ``run_action`` writes the same audit row
  with ``actor: tui``;
* non-terminal behaviour — bare ``floofy`` prints the usage and exits 2 as before;
* the curses fallback — ``TERM=dumb`` or a failing ``initscr`` → usage + one hint;
* the interface on a pseudo-terminal — home screen with the banner, quit with ``q``;
  ``floofy init`` through the curses consent frame (recorded ``screen``); a
  governance-altering install whose target is still a typed text field;
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
from floofy_core.cli import banner, tui
from floofy_core.cli.console import Console
from floofy_core.cli.main import build_parser, execute, run
from floofy_core.cli.style import Depth, Style
from floofy_core.consent import read_consent, write_consent
from floofy_core.datahome import DataHome

from floofy_testing import REPO_ROOT, fake_payload

_CURSES = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\(B|\x1b[=>]|\x1b\][^\x07]*\x07")


def screen_text(raw: str) -> str:
    """curses output as words: every control sequence (colours, cursor moves, modes) becomes a space."""
    return " ".join(_CURSES.sub(" ", raw).split())


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


# --- bare floofy: usage without a terminal, the curses fallback -------------------------------------------------


def test_bare_floofy_without_a_terminal_prints_the_usage_and_exits_2(host):
    result = run(host.argv(), non_interactive=True, actor="test")
    assert result.exit == 2 and result.stdout.startswith("usage: floofy") and result.json == {"error": "no command"}
    assert tui.FALLBACK_HINT not in result.stdout, "no hint when there simply is no terminal"
    console = Console(out=io.StringIO(), err=io.StringIO())
    code, document = execute(host.argv(), console)
    assert code == 2 and console.out.getvalue().startswith("usage: floofy") and document == {"error": "no command"}
    quiet = run(host.argv("--json"), non_interactive=True)
    assert quiet.exit == 2 and quiet.stdout == ""


def test_bare_floofy_falls_back_to_the_usage_when_curses_cannot_start(host, monkeypatch: pytest.MonkeyPatch):
    import curses

    monkeypatch.setattr(Console, "screen_available", property(lambda self: True))
    monkeypatch.setenv("TERM", "xterm-256color")

    def broken_initscr():
        raise curses.error("setupterm: could not find terminal")

    monkeypatch.setattr(curses, "initscr", broken_initscr)
    console = Console(out=io.StringIO(), err=io.StringIO())
    code, document = execute(host.argv(), console)
    out = console.out.getvalue()
    assert code == 2 and out.startswith("usage: floofy") and tui.FALLBACK_HINT in out and document == {"error": "no command"}
    # TERM=dumb never even tries curses
    calls: list[str] = []
    monkeypatch.setattr(curses, "initscr", lambda: calls.append("initscr"))
    monkeypatch.setenv("TERM", "dumb")
    assert tui.launch(build_parser().parse_args(host.argv()), console) is None and calls == []
    monkeypatch.delenv("TERM")
    assert tui.launch(build_parser().parse_args(host.argv()), console) is None and calls == []
    # and a console that is not on a terminal never launches
    monkeypatch.undo()
    assert tui.launch(build_parser().parse_args(host.argv()), Console(out=io.StringIO())) is None


# --- on a pseudo-terminal ------------------------------------------------------------------------------------


def _env(host, **extra: str) -> dict[str, str]:
    base = {k: v for k, v in os.environ.items() if k not in ("NO_COLOR", "FORCE_COLOR", "COLORTERM", "COLUMNS", "LINES")}
    return {**base, "FLOOFY_NO_ADAPTERS": "1", "KIRO_HOME": str(host.kiro), "PYTHONPATH": str(REPO_ROOT / "floofy-core"), "TERM": "xterm-256color", **extra}


def drive(argv: list[str], env: dict[str, str], steps: list[tuple[str, str]], *, rows: int = 45, columns: int = 120, timeout: float = 90) -> tuple[int, str]:
    """Run ``floofy`` on a pseudo-terminal; for each ``(wait_for, send)`` wait for the text on screen, then type."""
    controller, follower = pty.openpty()
    fcntl.ioctl(follower, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
    proc = subprocess.Popen([sys.executable, "-m", "floofy_core.cli", *argv], stdin=follower, stdout=follower, stderr=follower, env=env, close_fds=True)
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


def test_the_interface_opens_on_a_terminal_and_quits_with_q(host):
    code, out = drive(host.argv(), _env(host, COLORTERM="truecolor"), [("Quit", "q")])
    assert code == 0, screen_text(out)[-2000:]
    plain = screen_text(out)
    for text in ("floofy — interactive", "unofficial", "edition", "internal", "0.7.0.5", "Mods", "Registries", "Profiles", "Doctor", "Check for updates"):
        assert text in plain, text
    assert "\x1b[38;5;" in out, "the banner is drawn through the 256-colour cube"
    assert "usage: floofy" not in plain


def test_a_dumb_terminal_gets_the_usage_and_the_hint(host):
    code, out = drive(host.argv(), _env(host, TERM="dumb"), [])
    plain = screen_text(out)
    assert code == 2 and "usage: floofy" in plain and tui.FALLBACK_HINT in plain and "\x1b[?1049h" not in out


def test_init_through_the_interface_uses_the_consent_frame_and_records_screen(host, tmp_path: Path):
    # `--kirocrew` names a file that does not exist so no host launcher is ever run: init warns and prints the manual steps
    steps = [("Set up FloofyCrew (floofy init)", "jjjjj\r"), ("[ I AGREE ]", "\r"), ("floofy init — ok", "\r"), ("Re-run floofy init", "q")]
    code, out = drive(host.argv("--kirocrew", str(tmp_path / "no-such-kirocrew")), _env(host), steps, timeout=150)
    assert code == 0, screen_text(out)[-3000:]
    status = read_consent(host.paths.consent)
    assert status.ok and status.how == "screen"
    rows = read_audit(host.paths.audit)
    assert rows[0]["op"] == "consent" and rows[0]["actor"] == "tui" and rows[0]["how"] == "screen" and rows[-1]["op"] == "init" and rows[-1]["actor"] == "tui"
    plain = screen_text(out)
    assert "READ THIS ONCE" in plain and "the risk of every mod you install is yours" in plain
    assert "consent: acknowledged on the full-screen control" in plain, "the same init step ran, and its output landed in the pane"
    assert "cannot install automatically" in plain and not (host.home_dir / "apps").exists(), "no launcher was run"


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
    steps = [("Quit", "\r"), ("Install from a git reference", "jj\r"), ("Git reference to install", str(mod) + "\r"), ("Accept the 1 flag(s) above", "k\r"), ("Type the exact path to confirm this one file", "\r"), ("floofy install", "\r"), ("Install from a git reference", "jj\r"), ("Git reference to install", str(mod) + "\r"), ("Accept the 1 flag(s) above", "k\r"), ("Type the exact path to confirm this one file", "security_policy.json\r"), ("floofy install", "\r"), ("[on ] govy 1.0.0", "\x1b"), ("Quit", "q")]
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


# --- restated guarantees --------------------------------------------------------------------------------------


def test_banner_fallbacks_restated():
    assert banner.render(Depth.OFF, width=200) is None
    assert banner.render(Depth.TRUECOLOR, width=41) is None, "narrower than 38 + 4: omitted"
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
