"""The gateway half: the KAS verdict is read from the host config, never written; the
activation bridge narrows the host's reserved-id refusals to the four mode ids only."""

import importlib.util
import json
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hook", "__init__.py")
MODES = ["spec", "quick-spec", "bug-fix", "plan"]


def _load():
    sys.modules.setdefault("floofy", types.ModuleType("floofy"))
    spec = importlib.util.spec_from_file_location("kiro_spec_plan_hook", HOOK)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    return hook


class Hooks:
    """The loader's ``ctx.hooks`` facade: records replace() targets; refuses the ones in ``missing``."""

    def __init__(self, missing=()):
        self.replaced = {}
        self.missing = set(missing)

    def replace(self, target, fn):
        if target in self.missing:
            raise LookupError("no attribute %r" % target.split(":")[1])
        self.replaced[target] = fn


class Ctx:
    def __init__(self, home, hooks=None):
        self.host = types.SimpleNamespace(host_home=home, version="0.8.0.9")
        self.state = {}
        self.routes = types.SimpleNamespace(added=[], add=lambda m, p, h: self.routes.added.append((m, p)))
        self.log = types.SimpleNamespace(info=lambda *a: self.logged.append(("info", a[0] % a[1:] if a[1:] else a[0])),
                                         warning=lambda *a: self.logged.append(("warning", a[0] % a[1:] if a[1:] else a[0])))
        self.logged = []
        if hooks is not None:
            self.hooks = hooks


def _host(reserved):
    """Stand in for the host's ``kiro_crew.agent_files`` with the given reserved set (None = no such host)."""
    for name in ("kiro_crew", "kiro_crew.agent_files"):
        sys.modules.pop(name, None)
    if reserved is None:
        return
    pkg = types.ModuleType("kiro_crew")
    pkg.__path__ = []
    mod = types.ModuleType("kiro_crew.agent_files")
    mod.KAS_RESERVED_AGENT_IDS = frozenset(reserved)
    pkg.agent_files = mod
    sys.modules["kiro_crew"] = pkg
    sys.modules["kiro_crew.agent_files"] = mod


def test_verdict_and_lifecycle(hook):
    with tempfile.TemporaryDirectory() as home:
        cfg = os.path.join(home, "config.json")
        _host(None)
        # No config at all -> not kas, with the hint; bridge not yet installed.
        v = hook.verdict(home)
        assert v["kas"] is False and v["backend"] == "(default)" and v["modes"] == MODES and v["hint"] == hook.ENABLE_HINT, v
        assert v["bridge"] == {"needed": None, "seams": {}}, v["bridge"]
        json.dump({"agent": {"acp_backend": ""}}, open(cfg, "w"))
        assert hook.verdict(home)["kas"] is False
        json.dump({"agent": {"acp_backend": "kas", "model": "x"}}, open(cfg, "w"))
        v = hook.verdict(home)
        assert v["kas"] is True and v["hint"] is None and v["backend"] == "kas", v
        open(cfg, "w").write("{not json")
        assert hook.verdict(home)["kas"] is False

        # activate publishes the facts and warns when off; config is never written.
        json.dump({"agent": {"acp_backend": ""}}, open(cfg, "w"))
        before = open(cfg).read()
        ctx = Ctx(home, Hooks())
        hook.activate(ctx)
        assert ctx.state["kas"] is False and ctx.state["hint"] == hook.ENABLE_HINT
        assert any(level == "warning" and "acp_backend" in msg for level, msg in ctx.logged), ctx.logged
        assert ("GET", "status") in ctx.routes.added
        assert open(cfg).read() == before, "the hook must never write the host config"
        assert hook._status(None)["kas"] is False
        # An older host (no reserved set): nothing hooked, and the status says why.
        assert ctx.state["bridge"]["needed"] is False and ctx.state["bridge"]["seams"] == {}, ctx.state["bridge"]
        assert ctx.hooks.replaced == {}
        json.dump({"agent": {"acp_backend": "kas"}}, open(cfg, "w"))
        assert hook.refresh()["kas"] is True and ctx.state["kas"] is True
        hook.deactivate(ctx)
        assert hook._status(None)["bridge"] == {"needed": None, "seams": {}}


