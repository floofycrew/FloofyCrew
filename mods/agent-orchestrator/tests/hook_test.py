"""Hook-level tests with a fake Loader ctx: registrations, spec rendering, the cap gate, the route taps."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class FakeLog:
    def __init__(self):
        self.lines = []

    def _w(self, level, msg, *args, **kw):
        self.lines.append((level, msg % args if args else msg))

    info = lambda self, *a, **k: self._w("info", *a, **k)
    warning = lambda self, *a, **k: self._w("warning", *a, **k)
    error = lambda self, *a, **k: self._w("error", *a, **k)
    debug = lambda self, *a, **k: self._w("debug", *a, **k)


class FakeConfig(dict):
    def to_dict(self):
        return dict(self)


class FakeHooks:
    def __init__(self):
        self.regs = []

    def before(self, target, fn):
        self.regs.append(("before", target, fn))

    def after(self, target, fn):
        self.regs.append(("after", target, fn))

    def replace(self, target, fn):
        self.regs.append(("replace", target, fn))

    def find(self, kind, target):
        return next(fn for k, t, fn in self.regs if k == kind and t == target)


class FakeRoutes:
    def __init__(self):
        self.regs = {}

    def add(self, method, path, handler):
        self.regs[(method, path)] = handler


def make_ctx(tmp_path, cfg=None):
    return types.SimpleNamespace(
        mod_id="agent-orchestrator", version="0.1.0", mod_dir=str(ROOT), data_dir=str(tmp_path / "data"),
        host=types.SimpleNamespace(version="0.7.0.5", interpreter=sys.executable),
        log=FakeLog(), config=FakeConfig(cfg or {}), hooks=FakeHooks(), routes=FakeRoutes(), state={},
    )


def load_hook(monkeypatch):
    for name in list(sys.modules):
        if name.startswith("ao_hookpkg"):
            del sys.modules[name]
    spec = importlib.util.spec_from_file_location("ao_hookpkg", ROOT / "hook" / "__init__.py", submodule_search_locations=[str(ROOT / "hook")])
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "ao_hookpkg", module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def hook(tmp_path, monkeypatch):
    agents = tmp_path / "kiro" / "agents"
    agents.mkdir(parents=True)
    monkeypatch.setenv("KIRO_HOME", str(tmp_path / "kiro"))
    (agents / "kirocrew-conductor.json").write_text(json.dumps({
        "name": "kirocrew-conductor",
        "mcpServers": {"kirocrew-core": {"command": "/opt/kc/bin/kirocrew", "args": ["mcp-core"]},
                       "kirocrew-dashboard": {"command": "/opt/kc/bin/kirocrew", "args": ["mcp-dashboard"]},
                       "kirocrew-work": {"command": "/opt/kc/bin/kirocrew", "args": ["mcp-work"]},
                       "creds-agent": {"command": "aim", "args": ["x"]}},
    }))
    (tmp_path / "data").mkdir()
    mod = load_hook(monkeypatch)
    ctx = make_ctx(tmp_path)
    return types.SimpleNamespace(mod=mod, ctx=ctx, agents=agents, tmp=tmp_path)


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def activate(h):
    """activate() wants a running loop for the socket task; run it inside one."""

    async def go():
        h.mod.activate(h.ctx)
        await asyncio.sleep(0.05)  # let the socket task start
        return h.mod._socket

    return run(go())


def test_activate_registers_taps_routes_and_specs(hook):
    activate(hook)
    kinds = {(k, t) for k, t, _ in hook.ctx.hooks.regs}
    assert ("before", "kiro_crew.dashboard.state:DashboardState.broadcast_ws") in kinds
    assert ("before", "kiro_crew.dashboard.state:DashboardState._broadcast") in kinds
    assert ("after", "route:POST /api/session-control/send") in kinds
    assert ("after", "route:POST /api/work-ledger/report") in kinds
    assert ("replace", "route:POST /api/session-control/create") in kinds
    assert ("after", "route:DELETE /api/sessions/{key}") in kinds and ("after", "route:DELETE /api/sessions") in kinds
    assert set(hook.ctx.routes.regs) == {("GET", "runs"), ("POST", "runs/remove"), ("GET", "graph"), ("GET", "status")}
    assert hook.ctx.state["active"] is True and hook.ctx.state["max_workers"] == 6

    orch = json.loads((hook.agents / "agent-orchestrator.json").read_text())
    worker = json.loads((hook.agents / "agent-orchestrator-worker.json").read_text())
    for spec in (orch, worker):
        bus = spec["mcpServers"]["agent-orchestrator-bus"]
        assert bus["command"] == sys.executable and bus["args"][0] == str(ROOT / "mcp" / "server.py")
        assert bus["args"][1:] == ["--socket", bus["env"]["AGENT_ORCHESTRATOR_SOCK"]]
        assert "@agent-orchestrator-bus" in spec["tools"] and spec["includeMcpJson"] is False
    # host first-party servers copied from the host's own conductor rendering; creds-agent (not referenced) is not
    assert orch["mcpServers"]["kirocrew-dashboard"]["command"] == "/opt/kc/bin/kirocrew"
    assert set(orch["mcpServers"]) == {"agent-orchestrator-bus", "kirocrew-core", "kirocrew-dashboard", "kirocrew-work"}
    assert set(worker["mcpServers"]) == {"agent-orchestrator-bus", "kirocrew-core", "kirocrew-work"}
    assert "fs_write" not in orch["tools"] and "execute_bash" not in orch["tools"]  # the hub never does the work
    assert "@kirocrew-dashboard" not in worker["tools"]  # workers get no session control
    for n in ("agent-orchestrator", "agent-orchestrator-worker"):
        owner = json.loads((hook.agents / f"{n}.json.floofy-owner").read_text())
        assert owner == {"mod": "agent-orchestrator"}


def test_activate_never_overwrites_a_foreign_spec(hook):
    (hook.agents / "agent-orchestrator.json").write_text('{"name": "agent-orchestrator", "prompt": "mine"}')
    activate(hook)
    assert json.loads((hook.agents / "agent-orchestrator.json").read_text())["prompt"] == "mine"
    assert str(hook.agents / "agent-orchestrator.json") in hook.ctx.state["spec_conflicts"]
    assert (hook.agents / "agent-orchestrator-worker.json").is_file()  # the other one still installs


def test_deactivate_removes_only_our_specs_and_socket(hook):
    sock = activate(hook)
    assert sock is not None and sock.path.exists()
    run(_deactivate(hook))
    assert not (hook.agents / "agent-orchestrator.json").exists()
    assert not (hook.agents / "agent-orchestrator.json.floofy-owner").exists()
    assert (hook.agents / "kirocrew-conductor.json").exists()
    assert hook.ctx.state["active"] is False


async def _deactivate(h):
    h.mod.deactivate(h.ctx)
    await asyncio.sleep(0.05)


class FakeRequest:
    def __init__(self, headers=None, body=None):
        self.headers = headers or {}
        self._body = body or {}

    async def json(self):
        return self._body


def _slots(n_workers, running=True):
    out = [{"key": "orch", "title": "goal", "agent": "agent-orchestrator", "running": running, "created_by": ""}]
    for i in range(n_workers):
        out.append({"key": f"w{i}", "title": f"worker-{i+1}: t", "agent": "agent-orchestrator-worker", "running": True, "created_by": "dashboard:orch"})
    return out


def test_create_cap_applies_only_to_our_orchestrators(hook, monkeypatch):
    aiohttp = types.ModuleType("aiohttp")
    web = types.ModuleType("aiohttp.web")

    class Resp:
        def __init__(self, data, status=200):
            self.data, self.status = data, status

    web.json_response = lambda data, status=200: Resp(data, status)
    aiohttp.web = web
    monkeypatch.setitem(sys.modules, "aiohttp", aiohttp)
    monkeypatch.setitem(sys.modules, "aiohttp.web", web)
    hook.ctx.config.update({"max_workers": 2})
    activate(hook)
    tap = hook.ctx.hooks.find("before", "kiro_crew.dashboard.state:DashboardState._broadcast")
    tap(object(), {"_type": "slots", "_slots_list": _slots(2) + [
        {"key": "cond", "title": "host conductor", "agent": "kirocrew-conductor", "running": True, "created_by": ""},
        {"key": "cw1", "title": "w", "agent": "kirocrew-worker", "running": True, "created_by": "dashboard:cond"},
        {"key": "cw2", "title": "w", "agent": "kirocrew-worker", "running": True, "created_by": "dashboard:cond"},
        {"key": "cw3", "title": "w", "agent": "kirocrew-worker", "running": True, "created_by": "dashboard:cond"}]})
    gate = hook.ctx.hooks.find("replace", "route:POST /api/session-control/create")
    calls = []

    async def call_next(request):
        calls.append(request)
        return Resp({"ok": True})

    refused = run(gate(call_next, FakeRequest({"X-Session-Key": "dashboard:orch"})))
    assert refused.status == 429 and refused.data["code"] == "orchestrator_cap_reached" and not calls
    passed = run(gate(call_next, FakeRequest({"X-Session-Key": "dashboard:cond"})))  # the host's conductor: untouched
    assert passed.status == 200 and len(calls) == 1
    hook.ctx.config.update({"enforce_cap": False})
    passed = run(gate(call_next, FakeRequest({"X-Session-Key": "dashboard:orch"})))
    assert passed.status == 200 and len(calls) == 2


def test_route_taps_log_send_and_report_rows(hook):
    activate(hook)
    tap = hook.ctx.hooks.find("before", "kiro_crew.dashboard.state:DashboardState._broadcast")
    tap(object(), {"_type": "slots", "_slots_list": _slots(2)})
    send = hook.ctx.hooks.find("after", "route:POST /api/session-control/send")
    report = hook.ctx.hooks.find("after", "route:POST /api/work-ledger/report")
    ok = types.SimpleNamespace(status=200)
    out = run(send(ok, FakeRequest({"X-Session-Key": "dashboard:orch"}, {"target": "dashboard:w0", "message": "port billing"})))
    assert out is ok
    run(send(types.SimpleNamespace(status=403), FakeRequest({"X-Session-Key": "dashboard:orch"}, {"target": "dashboard:w0", "message": "refused"})))
    run(report(ok, FakeRequest({"X-Session-Key": "dashboard:w1"}, {"status": "done", "summary": "queue ported"})))
    graph = hook.ctx.routes.regs[("GET", "graph")](types.SimpleNamespace(query={"run": ["dashboard:orch"], "since": [""]}))
    kinds = [(m["from"], m["to"], m["kind"]) for m in graph["messages"]]
    assert kinds == [("orchestrator", "worker-1", "send"), ("worker-2", "orchestrator", "report")]
    w2 = next(w for w in graph["workers"] if w["nick"] == "worker-2")
    assert w2["state"] == "done" and w2["progress"] == 100
    runs = hook.ctx.routes.regs[("GET", "runs")](None)
    assert runs["runs"][0]["run"] == "dashboard:orch" and runs["runs"][0]["workers"] == 2
    status = hook.ctx.routes.regs[("GET", "status")](None)
    assert status["active"] and status["runs"] == 1


def test_broadcast_ws_tap_updates_nodes_and_captures_state(hook):
    activate(hook)
    tap_slots = hook.ctx.hooks.find("before", "kiro_crew.dashboard.state:DashboardState._broadcast")
    tap_ws = hook.ctx.hooks.find("before", "kiro_crew.dashboard.state:DashboardState.broadcast_ws")
    fake_state = object()
    tap_slots(fake_state, {"_type": "slots", "_slots_list": _slots(1)})
    tap_ws(fake_state, "tool_call", {"slot": "w0", "tool": "execute_bash"})
    assert hook.mod._state_ref is fake_state
    graph = hook.ctx.routes.regs[("GET", "graph")](types.SimpleNamespace(query={}))
    assert graph["workers"][0]["summary"] == "running execute_bash"
    tap_ws(fake_state, "chat_status", "not-a-dict")  # malformed frames never raise


def test_graph_route_through_the_loader_dispatch_takes_its_query(hook, monkeypatch):
    """The page fetches `routes.url("graph") + "?run=…&since=…"`. Pin the two halves of that contract at the Loader:
    a query reaches the handler as request.query, and a `?` INSIDE the path segment (what routes.fetch("graph?run=")
    produces) is a 404 with no `run` field — the bug the first page shipped with."""
    sys.path.insert(0, str(ROOT.parents[1] / "loader-app"))
    from floofy_loader.modroutes import ModRouteRegistry

    registry = ModRouteRegistry()
    hook.ctx.routes = registry.for_mod("agent-orchestrator")
    activate(hook)
    tap = hook.ctx.hooks.find("before", "kiro_crew.dashboard.state:DashboardState._broadcast")
    tap(object(), {"_type": "slots", "_slots_list": _slots(2)})
    status, _headers, raw = registry.dispatch_sync("agent-orchestrator", "GET", "graph", query={"run": ["dashboard:orch"], "since": [""]})
    body = json.loads(raw)
    assert status == 200 and body["run"] == "dashboard:orch" and len(body["workers"]) == 2
    status, _headers, raw = registry.dispatch_sync("agent-orchestrator", "GET", "graph?run=dashboard:orch", query={})
    body = json.loads(raw)
    assert status == 404 and "run" not in body and body["ok"] is False


def test_graph_route_rebuilds_a_run_from_disk_when_its_sessions_are_gone(hook):
    activate(hook)
    tap = hook.ctx.hooks.find("before", "kiro_crew.dashboard.state:DashboardState._broadcast")
    tap(object(), {"_type": "slots", "_slots_list": _slots(2)})
    svc = hook.mod._service
    run(svc.op_bus_run("dashboard:orch", {"action": "goal", "goal": "build the status page"}))
    run(svc.op_bus_status("dashboard:w0", {"text": "writing index.html", "progress": 40, "state": "working"}))
    run(svc.op_bus_status("dashboard:w1", {"text": "css done", "progress": 100, "state": "done"}))
    run(svc.op_bus_post("dashboard:w0", {"to": "orchestrator", "kind": "say", "text": "half way"}))
    graph = hook.ctx.routes.regs[("GET", "graph")]
    runs = hook.ctx.routes.regs[("GET", "runs")]
    live = graph(types.SimpleNamespace(query={"run": ["dashboard:orch"]}))
    assert live["orchestrator"]["running"] and "from_disk" not in live

    # the gateway restarts (fresh reducer, nothing in the slots snapshot any more): the picker still lists the run…
    hook.mod._graph = hook.mod._graph.__class__(orchestrator_agent="agent-orchestrator")
    hook.mod._service.graph = hook.mod._graph
    listed = runs(None)["runs"]
    assert listed == [{"run": "dashboard:orch", "title": "build the status page", "agent": "", "goal": "build the status page",
                       "closed": False, "workers": 2, "messages": 1, "live": False}]
    # …and the graph draws it from meta.json + status.json instead of an orchestrator-less (empty-state) snapshot
    disk = graph(types.SimpleNamespace(query={"run": ["dashboard:orch"]}))
    assert disk["from_disk"] is True and disk["orchestrator"]["title"] == "build the status page"
    assert [w["nick"] for w in disk["workers"]] == ["worker-1", "worker-2"]
    w1, w2 = disk["workers"]
    assert w1["summary"] == "writing index.html" and w1["progress"] == 40 and w1["state"] == "gone"
    assert w2["state"] == "done" and w2["progress"] == 100
    assert disk["counters"] == {"workers": 2, "done": 1, "needs_input": 0, "working": 0}
    assert [m["text"] for m in disk["messages"]] == ["half way"] and disk["message_total"] == 1
    # the slots snapshot comes back (tabs still open): live again, no disk marker
    tap(object(), {"_type": "slots", "_slots_list": _slots(2)})
    assert "from_disk" not in graph(types.SimpleNamespace(query={"run": ["dashboard:orch"]}))


def test_kas_identity_bridge_gives_only_our_bus_server_the_session_key(hook):
    """KAS withholds env for every non-managed server; the bridge puts the host's own session key on OUR bus server."""
    activate(hook)
    assert hook.ctx.state["kas_identity_bridge"] is True
    after = hook.ctx.hooks.find("after", "kiro_crew.acp.kas_agents:_project_mcp_servers")
    ours = str(ROOT / "mcp" / "server.py")
    projected = {
        "agent-orchestrator-bus": {"command": sys.executable, "args": [ours, "--socket", "/tmp/x.sock"]},  # env withheld by the host
        "kirocrew-work": {"command": "/opt/kc/bin/kirocrew", "args": ["mcp-work"], "env": {"KIROCREW_SESSION_KEY": "dashboard:w0"}},
        "some-other-server": {"command": "npx", "args": ["foo"]},
    }
    out = after(projected, {"name": "agent-orchestrator-worker"}, "agent-orchestrator-worker", frozenset(), "dashboard:w0")
    assert out["agent-orchestrator-bus"]["env"] == {"KIROCREW_SESSION_KEY": "dashboard:w0"}
    assert "env" not in out["some-other-server"]  # a third-party server stays as the host left it
    assert out["kirocrew-work"]["env"] == {"KIROCREW_SESSION_KEY": "dashboard:w0"}
    # the key as a keyword, the way a future host might pass it
    out = after({"agent-orchestrator-bus": {"command": sys.executable, "args": [ours, "--socket", "/tmp/x.sock"]}},
                {"name": "x"}, "x", frozenset(), session_key="dashboard:w1")
    assert out["agent-orchestrator-bus"]["env"]["KIROCREW_SESSION_KEY"] == "dashboard:w1"
    # no session key (a host without the parameter): nothing is invented
    out = after({"agent-orchestrator-bus": {"command": sys.executable, "args": [ours]}}, {"name": "x"}, "x", frozenset())
    assert "env" not in out["agent-orchestrator-bus"]
    # a server merely wearing our name (different argv) is not vouched for
    out = after({"agent-orchestrator-bus": {"command": "python", "args": ["/elsewhere/server.py"]}}, {"name": "x"}, "x", frozenset(), "dashboard:w0")
    assert "env" not in out["agent-orchestrator-bus"]


