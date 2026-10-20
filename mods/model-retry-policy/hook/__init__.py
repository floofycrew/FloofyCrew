"""Model Retry Policy — the gateway half.

KiroCrew retries a transient model/API error (throttle, 5xx, stream reset) on
the same model ``llm_helpers._TRANSIENT_RETRIES`` (= 3) times on the
``transient_retry_delay`` curve, then walks the ``agent.fallback_model`` chain
(two attempts per candidate) and surfaces the error. Three surfaces run that
ladder with their own copy of the names: the dashboard
(``dashboard/chat_runner.py``), the unattended ``llm_helpers.stream_and_collect``
(cron, Slack, background one-liners) and the sub-agent runner (whose code is
rebound onto ``kiro_crew.subagent``'s globals). This hook sits on the seams all
three share and swaps in the user's policy (``hook/policy.py``):

* ``replace`` on ``transient_retry_delay`` at every module that calls it — the
  delay curve (fixed interval / exponential / the host's, always bounded).
* ``replace`` on ``configured_fallback_chain`` at every caller — the fallback
  profile's chain instead of ``agent.fallback_model``.
* ``replace`` on ``FallbackState.should_retry_active`` (a class attribute, so
  one registration reaches every surface) — per-candidate attempts, and
  "stay on the last candidate forever" for ``retry-last``.
* ``replace`` on the dashboard's ``_fallback_swap_for_turn`` and
  ``fallback_rewound_transient_budget`` — the interactive ladder encodes the
  per-candidate budget as a counter rewind, so the two are driven together.
* ``after`` on ``advance_fallback_candidate`` — swap / exhaustion events for
  the page.
* ``replace`` on the dashboard's ``_ChatSlot.queue_insert`` (with
  ``_ChatSlot.append`` as the witness) — the MID-TURN recovery. A transient
  error that lands after the turn has already streamed text or run a tool is
  not retried by the ladder above at all: the dashboard re-prompts the live
  session with a "continue" instruction instead, and does so exactly once per
  user turn (``slot._posttoken_retry_used``), whatever the retry budget says.
  In a long tool-using turn nearly every throttle lands there. The hook counts
  those recoveries per slot and, while the policy's mid-turn budget
  (``policy.midturn_budget``) is not spent, clears the one-shot flag again
  right after the host has enqueued the recovery, so the next mid-turn error
  recovers too; the configured delay curve applies between them.
* the retry budget itself is a plain ``int`` the host compares against
  (``TRANSIENT_RETRIES``), which ``ctx.hooks`` cannot wrap; it is overridden by
  attribute assignment on the modules that bind it and restored in
  ``deactivate`` (the Loader runs ``deactivate`` on a fault too).
* BACKEND FAILOVER (opt-in): inside the same ``_fallback_swap_for_turn`` seam,
  before any model walk, a throttled dashboard session is moved to another
  agent backend that serves the SAME model. A backend is a process, so the move
  is the host's own three steps for a provider change — reset, cold start,
  conversation replay from KiroCrew's log — arranged from two seams: the swap
  hook marks the session key pending and files a route request with the Agent
  Backend Router; a ``replace`` on ``SessionManager.get_or_create`` resets the
  key's live session when the ladder's re-queued message is dequeued, and the
  allocation that follows cold-starts on the requested backend. The router
  answers which backends list the model (its served-list identity rule, its
  pins, its pool); the Agent Backend Doctor answers whether the target is
  usable right now (installed, un-cached, Codex's credential chain intact).
  Without the router the mechanism stays off and the page says so.

Every hook calls straight through when the user has not opted in
(``policy.is_host_behaviour``), so the default is KiroCrew's behaviour
byte-for-byte. Nothing here writes host config, touches credentials or opens
a socket; the mod's own state lives under ``ctx.data_dir``.

Coexistence with ``agent-backend-router``: that mod wraps
``AcpProvider.__init__`` / ``AcpProvider.stream`` / ``AcpProvider.start``
(which backend a session gets, per-turn telemetry, the launch). No target
overlaps. Each retry attempt is a new ``stream`` call, so the router's
telemetry sees one error sample per failed attempt — its own fallback strategy
trips on those counts — and the failover above asks the router for its
candidates and route requests through its public functions
(``failover_candidates`` / ``request_route``, router ≥ 0.3.2).
"""
from __future__ import annotations

import asyncio
import contextvars
import importlib
import os
import time
from typing import Any

from . import policy as P

_ctx = None
_journal: P.Journal | None = None
_int_overrides: list[tuple[Any, str, Any]] = []   # (module, attr, original) — restored on deactivate
_applied: dict[str, Any] = {"retries": None, "fingerprint": None}

JOURNAL_FILE = "journal.json"
ROUTER_MODULE = "floofy_mods.agent-backend-router.hook"
DOCTOR_MODULE = "floofy_mods.agent-backend-doctor.hook"
MOD_ID = "model-retry-policy"

# The dashboard ladder calls ``_fallback_swap_for_turn`` and then, with no await in
# between, ``fallback_rewound_transient_budget()`` and ``slot.append("notice", …)``.
# These carry the swap decision across that gap within the same task.
_pending_rewind: contextvars.ContextVar[int | None] = contextvars.ContextVar("floofy_mrp_rewind", default=None)
_pending_notice: contextvars.ContextVar[dict | None] = contextvars.ContextVar("floofy_mrp_notice", default=None)
# The unattended ladders call ``should_retry_active()`` and then ``transient_retry_delay(1)``
# for every retry on a fallback candidate; this carries the candidate's real attempt
# number across so an exponential curve backs off per candidate instead of sitting at
# its first step.
_pending_attempt: contextvars.ContextVar[int | None] = contextvars.ContextVar("floofy_mrp_attempt", default=None)