def test_bridge_registers_on_a_reserving_host(hook):
    with tempfile.TemporaryDirectory() as home:
        json.dump({"agent": {"acp_backend": "kas"}}, open(os.path.join(home, "config.json"), "w"))
        _host(["default", "vibe", "spec", "quick-spec", "bug-fix", "plan", "autonomous"])
        ctx = Ctx(home, Hooks())
        hook.activate(ctx)
        assert set(ctx.hooks.replaced) == set(hook.SEAMS.values()), ctx.hooks.replaced
        assert ctx.state["bridge"] == {"needed": True, "seams": {"project": "ok", "wire": "ok", "activate": "ok"}}, ctx.state["bridge"]
        assert any("activation bridge installed" in msg for _, msg in ctx.logged), ctx.logged
        assert hook._status(None)["bridge"]["seams"]["wire"] == "ok"
        hook.deactivate(ctx)

        # A host that moved one seam: the other two still take, status names the gap, no exception escapes.
        ctx = Ctx(home, Hooks(missing=[hook.SEAM_WIRE]))
        hook.activate(ctx)
        seams = ctx.state["bridge"]["seams"]
        assert seams["project"] == "ok" and seams["activate"] == "ok" and seams["wire"].startswith("missing:"), seams
        assert any("without the wire seam" in msg for level, msg in ctx.logged if level == "warning"), ctx.logged
        hook.deactivate(ctx)

        # A loader without ctx.hooks: reported, never raised.
        ctx = Ctx(home)
        hook.activate(ctx)
        assert set(ctx.state["bridge"]["seams"].values()) == {"no-hooks-api"}, ctx.state["bridge"]
        hook.deactivate(ctx)
        _host(None)


def test_seam_behaviour(hook):
    calls = []

    def call_next(*a, **kw):
        calls.append((a, kw))
        return {"id": a[0], "prompt": a[2], "tools": ["*"], "host": True}

    # project: other agents pass straight through with every argument intact.
    out = hook.project(call_next, "o3r_default", {"description": "d"}, "p", stub_server_names=frozenset({"x"}), member_dispatch=True)
    assert out["host"] is True and calls[-1] == (("o3r_default", {"description": "d"}, "p"), {"stub_server_names": frozenset({"x"}), "member_dispatch": True}), calls
    # project: a mode id never reaches the host (which would raise) and yields the tool-less placeholder.
    n = len(calls)
    out = hook.project(call_next, "spec", {"description": "Kiro mode: spec"}, "the drop-in prompt", stub_server_names=frozenset())
    assert len(calls) == n, "the host projection must not run for a mode id"
    assert out == {"id": "spec", "prompt": "the drop-in prompt", "tools": [], "description": "Kiro mode: spec"}, out
    out = hook.project(call_next, "plan", {}, "   ")
    assert out["prompt"] == hook._HANDLE_PROMPT and "description" not in out, out
    for mode in MODES:
        assert hook.project(call_next, mode, {}, "x")["id"] == mode

    # wire: the mode placeholder is removed; anything else is forwarded untouched; an
    # emptied list reaches the host as [] (its own "nothing to attach").
    wired = []

    def attach(params, agents):
        wired.append((params, agents))
        if agents:
            params.setdefault("_meta", {})["kiro"] = {"customAgents": agents}

    params = {}
    hook.wire(attach, params, [{"id": "spec", "prompt": "x", "tools": []}])
    assert wired[-1] == (params, []) and params == {}, (wired[-1], params)
    params = {}
    keep = [{"id": "o3r_default", "prompt": "x", "tools": ["*"]}]
    hook.wire(attach, params, keep)
    assert wired[-1][1] is keep and params["_meta"]["kiro"]["customAgents"] == keep
    params = {}
    hook.wire(attach, params, [{"id": "quick-spec", "prompt": "x", "tools": []}, {"id": "other", "prompt": "y", "tools": []}])
    assert [a["id"] for a in params["_meta"]["kiro"]["customAgents"]] == ["other"]
    hook.wire(attach, {}, None)
    assert wired[-1][1] is None

    # activation: a mode id is admitted whatever the engine stamped; other ids keep the host's verdict.
    def refusal(harness, agent, resp):
        return "Rename this crewmate's template: %s is reserved" % agent

    harness = object()
    resp = {"modes": {"availableModes": [{"id": "spec", "_meta": {"kiro": {"resource": {"source": {"origin": "bundled"}}}}}]}}
    assert hook.activation(refusal, harness, "spec", resp) is None
    assert hook.activation(refusal, harness, "bug-fix", resp) is None
    assert hook.activation(refusal, harness, "my-crewmate", resp) == "Rename this crewmate's template: my-crewmate is reserved"
    assert hook.activation(refusal, harness, "", resp).endswith("is reserved")
    assert hook.is_mode(None) is False and hook.is_mode("Spec") is False and hook.is_mode("spec") is True


def main():
    hook = _load()
    test_verdict_and_lifecycle(hook)
    test_bridge_registers_on_a_reserving_host(hook)
    test_seam_behaviour(hook)
    print("kiro-spec-plan hook OK: verdict from config, warning when off, read-only; "
          "bridge narrows the three reserved-id seams to the four mode ids")


if __name__ == "__main__":
    main()