def test_activate_tolerates_a_host_without_the_kas_seam(hook):
    def failing_after(target, fn, _orig=hook.ctx.hooks.after):
        if target.startswith("kiro_crew.acp.kas_agents:"):
            raise LookupError("no such attribute on this host")
        return _orig(target, fn)

    hook.ctx.hooks.after = failing_after
    activate(hook)
    assert hook.ctx.state["active"] is True and hook.ctx.state["kas_identity_bridge"] is False
    assert any("KAS identity bridge unavailable" in line for _, line in hook.ctx.log.lines)


def test_session_send_tap_logs_rows_with_the_0806_bare_created_by(hook):
    """Replays the demo run on the test box: created_by is the bare slot key, so the orchestrator's six session_send
    calls logged nothing (its run key did not match the workers'). Now they do."""
    activate(hook)
    tap = hook.ctx.hooks.find("before", "kiro_crew.dashboard.state:DashboardState._broadcast")
    tap(object(), {"_type": "slots", "_slots_list": [
        {"key": "chat-4", "title": "goal", "agent": "agent-orchestrator", "running": True, "created_by": ""},
        {"key": "chat-5", "title": "worker-1: index", "agent": "agent-orchestrator-worker", "running": True, "created_by": "chat-4"},
        {"key": "chat-6", "title": "worker-2: css", "agent": "agent-orchestrator-worker", "running": True, "created_by": "chat-4"},
    ]})
    send = hook.ctx.hooks.find("after", "route:POST /api/session-control/send")
    ok = types.SimpleNamespace(status=200)
    run(send(ok, FakeRequest({"X-Session-Key": "dashboard:chat-4"}, {"target": "chat-5", "message": "You are worker-1 …"})))
    run(send(ok, FakeRequest({"X-Session-Key": "dashboard:chat-4"}, {"target": "dashboard:chat-6", "message": "You are worker-2 …"})))
    graph = hook.ctx.routes.regs[("GET", "graph")](types.SimpleNamespace(query={"run": ["dashboard:chat-4"]}))
    assert [(m["from"], m["to"], m["kind"]) for m in graph["messages"]] == [("orchestrator", "worker-1", "send"), ("orchestrator", "worker-2", "send")]
    assert graph["orchestrator"]["nick"] == "orchestrator" and [w["nick"] for w in graph["workers"]] == ["worker-1", "worker-2"]
    runs = hook.ctx.routes.regs[("GET", "runs")](None)["runs"]
    assert runs[0]["run"] == "dashboard:chat-4" and runs[0]["workers"] == 2 and runs[0]["title"] == "goal"


