"""The hook against stand-ins for the host seams it reaches (both the 0.7 and the 0.8 shapes)."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UNRESOLVED = object()


def _exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def _install_host(monkeypatch, *, shape: str):
    """``kiro_crew`` stand-ins. ``shape`` is ``"0.7"`` (per-harness caches) or ``"0.8"`` (self-served dict + forget_for_recheck)."""
    client = types.ModuleType("kiro_crew.acp.client")
    client._UNRESOLVED = UNRESOLVED
    client._claude_acp_argv_cache = (None, "/usr/bin")        # a cached negative
    client._codex_acp_argv_cache = UNRESOLVED
    client._pi_acp_argv_cache = UNRESOLVED
    client._pi_bin_cache = UNRESOLVED
    calls = {"probe_cleared": 0, "forget": [], "bumped": []}

    def _resolve_node_adapter_argv(*, bin_name, override_env, vendored_entry):
        return None, "/usr/bin:/usr/local/bin"

    client._resolve_node_adapter_argv = _resolve_node_adapter_argv
    client._resolve_pi_bin = lambda: (None, "/usr/bin")
    client._resolve_claude_code_executable = lambda: "/opt/cli/bin/claude"

    class AcpClient:  # the real one: plain class, stores extra_env by reference, exposes .backend
        def __init__(self, work_dir=None, model=None, agent="kirocrew", sandbox_mode="auto", session_key=None, channel_id=None, extra_env=None, acp_backend="", **_kw):
            self._session_key = session_key
            self._extra_env = extra_env or {}
            self._acp_backend = acp_backend

        @property
        def backend(self):
            return self._acp_backend

    client.AcpClient = AcpClient
    harness_pkg = types.ModuleType("kiro_crew.acp.harness")
    harness_codex = types.ModuleType("kiro_crew.acp.harness.codex")

    class CodexHarness:
        def apply_spawn_env(self, env):
            env.pop("KIRO_API_KEY", None)

    harness_codex.CodexHarness = CodexHarness
    if shape == "0.7":
        client._opencode_bin_cache = (None, "/usr/bin")
        client._resolve_opencode_bin = lambda: (None, "/usr/bin")
    else:
        client._self_served_bin_caches = {"opencode": (None, "/usr/bin")}
        client._resolve_self_served_bin = lambda backend: (None, "/usr/bin")
        client.bump_resolution_generation = lambda b: calls["bumped"].append(b)

    acp_pkg = types.ModuleType("kiro_crew.acp")
    acp_pkg.client = client
    env = types.ModuleType("kiro_crew.env")
    env.augmented_path = lambda base: os.path.join(os.environ["HOME"], ".local", "bin") + ":/usr/bin:/usr/local/bin" + (os.pathsep + base if base else "")  # like the host: ~/.local/bin first
    acp_backends = types.ModuleType("kiro_crew.acp_backends")
    acp_backends.ACP_BACKENDS_KNOWN = frozenset({"", "kas", "claude", "codex", "opencode", "pi"})
    acp_backends.POLICY_ID_BY_BACKEND = {"": "kiro", "kas": "kas", "claude": "claude", "codex": "codex", "opencode": "opencode", "pi": "pi"}
    acp_backends.selectable_backend_values = lambda: ["", "kas", "claude", "codex", "opencode", "pi"]
    acp_backends.model_registry_namespace = lambda b: {"": "acp", "kas": "acp", "claude": "claude_code", "codex": "codex", "opencode": "opencode", "pi": "pi"}[b]

    class State:
        def __init__(self, backend):
            self.backend = backend
            self.installed = "missing" if backend in ("claude", "codex", "opencode", "pi") else "installed"
            self.missing_components = (backend + "-adapter",) if self.installed == "missing" else ()
            self.install_command = "npm i -g something" if self.installed == "missing" else ""
            self.restart_required = False

    agent_sdk = types.ModuleType("kiro_crew.agent_sdk")
    agent_sdk.probe_backend = lambda b: State(b)
    agent_sdk.clear_probe_cache = lambda: calls.__setitem__("probe_cleared", calls["probe_cleared"] + 1)
    if shape == "0.8":
        def forget_for_recheck(b):
            calls["forget"].append(b)
            for name in {"claude": ("_claude_acp_argv_cache",), "codex": ("_codex_acp_argv_cache",), "pi": ("_pi_acp_argv_cache", "_pi_bin_cache")}.get(b, ()):
                setattr(client, name, UNRESOLVED)
            client._self_served_bin_caches.pop(b, None)
        agent_sdk.forget_for_recheck = forget_for_recheck
    backends = types.ModuleType("kiro_crew.agent_sdk.backends")
    backends.ACP_BACKEND_PROCESS_NAMES = {"": "kiro-cli", "kas": "kiro-cli", "claude": "claude-agent-acp", "codex": "codex-acp", "pi": "pi-acp", "opencode": "opencode"}
    backends.ACP_BACKEND_NODE_ADAPTER_PACKAGES = {"claude": "@agentclientprotocol/claude-agent-acp", "codex": "@agentclientprotocol/codex-acp", "pi": "pi-acp"}
    model_registry = types.ModuleType("kiro_crew.model_registry")
    model_registry.advertised_models = lambda ns: {"claude_code": ["claude-opus-4-8[1m]", "fable"]}.get(ns, [])
    dashboard = types.ModuleType("kiro_crew.dashboard")
    handlers = types.ModuleType("kiro_crew.dashboard.handlers")
    core = types.ModuleType("kiro_crew.dashboard.handlers.core")
    core._selectable_acp_backends = lambda: ["", "kas", "claude", "codex"]
    status = types.ModuleType("kiro_crew.dashboard.handlers.acp_backend_status")
    status._snapshot = lambda: [{"id": "claude", "installed": "missing", "restart_required": False, "selectable": True, "missing_components": ["claude-agent-acp"], "install_command": "npm i -g @agentclientprotocol/claude-agent-acp"}]
    kiro_crew = types.ModuleType("kiro_crew")
    kiro_crew.acp = acp_pkg
    kiro_crew.model_registry = model_registry
    kiro_crew.agent_sdk = agent_sdk
    for name, mod in {"kiro_crew": kiro_crew, "kiro_crew.acp": acp_pkg, "kiro_crew.acp.client": client, "kiro_crew.acp.harness": harness_pkg, "kiro_crew.acp.harness.codex": harness_codex,
                      "kiro_crew.env": env, "kiro_crew.acp_backends": acp_backends,
                      "kiro_crew.agent_sdk": agent_sdk, "kiro_crew.agent_sdk.backends": backends, "kiro_crew.model_registry": model_registry,
                      "kiro_crew.dashboard": dashboard, "kiro_crew.dashboard.handlers": handlers, "kiro_crew.dashboard.handlers.core": core,
                      "kiro_crew.dashboard.handlers.acp_backend_status": status}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return client, calls


class FakeHooks:
    """Enough of the Loader's registry to install an ``after`` chain on a module attribute (dotted ``Class.method`` included)."""

    def __init__(self):
        self.registered = []

    def after(self, target, fn):
        module_name, attr = target.split(":")
        if module_name not in sys.modules:
            raise LookupError(f"no such target {target}")
        owner = sys.modules[module_name]
        parts = attr.split(".")
        for part in parts[:-1]:
            if not hasattr(owner, part):
                raise LookupError(f"no such target {target}")
            owner = getattr(owner, part)
        if not hasattr(owner, parts[-1]):
            raise LookupError(f"no such target {target}")
        original = getattr(owner, parts[-1])

        def wrapper(*args, **kwargs):
            return fn(original(*args, **kwargs), *args, **kwargs)

        setattr(owner, parts[-1], wrapper)
        self.registered.append(target)


