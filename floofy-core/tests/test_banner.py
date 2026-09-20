"""The FloofyCrew terminal banner (task 10.2; Requirement 15.5).

The package data file is a byte-identical copy of ``branding/ascii/floofy.json``
(built by ``branding/build_ascii.py``); the renderer has a 24-bit mode, a
256-colour mode, a single-colour 16-colour mode and an omitted mode (colours off,
or a terminal narrower than the art plus margins). ``floofy --version`` and
``floofy doctor`` print it only on a terminal; ``--json`` never carries it.
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
from pathlib import Path

import pytest

from floofy_core.cli import banner
from floofy_core.cli.console import Console
from floofy_core.cli.main import execute, run
from floofy_core.cli.style import Depth, Style, cube_index, strip_ansi

from floofy_testing import REPO_ROOT

BRANDING = REPO_ROOT / "branding" / "ascii"
PACKAGE_DATA = REPO_ROOT / "floofy-core" / "floofy_core" / "cli" / "banner.json"


def test_package_data_is_a_byte_identical_copy_of_the_branding_asset():
    """Regenerate with ``python branding/build_ascii.py`` and copy the JSON here — never edit either by hand."""
    assert PACKAGE_DATA.read_bytes() == (BRANDING / "floofy.json").read_bytes(), "floofy_core/cli/banner.json must equal branding/ascii/floofy.json (source: branding/build_ascii.py)"
    document = json.loads(PACKAGE_DATA.read_text(encoding="utf-8"))
    assert document["name"] == "floofycrew-banner" and document["width"] == 38 and document["height"] == 19
    art = banner.load()
    assert art.width == 38 and art.height == 19 and len(art.rows) == 19 and all(len(row) == 38 for row in art.rows)
    assert "\n".join(art.text_rows) + "\n" == (BRANDING / "floofy.txt").read_text(encoding="utf-8"), "the glyphs are floofy.txt"
    assert len(art.colours) > 50, "the art is a gradient, not a flat fill"


def test_truecolor_rendering_is_the_ans_export_with_the_margin():
    lines = banner.render_lines(Depth.TRUECOLOR, width=200)
    assert lines is not None and len(lines) == 19
    expected = (BRANDING / "floofy.ans").read_text(encoding="utf-8").splitlines()
    assert [line[banner.MARGIN // 2 :] for line in lines] == expected, "24-bit mode emits exactly branding/ascii/floofy.ans (per-cell 38;2;r;g;b runs)"
    assert all(line.startswith("  \x1b[38;2;") and line.endswith("\x1b[0m") for line in lines)
    text = banner.render(Depth.TRUECOLOR, width=200)
    assert text is not None and text.endswith("\n") and strip_ansi(text).splitlines() == ["  " + row for row in banner.load().text_rows]


def test_256_colour_rendering_quantises_to_the_cube():
    lines = banner.render_lines(Depth.EXTENDED, width=80)
    assert lines is not None and len(lines) == 19
    assert all("38;5;" in line and "38;2;" not in line for line in lines)
    first_cell = banner.load().rows[0][0]
    assert lines[0].startswith(f"  \x1b[38;5;{cube_index(*first_cell[1])}m{first_cell[0]}")
    assert strip_ansi("\n".join(lines)).splitlines() == ["  " + row for row in banner.load().text_rows]


def test_16_colour_rendering_is_a_single_colour():
    lines = banner.render_lines(Depth.BASIC, width=80)
    assert lines is not None and len(lines) == 19
    for line, text in zip(lines, banner.load().text_rows):
        assert line == f"  \x1b[{banner.BASIC_COLOR}m{text}\x1b[0m", "one bright-magenta sequence per row, no per-cell colour"
    assert banner.BASIC_COLOR == "95"


@pytest.mark.parametrize("depth", [Depth.BASIC, Depth.EXTENDED, Depth.TRUECOLOR])
def test_omitted_when_narrow_and_present_at_exactly_the_art_plus_margins(depth):
    art = banner.load()
    assert banner.render(depth, width=art.width + banner.MARGIN - 1) is None
    assert banner.render(depth, width=art.width + banner.MARGIN) is not None
    assert banner.fits(art.width + banner.MARGIN) and not banner.fits(art.width)


def test_omitted_when_colours_are_off():
    assert banner.render(Depth.OFF, width=500) is None and banner.render_lines(Depth.OFF) is None


def test_fits_reads_the_terminal_size_when_no_width_is_given(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COLUMNS", "41")
    monkeypatch.setenv("LINES", "40")
    assert not banner.fits()
    monkeypatch.setenv("COLUMNS", "42")
    assert banner.fits() and banner.render(Depth.BASIC) is not None


def test_console_prints_the_banner_only_on_a_terminal(tmp_path: Path):
    import io

    piped = Console(out=io.StringIO(), style=Style(Depth.TRUECOLOR, forced=True))
    assert piped.show_banner(width=100) is False and piped.out.getvalue() == "", "FORCE_COLOR on a pipe paints text, but the banner is terminal decoration"
    plain = Console(out=io.StringIO(), style=Style.off())
    assert plain.show_banner(width=100) is False

    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    terminal = Console(out=Tty(), style=Style(Depth.BASIC))
    assert terminal.show_banner(width=100) is True
    assert terminal.out.getvalue() == banner.render(Depth.BASIC, width=100) and terminal.transcript == []
    quiet = Console(out=Tty(), style=Style(Depth.BASIC), quiet=True)
    assert quiet.show_banner(width=100) is False
    narrow = Console(out=Tty(), style=Style(Depth.BASIC))
    assert narrow.show_banner(width=30) is False


def test_json_and_in_process_runs_never_carry_the_banner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FLOOFY_NO_ADAPTERS", "1")
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("COLORTERM", "truecolor")
    result = run(["--home", str(tmp_path / "h"), "--version"], non_interactive=True, actor="test")
    assert result.exit == 0 and "\x1b[" not in result.stdout and result.stdout.startswith("floofy ")
    import io

    console = Console(out=io.StringIO(), err=io.StringIO())
    code, document = execute(["--home", str(tmp_path / "h"), "--json", "doctor"], console)
    assert code == 0 and console.out.getvalue() == "" and document["unofficial"] is True


def _run_on_pty(argv: list[str], *, env: dict[str, str], columns: int) -> tuple[int, bytes]:
    base = {k: v for k, v in os.environ.items() if k not in ("NO_COLOR", "FORCE_COLOR", "COLORTERM", "COLUMNS", "LINES")}
    full_env = {**base, "FLOOFY_NO_ADAPTERS": "1", "PYTHONPATH": str(REPO_ROOT / "floofy-core"), **env}
    controller, follower = pty.openpty()
    attrs = termios.tcgetattr(follower)
    attrs[1] &= ~termios.ONLCR
    termios.tcsetattr(follower, termios.TCSANOW, attrs)
    fcntl.ioctl(follower, termios.TIOCSWINSZ, struct.pack("HHHH", 50, columns, 0, 0))
    proc = subprocess.Popen([sys.executable, "-m", "floofy_core.cli", *argv], stdin=follower, stdout=follower, stderr=subprocess.DEVNULL, env=full_env, close_fds=True)
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
    return proc.wait(timeout=60), b"".join(chunks)


def test_version_on_a_terminal_prints_the_banner_at_the_terminals_depth(tmp_path: Path):
    truecolor = {"COLORTERM": "truecolor", "TERM": "xterm-256color"}
    code, out = _run_on_pty(["--version"], env=truecolor, columns=100)
    assert code == 0
    text = out.decode("utf-8")
    lines = text.splitlines()
    art = banner.load()
    assert [line[2:] for line in lines[:19]] == (BRANDING / "floofy.ans").read_text(encoding="utf-8").splitlines(), "24-bit banner first"
    assert strip_ansi(lines[19]).startswith("floofy ") and "unofficial" in lines[19]
    code16, out16 = _run_on_pty(["--version"], env={"TERM": "xterm"}, columns=100)
    lines16 = out16.decode("utf-8").splitlines()
    assert code16 == 0 and lines16[:19] == [f"  \x1b[95m{row}\x1b[0m" for row in art.text_rows], "single colour on 16 colours"
    code_narrow, narrow = _run_on_pty(["--version"], env=truecolor, columns=41)
    assert code_narrow == 0 and strip_ansi(narrow.decode("utf-8")).startswith("floofy "), "narrower than 38 + 4: omitted"
    code_off, off = _run_on_pty(["--version"], env={**truecolor, "NO_COLOR": "1"}, columns=100)
    assert code_off == 0 and off.decode("utf-8").startswith("floofy ") and b"\x1b[" not in off, "colours off: omitted"
    code_json, as_json = _run_on_pty(["--home", str(tmp_path / "h"), "--json", "--version"], env=truecolor, columns=100)
    assert code_json == 0 and json.loads(as_json.decode("utf-8"))["unofficial"] is True, "--json on a wide terminal: only the document"
    code_doc, doctor = _run_on_pty(["--home", str(tmp_path / "h"), "doctor"], env=truecolor, columns=100)
    doctor_lines = doctor.decode("utf-8").splitlines()
    assert code_doc == 0 and [line[2:] for line in doctor_lines[:19]] == (BRANDING / "floofy.ans").read_text(encoding="utf-8").splitlines()
    assert strip_ansi(doctor_lines[19]).startswith("floofy doctor")