# --- removing runs: deleted chats, the page's ✕, retention ------------------------------------------------------


class _Resp:
    def __init__(self, data, status=200):
        self.status = status
        self.text = json.dumps(data)


def _delete_req(key):
    return types.SimpleNamespace(match_info={"key": key}, headers={})


def _start_run(hook, n_workers=2):
    tap = hook.ctx.hooks.find("before", "kiro_crew.dashboard.state:DashboardState._broadcast")
    tap(object(), {"_type": "slots", "_slots_list": _slots(n_workers)})
    svc = hook.mod._service
    run(svc.op_bus_run("dashboard:orch", {"action": "goal", "goal": "build the status page"}))
    run(svc.op_bus_status("dashboard:w0", {"text": "writing index.html", "progress": 40, "state": "working"}))
    return tap


def test_deleting_the_orchestrator_chat_removes_the_run(hook):
    """The report behind this: deleted chats left the run on the page as '○ archived (sessions gone)' forever."""
    activate(hook)
    tap = _start_run(hook)
    store = hook.mod._store
    assert store.meta("dashboard:orch") is not None
    after = hook.ctx.hooks.find("after", "route:DELETE /api/sessions/{key}")

    # worker tabs still open: the orchestrator's delete does NOT take the record (the workers may still post)
    run(after(_Resp({"ok": True}), _delete_req("dashboard:orch")))
    assert store.meta("dashboard:orch") is not None
    assert hook.mod._graph.nodes["orch"].gone is True
    # the host says the orchestrator transcript is gone; the last worker is deleted → the run goes with it
    hook.mod._state_ref = types.SimpleNamespace(conversation_log=types.SimpleNamespace(has_log=lambda k: k != "dashboard:orch"))
    tap(object(), {"_type": "slots", "_slots_list": [s for s in _slots(2) if s["key"] != "orch"]})
    run(after(_Resp({"ok": True}), _delete_req("dashboard:w0")))
    assert store.meta("dashboard:orch") is not None  # w1 is still open
    run(after(_Resp({"ok": True}), _delete_req("dashboard:w1")))
    assert store.meta("dashboard:orch") is None and not store.run_dir("dashboard:orch").exists()
    assert "orch" not in hook.mod._graph.nodes and hook.mod._graph.runs() == []
    assert hook.ctx.routes.regs[("GET", "runs")](None) == {"runs": []}
    assert any("run dashboard:orch removed" in line for _, line in hook.ctx.log.lines)


