"""Model Retry Policy — the policy brain (no host imports; unit-tested on its own).

KiroCrew retries a transient model/API error (throttle, 5xx, stream reset) on
the same model a fixed number of times (``llm_helpers._TRANSIENT_RETRIES`` = 3)
on a fixed curve (``transient_retry_delay``: 2 s doubling, +25 % jitter), then
walks the ``agent.fallback_model`` chain — ``""`` (none), ``"auto"`` or
``(<id>, "auto")`` — two attempts per candidate, and finally surfaces the error.

This module turns the user's configuration into the three numbers the host asks
for at those seams, and nothing else:

* :func:`effective_retries` — the same-model retry budget (``TRANSIENT_RETRIES``)
* :func:`effective_delay` — the seconds to sleep before the *attempt*-th retry
* :func:`effective_chain` — the ordered fallback chain the walk should use,
  plus :func:`step_attempts` for how many attempts each candidate gets
* :func:`midturn_budget` — how many times the dashboard may recover a turn
  that already streamed output (the host's hard-coded one-shot)

``None`` from any of them means "the host's own value" — the wiring in
``__init__`` then calls straight through, so a user who has not opted in keeps
KiroCrew's behaviour byte-for-byte.

Configuration (``ctx.config``; every key optional, see :data:`DEFAULTS`)::

    enabled: true
    retries: {mode: host | count | infinite, count: 3}
    delay:   {mode: host | fixed | exponential, interval_s: 30,
              base_s: 2, factor: 2, max_s: 300, jitter: 0.25}
    fallback: {enabled: false, profile: "<id>",
               profiles: {"<id>": {name, primary_retries,
                                   steps: [{model, retries}], exhausted: stop | retry-last}}}
    backend_failover: {enabled: false, max_per_turn: 2, cooldown_s: 120, exclude: []}

The four mechanisms the mod offers map onto it as presets (:data:`PRESETS`):
a retry count, infinite retries at a fixed interval, infinite retries with
exponential backoff, and a fallback profile that moves to another model after
N retries. A fallback profile owns the same-model budget (``primary_retries``)
and uses whatever delay curve is configured between attempts.

``backend_failover`` is independent of the four: when the same-model budget is
spent on a dashboard turn, the session is moved to ANOTHER AGENT BACKEND that
serves the same model (kiro-cli ↔ KAS ↔ Claude Code ↔ Codex, as the Agent
Backend Router's served lists allow) before any model chain is walked. The
model never changes; the conversation is replayed from KiroCrew's own log on
the new backend. :func:`backend_failover` returns the settings or ``None``.

"Infinite" is :data:`INFINITE` retries — the host compares its counter against
a plain ``int``, and a turn is still ended by Stop, a session reset or the
gateway restarting. A delay curve is always bounded by ``max_s``.
"""
from __future__ import annotations

import copy
import json
import os
import random
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

INFINITE = 10**9
"""The retry budget that stands for "never give up" (a real int: the host compares against one)."""

HOST_RETRIES = 3
"""KiroCrew's own same-model budget (``llm_helpers._TRANSIENT_RETRIES``), for display and the ``host`` mode."""

HOST_DELAY_BASE = 2.0
"""KiroCrew's own backoff base (``llm_helpers._TRANSIENT_DELAY``)."""

HOST_CANDIDATE_ATTEMPTS = 2
"""KiroCrew's own per-fallback-candidate budget (``llm_helpers.FALLBACK_CANDIDATE_ATTEMPTS``)."""

RETRY_MODES = ("host", "count", "infinite")
DELAY_MODES = ("host", "fixed", "exponential")
EXHAUSTED = ("stop", "retry-last")

MAX_COUNT = 10_000
MAX_DELAY_S = 24 * 3600.0
MAX_STEPS = 12
MAX_PROFILES = 32
MODEL_ID_MAX = 128

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "retries": {"mode": "host", "count": HOST_RETRIES},
    "delay": {"mode": "host", "interval_s": 30.0, "base_s": HOST_DELAY_BASE, "factor": 2.0, "max_s": 300.0, "jitter": 0.25},
    "fallback": {"enabled": False, "profile": "", "profiles": {}},
    "backend_failover": {"enabled": False, "max_per_turn": 2, "cooldown_s": 120.0, "exclude": []},
}

FAILOVER_MAX_PER_TURN = 8
"""The most backend moves one user turn may make (a pool is rarely larger)."""

