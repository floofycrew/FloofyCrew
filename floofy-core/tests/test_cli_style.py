"""The CLI's style layer (task 10.1; Requirement 15.1).

Depth detection from ``COLORTERM``/``TERM``, every off switch (non-terminal,
``NO_COLOR``, ``--no-color``, ``TERM=dumb``, ``--json``), the semantic palette and
the weights, and the guarantee that matters to automation: ``--json`` output is
byte-identical with and without styling — on a non-terminal, with ``FORCE_COLOR``,
and on a real pseudo-terminal. Scratch homes and fake payloads only.
"""
from __future__ import annotations

import fcntl
import io
import json
import os
import pty
import select
import struct
import subprocess
import sys
import termios
from pathlib import Path

import pytest

from floofy_core.cli.console import Console
from floofy_core.cli.main import execute, run
from floofy_core.cli.style import PALETTE, WEIGHTS, Depth, Style, cube_index, depth_from_terminal, strip_ansi
from floofy_core.consent import write_consent
from floofy_core.datahome import DataHome

from floofy_testing import REPO_ROOT, fake_payload

TRUECOLOR = {"COLORTERM": "truecolor", "TERM": "xterm-256color"}


# --- detection --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "is_tty", "no_color", "expected"),
    [
        ({"COLORTERM": "truecolor", "TERM": "xterm"}, True, False, Depth.TRUECOLOR),
        ({"COLORTERM": "24bit", "TERM": "xterm"}, True, False, Depth.TRUECOLOR),
        ({"TERM": "xterm-256color"}, True, False, Depth.EXTENDED),
        ({"TERM": "xterm-direct"}, True, False, Depth.EXTENDED),
        ({"TERM": "xterm"}, True, False, Depth.BASIC),
        ({}, True, False, Depth.BASIC),
        ({"COLORTERM": "truecolor"}, False, False, Depth.OFF),  # not a terminal
        ({"COLORTERM": "truecolor", "NO_COLOR": "1"}, True, False, Depth.OFF),
        ({"COLORTERM": "truecolor", "NO_COLOR": ""}, True, False, Depth.OFF),  # any value, even empty
        ({"COLORTERM": "truecolor", "TERM": "dumb"}, True, False, Depth.OFF),
        ({"COLORTERM": "truecolor"}, True, True, Depth.OFF),  # --no-color
        ({"COLORTERM": "truecolor", "FORCE_COLOR": "1"}, False, False, Depth.TRUECOLOR),  # forced on a pipe
        ({"TERM": "xterm-256color", "FORCE_COLOR": "1"}, False, False, Depth.EXTENDED),
        ({"FORCE_COLOR": "0", "TERM": "xterm"}, False, False, Depth.OFF),
        ({"FORCE_COLOR": "1", "NO_COLOR": "1"}, False, False, Depth.OFF),  # NO_COLOR wins
        ({"FORCE_COLOR": "1", "TERM": "dumb"}, False, False, Depth.OFF),
        ({"FORCE_COLOR": "1"}, True, True, Depth.OFF),  # --no-color wins
    ],
)
def test_depth_detection_follows_the_switches(env, is_tty, no_color, expected):
    style = Style.detect(env=env, is_tty=is_tty, no_color=no_color)
    assert style.depth == expected
    assert style.on == (expected != Depth.OFF)
    assert style.forced == (expected != Depth.OFF and not is_tty)


def test_depth_from_terminal_and_the_colour_cube():
    assert depth_from_terminal({"COLORTERM": "TrueColor"}) == Depth.TRUECOLOR
    assert depth_from_terminal({"TERM": "screen-256color"}) == Depth.EXTENDED
    assert depth_from_terminal({"TERM": "linux"}) == Depth.BASIC
    assert cube_index(0, 0, 0) == 16 and cube_index(255, 255, 255) == 231
    assert cube_index(255, 0, 0) == 196 and cube_index(0, 255, 0) == 46 and cube_index(0, 0, 255) == 21


