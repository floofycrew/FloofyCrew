"""The restyled install scripts of both editions (task 10.3; Requirement 15.2).

Both scripts are POSIX ``sh`` with the same structure: five numbered steps with one
status glyph each (``✓``/``✗``/``·``, ASCII ``+``/``x``/``-`` when the locale is not
UTF-8), the CLI's palette (off when stdout is not a terminal, under ``NO_COLOR``, on
``TERM=dumb`` and with ``--no-color``), the banner (24-bit with ``COLORTERM``, one
colour otherwise, skipped when colours are off, with ``--no-banner`` or on a narrow
terminal), numbered menus with a default for the two enumerated answers (the
re-apply trigger, running ``floofy init`` now) and a final summary naming the next
command. A run without a terminal takes and prints the defaults and never blocks;
every flag and environment variable of the previous scripts still works.

The packages are built into temporary directories exactly as the release builds do;
runs use a scratch ``HOME`` and prefix, ``setsid`` (no controlling terminal, so
``/dev/tty`` is unusable) and ``</dev/null``; the terminal run drives a
pseudo-terminal. Nothing touches a live install or the network.
"""
from __future__ import annotations

import fcntl
import importlib.util
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

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC = REPO_ROOT / "packaging" / "public"
INTERNAL = REPO_ROOT / "packaging" / "internal"
BRANDING = REPO_ROOT / "branding" / "ascii"
_SGR = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _SGR.sub("", text)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module  # registered before it runs: dataclasses resolve annotations through sys.modules (task 10.9)
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


public_build = _load("install_tests_public_build", PUBLIC / "build.py")
internal_build = _load("install_tests_internal_build", INTERNAL / "build.py")


@pytest.fixture(scope="module")
def public_release(tmp_path_factory: pytest.TempPathFactory) -> dict:
    out = tmp_path_factory.mktemp("public") / "public"
    result = public_build.build_release(out, supports={"external": {"stable": ["0.6.0"]}}, tag="v-test")
    result["root"] = out
    return result


@pytest.fixture(scope="module")
def internal_package(tmp_path_factory: pytest.TempPathFactory) -> dict:
    out = tmp_path_factory.mktemp("internal") / internal_build.PACKAGE_NAME
    result = internal_build.build_package(out, supports={"internal": {"beta": ["0.7.0.5"]}})
    result["root"] = out
    return result


def _env(home: Path, **extra: str) -> dict[str, str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith("FLOOFYCREW_") and k not in ("NO_COLOR", "FORCE_COLOR", "COLORTERM", "COLUMNS", "LINES", "FLOOFY_PYTHON", "LC_ALL", "LANG", "LC_CTYPE")}
    return {**base, "HOME": str(home), "TERM": "xterm-256color", "LC_ALL": "C.UTF-8", **extra}


def _public_env(release: dict, home: Path, **extra: str) -> dict[str, str]:
    defaults = {"FLOOFYCREW_ASSET_DIR": str(release["root"] / "dist"), "FLOOFYCREW_VERSION": "v-test", "FLOOFYCREW_PREFIX": str(home / ".local")}
    return _env(home, **{**defaults, **extra})