class FakeConfig(dict):
    def to_dict(self):
        return dict(self)

    def update(self, values):  # noqa: A003 - the Loader's name
        dict.update(self, values)


class FakeLog:
    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        return lambda msg, *a: self.lines.append((level, msg % a if a else msg))


class FakeRequest:
    def __init__(self, body=None):
        import json

        self.body = json.dumps(body).encode() if body is not None else b""

    def json(self):
        import json

        return json.loads(self.body) if self.body else None


def _load_hook(monkeypatch):
    spec = importlib.util.spec_from_file_location("abd_hook", ROOT / "hook" / "__init__.py", submodule_search_locations=[str(ROOT / "hook")])
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "abd_hook", module)
    spec.loader.exec_module(module)
    return module


CODEX_TOML = 'model_provider = "amazon-bedrock"\nmodel = "openai.gpt-6-sol"\n\n[model_providers.amazon-bedrock.aws]\nregion = "us-east-2"\nprofile = "codex-work"\n'
AWS_CONFIG = "[default]\nregion = us-west-2\n\n[profile codex-work]\ncredential_process = /opt/tools/vend --role admin\n\n[profile keys]\naws_access_key_id = NOT-A-REAL-KEY-ID\naws_secret_access_key = nope\n"


@pytest.fixture(params=["0.7", "0.8"])
def world(request, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    host_home = home / ".kiro" / "crew"
    host_home.mkdir(parents=True)
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text(CODEX_TOML)
    (home / ".aws").mkdir()
    (home / ".aws" / "config").write_text(AWS_CONFIG)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_PATH", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin")
    client, calls = _install_host(monkeypatch, shape=request.param)
    H = _load_hook(monkeypatch)
    ctx = types.SimpleNamespace(host=types.SimpleNamespace(version="0.8.0.6" if request.param == "0.8" else "0.7.0.5", host_home=str(host_home)), hooks=FakeHooks(),
                                routes=types.SimpleNamespace(add=lambda *a: None), config=FakeConfig(), state={}, log=FakeLog(), data_dir=str(tmp_path))
    return types.SimpleNamespace(H=H, ctx=ctx, client=client, calls=calls, home=home, host_home=host_home, shape=request.param)


