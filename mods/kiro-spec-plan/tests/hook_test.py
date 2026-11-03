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


#: The modules a 0.11 host binds ``kas_wire_agent_id`` / ``attach_kas_custom_agents`` into.
HOST_011_MODULES = {
    "kiro_crew.acp._dispatch": ("kas_wire_agent_id", "attach_kas_custom_agents"),
    "kiro_crew.acp.kas_agents": ("kas_wire_agent_id",),
    "kiro_crew.acp.runtime": ("kas_wire_agent_id", "attach_kas_custom_agents"),
    "kiro_crew.acp.runtime_session_create": ("kas_wire_agent_id", "attach_kas_custom_agents"),
    "kiro_crew.acp.runtime_session_load": ("kas_wire_agent_id", "attach_kas_custom_agents"),
}


def _host(reserved, renames=False):
    """Stand in for the host's ``kiro_crew.agent_files`` with the given reserved set.

    ``None`` = no such host: the names are pinned to ``None`` in ``sys.modules``, which
    makes their import raise, so the test says the same thing under an interpreter that
    has the real KiroCrew installed as under one that does not. ``renames`` adds the 0.11
    shape: ``kas_wire_agent_id`` in ``agent_files`` and every module that imports it."""
    for name in [n for n in list(sys.modules) if n == "kiro_crew" or n.startswith("kiro_crew.")]:
        sys.modules.pop(name, None)
    if reserved is None:
        sys.modules["kiro_crew.agent_files"] = None
        return
    pkg = types.ModuleType("kiro_crew")
    pkg.__path__ = []
    mod = types.ModuleType("kiro_crew.agent_files")
    mod.KAS_RESERVED_AGENT_IDS = frozenset(reserved)
    pkg.agent_files = mod
    sys.modules["kiro_crew"] = pkg
    sys.modules["kiro_crew.agent_files"] = mod
    if not renames:
        return

    def kas_wire_agent_id(agent_id):
        return agent_id + "-crewmate" if agent_id in mod.KAS_RESERVED_AGENT_IDS else agent_id

    mod.kas_wire_agent_id = kas_wire_agent_id
    acp = types.ModuleType("kiro_crew.acp")
    acp.__path__ = []
    sys.modules["kiro_crew.acp"] = acp
    pkg.acp = acp
    for name, attrs in HOST_011_MODULES.items():
        sub = types.ModuleType(name)
        for attr in attrs:
            setattr(sub, attr, kas_wire_agent_id if attr == "kas_wire_agent_id" else (lambda params, agents: None))
        sys.modules[name] = sub
        setattr(acp, name.rsplit(".", 1)[1], sub)


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
        # The stub host is a 0.8-0.10 shape: it has the five original seams and none of the
        # 0.11 bindings (no acp.runtime_session_* modules, no kas_wire_agent_id), which read
        # ``absent`` -- a host-version fact, not a gap -- and are never passed to replace().
        original = {hook.SEAM_PROJECT, hook.SEAM_WIRE, hook.SEAM_WIRE_RUNTIME, hook.SEAM_ACTIVATE, hook.SEAM_PROMPT}
        assert set(ctx.hooks.replaced) == original, ctx.hooks.replaced
        seams = ctx.state["bridge"]["seams"]
        assert ctx.state["bridge"]["needed"] is True
        assert {n: s for n, s in seams.items() if n not in hook._HOST_SHAPED_SEAMS} == {"project": "ok", "wire": "ok", "wire_runtime": "ok", "activate": "ok", "prompt": "ok"}, seams
        assert set(seams) == set(hook.SEAMS) and all(seams[n] == "absent" for n in hook._HOST_SHAPED_SEAMS), seams
        # One function, two bindings: both wire seams run the same hook.
        assert ctx.hooks.replaced[hook.SEAM_WIRE] is ctx.hooks.replaced[hook.SEAM_WIRE_RUNTIME] is hook.wire
        installed = [msg for level, msg in ctx.logged if "activation bridge installed" in msg]
        assert installed and "pinned" not in installed[0], ctx.logged
        assert not any(level == "warning" for level, _ in ctx.logged), ctx.logged
        assert hook._status(None)["bridge"]["seams"]["wire"] == "ok"
        hook.deactivate(ctx)

        # A 0.11-shaped host: it binds kas_wire_agent_id in six modules and
        # attach_kas_custom_agents in four; every binding is hooked and the log says so.
        _host(["default", "vibe", "spec", "quick-spec", "bug-fix", "plan", "autonomous"], renames=True)
        ctx = Ctx(home, Hooks())
        hook.activate(ctx)
        assert set(ctx.hooks.replaced) == set(hook.SEAMS.values()), set(hook.SEAMS.values()) - set(ctx.hooks.replaced)
        assert set(ctx.state["bridge"]["seams"].values()) == {"ok"}, ctx.state["bridge"]
        for name in hook._WIRE_ID_SEAMS:
            assert ctx.hooks.replaced[hook.SEAMS[name]] is hook.wire_id
        for name in hook._WIRE_SEAMS:
            assert ctx.hooks.replaced[hook.SEAMS[name]] is hook.wire
        installed = [msg for level, msg in ctx.logged if "activation bridge installed" in msg]
        assert installed and "pinned to the bundled ids" in installed[0], ctx.logged
        hook.deactivate(ctx)

        # The same host with the create-path wire-id binding gone: the bridge is reported
        # incomplete for the 0.11 refusal by name, not as "installed".
        ctx = Ctx(home, Hooks(missing=[hook.SEAM_WIRE_ID_CREATE]))
        hook.activate(ctx)
        assert ctx.state["bridge"]["seams"]["wire_id_create"].startswith("missing:"), ctx.state["bridge"]
        warned = [msg for level, msg in ctx.logged if level == "warning" and "without the wire-id seam" in msg]
        assert warned and hook.SEAM_WIRE_ID_CREATE in warned[0] and "is not available on this session" in warned[0], ctx.logged
        assert not any("activation bridge installed" in msg for _, msg in ctx.logged), ctx.logged
        hook.deactivate(ctx)
        _host(["default", "vibe", "spec", "quick-spec", "bug-fix", "plan", "autonomous"])

        # A host that moved one wire binding: the rest still take, status names the gap, the
        # warning names the binding, no exception escapes.
        ctx = Ctx(home, Hooks(missing=[hook.SEAM_WIRE]))
        hook.activate(ctx)
        seams = ctx.state["bridge"]["seams"]
        assert seams["project"] == "ok" and seams["activate"] == "ok" and seams["wire_runtime"] == "ok" and seams["wire"].startswith("missing:"), seams
        warned = [msg for level, msg in ctx.logged if level == "warning" and "without the wire seam" in msg]
        assert warned and hook.SEAM_WIRE in warned[0] and hook.SEAM_WIRE_RUNTIME not in warned[0], ctx.logged
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

    # wire_id: a mode id is its own wire id; every other id keeps the host's alias, arguments intact.
    seen = []

    def host_wire_id(agent_id, *a, **k):
        seen.append((agent_id, a, k))
        return agent_id + "-crewmate"

    for mode in MODES:
        assert hook.wire_id(host_wire_id, mode) == mode
    assert not seen, "the host rename must not run for a mode id"
    assert hook.wire_id(host_wire_id, "default") == "default-crewmate"
    assert hook.wire_id(host_wire_id, "vibe", 1, flag=True) == "vibe-crewmate" and seen[-1] == ("vibe", (1,), {"flag": True})
    assert hook.wire_id(host_wire_id, "o3r_default") == "o3r_default-crewmate"
    assert hook.wire_id(host_wire_id, "Spec") == "Spec-crewmate", "case matters: only the exact mode id is pinned"


