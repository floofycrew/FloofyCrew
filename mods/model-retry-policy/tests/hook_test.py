"""The gateway half against a fake host, through FloofyCrew's real hook registry.

The fake ``kiro_crew`` modules below carry the SAME seams the real 0.7.x host
has (module-level bindings of ``TRANSIENT_RETRIES``, ``transient_retry_delay``,
``configured_fallback_chain``, ``advance_fallback_candidate``, the
``FallbackState`` class, the dashboard's ``_fallback_swap_for_turn`` /
``fallback_rewound_transient_budget`` and a slotted ``_ChatSlot.append``), and
two ladder simulations replay the host's retry loops line for line
(``dashboard/chat_runner.py`` transient branches and
``llm_helpers.stream_and_collect`` Cases 2 / 2.75). Run: ``python -m pytest tests/``.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
REGISTRY_PY = REPO / "loader-app" / "floofy_loader" / "hooks_registry.py"

pytestmark = pytest.mark.skipif(not REGISTRY_PY.exists(), reason="needs the FloofyCrew checkout (loader-app/) for the real hook registry")

FAKE_MODULES = ("kiro_crew", "kiro_crew.llm_helpers", "kiro_crew.dashboard", "kiro_crew.dashboard.chat_runner", "kiro_crew.dashboard.chat_utils", "kiro_crew.dashboard.state", "kiro_crew.subagent", "kiro_crew.slack", "kiro_crew.slack.gateway")


# --- the fake host ---------------------------------------------------------------------------------------------


def _fake_host():
    """Modules shaped like the host's retry seams. Returns the namespace dict of them."""
    kc = types.ModuleType("kiro_crew")
    lh = types.ModuleType("kiro_crew.llm_helpers")
    # dataclasses resolve string annotations through sys.modules[cls.__module__]
    sys.modules["kiro_crew"] = kc
    sys.modules["kiro_crew.llm_helpers"] = lh

    lh._TRANSIENT_RETRIES = 3
    lh.TRANSIENT_RETRIES = 3
    lh.FALLBACK_CANDIDATE_ATTEMPTS = 2
    lh.HOST_CHAIN = ("auto",)

    def transient_retry_delay(attempt: int) -> float:
        return 2.0 * (2 ** (attempt - 1))

    def configured_fallback_chain() -> tuple:
        return lh.HOST_CHAIN

    def fallback_rewound_transient_budget() -> int:
        return max(0, lh.TRANSIENT_RETRIES - (lh.FALLBACK_CANDIDATE_ATTEMPTS - 1))

    class FallbackState:
        # a plain class (not a dataclass): pytest's importlib import mode keeps the test
        # module out of sys.modules, which breaks dataclass string-annotation resolution
        def __init__(self, chain, pos=0, active=None, attempts=0, primary="", walked=None):
            self.chain = tuple(chain)
            self.pos = pos
            self.active = active
            self.attempts = attempts
            self.primary = primary
            self.walked = list(walked or [])

        def next_candidate(self, active_model, advertised):
            remaining = self.chain[self.pos:]
            for cand in remaining:
                if cand.lower() != (active_model or "").lower():
                    self.pos += remaining.index(cand) + 1
                    return cand
            self.pos = len(self.chain)
            return None

        def should_retry_active(self) -> bool:
            if self.active is None or self.attempts >= lh.FALLBACK_CANDIDATE_ATTEMPTS:
                return False
            self.attempts += 1
            return True

        def exhaustion_story(self):
            return f"{self.primary} throttled; fallbacks {', '.join(self.walked)} also unavailable" if self.walked else None

    async def advance_fallback_candidate(provider, fb_state, *, surface, log_suffix=""):
        active = provider.model
        if not fb_state.primary:
            fb_state.primary = active
        while True:
            cand = fb_state.next_candidate(fb_state.primary, None)
            if cand is None:
                return None
            if cand.lower() == active.lower():
                continue
            provider.model = cand
            fb_state.active = cand
            fb_state.attempts = 1
            fb_state.walked.append(cand)
            return cand

    lh.transient_retry_delay = transient_retry_delay
    lh.configured_fallback_chain = configured_fallback_chain
    lh.fallback_rewound_transient_budget = fallback_rewound_transient_budget
    lh.FallbackState = FallbackState
    lh.advance_fallback_candidate = advance_fallback_candidate

    # dashboard/chat_runner: binds the names, owns the slot adapter over the shared walk
    cr = types.ModuleType("kiro_crew.dashboard.chat_runner")
    cr.TRANSIENT_RETRIES = lh.TRANSIENT_RETRIES
    cr.transient_retry_delay = lh.transient_retry_delay
    cr.configured_fallback_chain = lh.configured_fallback_chain
    cr.fallback_rewound_transient_budget = lh.fallback_rewound_transient_budget
    cr.FallbackState = FallbackState
    cr.advance_fallback_candidate = advance_fallback_candidate

    def _agent_fallback_chain():
        return cr.configured_fallback_chain()

    async def _fallback_swap_for_turn(slot, client):
        chain = cr._agent_fallback_chain()
        if not chain:
            return None
        fb = cr.FallbackState(chain, pos=max(0, int(slot._fallback_candidate_idx or 0)), primary=slot._fallback_primary_model or "")
        cand = await cr.advance_fallback_candidate(client, fb, surface="dashboard", log_suffix=f", slot={slot.key}")
        slot._fallback_candidate_idx = fb.pos
        if cand is None:
            return None
        if not slot._fallback_primary_model:
            slot._fallback_primary_model = fb.primary
        slot._active_fallback_model = cand
        slot._fallback_walked.append(cand)
        return cand

    cr._agent_fallback_chain = _agent_fallback_chain
    cr._fallback_swap_for_turn = _fallback_swap_for_turn

    st = types.ModuleType("kiro_crew.dashboard.state")

    class _ChatSlot:
        __slots__ = ("key", "messages", "queue", "_transient_5xx_retries", "_fallback_candidate_idx", "_fallback_walked", "_fallback_primary_model", "_active_fallback_model", "_posttoken_retry_used")

        def __init__(self, key="slot-1"):
            self.key = key
            self.messages = []
            self.queue = []
            self._transient_5xx_retries = 0
            self._fallback_candidate_idx = 0
            self._fallback_walked = []
            self._fallback_primary_model = ""
            self._active_fallback_model = ""
            self._posttoken_retry_used = False

        def append(self, role, content, cls="", ts="", *, broadcast=True, broadcast_user=False, meta=None, mint_mid=True):
            row = {"role": role, "content": content, "meta": meta}
            self.messages.append(row)
            return row

        def queue_insert(self, index, content, kind="", payload="", meta=None, on_consumed=None, on_irreversibly_consumed=None, directive_user_origin=False, directive_channel_origin=False):
            self.queue.insert(index, {"content": content, "kind": kind, "payload": payload})
            return f"q-{len(self.queue)}"

    st._ChatSlot = _ChatSlot

    # dashboard/chat_utils: the constants the mid-turn recovery rows carry
    cu = types.ModuleType("kiro_crew.dashboard.chat_utils")
    cu.TRANSIENT_RETRY_KIND = "transient_retry"
    cu.TRANSIENT_NOTICE_META_KEY = "notice"
    cu.TRANSIENT_NOTICE_RESUMING = "transient_resuming"
    cu.TRANSIENT_NOTICE_GIVE_UP = "transient_give_up"
    cu.TRANSIENT_RESUMING_TEXT = "⟳ Connection unstable — resuming…"
    cu.TRANSIENT_GIVE_UP_TEXT = "⚠ Connection unstable — press Continue to resume"
    cu.SYNTHETIC_RECOVERY_KIND = "synthetic_recovery"
    cu.POSTTOKEN_RECOVERY_PREFIX = "[Interrupted turn — automatic recovery]"
    cu._POSTTOKEN_RECOVER_MSG = f"{cu.POSTTOKEN_RECOVERY_PREFIX}\nThe previous response was interrupted partway through by a transient backend error. Continue from where it stopped."
    cu._SYNTHETIC_RECOVERY_MSGS = (cu._POSTTOKEN_RECOVER_MSG,)

    sa = types.ModuleType("kiro_crew.subagent")
    sa.TRANSIENT_RETRIES = lh.TRANSIENT_RETRIES
    sa.transient_retry_delay = lh.transient_retry_delay
    sa.configured_fallback_chain = lh.configured_fallback_chain
    sa.advance_fallback_candidate = advance_fallback_candidate
    sa.FallbackState = FallbackState

    sg = types.ModuleType("kiro_crew.slack.gateway")
    sg.transient_retry_delay = lh.transient_retry_delay
    sg.configured_fallback_chain = lh.configured_fallback_chain

    dash = types.ModuleType("kiro_crew.dashboard")
    slack = types.ModuleType("kiro_crew.slack")
    return {"kiro_crew": kc, "kiro_crew.llm_helpers": lh, "kiro_crew.dashboard": dash, "kiro_crew.dashboard.chat_runner": cr, "kiro_crew.dashboard.chat_utils": cu, "kiro_crew.dashboard.state": st,
            "kiro_crew.subagent": sa, "kiro_crew.slack": slack, "kiro_crew.slack.gateway": sg}