def test_deleting_the_orchestrator_chat_after_the_workers_removes_the_run_at_once(hook):
    activate(hook)
    tap = _start_run(hook)
    tap(object(), {"_type": "slots", "_slots_list": _slots(0)})  # worker tabs deleted first (or closed)
    after = hook.ctx.hooks.find("after", "route:DELETE /api/sessions/{key}")
    run(after(_Resp({"ok": True}), _delete_req("dashboard:orch")))
    assert hook.mod._store.meta("dashboard:orch") is None and hook.mod._graph.runs() == []


def test_a_failed_or_foreign_delete_removes_nothing(hook):
    activate(hook)
    _start_run(hook)
    tap = hook.ctx.hooks.find("before", "kiro_crew.dashboard.state:DashboardState._broadcast")
    tap(object(), {"_type": "slots", "_slots_list": []})  # every tab closed: the run is removable in principle
    after = hook.ctx.hooks.find("after", "route:DELETE /api/sessions/{key}")
    run(after(_Resp({"ok": False}), _delete_req("dashboard:orch")))  # the host refused the delete
    run(after(_Resp({"error": "x"}, status=500), _delete_req("dashboard:orch")))
    run(after(_Resp({"ok": True}), _delete_req("dashboard:someone-else")))  # a chat in no run
    assert hook.mod._store.meta("dashboard:orch") is not None
    # the user switched the behaviour off: the host's delete leaves the record (the page's ✕ still works)
    hook.ctx.config["follow_session_delete"] = False
    run(after(_Resp({"ok": True}), _delete_req("dashboard:orch")))
    assert hook.mod._store.meta("dashboard:orch") is not None


