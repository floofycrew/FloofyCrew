"""The hook registry (Requirement 3.4): semantics, fail-open faults, routes, and identity-restoring unwind."""
from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from floofy_core.resolver import Reason
from floofy_loader.consent import write_consent
from floofy_loader.hooks_registry import HookRegistry, HookTargetError, parse_target
from floofy_loader.runtime import LoaderRuntime

from test_boot import make_mod  # noqa: F401 (shared fixture helper)
from floofy_loader.paths import FloofyPaths


@pytest.fixture
def target_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """A fake host module with a sync function, an async function and a class method."""
    module = types.ModuleType("floofy_test_target")

    def greet(name: str, punctuation: str = "!") -> str:
        return f"hello {name}{punctuation}"

    async def fetch_status(kind: str) -> dict:
        await asyncio.sleep(0)
        return {"kind": kind, "ok": True}

    class Service:
        def compute(self, x: int) -> int:
            return x * 2

    module.greet = greet
    module.fetch_status = fetch_status
    module.Service = Service
    monkeypatch.setitem(sys.modules, "floofy_test_target", module)
    return module


def test_parse_target():
    assert parse_target("kiro_crew.dashboard.handlers.core:index") == ("attr", "kiro_crew.dashboard.handlers.core", "index")
    assert parse_target("route:get /api/status") == ("route", "GET", "/api/status")
    for bad in ("nocolon", ":x", "route:GET", "route: /x"):
        with pytest.raises(HookTargetError):
            parse_target(bad)


def test_before_after_replace_semantics_with_attribution(target_module):
    registry = HookRegistry()
    original = target_module.greet
    calls: list[str] = []
    a, b = registry.for_mod("alpha"), registry.for_mod("beta")
    a.before("floofy_test_target:greet", lambda name, **kw: calls.append(f"alpha-before:{name}"))
    b.replace("floofy_test_target:greet", lambda call_next, name, **kw: call_next(name.upper(), **kw))
    a.after("floofy_test_target:greet", lambda result, *args, **kw: result + " (seen by alpha)")
    assert target_module.greet is not original
    assert target_module.greet("bob", punctuation="?") == "hello BOB? (seen by alpha)"
    assert calls == ["alpha-before:bob"]
    assert [(r.mod_id, r.kind) for r in registry.registrations()] == [("alpha", "before"), ("beta", "replace"), ("alpha", "after")]
    assert [r.kind for r in a.registrations()] == ["before", "after"]
    assert registry.deactivate("beta") == 1
    assert target_module.greet("bob") == "hello bob! (seen by alpha)", "beta's replacement is gone, alpha's hooks stay"
    assert registry.deactivate("alpha") == 2
    assert target_module.greet is original and registry.targets() == []


def test_class_method_target(target_module):
    registry = HookRegistry()
    original = target_module.Service.compute
    registry.for_mod("m").after("floofy_test_target:Service.compute", lambda result, self, x: result + 1)
    assert target_module.Service().compute(5) == 11
    registry.shutdown()
    assert target_module.Service.compute is original and target_module.Service().compute(5) == 10


def test_async_original_gets_an_async_wrapper(target_module):
    registry = HookRegistry()
    original = target_module.fetch_status
    seen: list[str] = []

    async def before(kind):
        seen.append(kind)

    async def replace(call_next, kind):
        result = await call_next(kind)
        return {**result, "replaced": True}

    def after(result, kind):
        return {**result, "after": True}

    m = registry.for_mod("m")
    m.before("floofy_test_target:fetch_status", before)
    m.replace("floofy_test_target:fetch_status", replace)
    m.after("floofy_test_target:fetch_status", after)
    assert asyncio.iscoroutinefunction(target_module.fetch_status)
    result = asyncio.run(target_module.fetch_status("health"))
    assert result == {"kind": "health", "ok": True, "replaced": True, "after": True} and seen == ["health"]
    registry.shutdown()
    assert target_module.fetch_status is original