def test_wire_covers_session_new_on_the_real_host(hook):
    """Regression for 0.2.0: ``session/new`` params are built in ``_dispatch``, which calls its OWN
    binding of ``attach_kas_custom_agents``; hooking only ``runtime``'s left the placeholder on
    the wire. Runs against the installed host when importable, else is skipped (the stub host
    the other tests build has no ``acp`` package)."""
    for name in [n for n in sys.modules if n == "kiro_crew" or n.startswith("kiro_crew.")]:
        sys.modules.pop(name, None)
    try:
        from kiro_crew.acp import _dispatch, runtime  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 - not a KiroCrew interpreter
        print("  (session/new regression skipped: %s)" % exc)
        return
    original = _dispatch.attach_kas_custom_agents
    assert runtime.attach_kas_custom_agents is original, "the host no longer imports the function into runtime; re-measure the seams"
    placeholder = {"id": "spec", "prompt": "HANDLE", "tools": []}
    saved = (_dispatch.attach_kas_custom_agents, runtime.attach_kas_custom_agents)
    try:
        # What the loader's replace() does for each target: rebind that module's attribute.
        for module, target in ((_dispatch, hook.SEAM_WIRE), (runtime, hook.SEAM_WIRE_RUNTIME)):
            assert target.split(":")[0] == module.__name__ and target.split(":")[1] == "attach_kas_custom_agents", target
            setattr(module, "attach_kas_custom_agents", lambda params, agents, *a, **k: hook.wire(original, params, agents, *a, **k))
        params = _dispatch.build_session_new_params("/tmp", mcp_servers=[], kas_custom_agents=[placeholder])
        assert "_meta" not in params, params
        params = _dispatch.build_session_new_params("/tmp", mcp_servers=[], kas_custom_agents=[placeholder, {"id": "o3r_default", "prompt": "p", "tools": []}])
        assert [a["id"] for a in params["_meta"]["kiro"]["customAgents"]] == ["o3r_default"], params
        load_params = {"mcpServers": []}
        runtime.attach_kas_custom_agents(load_params, [placeholder])
        assert "_meta" not in load_params, load_params
    finally:
        _dispatch.attach_kas_custom_agents, runtime.attach_kas_custom_agents = saved