# Where the host binds the names this mod overrides. ``kiro_crew.subagent`` is the
# sub-agent surface: ``subagent_manager/run.py`` is rebound onto its globals.
_SURFACES = {
    "kiro_crew.llm_helpers": "unattended",
    "kiro_crew.dashboard.chat_runner": "dashboard",
    "kiro_crew.subagent": "subagent",
    "kiro_crew.slack.gateway": "cron",
}
_INT_TARGETS = (
    ("kiro_crew.llm_helpers", "_TRANSIENT_RETRIES"),
    ("kiro_crew.llm_helpers", "TRANSIENT_RETRIES"),
    ("kiro_crew.dashboard.chat_runner", "TRANSIENT_RETRIES"),
    ("kiro_crew.subagent", "TRANSIENT_RETRIES"),
)


# --- config -----------------------------------------------------------------------------------------------


def _cfg() -> dict[str, Any]:
    stored = _ctx.config.to_dict() if _ctx is not None else {}
    return P.merged_config(stored)


def _fingerprint(cfg: dict[str, Any]) -> tuple:
    return (cfg["enabled"], P.effective_retries(cfg))


def _sync(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Bring the ``int`` overrides in line with the config (cheap when nothing changed)."""
    cfg = cfg or _cfg()
    fp = _fingerprint(cfg)
    if fp != _applied["fingerprint"]:
        retries = P.effective_retries(cfg)
        _install_ints(retries)
        _applied.update({"retries": retries, "fingerprint": fp})
        if _ctx is not None:
            _publish(cfg)
    return cfg


def _install_ints(value: int | None) -> None:
    """Set (or restore) every ``TRANSIENT_RETRIES`` binding the host reads."""
    _restore_ints()
    if value is None:
        return
    for module_name, attr in _INT_TARGETS:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - an edition without that surface
            _ctx.log.debug("no module %s for the retry budget: %s", module_name, exc)
            continue
        if not hasattr(module, attr):
            _ctx.log.debug("%s has no %s; skipped", module_name, attr)
            continue
        original = getattr(module, attr)
        if not isinstance(original, int):
            _ctx.log.warning("%s.%s is %r, not an int — not overriding", module_name, attr, type(original).__name__)
            continue
        setattr(module, attr, int(value))
        _int_overrides.append((module, attr, original))
    _ctx.log.info("retry budget installed: %s on %d binding(s)", "infinite" if value >= P.INFINITE else value, len(_int_overrides))


def _restore_ints() -> None:
    while _int_overrides:
        module, attr, original = _int_overrides.pop()
        try:
            setattr(module, attr, original)
        except Exception:  # noqa: BLE001
            pass


def _publish(cfg: dict[str, Any]) -> None:
    desc = P.describe(cfg)
    _ctx.state.update({
        "enabled": cfg["enabled"],
        "preset": desc["preset"],
        "retries": desc["retries"],
        "delay": desc["delay"],
        "midturn": desc["midturn"],
        "profile": desc["profile"],
        "chain": desc["chain"],
        "host_behaviour": desc["host_behaviour"],
        "budget_overrides": len(_int_overrides),
        "failover": desc["failover"],
    })


def _record(kind: str, surface: str, **fields: Any) -> None:
    if _journal is None:
        return
    try:
        _journal.record(P.Event(ts=time.time(), surface=surface, kind=kind, **fields))
        _journal.flush()
        if _ctx is not None:
            c = _journal.counters
            _ctx.state.update({"retries_seen": c.retries, "resumes_seen": c.resumes, "swaps_seen": c.swaps, "exhausted_seen": c.exhausted, "failovers_seen": c.failovers})
    except Exception as exc:  # noqa: BLE001 - bookkeeping never breaks a retry
        _ctx.log.debug("journal record failed: %s", exc)


# --- hook: the delay curve ------------------------------------------------------------------------------------


def _make_delay_hook(surface: str):
    def _delay(call_next, attempt, *args, **kwargs):
        cfg = _sync()
        if P.is_host_behaviour(cfg):
            return call_next(attempt, *args, **kwargs)
        override = _pending_attempt.get()
        if override is not None:
            _pending_attempt.set(None)
            if attempt == 1:
                attempt = override
        host = call_next(attempt, *args, **kwargs) if cfg["delay"]["mode"] == "host" else None
        value = P.effective_delay(cfg, attempt, host)
        if value is None:
            value = host if host is not None else call_next(attempt, *args, **kwargs)
        _record("retry", surface, attempt=int(attempt or 0), delay=round(float(value), 3))
        return value

    _delay.__name__ = f"_delay_{surface}"
    return _delay


# --- hook: the fallback chain ----------------------------------------------------------------------------------


def _chain(call_next, *args, **kwargs):
    cfg = _sync()
    chain = P.effective_chain(cfg)
    if chain is None:
        return call_next(*args, **kwargs)
    return tuple(chain)


# --- hook: per-candidate attempts (unattended surfaces) ----------------------------------------------------------


def _should_retry_active(call_next, self, *args, **kwargs):
    cfg = _sync()
    if P.active_profile(cfg) is None:
        return call_next(self, *args, **kwargs)
    if getattr(self, "active", None) is None:
        return False
    budget = P.step_attempts(cfg, int(getattr(self, "pos", 0) or 0) - 1)
    if budget is None:
        return call_next(self, *args, **kwargs)
    if self.attempts >= budget:
        return False
    self.attempts += 1
    _pending_attempt.set(int(self.attempts))
    return True


# --- hook: the dashboard's swap + counter rewind ---------------------------------------------------------------------


# Per-slot walk tracking, keyed by ``slot.key`` (``_ChatSlot`` is slotted — nothing can
# be stashed on it): how many attempts the current fallback candidate has been given.
# An entry is only trusted while the host's own walk position still matches it.
_tracks: dict[str, dict[str, int]] = {}
_TRACKS_MAX = 512


# --- backend failover (same model, another agent backend) --------------------------------------------------------------
#
# The dashboard's ladder calls ``_fallback_swap_for_turn`` when the same-model budget is
# spent and the error is still transient. The host's answer is a MODEL swap in place
# (``set_model`` on the live harness process). This mod's answer, when the user enabled it,
# comes first and is a BACKEND move: the same model on another harness. A live session
# cannot change its backend — the backend is the process — so the move is a reset and a
# cold start: this hook marks the slot's session key *pending* and returns the model as
# the "candidate"; the ladder then re-queues the same message (its own
# ``_queue_recovery``); when that message is dequeued, ``SessionManager.get_or_create``
# (replaced below) sees the pending key, resets the live session through the host's own
# ``reset`` (process gone, conversation kept) and lets the allocation cold-start; the
# Agent Backend Router honours the route request this hook filed for that key; the host's
# own provider-switch machinery then replays the conversation from KiroCrew's log onto the
# new backend (``build_session_replay``). Nothing here reaches into the host's session
# internals: a reset, a cold start and a replay are the three things the host does on its
# own model change too. The Agent Backend Doctor answers whether the target backend is
# usable right now; the router answers which backends serve the model.

_pending_failover: dict[str, dict[str, Any]] = {}     # session key → what the next allocation of that key should do
_failover_turns: dict[str, dict[str, Any]] = {}       # slot key → backends tried in the current user turn
_PENDING_TTL = 180.0
_TURN_TTL = 1800.0
_failover_last: dict[str, Any] = {}                    # the last failover decision, for the page


def _router():
    """The agent-backend-router's hook module when it is active and new enough, else ``None``."""
    import sys  # noqa: PLC0415

    mod = sys.modules.get(ROUTER_MODULE)
    return mod if mod is not None and hasattr(mod, "failover_candidates") and hasattr(mod, "request_route") else None


def _doctor():
    """The agent-backend-doctor's hook module when it is active and offers the readiness read, else ``None``."""
    import sys  # noqa: PLC0415

    mod = sys.modules.get(DOCTOR_MODULE)
    return mod if mod is not None and hasattr(mod, "backend_ready") else None


def _failover_support() -> dict[str, Any]:
    """What the failover can do on this gateway, for the page and the log."""
    import sys  # noqa: PLC0415

    router_any = sys.modules.get(ROUTER_MODULE) is not None
    doctor_any = sys.modules.get(DOCTOR_MODULE) is not None
    return {
        "router": "ok" if _router() is not None else ("too old (needs >= 0.3.2)" if router_any else "not active"),
        "doctor": "ok" if _doctor() is not None else ("too old (needs >= 0.2.1)" if doctor_any else "not active"),
        "allocation_hook": _alloc_hook_state,
        "available": _router() is not None and _doctor() is not None and _alloc_hook_state == "active",
    }


_alloc_hook_state = "none"  # "active" once SessionManager.get_or_create is wrapped; else why not


def _session_key_of(slot) -> str:
    """The session key the slot's turns run on — the key the dashboard hands ``get_or_create``."""
    try:
        from kiro_crew.dashboard import chat_utils as cu  # noqa: PLC0415

        fn = getattr(cu, "effective_session_key", None)
        if callable(fn):
            return str(fn(slot))
        fn = getattr(cu, "session_key_for", None)
        if callable(fn):
            return str(fn(slot.key, getattr(slot, "linked_session_key", "") or ""))
    except Exception:  # noqa: BLE001
        pass
    return str(getattr(slot, "linked_session_key", "") or f"dashboard:{getattr(slot, 'key', '')}")


def _live_backend(client) -> tuple[str, str]:
    """``(backend name, wire model)`` of the slot's live provider, from the router's stamp or the client itself."""
    abr = getattr(client, "_floofy_abr", None)
    if isinstance(abr, dict):
        backend = str(abr.get("backend", ""))
        model = str(abr.get("model", "") or "")
    else:
        backend = str(getattr(getattr(client, "client", None), "backend", "") or "")
        model = str(getattr(client, "model", "") or getattr(client, "served_model", "") or "")
    return ("kiro" if backend == "" else backend), model


def _turn_track(slot) -> dict[str, Any]:
    key = _slot_key(slot)
    now = time.time()
    track = _failover_turns.get(key)
    if track is None or now - track.get("ts", 0) > _TURN_TTL:
        if len(_failover_turns) >= _TRACKS_MAX:
            _failover_turns.clear()
        track = {"tried": [], "moves": 0, "ts": now}
        _failover_turns[key] = track
    track["ts"] = now
    return track


async def _pick_failover_target(slot, client, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Decide where a throttled session should move, or ``None`` (with the reason logged) to leave it to the model chain."""
    bf = P.backend_failover(cfg)
    router, doctor = _router(), _doctor()
    if bf is None or router is None or doctor is None or _alloc_hook_state != "active":
        return None
    if getattr(slot, "_active_fallback_model", ""):
        return None  # already on a fallback model: the model chain is in progress, backends were its turn first
    session_key = _session_key_of(slot)
    if not session_key or session_key in _pending_failover:
        return None
    track = _turn_track(slot)
    current, model = _live_backend(client)
    if track["moves"] >= bf["max_per_turn"]:
        _ctx.log.info("backend failover: slot %s already moved %d time(s) this turn (max %d) — leaving the rest to the model chain",
                      slot.key, track["moves"], bf["max_per_turn"])
        return None
    exclude = list(track["tried"]) + list(bf["exclude"])
    try:
        answer = router.failover_candidates(session_key, model=model, current=current, exclude=exclude)
    except Exception as exc:  # noqa: BLE001 - the router's fence failing is not a reason to break the retry
        _ctx.log.warning("backend failover: router query failed (%s) — model chain instead", exc)
        return None
    candidates = [c for c in answer.get("candidates", []) if c.get("eligible")]
    if not candidates:
        _ctx.log.info("backend failover: no other backend for %s on %s (%s)", model or "auto", current, answer.get("reason") or "none eligible")
        return None
    skipped: list[str] = []
    for cand in candidates:
        ago = cand.get("throttled_ago")
        if ago is not None and bf["cooldown_s"] > 0 and ago < bf["cooldown_s"]:
            skipped.append(f"{cand['backend']} (throttled {ago:.0f} s ago)")
            continue
        try:
            ready = await asyncio.to_thread(doctor.backend_ready, _backend_id(cand["backend"]))
        except Exception as exc:  # noqa: BLE001 - a readiness read failing is logged, then the backend is treated as present
            _ctx.log.warning("backend failover: readiness read for %s failed (%s: %s); treating it as present", cand["backend"], type(exc).__name__, exc)
            ready = {"ready": True, "detail": f"readiness read failed: {exc}", "needs_forget": False}
        if not ready.get("ready"):
            skipped.append(f"{cand['backend']} ({ready.get('detail') or 'not ready'})")
            continue
        if ready.get("needs_forget"):
            try:
                doctor.forget(_backend_id(cand["backend"]))  # on the loop, as the doctor asks
            except Exception as exc:  # noqa: BLE001
                _ctx.log.debug("forget(%s) failed: %s", cand["backend"], exc)
        return {"session_key": session_key, "from": current, "to": cand["backend"], "model": model, "wire_model": cand.get("wire_model") or model,
                "skipped": skipped, "ready": ready.get("detail", "")}
    _ctx.log.info("backend failover: every other backend is skipped for %s on %s: %s", model or "auto", current, "; ".join(skipped))
    return None


def _backend_id(name: str) -> str:
    return "" if name == "kiro" else name


def _arm_failover(slot, target: dict[str, Any], cfg: dict[str, Any]) -> None:
    """File the route request with the router, mark the session key pending, record the event, word the notice."""
    router = _router()
    reason = f"{target['model'] or 'auto'} throttled on {target['from']}"
    # The ladder re-queues after the configured backoff; the move must outlive that wait, and not much more.
    delay = P.effective_delay(cfg, 1, P.HOST_DELAY_BASE)
    ttl = _PENDING_TTL + float(delay if delay is not None else P.HOST_DELAY_BASE)
    router.request_route(target["session_key"], target["to"], model=target["wire_model"], reason=reason, by=MOD_ID, ttl=ttl)
    if len(_pending_failover) >= _TRACKS_MAX:
        _pending_failover.clear()
    _pending_failover[target["session_key"]] = {**target, "ts": time.time(), "ttl": ttl, "slot": _slot_key(slot)}
    track = _turn_track(slot)
    track["tried"].append(target["from"])
    track["moves"] += 1
    _failover_last.clear()
    _failover_last.update({**target, "ts": time.time(), "state": "pending"})
    _pending_rewind.set(0)  # the new backend gets the whole same-model budget before the next swap
    _pending_notice.set({"kind": "backend", "model": target["model"], "from": target["from"], "to": target["to"]})
    _record("failover", "dashboard", attempt=track["moves"], model=target["model"], previous=target["from"], backend=target["to"],
            detail=f"move {track['moves']}/{cfg['backend_failover']['max_per_turn']} this turn" + (f"; skipped {', '.join(target['skipped'])}" if target["skipped"] else ""))
    _ctx.log.info("backend failover: session %s (slot %s) %s -> %s for %s; the next allocation resets and cold-starts with a replay%s",
                  target["session_key"], slot.key, target["from"], target["to"], target["model"] or "auto",
                  f" (skipped {', '.join(target['skipped'])})" if target["skipped"] else "")


def _take_pending(key: str) -> dict[str, Any] | None:
    pending = _pending_failover.pop(key, None)
    if pending is None:
        return None
    if time.time() - pending.get("ts", 0) > float(pending.get("ttl", _PENDING_TTL)):
        _ctx.log.info("backend failover for %s expired unconsumed; dropped", key)
        router = _router()
        if router is not None:
            router.cancel_route_request(key)
        return None
    return pending


def _cancel_pending_for_slot(slot, why: str) -> None:
    key = _session_key_of(slot)
    if _pending_failover.pop(key, None) is not None:
        router = _router()
        if router is not None:
            router.cancel_route_request(key)
        _failover_last.update({"state": "cancelled", "why": why})
        _ctx.log.info("backend failover for %s cancelled: %s", key, why)


async def _get_or_create(call_next, self, key, *args, **kwargs):
    """``replace`` on ``SessionManager.get_or_create``: a pending failover resets the key's live session first.

    The reset is the host's own ``reset`` (the process is recycled, the
    conversation entry survives); the allocation that follows is a cold start,
    which the router routes per the request filed for this key and the host
    replays from its conversation log. A reset the host refuses (a turn still
    in flight on the key) cancels the move and the allocation proceeds as it
    would have. Every other key passes straight through.
    """
    pending = _take_pending(str(key)) if _pending_failover else None
    if pending is None:
        return await call_next(self, key, *args, **kwargs)
    router = _router()
    try:
        try:
            ok = await self.reset(key, skip_if_busy=True)
        except TypeError:  # an older reset() without the keyword: busy keys are rare at this point (the previous turn released)
            ok = await self.reset(key)
    except Exception as exc:  # noqa: BLE001 - the host refusing is not our fault; allocate as before
        ok = False
        _ctx.log.warning("backend failover: reset of %s failed (%s) — staying on %s", key, exc, pending["from"])
    if not ok:
        _ctx.log.info("backend failover: %s is busy or gone; move to %s dropped, allocating as before", key, pending["to"])
        if router is not None:
            router.cancel_route_request(str(key))
        _failover_last.update({"state": "dropped", "why": "reset refused"})
        return await call_next(self, key, *args, **kwargs)
    result = await call_next(self, key, *args, **kwargs)
    try:
        provider = result[0]
        landed, _m = _live_backend(provider)
    except Exception:  # noqa: BLE001
        landed = "?"
    if landed == pending["to"]:
        _failover_last.update({"state": "landed", "landed": landed})
        _ctx.log.info("backend failover: %s now runs on %s (%s)", key, landed, pending["model"] or "auto")
    else:
        if router is not None:
            router.cancel_route_request(str(key))
        _failover_last.update({"state": "landed_elsewhere", "landed": landed})
        _ctx.log.warning("backend failover: %s was meant for %s but the allocation landed on %s (a warm-pool claim or the router's fence); the turn runs there",
                         key, pending["to"], landed)
    return result


async def _swap_for_turn(call_next, slot, client, *args, **kwargs):
    cfg = _sync()
    target = await _pick_failover_target(slot, client, cfg)
    if target is not None:
        _arm_failover(slot, target, cfg)
        return target["model"] or str(getattr(slot, "model", "") or "auto")
    prof = P.active_profile(cfg)
    if prof is None or not prof["steps"]:
        return await call_next(slot, client, *args, **kwargs)

    key = str(getattr(slot, "key", "") or id(slot))
    idx = int(getattr(slot, "_fallback_candidate_idx", 0) or 0)
    active = str(getattr(slot, "_active_fallback_model", "") or "")
    walked = list(getattr(slot, "_fallback_walked", None) or [])
    track = _tracks.get(key)
    if track is None or track.get("idx") != idx or not walked:
        track = None   # the host advanced or reset the walk since we last saw it
        _tracks.pop(key, None)

    def _stay(done: int, budget: int) -> str:
        left = budget - done
        rewind = P.rewind_for(cfg, left)
        if len(_tracks) >= _TRACKS_MAX:
            _tracks.clear()
        _tracks[key] = {"idx": int(getattr(slot, "_fallback_candidate_idx", idx) or 0), "done": done + P.block_size(cfg, rewind)}
        _pending_rewind.set(rewind)
        _pending_notice.set({"model": active, "done": done, "budget": budget})
        _record("stay", "dashboard", attempt=done, model=active, detail=f"{done}/{'∞' if budget >= P.INFINITE else budget} attempts")
        _ctx.log.info("staying on %s for slot %s (%d of %s attempts done)", active, key, done, "∞" if budget >= P.INFINITE else budget)
        return active

    if track is not None and active:
        budget = P.step_attempts(cfg, idx - 1) or P.HOST_CANDIDATE_ATTEMPTS
        if track["done"] < budget:
            return _stay(track["done"], budget)

    candidate = await call_next(slot, client, *args, **kwargs)
    if candidate is None:
        if prof["exhausted"] == "retry-last" and active:
            return _stay(0, P.INFINITE)
        _tracks.pop(key, None)
        return None
    new_idx = int(getattr(slot, "_fallback_candidate_idx", 0) or 0)
    budget = P.step_attempts(cfg, new_idx - 1) or P.HOST_CANDIDATE_ATTEMPTS
    rewind = P.rewind_for(cfg, budget)
    if len(_tracks) >= _TRACKS_MAX:
        _tracks.clear()
    _tracks[key] = {"idx": new_idx, "done": P.block_size(cfg, rewind)}
    _pending_rewind.set(rewind)
    return candidate


def _rewound_budget(call_next, *args, **kwargs):
    pending = _pending_rewind.get()
    if pending is not None:
        _pending_rewind.set(None)
        return int(pending)
    cfg = _sync()
    if P.active_profile(cfg) is None:
        return call_next(*args, **kwargs)
    return P.rewind_for(cfg, P.HOST_CANDIDATE_ATTEMPTS)


def _slot_append(call_next, self, role, content, *args, **kwargs):
    """Reword the host's swap notice when the ladder is *staying* on a candidate rather than swapping,
    and witness the two transcript rows the mid-turn recovery budget keys on."""
    notice = _pending_notice.get()
    if notice is not None and role == "notice" and isinstance(content, str) and "is throttled" in content:
        _pending_notice.set(None)
        if notice.get("kind") == "backend":
            content = (f"⟳ {notice['model'] or 'The model'} is throttled on {notice['from']} — moving this chat to {notice['to']} with the same model. "
                       f"The conversation is replayed from KiroCrew's own log (tool results are not carried). Stop ends the retries.")
        else:
            budget = notice["budget"]
            left = "unlimited" if budget >= P.INFINITE else f"{max(0, budget - notice['done'])} left"
            content = f"⟳ Still throttled — retrying on {notice['model']} ({notice['done']} attempts so far, {left}). Stop ends the retries."
    elif role == "user":
        # A genuine new turn: the host refreshes its one-shot allowance here, so the mod's counts start over too.
        _midturn.pop(_slot_key(self), None)
        _failover_turns.pop(_slot_key(self), None)
    elif role == "error" and _is_give_up_notice(content, kwargs.get("meta")) and _pending_failover:
        # The ladder dropped its re-queue (Stop during the backoff, or a nested turn): nothing will consume the move.
        _cancel_pending_for_slot(self, "the turn ended without a re-queue")
    elif role == "error" and _is_resuming_notice(content, kwargs.get("meta")):
        # The dashboard is about to recover a turn that already streamed output: it sleeps
        # ``transient_retry_delay(1)`` next, in this same task. Hand the delay hook the real
        # attempt number so a fixed / exponential curve applies to mid-turn recoveries too.
        cfg = _sync()
        if P.midturn_budget(cfg) is not None:
            _pending_attempt.set(_midturn.get(_slot_key(self), 0) + 1)
    return call_next(self, role, content, *args, **kwargs)


# --- hook: the mid-turn recovery budget --------------------------------------------------------------------------------------


# Mid-turn recoveries already granted in the current user turn, per ``slot.key``
# (``_ChatSlot`` is slotted — nothing can be stashed on it). Reset when a ``user`` row
# is appended (the host refreshes its own one-shot at the same point).
_midturn: dict[str, int] = {}

# The host's constants, read from ``kiro_crew.dashboard.chat_utils`` when present; the
# literals are the 0.7 / 0.8 wording and only matter on a build without that module.
_RESUMING_NOTICE = "transient_resuming"
_RESUMING_TEXT = "⟳ Connection unstable — resuming…"
_GIVE_UP_NOTICE = "transient_give_up"
_GIVE_UP_TEXT = "⚠ Connection unstable — press Continue to resume"
_RECOVERY_KIND = "synthetic_recovery"
_RECOVERY_PREFIX = "[Interrupted turn — automatic recovery]"


def _load_host_constants() -> None:
    global _RESUMING_NOTICE, _RESUMING_TEXT, _RECOVERY_KIND, _RECOVERY_PREFIX, _GIVE_UP_NOTICE, _GIVE_UP_TEXT
    try:
        cu = importlib.import_module("kiro_crew.dashboard.chat_utils")
    except Exception:  # noqa: BLE001 - a build without the module keeps the literals
        return
    _RESUMING_NOTICE = str(getattr(cu, "TRANSIENT_NOTICE_RESUMING", _RESUMING_NOTICE))
    _RESUMING_TEXT = str(getattr(cu, "TRANSIENT_RESUMING_TEXT", _RESUMING_TEXT))
    _GIVE_UP_NOTICE = str(getattr(cu, "TRANSIENT_NOTICE_GIVE_UP", _GIVE_UP_NOTICE))
    _GIVE_UP_TEXT = str(getattr(cu, "TRANSIENT_GIVE_UP_TEXT", _GIVE_UP_TEXT))
    _RECOVERY_KIND = str(getattr(cu, "SYNTHETIC_RECOVERY_KIND", _RECOVERY_KIND))
    _RECOVERY_PREFIX = str(getattr(cu, "POSTTOKEN_RECOVERY_PREFIX", _RECOVERY_PREFIX))


def _slot_key(slot) -> str:
    return str(getattr(slot, "key", "") or id(slot))


def _is_resuming_notice(content, meta) -> bool:
    if isinstance(meta, dict) and meta.get("notice") == _RESUMING_NOTICE:
        return True
    return isinstance(content, str) and content.strip() == _RESUMING_TEXT


def _is_give_up_notice(content, meta) -> bool:
    if isinstance(meta, dict) and meta.get("notice") == _GIVE_UP_NOTICE:
        return True
    return isinstance(content, str) and content.strip() == _GIVE_UP_TEXT


def _slot_queue_insert(call_next, self, index, content, *args, **kwargs):
    """Re-arm the host's one-shot mid-turn recovery while the policy's budget allows another.

    The dashboard sets ``slot._posttoken_retry_used = True`` and then enqueues the
    "continue" recovery through this method. Clearing the flag *after* the enqueue
    means the recovery turn — which the host deliberately never refreshes the flag
    for — can recover once more if the backend fails again mid-way. With no opt-in
    (budget ``None``) the flag is left exactly as the host set it.
    """
    result = call_next(self, index, content, *args, **kwargs)
    kind = kwargs.get("kind", args[0] if args else "")
    if kind != _RECOVERY_KIND or not isinstance(content, str) or not content.startswith(_RECOVERY_PREFIX):
        return result
    cfg = _sync()
    budget = P.midturn_budget(cfg)
    if budget is None:
        return result
    key = _slot_key(self)
    if len(_midturn) >= _TRACKS_MAX:
        _midturn.clear()
    used = _midturn.get(key, 0) + 1
    _midturn[key] = used
    _record("resume", "dashboard", attempt=used, model=str(getattr(self, "_active_fallback_model", "") or getattr(self, "model", "") or ""),
            detail=f"{used}/{'∞' if budget >= P.INFINITE else budget} mid-turn recoveries")
    if used < budget:
        try:
            self._posttoken_retry_used = False
        except AttributeError:
            _ctx.log.warning("this KiroCrew has no _posttoken_retry_used on the chat slot — mid-turn recoveries keep the host's one-shot")
    else:
        _ctx.log.info("mid-turn recovery budget spent for slot %s (%d of %d) — the next mid-turn error is shown", key, used, budget)
    return result


# --- hook: swap / exhaustion events -------------------------------------------------------------------------------------


def _make_advance_after(surface: str):
    def _after(result, provider=None, fb_state=None, *args, **kwargs):
        try:
            walked = list(getattr(fb_state, "walked", None) or [])
            primary = str(getattr(fb_state, "primary", "") or "")
            if result is None:
                _record("exhausted", surface, model=walked[-1] if walked else primary, detail=getattr(fb_state, "exhaustion_story", lambda: "")() or "")
            else:
                previous = walked[-2] if len(walked) > 1 else primary
                _record("swap", surface, model=str(result), previous=previous)
        except Exception as exc:  # noqa: BLE001
            _ctx.log.debug("swap bookkeeping failed: %s", exc)
        return result

    _after.__name__ = f"_advance_after_{surface}"
    return _after


# --- routes for the page ---------------------------------------------------------------------------------------------------


def _status(_request):
    cfg = _sync()
    snap = _journal.snapshot() if _journal is not None else {"counters": {}, "recent": []}
    hooks = [r.to_dict() for r in _ctx.hooks.registrations()]
    return {
        "config": cfg,
        "effective": P.describe(cfg),
        "host": {"retries": P.HOST_RETRIES, "delay_base_s": P.HOST_DELAY_BASE, "candidate_attempts": P.HOST_CANDIDATE_ATTEMPTS, "version": str(getattr(_ctx.host, "version", ""))},
        "budget_overrides": [f"{m.__name__}.{a}" for m, a, _ in _int_overrides],
        "hooks": len(hooks),
        "presets": list(P.PRESETS),
        "infinite": P.INFINITE,
        "failover": _failover_facts(cfg),
        **snap,
    }


def _failover_facts(cfg: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    return {
        "enabled": P.backend_failover(cfg) is not None,
        "support": _failover_support(),
        "pending": [{"session": k, "from": v["from"], "to": v["to"], "model": v["model"], "age_s": round(now - v["ts"], 1)} for k, v in list(_pending_failover.items())],
        "last": dict(_failover_last),
        "turns_tracked": len(_failover_turns),
    }


def _failover_status(_request):
    """The failover picture plus what a move would look like for the host's default model (the page's preview)."""
    cfg = _sync()
    out = _failover_facts(cfg)
    router = _router()
    preview: dict[str, Any] = {"available": False}
    if router is not None:
        try:
            default_backend = str(getattr(router, "_read_default_backend")() or "")
            default_model = str(getattr(router, "_read_default_model")() or "")
            current = "kiro" if default_backend == "" else default_backend
            answer = router.failover_candidates("", model=default_model, current=current, exclude=list(cfg["backend_failover"]["exclude"]))
            preview = {"available": True, "current": current, "model": default_model or "auto", **answer}
        except Exception as exc:  # noqa: BLE001 - the preview is a courtesy
            preview = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    out["preview"] = preview
    return out


def _preview(request):
    """The first attempts a policy would make — for the page's timeline (pure computation)."""
    body = request.json() if request.body else {}
    cfg = P.merged_config((body or {}).get("config") or _ctx.config.to_dict())
    limit = max(1, min(int((body or {}).get("limit", 12) or 12), 60))
    return {"timeline": _timeline(cfg, limit), "effective": P.describe(cfg)}