def _codex_tree(home: Path) -> tuple[Path, Path]:
    prefix = home / ".local" / "node" / "current"
    entry = prefix / "lib" / "node_modules" / "@agentclientprotocol" / "codex-acp" / "dist" / "index.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("export {};\n")
    (prefix / "lib" / "node_modules" / "@agentclientprotocol" / "sdk").mkdir(parents=True)
    node = _exe(prefix / "bin" / "node")
    _exe(prefix / "bin" / "codex-acp")
    return entry, node


def test_activate_resets_cached_negatives_and_installs_the_hooks(world):
    world.H.activate(world.ctx)
    assert "kiro_crew.acp.client:_resolve_node_adapter_argv" in world.ctx.hooks.registered
    assert world.client._claude_acp_argv_cache is UNRESOLVED, "the cached negative for claude was reset"
    if world.shape == "0.8":
        assert world.calls["forget"] == ["claude", "opencode"] or set(world.calls["forget"]) == {"claude", "opencode"}
        assert "opencode" not in world.client._self_served_bin_caches
    else:
        assert world.client._opencode_bin_cache is UNRESOLVED
        assert world.calls["probe_cleared"] >= 1
    assert set(world.ctx.state["reset_on_activate"]) == {"claude", "opencode"}
    assert world.ctx.state["hooks"] == {"node_adapters": "active", "plain_binaries": "active", "codex_awsenv": "active", "codex_spawn_env": "active"}
    assert world.ctx.state["codex_awsenv"]["verdict"] == "not_applied", "report-only at activation: nothing is written before the first launch"
    assert not (world.host_home / "codex-aws").exists()


def test_after_hook_fills_a_miss_from_the_npm_prefix(world):
    entry, node = _codex_tree(world.home)
    world.H.activate(world.ctx)
    argv, searched = world.client._resolve_node_adapter_argv(bin_name="codex-acp", override_env="CODEX_ACP_BIN", vendored_entry=lambda: None)
    assert argv == [str(node), str(entry.resolve())]
    assert searched == "/usr/bin:/usr/local/bin"
    assert world.H._findings["codex-acp"]["doctor"]["rung"] in ("the tree node lives in", "~/.local/node/current")
    # a host hit passes through untouched, and a name nothing is installed for stays a miss
    world.client._resolve_node_adapter_argv.__wrapped__ = None
    assert world.client._resolve_node_adapter_argv(bin_name="claude-agent-acp", override_env="X", vendored_entry=lambda: None) == (None, "/usr/bin:/usr/local/bin")
    assert world.H._findings["claude-agent-acp"] == {"host": "missing", "doctor": None, "ts": world.H._findings["claude-agent-acp"]["ts"]}


def test_plain_binary_hook_uses_a_launcher_under_a_prefix(world):
    _exe(world.home / ".npm-global" / "bin" / "opencode")
    _exe(world.home / ".npm-global" / "bin" / "pi")
    world.H.activate(world.ctx)
    if world.shape == "0.8":
        assert world.client._resolve_self_served_bin("opencode") == (str(world.home / ".npm-global" / "bin" / "opencode"), "/usr/bin")
    else:
        assert world.client._resolve_opencode_bin() == (str(world.home / ".npm-global" / "bin" / "opencode"), "/usr/bin")
    assert world.client._resolve_pi_bin() == (str(world.home / ".npm-global" / "bin" / "pi"), "/usr/bin")


