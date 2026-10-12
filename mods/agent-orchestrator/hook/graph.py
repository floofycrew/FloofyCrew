"""The graph reducer — turns the dashboard's own frames into per-node live state.

Pure Python, no host imports. Fed by two taps the hook installs on the host's
fan-out (``DashboardState._broadcast`` for the ``slots`` snapshot,
``DashboardState.broadcast_ws`` for slot-scoped frames) and by the bus store
(self-reported ``bus_status`` lines, ledger reports). It never writes anything
the host reads; it is the page's and the service's view of the world.

A *run* is any session that created at least one other session
(``slots[].created_by == "dashboard:<its slot>"``). That makes the graph work
for the host's own ``kirocrew-conductor`` fleets too — they just have no bus
messages. Nicknames: the hub is ``orchestrator``; spokes are ``worker-N`` in
first-seen order (a seed map from ``meta.json`` keeps them stable across
gateway restarts).

Summary line priority (design §3.6): ``bus_status`` fresher than 60 s →
``todo.current`` → last ``tool_call`` → ``chat_status`` → ``last_message``.
Progress: ``bus_status.progress`` → ``todo.completed/total`` → ledger ``done``
⇒ 100. Liveness: last activity < 30 s *live*, < 5 min *quiet*, else *stale*;
``done`` freezes the age like Claude Code's agent view.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

SESSION_PREFIX = "dashboard:"
STATUS_FRESH_SECS = 60.0
LIVE_SECS = 30.0
QUIET_SECS = 300.0
STALL_SECS_DEFAULT = 600.0


def session_key_of(slot_key: str) -> str:
    return slot_key if slot_key.startswith(SESSION_PREFIX) else SESSION_PREFIX + slot_key


def slot_key_of(session_key: str) -> str:
    return session_key[len(SESSION_PREFIX):] if session_key.startswith(SESSION_PREFIX) else session_key


def creator_key_of(created_by: Any) -> str:
    """``slots[].created_by`` as a session key.

    The host writes it in two spellings: ``dashboard:<slot>`` (0.7.x) and the bare ``<slot>`` (0.8.0.6, seen
    live: the run folded to ``chat-4-…`` and the orchestrator's sends found no run). A creator with another
    scheme (``cron:…``, ``channel:…``) is kept as-is — it is never a slot, so never a hub.
    """
    text = str(created_by or "").strip()
    if not text or ":" in text:
        return text
    return SESSION_PREFIX + text


def _trunc(text: Any, n: int = 140) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


@dataclass
class Node:
    slot: str
    title: str = ""
    agent: str = ""
    model: str = ""
    running: bool = False
    needs_input: bool = False
    created_by: str = ""
    last_message: str = ""
    todo: dict[str, Any] | None = None
    chat_status: str = ""
    last_tool: str = ""
    last_activity: float = 0.0
    first_seen: float = field(default_factory=time.time)
    gone: bool = False
    # from the bus / ledger
    status_text: str = ""
    status_progress: int | None = None
    status_state: str = ""
    status_ts: float = 0.0
    ledger_status: str = ""  # progress|done|blocked|question
    ledger_summary: str = ""
    ledger_ts: float = 0.0
    done_at: float = 0.0

    @property
    def session_key(self) -> str:
        return session_key_of(self.slot)


class Graph:
    def __init__(self, *, orchestrator_agent: str = "agent-orchestrator", stall_secs: float = STALL_SECS_DEFAULT,
                 clock: Callable[[], float] = time.time):
        self.orchestrator_agent = orchestrator_agent
        self.stall_secs = stall_secs
        self.clock = clock
        self.nodes: dict[str, Node] = {}
        self._nicks: dict[str, dict[str, str]] = {}  # run session key -> {session key -> nick}
        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []

    # -- events out ---------------------------------------------------------------------------------

    def subscribe(self, fn: Callable[[str, dict[str, Any]], None]) -> None:
        self._listeners.append(fn)

    def _emit(self, run: str, event: dict[str, Any]) -> None:
        for fn in list(self._listeners):
            try:
                fn(run, event)
            except Exception:  # noqa: BLE001 - observers never break the reducer
                pass

    # -- frames in ------------------------------------------------------------------------------------

    def on_slots(self, slots: list[dict[str, Any]]) -> None:
        seen: set[str] = set()
        now = self.clock()
        for s in slots:
            if not isinstance(s, dict):
                continue
            key = str(s.get("key") or "")
            if not key:
                continue
            seen.add(key)
            node = self.nodes.get(key)
            if node is None:
                node = Node(slot=key, first_seen=now, last_activity=now)
                self.nodes[key] = node
            was_running = node.running
            node.title = str(s.get("title") or node.title)
            node.agent = str(s.get("agent") or node.agent)
            node.model = str(s.get("model") or node.model)
            node.running = bool(s.get("running"))
            node.needs_input = bool(s.get("needs_input"))
            node.created_by = creator_key_of(s.get("created_by")) or node.created_by
            if s.get("last_message"):
                node.last_message = _trunc(s.get("last_message"))
            todo = s.get("todo")
            if isinstance(todo, dict):
                node.todo = {"completed": int(todo.get("completed") or 0), "total": int(todo.get("total") or 0),
                            "current": _trunc(todo.get("current") or "")}
            node.gone = False
            if node.running != was_running:
                node.last_activity = now
                run = self.run_of(key)
                if run:
                    self._emit(run, {"type": "idle" if not node.running else "running", "slot": key,
                                     "nick": self.nick_for(run, key)})
            if node.needs_input:
                run = self.run_of(key)
                if run:
                    self._emit(run, {"type": "needs_input", "slot": key, "nick": self.nick_for(run, key)})
        for key, node in self.nodes.items():
            if key not in seen and not node.gone:
                node.gone = True
                node.running = False
                run = self.run_of(key)
                if run:
                    self._emit(run, {"type": "gone", "slot": key, "nick": self.nick_for(run, key)})

    def on_frame(self, msg_type: str, data: Any) -> None:
        if not isinstance(data, dict):
            return
        slot = str(data.get("slot") or "")
        if not slot:
            return
        node = self.nodes.get(slot)
        if node is None:
            return  # frames for tabs we have not seen in a slots snapshot yet carry no roster info
        now = self.clock()
        if msg_type == "chat_status":
            node.chat_status = _trunc(data.get("status") or "")
            node.last_activity = now
        elif msg_type == "tool_call":
            node.last_tool = _trunc(data.get("tool") or data.get("kind") or "")
            node.last_activity = now
        elif msg_type == "todo_update":
            todo = data.get("todo")
            if isinstance(todo, dict):
                node.todo = {"completed": int(todo.get("completed") or 0), "total": int(todo.get("total") or 0),
                             "current": _trunc(todo.get("current") or "")}
            node.last_activity = now
        elif msg_type in ("chat_segment", "chat_chunk", "chat_message", "chat_append"):
            node.last_activity = now
        elif msg_type == "chat_done":
            node.running = False
            node.chat_status = ""
            node.last_activity = now
            run = self.run_of(slot)
            if run:
                self._emit(run, {"type": "idle", "slot": slot, "nick": self.nick_for(run, slot)})
        elif msg_type in ("question_card", "approval"):
            node.needs_input = True
            node.last_activity = now
            run = self.run_of(slot)
            if run:
                self._emit(run, {"type": "needs_input", "slot": slot, "nick": self.nick_for(run, slot)})
        elif msg_type in ("approval_resolved", "question_card_resolved"):
            node.needs_input = False

    # -- bus / ledger in ---------------------------------------------------------------------------

    def on_status(self, run: str, nick: str, row: dict[str, Any]) -> None:
        slot = self.slot_for_nick(run, nick)
        node = self.nodes.get(slot) if slot else None
        if node is None:
            return
        node.status_text = _trunc(row.get("text") or "")
        node.status_progress = row.get("progress")
        node.status_state = str(row.get("state") or "")
        node.status_ts = float(row.get("ts") or self.clock())
        node.last_activity = node.status_ts
        if node.status_state == "done" and not node.done_at:
            node.done_at = node.status_ts

    def on_report(self, run: str, nick: str, status: str, summary: str) -> None:
        slot = self.slot_for_nick(run, nick)
        node = self.nodes.get(slot) if slot else None
        if node is None:
            return
        node.ledger_status = status
        node.ledger_summary = _trunc(summary)
        node.ledger_ts = self.clock()
        node.last_activity = node.ledger_ts
        if status == "done" and not node.done_at:
            node.done_at = node.ledger_ts

    # -- roster -----------------------------------------------------------------------------------

    def seed_nicks(self, run: str, nicks: dict[str, str]) -> None:
        self._nicks.setdefault(run, {}).update(nicks)

    def children(self, run: str) -> list[Node]:
        kids = [n for n in self.nodes.values() if n.created_by == run and n.slot != slot_key_of(run)]
        kids.sort(key=lambda n: (n.first_seen, n.slot))
        return kids

    def is_live(self, run: str) -> bool:
        """The run still has an open orchestrator tab, or an open worker tab: its record must not be removed."""
        hub = self.nodes.get(slot_key_of(run))
        if hub is not None and not hub.gone:
            return True
        return any(not n.gone for n in self.children(run))

    def forget(self, run: str) -> int:
        """Drop the hub and its workers from the reducer (the run's record was removed); returns nodes dropped.
        A later slots snapshot that still carries the sessions re-creates them — this is for runs whose sessions
        are gone, so the picker stops listing a run the disk no longer has."""
        keys = [slot_key_of(run)] + [n.slot for n in self.children(run)]
        dropped = 0
        for key in keys:
            if self.nodes.pop(key, None) is not None:
                dropped += 1
        self._nicks.pop(run, None)
        return dropped

    def runs(self) -> list[str]:
        """Every session key that created at least one other session, hubs with live children first."""
        hubs: dict[str, int] = {}
        for n in self.nodes.values():
            if n.created_by and n.created_by != n.session_key:
                hubs[n.created_by] = hubs.get(n.created_by, 0) + (0 if n.gone else 1)
        # The hub must itself be a slot we know (a cron/channel creator is not drawn as a hub).
        out = [h for h in hubs if slot_key_of(h) in self.nodes]
        out.sort(key=lambda h: (-hubs[h], self.nodes[slot_key_of(h)].first_seen))
        return out

    def run_of(self, slot: str) -> str:
        """The run a slot belongs to: its own session key if it is a hub, else its creator's (depth 1)."""
        node = self.nodes.get(slot)
        if node is None:
            return ""
        own = node.session_key
        if any(n.created_by == own for n in self.nodes.values()):
            return own
        if node.created_by and slot_key_of(node.created_by) in self.nodes:
            return node.created_by
        if node.agent == self.orchestrator_agent:
            return own  # an orchestrator that has not dispatched yet is still a (lonely) run
        return ""

    def role_of(self, run: str, slot: str) -> str:
        return "orchestrator" if session_key_of(slot) == run else "worker"

    def nick_for(self, run: str, slot: str) -> str:
        key = session_key_of(slot)
        if key == run:
            return "orchestrator"
        table = self._nicks.setdefault(run, {})
        if key in table:
            return table[key]
        used = {v for v in table.values()}
        # Assign in creation order so a restart that re-seeds from meta.json stays consistent.
        for n in self.children(run):
            if n.session_key not in table:
                i = 1
                while f"worker-{i}" in used:
                    i += 1
                table[n.session_key] = f"worker-{i}"
                used.add(f"worker-{i}")
        return table.get(key, "")

    def slot_for_nick(self, run: str, nick: str) -> str:
        if nick == "orchestrator":
            return slot_key_of(run)
        for n in self.children(run):
            if self.nick_for(run, n.slot) == nick:
                return n.slot
        for key, v in self._nicks.get(run, {}).items():
            if v == nick:
                return slot_key_of(key)
        return ""

    def nick_map(self, run: str) -> dict[str, str]:
        for n in self.children(run):
            self.nick_for(run, n.slot)
        return dict(self._nicks.get(run, {}))

    def member_of(self, session_key: str) -> tuple[str, str, str]:
        """(run, nick, role) for a caller's session key, or ('', '', '') when it is in no run."""
        slot = slot_key_of(session_key)
        run = self.run_of(slot)
        if not run:
            return "", "", ""
        return run, self.nick_for(run, slot), self.role_of(run, slot)

    # -- projection ---------------------------------------------------------------------------------

    def node_view(self, run: str, node: Node) -> dict[str, Any]:
        now = self.clock()
        summary, source = self._summary(node, now)
        progress, psrc = self._progress(node)
        state = self._state(node, now)
        age_end = node.done_at or now
        return {
            "slot": node.slot, "key": node.session_key, "nick": self.nick_for(run, node.slot),
            "role": self.role_of(run, node.slot), "title": node.title, "agent": node.agent, "model": node.model,
            "running": node.running, "needs_input": node.needs_input, "gone": node.gone,
            "state": state, "summary": summary, "summary_source": source,
            "progress": progress, "progress_source": psrc,
            "liveness": self._liveness(node, now), "last_activity": node.last_activity,
            "age_secs": max(0.0, age_end - node.first_seen), "stalled": self._stalled(node, now),
            "todo": node.todo, "ledger": {"status": node.ledger_status, "summary": node.ledger_summary} if node.ledger_status else None,
            "href": f"/chat?slot={node.slot}",
        }

    def _summary(self, node: Node, now: float) -> tuple[str, str]:
        if node.status_text and now - node.status_ts < STATUS_FRESH_SECS:
            return node.status_text, "bus_status"
        if node.todo and node.todo.get("current") and node.running:
            return node.todo["current"], "todo"
        if node.last_tool and node.running:
            return f"running {node.last_tool}", "tool_call"
        if node.chat_status and node.running:
            return node.chat_status, "chat_status"
        if node.ledger_summary:
            return node.ledger_summary, "work_report"
        if node.status_text:
            return node.status_text, "bus_status"
        if node.last_message:
            return node.last_message, "last_message"
        return "", ""

    def _progress(self, node: Node) -> tuple[int | None, str]:
        if node.ledger_status == "done" or node.status_state == "done":
            return 100, "done"
        if node.status_progress is not None:
            return int(node.status_progress), "bus_status"
        if node.todo and node.todo.get("total"):
            return int(round(100 * node.todo["completed"] / node.todo["total"])), "todo"
        return None, ""

    def _state(self, node: Node, now: float) -> str:
        if node.gone:
            return "gone"
        if node.ledger_status == "done" or node.status_state == "done":
            return "done"
        if node.needs_input or node.ledger_status == "question":
            return "needs_input"
        if node.ledger_status == "blocked" or node.status_state == "blocked":
            return "blocked"
        if node.running and self._stalled(node, now):
            return "stalled"
        if node.running:
            return "waiting" if node.status_state == "waiting" else "working"
        return "idle"

    def _stalled(self, node: Node, now: float) -> bool:
        return bool(node.running and node.last_activity and now - node.last_activity > self.stall_secs)

    def _liveness(self, node: Node, now: float) -> str:
        if node.gone:
            return "gone"
        if node.done_at:
            return "done"
        idle = now - (node.last_activity or node.first_seen)
        return "live" if idle < LIVE_SECS else "quiet" if idle < QUIET_SECS else "stale"

    def snapshot(self, run: str) -> dict[str, Any] | None:
        hub = self.nodes.get(slot_key_of(run))
        if hub is None:
            return None
        workers = [self.node_view(run, n) for n in self.children(run)]
        return {
            "run": run,
            "orchestrator": self.node_view(run, hub),
            "workers": workers,
            "counters": {
                "workers": len(workers),
                "done": sum(1 for w in workers if w["state"] == "done"),
                "needs_input": sum(1 for w in workers if w["state"] == "needs_input"),
                "working": sum(1 for w in workers if w["state"] in ("working", "waiting")),
            },
        }