def test_bulk_clear_removes_runs_whose_orchestrator_transcript_is_gone(hook):
    activate(hook)
    svc = hook.mod._service
    tap = _start_run(hook)
    run(svc.op_bus_run("dashboard:orch", {"action": "close"}))
    # a second, older run that exists on disk only (its sessions predate this gateway)
    hook.mod._store.ensure_run("dashboard:old")
    hook.mod._store.assign_nick("dashboard:old", "dashboard:old", "orchestrator")
    tap(object(), {"_type": "slots", "_slots_list": []})
    hook.mod._state_ref = types.SimpleNamespace(conversation_log=types.SimpleNamespace(has_log=lambda k: k == "dashboard:orch"))
    after = hook.ctx.hooks.find("after", "route:DELETE /api/sessions")
    run(after(_Resp({"ok": True, "cleared": 3}), types.SimpleNamespace(match_info={}, headers={})))
    assert hook.mod._store.meta("dashboard:old") is None  # transcript gone → record gone
    assert hook.mod._store.meta("dashboard:orch") is not None  # transcript still in history → kept (archived)


def test_runs_remove_route_refuses_a_live_run_and_removes_an_archived_one(hook):
    activate(hook)
    tap = _start_run(hook)
    remove = hook.ctx.routes.regs[("POST", "runs/remove")]
    runs = hook.ctx.routes.regs[("GET", "runs")]
    graph = hook.ctx.routes.regs[("GET", "graph")]

    def req(body):
        return types.SimpleNamespace(json=lambda: body, query={}, headers={})

    assert remove(req(None))[0] == 400 and remove(req({}))[0] == 400
    status, body = remove(req({"run": "dashboard:orch"}))
    assert status == 409 and body["code"] == "run_live"
    assert hook.mod._store.meta("dashboard:orch") is not None
    snap = graph(types.SimpleNamespace(query={"run": ["dashboard:orch"]}))
    assert snap["sessions_gone"] is False and runs(None)["runs"][0]["live"] is True

    tap(object(), {"_type": "slots", "_slots_list": [dict(s) for s in _slots(2) if s["key"] == "w1"]})  # one worker tab open
    assert remove(req({"run": "orch"}))[0] == 409  # the bare slot key is accepted too
    tap(object(), {"_type": "slots", "_slots_list": []})  # every tab closed (to history, not deleted)
    snap = graph(types.SimpleNamespace(query={"run": ["dashboard:orch"]}))
    assert snap["sessions_gone"] is True and "from_disk" not in snap  # the reducer still remembers the nodes
    assert runs(None)["runs"][0]["live"] is False  # the picker shows ○ for it
    assert remove(req({"run": "dashboard:orch"})) == {"ok": True, "run": "dashboard:orch"}
    assert hook.mod._store.meta("dashboard:orch") is None and hook.mod._graph.runs() == []
    assert remove(req({"run": "dashboard:orch"}))[0] == 404


