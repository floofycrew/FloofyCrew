"""The full-screen consent (task 10.4; Requirement 15.3, 11.1, 11.4).

On a terminal ``floofy init`` shows the warning on the alternate screen buffer with
a single ``[ I AGREE ]`` control: Enter accepts (recorded ``how="screen"``, the rest
of the record identical to the typed path), Esc or ``q`` declines (exit 3, nothing
written), and the screen is restored either way. ``--i-accept-the-risk`` is
untouched (``flag``), a run without a terminal keeps the typed prompt (``typed``),
and the governance-target confirmation stays typed even on the very terminal the
consent screen would use. The screen is driven through a pseudo-terminal; homes
and payloads are scratch.
"""
from __future__ import annotations

import fcntl
import json
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

from floofy_core.audit import read_audit
from floofy_core.cli import consent_screen
from floofy_core.cli.console import Console
from floofy_core.cli.style import Depth, Style, strip_ansi
from floofy_core.consent import ACCEPT_PHRASE, WARNING_TEXT, read_consent, write_consent
from floofy_core.datahome import DataHome

from floofy_testing import REPO_ROOT, fake_payload

ENTER, LEAVE = consent_screen.ENTER_ALTERNATE, consent_screen.LEAVE_ALTERNATE


# --- the pure layout -------------------------------------------------------------------------------------


def test_compose_wraps_the_warning_and_ends_with_the_control():
    frame = consent_screen.compose(80, 30)
    plain = [strip_ansi(row) for row in frame.rows]
    assert plain[-2].strip() == consent_screen.CONTROL and consent_screen.HINT in plain[-1]
    body = "\n".join(plain[:-3])
    assert consent_screen.TITLE in body
    for word in ("UNOFFICIAL", "gateway's full privileges", "governance ceiling", "the risk of every mod you install is yours"):
        assert word in " ".join(body.split()), word
    assert all(len(row) <= 80 for row in plain)
    assert len(frame.rows) == 30 and frame.max_scroll == 0 and not frame.more_below


def test_compose_scrolls_when_the_terminal_is_short():
    tall = consent_screen.compose(60, 40)
    short = consent_screen.compose(60, 10)
    assert short.max_scroll > 0 and short.more_below and not short.more_above
    assert len(short.rows) == 10 and strip_ansi(short.rows[-2]).strip() == consent_screen.CONTROL, "the control is always on screen"
    bottom = consent_screen.compose(60, 10, scroll=999)
    assert bottom.scroll == bottom.max_scroll and bottom.more_above and not bottom.more_below
    assert "more above" in strip_ansi(bottom.hint)
    full_text = [strip_ansi(r) for r in tall.body if r.strip()]
    seen = {strip_ansi(r) for r in short.body} | {strip_ansi(r) for r in bottom.body}
    assert full_text[0] in seen and full_text[-1] in seen


def test_compose_paints_the_control_when_styling_is_on():
    styled = consent_screen.compose(80, 30, style=Style(Depth.TRUECOLOR))
    assert "\x1b[7m" in styled.control and consent_screen.CONTROL in styled.control
    plain = consent_screen.compose(80, 30, style=Style.off())
    assert plain.control.strip() == consent_screen.CONTROL and "\x1b[" not in plain.control
    with_banner = consent_screen.compose(100, 60, banner_lines=["<art>"] * 19)
    assert strip_ansi(with_banner.body[0]) == "<art>" and consent_screen.TITLE in strip_ansi(with_banner.body[20])


def test_screen_is_unavailable_off_a_terminal():
    import io

    assert consent_screen.ConsentScreen(stdin=io.StringIO(), stdout=io.StringIO()).run() is None
    console = Console(out=io.StringIO(), err=io.StringIO())
    assert console.screen_available is False and console.ask_consent_screen() is None
    scripted = Console(input_fn=lambda _p: "x", non_interactive=False)
    assert scripted.screen_available is False, "a scripted console (tests, the Loader route) never gets a screen"
    hosted = Console(out=io.StringIO(), consent_fn=lambda: True)
    assert hosted.ask_consent_screen() is True, "a host (the interactive floofy) may supply its own screen"


# --- through a pseudo-terminal -------------------------------------------------------------------------------


@pytest.fixture
def host(tmp_path: Path):
    payload_root = tmp_path / "payload"
    fake_payload(payload_root, "0.7.0", build="0.7.0.5")
    home = tmp_path / "home"
    home.mkdir()
    data = DataHome.for_host_home(home)

    class Host:
        root = payload_root
        home_dir = home
        paths = data
        kiro = tmp_path / "kiro"

        def argv(self, *args: str) -> list[str]:
            return ["--home", str(home), "--root", str(payload_root), *args]

    return Host()