# --- the two ladders, as the host runs them ------------------------------------------------------------------------------


async def dashboard_ladder(cr, slot, client, *, max_attempts):
    """The interactive transient-5xx ladder: same-model retries, then the fallback branch, then terminal."""
    attempts, delays = [], []
    primary = client.model
    while len(attempts) < max_attempts:
        attempts.append(client.model)  # one prompt attempt that fails transiently, zero output
        if slot._transient_5xx_retries < cr.TRANSIENT_RETRIES:
            slot._transient_5xx_retries += 1
            delays.append(cr.transient_retry_delay(slot._transient_5xx_retries))
            continue
        cand = await cr._fallback_swap_for_turn(slot, client)
        if cand is not None:
            slot.append("notice", f"⚠️ {slot._fallback_primary_model or primary} is throttled — running on {cand} until {slot._fallback_primary_model or primary} recovers.", "msg msg-info")
            slot._transient_5xx_retries = cr.fallback_rewound_transient_budget()
            delays.append(cr.transient_retry_delay(1))
            continue
        slot._transient_5xx_retries = 0
        slot._fallback_candidate_idx = 0
        slot._fallback_walked = []
        return attempts, delays, "terminal"
    return attempts, delays, "cut"


async def midturn_ladder(cr, cu, slot, *, errors, stop_after=None):
    """The dashboard's post-emit branch: a transient error after the turn already streamed output.

    Replays ``chat_runner`` 0.7/0.8: a genuine user turn resets the one-shot flag; each
    mid-turn error either RECOVERS (resuming notice, ``transient_retry_delay(1)``, flag set,
    the "continue" instruction enqueued as a synthetic recovery — the recovery turn never
    refreshes the flag) or, with the flag already set, surfaces the error and ends the turn.
    *errors* is how many mid-turn errors the backend throws before the turn would land.
    """
    slot.append("user", "do the thing", "msg msg-u")
    slot._posttoken_retry_used = False  # start of a GENUINE turn (message not synthetic)
    recoveries, delays = 0, []
    for n in range(errors):
        if slot._posttoken_retry_used:
            slot.append("error", "❌ Bedrock is throttling requests.", "msg msg-err")
            return recoveries, delays, "terminal"
        if stop_after is not None and recoveries >= stop_after:
            slot.append("error", cu.TRANSIENT_GIVE_UP_TEXT, "msg msg-err", meta={cu.TRANSIENT_NOTICE_META_KEY: cu.TRANSIENT_NOTICE_GIVE_UP})
            return recoveries, delays, "stopped"
        slot.append("error", cu.TRANSIENT_RESUMING_TEXT, "msg msg-err", meta={"kind": cu.TRANSIENT_RETRY_KIND, cu.TRANSIENT_NOTICE_META_KEY: cu.TRANSIENT_NOTICE_RESUMING})
        delays.append(cr.transient_retry_delay(1))
        slot._posttoken_retry_used = True
        slot.queue_insert(0, cu._POSTTOKEN_RECOVER_MSG, kind=cu.SYNTHETIC_RECOVERY_KIND, payload="continuation")
        recoveries += 1
    return recoveries, delays, "landed"