# --- palette -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("tone", [*PALETTE, *WEIGHTS])
def test_every_tone_paints_without_changing_the_text(tone):
    assert Style.off().paint("hello", tone) == "hello", "off: the text passes through untouched"
    for depth in (Depth.BASIC, Depth.EXTENDED, Depth.TRUECOLOR):
        painted = Style(depth).paint("hello", tone)
        assert painted.startswith("\x1b[") and painted.endswith("hello\x1b[0m")
        assert strip_ansi(painted) == "hello"
    assert Style(Depth.TRUECOLOR).paint("", tone) == "", "nothing to paint, nothing emitted"


def test_palette_depth_columns_and_named_painters():
    assert Style(Depth.TRUECOLOR).heading("x") == "\x1b[1;38;2;255;154;61mx\x1b[0m"
    assert Style(Depth.EXTENDED).warn("x") == "\x1b[38;5;214mx\x1b[0m"
    assert Style(Depth.BASIC).danger("x") == "\x1b[1;91mx\x1b[0m"
    assert Style(Depth.BASIC).muted("x") == "\x1b[2mx\x1b[0m"
    assert Style(Depth.BASIC).accent("x") == "\x1b[95mx\x1b[0m"
    assert Style(Depth.BASIC).ok("x") == "\x1b[32mx\x1b[0m"
    assert Style(Depth.BASIC).bold("x") == "\x1b[1mx\x1b[0m" and Style(Depth.BASIC).dim("x") == "\x1b[2mx\x1b[0m" and Style(Depth.BASIC).italic("x") == "\x1b[3mx\x1b[0m"
    assert Style(Depth.TRUECOLOR).rgb("x", 1, 2, 3) == "\x1b[38;2;1;2;3mx\x1b[0m"
    assert Style(Depth.EXTENDED).rgb("x", 255, 255, 255) == "\x1b[38;5;231mx\x1b[0m"
    assert Style(Depth.BASIC).rgb("x", 255, 0, 0) == "\x1b[95mx\x1b[0m"
    with pytest.raises(KeyError):
        Style(Depth.BASIC).paint("x", "purple")
    assert set(PALETTE) == {"heading", "ok", "warn", "danger", "muted", "accent"}


def test_console_paints_out_but_keeps_the_transcript_plain():
    out, err = io.StringIO(), io.StringIO()
    console = Console(out=out, err=err, style=Style(Depth.TRUECOLOR, forced=True))
    console.say("plain")
    console.say("bad", tone="danger")
    console.say(console.paint("frag", "ok") + " tail")
    console.heading("head")
    console.warn("careful")
    console.error("broken")
    assert console.transcript == ["plain", "bad", "frag tail", "head", "WARNING careful", "ERROR broken"]
    assert out.getvalue().splitlines() == ["plain", "\x1b[1;38;2;255;95;115mbad\x1b[0m", "\x1b[38;2;80;200;120mfrag\x1b[0m tail", "\x1b[1;38;2;255;154;61mhead\x1b[0m"]
    assert err.getvalue().splitlines() == ["\x1b[38;2;255;179;71mWARNING\x1b[0m careful", "\x1b[1;38;2;255;95;115mERROR\x1b[0m broken"]
    # an unstyled console: identical words, no sequences
    plain = Console(out=io.StringIO(), err=io.StringIO())
    plain.say("bad", tone="danger")
    plain.warn("careful")
    assert plain.out.getvalue() == "bad\n" and plain.err.getvalue() == "WARNING careful\n"


def test_err_is_painted_only_when_it_is_a_terminal_or_forced():
    console = Console(out=io.StringIO(), err=io.StringIO(), style=Style(Depth.BASIC))
    console.warn("w")
    assert console.err.getvalue() == "WARNING w\n", "stdout is a terminal, stderr is a file: the file stays plain"
    forced = Console(out=io.StringIO(), err=io.StringIO(), style=Style(Depth.BASIC, forced=True))
    forced.warn("w")
    assert forced.err.getvalue() == "\x1b[33mWARNING\x1b[0m w\n"


