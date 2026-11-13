"""The Loader event bus (Requirement 3.8) — consumable by mods (``ctx.events`` / ``floofy.events``) and the manager UI.

Names the Loader publishes (:data:`EVENTS`):

* ``mod.activated`` / ``mod.deactivated`` — ``{"mod", "version"}``;
* ``mod.quarantined`` — ``{"mod", "version", "hostVersion"}`` (a ``broken`` matrix cell);
* ``mod.faulted`` — ``{"mod", "source", "message"}`` (hook fault or SPA fault report);
* ``host.version_changed`` — ``{"previous", "current"}`` (the last recorded boot ran another host version);
* ``loader.state`` — ``{"loader", "active", "bootedAt"}`` after every boot and reload.

Delivery is synchronous and fail-open per subscriber: a raising subscriber is
logged with attribution and counted, the others still receive the event. Mods
may publish their own names (``mod.<id>.<topic>`` by convention); every
delivered event is kept in a short history the state API exposes.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from .state import utc_now

__all__ = ["EVENTS", "EventBus", "ModEvents", "Subscription"]

logger = logging.getLogger("floofy.loader.events")

EVENTS = ("mod.activated", "mod.deactivated", "mod.quarantined", "mod.faulted", "host.version_changed", "loader.state")
WILDCARD = "*"
HISTORY = 200

Handler = Callable[[str, dict[str, Any]], Any]


@dataclass(frozen=True)
class Subscription:
    name: str
    fn: Handler
    mod_id: str | None
    sequence: int

    def to_dict(self) -> dict[str, Any]:
        return {"event": self.name, "mod": self.mod_id, "fn": getattr(self.fn, "__qualname__", repr(self.fn))}


@dataclass
class EventBus:
    """Synchronous pub/sub with per-mod attribution."""

    subscriptions: list[Subscription] = field(default_factory=list)
    history: deque = field(default_factory=lambda: deque(maxlen=HISTORY))
    errors: list[dict[str, str]] = field(default_factory=list)
    _sequence: int = 0

    def subscribe(self, name: str, fn: Handler, mod_id: str | None = None) -> Subscription:
        if not callable(fn):
            raise TypeError("event handler must be callable")
        self._sequence += 1
        subscription = Subscription(name, fn, mod_id, self._sequence)
        self.subscriptions.append(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> bool:
        if subscription in self.subscriptions:
            self.subscriptions.remove(subscription)
            return True
        return False

    def unsubscribe_mod(self, mod_id: str) -> int:
        mine = [s for s in self.subscriptions if s.mod_id == mod_id]
        for subscription in mine:
            self.subscriptions.remove(subscription)
        return len(mine)

    def publish(self, name: str, payload: dict[str, Any] | None = None, *, source: str = "loader") -> int:
        """Deliver ``name`` to every matching subscriber, fail-open; returns the number delivered."""
        data = dict(payload or {})
        record = {"event": name, "payload": data, "source": source, "ts": utc_now()}
        self.history.append(record)
        delivered = 0
        for subscription in list(self.subscriptions):
            if subscription.name != name and subscription.name != WILDCARD:
                continue
            try:
                subscription.fn(name, data)
                delivered += 1
            except Exception as exc:  # noqa: BLE001 - one bad subscriber never blocks the others
                who = subscription.mod_id or "loader"
                logger.exception("event %s: subscriber of %s raised %s", name, who, exc)
                self.errors.append({"event": name, "mod": who, "error": f"{type(exc).__name__}: {exc}"})
        return delivered

    def to_dict(self) -> dict[str, Any]:
        return {
            "subscriptions": [s.to_dict() for s in self.subscriptions],
            "history": list(self.history)[-20:],
            "errors": list(self.errors)[-20:],
            "events": list(EVENTS),
        }


@dataclass
class ModEvents:
    """``ctx.events`` — the bus bound to one mod (subscriptions carry its id and are dropped with it)."""

    bus: EventBus
    mod_id: str

    def subscribe(self, name: str, fn: Handler) -> Subscription:
        return self.bus.subscribe(name, fn, self.mod_id)

    def unsubscribe(self, subscription: Subscription) -> bool:
        return self.bus.unsubscribe(subscription)

    def publish(self, name: str, payload: dict[str, Any] | None = None) -> int:
        return self.bus.publish(name, payload, source=f"mod:{self.mod_id}")

    @property
    def history(self) -> list[dict[str, Any]]:
        return list(self.bus.history)