async def unattended_ladder(lh, provider, *, max_attempts):
    """stream_and_collect Case 2 (same-model) + Case 2.75 (chain walk)."""
    attempts, delays = [], []
    chain = lh.configured_fallback_chain()
    fb_state = lh.FallbackState(tuple(chain)) if chain else None
    transient_attempts = 0
    while len(attempts) < max_attempts:
        attempts.append(provider.model)
        if transient_attempts < lh._TRANSIENT_RETRIES:
            transient_attempts += 1
            delays.append(lh.transient_retry_delay(transient_attempts))
            continue
        if fb_state is not None:
            if fb_state.should_retry_active():
                delays.append(lh.transient_retry_delay(1))
                continue
            cand = await lh.advance_fallback_candidate(provider, fb_state, surface="stream_and_collect")
            if cand is not None:
                delays.append(lh.transient_retry_delay(1))
                continue
        return attempts, delays, "terminal"
    return attempts, delays, "cut"


# --- the mod under the real registry ----------------------------------------------------------------------------------------


class FakeConfig:
    def __init__(self, doc):
        self.doc = dict(doc)

    def to_dict(self):
        return dict(self.doc)


class FakeRoutes:
    def __init__(self):
        self.handlers = {}

    def add(self, method, path, handler):
        self.handlers[(method, path)] = handler