def test_wire_id_pins_mode_ids_on_the_real_host(hook):
    """Regression for 0.2.1 on KiroCrew 0.11.1.5: the host renames a reserved id to
    ``<id>-crewmate`` on every wire crossing (``kas_wire_agent_id``), the bridge strips the
    mode entry, so the host looked for ``spec-crewmate`` in the advertised modes and
    refused ("Agent mode 'spec' is not available on this session"). With the seam on
    every binding, a mode id crosses as itself and the batch inverse still maps back.
    Skipped on an interpreter without a renaming host."""
    for name in [n for n in sys.modules if n == "kiro_crew" or n.startswith("kiro_crew.")]:
        sys.modules.pop(name, None)
    try:
        from kiro_crew import agent_files  # noqa: PLC0415
        from kiro_crew.acp import _dispatch, kas_agents, runtime, runtime_session_create, runtime_session_load  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 - not a KiroCrew interpreter, or one before 0.11
        print("  (wire-id regression skipped: %s)" % exc)
        return
    if not hasattr(agent_files, "kas_wire_agent_id"):
        print("  (wire-id regression skipped: this host does not rename reserved ids)")
        return
    original = agent_files.kas_wire_agent_id
    assert original("spec") == "spec-crewmate" and original("o3r_default") == "o3r_default", "re-measure the host's rename"
    bindings = {m: m.kas_wire_agent_id for m in (agent_files, _dispatch, kas_agents, runtime, runtime_session_create, runtime_session_load)}
    assert all(fn is original for fn in bindings.values()), "the host no longer imports the function by name everywhere; re-measure the seams"
    for name in hook._WIRE_ID_SEAMS:
        assert hook._host_has(hook.SEAMS[name]), hook.SEAMS[name]
    assert hook._host_has(hook.SEAM_WIRE_CREATE) and hook._host_has(hook.SEAM_WIRE_LOAD)
    try:
        # What the loader's replace() does for each wire-id target: rebind that module's attribute.
        for module in bindings:
            module.kas_wire_agent_id = lambda agent_id, *a, **k: hook.wire_id(original, agent_id, *a, **k)
        # The two start paths derive the set_mode id from their own binding.
        assert runtime_session_create.kas_wire_agent_id("spec") == "spec"
        assert runtime_session_load.kas_wire_agent_id("plan") == "plan"
        assert runtime_session_create.kas_wire_agent_id("default") == "default-crewmate"
        # The batch rename (not stripped here, to see the rename alone) sends a mode id as itself and
        # a reserved crewmate id under its alias; the inverse maps both back.
        params = {}
        _dispatch.attach_kas_custom_agents(params, [{"id": "spec", "prompt": "x", "tools": []}, {"id": "vibe", "prompt": "y", "tools": []}])
        sent = [a["id"] for a in params["_meta"]["kiro"]["customAgents"]]
        assert sent == ["spec", "vibe-crewmate"], sent
        batch = [{"id": "spec"}, {"id": "vibe"}]
        assert _dispatch.kas_crew_agent_id(batch, "spec") == "spec" and _dispatch.kas_crew_agent_id(batch, "vibe-crewmate") == "vibe"
        # With the wire seam too (the real install), the mode entry never goes out at all.
        params = {}
        hook.wire(_dispatch.attach_kas_custom_agents, params, [{"id": "spec", "prompt": "x", "tools": []}])
        assert "_meta" not in params, params
    finally:
        for module, fn in bindings.items():
            module.kas_wire_agent_id = fn


