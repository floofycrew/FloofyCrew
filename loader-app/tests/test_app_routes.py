"""Route contracts of the manager App (task 11.1; Requirement 16.2, 16.3, 16.5, 8.8, 7.6).

Every typed route runs the same ``floofy`` handler as the CLI with ``actor: app``:
the audit row of an action taken through a route equals the CLI's row for the
same action minus ``actor`` and ``ts`` (and the ``consentRef`` digest, which
covers the consent record's timestamp). A confirmation the CLI would ask is a
``409`` question here — the one-time consent, an ordinary yes/no, a governance
target typed byte for byte, the unlisted-source line, the loosening of a
registry's signature requirement — and nothing changes until it is answered;
``--yes``-style shortcuts cannot be spelled in a request. Fake payload, scratch
homes, a bare git repository over ``file://`` under the test switch; no live install.
"""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import types
from pathlib import Path
from typing import Any

import pytest

from floofy_core.audit import read_audit
from floofy_core.cli.console import Console
from floofy_core.cli.main import execute
from floofy_core.consent import ACCEPT_PHRASE, WARNING_TEXT, read_consent, write_consent
from floofy_core.datahome import DataHome
from floofy_core.modstore import read_enabled
from floofy_loader import app_routes
from floofy_loader.app_routes import ROUTES, AppRouteSpec, dispatch, spec
from floofy_loader.host import read_host_facts
from floofy_loader.routes import CLI_ALLOWLIST, ROUTE_TABLE, cli_response
from floofy_loader.runtime import LoaderRuntime

from floofy_testing import EXAMPLES_DIR, fake_payload
from test_boot import make_mod

