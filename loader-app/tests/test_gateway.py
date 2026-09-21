"""Loader tests against ``kirocrew gateway --test-mode`` (Requirement 13.3) on a payload COPY with scratch homes.

Skipped unless a payload copy exists (``.scratch/payload-0.7.0.5`` or
``FLOOFY_SCRATCH_PAYLOAD``). One gateway serves cases 1–3 and 5–8; case 4 (the
Loader itself fails → vanilla boot) starts a second, short-lived gateway with
``FLOOFY_LOADER_SELFTEST_FAIL=1`` after the first one stopped. Nothing touches
the live install: ``KIROCREW_HOME`` / ``KIRO_HOME`` live under
``.scratch/loader-tests/`` and the Loader runs with ``FLOOFY_NO_ADAPTERS=1``.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from floofy_core import API_VERSION
from floofy_loader.consent import REASON_CONSENT_REQUIRED, write_consent
from floofy_loader.paths import FloofyPaths
from floofy_loader.storage import RUNTIME_DIR

from loader_testing import EXAMPLES_DIR, ScratchGateway, ShellGuard, build_loader_app, fresh_scratch, scratch_payload

pytestmark = pytest.mark.skipif(scratch_payload() is None, reason="no payload copy under .scratch (set FLOOFY_SCRATCH_PAYLOAD)")

API = "/api/apps/floofycrew"

HOOKER = '''
def activate(ctx):
    def after(result, request, *args, **kwargs):
        try:
            result.headers["X-Floofy-Hooked"] = ctx.mod_id
        except Exception as exc:  # noqa: BLE001
            ctx.log.warning("could not stamp the response: %s", exc)
        return result
    ctx.hooks.after("route:GET /api/health", after)
    ctx.state["marker"] = f"hooked on {ctx.host.version} ({ctx.host.edition})"
    ctx.state["targets"] = [r.target for r in ctx.hooks.registrations()]
    ctx.log.info("gw-hooker active")

def deactivate(ctx):
    ctx.state["deactivated"] = True
'''

RAISER = '''
def activate(ctx):
    raise RuntimeError("gw-raiser explodes on purpose")
'''

NETMOD = '''
import http.server, threading

class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        return None
    def do_GET(self):
        body = b'{"local": true}'
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

def activate(ctx):
    from floofy_loader.net import FloofyNetworkDenied
    ctx.state["local_policy"] = ctx.http.policy.allows("http://127.0.0.1:1234/x")
    try:
        ctx.http.fetch("http://example.com/")
    except FloofyNetworkDenied as exc:
        ctx.state["remote_denied"] = exc.code
    try:
        ctx.http.fetch("https://undeclared.example.net/")
    except FloofyNetworkDenied as exc:
        ctx.state["undeclared_denied"] = exc.code
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        reply = ctx.http.fetch(f"http://127.0.0.1:{server.server_address[1]}/ok")
        ctx.state["local_status"] = reply.status
        ctx.state["local_body"] = reply.json()
    finally:
        server.shutdown(); server.server_close()
    ctx.state["declared"] = list(ctx.http.declared_hosts)
'''


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_mod(paths: FloofyPaths, mod_id: str, *, code: str | None = None, parts: list[dict] | None = None, files: dict[str, str] | None = None, extra: dict | None = None) -> Path:
    root = paths.mods / mod_id
    root.mkdir(parents=True, exist_ok=True)
    shipped = dict(files or {})
    part_list = list(parts or [])
    if code is not None:
        shipped["hook/__init__.py"] = code
        part_list.append({"kind": "python-hook", "side": "gateway", "path": "hook/", "module": "hook"})
    for rel, text in shipped.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    manifest = {
        "schema": 1,
        "id": mod_id,
        "name": mod_id,
        "version": "1.0.0",
        "description": "gateway test mod",
        "authors": ["tests"],
        "license": "MIT",
        "kirocrew": {"version": ">=0.7.0 <0.9.0"},
        "dependsOn": {"floofycrew": ">=0.0.0"},
        "parts": part_list,
        "files": [{"path": rel, "sha256": sha(root / rel)} for rel in sorted(shipped)],
    }
    manifest.update(extra or {})
    (root / "floofy.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return root


def stage_example_python_hook(paths: FloofyPaths) -> None:
    """The shipped example, with its framework range widened to this pre-1.0 checkout."""
    root = paths.mods / "example-python-hook"
    shutil.copytree(EXAMPLES_DIR / "python-hook", root)
    manifest = json.loads((root / "floofy.json").read_text(encoding="utf-8"))
    manifest["dependsOn"] = {"floofycrew": ">=0.0.0"}
    (root / "floofy.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def gateway():
    payload = scratch_payload()
    assert payload is not None
    scratch = fresh_scratch("gateway")
    app_dir = build_loader_app(scratch / "app" / "floofycrew")
    gw = ScratchGateway(payload, scratch)
    paths = FloofyPaths(gw.data_home).ensure()
    # the Loader patches the copy's index.html (loader tag for gw-spa); the guard puts every byte back at the end
    guard = ShellGuard(payload, gw.data_home)

    stage_example_python_hook(paths)
    write_mod(paths, "gw-hooker", code=HOOKER)
    write_mod(paths, "gw-raiser", code=RAISER)
    write_mod(paths, "gw-netmod", code=NETMOD, extra={"network": {"hosts": ["api.example.com"], "credentials": False}})
    write_mod(paths, "gw-theme", parts=[{"kind": "theme", "side": "gateway", "path": "theme/theme.json"}], files={"theme/theme.json": json.dumps({"id": "gw-theme", "name": "GW theme", "version": "1.0.0"})})
    write_mod(paths, "gw-spa", parts=[{"kind": "spa", "side": "spa", "path": "spa/main.js"}], files={"spa/main.js": "import './lib/util.js';\nwindow.__gw_spa = 1;\n", "spa/lib/util.js": "export const u = 1;\n"})
    paths.enabled.write_text(json.dumps({m: True for m in ("example-python-hook", "gw-hooker", "gw-raiser", "gw-netmod", "gw-theme", "gw-spa")}), encoding="utf-8")

    # install the Loader app through the host CLI; a fresh home lands it disabled and refuses enable until granted
    installed = gw.cli("app", "install", str(app_dir))
    assert installed.returncode == 0 and "installed floofycrew" in installed.stdout, installed.stdout + installed.stderr
    meta = json.loads((gw.home / "apps" / "floofycrew" / "installed.json").read_text(encoding="utf-8"))
    assert meta["enabled"] is False
    refused = gw.cli("app", "enable", "floofycrew")
    assert refused.returncode != 0 and "blocked by execution policy" in (refused.stdout + refused.stderr)
    config_path = gw.home / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    config.setdefault("agent", {})["apps_trusted"] = ["floofycrew"]
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    enabled = gw.cli("app", "enable", "floofycrew")
    assert enabled.returncode == 0 and "enabled floofycrew" in enabled.stdout, enabled.stdout + enabled.stderr
    # governance (information only): the home-tier policy closes theme_install
    (gw.home / "security_policy.json").write_text(json.dumps({"version": 1, "boot": {"fail_closed": True}, "capabilities": {"theme_install": {"enabled": False}}}, indent=2) + "\n", encoding="utf-8")

    gw.start()
    try:
        yield gw
    finally:
        gw.stop()
        guard.check()


def state_of(client) -> dict:
    reply = client.get(f"{API}/state")
    assert reply.status == 200, (reply.status, reply.body[:300])
    return reply.json()


def test_1_inert_without_consent(gateway: ScratchGateway):
    client = gateway.client()
    assert client.get("/api/health", auth=False).json()["ok"] is True
    state = state_of(client)
    assert state["loader"] == "inert" and state["consent"]["required"] is True and state["consent"]["status"] == "required"
    assert state["active"] == [] and state["unofficial"] is True and state["api_version"] == API_VERSION
    assert state["host"]["version"] == "0.7.0.5" and state["host"]["edition"] == "internal"
    for mod_id in ("example-python-hook", "gw-hooker", "gw-netmod", "gw-theme", "gw-spa", "gw-raiser"):
        assert state["mods"][mod_id]["active"] is False and state["mods"][mod_id]["reason"] == REASON_CONSENT_REQUIRED, mod_id
    assert state["hooks"] == [] and not (gateway.data_home / "spa" / "gw-spa").exists()
    health = client.get(f"{API}/health").json()
    assert health["loader"] == "inert" and health["unofficial"] is True
    assert client.get("/api/health", auth=False).headers.get("x-floofy-hooked") is None


def test_2_and_3_consent_activates_mods_and_a_raising_mod_is_isolated(gateway: ScratchGateway):
    client = gateway.client()
    write_consent(FloofyPaths(gateway.data_home).consent, by="gateway-test")
    reply = client.post(f"{API}/reload")
    assert reply.status == 200 and reply.json()["ok"] is True
    state = state_of(client)
    assert state["loader"] == "ok" and state["consent"]["required"] is False
    assert set(state["active"]) == {"example-python-hook", "gw-hooker", "gw-netmod", "gw-theme", "gw-spa"}
    assert state["mods"]["gw-hooker"]["exports"]["marker"] == "hooked on 0.7.0.5 (internal)"
    assert state["mods"]["gw-hooker"]["exports"]["targets"] == ["route:GET /api/health"]
    assert state["mods"]["example-python-hook"]["active"] is True and state["mods"]["example-python-hook"]["parts"][0]["status"] == "active"
    assert {(h["mod"], h["target"]) for h in state["hooks"]} == {("gw-hooker", "route:GET /api/health"), ("example-python-hook", "kiro_crew.dashboard.handlers.core:index")}
    # (3) the raising mod is the only casualty and the gateway stays healthy
    raiser = state["mods"]["gw-raiser"]
    assert raiser["active"] is False and raiser["reason"] == "Error" and "gw-raiser explodes on purpose" in raiser["detail"]
    assert raiser["parts"][0]["status"] == "error" and any("Traceback" in e for e in raiser["errors"])
    assert client.get("/api/health", auth=False).status == 200
    assert json.loads((gateway.data_home / "loader-state.json").read_text(encoding="utf-8"))["active"] == state["active"]


def test_5_hook_unwind_restores_the_route(gateway: ScratchGateway):
    client = gateway.client()
    hooked = client.get("/api/health", auth=False)
    assert hooked.status == 200 and hooked.headers.get("x-floofy-hooked") == "gw-hooker", hooked.headers
    reply = client.post(f"{API}/mods/gw-hooker/fault", {"message": "test-induced fault", "stack": "n/a", "source": "test"})
    assert reply.status == 200 and reply.json()["faulted"] is True
    plain = client.get("/api/health", auth=False)
    assert plain.status == 200 and plain.headers.get("x-floofy-hooked") is None, "the original handler is back"
    state = state_of(client)
    assert state["mods"]["gw-hooker"]["reason"] == "Error" and "test-induced fault" in state["mods"]["gw-hooker"]["detail"]
    assert all(h["mod"] != "gw-hooker" for h in state["hooks"]) and state["mods"]["example-python-hook"]["active"] is True
    assert state["faults"][0]["mod"] == "gw-hooker" and state["events"]["history"][-1]["event"] == "mod.faulted"


def test_6_governance_is_a_warning_never_a_reason(gateway: ScratchGateway):
    state = state_of(gateway.client())
    theme = state["mods"]["gw-theme"]
    assert theme["active"] is True and theme["reason"] is None
    assert "ThemeInstallClosed" in {g["code"] for g in theme["governance"]}
    assert state["governanceSnapshot"]["capabilities"]["theme_install"] is False
    assert state["governanceSnapshot"]["appsTrusted"] == ["floofycrew"]
    assert "Governance" not in state["reasons"]
    for mod in state["mods"].values():
        assert mod["reason"] in (None, *state["reasons"])


def test_7_ctx_http_rules_inside_the_gateway(gateway: ScratchGateway):
    state = state_of(gateway.client())
    exports = state["mods"]["gw-netmod"]["exports"]
    assert exports["local_policy"] is True and exports["local_status"] == 200 and exports["local_body"] == {"local": True}
    assert exports["remote_denied"] == "PlaintextDenied" and exports["undeclared_denied"] == "UndeclaredHost"
    assert exports["declared"] == ["api.example.com"]
    rows = [json.loads(line) for line in (gateway.data_home / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    denied = [(r["mod"], r["code"], r["url"]) for r in rows if r["op"] == "net-denied"]
    assert ("gw-netmod", "PlaintextDenied", "http://example.com/") in denied and ("gw-netmod", "UndeclaredHost", "https://undeclared.example.net/") in denied
    assert state["network"]["gw-netmod"]["declaredHosts"] == ["api.example.com"] and state["network"]["gw-netmod"]["denials"] >= 2


def test_8_spa_route_serves_an_active_mods_files(gateway: ScratchGateway):
    client = gateway.client()
    main = client.get(f"{API}/spa/gw-spa/main.js")
    assert main.status == 200 and main.headers["content-type"].startswith("text/javascript") and main.headers["cache-control"] == "no-cache"
    assert b"__gw_spa" in main.body
    nested = client.get(f"{API}/spa/gw-spa/lib/util.js")
    assert nested.status == 200 and nested.body == b"export const u = 1;\n"
    assert client.get(f"{API}/spa/gw-spa/lib/../floofy.json").status in (400, 404)
    assert client.get(f"{API}/spa/gw-raiser/hook/__init__.py").status == 404, "an inactive mod serves nothing"
    assert client.get(f"{API}/spa/gw-spa/missing.js").status == 404


def test_9_loader_tag_is_patched_into_the_served_shell(gateway: ScratchGateway):
    """A spa mod is active, so the Loader (a re-apply trigger) baked the spike-1.3 loader tag into the copy's shell."""
    client = gateway.client()
    state = state_of(client)
    assert state["patches"].get("ran") is True, state["patches"]
    applied = [a for p in state["patches"]["report"]["payloads"] for a in p["applied"]]
    assert any("floofycrew#boot" in a for a in applied), applied
    shell = client.get("/", auth=False).body.decode("utf-8")
    tag = '<script type="module" src="/apps/floofycrew/ui/host.mjs" id="floofy-host"></script>'
    assert tag in shell and shell.index(tag) < shell.index('src="/assets/main-')
    served = client.get("/apps/floofycrew/ui/host.mjs", auth=False)
    assert served.status == 200 and b"createHost" in served.body and served.headers["cache-control"] == "no-cache"