def test_a_raising_hook_disables_only_its_mod_and_falls_through(target_module):
    faults: list[tuple[str, str, str]] = []
    registry = HookRegistry(on_fault=lambda mod, target, exc: faults.append((mod, target, type(exc).__name__)))
    original = target_module.greet
    bad, good = registry.for_mod("bad"), registry.for_mod("good")

    def explode(*_a, **_k):
        raise ValueError("kaboom")

    bad.before("floofy_test_target:greet", explode)
    bad.after("floofy_test_target:greet", lambda result, *a, **k: "never")
    bad.replace("floofy_test_target:greet", lambda call_next, *a, **k: "never either")
    good.after("floofy_test_target:greet", lambda result, *a, **k: result + "+good")
    assert target_module.greet("x") == "hello x!+good", "the faulted mod's replace/after hooks are dropped in the same call"
    assert faults == [("bad", "floofy_test_target:greet", "ValueError")]
    assert registry.registrations("bad") == [] and [r.mod_id for r in registry.registrations()] == ["good"]
    assert registry.faults[0]["error"] == "ValueError: kaboom"
    registry.shutdown()
    assert target_module.greet is original


def test_replace_chain_order_and_fallthrough(target_module):
    registry = HookRegistry()
    r = registry.for_mod("r")
    r.replace("floofy_test_target:greet", lambda call_next, name, **kw: "[1 " + call_next(name, **kw) + "]")
    registry.for_mod("s").replace("floofy_test_target:greet", lambda call_next, name, **kw: "[2 " + call_next(name, **kw) + "]")

    def broken(call_next, name, **kw):
        raise RuntimeError("no")

    registry.for_mod("t").replace("floofy_test_target:greet", broken)
    assert target_module.greet("z") == "[2 [1 hello z!]]", "last registered is outermost; the broken outer one falls through"
    assert registry.registrations("t") == []


def test_unknown_targets_raise(target_module):
    registry = HookRegistry(router=lambda: None)
    with pytest.raises(HookTargetError):
        registry.for_mod("m").after("floofy_test_target:nope", lambda r: r)
    with pytest.raises(HookTargetError):
        registry.for_mod("m").after("no_such_module_xyz:thing", lambda r: r)
    with pytest.raises(HookTargetError, match="not reachable"):
        registry.for_mod("m").after("route:GET /api/status", lambda r: r)
    assert registry.targets() == []


class FakeRoute:
    """The parts of aiohttp's ResourceRoute the registry relies on."""

    def __init__(self, method: str, path: str, handler):
        self.method = method
        self.resource = types.SimpleNamespace(canonical=path)
        self._handler = handler

    @property
    def handler(self):
        return self._handler


class FakeRouter:
    def __init__(self, routes):
        self._routes = routes

    def routes(self):
        return list(self._routes)


def test_route_target_wraps_the_live_handler_and_restores_it():
    async def status(request):
        return {"ok": True, "request": request}

    route = FakeRoute("GET", "/api/status", status)
    registry = HookRegistry(router=lambda: FakeRouter([FakeRoute("POST", "/api/other", status), route]))
    registry.for_mod("m").after("route:GET /api/status", lambda result, request: {**result, "floofy": 1})
    assert route.handler is not status
    assert asyncio.run(route.handler("req")) == {"ok": True, "request": "req", "floofy": 1}
    with pytest.raises(HookTargetError, match="no route"):
        registry.for_mod("m").after("route:DELETE /api/status", lambda r, q: r)
    registry.deactivate("m")
    assert route.handler is status


# --- property: any interleaving, then unwinding, leaves every target identical to its original ------