def test_status_snapshot_shape(world):
    _codex_tree(world.home)
    world.H.activate(world.ctx)
    snap = asyncio.run(world.H._status(FakeRequest()))
    rows = {r["id"]: r for r in snap["backends"]}
    assert set(rows) == {"", "kas", "claude", "codex", "opencode", "pi"}
    assert rows["codex"]["components"]["adapter"]["rung"] in ("the tree node lives in", "~/.local/node/current")
    assert rows["codex"]["components"]["launcher_on_host_path"] is None
    assert rows["claude"]["components"]["cli"] == {"name": "claude", "path": "/opt/cli/bin/claude", "env": "CLAUDE_CODE_EXECUTABLE", "forwarded": True}
    assert rows["claude"]["advertised"] == ["claude-opus-4-8[1m]", "fable"]
    assert rows["claude"]["host_row"]["installed"] == "missing"
    assert rows["claude"]["selectable"] and not rows["opencode"]["selectable"]
    assert rows["claude"]["cache"]["state"] == "unresolved"
    names = {p["name"]: p for p in snap["repairs"]}
    assert names["codex-acp"]["target"] == str(world.home / ".local" / "node" / "current" / "bin" / "codex-acp")
    assert names["node"]["target"] == str(world.home / ".local" / "node" / "current" / "bin" / "node")
    assert snap["hooks"]["node_adapters"] == "active"


def test_reresolve_route_resets_then_probes(world):
    world.H.activate(world.ctx)
    world.client._codex_acp_argv_cache = (None, "/usr/bin")
    out = asyncio.run(world.H._reresolve(FakeRequest({"backend": "codex"})))
    assert out["ok"] and out["reset"][0]["backend"] == "codex" and out["reset"][0]["before"] == "negative" and out["reset"][0]["after"] == "unresolved"
    assert out["probes"]["codex"]["installed"] == "missing"
    assert world.client._codex_acp_argv_cache is UNRESOLVED


def test_codex_path_route_exports_and_restores(world):
    codex = _exe(world.home / ".local" / "bin" / "codex")   # ~/.local/bin is on the stubbed search path
    world.H.activate(world.ctx)
    status, body = world.H._codex_path(FakeRequest({"enabled": True}))
    assert status == 200 and body["codex_path"] == str(codex) and os.environ["CODEX_PATH"] == str(codex)
    assert world.ctx.config["codex_path_enabled"] is True
    status, body = world.H._codex_path(FakeRequest({"enabled": True, "path": str(world.home / "nope")}))
    assert status == 400 and "not an executable" in body["error"] and os.environ["CODEX_PATH"] == str(codex)
    status, body = world.H._codex_path(FakeRequest({"enabled": False}))
    assert status == 200 and "CODEX_PATH" not in os.environ
    world.H._codex_path(FakeRequest({"enabled": True}))
    world.H.deactivate(world.ctx)
    assert "CODEX_PATH" not in os.environ, "deactivate restores the pre-mod environment"


def test_codex_path_is_reapplied_from_config_on_activate(world):
    codex = _exe(world.home / ".local" / "bin" / "codex")
    world.ctx.config.update({"codex_path_enabled": True, "codex_path": str(codex)})
    world.H.activate(world.ctx)
    assert os.environ["CODEX_PATH"] == str(codex) and world.ctx.state["codex_path"] == str(codex)


def test_symlink_route_links_into_local_bin_and_never_overwrites(world):
    entry, node = _codex_tree(world.home)
    world.H.activate(world.ctx)
    assert world.H._symlink(FakeRequest({"entries": [{"name": "codex-acp"}]}))[0] == 400, "confirm: true is required"
    out = world.H._symlink(FakeRequest({"confirm": True, "entries": [{"name": "codex-acp"}, {"name": "node"}, {"name": "../evil"}]}))
    by = {r["name"]: r for r in out["results"]}
    assert by["codex-acp"]["ok"] and os.readlink(world.home / ".local" / "bin" / "codex-acp") == str(world.home / ".local" / "node" / "current" / "bin" / "codex-acp")
    assert by["node"]["ok"] and os.readlink(world.home / ".local" / "bin" / "node") == str(node)
    assert not by["../evil"]["ok"]
    (world.home / ".local" / "bin" / "codex-acp").unlink()
    (world.home / ".local" / "bin" / "codex-acp").write_text("mine")
    out = world.H._symlink(FakeRequest({"confirm": True, "entries": [{"name": "codex-acp"}]}))
    assert out["ok"] is False and "already exists" in out["results"][0]["error"]
    assert (world.home / ".local" / "bin" / "codex-acp").read_text() == "mine"