FORBIDDEN_FLAGS = {"--yes", "-y", "--i-accept-the-risk", "--accept-unlisted-source", "--accept-flags", "--confirm-governance-target", "--json"}
#: What the audit row of a route may differ in from the CLI's row for the same action.
SURFACE_FIELDS = {"actor", "ts", "consentRef"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(*args: str, cwd: Path) -> str:
    done = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True, env={"GIT_AUTHOR_NAME": "tests", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "tests", "GIT_COMMITTER_EMAIL": "t@example.invalid", "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(cwd)})
    return done.stdout.strip()


def write_mod(root: Path, mod_id: str, *, parts: list[dict], files: dict[str, str], extra: dict | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    manifest = {"schema": 1, "id": mod_id, "name": mod_id, "version": "1.0.0", "description": "test mod", "authors": ["tests"], "license": "MIT", "kirocrew": {"version": ">=0.7.0 <0.9.0"}, "dependsOn": {"floofycrew": ">=0.0.0"}, "parts": parts, "files": [{"path": rel, "sha256": sha((root / rel).read_bytes())} for rel in sorted(files)]}
    manifest.update(extra or {})
    (root / "floofy.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return root


class Home:
    """One scratch host home with a fake payload: a runtime for the routes and an argv prefix for the CLI."""

    def __init__(self, root: Path, *, consent: bool, payload: Path | None = None):
        self.root = root
        self.payload = payload if payload is not None else fake_payload(root / "payload", "0.7.0", build="0.7.0.5")
        self.host_home = root / "home"
        self.paths = DataHome.for_host_home(self.host_home).ensure()
        if consent:
            write_consent(self.paths.consent, by="tests", how="test")
        module = types.ModuleType("kiro_crew")
        module.__version__ = "0.7.0.5"
        module.__file__ = str(self.payload / "__init__.py")
        self.runtime = LoaderRuntime()
        self.runtime.facts = read_host_facts(host=module, env={"KIROCREW_HOME": str(self.host_home)}, adapters=[])
        self.runtime.paths = self.paths

    def call(self, name: str, params: dict[str, str] | None = None, body: dict | None = None, query: dict[str, list[str]] | None = None, *, reload: bool = True) -> tuple[int, dict]:
        status, headers, raw = dispatch(self.runtime, spec(name), params or {}, body, query, reload=reload)
        assert headers["Content-Type"].startswith("application/json")
        return status, json.loads(raw.decode("utf-8"))

    def cli(self, *args: str, answers: dict[str, str] | None = None) -> tuple[int, dict, Console]:
        """The CLI with a keyboard: ``answers`` maps a prompt fragment to what the user types."""
        answers = answers or {}

        def typed(prompt: str) -> str:
            for fragment, answer in answers.items():
                if fragment in prompt:
                    return answer
            return ""

        console = Console(out=io.StringIO(), err=io.StringIO(), input_fn=typed, non_interactive=False)
        code, result = execute(["--home", str(self.host_home), "--root", str(self.payload.parent), *args], console, actor="cli", in_gateway=True, live_state=self.runtime.state_dict)
        return code, result, console

    def audit(self) -> list[dict]:
        return read_audit(self.paths.audit)


@pytest.fixture
def homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FLOOFY_NO_ADAPTERS", "1")
    monkeypatch.setenv("FLOOFY_ALLOW_LOCAL_GIT", "1")
    monkeypatch.delenv("KIROCREW_HOME", raising=False)
    monkeypatch.setenv("KIRO_HOME", str(tmp_path / "kiro"))
    yield tmp_path
    # the reloads booted test mods in-process: forget their modules and per-mod log handlers so the boot and storage
    # tests start from a clean interpreter
    import logging
    import sys

    for key in [k for k in sys.modules if k == "floofy_mods" or k.startswith("floofy_mods.")]:
        sys.modules.pop(key, None)
    for name, logger in list(logging.Logger.manager.loggerDict.items()):
        if name.startswith("floofy.mods.") and isinstance(logger, logging.Logger):
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()


def comparable(row: dict) -> dict:
    """An audit row without the fields a surface may differ in."""
    return {k: v for k, v in row.items() if k not in SURFACE_FIELDS}


# --- the table --------------------------------------------------------------------------------------------


def test_every_cli_action_of_requirement_15_4_has_a_route_and_no_route_can_spell_a_shortcut_flag():
    names = {r.name for r in ROUTES}
    assert {"mods.install", "mods.enable", "mods.disable", "mods.update", "updates.check", "updates.apply", "mods.uninstall", "mods.yeet", "mods.yeet_restore", "mods.yeet_list", "registries.add", "registries.remove", "registries.refresh", "registries.trust", "registries.defaults", "profiles.list", "profiles.save", "profiles.use", "profiles.export", "profiles.import", "doctor", "audit", "consent", "mods.search", "mods.info", "status"} <= names
    assert all((r.method, r.path) in ROUTE_TABLE for r in ROUTES), "the route table the app.json documents covers the App routes"
    everything = {"source": "x", "now": True, "enable": True, "disabled": True, "ref": "main", "reload": False, "keepConfig": True, "reason": "r", "ids": ["a-mod"], "version": "0.7.0.5", "url": "https://r.example/", "trust": "owner", "allowUnsigned": True, "name": "p", "keyId": "k", "publicKey": "pk", "refresh": False, "hostRegistry": False, "check": True, "file": "/tmp/x.json", "cancel": True, "agree": True, "reaccept": True}
    for route in ROUTES:
        argv = route.argv({"id": "a-mod", "key": "srckey", "name": "p"}, everything, {"q": ["x"], "tail": ["3"], "op": ["enable"]})
        assert not (set(argv) & FORBIDDEN_FLAGS), (route.name, argv)
    # a value that tries to smuggle a flag in is refused before anything runs
    with pytest.raises(app_routes.RouteError):
        spec("mods.install").argv({}, {"source": "--yes"}, {})
    with pytest.raises(app_routes.RouteError):
        spec("profiles.save").argv({}, {"name": "-y"}, {})
    with pytest.raises(app_routes.RouteError):
        spec("mods.enable").argv({"id": "../x"}, {}, {})


def test_cli_route_is_read_only_now_and_points_at_the_typed_routes(homes: Path):
    home = Home(homes, consent=True)
    make_mod(home.paths, "alpha")
    assert CLI_ALLOWLIST == {"status", "doctor", "search", "info", "list", "audit", "which"}
    for argv, route in (("enable alpha", "/mods/{id}/enable"), ("install /tmp/mod", "/mods/install"), ("init --i-accept-the-risk", "/consent"), ("registry refresh", "/registries")):
        status, _, raw = cli_response(home.runtime, {"argv": argv.split()})
        body = json.loads(raw)
        assert status == 403 and route in body["error"], body
    status, _, raw = cli_response(home.runtime, {"argv": ["status"]})
    assert status == 200 and json.loads(raw)["json"]["mods"][0]["id"] == "alpha"
    assert read_enabled(home.paths.enabled) == {}, "nothing mutated through /cli"


# --- consent ----------------------------------------------------------------------------------------------


def test_consent_outranks_everything_and_is_recorded_as_app(homes: Path):
    home = Home(homes, consent=False)
    make_mod(home.paths, "alpha")
    # a mutation that needs consent stops with the question; nothing changed, no audit row
    status, body = home.call("mods.enable", {"id": "alpha"})
    assert status == 409 and body["confirmation"]["kind"] == "consent" and body["confirmation"]["expects"] == "agree" and body["confirmation"]["text"] == WARNING_TEXT
    assert read_enabled(home.paths.enabled) == {} and not home.paths.consent.exists() and home.audit() == []
    # the consent route without the agreement is the same question
    status, body = home.call("consent", body={})
    assert status == 409 and body["confirmation"]["kind"] == "consent" and not home.paths.consent.exists()
    # {"agree": true} IS the [ I AGREE ] answer — through the protocol it arrives as confirmations.consent: recorded through
    # the CLI's own init step, how = app
    status, body = home.call("consent", body={"confirmations": {"consent": True}})
    assert status == 200 and body["ok"] is True and body["reloaded"] is True, body
    record = json.loads(home.paths.consent.read_text(encoding="utf-8"))
    assert record["how"] == "app" and read_consent(home.paths.consent).ok
    ops = [(r["op"], r["actor"], r.get("how")) for r in home.audit()]
    assert ops == [("consent", "app", "app"), ("init", "app", None)]
    assert home.runtime.result is not None and home.runtime.result.state.loader == "ok", "the Loader left the inert state"
    # the enable now goes through
    status, body = home.call("mods.enable", {"id": "alpha"})
    assert status == 200 and read_enabled(home.paths.enabled) == {"alpha": True} and body["reloaded"] is True
    assert home.audit()[-1]["op"] == "enable" and home.audit()[-1]["actor"] == "app"


def test_a_mutating_request_may_carry_the_agreement_itself(homes: Path):
    home = Home(homes, consent=False)
    make_mod(home.paths, "alpha")
    status, body = home.call("mods.enable", {"id": "alpha"}, {"confirmations": {"consent": True}})
    assert status == 200 and body["ok"] is True
    assert json.loads(home.paths.consent.read_text(encoding="utf-8"))["how"] == "app"
    assert [r["op"] for r in home.audit()] == ["consent", "init", "enable"] and {r["actor"] for r in home.audit()} == {"app"}
    assert read_enabled(home.paths.enabled) == {"alpha": True}
    # re-acknowledging on request (the Settings page): the same step, the record rewritten, a second consent row
    status, body = home.call("consent", body={"agree": True, "reaccept": True})
    assert status == 200 and body["ok"] is True and json.loads(home.paths.consent.read_text(encoding="utf-8"))["how"] == "app"
    assert [r["op"] for r in home.audit()[-2:]] == ["consent", "init"] and len([r for r in home.audit() if r["op"] == "consent"]) == 2


# --- yes/no and the audit contract ------------------------------------------------------------------------


def test_uninstall_asks_yes_no_then_writes_the_cli_row(homes: Path):
    app = Home(homes / "app", consent=True)
    cli = Home(homes / "cli", consent=True, payload=app.payload)
    for home in (app, cli):
        make_mod(home.paths, "alpha")
    status, body = app.call("mods.uninstall", {"id": "alpha"}, {"now": True})
    assert status == 409 and body["confirmation"]["kind"] == "yes-no" and body["confirmation"]["expects"] == "confirm" and body["confirmation"]["text"] == "Uninstall alpha 1.0.0?"
    assert (app.paths.mods / "alpha").is_dir() and app.audit() == [], "no side effect on a 409"
    # the exact prompt in a list is an answer; an unrelated prompt is not
    status, body = app.call("mods.uninstall", {"id": "alpha"}, {"now": True, "confirmations": {"yes": ["Something else?"]}})
    assert status == 409 and body["confirmation"]["detail"] == "prompt-not-listed" and (app.paths.mods / "alpha").is_dir()
    status, body = app.call("mods.uninstall", {"id": "alpha"}, {"now": True, "confirmations": {"yes": ["Uninstall alpha 1.0.0?"]}})
    assert status == 200 and not (app.paths.mods / "alpha").exists() and body["reloaded"] is True
    code, _, _ = cli.cli("uninstall", "alpha", "--now", answers={"Uninstall alpha": "y"})
    assert code == 0
    app_row, cli_row = app.audit()[-1], cli.audit()[-1]
    assert app_row["op"] == cli_row["op"] == "uninstall" and app_row["actor"] == "app" and cli_row["actor"] == "cli"
    assert comparable(app_row) == comparable(cli_row) | {"files": [str(app.paths.mods / "alpha")]}


def test_enable_disable_rows_equal_the_cli_rows(homes: Path):
    app = Home(homes / "app", consent=True)
    cli = Home(homes / "cli", consent=True, payload=app.payload)
    for home in (app, cli):
        make_mod(home.paths, "alpha")
    assert app.call("mods.enable", {"id": "alpha"})[0] == 200
    assert app.call("mods.disable", {"id": "alpha"}, {"reload": False})[0] == 200
    assert cli.cli("enable", "alpha")[0] == 0 and cli.cli("disable", "alpha", "--no-reload")[0] == 0
    assert [comparable(r) for r in app.audit()] == [comparable(r) for r in cli.audit()]
    assert {r["actor"] for r in app.audit()} == {"app"} and {r["actor"] for r in cli.audit()} == {"cli"}


# --- install: the disclosure, the kinds, the typed governance target --------------------------------------------


def _govy(root: Path) -> Path:
    """A mod that needs every install question: a python-hook (yes), a governance-altering shipped file (typed)."""
    return write_mod(root / "govy", "govy", parts=[{"kind": "agent", "side": "gateway", "path": "agents/a.json"}, {"kind": "python-hook", "side": "gateway", "path": "hook.py", "module": "hook"}], files={"agents/a.json": json.dumps({"name": "a"}), "hook.py": "def activate(ctx):\n    pass\n", "security_policy.json": "{}"})


def test_install_stops_at_each_missing_question_without_side_effects(homes: Path):
    home = Home(homes, consent=True)
    mod = _govy(homes / "src")
    # 1. the kinds question comes first, with the whole disclosure so the client can show everything at once
    status, body = home.call("mods.install", body={"source": str(mod), "now": True})
    assert status == 409 and body["confirmation"]["kind"] == "yes-no" and "python-hook" in body["confirmation"]["text"], body
    disclosure = body["disclosure"]
    assert disclosure["id"] == "govy" and disclosure["confirmKinds"] == ["python-hook"] and disclosure["governanceTargets"] == ["security_policy.json"] and disclosure["codeParts"] is True
    assert any(f["code"] == "GovernanceAltering" for f in disclosure["flags"])
    assert any("GOVERNANCE-ALTERING" in line for line in body["transcript"])
    # 2. yes answers the kinds and the flags; the governance target is a typed question of its own
    status, body = home.call("mods.install", body={"source": str(mod), "now": True, "confirmations": {"yes": True}})
    assert status == 409 and body["confirmation"] == {"kind": "governance-target", "text": body["confirmation"]["text"], "expects": "typed-path", "targets": ["security_policy.json"], "detail": "missing"}
    assert "Type the exact path" in body["confirmation"]["text"]
    # 3. a typed path is compared byte for byte
    for wrong in ("security_policy.json ", "Security_policy.json", "./security_policy.json", "security_policy"):
        status, body = home.call("mods.install", body={"source": str(mod), "now": True, "confirmations": {"yes": True, "governanceTargets": [wrong]}})
        assert status == 409 and body["confirmation"]["kind"] == "governance-target" and body["confirmation"]["detail"] == "mismatch", wrong
    assert not (home.paths.mods / "govy").exists() and not (home.paths.pending / "govy").exists()
    assert home.audit() == [], "four refusals, no audit row: the command never got past its questions"
    # 4. every answer present: installed now, disabled (a code mod), the rows written
    status, body = home.call("mods.install", body={"source": str(mod), "now": True, "confirmations": {"yes": True, "governanceTargets": ["security_policy.json"]}})
    assert status == 200 and body["ok"] is True, body
    assert (home.paths.mods / "govy").is_dir() and read_enabled(home.paths.enabled) == {"govy": True}, "a confirmed install lands enabled (Requirement 11.7)"
    ops = [r["op"] for r in home.audit()]
    assert ops == ["governance-target-confirm", "agent-install", "install"] and all(r["actor"] == "app" for r in home.audit())
    assert home.audit()[0]["detail"] == "typed at the prompt" and home.audit()[0]["files"] == ["security_policy.json"]
    assert "GovernanceAltering" in home.audit()[-1]["governanceFlags"] and home.audit()[-1]["result"] == "ok"


def test_install_rows_equal_the_cli_rows_typed_at_a_keyboard(homes: Path):
    app = Home(homes / "app", consent=True)
    cli = Home(homes / "cli", consent=True, payload=app.payload)
    mod = _govy(homes / "src")
    assert app.call("mods.install", body={"source": str(mod), "now": True, "confirmations": {"yes": True, "governanceTargets": ["security_policy.json"]}})[0] == 200
    code, _, console = cli.cli("install", str(mod), "--now", answers={"GOVERNANCE-ALTERING": "security_policy.json", "Install govy": "y", "Accept the": "y"})
    assert code == 0, console.err.getvalue()
    app_rows, cli_rows = app.audit(), cli.audit()
    assert [r["op"] for r in app_rows] == [r["op"] for r in cli_rows] == ["governance-target-confirm", "agent-install", "install"]
    for app_row, cli_row in zip(app_rows, cli_rows):
        left, right = comparable(app_row), comparable(cli_row)
        # the two homes differ only in their paths
        assert json.dumps(left, sort_keys=True).replace(str(app.root), "<home>") == json.dumps(right, sort_keys=True).replace(str(cli.root), "<home>"), (left, right)
    assert app_rows[-1]["actor"] == "app" and cli_rows[-1]["actor"] == "cli"


def test_install_is_staged_by_default_and_apply_now_reloads(homes: Path):
    home = Home(homes, consent=True)
    plain = write_mod(homes / "src" / "plain", "plain", parts=[{"kind": "agent", "side": "gateway", "path": "agents/a.json"}], files={"agents/a.json": json.dumps({"name": "a"})})
    status, body = home.call("mods.install", body={"source": str(plain)})
    assert status == 200 and body["json"]["placed"]["how"] == "staged" and body["reloaded"] is False, body
    assert (home.paths.pending / "plain").is_dir() and not (home.paths.mods / "plain").exists()
    assert any("staged in pending/" in line for line in body["transcript"])
    assert home.audit()[-1]["op"] == "install" and "staged into" in home.audit()[-1]["detail"]
    # "apply now" = the Loader's reload: pending/ is applied at boot
    home.runtime.reload()
    assert (home.paths.mods / "plain").is_dir() and not (home.paths.pending / "plain").exists()
    # a second mod with now: true lands directly and reloads in-process
    other = write_mod(homes / "src" / "other", "other", parts=[{"kind": "agent", "side": "gateway", "path": "agents/b.json"}], files={"agents/b.json": json.dumps({"name": "b"})})
    status, body = home.call("mods.install", body={"source": str(other), "now": True})
    assert status == 200 and body["json"]["placed"]["how"] == "installed" and body["reloaded"] is True and (home.paths.mods / "other").is_dir()


def test_install_from_a_git_reference_needs_the_unlisted_source_line(homes: Path):
    import shutil

    work = homes / "work"
    shutil.copytree(EXAMPLES_DIR / "theme", work)
    _git("init", "-q", "-b", "main", cwd=work)
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "release", cwd=work)
    _git("tag", "1.0.0", cwd=work)
    commit = _git("rev-parse", "HEAD", cwd=work)
    bare = homes / "forge.git"
    _git("clone", "-q", "--bare", str(work), str(bare), cwd=homes)
    reference = f"{bare.as_uri()}@1.0.0"
    home = Home(homes / "home", consent=True)
    status, body = home.call("mods.install", body={"source": reference, "now": True})
    assert status == 409 and body["confirmation"]["kind"] == "unlisted-source" and body["confirmation"]["expects"] == "accept" and "no curator review" in body["confirmation"]["detail"]
    assert body["disclosure"]["unlistedSource"]["commit"] == commit and body["disclosure"]["tier"] == "unlisted"
    assert home.audit() == [] and not any(home.paths.mods.iterdir())
    status, body = home.call("mods.install", body={"source": reference, "now": True, "confirmations": {"unlistedSource": "i accept"}})
    assert status == 409 and body["confirmation"]["detail"].startswith("mismatch")
    status, body = home.call("mods.install", body={"source": reference, "now": True, "confirmations": {"unlistedSource": ACCEPT_PHRASE}})
    assert status == 200, body
    row = home.audit()[-1]
    assert row["op"] == "install" and row["actor"] == "app" and row["tier"] == "unlisted" and row["commit"] == commit and row["unlistedSource"]["how"] == "typed"


# --- registries -------------------------------------------------------------------------------------------


def test_registry_trust_loosening_asks_first_and_writes_the_cli_rows(homes: Path):
    app = Home(homes / "app", consent=True)
    cli = Home(homes / "cli", consent=True, payload=app.payload)
    status, body = app.call("registries.add", body={"url": "https://registry.example/", "name": "ex", "allowUnsigned": True, "refresh": False})
    assert status == 409 and body["confirmation"]["kind"] == "unsigned-index" and "TRUST LOOSENED for https://registry.example/" in body["confirmation"]["text"]
    assert app.audit() == [] and not app.paths.registries.exists(), "no side effect on the 409"
    status, body = app.call("registries.add", body={"url": "https://registry.example/", "name": "ex", "allowUnsigned": True, "refresh": False, "confirmations": {"unsignedIndex": True}})
    assert status == 200 and body["ok"] is True, body
    code, _, _ = cli.cli("registry", "add", "https://registry.example/", "--trust", "index", "--allow-unsigned", "--name", "ex", "--no-refresh")
    assert code == 0
    assert [r["op"] for r in app.audit()] == [r["op"] for r in cli.audit()] == ["registry-add", "registry-trust-loosened"]
    for app_row, cli_row in zip(app.audit(), cli.audit()):
        assert json.dumps(comparable(app_row), sort_keys=True).replace(str(app.root), "<home>") == json.dumps(comparable(cli_row), sort_keys=True).replace(str(cli.root), "<home>")
    # the trust route works by key and requires the same confirmation when it loosens
    key = json.loads(app.paths.registries.read_text(encoding="utf-8"))["sources"][0]
    status, body = app.call("registries.trust", {"key": "ex"}, {"trust": "owner"})
    assert status == 200 and body["json"]["source"]["trust"] == "owner" and body["json"]["source"]["allowUnsigned"] is False, body
    status, body = app.call("registries.trust", {"key": "ex"}, {"trust": "owner", "allowUnsigned": True})
    assert status == 409 and body["confirmation"]["kind"] == "unsigned-index"
    status, body = app.call("registries.trust", {"key": "nope"}, {"trust": "owner"})
    assert status == 404
    status, body = app.call("registries.remove", {"key": "ex"})
    assert status == 200 and app.audit()[-1]["op"] == "registry-remove"
    assert key["url"] == "https://registry.example/"


# --- read routes ------------------------------------------------------------------------------------------


def test_read_routes_run_the_read_only_commands(homes: Path):
    home = Home(homes, consent=True)
    make_mod(home.paths, "alpha")
    assert home.call("mods.enable", {"id": "alpha"})[0] == 200
    status, body = home.call("status")
    assert status == 200 and body["json"]["mods"][0]["id"] == "alpha" and body["json"]["loader"]["source"] == "in-process"
    status, body = home.call("audit", query={"tail": ["1"], "op": ["enable"]})
    assert status == 200 and body["json"]["count"] == 1 and body["json"]["rows"][0]["op"] == "enable"
    assert home.call("audit", query={"tail": ["x"]})[0] == 400
    status, body = home.call("mods.search", query={"q": ["alpha"]})
    assert status == 200 and body["json"]["mods"] == []
    status, body = home.call("mods.info", {"id": "alpha"})
    assert status == 200 and body["json"]["installed"]["version"] == "1.0.0" and body["json"]["installed"]["tier"] == "unlisted"
    assert home.call("mods.info", {"id": "nope"})[0] == 422
    status, body = home.call("profiles.list")
    assert status == 200 and body["json"]["profiles"] == []
    status, body = home.call("registries.list")
    assert status == 200 and body["json"]["sources"] == []
    status, body = home.call("mods.yeet_list")
    assert status == 200 and body["ok"] is True
    status, body = home.call("doctor")
    assert status == 200 and body["json"]["consent"]["status"] == "ok"
    before = len(home.audit())
    assert len(home.audit()) == before, "read routes write no audit row"


def test_profiles_and_vanilla_routes(homes: Path):
    home = Home(homes, consent=True)
    make_mod(home.paths, "alpha")
    assert home.call("mods.enable", {"id": "alpha"})[0] == 200
    status, body = home.call("profiles.save", body={"name": "base"})
    assert status == 200 and body["json"]["name"] == "base" and home.audit()[-1]["op"] == "profile-save"
    status, body = home.call("profiles.use", {"name": "base"}, {"check": True})
    assert status == 200 and body["json"]["plan"]
    target = homes / "base.json"
    assert home.call("profiles.export", {"name": "base"}, {"file": str(target)})[0] == 200 and target.is_file()
    assert home.call("profiles.import", body={"file": str(target), "name": "copy"})[0] == 200
    assert home.call("profiles.save", body={"name": "bad name"})[0] == 400
    status, body = home.call("vanilla", body={})
    assert status == 200 and home.paths.vanilla_marker.is_file() and body["reloaded"] is False, "the marker is consumed by the next start, never by a route's reload"
    assert home.call("vanilla", body={"cancel": True})[0] == 200 and not home.paths.vanilla_marker.exists()


def test_yeet_and_restore_routes(homes: Path):
    home = Home(homes, consent=True)
    make_mod(home.paths, "alpha")
    assert home.call("mods.enable", {"id": "alpha"})[0] == 200
    status, body = home.call("mods.yeet", {"id": "alpha"}, {"reason": "test"})
    assert status == 200, body
    assert not (home.paths.mods / "alpha").exists() and home.audit()[-1]["op"] == "yeet"
    status, body = home.call("mods.yeet_list")
    quarantine = body["json"]
    versions = [k for k in quarantine.get("quarantine", quarantine) if k != "requests"] if isinstance(quarantine, dict) else []
    assert versions, quarantine
    status, body = home.call("mods.yeet_restore", body={"version": versions[0]})
    assert status == 200 and (home.paths.mods / "alpha").is_dir()
    assert home.call("mods.yeet_restore", body={"version": "../x"})[0] == 400



# --- updates (task 11.4) ------------------------------------------------------------------------------------


def _archive_server(mod_dir: Path, top: str):
    """A loopback HTTP server holding one zip of ``mod_dir``; returns ``(server, url, sha256)``."""
    import io
    import threading
    import zipfile
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(mod_dir.rglob("*")):
            if path.is_file():
                archive.write(path, f"{top}/{path.relative_to(mod_dir).as_posix()}")
    payload = buffer.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):  # noqa: D401
            return

        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/{top}.zip", sha(payload)


def test_update_check_and_one_click_update_share_the_cli_path(homes: Path):
    """Requirement 16.6: update availability with the changelog link, and an update through `floofy update <id>`."""
    app = Home(homes / "app", consent=True)
    cli = Home(homes / "cli", consent=True, payload=app.payload)
    newer = write_mod(homes / "src" / "reggy", "reggy", parts=[{"kind": "agent", "side": "gateway", "path": "agents/r.json"}], files={"agents/r.json": json.dumps({"name": "r"})}, extra={"version": "1.1.0"})
    server, url, digest = _archive_server(newer, "reggy")
    try:
        index = {"schema": 1, "source": "test-source", "generatedAt": "2026-09-19T00:00:00Z", "mods": [{"id": "reggy", "name": "Reggy", "description": "a registry mod", "authors": ["tests"], "tags": [], "repo": "https://forge.example/reggy", "versions": [{"version": "1.0.0", "kirocrew": ">=0.7.0 <0.9.0", "compat": {}, "files": [{"url": url, "sha256": digest}]}, {"version": "1.1.0", "kirocrew": ">=0.7.0 <0.9.0", "compat": {"0.7.0.5": "expected"}, "files": [{"url": url, "sha256": digest}], "changelog": "https://forge.example/reggy/releases/1.1.0"}]}]}
        for home in (app, cli):
            home.paths.index_cache.write_text(json.dumps(index), encoding="utf-8")
            installed = make_mod(home.paths, "reggy", code=None, parts=[{"kind": "agent", "side": "gateway", "path": "agents/r.json"}], files={"agents/r.json": json.dumps({"name": "r"})})
            (installed / ".floofy").mkdir(exist_ok=True)
            (installed / ".floofy" / "source.json").write_text(json.dumps({"source": "registry", "ref": "reggy@1.0.0", "registryKey": "reggy", "tier": "listed", "version": "1.0.0"}), encoding="utf-8")
        # the check: the plan names the candidate and its release notes; no audit row (read-only)
        status, body = app.call("updates.check")
        assert status == 200, body
        plan = body["json"]["plan"]
        assert plan == [{"id": "reggy", "key": "reggy", "installed": "1.0.0", "candidate": "1.1.0", "verdict": "expected", "why": "", "inRegistry": True, "update": True, "changelog": "https://forge.example/reggy/releases/1.1.0"}]
        assert any("release notes: https://forge.example/reggy/releases/1.1.0" in line for line in body["transcript"])
        assert app.audit() == []
        # the one-click update: `update reggy` — staged by default (a gateway runs), the same rows as the CLI
        status, body = app.call("mods.update", {"id": "reggy"})
        assert status == 200 and body["ok"] is True, body
        assert (app.paths.pending / "reggy").is_dir() and json.loads((app.paths.pending / "reggy" / "floofy.json").read_text(encoding="utf-8"))["version"] == "1.1.0"
        assert body["reloaded"] is False, "staged: the Loader does not reload until apply now"
        code, _, console = cli.cli("update", "reggy")
        assert code == 0, console.err.getvalue()
        app_rows, cli_rows = app.audit(), cli.audit()
        assert [r["op"] for r in app_rows] == [r["op"] for r in cli_rows] == ["agent-install", "install", "update"]
        for app_row, cli_row in zip(app_rows, cli_rows):
            left = json.dumps(comparable(app_row), sort_keys=True).replace(str(app.root), "<home>")
            right = json.dumps(comparable(cli_row), sort_keys=True).replace(str(cli.root), "<home>")
            assert left == right, (left, right)
        assert app_rows[-1]["actor"] == "app" and cli_rows[-1]["actor"] == "cli"
        # apply now lands the update; `now: true` on a second home does it in one step
        app.runtime.reload()
        assert json.loads((app.paths.mods / "reggy" / "floofy.json").read_text(encoding="utf-8"))["version"] == "1.1.0"
        status, body = app.call("updates.check")
        assert status == 200 and body["json"]["plan"][0]["update"] is False
    finally:
        server.shutdown()
        server.server_close()