PRESETS: dict[str, dict[str, Any]] = {
    # The four mechanisms, as the page offers them. Each is a config patch.
    "host": {"retries": {"mode": "host"}, "delay": {"mode": "host"}, "fallback": {"enabled": False}},
    "count": {"retries": {"mode": "count"}, "fallback": {"enabled": False}},
    "infinite_fixed": {"retries": {"mode": "infinite"}, "delay": {"mode": "fixed"}, "fallback": {"enabled": False}},
    "infinite_exponential": {"retries": {"mode": "infinite"}, "delay": {"mode": "exponential"}, "fallback": {"enabled": False}},
    "fallback": {"fallback": {"enabled": True}},
}


# --- config ----------------------------------------------------------------------------------------


def _num(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if f != f:  # NaN
        return default
    return min(max(f, lo), hi)


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        i = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(i, lo), hi)


def _model_id(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or len(text) > MODEL_ID_MAX or any(ch.isspace() for ch in text):
        return ""
    return text


def sanitize_profile(raw: Any, *, profile_id: str = "") -> dict[str, Any] | None:
    """One fallback profile, sanitised; ``None`` when *raw* is not a mapping."""
    if not isinstance(raw, dict):
        return None
    steps: list[dict[str, Any]] = []
    for item in raw.get("steps") or []:
        if len(steps) >= MAX_STEPS:
            break
        if isinstance(item, str):
            item = {"model": item}
        if not isinstance(item, dict):
            continue
        model = _model_id(item.get("model"))
        if not model:
            continue
        steps.append({"model": model, "retries": _int(item.get("retries", HOST_CANDIDATE_ATTEMPTS), HOST_CANDIDATE_ATTEMPTS, 1, MAX_COUNT)})
    exhausted = raw.get("exhausted", "stop")
    name = raw.get("name")
    return {
        "name": (name.strip()[:80] if isinstance(name, str) and name.strip() else (profile_id or "profile")),
        "primary_retries": _int(raw.get("primary_retries", HOST_RETRIES), HOST_RETRIES, 0, MAX_COUNT),
        "steps": steps,
        "exhausted": exhausted if exhausted in EXHAUSTED else "stop",
    }


def merged_config(stored: dict[str, Any] | None) -> dict[str, Any]:
    """Defaults overlaid with *stored*, every value clamped to something the host can live with."""
    cfg = copy.deepcopy(DEFAULTS)
    stored = stored if isinstance(stored, dict) else {}
    # Only an explicit boolean switches the policy off; anything else (missing, or a
    # malformed value another writer left behind) is the default, "on". ``bool({})``
    # would be False and silently pin every mechanism to the host's behaviour.
    enabled = stored.get("enabled", True)
    cfg["enabled"] = enabled if isinstance(enabled, bool) else True

    r = stored.get("retries") if isinstance(stored.get("retries"), dict) else {}
    cfg["retries"]["mode"] = r.get("mode") if r.get("mode") in RETRY_MODES else "host"
    cfg["retries"]["count"] = _int(r.get("count", HOST_RETRIES), HOST_RETRIES, 0, MAX_COUNT)

    d = stored.get("delay") if isinstance(stored.get("delay"), dict) else {}
    cfg["delay"]["mode"] = d.get("mode") if d.get("mode") in DELAY_MODES else "host"
    cfg["delay"]["interval_s"] = _num(d.get("interval_s", 30.0), 30.0, 0.0, MAX_DELAY_S)
    cfg["delay"]["base_s"] = _num(d.get("base_s", HOST_DELAY_BASE), HOST_DELAY_BASE, 0.0, MAX_DELAY_S)
    cfg["delay"]["factor"] = _num(d.get("factor", 2.0), 2.0, 1.0, 10.0)
    cfg["delay"]["max_s"] = _num(d.get("max_s", 300.0), 300.0, 0.0, MAX_DELAY_S)
    cfg["delay"]["jitter"] = _num(d.get("jitter", 0.25), 0.25, 0.0, 1.0)

    f = stored.get("fallback") if isinstance(stored.get("fallback"), dict) else {}
    profiles: dict[str, dict[str, Any]] = {}
    raw_profiles = f.get("profiles") if isinstance(f.get("profiles"), dict) else {}
    for pid, raw in raw_profiles.items():
        if len(profiles) >= MAX_PROFILES:
            break
        if not isinstance(pid, str) or not pid.strip():
            continue
        prof = sanitize_profile(raw, profile_id=pid)
        if prof is not None:
            profiles[pid.strip()[:64]] = prof
    profile = f.get("profile") if isinstance(f.get("profile"), str) else ""
    cfg["fallback"] = {
        "enabled": bool(f.get("enabled", False)) and profile in profiles,
        "profile": profile if profile in profiles else "",
        "profiles": profiles,
    }

    bf = stored.get("backend_failover") if isinstance(stored.get("backend_failover"), dict) else {}
    exclude = [_backend_name(b) for b in (bf.get("exclude") or []) if _backend_name(b)] if isinstance(bf.get("exclude"), list) else []
    cfg["backend_failover"] = {
        "enabled": bf.get("enabled", False) is True,
        "max_per_turn": _int(bf.get("max_per_turn", 2), 2, 1, FAILOVER_MAX_PER_TURN),
        "cooldown_s": _num(bf.get("cooldown_s", 120.0), 120.0, 0.0, MAX_DELAY_S),
        "exclude": exclude[:16],
    }
    return cfg


def _backend_name(value: Any) -> str:
    """A backend as the router names it (``kiro`` / ``kas`` / ``claude`` / ``codex`` …); ``""`` for junk."""
    if not isinstance(value, str):
        return ""
    text = value.strip().lower()
    if text == "":
        return "kiro"  # the host spells kiro-cli's v2 loop as the empty string
    return text if len(text) <= 32 and text.replace("-", "").replace("_", "").isalnum() else ""


def backend_failover(cfg: dict[str, Any]) -> dict[str, Any] | None:
    """The backend-failover settings when the mechanism is on; ``None`` when it is off (or the policy is)."""
    if not cfg["enabled"] or not cfg["backend_failover"]["enabled"]:
        return None
    return cfg["backend_failover"]


def active_profile(cfg: dict[str, Any]) -> dict[str, Any] | None:
    fb = cfg["fallback"]
    if not cfg["enabled"] or not fb["enabled"]:
        return None
    return fb["profiles"].get(fb["profile"])


def is_host_behaviour(cfg: dict[str, Any]) -> bool:
    """True when nothing is overridden: every hook should call straight through."""
    return (not cfg["enabled"]) or (
        cfg["retries"]["mode"] == "host" and cfg["delay"]["mode"] == "host" and active_profile(cfg) is None and backend_failover(cfg) is None
    )


# --- the three numbers -------------------------------------------------------------------------------


def effective_retries(cfg: dict[str, Any]) -> int | None:
    """The same-model retry budget to install as ``TRANSIENT_RETRIES``; ``None`` = leave the host's.

    A fallback profile owns the budget: ``primary_retries`` on the primary, then
    the chain. A profile with no steps and ``retry-last`` is the same thing as
    infinite same-model retries, so it is expressed that way (the unattended
    surfaces cannot "stay on the last candidate" when there is none).
    """
    if not cfg["enabled"]:
        return None
    prof = active_profile(cfg)
    if prof is not None:
        if not prof["steps"] and prof["exhausted"] == "retry-last":
            return INFINITE
        return prof["primary_retries"]
    mode = cfg["retries"]["mode"]
    if mode == "count":
        return cfg["retries"]["count"]
    if mode == "infinite":
        return INFINITE
    return None


def effective_delay(cfg: dict[str, Any], attempt: int, host_delay: float | None, rng: random.Random | None = None) -> float | None:
    """Seconds before the *attempt*-th (1-based) retry; ``None`` = the host's value untouched.

    ``host`` delay mode keeps the host's curve while the budget is the host's
    too; with a custom budget the host's unbounded doubling is capped at
    ``max_s`` (attempt 20 on the raw curve would be 12 days). Jitter is a
    fraction of the computed delay, added uniformly, like the host's 25 %.
    """
    if not cfg["enabled"]:
        return None
    d = cfg["delay"]
    rng = rng or random
    attempt = max(1, int(attempt or 1))
    if d["mode"] == "host":
        if host_delay is None:
            return None
        if effective_retries(cfg) is None:
            return None
        return min(float(host_delay), d["max_s"])
    if d["mode"] == "fixed":
        base = d["interval_s"]
    else:
        try:
            base = min(d["base_s"] * (d["factor"] ** (attempt - 1)), d["max_s"])
        except OverflowError:
            base = d["max_s"]
    return base + rng.random() * d["jitter"] * base


def effective_chain(cfg: dict[str, Any]) -> tuple[str, ...] | None:
    """The fallback chain (model ids, in order) or ``None`` for the host's ``agent.fallback_model`` chain."""
    prof = active_profile(cfg)
    if prof is None:
        return None
    return tuple(step["model"] for step in prof["steps"])


def step_attempts(cfg: dict[str, Any], chain_index: int) -> int | None:
    """How many attempts the candidate at *chain_index* gets; ``None`` for the host's budget.

    Past the last step, ``retry-last`` keeps the last candidate forever
    (:data:`INFINITE`); ``stop`` gives it its own count and then the chain is
    exhausted.
    """
    prof = active_profile(cfg)
    if prof is None:
        return None
    steps = prof["steps"]
    if not steps:
        return None
    if chain_index < 0:
        chain_index = 0
    if chain_index >= len(steps) - 1 and prof["exhausted"] == "retry-last":
        return INFINITE
    if chain_index >= len(steps):
        return steps[-1]["retries"]
    return steps[chain_index]["retries"]


def midturn_budget(cfg: dict[str, Any]) -> int | None:
    """How many mid-turn recoveries a turn gets; ``None`` = the host's own single one.

    The dashboard treats a transient error that lands AFTER the turn has already
    streamed text or run a tool differently from one that lands before any
    output: it never re-sends the prompt (that could re-run a tool) but
    re-prompts the live session with a "continue" instruction — and it does so
    EXACTLY ONCE per user turn (``_ChatSlot._posttoken_retry_used``), whatever
    ``TRANSIENT_RETRIES`` says. In a long tool-using turn nearly every throttle
    lands there, so a policy that only governed the pre-output ladder would
    never be felt. This is the budget for those recoveries:

    * retry count N → N recoveries per user turn;
    * infinite → unlimited (Stop still ends the turn);
    * a fallback profile → unlimited for ``retry-last``, otherwise every attempt
      the profile allows in total (primary + each step) — the host cannot
      switch models mid-turn, so they all run on the current model.
    """
    if not cfg["enabled"]:
        return None
    prof = active_profile(cfg)
    if prof is not None:
        if prof["exhausted"] == "retry-last":
            return INFINITE
        return prof["primary_retries"] + sum(step["retries"] for step in prof["steps"])
    mode = cfg["retries"]["mode"]
    if mode == "count":
        return cfg["retries"]["count"]
    if mode == "infinite":
        return INFINITE
    return None


def rewind_for(cfg: dict[str, Any], attempts_left: int) -> int:
    """The dashboard counter value that grants a candidate *attempts_left* more attempts.

    The interactive ladder re-queues the turn (one attempt) and then re-enters
    the same-model branch while ``counter < TRANSIENT_RETRIES``, so setting the
    counter to ``TRANSIENT_RETRIES - (attempts_left - 1)`` yields exactly
    *attempts_left* attempts before the next exhaustion (clamped at 0: a block
    can never exceed ``TRANSIENT_RETRIES + 1`` attempts — the caller loops
    blocks until the step's budget is spent).
    """
    budget = effective_retries(cfg)
    if budget is None:
        budget = HOST_RETRIES
    return max(0, budget - (max(1, attempts_left) - 1))


def block_size(cfg: dict[str, Any], rewind: int) -> int:
    """How many attempts a dashboard block started with counter=*rewind* contains."""
    budget = effective_retries(cfg)
    if budget is None:
        budget = HOST_RETRIES
    return max(1, budget - rewind + 1)


def describe(cfg: dict[str, Any]) -> dict[str, Any]:
    """A summary for the page and the exports: what is in force right now."""
    retries = effective_retries(cfg)
    chain = effective_chain(cfg)
    prof = active_profile(cfg)
    d = cfg["delay"]
    if d["mode"] == "fixed":
        delay = f"every {d['interval_s']:g} s"
    elif d["mode"] == "exponential":
        delay = f"{d['base_s']:g} s × {d['factor']:g}^n, max {d['max_s']:g} s"
    else:
        delay = "host curve (2 s doubling)" + (f", max {d['max_s']:g} s" if retries is not None else "")
    if d["mode"] != "host" or retries is not None:
        delay += f", jitter {round(d['jitter'] * 100)} %"
    midturn = midturn_budget(cfg)
    bf = backend_failover(cfg)
    return {
        "host_behaviour": is_host_behaviour(cfg),
        "retries": "host (3)" if retries is None else ("infinite" if retries >= INFINITE else str(retries)),
        "retries_value": HOST_RETRIES if retries is None else retries,
        "delay": delay,
        "midturn": "host (1)" if midturn is None else ("infinite" if midturn >= INFINITE else str(midturn)),
        "chain": list(chain) if chain is not None else None,
        "profile": prof["name"] if prof else None,
        "exhausted": prof["exhausted"] if prof else None,
        "preset": preset_of(cfg),
        "failover": None if bf is None else f"up to {bf['max_per_turn']} backend move(s) per turn, skip a backend throttled within {bf['cooldown_s']:g} s"
                    + (f", never {', '.join(bf['exclude'])}" if bf["exclude"] else ""),
        "failover_blocked_by_infinite_retries": bf is not None and retries is not None and retries >= INFINITE,
    }


def preset_of(cfg: dict[str, Any]) -> str:
    """Which of the four mechanisms (or ``host`` / ``custom``) the config amounts to."""
    if not cfg["enabled"]:
        return "host"
    if active_profile(cfg) is not None:
        return "fallback"
    r, d = cfg["retries"]["mode"], cfg["delay"]["mode"]
    if r == "host" and d == "host":
        return "host"
    if r == "count":
        return "count"
    if r == "infinite" and d == "fixed":
        return "infinite_fixed"
    if r == "infinite" and d == "exponential":
        return "infinite_exponential"
    return "custom"


# --- events (what happened) ----------------------------------------------------------------------------


@dataclass
class Event:
    ts: float
    surface: str          # dashboard | unattended | subagent | cron
    kind: str             # retry | resume | swap | exhausted | stay | failover
    attempt: int = 0
    delay: float | None = None
    model: str = ""       # the model retried / swapped to (for failover: the model kept)
    previous: str = ""    # for swap: the model swapped away from; for failover: the backend left
    detail: str = ""
    backend: str = ""     # for failover: the backend moved to


@dataclass
class Counters:
    retries: int = 0
    resumes: int = 0      # mid-turn recoveries (dashboard: the "continue" re-prompt after output had streamed)
    swaps: int = 0
    exhausted: int = 0
    stays: int = 0
    failovers: int = 0    # backend moves (same model, another backend, conversation replayed)
    by_surface: dict[str, int] = field(default_factory=dict)
    since: float = field(default_factory=time.time)
    last_ts: float | None = None


class Journal:
    """A bounded ring of :class:`Event` plus counters, persisted to one JSON file (best effort)."""

    def __init__(self, path: str, *, keep: int = 200, flush_every: float = 5.0):
        self.path = path
        self.keep = keep
        self._events: list[Event] = []
        self.counters = Counters()
        self._lock = threading.Lock()
        self._dirty = False
        self._last_flush = 0.0
        self._flush_every = flush_every
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            return
        if not isinstance(doc, dict):
            return
        for raw in doc.get("events") or []:
            try:
                self._events.append(Event(**{k: raw[k] for k in Event.__dataclass_fields__ if k in raw}))
            except (TypeError, KeyError):
                continue
        c = doc.get("counters") or {}
        if isinstance(c, dict):
            for k in Counters.__dataclass_fields__:
                if k in c:
                    setattr(self.counters, k, c[k])
        self._events = self._events[-self.keep:]

    def record(self, event: Event) -> None:
        with self._lock:
            self._events.append(event)
            if len(self._events) > self.keep:
                del self._events[: len(self._events) - self.keep]
            c = self.counters
            if event.kind == "retry":
                c.retries += 1
            elif event.kind == "resume":
                c.resumes += 1
            elif event.kind == "swap":
                c.swaps += 1
            elif event.kind == "exhausted":
                c.exhausted += 1
            elif event.kind == "stay":
                c.stays += 1
            elif event.kind == "failover":
                c.failovers += 1
            c.by_surface[event.surface] = c.by_surface.get(event.surface, 0) + 1
            c.last_ts = event.ts
            self._dirty = True

    def recent(self, n: int = 50) -> list[Event]:
        with self._lock:
            return list(self._events[-n:])

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self.counters = Counters()
            self._dirty = True

    def flush(self, force: bool = False) -> None:
        with self._lock:
            now = time.time()
            if not self._dirty or (not force and now - self._last_flush < self._flush_every):
                return
            doc = {"events": [asdict(e) for e in self._events], "counters": asdict(self.counters)}
            self._dirty = False
            self._last_flush = now
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"counters": asdict(self.counters), "recent": [asdict(e) for e in self._events[-80:]]}