class FakeRequest:
    def __init__(self, payload=None):
        self.body = json.dumps(payload).encode() if payload is not None else b""

    def json(self):
        return json.loads(self.body) if self.body else None


def _load_registry():
    name = "floofy_hooks_registry_under_test"
    spec = importlib.util.spec_from_file_location(name, REGISTRY_PY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses in the module resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def _load_hook():
    name = "mrp_hook_under_test"
    for key in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
        del sys.modules[key]
    spec = importlib.util.spec_from_file_location(name, ROOT / "hook" / "__init__.py", submodule_search_locations=[str(ROOT / "hook")])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def host(tmp_path):
    saved = {k: sys.modules.get(k) for k in FAKE_MODULES}
    modules = _fake_host()
    sys.modules.update(modules)
    registry_mod = _load_registry()
    faults = []
    registry = registry_mod.HookRegistry(on_fault=lambda mod, target, exc: faults.append((target, repr(exc))), router=lambda: None)
    hook = _load_hook()

    def activate(config):
        ctx = SimpleNamespace(
            mod_id="model-retry-policy", version="0.1.0", config=FakeConfig(config), log=logging.getLogger("test.mrp"), state={},
            routes=FakeRoutes(), data_dir=str(tmp_path), hooks=registry.for_mod("model-retry-policy"), host=SimpleNamespace(version="0.7.0.5"),
        )
        hook.activate(ctx)
        return ctx

    ns = SimpleNamespace(modules=modules, lh=modules["kiro_crew.llm_helpers"], cr=modules["kiro_crew.dashboard.chat_runner"], cu=modules["kiro_crew.dashboard.chat_utils"], st=modules["kiro_crew.dashboard.state"],
                         sa=modules["kiro_crew.subagent"], sg=modules["kiro_crew.slack.gateway"], registry=registry, faults=faults, hook=hook, activate=activate)
    yield ns
    if hook is not None:
        try:
            hook.deactivate(None)
        except Exception:
            pass
    registry.shutdown()
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


def profile(steps, primary=2, exhausted="stop"):
    return {"fallback": {"enabled": True, "profile": "p", "profiles": {"p": {"name": "p", "primary_retries": primary, "steps": steps, "exhausted": exhausted}}}}


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --- tests ------------------------------------------------------------------------------------------------------------------


def test_host_behaviour_calls_straight_through(host):
    ctx = host.activate({})
    lh, cr = host.lh, host.cr
    assert lh._TRANSIENT_RETRIES == 3 and cr.TRANSIENT_RETRIES == 3 and host.sa.TRANSIENT_RETRIES == 3
    assert cr.transient_retry_delay(2) == 4.0 and host.sg.transient_retry_delay(3) == 8.0
    assert cr.configured_fallback_chain() == ("auto",)
    assert ctx.state["host_behaviour"] is True and ctx.state["preset"] == "host"
    slot = host.st._ChatSlot()
    client = SimpleNamespace(model="primary")
    attempts, delays, end = run(dashboard_ladder(cr, slot, client, max_attempts=50))
    # KiroCrew's own ladder: 1 + 3 attempts on the primary, 2 on the auto candidate, then the error
    assert attempts == ["primary"] * 4 + ["auto"] * 2 and end == "terminal"
    assert delays == [2.0, 4.0, 8.0, 2.0, 8.0]
    attempts2, _, end2 = run(unattended_ladder(lh, SimpleNamespace(model="primary"), max_attempts=50))
    assert attempts2 == ["primary"] * 4 + ["auto"] * 2 and end2 == "terminal"
    assert not host.faults


def test_count_mode_overrides_every_budget_binding_and_restores(host):
    ctx = host.activate({"retries": {"mode": "count", "count": 5}})
    lh, cr, sa = host.lh, host.cr, host.sa
    assert (lh._TRANSIENT_RETRIES, lh.TRANSIENT_RETRIES, cr.TRANSIENT_RETRIES, sa.TRANSIENT_RETRIES) == (5, 5, 5, 5)
    assert ctx.state["retries"] == "5" and ctx.state["budget_overrides"] == 4
    slot = host.st._ChatSlot()
    attempts, delays, end = run(dashboard_ladder(cr, slot, SimpleNamespace(model="p"), max_attempts=50))
    assert attempts == ["p"] * 6 + ["auto"] * 2 and end == "terminal"  # host chain still applies after the custom budget
    assert delays[:5] == [2.0, 4.0, 8.0, 16.0, 32.0]  # host curve kept (delay mode host), bounded by max_s
    attempts2, _, _ = run(unattended_ladder(lh, SimpleNamespace(model="p"), max_attempts=50))
    assert attempts2 == ["p"] * 6 + ["auto"] * 2
    original_delay = host.registry.original("kiro_crew.dashboard.chat_runner:transient_retry_delay")
    host.hook.deactivate(ctx)
    host.registry.deactivate("model-retry-policy")
    assert (lh._TRANSIENT_RETRIES, lh.TRANSIENT_RETRIES, cr.TRANSIENT_RETRIES, sa.TRANSIENT_RETRIES) == (3, 3, 3, 3)
    assert cr.transient_retry_delay is original_delay
    assert not host.faults


def test_infinite_fixed_interval_never_gives_up(host):
    host.activate({"retries": {"mode": "infinite"}, "delay": {"mode": "fixed", "interval_s": 7, "jitter": 0}})
    attempts, delays, end = run(dashboard_ladder(host.cr, host.st._ChatSlot(), SimpleNamespace(model="p"), max_attempts=40))
    assert attempts == ["p"] * 40 and end == "cut" and set(delays) == {7.0}
    attempts2, delays2, end2 = run(unattended_ladder(host.lh, SimpleNamespace(model="p"), max_attempts=25))
    assert attempts2 == ["p"] * 25 and end2 == "cut" and set(delays2) == {7.0}
    assert not host.faults


def test_infinite_exponential_backs_off_to_the_cap(host):
    host.activate({"retries": {"mode": "infinite"}, "delay": {"mode": "exponential", "base_s": 1, "factor": 2, "max_s": 16, "jitter": 0}})
    _, delays, end = run(dashboard_ladder(host.cr, host.st._ChatSlot(), SimpleNamespace(model="p"), max_attempts=9))
    assert end == "cut" and delays == [1, 2, 4, 8, 16, 16, 16, 16, 16]
    assert not host.faults


def test_fallback_profile_dashboard_walks_the_steps_then_stops(host):
    ctx = host.activate(profile([{"model": "a", "retries": 3}, {"model": "b", "retries": 1}], primary=2, exhausted="stop"))
    assert host.cr.configured_fallback_chain() == ("a", "b") and host.cr.TRANSIENT_RETRIES == 2
    slot = host.st._ChatSlot()
    attempts, _, end = run(dashboard_ladder(host.cr, slot, SimpleNamespace(model="p"), max_attempts=50))
    assert attempts == ["p"] * 3 + ["a"] * 3 + ["b"] * 1 and end == "terminal"
    notices = [m["content"] for m in slot.messages if m["role"] == "notice"]
    assert len(notices) == 2 and all("is throttled" in n for n in notices)  # the host's own swap notices, untouched
    assert ctx.state["swaps_seen"] == 2 and ctx.state["exhausted_seen"] == 1
    assert not host.faults


def test_fallback_profile_unattended_matches_the_dashboard(host):
    host.activate(profile([{"model": "a", "retries": 3}, {"model": "b", "retries": 1}], primary=2, exhausted="stop"))
    attempts, _, end = run(unattended_ladder(host.lh, SimpleNamespace(model="p"), max_attempts=50))
    assert attempts == ["p"] * 3 + ["a"] * 3 + ["b"] * 1 and end == "terminal"
    assert not host.faults


def test_step_budget_larger_than_the_dashboard_block_is_honoured(host):
    # primary budget 2 means a dashboard block is at most 3 attempts; a 5-attempt step needs two blocks
    host.activate(profile([{"model": "a", "retries": 5}, {"model": "b", "retries": 2}], primary=2, exhausted="stop"))
    slot = host.st._ChatSlot()
    attempts, _, end = run(dashboard_ladder(host.cr, slot, SimpleNamespace(model="p"), max_attempts=50))
    assert attempts == ["p"] * 3 + ["a"] * 5 + ["b"] * 2 and end == "terminal"
    notices = [m["content"] for m in slot.messages if m["role"] == "notice"]
    assert len(notices) == 3
    assert "is throttled" in notices[0] and "Still throttled — retrying on a (3 attempts so far, 2 left)" in notices[1] and "is throttled" in notices[2]
    attempts2, _, _ = run(unattended_ladder(host.lh, SimpleNamespace(model="p"), max_attempts=50))
    assert attempts2 == attempts
    assert not host.faults


def test_retry_last_stays_on_the_last_candidate(host):
    host.activate(profile([{"model": "a", "retries": 2}, {"model": "b", "retries": 1}], primary=2, exhausted="retry-last"))
    slot = host.st._ChatSlot()
    attempts, _, end = run(dashboard_ladder(host.cr, slot, SimpleNamespace(model="p"), max_attempts=20))
    assert attempts[:5] == ["p"] * 3 + ["a"] * 2 and set(attempts[5:]) == {"b"} and end == "cut"
    notices = [m["content"] for m in slot.messages if m["role"] == "notice"]
    assert any("Still throttled — retrying on b" in n and "unlimited" in n for n in notices)
    attempts2, _, end2 = run(unattended_ladder(host.lh, SimpleNamespace(model="p"), max_attempts=20))
    assert attempts2[:5] == attempts[:5] and set(attempts2[5:]) == {"b"} and end2 == "cut"
    assert not host.faults


def test_candidate_retries_back_off_per_candidate_on_unattended_surfaces(host):
    host.activate({**profile([{"model": "a", "retries": 4}], primary=1, exhausted="stop"), "delay": {"mode": "exponential", "base_s": 1, "factor": 2, "max_s": 100, "jitter": 0}})
    attempts, delays, end = run(unattended_ladder(host.lh, SimpleNamespace(model="p"), max_attempts=50))
    assert attempts == ["p"] * 2 + ["a"] * 4 and end == "terminal"
    # primary retry 1 -> 1 s; swap -> attempt 1 -> 1 s; then the candidate's own attempts 2, 3, 4 -> 2, 4, 8 s
    assert delays == [1, 1, 2, 4, 8]


def test_retry_last_without_steps_is_infinite_same_model(host):
    host.activate(profile([], primary=3, exhausted="retry-last"))
    assert host.cr.TRANSIENT_RETRIES >= 10**9 and host.cr.configured_fallback_chain() == ()
    attempts, _, end = run(dashboard_ladder(host.cr, host.st._ChatSlot(), SimpleNamespace(model="p"), max_attempts=15))
    assert attempts == ["p"] * 15 and end == "cut"


def test_config_changes_are_picked_up_without_a_restart(host):
    ctx = host.activate({})
    assert host.cr.TRANSIENT_RETRIES == 3
    ctx.config.doc = {"retries": {"mode": "count", "count": 9}}
    host.cr.transient_retry_delay(1)  # any seam call re-syncs
    assert host.cr.TRANSIENT_RETRIES == 9 and host.lh._TRANSIENT_RETRIES == 9
    ctx.config.doc = {}
    host.cr.transient_retry_delay(1)
    assert host.cr.TRANSIENT_RETRIES == 3 and ctx.state["preset"] == "host"


def test_routes_status_preview_reset(host):
    ctx = host.activate({"retries": {"mode": "count", "count": 4}})
    run(dashboard_ladder(host.cr, host.st._ChatSlot(), SimpleNamespace(model="p"), max_attempts=50))
    status = ctx.routes.handlers[("GET", "status")](FakeRequest())
    assert status["effective"]["retries"] == "4" and status["counters"]["retries"] >= 4 and len(status["budget_overrides"]) == 4
    assert status["presets"] == ["host", "count", "infinite_fixed", "infinite_exponential", "fallback"]
    preview = ctx.routes.handlers[("POST", "preview")](FakeRequest({"config": {**profile([{"model": "a", "retries": 2}], primary=1), "delay": {"mode": "fixed", "interval_s": 5, "jitter": 0}}, "limit": 10}))
    rows = preview["timeline"]
    assert [r["model"] for r in rows] == ["primary", "primary", "a", "a", "—"]
    assert rows[1]["delay"] == 5.0 and rows[-1]["note"].startswith("chain exhausted")
    assert ctx.routes.handlers[("POST", "reset")](FakeRequest())["ok"] is True
    assert ctx.routes.handlers[("GET", "status")](FakeRequest())["counters"]["retries"] == 0
    assert ctx.routes.handlers[("POST", "apply")](FakeRequest())["effective"]["retries"] == "4"


def test_a_missing_seam_is_skipped_not_fatal(host, caplog):
    del host.cr._fallback_swap_for_turn
    with caplog.at_level(logging.WARNING, logger="test.mrp"):
        ctx = host.activate({"retries": {"mode": "count", "count": 2}})
    assert any("_fallback_swap_for_turn" in r.getMessage() for r in caplog.records)
    assert host.cr.TRANSIENT_RETRIES == 2 and ctx.state["preset"] == "count"


# --- mid-turn recoveries (a transient error after the turn already streamed output) ---------------------------------------------


def test_host_behaviour_keeps_the_one_shot_midturn_recovery(host):
    host.activate({})
    recoveries, delays, end = run(midturn_ladder(host.cr, host.cu, host.st._ChatSlot(), errors=5))
    # KiroCrew's own rule: exactly one "continue" recovery per user turn, then the error is shown
    assert (recoveries, end) == (1, "terminal") and delays == [2.0]
    assert not host.faults


def test_count_mode_grants_that_many_midturn_recoveries(host):
    ctx = host.activate({"retries": {"mode": "count", "count": 3}})
    slot = host.st._ChatSlot()
    recoveries, _, end = run(midturn_ladder(host.cr, host.cu, slot, errors=10))
    assert (recoveries, end) == (3, "terminal")
    assert len([q for q in slot.queue if q["kind"] == "synthetic_recovery"]) == 3
    assert ctx.state["resumes_seen"] == 3 and ctx.state["midturn"] == "3"
    # fewer errors than the budget: the turn lands and no error row is written
    recoveries2, _, end2 = run(midturn_ladder(host.cr, host.cu, host.st._ChatSlot("slot-2"), errors=2))
    assert (recoveries2, end2) == (2, "landed")
    assert not host.faults


def test_infinite_midturn_recoveries_follow_the_configured_curve(host):
    host.activate({"retries": {"mode": "infinite"}, "delay": {"mode": "exponential", "base_s": 1, "factor": 2, "max_s": 8, "jitter": 0}})
    slot = host.st._ChatSlot()
    recoveries, delays, end = run(midturn_ladder(host.cr, host.cu, slot, errors=7))
    assert (recoveries, end) == (7, "landed")
    # the host always asks for transient_retry_delay(1); the witness hands the real attempt number across
    assert delays == [1, 2, 4, 8, 8, 8, 8]
    assert slot._posttoken_retry_used is False  # re-armed after the last recovery too
    resumes = [m for m in slot.messages if m["role"] == "error" and (m["meta"] or {}).get("notice") == "transient_resuming"]
    assert len(resumes) == 7 and not any("Bedrock" in m["content"] for m in slot.messages)
    assert not host.faults


def test_stop_during_a_midturn_recovery_still_ends_the_turn(host):
    host.activate({"retries": {"mode": "infinite"}, "delay": {"mode": "fixed", "interval_s": 3, "jitter": 0}})
    slot = host.st._ChatSlot()
    recoveries, _, end = run(midturn_ladder(host.cr, host.cu, slot, errors=9, stop_after=2))
    # the host's own Stop guard withholds the enqueue; the mod never re-arms what was not enqueued
    assert (recoveries, end) == (2, "stopped")


def test_a_new_user_turn_starts_the_midturn_count_over(host):
    host.activate({"retries": {"mode": "count", "count": 2}})
    slot = host.st._ChatSlot()
    assert run(midturn_ladder(host.cr, host.cu, slot, errors=5))[::2] == (2, "terminal")
    assert run(midturn_ladder(host.cr, host.cu, slot, errors=5))[::2] == (2, "terminal")  # not 0: the "user" row reset the count
    assert run(midturn_ladder(host.cr, host.cu, slot, errors=1))[::2] == (1, "landed")


def test_fallback_profile_midturn_budget_is_the_profile_total_or_unlimited(host):
    host.activate(profile([{"model": "a", "retries": 3}, {"model": "b", "retries": 1}], primary=2, exhausted="stop"))
    assert run(midturn_ladder(host.cr, host.cu, host.st._ChatSlot(), errors=20))[::2] == (6, "terminal")  # 2 + 3 + 1, all on the current model
    host.hook.deactivate(None)
    host.registry.deactivate("model-retry-policy")
    host.activate(profile([{"model": "a", "retries": 1}], primary=1, exhausted="retry-last"))
    assert run(midturn_ladder(host.cr, host.cu, host.st._ChatSlot(), errors=30))[::2] == (30, "landed")


def test_deactivate_restores_the_host_one_shot(host):
    ctx = host.activate({"retries": {"mode": "infinite"}})
    original_qi = host.registry.original("kiro_crew.dashboard.state:_ChatSlot.queue_insert")
    assert run(midturn_ladder(host.cr, host.cu, host.st._ChatSlot(), errors=4))[::2] == (4, "landed")
    host.hook.deactivate(ctx)
    host.registry.deactivate("model-retry-policy")
    assert host.st._ChatSlot.queue_insert is original_qi
    assert run(midturn_ladder(host.cr, host.cu, host.st._ChatSlot(), errors=4))[::2] == (1, "terminal")
