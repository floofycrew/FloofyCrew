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

Every hook calls straight through when the user has not opted in
(``policy.is_host_behaviour``), so the default is KiroCrew's behaviour
byte-for-byte. Nothing here writes host config, touches credentials or opens
a socket; the mod's own state lives under ``ctx.data_dir``.

Coexistence with ``agent-backend-router``: that mod wraps
``AcpProvider.__init__`` / ``AcpProvider.stream`` (which backend a session
gets, and per-turn telemetry). No target overlaps. Each retry attempt is a
new ``stream`` call, so the router's telemetry sees one error sample per
failed attempt — its own fallback strategy trips on those counts.
"""
from __future__ import annotations

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
    })


def _record(kind: str, surface: str, **fields: Any) -> None:
    if _journal is None:
        return
    try:
        _journal.record(P.Event(ts=time.time(), surface=surface, kind=kind, **fields))
        _journal.flush()
        if _ctx is not None:
            c = _journal.counters
            _ctx.state.update({"retries_seen": c.retries, "resumes_seen": c.resumes, "swaps_seen": c.swaps, "exhausted_seen": c.exhausted})
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


async def _swap_for_turn(call_next, slot, client, *args, **kwargs):
    cfg = _sync()
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
        budget = notice["budget"]
        left = "unlimited" if budget >= P.INFINITE else f"{max(0, budget - notice['done'])} left"
        content = f"⟳ Still throttled — retrying on {notice['model']} ({notice['done']} attempts so far, {left}). Stop ends the retries."
    elif role == "user":
        # A genuine new turn: the host refreshes its one-shot allowance here, so the mod's count starts over too.
        _midturn.pop(_slot_key(self), None)
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
_RECOVERY_KIND = "synthetic_recovery"
_RECOVERY_PREFIX = "[Interrupted turn — automatic recovery]"


def _load_host_constants() -> None:
    global _RESUMING_NOTICE, _RESUMING_TEXT, _RECOVERY_KIND, _RECOVERY_PREFIX
    try:
        cu = importlib.import_module("kiro_crew.dashboard.chat_utils")
    except Exception:  # noqa: BLE001 - a build without the module keeps the literals
        return
    _RESUMING_NOTICE = str(getattr(cu, "TRANSIENT_NOTICE_RESUMING", _RESUMING_NOTICE))
    _RESUMING_TEXT = str(getattr(cu, "TRANSIENT_RESUMING_TEXT", _RESUMING_TEXT))
    _RECOVERY_KIND = str(getattr(cu, "SYNTHETIC_RECOVERY_KIND", _RECOVERY_KIND))
    _RECOVERY_PREFIX = str(getattr(cu, "POSTTOKEN_RECOVERY_PREFIX", _RECOVERY_PREFIX))


def _slot_key(slot) -> str:
    return str(getattr(slot, "key", "") or id(slot))


def _is_resuming_notice(content, meta) -> bool:
    if isinstance(meta, dict) and meta.get("notice") == _RESUMING_NOTICE:
        return True
    return isinstance(content, str) and content.strip() == _RESUMING_TEXT


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
        **snap,
    }


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
    _ctx.state.update({"retries_seen": 0, "resumes_seen": 0, "swaps_seen": 0, "exhausted_seen": 0})
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

    ctx.routes.add("GET", "status", _status)
    ctx.routes.add("POST", "preview", _preview)
    ctx.routes.add("POST", "reset", _reset)
    ctx.routes.add("POST", "apply", _apply)

    cfg = _sync()
    c = _journal.counters
    ctx.state.update({"retries_seen": c.retries, "resumes_seen": c.resumes, "swaps_seen": c.swaps, "exhausted_seen": c.exhausted})
    ctx.log.info("Model Retry Policy active: %s", P.describe(cfg))


def _hook_if_present(module_name: str, attr: str, kind: str, fn) -> None:
    """Register a hook, skipping quietly when this host build lacks the seam (logged, never a fault)."""
    try:
        module = importlib.import_module(module_name)
        owner = module
        parts = attr.split(".")
        for part in parts[:-1]:
            owner = getattr(owner, part)
        if not hasattr(owner, parts[-1]):
            _ctx.log.warning("seam %s:%s missing on this KiroCrew — that surface keeps the host's behaviour", module_name, attr)
            return
        getattr(_ctx.hooks, kind)(f"{module_name}:{attr}", fn)
    except Exception as exc:  # noqa: BLE001
        _ctx.log.warning("could not hook %s:%s (%s) — that surface keeps the host's behaviour", module_name, attr, exc)


def deactivate(ctx):
    global _ctx, _journal
    _restore_ints()
    _applied.update({"retries": None, "fingerprint": None})
    _tracks.clear()
    _midturn.clear()
    if _journal is not None:
        _journal.flush(force=True)
    _ctx = None
    _journal = None