# --- the CLI ------------------------------------------------------------------------------------------


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FLOOFY_NO_ADAPTERS", "1")
    monkeypatch.delenv("KIROCREW_HOME", raising=False)
    monkeypatch.setenv("KIRO_HOME", str(tmp_path / "kiro"))
    for name in ("NO_COLOR", "FORCE_COLOR", "COLORTERM"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    payload_root = tmp_path / "payload"
    fake_payload(payload_root, "0.7.0", build="0.7.0.5")
    home = tmp_path / "home"
    data = DataHome.for_host_home(home).ensure()
    write_consent(data.consent, by="tests", how="test")
    (data.mods / "alpha").mkdir()
    (data.mods / "alpha" / "floofy.json").write_text(json.dumps({"schema": 1, "id": "alpha", "name": "A", "version": "1.0.0", "description": "d", "authors": ["a"], "license": "MIT", "kirocrew": {"version": ">=0.7.0 <0.9.0"}, "dependsOn": {"floofycrew": ">=0.0.0"}, "parts": [{"kind": "theme", "side": "gateway", "path": "theme/theme.json"}], "files": []}), encoding="utf-8")

    class Host:
        root = payload_root
        home_dir = home
        paths = data

        def argv(self, *args: str) -> list[str]:
            return ["--home", str(home), "--root", str(payload_root), *args]

    return Host()


def _styled_execute(argv: list[str], *, env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> tuple[int, str, str, Console]:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    console = Console(out=io.StringIO(), err=io.StringIO())
    code, _ = execute(argv, console)
    return code, console.out.getvalue(), console.err.getvalue(), console


@pytest.mark.parametrize("command", [["doctor"], ["status"], ["list"], ["--version"]])
def test_human_output_says_the_same_with_and_without_colour(host, monkeypatch: pytest.MonkeyPatch, command):
    plain_code, plain_out, plain_err, _ = _styled_execute(host.argv(*command), env={"NO_COLOR": "1"}, monkeypatch=monkeypatch)
    monkeypatch.delenv("NO_COLOR")
    code, out, err, console = _styled_execute(host.argv(*command), env={"FORCE_COLOR": "1", **TRUECOLOR}, monkeypatch=monkeypatch)
    assert (code, strip_ansi(out), strip_ansi(err)) == (plain_code, plain_out, plain_err)
    assert console.style is not None and console.style.depth == Depth.TRUECOLOR and console.style.forced
    assert "\x1b[" in out, "the human output of a styled run is actually styled"
    assert "\x1b[" not in plain_out and "\x1b[" not in plain_err
    assert all("\x1b[" not in line for line in console.transcript), "the transcript is plain text"


def test_no_color_flag_and_json_turn_styling_off_even_when_forced(host, monkeypatch: pytest.MonkeyPatch):
    _, out, _, console = _styled_execute(host.argv("--no-color", "doctor"), env={"FORCE_COLOR": "1", **TRUECOLOR}, monkeypatch=monkeypatch)
    assert console.style == Style.off() and "\x1b[" not in out and "floofy doctor" in out
    _, out_json, _, console_json = _styled_execute(host.argv("--json", "doctor"), env={"FORCE_COLOR": "1", **TRUECOLOR}, monkeypatch=monkeypatch)
    assert console_json.style == Style.off() and out_json == "", "--json: no prose at all, styled or not"
    dumb = Style.detect(env={"TERM": "dumb", "FORCE_COLOR": "1"}, is_tty=True)
    assert dumb.depth == Depth.OFF


def test_run_in_process_is_never_styled(host, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("COLORTERM", "truecolor")
    result = run(host.argv("doctor"), non_interactive=True, actor="test")
    assert result.exit == 0 and "\x1b[" not in result.stdout and "\x1b[" not in result.stderr
    assert "floofy doctor" in result.stdout


def _cli_bytes(argv: list[str], *, env: dict[str, str], on_pty: bool = False, columns: int = 30) -> tuple[int, bytes, bytes]:
    """Run the CLI as a subprocess (the way a user or a script does) and return raw stdout/stderr bytes.

    The pseudo-terminal is ``columns`` wide (30 by default: narrower than the banner
    plus its margins, so the decoration stays out of these comparisons — the banner
    has its own tests).
    """
    base = {k: v for k, v in os.environ.items() if k not in ("NO_COLOR", "FORCE_COLOR", "COLORTERM", "COLUMNS", "LINES")}
    full_env = {**base, "FLOOFY_NO_ADAPTERS": "1", "PYTHONPATH": str(REPO_ROOT / "floofy-core"), "TERM": "xterm-256color", **env}
    cmd = [sys.executable, "-m", "floofy_core.cli", *argv]
    if not on_pty:
        completed = subprocess.run(cmd, capture_output=True, env=full_env, timeout=120, check=False)
        return completed.returncode, completed.stdout, completed.stderr
    controller, follower = pty.openpty()
    attrs = termios.tcgetattr(follower)
    attrs[1] &= ~termios.ONLCR  # no LF -> CRLF translation, so the bytes compare as written
    termios.tcsetattr(follower, termios.TCSANOW, attrs)
    fcntl.ioctl(follower, termios.TIOCSWINSZ, struct.pack("HHHH", 40, columns, 0, 0))
    proc = subprocess.Popen(cmd, stdin=follower, stdout=follower, stderr=subprocess.PIPE, env=full_env, close_fds=True)
    os.close(follower)
    chunks: list[bytes] = []
    while True:
        ready, _, _ = select.select([controller], [], [], 120)
        if not ready:
            proc.kill()
            raise AssertionError("no output within the timeout")
        try:
            chunk = os.read(controller, 65536)
        except OSError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    os.close(controller)
    stderr = proc.stderr.read() if proc.stderr else b""
    code = proc.wait(timeout=60)
    return code, b"".join(chunks), stderr


@pytest.mark.parametrize("command", [["doctor"], ["status"], ["list"], ["--version"], ["audit"]])
def test_json_output_is_byte_identical_on_a_pipe_when_forced_and_on_a_pty(host, command):
    argv = host.argv("--json", *command)
    plain = _cli_bytes(argv, env={"NO_COLOR": "1"})
    forced = _cli_bytes(argv, env={"FORCE_COLOR": "1", **TRUECOLOR})
    on_pty = _cli_bytes(argv, env={"FORCE_COLOR": "1", **TRUECOLOR}, on_pty=True)
    assert plain[0] == forced[0] == on_pty[0] == 0, (plain[2], forced[2], on_pty[2])
    assert plain[1] == forced[1], "FORCE_COLOR on a pipe: the JSON bytes are untouched"
    assert plain[1] == on_pty[1], "a real terminal: the JSON bytes are untouched"
    assert plain[2] == forced[2], "stderr of a --json run is not styled either"
    document = json.loads(plain[1])
    assert document.get("unofficial") is True and b"\x1b[" not in plain[1]


def test_a_pty_run_without_json_is_styled_and_says_the_same_words(host):
    argv = host.argv("doctor")
    code, styled, _ = _cli_bytes(argv, env=TRUECOLOR, on_pty=True)
    plain_code, plain, _ = _cli_bytes(argv, env={"NO_COLOR": "1"})
    assert code == plain_code == 0
    assert b"\x1b[1;38;2;255;154;61mfloofy doctor\x1b[0m" in styled, "24-bit heading on a truecolor terminal"
    assert strip_ansi(styled.decode("utf-8")) == plain.decode("utf-8")
    code16, basic, _ = _cli_bytes(argv, env={"TERM": "xterm"}, on_pty=True)
    assert code16 == 0 and b"\x1b[1;33mfloofy doctor\x1b[0m" in basic, "16 colours when the terminal advertises nothing more"
    code_no, no_color, _ = _cli_bytes([*argv], env={"NO_COLOR": "1", **TRUECOLOR}, on_pty=True)
    assert code_no == 0 and b"\x1b[" not in no_color, "NO_COLOR on a terminal: plain"
    code_flag, flagged, _ = _cli_bytes(host.argv("--no-color", "doctor"), env=TRUECOLOR, on_pty=True)
    assert code_flag == 0 and b"\x1b[" not in flagged, "--no-color on a terminal: plain"