def test_sweep_ages_out_never_closed_runs_but_never_a_live_one(hook, monkeypatch):
    hook.ctx.config["run_retention_days"] = 1
    activate(hook)
    store = hook.mod._store
    tap = _start_run(hook)  # never closed; its tabs are open
    store.ensure_run("dashboard:stale")  # never closed, sessions long gone
    store.ensure_run("dashboard:recent")
    store.update_meta("dashboard:closed-old", closed_at=time.time() - 3 * 86400)
    two_days_ago = time.time() - 2 * 86400
    for d in (store.run_dir("dashboard:stale"), store.run_dir("dashboard:orch")):
        for f in d.iterdir():
            os.utime(f, (two_days_ago, two_days_ago))
    monkeypatch.setattr(hook.mod, "_last_sweep", 0.0)
    tap(object(), {"_type": "slots", "_slots_list": _slots(2)})  # the hourly tick rides on the slots push
    left = {m.run for m in store.list_runs()}
    assert left == {"dashboard:orch", "dashboard:recent"}  # stale + closed-old swept; the live old run kept
    assert hook.mod._graph.runs() == ["dashboard:orch"]
    # not more often than hourly
    os.utime(store.run_dir("dashboard:recent") / "meta.json", (two_days_ago, two_days_ago))
    tap(object(), {"_type": "slots", "_slots_list": _slots(2)})
    assert store.meta("dashboard:recent") is not None