def test_prompt_seam_keeps_the_handle_text_out_of_a_kas_session(hook):
    """The in-band [AGENT SYSTEM PROMPT] block is empty for a mode id whose session runs on KAS; kept otherwise."""
    HANDLE = "This agent is a handle ... KiroCrew is not running on the KAS backend ..."
    calls = []

    def resolve(builder, agent, *a, **k):
        calls.append((agent, k.get("session_key")))
        return HANDLE if agent in MODES else "persona of %s" % agent

    builder = object()
    with tempfile.TemporaryDirectory() as home:
        json.dump({"agent": {"acp_backend": "kas"}}, open(os.path.join(home, "config.json"), "w"))
        _host(["spec", "quick-spec", "bug-fix", "plan"])
        ctx = Ctx(home, Hooks())
        hook.activate(ctx)
        try:
            # Router active: its per-session answer decides, config does not.
            router = types.ModuleType("abr_stub")
            answers = {"dashboard:on-kas": "kas", "dashboard:on-kiro": "kiro", "dashboard:unknown": None}
            router.session_backend = lambda key: answers.get(key)
            sys.modules[hook.ROUTER_MODULE] = router
            assert hook.prompt(resolve, builder, "spec", project=None, mode="", session_key="dashboard:on-kas", is_cc=False, private_owner=False, session_start=True) == ""
            assert calls == [], "call_next must not run for a KAS mode session (its read would be wasted and its text leaks)"
            assert hook.prompt(resolve, builder, "plan", project=None, mode="", session_key="dashboard:on-kiro", is_cc=False, private_owner=False, session_start=True) == HANDLE
            # The router never built this one (member DM, older key): config says kas -> empty.
            assert hook.prompt(resolve, builder, "quick-spec", project=None, mode="", session_key="dashboard:unknown", is_cc=False, private_owner=False, session_start=False) == ""
            # A router that raises is not a verdict: fall through to config.
            router.session_backend = lambda key: 1 / 0
            assert hook.prompt(resolve, builder, "bug-fix", project=None, mode="", session_key="dashboard:x", is_cc=False, private_owner=False, session_start=True) == ""
            # Any other agent passes straight through, on KAS too.
            assert hook.prompt(resolve, builder, "o3r_default", project=None, mode="", session_key="dashboard:on-kas", is_cc=False, private_owner=False, session_start=True) == "persona of o3r_default"
            assert hook.prompt(resolve, builder, "", project=None, mode="", session_key="dashboard:on-kas", is_cc=False, private_owner=False, session_start=True) == "persona of "
            sys.modules.pop(hook.ROUTER_MODULE, None)
            # No router: the host's configured backend decides.
            assert hook.prompt(resolve, builder, "spec", project=None, mode="", session_key="dashboard:any", is_cc=False, private_owner=False, session_start=True) == ""
            json.dump({"agent": {"acp_backend": ""}}, open(os.path.join(home, "config.json"), "w"))
            assert hook.prompt(resolve, builder, "spec", project=None, mode="", session_key="dashboard:any", is_cc=False, private_owner=False, session_start=True) == HANDLE
        finally:
            sys.modules.pop(hook.ROUTER_MODULE, None)
            hook.deactivate(ctx)
            _host(None)
    # Deactivated (no ctx) and no router: never empties, the host's own behaviour.
    assert hook.prompt(resolve, builder, "spec", project=None, mode="", session_key="dashboard:any", is_cc=False, private_owner=False, session_start=True) == HANDLE


def test_prompt_seam_matches_the_real_host_signature(hook):
    """The host passes session_key by keyword to _resolve_agent_prompt; a rename there must fail here, not in production."""
    for name in [n for n in sys.modules if n == "kiro_crew" or n.startswith("kiro_crew.")]:
        sys.modules.pop(name, None)  # the stub host an earlier test installed
    try:
        import inspect  # noqa: PLC0415
        from kiro_crew.context import ContextBuilder  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        print("  (signature check skipped: %s)" % exc)
        return
    params = inspect.signature(ContextBuilder._resolve_agent_prompt).parameters
    assert "session_key" in params and list(params)[:2] == ["self", "agent"], list(params)
    target_module, target_attr = hook.SEAM_PROMPT.split(":")
    assert target_module == "kiro_crew.context" and target_attr == "ContextBuilder._resolve_agent_prompt"


def main():
    hook = _load()
    test_verdict_and_lifecycle(hook)
    test_bridge_registers_on_a_reserving_host(hook)
    test_seam_behaviour(hook)
    test_wire_covers_session_new_on_the_real_host(hook)
    test_wire_id_pins_mode_ids_on_the_real_host(hook)
    test_prompt_seam_keeps_the_handle_text_out_of_a_kas_session(hook)
    test_prompt_seam_matches_the_real_host_signature(hook)
    print("kiro-spec-plan hook OK: verdict from config, warning when off, read-only; "
          "bridge narrows the reserved-id seams (every wire binding) to the four mode ids and pins their "
          "wire id to the bundled id on a 0.11 host; no in-band handle prompt on KAS")


if __name__ == "__main__":
    main()