def _timeline(cfg: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    import random  # noqa: PLC0415

    rng = random.Random(0)
    rows: list[dict[str, Any]] = []
    retries = P.effective_retries(cfg)
    retries = P.HOST_RETRIES if retries is None else retries
    chain = P.effective_chain(cfg)
    prof = P.active_profile(cfg)
    host_curve = lambda n: P.HOST_DELAY_BASE * (2 ** (n - 1))  # noqa: E731

    def delay(n: int) -> float:
        d = P.effective_delay(cfg, n, host_curve(n), rng)
        return host_curve(n) if d is None else d

    rows.append({"attempt": 1, "model": "primary", "delay": 0.0, "note": "the turn"})
    n = 1
    while n <= retries and len(rows) < limit:
        rows.append({"attempt": len(rows) + 1, "model": "primary", "delay": round(delay(n), 2), "note": f"same-model retry {n}" + ("" if retries < P.INFINITE else " (unbounded)")})
        n += 1
    if len(rows) >= limit:
        return rows
    if chain is not None and prof is not None:
        for i, model in enumerate(chain):
            budget = P.step_attempts(cfg, i) or P.HOST_CANDIDATE_ATTEMPTS
            k = 0
            while k < budget and len(rows) < limit:
                rows.append({"attempt": len(rows) + 1, "model": model, "delay": round(delay(k + 1), 2), "note": ("switch to " if k == 0 else "retry on ") + model + ("" if budget < P.INFINITE else " (unbounded)")})
                k += 1
            if len(rows) >= limit:
                break
        if len(rows) < limit:
            rows.append({"attempt": len(rows) + 1, "model": "—", "delay": None, "note": "chain exhausted: the error is shown" if prof["exhausted"] == "stop" else "keeps retrying the last model"})
    elif chain is None and retries < P.INFINITE and len(rows) < limit:
        rows.append({"attempt": len(rows) + 1, "model": "—", "delay": None, "note": "then KiroCrew's own agent.fallback_model chain, then the error"})
    return rows


def _reset(_request):
    if _journal is not None:
        _journal.clear()
        _journal.flush(force=True)
    _ctx.state.update({"retries_seen": 0, "resumes_seen": 0, "swaps_seen": 0, "exhausted_seen": 0, "failovers_seen": 0})
    return {"ok": True}


def _apply(_request):
    cfg = _sync()
    return {"ok": True, "effective": P.describe(cfg), "budget_overrides": [f"{m.__name__}.{a}" for m, a, _ in _int_overrides]}


# --- lifecycle -----------------------------------------------------------------------------------------------------------------


def activate(ctx):
    global _ctx, _journal
    _ctx = ctx
    _journal = P.Journal(os.path.join(str(ctx.data_dir), JOURNAL_FILE))

    for module_name, surface in _SURFACES.items():
        _hook_if_present(module_name, "transient_retry_delay", "replace", _make_delay_hook(surface))
        _hook_if_present(module_name, "configured_fallback_chain", "replace", _chain)
        if module_name != "kiro_crew.slack.gateway":
            _hook_if_present(module_name, "advance_fallback_candidate", "after", _make_advance_after(surface))
    ctx.hooks.replace("kiro_crew.llm_helpers:FallbackState.should_retry_active", _should_retry_active)
    _hook_if_present("kiro_crew.dashboard.chat_runner", "_fallback_swap_for_turn", "replace", _swap_for_turn)
    _hook_if_present("kiro_crew.dashboard.chat_runner", "fallback_rewound_transient_budget", "replace", _rewound_budget)
    _load_host_constants()
    _hook_if_present("kiro_crew.dashboard.state", "_ChatSlot.append", "replace", _slot_append)
    _hook_if_present("kiro_crew.dashboard.state", "_ChatSlot.queue_insert", "replace", _slot_queue_insert)
    global _alloc_hook_state
    _alloc_hook_state = "active" if _hook_if_present("kiro_crew.session", "SessionManager.get_or_create", "replace", _get_or_create) else "seam missing on this KiroCrew"

    ctx.routes.add("GET", "status", _status)
    ctx.routes.add("POST", "preview", _preview)
    ctx.routes.add("POST", "reset", _reset)
    ctx.routes.add("POST", "apply", _apply)
    ctx.routes.add("GET", "failover", _failover_status)

    cfg = _sync()
    c = _journal.counters
    ctx.state.update({"retries_seen": c.retries, "resumes_seen": c.resumes, "swaps_seen": c.swaps, "exhausted_seen": c.exhausted, "failovers_seen": c.failovers})
    support = _failover_support()
    ctx.state["backend_failover"] = {"enabled": P.backend_failover(cfg) is not None, **support}
    ctx.log.info("Model Retry Policy active: %s; backend failover %s (router %s, doctor %s, allocation seam %s)", P.describe(cfg),
                 "on" if P.backend_failover(cfg) is not None else "off", support["router"], support["doctor"], support["allocation_hook"])


def _hook_if_present(module_name: str, attr: str, kind: str, fn) -> bool:
    """Register a hook, skipping quietly when this host build lacks the seam (logged, never a fault). True when registered."""
    try:
        module = importlib.import_module(module_name)
        owner = module
        parts = attr.split(".")
        for part in parts[:-1]:
            owner = getattr(owner, part)
        if not hasattr(owner, parts[-1]):
            _ctx.log.warning("seam %s:%s missing on this KiroCrew — that surface keeps the host's behaviour", module_name, attr)
            return False
        getattr(_ctx.hooks, kind)(f"{module_name}:{attr}", fn)
        return True
    except Exception as exc:  # noqa: BLE001
        _ctx.log.warning("could not hook %s:%s (%s) — that surface keeps the host's behaviour", module_name, attr, exc)
        return False


def deactivate(ctx):
    global _ctx, _journal, _alloc_hook_state
    _restore_ints()
    _applied.update({"retries": None, "fingerprint": None})
    _tracks.clear()
    _midturn.clear()
    router = _router()
    for key in list(_pending_failover):
        if router is not None:
            router.cancel_route_request(key)
    _pending_failover.clear()
    _failover_turns.clear()
    _alloc_hook_state = "none"
    if _journal is not None:
        _journal.flush(force=True)
    _ctx = None
    _journal = None
