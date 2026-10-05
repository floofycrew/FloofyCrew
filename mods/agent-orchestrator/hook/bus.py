"""The message bus store — one append-only log per orchestration run.

Pure Python, no host imports: this is the part the tests drive directly and
the part that must stay honest about its caps. A *run* is keyed by the
orchestrator's session key (``dashboard:<slot>``); its directory holds

* ``bus.jsonl``   — every message, one JSON object per line, append-only;
* ``status.json`` — the latest self-reported status line per member nick;
* ``meta.json``   — goal, nick assignments, open/closed, timestamps.

Caps REFUSE rather than truncate (the host's work ledger makes the same
choice): a message over the size cap, a run over the message cap, or a sender
over its burst allowance gets a ``BusError`` whose text tells the model not to
retry. Nothing here authorizes — the service layer (``rpc.py``) decides who may
talk to whom; this module only knows how to store and read.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

try:  # POSIX advisory locks; on Windows the lock degrades to a no-op (single-writer gateway anyway)
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

KINDS = frozenset({"say", "ask", "answer", "status", "report", "send", "done", "blocked"})
AGENT_KINDS = frozenset({"say", "ask", "answer"})  # what bus_post may carry; the rest are synthesized by the hook
STATES = frozenset({"working", "blocked", "waiting", "done"})

DEFAULT_CAPS: dict[str, int] = {
    "message_max_chars": 2000,
    "messages_per_run": 500,
    "burst_per_minute": 20,
    "status_max_chars": 140,
    "goal_max_chars": 300,
}

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class BusError(Exception):
    """A refusal the tool layer renders verbatim; ``code`` is what a caller matches on."""

    def __init__(self, message: str, *, code: str, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.message, "code": self.code, "retryable": self.retryable}


def fold(key: str) -> str:
    """A run key folded to one safe directory name (``dashboard:orch-1`` → ``dashboard_orch-1``)."""
    return _SAFE.sub("_", key).strip("_") or "run"


def new_id(prefix: str = "m") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@dataclass
class Message:
    id: str
    ts: float
    run: str
    frm: str
    to: str
    kind: str
    text: str
    reply_to: str | None = None
    from_key: str = ""
    to_key: str = ""
    delivered: bool = False
    nudged: bool = False
    delivered_to: list[str] = field(default_factory=list)

    def delivered_for(self, nick: str) -> bool:
        """Delivered to *nick*: a direct message once read, a broadcast once THIS recipient read it."""
        if nick in self.delivered_to:
            return True
        return self.delivered and self.to != "*"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "ts": self.ts, "run": self.run, "from": self.frm, "to": self.to,
            "kind": self.kind, "text": self.text, "reply_to": self.reply_to,
            "from_key": self.from_key, "to_key": self.to_key,
            "delivered": self.delivered, "nudged": self.nudged, "delivered_to": list(self.delivered_to),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Message":
        return cls(
            id=str(d.get("id", "")), ts=float(d.get("ts", 0.0)), run=str(d.get("run", "")),
            frm=str(d.get("from", "")), to=str(d.get("to", "")), kind=str(d.get("kind", "say")),
            text=str(d.get("text", "")), reply_to=d.get("reply_to"),
            from_key=str(d.get("from_key", "")), to_key=str(d.get("to_key", "")),
            delivered=bool(d.get("delivered", False)), nudged=bool(d.get("nudged", False)),
            delivered_to=[str(x) for x in (d.get("delivered_to") or [])],
        )


@dataclass
class RunMeta:
    run: str
    created_at: float
    goal: str = ""
    closed_at: float | None = None
    nicks: dict[str, str] = field(default_factory=dict)  # session key -> nick
    counters: dict[str, int] = field(default_factory=lambda: {"messages": 0})

    def to_dict(self) -> dict[str, Any]:
        return {"run": self.run, "created_at": self.created_at, "goal": self.goal, "closed_at": self.closed_at,
                "nicks": dict(self.nicks), "counters": dict(self.counters)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RunMeta":
        return cls(run=str(d.get("run", "")), created_at=float(d.get("created_at", 0.0)), goal=str(d.get("goal", "")),
                   closed_at=d.get("closed_at"), nicks=dict(d.get("nicks") or {}),
                   counters=dict(d.get("counters") or {"messages": 0}))


class _Lock:
    def __init__(self, path: Path):
        self._path = path
        self._fh = None

    def __enter__(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._path, "a+", encoding="utf-8")
        if fcntl is not None:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            if fcntl is not None and self._fh is not None:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            if self._fh is not None:
                self._fh.close()
                self._fh = None


class BusStore:
    """File-backed, lock-serialised message store for every run under ``root``."""

    def __init__(self, root: Path, caps: dict[str, int] | None = None):
        self.root = Path(root)
        self.caps = {**DEFAULT_CAPS, **(caps or {})}
        self.root.mkdir(parents=True, exist_ok=True)
        self._burst: dict[tuple[str, str], list[float]] = {}

    # -- paths ----------------------------------------------------------------------------------

    def run_dir(self, run: str) -> Path:
        return self.root / fold(run)

    def _lock(self, run: str) -> _Lock:
        return _Lock(self.run_dir(run) / ".lock")

    # -- meta -------------------------------------------------------------------------------------

    def meta(self, run: str) -> RunMeta | None:
        p = self.run_dir(run) / "meta.json"
        if not p.is_file():
            return None
        try:
            return RunMeta.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None

    def ensure_run(self, run: str) -> RunMeta:
        existing = self.meta(run)
        if existing is not None:
            return existing
        with self._lock(run):
            existing = self.meta(run)
            if existing is not None:
                return existing
            meta = RunMeta(run=run, created_at=time.time())
            self._write_meta(meta)
            return meta

    def _write_meta(self, meta: RunMeta) -> None:
        d = self.run_dir(meta.run)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "meta.json.tmp"
        tmp.write_text(json.dumps(meta.to_dict(), indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, d / "meta.json")

    def update_meta(self, run: str, **changes: Any) -> RunMeta:
        with self._lock(run):
            meta = self.meta(run) or RunMeta(run=run, created_at=time.time())
            for k, v in changes.items():
                if k == "goal":
                    v = str(v or "")[: self.caps["goal_max_chars"]]
                setattr(meta, k, v)
            self._write_meta(meta)
            return meta

    def assign_nick(self, run: str, session_key: str, nick: str) -> None:
        with self._lock(run):
            meta = self.meta(run) or RunMeta(run=run, created_at=time.time())
            if meta.nicks.get(session_key) != nick:
                meta.nicks[session_key] = nick
                self._write_meta(meta)

    def close_run(self, run: str) -> RunMeta:
        return self.update_meta(run, closed_at=time.time())

    def is_closed(self, run: str) -> bool:
        meta = self.meta(run)
        return bool(meta and meta.closed_at)

    def list_runs(self) -> list[RunMeta]:
        out: list[RunMeta] = []
        for d in sorted(self.root.iterdir()) if self.root.is_dir() else []:
            if (d / "meta.json").is_file():
                m = self.meta_from_dir(d)
                if m is not None:
                    out.append(m)
        out.sort(key=lambda m: m.created_at, reverse=True)
        return out

    def meta_from_dir(self, d: Path) -> RunMeta | None:
        try:
            return RunMeta.from_dict(json.loads((d / "meta.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None

    # -- messages ---------------------------------------------------------------------------------

    def append(self, run: str, *, frm: str, to: str, kind: str, text: str, reply_to: str | None = None,
               from_key: str = "", to_key: str = "", enforce_burst: bool = True) -> Message:
        if kind not in KINDS:
            raise BusError(f"unknown message kind {kind!r}", code="invalid_kind")
        text = str(text or "")
        if not text.strip():
            raise BusError("message text is empty", code="empty_text")
        if len(text) > self.caps["message_max_chars"]:
            raise BusError(
                f"message is {len(text)} chars; the cap is {self.caps['message_max_chars']}. "
                "Do not retry with the same text — summarise it and post once.",
                code="message_too_long",
            )
        if self.is_closed(run):
            raise BusError("this run is closed; no more messages are accepted", code="run_closed")
        if enforce_burst:
            self._check_burst(run, frm)
        with self._lock(run):
            meta = self.meta(run) or RunMeta(run=run, created_at=time.time())
            count = int(meta.counters.get("messages", 0))
            if count >= self.caps["messages_per_run"]:
                raise BusError(
                    f"this run already holds {count} messages (cap {self.caps['messages_per_run']}). "
                    "Do not retry — finish via work_report instead.",
                    code="run_full",
                )
            msg = Message(id=new_id(), ts=time.time(), run=run, frm=frm, to=to, kind=kind, text=text,
                          reply_to=reply_to, from_key=from_key, to_key=to_key)
            with open(self.run_dir(run) / "bus.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(msg.to_dict(), ensure_ascii=False) + "\n")
            meta.counters["messages"] = count + 1
            self._write_meta(meta)
        if enforce_burst:
            self._burst.setdefault((run, frm), []).append(msg.ts)
        return msg

    def _check_burst(self, run: str, frm: str) -> None:
        now = time.time()
        window = [t for t in self._burst.get((run, frm), []) if now - t < 60.0]
        self._burst[(run, frm)] = window
        if len(window) >= self.caps["burst_per_minute"]:
            raise BusError(
                f"{frm} posted {len(window)} messages in the last minute (cap {self.caps['burst_per_minute']}). "
                "Do not retry now — batch what you have into one message later.",
                code="burst_exceeded",
            )

    def messages(self, run: str, *, since: str | None = None, limit: int = 500) -> list[Message]:
        """Messages after cursor ``since`` (a message id) in log order, newest last."""
        p = self.run_dir(run) / "bus.jsonl"
        if not p.is_file():
            return []
        rows: list[Message] = []
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(Message.from_dict(json.loads(line)))
                except ValueError:
                    continue
        if since:
            idx = next((i for i, m in enumerate(rows) if m.id == since), None)
            if idx is not None:
                rows = rows[idx + 1:]
        return rows[-limit:] if limit else rows

    def inbox(self, run: str, nick: str, *, since: str | None = None, include_broadcast: bool = True) -> list[Message]:
        return [m for m in self.messages(run, since=since, limit=0)
                if m.to == nick or (include_broadcast and m.to == "*" and m.frm != nick)]

    def mark_delivered(self, run: str, ids: Iterable[str], nick: str | None = None) -> None:
        """Mark ``ids`` delivered — to *nick* when given (broadcasts are per recipient), else outright.

        The log is small by cap, so a full rewrite under the run lock is fine.
        """
        wanted = set(ids)
        if not wanted:
            return
        p = self.run_dir(run) / "bus.jsonl"
        if not p.is_file():
            return
        with self._lock(run):
            lines = p.read_text(encoding="utf-8").splitlines()
            out = []
            for line in lines:
                try:
                    d = json.loads(line)
                except ValueError:
                    out.append(line)
                    continue
                if d.get("id") in wanted:
                    if nick:
                        seen = [str(x) for x in (d.get("delivered_to") or [])]
                        if nick not in seen:
                            seen.append(nick)
                        d["delivered_to"] = seen
                        if d.get("to") != "*":
                            d["delivered"] = True
                    else:
                        d["delivered"] = True
                out.append(json.dumps(d, ensure_ascii=False))
            tmp = p.with_suffix(".jsonl.tmp")
            tmp.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
            os.replace(tmp, p)

    def undelivered(self, run: str, nick: str, *, since: str | None = None) -> list[Message]:
        return [m for m in self.inbox(run, nick, since=since) if not m.delivered_for(nick)]

    def undelivered_count(self, run: str, nick: str) -> int:
        return len(self.undelivered(run, nick))

    # -- status -----------------------------------------------------------------------------------

    def set_status(self, run: str, nick: str, *, text: str, progress: int | None = None, state: str | None = None) -> dict[str, Any]:
        text = str(text or "").strip()
        if not text:
            raise BusError("status text is empty", code="empty_text")
        if len(text) > self.caps["status_max_chars"]:
            raise BusError(f"status is {len(text)} chars; the cap is {self.caps['status_max_chars']}", code="status_too_long")
        if progress is not None:
            try:
                progress = max(0, min(100, int(progress)))
            except (TypeError, ValueError):
                raise BusError("progress must be an integer 0..100", code="invalid_value")
        if state is not None and state not in STATES:
            raise BusError(f"state must be one of {sorted(STATES)}", code="invalid_value")
        row = {"text": text, "progress": progress, "state": state, "ts": time.time()}
        with self._lock(run):
            statuses = self.statuses(run)
            statuses[nick] = row
            d = self.run_dir(run)
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / "status.json.tmp"
            tmp.write_text(json.dumps(statuses, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, d / "status.json")
        return row

    def statuses(self, run: str) -> dict[str, dict[str, Any]]:
        p = self.run_dir(run) / "status.json"
        if not p.is_file():
            return {}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    # -- retention --------------------------------------------------------------------------------

    def remove_run(self, run: str) -> bool:
        """Delete a run's record (meta, bus log, statuses, lock) — the whole directory. ``False`` if there was none."""
        d = self.run_dir(run)
        if not d.is_dir():
            return False
        shutil.rmtree(d, ignore_errors=True)
        return not d.exists()

    def last_activity(self, run: str) -> float:
        """When the run last changed on disk: the newest mtime among its files (``created_at`` when unreadable)."""
        d = self.run_dir(run)
        stamps = []
        try:
            for f in d.iterdir():
                if f.name == ".lock":
                    continue  # the lock is touched by every reader
                try:
                    stamps.append(f.stat().st_mtime)
                except OSError:
                    pass
        except OSError:
            pass
        if stamps:
            return max(stamps)
        meta = self.meta(run)
        return float(meta.created_at) if meta else 0.0

    def sweep(self, *, older_than_secs: float, live: Iterable[str] = ()) -> list[str]:
        """Age out runs: a CLOSED run once its close is older than the window, and a run that was never closed
        (its orchestrator stopped without ``bus_run close`` — the tabs were deleted, or the agent fell back to
        the ledger) once nothing has touched it for the window AND it is not in ``live`` (a run whose sessions
        the reducer still sees is never swept, however old)."""
        removed: list[str] = []
        now = time.time()
        live_set = {str(r) for r in live}
        for meta in self.list_runs():
            if meta.run in live_set:
                continue
            if meta.closed_at:
                stale = now - float(meta.closed_at) > older_than_secs
            else:
                stale = now - self.last_activity(meta.run) > older_than_secs
            if stale and self.remove_run(meta.run):
                removed.append(meta.run)
        return removed