def test_9b_host_registry_operator_row_round_trips_through_put(gateway: ScratchGateway):
    """Requirement 8.6: FloofyCrew's operator row lands via PUT /api/apps/registries, respects the pinned ids, and is removable."""
    from floofy_core.gateway import GatewaySession
    from floofy_core.hostregistry import REGISTRIES_ROUTE, HostRegistryError, HostRegistryRow, apply_row, current_rows, remove_row

    session = GatewaySession(gateway.home, int(gateway.ready["port"]), token=str(gateway.ready["token"]))
    before = current_rows(gateway.home, session)
    assert before["how"] == "gateway" and before["registries"] == []
    pinned_ids = [p["name"] for p in before["pinned"]]
    row = HostRegistryRow("floofycrew-test", "https://git.example/floofycrew/floofycrew-registry", "main")
    outcome = apply_row(gateway.home, row, session=session, pinned_ids=pinned_ids)
    assert outcome["how"] == "gateway" and outcome["written"] is True and outcome["rows"][-1]["trust"] == "index"
    listed = session.get(REGISTRIES_ROUTE).json()
    assert [r["name"] for r in listed["registries"]] == ["floofycrew-test"] and listed["registries"][0]["repo"] == row.repo and listed["registries"][0]["branch"] == "main"
    assert [p["name"] for p in listed["pinned"]] == pinned_ids, "the build's pinned rows are reported separately and untouched"
    on_disk = json.loads((gateway.home / "config.json").read_text(encoding="utf-8"))["registries"]
    assert on_disk == [{"name": "floofycrew-test", "repo": row.repo, "branch": "main", "trust": "index"}], "the host persisted the operator row (routes.py L4029)"
    again = apply_row(gateway.home, row, session=session, pinned_ids=pinned_ids)
    assert again["written"] is False, "idempotent"
    for pinned in pinned_ids:
        with pytest.raises(HostRegistryError, match="pins"):
            apply_row(gateway.home, HostRegistryRow(pinned, row.repo, "main"), session=session, pinned_ids=pinned_ids)
    if pinned_ids:
        refused = session.request("PUT", REGISTRIES_ROUTE, {"registries": [{"name": pinned_ids[0], "repo": row.repo, "branch": "main"}]})
        assert refused.status == 400 and "this build" in refused.body.decode("utf-8"), "the host itself refuses a pinned name (routes.py L3950)"
    with pytest.raises(HostRegistryError, match="never plaintext"):
        apply_row(gateway.home, HostRegistryRow("plain", "http://git.example/x/y", "main"), session=session)
    removed = remove_row(gateway.home, "floofycrew-test", session=session)
    assert removed["removed"] is True and session.get(REGISTRIES_ROUTE).json()["registries"] == []
    assert json.loads((gateway.home / "config.json").read_text(encoding="utf-8"))["registries"] == []


def test_4_vanilla_boot_when_the_loader_itself_fails(gateway: ScratchGateway):
    """A second gateway on the same scratch home with the self-test failure switch: the host boots as shipped."""
    gateway.stop()
    failure = gateway.data_home / "loader-failure.json"
    if failure.exists():
        failure.unlink()
    broken = ScratchGateway(gateway.payload, gateway.scratch, extra_env={"FLOOFY_LOADER_SELFTEST_FAIL": "1"})
    broken.start()
    try:
        client = broken.client()
        health = client.get("/api/health", auth=False)
        assert health.status == 200 and health.json()["ok"] is True and health.json()["version"] == "0.7.0.5"
        record = json.loads(failure.read_text(encoding="utf-8"))
        assert record["phase"] in ("routes", "startup") and "SELFTEST" in record["error"] and record["traceback"]
        assert client.get(f"{API}/state").status == 404, "no Loader routes were registered"
        assert client.get("/api/apps/floofycrew").status == 200, "the app itself is still installed and enabled"
        assert "Traceback" not in broken.stderr().split("KIROCREW_READY")[0] or "SELFTEST" in broken.stderr()
    finally:
        broken.stop()
