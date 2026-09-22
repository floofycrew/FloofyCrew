"""``floofy search/install/uninstall/enable/disable/update/apply/restore/verify/validate/new/dev`` (task 6.2).

Requirement 7.1, 7.6 (pending/ staging and --now), 11.3, 11.4 (per-file typed
confirmation, never by --yes), 11.7 (disclosure, confirmations, code mods land
disabled), 14.1 (scaffold), 14.2 (dev link). Temp homes, fake payloads, a fake
gateway for the "gateway running" cases; nothing touches a live install.
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import tarfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from floofy_core.audit import read_audit
from floofy_core.cli.console import Console
from floofy_core.cli.main import execute, run
from floofy_core.consent import write_consent
from floofy_core.datahome import DataHome
from floofy_core.modstore import read_enabled, read_source
from floofy_core.scaffold import KINDS, scaffold
from floofy_core.validator import validate_mod

from floofy_testing import EXAMPLES_DIR, FakeGateway, fake_payload


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_mod(root: Path, mod_id: str, *, parts: list[dict], files: dict[str, str], extra: dict | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    manifest = {
        "schema": 1,
        "id": mod_id,
        "name": mod_id,
        "version": "1.0.0",
        "description": "test mod",
        "authors": ["tests"],
        "license": "MIT",
        "kirocrew": {"version": ">=0.7.0 <0.9.0"},
        "dependsOn": {"floofycrew": ">=0.0.0"},
        "parts": parts,
        "files": [{"path": rel, "sha256": sha((root / rel).read_bytes())} for rel in sorted(files)],
    }
    manifest.update(extra or {})
    (root / "floofy.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return root


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FLOOFY_NO_ADAPTERS", "1")
    monkeypatch.delenv("KIROCREW_HOME", raising=False)
    monkeypatch.setenv("KIRO_HOME", str(tmp_path / "kiro"))  # the agent seam lands in KIRO_HOME; never the live ~/.kiro
    payload_root = tmp_path / "payload"
    fake_payload(payload_root, "0.7.0", build="0.7.0.5")
    home = tmp_path / "home"
    data = DataHome.for_host_home(home).ensure()
    write_consent(data.consent, by="tests", how="test")

    class Env:
        root = payload_root
        host_home = home
        paths = data
        work = tmp_path / "work"

        def argv(self, *args: str) -> list[str]:
            return ["--home", str(home), "--root", str(payload_root), *args]

        def run(self, *args: str, **kw):
            return run(self.argv(*args), non_interactive=True, actor="test", **kw)

        def hook_mod(self, mod_id: str = "hooky") -> Path:
            return write_mod(self.work / mod_id, mod_id, parts=[{"kind": "python-hook", "side": "gateway", "path": "hook/", "module": "hook"}], files={"hook/__init__.py": "def activate(ctx):\n    pass\n"})

        def spa_mod(self, mod_id: str = "spaish") -> Path:
            return write_mod(self.work / mod_id, mod_id, parts=[{"kind": "spa", "side": "spa", "path": "spa/main.js"}], files={"spa/main.js": "export default function activate() {}\n"})

    return Env()


# --- install: disclosure, confirmations, staging --------------------------------------------------------


def test_install_refuses_without_consent(env):
    env.paths.consent.unlink()
    result = env.run("--yes", "install", str(env.hook_mod()))
    assert result.exit == 3 and "consent" in result.stderr


def test_install_discloses_and_needs_a_yes_for_code_kinds_then_lands_enabled(env):
    mod = env.hook_mod()
    declined = env.run("install", str(mod))  # non-interactive without --yes
    assert declined.exit == 1 and "explicit yes" in declined.stderr
    assert not (env.paths.mods / "hooky").exists()
    assert read_audit(env.paths.audit)[-1]["result"] == "declined"
    result = env.run("--yes", "install", str(mod))
    assert result.exit == 0, result.stderr
    disclosure = result.json["disclosure"]
    assert disclosure["parts"][0]["kind"] == "python-hook" and "Loader" in disclosure["parts"][0]["seam"] and disclosure["modifiesPayload"] is False
    assert disclosure["confirmKinds"] == ["python-hook"] and disclosure["codeParts"] is True
    assert "python-hook/gateway hook/ -> Loader" in result.stdout and "lands ENABLED" in result.stdout
    assert (env.paths.mods / "hooky" / "hook" / "__init__.py").is_file()
    assert read_enabled(env.paths.enabled) == {"hooky": True}, "a confirmed install lands enabled (Requirement 11.7)"
    assert read_source(env.paths.mods / "hooky")["source"] == "path"
    row = read_audit(env.paths.audit)[-1]
    assert row["op"] == "install" and row["mod"] == "hooky" and row["version"] == "1.0.0" and row["result"] == "ok" and row["consentRef"] and row["payload"]
    assert row["actor"] == "test"


def test_install_disabled_flag_lands_the_mod_switched_off(env):
    mod = env.hook_mod()
    result = env.run("--yes", "install", str(mod), "--disabled")
    assert result.exit == 0, result.stderr
    assert read_enabled(env.paths.enabled) == {"hooky": False}, "--disabled is the opt-out"
    assert "landed switched off" in result.stdout and "floofy enable hooky" in result.stdout


def test_zero_code_mod_is_enabled_and_needs_no_confirmation(env):
    mod = write_mod(env.work / "agenty", "agenty", parts=[{"kind": "agent", "side": "gateway", "path": "agents/a.json"}], files={"agents/a.json": json.dumps({"name": "a"})})
    result = env.run("install", str(mod))
    assert result.exit == 0, result.stderr
    assert read_enabled(env.paths.enabled) == {"agenty": True}
    assert result.json["disclosure"]["confirmKinds"] == [] and result.json["disclosure"]["codeParts"] is False


def test_install_enable_flag_and_disclosure_of_network_and_patch(env):
    descriptor = {"schema": 1, "target": "kiro_crew/static/dist/index.html", "appliesTo": ">=0.7.0 <0.9.0", "ops": [{"op": "append-head", "content": "<meta name=\"floofy-test\" content=\"1\">", "marker": "floofy-test"}]}
    mod = write_mod(
        env.work / "patchy",
        "patchy",
        parts=[{"kind": "patch", "side": "spa", "path": "patches/p.json"}, {"kind": "spa", "side": "spa", "path": "spa/main.js"}],
        files={"patches/p.json": json.dumps(descriptor), "spa/main.js": "fetch('https://api.example.com/x'); fetch('http://plain.example.net/y');\n"},
        extra={"network": {"hosts": ["api.example.com"], "credentials": True}},
    )
    result = env.run("--yes", "install", str(mod), "--enable")
    assert result.exit == 0, result.stderr
    disclosure = result.json["disclosure"]
    assert disclosure["modifiesPayload"] is True and disclosure["network"] == {"hosts": ["api.example.com"], "credentials": True}
    assert {f["code"] for f in disclosure["flags"]} == {"PlaintextNetwork"}
    assert "MODIFIES PAYLOAD FILES" in result.stdout and "api.example.com" in result.stdout and "PlaintextNetwork" in result.stdout
    assert read_enabled(env.paths.enabled) == {"patchy": True}
    # the Patcher re-applied to the fake payload: the meta tag is in index.html and a backup exists
    shell = (env.root / "kiro_crew" / "static" / "dist" / "index.html").read_text(encoding="utf-8")
    assert 'name="floofy-test"' in shell and '/apps/floofycrew/ui/host.mjs' in shell
    assert (env.root / "kiro_crew" / "static" / "dist" / "index.html.floofybak").is_file()
    disabled = env.run("disable", "patchy")
    assert disabled.exit == 0 and read_enabled(env.paths.enabled) == {"patchy": False}
    shell = (env.root / "kiro_crew" / "static" / "dist" / "index.html").read_text(encoding="utf-8")
    assert 'name="floofy-test"' not in shell, "disable re-applied the (now empty) set: the payload is back to vanilla"
    removed = env.run("--yes", "uninstall", "patchy")
    assert removed.exit == 0 and not (env.paths.mods / "patchy").exists() and read_enabled(env.paths.enabled) == {}
    assert not (env.root / "kiro_crew" / "static" / "dist" / "index.html.floofybak").exists()


def test_governance_altering_target_needs_the_typed_path_not_yes(env):
    mod = write_mod(env.work / "govy", "govy", parts=[{"kind": "agent", "side": "gateway", "path": "agents/a.json"}], files={"agents/a.json": json.dumps({"name": "a"}), "security_policy.json": "{}"})
    with_yes = env.run("--yes", "install", str(mod))
    assert with_yes.exit == 1 and "governance-altering target security_policy.json was not confirmed" in with_yes.stderr
    assert not (env.paths.mods / "govy").exists()
    typed_wrong = Console(input_fn=lambda prompt: "yes", non_interactive=False, assume_yes=True)
    code, _ = execute(env.argv("--yes", "install", str(mod)), typed_wrong)
    assert code == 1 and not (env.paths.mods / "govy").exists()
    typed_right = Console(input_fn=lambda prompt: "security_policy.json" if "GOVERNANCE" in prompt else "y", non_interactive=False)
    code, result = execute(env.argv("install", str(mod)), typed_right)
    assert code == 0, typed_right.transcript
    assert (env.paths.mods / "govy").is_dir()
    rows = read_audit(env.paths.audit)
    confirm = [r for r in rows if r["op"] == "governance-target-confirm"]
    assert confirm and confirm[-1]["files"] == ["security_policy.json"] and confirm[-1]["detail"] == "typed at the prompt"
    assert "GovernanceAltering" in rows[-1]["governanceFlags"]
    # automation: the repeatable flag, recorded as pre-confirmed
    shutil.rmtree(env.paths.mods / "govy")
    flagged = env.run("--yes", "install", str(mod), "--confirm-governance-target", "security_policy.json")
    assert flagged.exit == 0, flagged.stderr
    tail = read_audit(env.paths.audit)[-3:]
    assert [r["op"] for r in tail] == ["governance-target-confirm", "agent-install", "install"], "the confirmation precedes the seam handler's row and the command's"
    assert tail[0]["detail"] == "pre-confirmed by flag" and tail[0]["files"] == ["security_policy.json"]


def test_install_from_archives_and_validation_errors_refuse(env, tmp_path: Path):
    mod = env.hook_mod("archy")
    zipped = tmp_path / "archy.zip"
    with zipfile.ZipFile(zipped, "w") as archive:
        for path in mod.rglob("*"):
            if path.is_file():
                archive.write(path, f"archy/{path.relative_to(mod).as_posix()}")
    result = env.run("--yes", "install", str(zipped))
    assert result.exit == 0, result.stderr
    assert result.json["source"]["kind"] == "archive" and result.json["source"]["sha256"] == sha(zipped.read_bytes())
    tarred = tmp_path / "archy.tar.gz"
    with tarfile.open(tarred, "w:gz") as archive:
        archive.add(mod, arcname="archy")
    again = env.run("--yes", "install", str(tarred), "--now")
    assert again.exit == 0 and again.json["placed"]["how"] == "installed"
    # a tampered file: MissingFiles → refused
    broken = env.hook_mod("brokey")
    (broken / "hook" / "__init__.py").write_text("tampered\n", encoding="utf-8")
    refused = env.run("--yes", "install", str(broken))
    assert refused.exit == 1 and "validate" in refused.stderr and not (env.paths.mods / "brokey").exists()
    # an unsafe archive (absolute path) is refused before extraction
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as archive:
        archive.writestr("/etc/passwd", "x")
    assert env.run("--yes", "install", str(evil)).exit == 1


class _Serve(BaseHTTPRequestHandler):
    payload: bytes = b""

    def log_message(self, *_args) -> None:
        return None

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)


def test_install_from_url_loopback_plaintext_ok_remote_plaintext_refused_hash_checked(env, tmp_path: Path):
    mod = env.hook_mod("urly")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in mod.rglob("*"):
            if path.is_file():
                archive.write(path, f"urly/{path.relative_to(mod).as_posix()}")
    _Serve.payload = buffer.getvalue()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Serve)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/urly.zip"
        bad_hash = env.run("--yes", "install", url, "--sha256", "0" * 64)
        assert bad_hash.exit == 1 and "sha256 mismatch" in bad_hash.stderr
        good = env.run("--yes", "install", url, "--sha256", sha(_Serve.payload))
        assert good.exit == 0, good.stderr
        assert good.json["source"]["kind"] == "url" and (env.paths.mods / "urly").is_dir()
    finally:
        server.shutdown()
        server.server_close()
    plaintext = env.run("--yes", "install", "http://example.com/mod.zip")
    assert plaintext.exit == 1 and "must use https" in plaintext.stderr


def test_registry_install_search_and_update_from_the_cache(env, tmp_path: Path):
    mod = env.hook_mod("reggy")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in mod.rglob("*"):
            if path.is_file():
                archive.write(path, f"reggy/{path.relative_to(mod).as_posix()}")
    _Serve.payload = buffer.getvalue()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Serve)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/reggy-1.0.0.zip"
        index = {
            "schema": 1,
            "source": "test-source",
            "generatedAt": "2026-09-19T00:00:00Z",
            "mods": [
                {
                    "id": "reggy",
                    "name": "Reggy",
                    "description": "a registry mod",
                    "authors": ["tests"],
                    "tags": ["test"],
                    "versions": [
                        {"version": "0.9.0", "kirocrew": ">=0.7.0 <0.9.0", "editions": ["internal", "external"], "compat": {"0.7.0.5": "tested"}, "files": [{"url": url, "sha256": sha(_Serve.payload)}]},
                        {"version": "1.0.0", "kirocrew": ">=0.7.0 <0.9.0", "editions": ["internal", "external"], "compat": {"0.7.0.5": "expected"}, "files": [{"url": url, "sha256": sha(_Serve.payload)}]},
                        {"version": "2.0.0", "kirocrew": ">=0.7.0 <0.9.0", "editions": ["internal", "external"], "compat": {"0.7.0.5": "broken"}, "files": [{"url": url, "sha256": sha(_Serve.payload)}]},
                        {"version": "3.0.0", "kirocrew": ">=0.9.0", "compat": {}, "files": [{"url": url, "sha256": sha(_Serve.payload)}]},
                    ],
                },
                {"id": "other", "name": "Other", "description": "unrelated", "authors": [], "tags": [], "versions": []},
            ],
        }
        env.paths.index_cache.write_text(json.dumps(index), encoding="utf-8")
        found = env.run("--json", "search", "registry")
        assert found.exit == 0 and [m["id"] for m in found.json["mods"]] == ["reggy"]
        assert found.json["mods"][0]["worksHere"] == "0.9.0" and found.json["mods"][0]["verdict"] == "tested", "tested beats a newer expected version; broken and out-of-range never (Requirement 9.2)"
        picked = env.run("--yes", "install", "reggy")
        assert picked.exit == 0, picked.stderr
        assert picked.json["source"]["kind"] == "registry" and picked.json["source"]["ref"] == "reggy@0.9.0"
        assert read_source(env.paths.mods / "reggy")["registryKey"] == "reggy"
        exact = env.run("--yes", "install", "reggy@1.0.0")
        assert exact.exit == 0 and exact.json["source"]["ref"] == "reggy@1.0.0", "an explicit version is honoured"
        check = env.run("--json", "update", "--all", "--check")
        assert check.exit == 0 and check.json["plan"][0]["update"] is False, "installed 1.0.0 (the archive's manifest) is newer than the best candidate 0.9.0"
        # 2.0.0 becomes tested → it is the newest known-to-work version → update
        index["mods"][0]["versions"][2]["compat"]["0.7.0.5"] = "tested"
        env.paths.index_cache.write_text(json.dumps(index), encoding="utf-8")
        plan = env.run("--json", "update", "--all", "--check")
        assert plan.json["plan"][0]["candidate"] == "2.0.0" and plan.json["plan"][0]["update"] is True
        env.run("enable", "reggy")
        applied = env.run("--yes", "update", "--all")
        assert applied.exit == 0, applied.stderr
        assert applied.json["applied"][0]["version"] == "2.0.0"
        assert read_enabled(env.paths.enabled)["reggy"] is True, "an update keeps the enabled state"
    finally:
        server.shutdown()
        server.server_close()


# --- staging with a running gateway ------------------------------------------------------------------


def test_install_stages_into_pending_while_a_gateway_runs_and_now_installs_directly(env, monkeypatch: pytest.MonkeyPatch):
    dist = env.root / "kiro_crew" / "static" / "dist"
    # socket mode as on the live host: the socket answers the bare liveness bit; the version comes only through the loopback retry (task 10.8)
    with FakeGateway(dist, "0.7.0.5", socket_dir=env.host_home) as gw:
        assert gw.socket_path == env.host_home / f"dashboard-{gw.port}.sock"
        doc = env.run("--json", "doctor")
        gateway = doc.json["host"]["gateway"]
        assert gateway["running"] is True and gateway["servedVersion"] == "0.7.0.5" and gateway["via"] == "loopback-tcp" and gateway["endpoint"] == str(gw.socket_path)
        assert [p["transport"] for p in gw.probes()][:2] == ["unix", "tcp"] and gw.probes("tcp")[0]["host"] == f"127.0.0.1:{gw.port}"
        assert doc.json["host"]["payloads"][0]["dormant"] is False, "the served version names this payload"
        assert doc.json["drift"]["servedVersion"] == "0.7.0.5" and doc.json["drift"]["payloads"][0]["dormant"] is False
        staged = env.run("--yes", "install", str(env.spa_mod("stagey")))
        assert staged.exit == 0, staged.stderr
        assert staged.json["placed"]["how"] == "staged" and (env.paths.pending / "stagey" / "floofy.json").is_file()
        assert "restart the gateway to apply" in staged.stdout
        assert read_enabled(env.paths.enabled) == {"stagey": True}, "staged installs land enabled too: the flag is read when the stage applies"
        status = env.run("--json", "status")
        assert status.json["pending"] == ["stagey"]
        # --now installs directly (the reload POST fails against the fake gateway: reported, never fatal)
        direct = env.run("--yes", "install", str(env.spa_mod("nowy")), "--now")
        assert direct.exit == 0, direct.stderr
        assert direct.json["placed"]["how"] == "installed" and (env.paths.mods / "nowy").is_dir()
        assert direct.json["reload"]["reloaded"] is False
        # uninstall of a code mod stages a .remove marker while the gateway runs
        env.run("--yes", "uninstall", "nowy")
        assert (env.paths.pending / "nowy.remove").is_file() and (env.paths.mods / "nowy").is_dir()
        assert "nowy" not in read_enabled(env.paths.enabled)


# --- dev, new, validate, apply, restore, verify -----------------------------------------------------------


def test_dev_links_a_checkout_and_the_loader_accepts_the_symlinked_dir(env):
    mod = env.hook_mod("devy")
    result = env.run("dev", str(mod))
    assert result.exit == 0, result.stderr
    link = env.paths.mods / "devy"
    assert link.is_symlink() and link.resolve() == mod.resolve() and read_enabled(env.paths.enabled) == {"devy": True}
    # the Loader's per-mod data dir accepts the symlinked mod dir (floofy dev must not break config/log storage)
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "loader-app"))
    from floofy_loader.storage import mod_data_dir

    data_dir = mod_data_dir(env.paths.data_home, "devy", payload_root=env.root)
    assert data_dir.resolve() == (mod.resolve() / ".floofy")
    listed = env.run("--json", "status")
    assert listed.json["mods"][0]["link"] is True
    unlinked = env.run("dev", str(mod), "--unlink")
    assert unlinked.exit == 0 and not link.exists() and (mod / "floofy.json").is_file(), "the checkout is untouched"


@pytest.mark.parametrize("kind", KINDS)
def test_new_scaffolds_a_valid_mod_for_every_kind(tmp_path: Path, kind: str):
    root = scaffold(kind, tmp_path, author="tests")
    report = validate_mod(root)
    assert report.ok, report.format()
    assert (root / "README.md").is_file() and (root / "LICENSE").is_file() and (root / "tests" / "smoke_test.py").is_file()
    assert (root / ".github" / "workflows" / "floofy-validate.yml").is_file()
    manifest = json.loads((root / "floofy.json").read_text(encoding="utf-8"))
    assert manifest["parts"][0]["kind"] == kind and manifest["id"] == f"my-{kind}"
    assert {f["path"] for f in manifest["files"]} >= {"README.md", "LICENSE", "tests/smoke_test.py"}


def test_new_command_and_validate_command(env, tmp_path: Path):
    made = env.run("new", "spa", "--dir", str(tmp_path / "out"), "--id", "shiny-spa", "--author", "tests")
    assert made.exit == 0 and (tmp_path / "out" / "shiny-spa" / "floofy.json").is_file()
    again = env.run("new", "spa", "--dir", str(tmp_path / "out"), "--id", "shiny-spa")
    assert again.exit == 1 and "already exists" in again.stderr
    bad_id = env.run("new", "spa", "--dir", str(tmp_path / "out"), "--id", "Bad Id")
    assert bad_id.exit == 1
    valid = env.run("--json", "validate", str(tmp_path / "out" / "shiny-spa"))
    assert valid.exit == 0 and valid.json["ok"] is True
    text = env.run("validate", str(EXAMPLES_DIR / "theme"))
    assert text.exit == 0 and "OK:" in text.stdout
    missing = env.run("validate", str(tmp_path / "nope"))
    assert missing.exit == 2


def test_apply_restore_verify_and_apply_if_changed(env):
    descriptor = {"schema": 1, "target": "kiro_crew/static/dist/index.html", "appliesTo": ">=0.7.0 <0.9.0", "ops": [{"op": "append-head", "content": "<meta name=\"floofy-apply\" content=\"1\">", "marker": "floofy-apply"}]}
    mod = write_mod(env.work / "applyme", "applyme", parts=[{"kind": "patch", "side": "spa", "path": "patches/p.json"}], files={"patches/p.json": json.dumps(descriptor)})
    assert env.run("--yes", "install", str(mod)).exit == 0
    shell = env.root / "kiro_crew" / "static" / "dist" / "index.html"
    assert 'name="floofy-apply"' in shell.read_text(encoding="utf-8")
    applied = env.run("--json", "apply", "--no-verify")
    assert applied.exit == 0 and applied.json["ok"] is True and applied.json["payloads"][0]["unchanged"]
    verified = env.run("--json", "verify")
    assert verified.exit == 0 and verified.json["payloads"][0]["status"] == "no-gateway"
    changed = env.run("--json", "apply", "--if-changed", "--no-verify")
    assert changed.exit == 0 and changed.json["change"]["firstRun"] is True and changed.json["reapplied"] is True
    quiet = env.run("--json", "apply", "--if-changed", "--no-verify")
    assert quiet.exit == 0 and quiet.json["change"]["changed"] is False and quiet.json["reapplied"] is False
    assert env.paths.host_state.is_file()
    restored = env.run("--json", "restore", "--all")
    assert restored.exit == 0 and 'name="floofy-apply"' not in shell.read_text(encoding="utf-8")
    assert not shell.with_name("index.html.floofybak").exists()
    ops = [r["op"] for r in read_audit(env.paths.audit)]
    assert "apply-if-changed" in ops and ops[-1] == "restore-all"



# --- the early-activation list (Requirement 3.2) ---------------------------------------------------------


def test_early_list_follows_install_enable_disable_uninstall(env):
    """``early.json`` lists exactly the ENABLED mods' ``early: true`` python-hook parts, and is absent otherwise."""
    early = write_mod(
        env.work / "earlybird",
        "earlybird",
        parts=[{"kind": "python-hook", "side": "gateway", "path": "hook/", "module": "hook", "early": True}],
        files={"hook/__init__.py": "def early(ctx):\n    pass\n\ndef activate(ctx):\n    pass\n"},
    )
    plain = env.hook_mod()  # no early part
    assert env.run("--yes", "install", str(early), "--disabled").exit == 0
    assert env.run("--yes", "install", str(plain), "--disabled").exit == 0
    assert not env.paths.early.exists(), "installed switched off (--disabled): nothing is early yet"

    assert env.run("enable", "earlybird").exit == 0
    document = json.loads(env.paths.early.read_text(encoding="utf-8"))
    assert document == {"mods": [{"id": "earlybird", "path": str(env.paths.mods / "earlybird"), "module": "hook", "part": 0}]}

    assert env.run("enable", "hooky").exit == 0
    assert json.loads(env.paths.early.read_text(encoding="utf-8"))["mods"] == document["mods"], "a mod without an early part never appears"

    assert env.run("disable", "earlybird").exit == 0
    assert not env.paths.early.exists(), "no early part enabled: the file is removed, the shim stays a no-op"

    assert env.run("enable", "earlybird").exit == 0 and env.paths.early.exists()
    assert env.run("--yes", "uninstall", "earlybird").exit == 0
    assert not env.paths.early.exists()