@settings(max_examples=60, deadline=None)
@given(
    plan=st.lists(st.tuples(st.sampled_from(["a", "b", "c"]), st.sampled_from(["greet", "fetch_status", "Service.compute"]), st.sampled_from(["before", "after", "replace"])), min_size=0, max_size=12),
    order=st.permutations(["a", "b", "c"]),
    use_shutdown=st.booleans(),
)
def test_unwinding_restores_identity(plan, order, use_shutdown):
    module = types.ModuleType("floofy_prop_target")

    def greet(name):
        return name

    async def fetch_status(kind):
        return kind

    class Service:
        def compute(self, x):
            return x

    module.greet, module.fetch_status, module.Service = greet, fetch_status, Service
    originals = {"greet": greet, "fetch_status": fetch_status, "Service.compute": Service.__dict__["compute"]}
    sys.modules["floofy_prop_target"] = module
    try:
        registry = HookRegistry()
        for mod, attr, kind in plan:
            fn = {"before": lambda *a, **k: None, "after": lambda result, *a, **k: result, "replace": lambda call_next, *a, **k: call_next(*a, **k)}[kind]
            getattr(registry.for_mod(mod), kind)(f"floofy_prop_target:{attr}", fn)
        touched = {attr for _, attr, _ in plan}
        for attr in touched:
            owner, name = (module.Service, "compute") if attr == "Service.compute" else (module, attr)
            assert getattr(owner, name) is not originals[attr] or not touched
        # the wrapped targets still behave like the originals (pass-through hooks)
        assert module.greet("n") == "n" and module.Service().compute(3) == 3 and asyncio.run(module.fetch_status("k")) == "k"
        if use_shutdown:
            registry.shutdown()
        else:
            for mod in order:
                registry.deactivate(mod)
        assert module.greet is greet and module.fetch_status is fetch_status and module.Service.__dict__["compute"] is originals["Service.compute"]
        assert registry.targets() == [] and registry.registrations() == []
    finally:
        sys.modules.pop("floofy_prop_target", None)


# --- runtime wiring: a booted mod's hook fault disables the mod and unwinds --------------------------


HOOKING_MOD = '''
import floofy_test_target

def activate(ctx):
    def after(result, *args, **kwargs):
        if args and args[0] == "explode":
            raise RuntimeError("hook exploded")
        return result + " via " + ctx.mod_id
    ctx.hooks.after("floofy_test_target:greet", after)
    ctx.state["hooked"] = True

def deactivate(ctx):
    ctx.state["deactivated"] = True
'''


def test_runtime_wires_hooks_and_disables_a_faulting_mod(tmp_path: Path, target_module, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path / "home"))
    monkeypatch.setitem(sys.modules, "kiro_crew", None)
    paths = FloofyPaths(tmp_path / "home" / "floofy").ensure()
    write_consent(paths.consent, by="tests")
    make_mod(paths, "hooker", code=HOOKING_MOD, extra={"kirocrew": {"version": "*"}})
    make_mod(paths, "bystander")
    paths.enabled.write_text(json.dumps({"hooker": True, "bystander": True}), encoding="utf-8")
    runtime = LoaderRuntime()
    original = target_module.greet
    runtime.startup(types.SimpleNamespace(name="floofycrew", data_dir=tmp_path / "home" / "apps" / "floofycrew" / "data", logger=None))
    state = runtime.result.state
    assert state.active == ["bystander", "hooker"] and target_module.greet is not original
    assert target_module.greet("x") == "hello x! via hooker"
    assert runtime.state_dict()["hooks"][0]["mod"] == "hooker"
    # the hook faults on this input: fail-open, original behaviour, the mod is disabled with Error
    assert target_module.greet("explode") == "hello explode!"
    assert target_module.greet is original
    assert state.mods["hooker"].reason == Reason.Error.value and "hook exploded" in state.mods["hooker"].detail
    assert state.mods["bystander"].active
    assert runtime.result.activations.keys() == {"bystander"}
    written = json.loads(paths.loader_state.read_text(encoding="utf-8"))
    assert written["mods"]["hooker"]["reason"] == "Error" and written["faults"][0]["source"] == "hook"
    runtime.shutdown(None)
    assert target_module.greet is original and runtime.result.activations == {}