def _run_headless(script: Path, args: list[str], env: dict[str, str], *, timeout: float = 120) -> tuple[subprocess.CompletedProcess, float]:
    """No controlling terminal (``setsid``), stdin from /dev/null, stdout a pipe: the non-interactive path."""
    started = time.monotonic()
    completed = subprocess.run(["sh", str(script), *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, timeout=timeout, start_new_session=True, check=False)
    return completed, time.monotonic() - started


def _run_on_pty(script: Path, args: list[str], env: dict[str, str], answers: list[tuple[str, str]], *, columns: int = 100, timeout: float = 120) -> tuple[int, str]:
    """Drive the script on a pseudo-terminal: for each ``(wait_for, send)`` wait for the text, then type the answer."""
    controller, follower = pty.openpty()
    fcntl.ioctl(follower, termios.TIOCSWINSZ, struct.pack("HHHH", 50, columns, 0, 0))
    proc = subprocess.Popen(["sh", str(script), *args], stdin=follower, stdout=follower, stderr=follower, env=env, close_fds=True, start_new_session=True)
    os.close(follower)
    transcript = b""
    deadline = time.monotonic() + timeout
    pending = list(answers)

    def pump(until: str | None) -> None:
        nonlocal transcript
        while time.monotonic() < deadline:
            if until is not None and until.encode("utf-8") in transcript:
                return
            ready, _, _ = select.select([controller], [], [], 0.5)
            if not ready:
                if proc.poll() is not None and until is None:
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
        raise AssertionError(f"timed out waiting for {until!r}; transcript so far:\n{transcript.decode('utf-8', 'replace')}")

    for wait_for, send in pending:
        pump(wait_for)
        transcript = transcript.replace(wait_for.encode("utf-8"), b"<<answered>>", 1)
        os.write(controller, send.encode("utf-8"))
    pump(None)
    os.close(controller)
    code = proc.wait(timeout=30)
    return code, transcript.decode("utf-8", "replace").replace("\r\n", "\n")


# --- the scripts themselves ---------------------------------------------------------------------------------


@pytest.mark.parametrize("script", [PUBLIC / "install.sh", INTERNAL / "FloofyCrew" / "install.sh"])
def test_scripts_are_posix_sh_with_set_eu_and_never_accept_the_warning(script: Path):
    text = script.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n") and "\nset -eu\n" in text
    assert subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True, check=False).returncode == 0
    assert "I ACCEPT" not in text and "consent.json" not in text, "the installer never acknowledges the warning for the user"
    for step in range(1, 6):
        assert f'step {step} "' in text or f'done_step {step} "' in text or f'[{step}/$STEPS]' in text, f"step {step} is numbered"
    assert "ask_menu" in text and "(default)" in text and 'say "  next ' in text
    assert "NO_COLOR+set" in text and '"${TERM:-}" != dumb' in text and "--no-color" in text and "[ -t 1 ]" in text


def test_the_public_script_embeds_the_branding_banner_and_the_internal_package_ships_it():
    public = (PUBLIC / "install.sh").read_text(encoding="utf-8")
    embedded_txt = public.split("BANNER_TXT='", 1)[1].split("'", 1)[0]
    assert embedded_txt + "\n" == (BRANDING / "floofy.txt").read_text(encoding="utf-8")
    embedded_ans = public.split("BANNER_ANS='", 1)[1].split("'", 1)[0]
    assert embedded_ans.replace("\\033", "\x1b") + "\n" == (BRANDING / "floofy.ans").read_text(encoding="utf-8"), "printf %b turns \\033 into ESC: the embedded art is branding/ascii/floofy.ans"
    embedded_wide_txt = public.split("BANNER_WIDE_TXT='", 1)[1].split("'", 1)[0]
    assert embedded_wide_txt + "\n" == (BRANDING / "floofy-wide.txt").read_text(encoding="utf-8"), "the wide art (fox + wordmark) is embedded too"
    embedded_wide_ans = public.split("BANNER_WIDE_ANS='", 1)[1].split("'", 1)[0]
    assert embedded_wide_ans.replace("\\033", "\x1b") + "\n" == (BRANDING / "floofy-wide.ans").read_text(encoding="utf-8")
    for name in ("floofy.ans", "floofy.txt", "floofy-wide.ans", "floofy-wide.txt"):
        assert (INTERNAL / "FloofyCrew" / "banner" / name).read_bytes() == (BRANDING / name).read_bytes(), f"packaging/internal/FloofyCrew/banner/{name} is a byte-identical copy (source: branding/build_ascii.py, copied by scripts/refresh_install_banners.py)"
    wide = (BRANDING / "floofy-wide.txt").read_text(encoding="utf-8").splitlines()
    assert len(wide[0]) + 2 <= 110, "indented by two, the wide art fits a 110-column terminal"
    assert wide[4:10] != (BRANDING / "floofy.txt").read_text(encoding="utf-8").splitlines()[4:10], "the wordmark occupies the fox's middle rows"