def test_first_slots_push_after_activation_drops_transcript_gone_runs_without_waiting_an_hour(hook):
    activate(hook)  # the activation sweep ran (retention only) and stamped _last_sweep = now
    store = hook.mod._store
    store.ensure_run("dashboard:deleted-while-off")  # its chats were deleted from history while the mod was inactive
    store.assign_nick("dashboard:deleted-while-off", "dashboard:deleted-while-off", "orchestrator")
    store.ensure_run("dashboard:kept")
    store.assign_nick("dashboard:kept", "dashboard:kept", "orchestrator")
    state = types.SimpleNamespace(conversation_log=types.SimpleNamespace(has_log=lambda k: k == "dashboard:kept"))
    tap = hook.ctx.hooks.find("before", "kiro_crew.dashboard.state:DashboardState._broadcast")
    tap(state, {"_type": "slots", "_slots_list": []})  # first push: the host state is captured here
    assert store.meta("dashboard:deleted-while-off") is None  # gone now, not at the hourly tick
    assert store.meta("dashboard:kept") is not None
    # and the forced first pass does not repeat: a later gone transcript waits for the hourly tick as before
    store.ensure_run("dashboard:later")
    store.assign_nick("dashboard:later", "dashboard:later", "orchestrator")
    tap(state, {"_type": "slots", "_slots_list": []})
    assert store.meta("dashboard:later") is not None