def _env(host, **extra: str) -> dict[str, str]:
    base = {k: v for k, v in os.environ.items() if k not in ("NO_COLOR", "FORCE_COLOR", "COLORTERM", "COLUMNS", "LINES")}
    return {**base, "FLOOFY_NO_ADAPTERS": "1", "KIRO_HOME": str(host.kiro), "PYTHONPATH": str(REPO_ROOT / "floofy-core"), "TERM": "xterm-256color", **extra}


def drive_pty(argv: list[str], env: dict[str, str], answers: list[tuple[str, str]], *, rows: int = 40, columns: int = 100, timeout: float = 60) -> tuple[int, str]:
    """Run the CLI on a pseudo-terminal; for each ``(wait_for, send)`` wait for the text, then type. Returns ``(exit, output)``."""
    controller, follower = pty.openpty()
    fcntl.ioctl(follower, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
    proc = subprocess.Popen([sys.executable, "-m", "floofy_core.cli", *argv], stdin=follower, stdout=follower, stderr=follower, env=env, close_fds=True)
    os.close(follower)
    transcript = b""
    deadline = time.monotonic() + timeout

    def pump(until: str | None) -> None:
        nonlocal transcript
        while time.monotonic() < deadline:
            if until is not None and until.encode("utf-8") in transcript:
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
        raise AssertionError(f"timed out waiting for {until!r}; got:\n{transcript.decode('utf-8', 'replace')}")

    for wait_for, send in answers:
        pump(wait_for)
        transcript = transcript.replace(wait_for.encode("utf-8"), b"<<seen>>", 1)
        os.write(controller, send.encode("utf-8"))
    pump(None)
    os.close(controller)
    return proc.wait(timeout=30), transcript.decode("utf-8", "replace")


def test_enter_on_the_control_records_screen_consent_and_restores_the_screen(host):
    code, out = drive_pty(host.argv("init", "--no-loader-app", "--no-trigger"), _env(host, COLORTERM="truecolor"), [(consent_screen.CONTROL, "\r")])
    assert code == 0, out
    enter, leave = out.index(ENTER), out.index(LEAVE)
    assert enter < out.index("<<seen>>") < leave, "the control was drawn on the alternate screen, which was left afterwards"
    assert consent_screen.TITLE in out and "the risk of every mod you install is yours" in " ".join(strip_ansi(out).split())
    assert "\x1b[38;2;" in out[enter:leave], "the banner is drawn on a tall truecolor terminal"
    assert "consent: acknowledged on the full-screen control" in strip_ansi(out)
    status = read_consent(host.paths.consent)
    assert status.ok and status.how == "screen" and status.by and status.warning_version == 1
    record = json.loads(host.paths.consent.read_text(encoding="utf-8"))
    assert set(record) == {"warningVersion", "acknowledgedAt", "by", "how"}, "the record shape is today's; only `how` says screen"
    rows = read_audit(host.paths.audit)
    assert rows[0]["op"] == "consent" and rows[0]["how"] == "screen" and "(screen)" in rows[0]["detail"]
    # a second init on the same terminal: consent exists, no screen
    again_code, again = drive_pty(host.argv("init", "--no-loader-app", "--no-trigger"), _env(host), [])
    assert again_code == 0 and ENTER not in again and "already recorded" in strip_ansi(again)


@pytest.mark.parametrize("key", ["\x1b", "q", "\x03"])
def test_escape_q_or_interrupt_declines_and_writes_nothing(host, key):
    code, out = drive_pty(host.argv("init", "--no-loader-app", "--no-trigger"), _env(host), [(consent_screen.CONTROL, key)])
    assert code == 3, out
    assert ENTER in out and LEAVE in out and out.index(ENTER) < out.index(LEAVE), "declined, and the screen was restored"
    assert "consent not given: declined on the full-screen control" in strip_ansi(out)
    assert not host.paths.consent.exists() and not host.paths.audit.exists()


def test_the_screen_scrolls_on_a_short_terminal(host):
    code, out = drive_pty(host.argv("init", "--no-loader-app", "--no-trigger"), _env(host, NO_COLOR="1"), [("more below", "\x1b[B"), ("more above", "\r")], rows=10, columns=60)
    assert code == 0, out
    assert read_consent(host.paths.consent).how == "screen"
    assert "\x1b[38;2;" not in out, "NO_COLOR: the screen is drawn without colour and without the banner"


def test_a_tiny_terminal_falls_back_to_the_typed_prompt(host):
    code, out = drive_pty(host.argv("init", "--no-loader-app", "--no-trigger"), _env(host), [(f"Type {ACCEPT_PHRASE}", ACCEPT_PHRASE + "\r")], rows=6, columns=100)
    assert code == 0, out
    assert ENTER not in out and read_consent(host.paths.consent).how == "typed"


def _typed_run(host, argv: list[str], typed: str) -> tuple[int, str, str]:
    """stdin a terminal, stdout a pipe: the screen cannot be drawn, so the typed prompt is used (fed ``typed``)."""
    controller, follower = pty.openpty()
    proc = subprocess.Popen([sys.executable, "-m", "floofy_core.cli", *argv], stdin=follower, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_env(host), close_fds=True, text=True)
    os.close(follower)
    time.sleep(0.5)
    os.write(controller, (typed + "\r").encode("utf-8"))
    out, err = proc.communicate(timeout=120)
    os.close(controller)
    return proc.returncode, out, err


def test_the_flag_and_the_typed_prompt_are_unchanged(host, tmp_path: Path):
    code, out = drive_pty(host.argv("init", "--i-accept-the-risk", "--no-loader-app", "--no-trigger"), _env(host), [])
    assert code == 0, out
    assert ENTER not in out and read_consent(host.paths.consent).how == "flag" and "acknowledged with --i-accept-the-risk" in strip_ansi(out)
    host.paths.consent.unlink()
    # stdout is not a terminal (piped to a file or a pager): the typed prompt, as before
    code_typed, out_typed, err_typed = _typed_run(host, host.argv("init", "--no-loader-app", "--no-trigger"), ACCEPT_PHRASE)
    assert code_typed == 0, err_typed
    assert ENTER not in out_typed and f"Type {ACCEPT_PHRASE}" in out_typed and WARNING_TEXT.splitlines()[0] in out_typed
    assert read_consent(host.paths.consent).how == "typed"
    host.paths.consent.unlink()
    code_wrong, _, _ = _typed_run(host, host.argv("init", "--no-loader-app", "--no-trigger"), "yes")
    assert code_wrong == 3 and not host.paths.consent.exists()
    # no terminal at all: refused, as before — a pipe cannot acknowledge the warning
    piped = subprocess.run([sys.executable, "-m", "floofy_core.cli", *host.argv("init", "--no-loader-app", "--no-trigger")], input=ACCEPT_PHRASE + "\n", capture_output=True, text=True, env=_env(host), timeout=120, check=False)
    assert piped.returncode == 3 and not host.paths.consent.exists() and "consent not given" in piped.stderr
    with_yes = subprocess.run([sys.executable, "-m", "floofy_core.cli", *host.argv("--yes", "init", "--no-loader-app", "--no-trigger")], stdin=subprocess.DEVNULL, capture_output=True, text=True, env=_env(host), timeout=120, check=False)
    assert with_yes.returncode == 3 and not host.paths.consent.exists(), "--yes never accepts the warning"


def test_governance_target_confirmation_stays_typed_on_the_same_terminal(host, tmp_path: Path):
    import hashlib

    write_consent(host.paths.ensure().consent, by="tests", how="test")
    mod = tmp_path / "govy"
    (mod / "agents").mkdir(parents=True)
    (mod / "agents" / "a.json").write_text(json.dumps({"name": "a"}), encoding="utf-8")
    (mod / "security_policy.json").write_text("{}", encoding="utf-8")
    files = [{"path": rel, "sha256": hashlib.sha256((mod / rel).read_bytes()).hexdigest()} for rel in ("agents/a.json", "security_policy.json")]
    (mod / "floofy.json").write_text(json.dumps({"schema": 1, "id": "govy", "name": "govy", "version": "1.0.0", "description": "d", "authors": ["t"], "license": "MIT", "kirocrew": {"version": ">=0.7.0 <0.9.0"}, "dependsOn": {"floofycrew": ">=0.0.0"}, "parts": [{"kind": "agent", "side": "gateway", "path": "agents/a.json"}], "files": files}), encoding="utf-8")
    # Enter (the consent screen's accept key) is not a confirmation here: an empty answer refuses
    code, out = drive_pty(host.argv("--yes", "install", str(mod)), _env(host), [("Type the exact path", "\r")])
    assert code == 1, out
    assert ENTER not in out, "no alternate screen, no button: the governance-altering target is a typed prompt"
    assert "GOVERNANCE-ALTERING" in out and "was not confirmed by typing its path" in strip_ansi(out)
    assert not (host.paths.mods / "govy").exists()
    code2, out2 = drive_pty(host.argv("--yes", "install", str(mod)), _env(host), [("Type the exact path", "security_policy.json\r")])
    assert code2 == 0, out2
    assert (host.paths.mods / "govy").is_dir()
    rows = read_audit(host.paths.audit)
    confirm = [r for r in rows if r["op"] == "governance-target-confirm"]
    assert confirm and confirm[-1]["detail"] == "typed at the prompt" and confirm[-1]["files"] == ["security_policy.json"]