# --- non-interactive runs -------------------------------------------------------------------------------------


def test_public_headless_run_takes_the_defaults_prints_every_step_and_never_blocks(public_release: dict, tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    completed, elapsed = _run_headless(public_release["root"] / "install.sh", [], _public_env(public_release, home, NO_COLOR="1", LC_ALL="C"))
    assert completed.returncode == 0, completed.stderr
    out = completed.stdout
    assert "\x1b[" not in out and elapsed < 120
    for step in ("[1/5] + Python:", "[2/5] + Release: v-test verified against SHA256SUMS", "[3/5] + Command: installed", "[4/5] + Choices: trigger=default, init=later"):
        assert step in out, out
    assert "verified floofy.pyz" in out and f"verified floofycrew-loader-app-{public_release['version']}.zip" in out
    assert "1) hourly user timer (systemd/launchd) and the kirocrew PATH wrapper" in out and "(default)" in out and "choice: 1 (default: no terminal to ask on)" in out
    assert "floofy init: later (no terminal to acknowledge the one-time warning on)" in out
    lib = home / ".local" / "lib" / "floofycrew"
    init_cmd = f"{home / '.local' / 'bin' / 'floofy'} init --loader-app {lib}/floofycrew-loader-app-{public_release['version']}.zip"
    assert f"[5/5] - no terminal to acknowledge the one-time warning on; run: {init_cmd}" in out
    assert "FloofyCrew v-test is installed." in out and f"  next      {init_cmd}" in out and "floofy deinit" in out
    assert (home / ".local" / "bin" / "floofy").is_file() and not (home / ".kiro").exists(), "installed, nothing initialised"
    # glyphs follow the locale
    again, _ = _run_headless(public_release["root"] / "install.sh", [], _public_env(public_release, home, NO_COLOR="1", LC_ALL="C.UTF-8"))
    assert again.returncode == 0 and "[1/5] ✓ Python:" in again.stdout and "[5/5] · no terminal" in again.stdout
    # a pipe without NO_COLOR is plain too (stdout is not a terminal)
    piped, _ = _run_headless(public_release["root"] / "install.sh", [], _public_env(public_release, home, COLORTERM="truecolor"))
    assert piped.returncode == 0 and "\x1b[" not in piped.stdout


def test_public_flags_and_environment_still_answer_everything(public_release: dict, tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    lib = home / ".local" / "lib" / "floofycrew"
    wrapper = home / ".local" / "bin" / "floofy"
    no_init, _ = _run_headless(public_release["root"] / "install.sh", [], _public_env(public_release, home, FLOOFYCREW_NO_INIT="1"))
    assert no_init.returncode == 0 and f"skipped floofy init (FLOOFYCREW_NO_INIT=1); run: {wrapper} init --loader-app {lib}" in no_init.stdout
    assert "Which re-apply trigger" not in no_init.stdout, "FLOOFYCREW_NO_INIT skips both questions"
    flagged, _ = _run_headless(public_release["root"] / "install.sh", ["--no-init", "--trigger", "none", "--no-color", "--no-banner"], _public_env(public_release, home))
    assert flagged.returncode == 0 and "skipped floofy init (--no-init" in flagged.stdout and "--no-trigger" in flagged.stdout and "re-apply trigger: none" in flagged.stdout
    for trigger, flags in (("timer", "--trigger user-timer"), ("wrapper", "--trigger path-wrapper"), ("none", "--no-trigger"), ("default", "")):
        chosen, _ = _run_headless(public_release["root"] / "install.sh", [], _public_env(public_release, home, FLOOFYCREW_TRIGGER=trigger))
        assert chosen.returncode == 0, chosen.stderr
        expected = f"run: {wrapper} init --loader-app {lib}/floofycrew-loader-app-{public_release['version']}.zip{(' ' + flags) if flags else ''}\n"
        assert expected in chosen.stdout, chosen.stdout
        assert f"trigger={trigger}, init=later" in chosen.stdout
    bogus, _ = _run_headless(public_release["root"] / "install.sh", [], _public_env(public_release, home, FLOOFYCREW_TRIGGER="bogus"))
    assert bogus.returncode == 1 and "must be default, timer, wrapper or none" in bogus.stderr
    unknown, _ = _run_headless(public_release["root"] / "install.sh", ["--bogus"], _public_env(public_release, home))
    assert unknown.returncode == 2 and "unknown argument --bogus" in unknown.stderr
    helped, _ = _run_headless(public_release["root"] / "install.sh", ["--help"], _public_env(public_release, home))
    assert helped.returncode == 0 and "curl -fsSL" in helped.stdout and "set -eu" not in helped.stdout
    prefixed, _ = _run_headless(public_release["root"] / "install.sh", ["--prefix", str(tmp_path / "elsewhere"), "--no-init"], _public_env(public_release, home))
    assert prefixed.returncode == 0 and (tmp_path / "elsewhere" / "bin" / "floofy").is_file()


def test_public_verification_failure_is_a_numbered_failed_step(public_release: dict, tmp_path: Path):
    import shutil

    tampered = tmp_path / "assets"
    shutil.copytree(public_release["root"] / "dist", tampered)
    with (tampered / "floofy.pyz").open("ab") as handle:
        handle.write(b"\n# tampered\n")
    home = tmp_path / "home"
    home.mkdir()
    failed, _ = _run_headless(public_release["root"] / "install.sh", [], _public_env(public_release, home, FLOOFYCREW_ASSET_DIR=str(tampered), LC_ALL="C"))
    assert failed.returncode == 1 and "[2/5] x install.sh: sha256 mismatch for floofy.pyz" in failed.stderr
    assert not (home / ".local").exists()


def test_internal_headless_run_takes_the_defaults_and_prints_the_summary(internal_package: dict, tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    prefix = tmp_path / "prefix"
    script = internal_package["root"] / "install.sh"
    completed, elapsed = _run_headless(script, ["--prefix", str(prefix)], _env(home, NO_COLOR="1"))
    assert completed.returncode == 0, completed.stderr
    out = completed.stdout
    assert "\x1b[" not in out and elapsed < 120
    for step in ("[1/5] ✓ Python:", "[2/5] ✓ Package:", "[3/5] ✓ Command: installed", "[4/5] ✓ Choices: trigger=default, init=later"):
        assert step in out, out
    assert "1) hourly user timer (systemd, at :55 after the host's own update) — the edition's default (default)" in out
    assert "2) none (floofy init --no-trigger" in out and "choice: 1 (default: no terminal to ask on)" in out
    init_cmd = f"{prefix / 'bin' / 'floofy'} init --loader-app {internal_package['root'] / 'app' / 'floofycrew'}"
    assert f"[5/5] · no terminal to acknowledge the one-time warning on; run: {init_cmd}" in out
    assert "FloofyCrew is installed." in out and f"  next      {init_cmd}" in out
    assert (prefix / "bin" / "floofy").is_file() and not (home / ".kiro").exists()
    version = subprocess.run([str(prefix / "bin" / "floofy"), "--version"], capture_output=True, text=True, env=_env(home), timeout=60, check=False)
    assert version.returncode == 0 and f"floofy {internal_package['version']}" in version.stdout


def test_internal_flags_keep_working_and_init_flags_pass_through(internal_package: dict, tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    prefix = tmp_path / "prefix"
    script = internal_package["root"] / "install.sh"
    app = internal_package["root"] / "app" / "floofycrew"
    wrapper = prefix / "bin" / "floofy"
    passthrough, _ = _run_headless(script, ["--no-init", "--prefix", str(prefix), "--trigger", "none", "--", "--i-accept-the-risk", "--no-loader-app"], _env(home))
    assert passthrough.returncode == 0, passthrough.stderr
    assert f"skipped floofy init (--no-init); run: {wrapper} init --loader-app {app} --no-trigger --i-accept-the-risk --no-loader-app" in passthrough.stdout
    assert "Which re-apply trigger" not in passthrough.stdout
    timer, _ = _run_headless(script, ["--prefix", str(prefix)], _env(home, FLOOFYCREW_TRIGGER="timer"))
    assert timer.returncode == 0 and f"run: {wrapper} init --loader-app {app} --trigger user-timer" in timer.stdout
    env_no_init, _ = _run_headless(script, ["--prefix", str(prefix)], _env(home, FLOOFYCREW_NO_INIT="1"))
    assert env_no_init.returncode == 0 and "skipped floofy init (--no-init)" in env_no_init.stdout
    bogus, _ = _run_headless(script, ["--trigger", "wrapper", "--prefix", str(prefix)], _env(home))
    assert bogus.returncode == 1 and "must be default, timer or none" in bogus.stderr
    unknown, _ = _run_headless(script, ["--bogus"], _env(home))
    assert unknown.returncode == 2 and "unknown argument --bogus" in unknown.stderr
    helped, _ = _run_headless(script, ["--help"], _env(home))
    assert helped.returncode == 0 and "sh install.sh [--no-init]" in helped.stdout and "set -eu" not in helped.stdout
    legacy = subprocess.run(["bash", str(script), "--no-init", "--prefix", str(prefix)], stdin=subprocess.DEVNULL, capture_output=True, text=True, env=_env(home), timeout=120, start_new_session=True, check=False)
    assert legacy.returncode == 0 and "skipped floofy init" in legacy.stdout and "python3" in legacy.stdout, "the previous invocation still works"


# --- terminal runs -------------------------------------------------------------------------------------------


def test_internal_terminal_run_walks_the_menus_with_defaults_and_paints(internal_package: dict, tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    prefix = tmp_path / "prefix"
    script = internal_package["root"] / "install.sh"
    env = _env(home, COLORTERM="truecolor")
    # an invalid answer re-asks; an empty answer takes the default (1); "2" declines init
    code, out = _run_on_pty(script, ["--prefix", str(prefix)], env, [("choice [1]: ", "9\n"), ("choice [1]: ", "\n"), ("choice [1]: ", "2\n")])
    assert code == 0, out
    assert "please answer a number between 1 and 2" in out
    assert "\x1b[95m[1/5]\x1b[0m \x1b[32m✓\x1b[0m Python:" in out, "the palette is on for a terminal"
    assert "\x1b[38;2;" in out, "24-bit banner on a truecolor terminal"
    ans_rows = (BRANDING / "floofy.ans").read_text(encoding="utf-8").splitlines()
    assert "  " + ans_rows[-1] in out, "the art is printed row by row, indented by two columns"
    assert "[4/5]\x1b[0m \x1b[32m✓\x1b[0m Choices: trigger=default, init=later" in out
    assert "skipped floofy init (--no-init); run:" in _plain(out) and "FloofyCrew is installed." in _plain(out)
    assert not (home / ".kiro").exists()


def test_terminal_banner_switches(internal_package: dict, tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    prefix = tmp_path / "prefix"
    script = internal_package["root"] / "install.sh"
    answers = [("choice [1]: ", "\n"), ("choice [1]: ", "2\n")]
    basic_code, basic = _run_on_pty(script, ["--prefix", str(prefix)], _env(home), answers)
    assert basic_code == 0 and "\x1b[95m\n" not in basic and "\x1b[38;2;" not in basic
    art = (BRANDING / "floofy.txt").read_text(encoding="utf-8").splitlines()
    assert "\x1b[95m  " + art[0] in basic, "16/256 colours: the plain art in one colour"
    narrow_code, narrow = _run_on_pty(script, ["--prefix", str(prefix)], _env(home, COLORTERM="truecolor"), answers, columns=41)
    assert narrow_code == 0 and "\x1b[38;2;" not in narrow and art[0] not in narrow, "narrower than 42 columns: no banner, colours still on"
    wide_rows = (BRANDING / "floofy-wide.ans").read_text(encoding="utf-8").splitlines()
    wide_code, wide = _run_on_pty(script, ["--prefix", str(prefix)], _env(home, COLORTERM="truecolor"), answers, columns=110)
    assert wide_code == 0 and "  " + wide_rows[6] in wide, "110 columns: the fox with the FloofyCrew wordmark beside it"
    plain_wide_code, plain_wide = _run_on_pty(script, ["--prefix", str(prefix)], _env(home), answers, columns=110)
    wide_txt = (BRANDING / "floofy-wide.txt").read_text(encoding="utf-8").splitlines()
    assert plain_wide_code == 0 and "\x1b[95m  " + wide_txt[0] in plain_wide and "  " + wide_txt[6] in plain_wide, "16 colours at 110 columns: the wide art in one colour"
    just_short_code, just_short = _run_on_pty(script, ["--prefix", str(prefix)], _env(home, COLORTERM="truecolor"), answers, columns=109)
    assert just_short_code == 0 and "  " + wide_rows[6] not in just_short and "  " + (BRANDING / "floofy.ans").read_text(encoding="utf-8").splitlines()[6] in just_short, "109 columns: the fox alone"
    assert "\x1b[95m[1/5]\x1b[0m" in narrow
    no_banner_code, no_banner = _run_on_pty(script, ["--prefix", str(prefix), "--no-banner"], _env(home, COLORTERM="truecolor"), answers)
    assert no_banner_code == 0 and art[0] not in no_banner and "\x1b[95m[1/5]\x1b[0m" in no_banner
    env_off_code, env_off = _run_on_pty(script, ["--prefix", str(prefix)], _env(home, COLORTERM="truecolor", FLOOFYCREW_NO_BANNER="1"), answers)
    assert env_off_code == 0 and art[0] not in env_off
    plain_code, plain = _run_on_pty(script, ["--prefix", str(prefix)], _env(home, COLORTERM="truecolor", NO_COLOR=""), answers)
    assert plain_code == 0 and "\x1b[" not in plain and art[0] not in plain, "NO_COLOR (even empty) on a terminal: no colour, no banner"
    flag_code, flag = _run_on_pty(script, ["--prefix", str(prefix), "--no-color"], _env(home, COLORTERM="truecolor"), answers)
    assert flag_code == 0 and "\x1b[" not in flag
    dumb_code, dumb = _run_on_pty(script, ["--prefix", str(prefix)], _env(home, COLORTERM="truecolor", TERM="dumb"), answers)
    assert dumb_code == 0 and "\x1b[" not in dumb


def test_public_terminal_run_answers_the_trigger_menu_by_number(public_release: dict, tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    env = _public_env(public_release, home, COLORTERM="truecolor")
    code, out = _run_on_pty(public_release["root"] / "install.sh", [], env, [("choice [1]: ", "3\n"), ("choice [1]: ", "2\n")])
    assert code == 0, out
    plain = _plain(out)
    assert "3) kirocrew PATH wrapper only" in plain and "trigger=wrapper, init=later" in plain
    assert "--trigger path-wrapper" in plain and "FloofyCrew v-test is installed." in plain
    assert "\x1b[38;2;" in out and "\x1b[95m[2/5]\x1b[0m \x1b[32m✓\x1b[0m Release:" in out
