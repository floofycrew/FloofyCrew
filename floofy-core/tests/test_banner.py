"""The FloofyCrew terminal banner (task 10.2; Requirement 15.5).

The package data file is a byte-identical copy of ``branding/ascii/floofy.json``
(built by ``branding/build_ascii.py`` from ``branding/logo-nobg.svg``); the
renderer has a 24-bit mode, a 256-colour mode, a single-colour 16-colour mode and
an omitted mode (colours off, or a terminal narrower than the art plus margins).
``floofy --version`` and ``floofy doctor`` print it only on a terminal, as a left
column with their first lines beside it; ``--json`` never carries it.
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
WIDTH, HEIGHT = 34, 15
PACKAGE_DATA = REPO_ROOT / "floofy-core" / "floofy_core" / "cli" / "banner.json"
WORDMARK_DATA = REPO_ROOT / "floofy-core" / "floofy_core" / "cli" / "wordmark.json"


def test_package_data_is_a_byte_identical_copy_of_the_branding_asset():
    """Regenerate with ``python branding/build_ascii.py`` and copy the JSON here — never edit either by hand."""
    assert PACKAGE_DATA.read_bytes() == (BRANDING / "floofy.json").read_bytes(), "floofy_core/cli/banner.json must equal branding/ascii/floofy.json (source: branding/build_ascii.py)"
    document = json.loads(PACKAGE_DATA.read_text(encoding="utf-8"))
    assert document["name"] == "floofycrew-banner" and document["width"] == WIDTH and document["height"] == HEIGHT and document["source"] == "logo-nobg.svg"
    art = banner.load()
    assert art.width == WIDTH and art.height == HEIGHT and len(art.rows) == HEIGHT and all(len(row) == WIDTH for row in art.rows)
    assert "\n".join(art.text_rows) + "\n" == (BRANDING / "floofy.txt").read_text(encoding="utf-8"), "the glyphs are floofy.txt"
    inked = {rgb for row in art.rows for ch, rgb in row if ch != " "}
    assert inked == {(0xFF, 0xE4, 0xCF), (0xFF, 0xBF, 0xAE), (0xFF, 0xFF, 0xFF), (0x8A, 0x3B, 0x4C)}, "peach fluff, inner ear, white tail tip, lifted-ink eyes — the sunset palette, flat"
    assert any(ch == "." and rgb == (0x8A, 0x3B, 0x4C) for row in art.rows for ch, rgb in row), "the eyes are drawn"


def test_wordmark_package_data_is_a_byte_identical_copy_and_sits_beside_the_fox():
    """The interactive banner draws "FloofyCrew" right of the fox, centred on its height (wordmark-text.svg rasterised)."""
    assert WORDMARK_DATA.read_bytes() == (BRANDING / "wordmark.json").read_bytes(), "floofy_core/cli/wordmark.json must equal branding/ascii/wordmark.json (source: branding/build_ascii.py)"
    mark = banner.load_wordmark()
    assert mark.name == "floofycrew-wordmark" and mark.source == "wordmark-text.svg" and mark.height == 6 and mark.width <= 71, "six rows, at most 71 columns: fox + gap + wordmark fit a 110-column terminal"
    assert "\n".join(mark.text_rows) + "\n" == (BRANDING / "wordmark.txt").read_text(encoding="utf-8")
    colours = {rgb for row in mark.rows for ch, rgb in row if ch != " "}
    assert banner.WORDMARK_NAME_RGB in colours, '"Floofy" is the peach of wordmark-dark.svg'
    assert len(colours) > 20 and all(r == 255 and 90 <= g <= 240 for r, g, _ in colours), '"Crew" carries the pink→orange gradient: every colour is a sunset tint'
    art = banner.load()
    assert banner.wordmark_top(art, mark) == (art.height - mark.height) // 2 == 4, "centred on the fox's 15 rows"
    needed = banner.wordmark_columns(art, mark)
    assert needed == banner.MARGIN // 2 + art.width + banner.WORDMARK_GAP + mark.width <= 110, "the wordmark may end on the last column; 110 columns is enough"
    assert banner.wordmark_fits(needed) and not banner.wordmark_fits(needed - 1)


def test_truecolor_rendering_is_the_ans_export_with_the_margin():
    lines = banner.render_lines(Depth.TRUECOLOR, width=200)
    assert lines is not None and len(lines) == HEIGHT
    expected = (BRANDING / "floofy.ans").read_text(encoding="utf-8").splitlines()
    assert [line[banner.MARGIN // 2 :] for line in lines] == expected, "24-bit mode emits exactly branding/ascii/floofy.ans (per-cell 38;2;r;g;b runs)"
    assert all(line.startswith("  ") and "\x1b[38;2;" in line and line.endswith("\x1b[0m") for line in lines)
    assert not any("\x1b[38;2;0;0;0m" in line for line in lines), "transparent cells carry no colour sequence"
    text = banner.render(Depth.TRUECOLOR, width=200)
    assert text is not None and text.endswith("\n") and strip_ansi(text).splitlines() == ["  " + row for row in banner.load().text_rows]


def test_256_colour_rendering_quantises_to_the_cube():
    lines = banner.render_lines(Depth.EXTENDED, width=80)
    assert lines is not None and len(lines) == HEIGHT
    assert all("38;5;" in line and "38;2;" not in line for line in lines)
    first_row = banner.load().rows[0]
    first_cell = next(cell for cell in first_row if cell[0] != " ")
    blanks = " " * next(i for i, cell in enumerate(first_row) if cell[0] != " ")
    assert lines[0].startswith(f"  {blanks}\x1b[38;5;{cube_index(*first_cell[1])}m{first_cell[0]}")
    assert strip_ansi("\n".join(lines)).splitlines() == ["  " + row for row in banner.load().text_rows]


def test_16_colour_rendering_is_a_single_colour():
    lines = banner.render_lines(Depth.BASIC, width=80)
    assert lines is not None and len(lines) == HEIGHT
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
    monkeypatch.setenv("COLUMNS", str(WIDTH + banner.MARGIN - 1))
    monkeypatch.setenv("LINES", "40")
    assert not banner.fits()
    monkeypatch.setenv("COLUMNS", str(WIDTH + banner.MARGIN))
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
    ans = (BRANDING / "floofy.ans").read_text(encoding="utf-8").splitlines()
    assert len(lines) == HEIGHT, "one terminal line per art row; the version line shares the first"
    assert lines[0].startswith("  " + ans[0] + "  ") and strip_ansi(lines[0][2 + len(ans[0]) + 2 :]).startswith("floofy ") and "unofficial" in lines[0], "the version line sits on the art's top row"
    assert [line[2:] for line in lines[1:]] == ans[1:], "the remaining rows follow, art only"
    code16, out16 = _run_on_pty(["--version"], env={"TERM": "xterm"}, columns=100)
    lines16 = out16.decode("utf-8").splitlines()
    prefix16 = f"  \x1b[95m{art.text_rows[0]}\x1b[0m  "
    assert code16 == 0 and lines16[0].startswith(prefix16) and strip_ansi(lines16[0][len(prefix16) :]).startswith("floofy "), "single colour on 16 colours, version beside the top row"
    assert lines16[1:] == [f"  \x1b[95m{row}\x1b[0m" for row in art.text_rows[1:]]
    code_narrow, narrow = _run_on_pty(["--version"], env=truecolor, columns=WIDTH + banner.MARGIN - 1)
    assert code_narrow == 0 and strip_ansi(narrow.decode("utf-8")).startswith("floofy "), f"narrower than {WIDTH} + {banner.MARGIN}: omitted"
    code_off, off = _run_on_pty(["--version"], env={**truecolor, "NO_COLOR": "1"}, columns=100)
    assert code_off == 0 and off.decode("utf-8").startswith("floofy ") and b"\x1b[" not in off, "colours off: omitted"
    code_json, as_json = _run_on_pty(["--home", str(tmp_path / "h"), "--json", "--version"], env=truecolor, columns=100)
    assert code_json == 0 and json.loads(as_json.decode("utf-8"))["unofficial"] is True, "--json on a wide terminal: only the document"
    code_doc, doctor = _run_on_pty(["--home", str(tmp_path / "h"), "doctor"], env=truecolor, columns=100)
    doctor_lines = doctor.decode("utf-8").splitlines()
    assert code_doc == 0
    assert all(line.startswith("  " + ans[i]) for i, line in enumerate(doctor_lines[:HEIGHT])), "the art is the left column of the header block (rows past the block print alone)"
    assert strip_ansi(doctor_lines[0][2 + len(ans[0]) + 2 :]).startswith("floofy doctor"), "the doctor header sits on the art's top row"
    assert strip_ansi(doctor_lines[1][2 + len(ans[1]) + 2 :]).startswith("host:")
    plain = [strip_ansi(line) for line in doctor_lines]
    assert any(line.startswith("consent:") for line in plain), "sections after the header block print flush-left, below the art"
    assert not any("#" in strip_ansi(line)[2 + len(strip_ansi(ans[0])) :] for line in doctor_lines[:6]), "at 100 columns the block wordmark does not fit and is not drawn"
    # wide enough for the block wordmark: it takes the art's top rows, the header follows right under it
    mark = banner.load_wordmark()
    wide = banner.wordmark_columns()
    code_wide, wide_out = _run_on_pty(["--home", str(tmp_path / "h"), "doctor"], env=truecolor, columns=wide)
    wide_lines = wide_out.decode("utf-8").splitlines()
    mark_ans = (BRANDING / "wordmark.ans").read_text(encoding="utf-8").splitlines()
    assert code_wide == 0
    for i in range(mark.height):
        assert wide_lines[i] == "  " + ans[i] + " " * banner.WORDMARK_GAP + mark_ans[i], f"row {i}: fox, gap, wordmark"
    assert wide_lines[mark.height].startswith("  " + ans[mark.height] + "  ") and strip_ansi(wide_lines[mark.height][2 + len(ans[mark.height]) + 2 :]).startswith("floofy doctor"), "the header sits right under the wordmark"
    code_v, version_wide = _run_on_pty(["--version"], env=truecolor, columns=wide)
    v_lines = version_wide.decode("utf-8").splitlines()
    assert code_v == 0 and v_lines[0].endswith(mark_ans[0]) and strip_ansi(v_lines[mark.height][2 + len(ans[mark.height]) + 2 :]).startswith("floofy "), "--version: wordmark first, then the version line beside the art"