def test_missing_seams_degrade_to_report_only(world, monkeypatch):
    for name in ("_resolve_node_adapter_argv", "_resolve_self_served_bin", "_resolve_opencode_bin", "_resolve_pi_bin", "AcpClient"):
        if hasattr(world.client, name):
            monkeypatch.delattr(world.client, name)
    monkeypatch.delattr(sys.modules["kiro_crew.acp.harness.codex"], "CodexHarness")
    world.H.activate(world.ctx)
    assert world.ctx.state["hooks"] == {"node_adapters": "report-only", "plain_binaries": "report-only", "codex_awsenv": "report-only", "codex_spawn_env": "report-only"}
    snap = asyncio.run(world.H._status(FakeRequest()))
    assert snap["hooks"]["node_adapters"].startswith("report-only")
    aws = asyncio.run(world.H._awsenv_status(FakeRequest()))
    assert aws["hook"].startswith("report-only") and aws["report"]["verdict"] in ("not_applied", "pending")


# --- seam 3: Codex's AWS launch environment ---------------------------------------------------------------------


def _new_client(world, backend, extra_env=None, session_key="dash:1"):
    return world.client.AcpClient(model="openai.gpt-6-sol", session_key=session_key, extra_env=extra_env, acp_backend=backend)


def test_codex_client_gets_the_isolated_aws_pointers(world):
    world.H.activate(world.ctx)
    broker_env = {"AIM_CREDS_AGENT_URL": "http://127.0.0.1:1/mcp", "SANDBOX_SESSION_ID": "u", "AWS_CONFIG_FILE": str(world.home / ".sbox-session" / "u" / "config"),
                  "AWS_SHARED_CREDENTIALS_FILE": str(world.home / ".sbox-session" / "u" / "creds"), "AWS_PROFILE": "default", "AWS_DEFAULT_PROFILE": "default"}
    c = _new_client(world, "codex", dict(broker_env))
    root = world.host_home / "codex-aws"
    assert c._extra_env["AWS_CONFIG_FILE"] == str(root / "config") and c._extra_env["AWS_SHARED_CREDENTIALS_FILE"] == str(root / "credentials")
    assert c._extra_env["AWS_PROFILE"] == "codex-work" == c._extra_env["AWS_DEFAULT_PROFILE"]
    assert c._extra_env["AIM_CREDS_AGENT_URL"] == broker_env["AIM_CREDS_AGENT_URL"], "the broker's own keys are left alone"
    assert oct(os.stat(root).st_mode & 0o777) == "0o700" and oct(os.stat(root / "config").st_mode & 0o777) == "0o600"
    text = (root / "config").read_text()
    assert "[profile codex-work]" in text and "credential_process = /opt/tools/vend" in text and "AKIA" not in text and "[default]" not in text
    assert (root / "credentials").read_text() == ""
    rec = world.H.codex_launch_record("dash:1")
    assert rec["applied"] and rec["preflight"] == "ok" and rec["profile"] == "codex-work" and rec["credential_source"] == "process"
    assert rec["superseded"] == {"AWS_CONFIG_FILE": "set", "AWS_SHARED_CREDENTIALS_FILE": "set", "AWS_PROFILE": "set", "AWS_DEFAULT_PROFILE": "set"}
    assert any("isolated config applied" in line for _lvl, line in world.ctx.log.lines)
    # a client with no extra_env at all gets one
    bare = _new_client(world, "codex", None, session_key="dash:2")
    assert bare._extra_env["AWS_PROFILE"] == "codex-work"


def test_non_codex_clients_are_untouched(world):
    world.H.activate(world.ctx)
    for backend in ("", "kas", "claude"):
        c = _new_client(world, backend, {"AWS_PROFILE": "default"}, session_key=f"s:{backend}")
        assert c._extra_env == {"AWS_PROFILE": "default"}
    assert world.H.codex_launch_record("s:kas") is None
    assert not (world.host_home / "codex-aws").exists()


def test_spawn_env_hook_reasserts_the_pointers_on_the_final_env(world):
    world.H.activate(world.ctx)
    _new_client(world, "codex", {}, session_key="dash:3")
    harness = sys.modules["kiro_crew.acp.harness.codex"].CodexHarness()
    env = {"KIRO_API_KEY": "x", "AWS_PROFILE": "default", "AWS_CONFIG_FILE": "/elsewhere", "PATH": "/usr/bin"}
    harness.apply_spawn_env(env)
    assert "KIRO_API_KEY" not in env, "the host's own strip still ran"
    assert env["AWS_PROFILE"] == "codex-work" and env["AWS_CONFIG_FILE"].endswith("/codex-aws/config") and env["PATH"] == "/usr/bin"


