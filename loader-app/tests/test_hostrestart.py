"""``POST /host/restart`` and ``POST /self-update`` (1.1.6): the red button and "Update & restart".

The restart is never answered by default: without ``confirmations.yes`` the
route is a 409 ``yes-no`` question with the exact prompt; with it the Loader
starts ``kirocrew restart`` **detached** (its own session, no descriptors, the
managed host home in ``KIROCREW_HOME``) and answers 202 with an audit row. No
launcher is a 422 that names the terminal command. ``self-update`` is a typed
route like every other CLI action: ``{now: true}`` spells ``--now`` and nothing
else is reachable (no ``--force``, ``--check``, ``--target``).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from floofy_core.audit import read_audit

from floofy_loader import hostrestart
from floofy_loader.app_routes import ROUTES, spec
from floofy_loader.routes import CLI_MOVED, ROUTE_TABLE

from test_app_routes import Home, homes  # noqa: F401 - the fixture


class FakePopen:
    calls: list[dict] = []

    def __init__(self, argv, **kwargs):
        self.pid = 4242
        FakePopen.calls.append({"argv": argv, **kwargs})


@pytest.fixture(autouse=True)
def _reset_popen():
    FakePopen.calls = []


def restart(home: Home, body: dict | None, *, popen=FakePopen) -> tuple[int, dict]:
    status, headers, raw = hostrestart.restart_response(home.runtime, body, popen=popen)
    assert headers["Content-Type"].startswith("application/json") and headers["Cache-Control"] == "no-store"
    return status, json.loads(raw.decode("utf-8"))


def launcher_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake ``kirocrew`` first on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launcher = bin_dir / "kirocrew"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return launcher


def test_the_route_is_declared_and_documented():
    assert ("POST", "/host/restart") in ROUTE_TABLE
    assert ("POST", "/self-update") in ROUTE_TABLE
    assert CLI_MOVED["self-update"] == "POST /self-update"
    su = spec("selfupdate")
    assert su.method == "POST" and su.path == "/self-update" and su.mutating
    assert su.argv({}, {}, {}) == ["self-update"]
    assert su.argv({}, {"now": True}, {}) == ["self-update", "--now"]
    assert su.argv({}, {"now": True, "force": True, "check": True, "target": "/x"}, {}) == ["self-update", "--now"], "only --now is reachable"
    assert any(r.name == "selfupdate" for r in ROUTES)


def test_without_the_answer_the_restart_is_a_yes_no_question(homes: Path):
    home = Home(homes, consent=True)
    status, body = restart(home, {})
    assert status == 409 and body["ok"] is False
    assert body["confirmation"]["kind"] == "yes-no" and body["confirmation"]["text"] == hostrestart.RESTART_PROMPT
    assert body["argv"] == ["kirocrew", "restart"]
    assert FakePopen.calls == [], "nothing was started"
    # a listed prompt that is not this one does not count either
    status, _ = restart(home, {"confirmations": {"yes": ["Uninstall alpha 1.0.0?"]}})
    assert status == 409
    assert restart(home, "nope")[0] == 400
    assert not read_audit(home.paths.audit)


def test_with_the_answer_kirocrew_restart_starts_detached_against_the_managed_home(homes: Path, monkeypatch: pytest.MonkeyPatch):
    launcher = launcher_in(homes, monkeypatch)
    home = Home(homes, consent=True)
    from floofy_core.audit import AuditLog

    home.runtime.audit = AuditLog(home.paths.data_home, actor="loader")
    status, body = restart(home, {"confirmations": {"yes": [hostrestart.RESTART_PROMPT]}})
    assert status == 202 and body["ok"] is True and body["restarting"] is True
    assert body["launcher"] == str(launcher) and body["pid"] == 4242
    assert body["argv"] == [str(launcher), "restart"] and "started" in body["transcript"][0]
    assert len(FakePopen.calls) == 1
    call = FakePopen.calls[0]
    assert call["argv"] == [str(launcher), "restart"]
    assert call["start_new_session"] is True and call["close_fds"] is True
    assert call["stdin"] is subprocess.DEVNULL and call["stdout"] is subprocess.DEVNULL and call["stderr"] is subprocess.DEVNULL
    assert call["env"]["KIROCREW_HOME"] == str(home.host_home), "the host home the Loader manages, never the process's own"
    rows = read_audit(home.paths.audit)
    assert [r["op"] for r in rows] == ["host-restart"] and rows[0]["actor"] == "app" and rows[0]["result"] == "ok" and rows[0]["pid"] == 4242
    # `yes: true` (every question of the request) is the other accepted shape
    assert restart(home, {"confirmations": {"yes": True}})[0] == 202


def test_no_launcher_is_a_422_that_names_the_terminal_command(homes: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PATH", str(homes / "empty"))
    home = Home(homes, consent=True)
    status, body = restart(home, {"confirmations": {"yes": True}})
    assert status == 422 and "kirocrew restart" in body["error"] and "launcher" in body["error"]
    assert FakePopen.calls == []


def test_a_spawn_failure_is_reported_not_raised(homes: Path, monkeypatch: pytest.MonkeyPatch):
    launcher_in(homes, monkeypatch)
    home = Home(homes, consent=True)

    def broken(argv, **kwargs):
        raise OSError("exec format error")

    status, body = restart(home, {"confirmations": {"yes": True}}, popen=broken)
    assert status == 500 and "exec format error" in body["error"]


def test_the_launcher_falls_back_to_the_payload_and_the_interpreter_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    class Facts:
        payload_root = tmp_path / "payload"
        interpreter = tmp_path / "venv" / "bin" / "python"

    assert hostrestart.find_restart_launcher(Facts()) is None
    beside = tmp_path / "venv" / "bin" / "kirocrew"
    beside.parent.mkdir(parents=True)
    beside.write_text("#!/bin/sh\n", encoding="utf-8")
    beside.chmod(0o755)
    assert hostrestart.find_restart_launcher(Facts()) == beside
    in_payload = tmp_path / "payload" / "bin" / "kirocrew"
    in_payload.parent.mkdir(parents=True)
    in_payload.write_text("#!/bin/sh\n", encoding="utf-8")
    in_payload.chmod(0o755)
    assert hostrestart.find_restart_launcher(Facts()) == in_payload, "the payload's own launcher outranks the interpreter's sibling"


def test_the_loader_never_starts_before_it_booted():
    from floofy_loader.runtime import LoaderRuntime

    status, _, raw = hostrestart.restart_response(LoaderRuntime(), {"confirmations": {"yes": True}}, popen=FakePopen)
    assert status == 503 and b"has not started" in raw and FakePopen.calls == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX sessions")
def test_spawn_restart_really_detaches(tmp_path: Path):
    """The real Popen path once: the child lives in its own session, so the gateway going away does not take it along."""
    launcher = tmp_path / "kirocrew"
    launcher.write_text("#!/bin/sh\nps -o sid= -p $$ > \"$1.sid\"\n", encoding="utf-8")
    launcher.chmod(0o755)
    marker = tmp_path / "restart"
    # the fake launcher records its session id under "<first arg>.sid" — the first arg is "restart", written into cwd; use the home for cwd
    pid = hostrestart.spawn_restart(launcher, host_home=tmp_path, popen=lambda argv, **kw: subprocess.Popen([argv[0], str(marker)], **{k: v for k, v in kw.items() if k != "cwd"}, cwd=str(tmp_path)))
    assert pid > 0
    os.waitpid(pid, 0)
    child_sid = int((tmp_path / "restart.sid").read_text().strip())
    own_sid = int(subprocess.run(["ps", "-o", "sid=", "-p", str(os.getpid())], capture_output=True, text=True, check=False).stdout.strip() or -1)
    assert child_sid != own_sid, "start_new_session: the restart runs in its own session"



def test_self_update_route_runs_the_cli_handler(homes: Path):
    """The route is `floofy self-update [--now]` in-process: with no release feed (no adapters) the CLI's own refusal comes back, nothing else."""
    home = Home(homes, consent=True)
    status, body = home.call("selfupdate", body={"now": True})
    assert status == 422 and body["ok"] is False
    assert body["argv"] == ["self-update", "--now"] and body["command"] == "self-update"
    assert "cannot check for a FloofyCrew release" in body["error"]
    assert body["reloaded"] is False, "a self-update never reloads the (old) Loader in-process; the restart follows"