def test_preflight_failure_leaves_the_env_alone_and_records_why(world):
    (world.home / ".codex" / "config.toml").write_text(CODEX_TOML.replace("codex-work", "keys"))
    world.H.activate(world.ctx)
    c = _new_client(world, "codex", {"AWS_PROFILE": "default"}, session_key="dash:4")
    assert c._extra_env == {"AWS_PROFILE": "default"}, "a profile that only has static keys is never isolated"
    rec = world.H.codex_launch_record("dash:4")
    assert rec["applied"] is False and rec["preflight"] == "static_only"
    assert any(lvl == "warning" and "NOT applied (static_only)" in line for lvl, line in world.ctx.log.lines)
    (world.home / ".codex" / "config.toml").write_text('model = "gpt-5"\n')
    c = _new_client(world, "codex", {"AWS_PROFILE": "default"}, session_key="dash:5")
    assert c._extra_env == {"AWS_PROFILE": "default"} and world.H.codex_launch_record("dash:5")["preflight"] == "not_bedrock"


def test_repair_recreates_the_files_and_repoints_the_client(world):
    world.H.activate(world.ctx)
    c = _new_client(world, "codex", {}, session_key="dash:6")
    root = world.host_home / "codex-aws"
    (root / "config").write_text("corrupted")
    (root / "credentials").write_text("aws_secret_access_key = planted")
    c._extra_env["AWS_CONFIG_FILE"] = "/wrong"
    rec = world.H.repair_codex_awsenv("dash:6", "credentials", c)
    assert rec["repaired"] and rec["applied"] and rec["reason"] == "repair:credentials"
    assert "[profile codex-work]" in (root / "config").read_text() and (root / "credentials").read_text() == ""
    assert c._extra_env["AWS_CONFIG_FILE"] == str(root / "config")


def test_toggle_off_disables_injection(world):
    world.H.activate(world.ctx)
    assert world.H._awsenv_toggle(FakeRequest({"enabled": False})) == {"ok": True, "enabled": False}
    c = _new_client(world, "codex", {"AWS_PROFILE": "default"}, session_key="dash:7")
    assert c._extra_env == {"AWS_PROFILE": "default"} and world.H.codex_launch_record("dash:7") is None
    world.H._awsenv_toggle(FakeRequest({"enabled": True}))
    assert _new_client(world, "codex", {}, session_key="dash:8")._extra_env["AWS_PROFILE"] == "codex-work"


def test_awsenv_status_and_rebuild_routes(world):
    world.H.activate(world.ctx)
    snap = asyncio.run(world.H._awsenv_status(FakeRequest()))
    assert snap["enabled"] and snap["hook"] == "active" and snap["dir"] == str(world.host_home / "codex-aws")
    assert snap["report"]["verdict"] == "not_applied" and snap["report"]["profile"]["profile"] == "codex-work"
    out = world.H._awsenv_rebuild(FakeRequest())
    assert out["ok"] and out["record"]["repaired"] and (world.host_home / "codex-aws" / "config").exists()
    snap = asyncio.run(world.H._awsenv_status(FakeRequest()))
    assert snap["report"]["verdict"] == "ok" and snap["report"]["env"]["AWS_PROFILE"] == "codex-work"
    assert "AKIA" not in json.dumps(snap)


def test_classify_is_exposed_for_the_router(world):
    world.H.activate(world.ctx)
    out = world.H.classify_codex_failure("failed to load AWS credentials: the credential provider was not enabled")
    assert out == {"category": "credentials", "repairable": True, "redacted": "failed to load AWS credentials: the credential provider was not enabled"}
    assert world.H.classify_codex_failure("ThrottlingException")["category"] == "throttle"


def test_probe_refuses_to_run_unmasked_when_the_sandbox_cannot_be_built(world, monkeypatch):
    world.H.activate(world.ctx)
    # no kiro_crew.sandbox / acp_tool_gate stubs are installed → the probe must refuse rather than run `aws` unmasked
    out = world.H.probe_awsenv(sandboxed=True)
    assert out["ok"] is False and out["sandboxed"] is False and "refusing to probe unmasked" in out["error"]
    assert out["preflight"] == "ok"
