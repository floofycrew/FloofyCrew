"""Agent Orchestrator — the gateway half.

Adds to KiroCrew what its two existing fan-out mechanisms lack (research in
``research/orchestration-mod/``): a typed, bounded **message bus** between an
orchestrator session and the worker sessions it created (hub-and-spoke; a
worker ⇄ worker link exists only when ``peer_messaging`` is switched on),
wake-ups that carry no body, a wait-any primitive for the hub, a live
per-worker summary line, and the data feed for the hub-and-spoke graph page.

Host seams, all through ``ctx.hooks`` / ``ctx.routes`` (docs/python-api.md):

* ``before`` on ``kiro_crew.dashboard.state:DashboardState.broadcast_ws`` and
  ``…:DashboardState._broadcast`` — observe every slot-scoped frame and every
  ``slots`` snapshot the dashboard sends; the reducer in ``graph.py`` turns them
  into node state. Nothing is altered or suppressed.
* ``after`` on ``route:POST /api/session-control/send`` and
  ``route:POST /api/work-ledger/report`` — log the host's own
  ``session_send`` / ``work_report`` traffic as bus rows so the graph shows
  the *whole* conversation, not just the mod's channel.
* ``replace`` on ``route:POST /api/session-control/create`` (optional,
  ``enforce_cap``) — refuses a create when the caller is one of THIS mod's
  orchestrators and already has ``max_workers`` live children. Any other caller
  (the host's own conductor, a human) passes through untouched.
* ``after`` on ``route:DELETE /api/sessions/{key}`` and ``route:DELETE /api/sessions``
  (``follow_session_delete``) — the host's PERMANENT deletes (closing a tab only
  moves a chat to history): deleting a run's orchestrator chat removes the run's
  record, so the page does not keep a deleted run as "archived" forever.
* ``ctx.routes``: ``GET runs``, ``GET graph``, ``GET status`` for the page, and
  ``POST runs/remove`` behind its ✕ for a run whose tabs are closed but not deleted.
  Records also age out after ``run_retention_days`` (hourly sweep on the slots push).

Identity for the bus comes from ``KIROCREW_SESSION_KEY`` in the worker's
kiro-cli environment, read by ``mcp/server.py`` (stdio MCP) and forwarded over
a 0600 unix socket in ``ctx.data_dir`` — the same trust level as the host's
first-party ``kirocrew-work`` server. The two agent specs are RENDERED at
activation (absolute interpreter / server paths differ per machine) into
``~/.kiro/agents/`` with FloofyCrew's ``.floofy-owner`` sidecar and removed on
deactivate; a user's same-named spec is never overwritten.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from . import bus as B
from . import graph as G
from . import rpc as R

ORCH_AGENT = "agent-orchestrator"
WORKER_AGENT = "agent-orchestrator-worker"
OWNER_SUFFIX = ".floofy-owner"
SOCK_NAME = "bus.sock"

DEFAULTS: dict[str, Any] = {
    "max_workers": 6,
    "enforce_cap": True,
    "peer_messaging": False,  # hub-and-spoke: workers post to the orchestrator only; it relays
    "wake_idle": True,
    "steer_running": True,
    "nudge_cooldown_secs": 10,
    "inbox_wait_max_secs": 300,
    "message_max_chars": 2000,
    "run_retention_days": 7,
    "follow_session_delete": True,  # deleting the orchestrator's chat for good deletes the run's record too
    "stall_secs": 600,
    "close_workers_on_done": False,
}

SWEEP_EVERY_SECS = 3600.0

_ctx = None
_store: B.BusStore | None = None
_graph: G.Graph | None = None
_service: R.BusService | None = None
_socket: R.SocketServer | None = None
_state_ref: Any = None  # the host DashboardState, captured from the first tapped call
_installed_specs: list[Path] = []


def _cfg() -> dict[str, Any]:
    stored = _ctx.config.to_dict() if _ctx is not None else {}
    merged = dict(DEFAULTS)
    for k, v in (stored or {}).items():
        if k in merged and v is not None:
            merged[k] = v
    return merged


# --- host taps ----------------------------------------------------------------------------------------


def _tap_broadcast_ws(state, msg_type, data, *args, **kwargs):  # before-hook: observe only
    global _state_ref
    if _state_ref is None:
        _state_ref = state
    if _graph is not None and isinstance(msg_type, str):
        try:
            _graph.on_frame(msg_type, data)
        except Exception:  # noqa: BLE001 - never let the reducer fault the host's fan-out
            _ctx.log.debug("reducer on_frame failed", exc_info=True)


def _tap_broadcast(state, note, *args, **kwargs):  # before-hook on the SSE/WS note path (slots snapshot)
    global _state_ref
    if _state_ref is None:
        _state_ref = state
    if _graph is None or not isinstance(note, dict) or note.get("_type") != "slots":
        return
    slots = note.get("_slots_list_ws") or note.get("_slots_list")
    if slots is None and isinstance(note.get("slots"), str):
        try:
            slots = json.loads(note["slots"])
        except ValueError:
            slots = None
    if isinstance(slots, list):
        try:
            _graph.on_slots(slots)
            _seed_nicks_for_new_runs()
        except Exception:  # noqa: BLE001
            _ctx.log.debug("reducer on_slots failed", exc_info=True)
        _maybe_sweep()


_nick_cache: dict[str, dict[str, str]] = {}
_last_sweep = 0.0
_swept_with_state = False  # the first sweep that could ask the host about transcripts has run


def _maybe_sweep(force: bool = False) -> list[str]:
    """Hourly housekeeping, piggybacked on the slots push (the hook has no timer of its own): age out runs past
    ``run_retention_days`` and, when ``follow_session_delete`` is on, drop runs whose orchestrator transcript the
    host no longer has (deleted while the mod was inactive, or by a path the delete hooks do not see).

    The activation sweep cannot ask about transcripts (no DashboardState yet), so the first sweep after the state
    is captured is forced: a run deleted while the mod was inactive goes at the first slots push, not an hour later."""
    global _last_sweep, _swept_with_state
    if _store is None or _graph is None:
        return []
    if _state_ref is not None and not _swept_with_state:
        _swept_with_state = True
        force = True
    now = time.time()
    if not force and now - _last_sweep < SWEEP_EVERY_SECS:
        return []
    _last_sweep = now
    cfg = _cfg()
    removed: list[str] = []
    try:
        live = [r for r in _graph.runs() if _graph.is_live(r)]
        removed += _store.sweep(older_than_secs=float(cfg["run_retention_days"]) * 86400, live=live)
        for run in removed:
            _graph.forget(run)
            _nick_cache.pop(run, None)
    except Exception:  # noqa: BLE001
        _ctx.log.debug("sweep failed", exc_info=True)
    if cfg.get("follow_session_delete"):
        for meta in _store.list_runs():
            if meta.run not in removed and not _graph.is_live(meta.run) and _hub_transcript_gone(meta.run):
                if _remove_run(meta.run, "orchestrator transcript deleted"):
                    removed.append(meta.run)
    if removed:
        _ctx.log.info("removed %d run record(s): %s", len(removed), ", ".join(removed))
    return removed


def _hub_transcript_gone(run: str) -> bool:
    """True only when the host positively says the orchestrator's transcript no longer exists.
    Unknown (no DashboardState captured yet, no conversation log) is False — never delete on a guess."""
    log = getattr(_state_ref, "conversation_log", None)
    has_log = getattr(log, "has_log", None)
    if not callable(has_log):
        return False
    try:
        return not bool(has_log(run))
    except Exception:  # noqa: BLE001
        return False


def _remove_run(run: str, reason: str) -> bool:
    """Delete the run's record and forget it in the reducer. Refuses while an orchestrator or worker tab is open."""
    if _store is None or _graph is None:
        return False
    if _graph.is_live(run):
        return False
    existed = _store.remove_run(run)
    _graph.forget(run)
    _nick_cache.pop(run, None)
    if existed:
        _ctx.log.info("run %s removed (%s)", run, reason)
    return existed


def _seed_nicks_for_new_runs() -> None:
    """Keep ``meta.json`` nick maps in step with the roster so names survive a gateway restart."""
    if _graph is None or _store is None:
        return
    for run in _graph.runs():
        current = _graph.nick_map(run)
        if _nick_cache.get(run) == current:
            continue  # the slots snapshot is pushed often; touch disk only when the roster changed
        meta = _store.meta(run)
        if meta is not None and meta.nicks and not _nick_cache.get(run):
            _graph.seed_nicks(run, meta.nicks)
            current = _graph.nick_map(run)
        for key, nick in current.items():
            if meta is None or meta.nicks.get(key) != nick:
                _store.assign_nick(run, key, nick)
        _nick_cache[run] = current


async def _after_session_send(result, request, *args, **kwargs):
    """Log the host's ``session_send`` as a ``send`` row (orchestrator → worker) when both are in a run."""
    try:
        if _service is None or getattr(result, "status", 200) != 200:
            return result
        body = await request.json()
        caller = request.headers.get("X-Session-Key", "").strip()
        target = str(body.get("target") or "")
        text = str(body.get("message") or "")
        slot = _resolve_target_slot(target)
        if caller and slot and text:
            _service.record_send(caller, slot, text)
    except Exception:  # noqa: BLE001
        _ctx.log.debug("session_send tap failed", exc_info=True)
    return result


async def _after_work_report(result, request, *args, **kwargs):
    try:
        if _service is None or getattr(result, "status", 200) != 200:
            return result
        body = await request.json()
        caller = request.headers.get("X-Session-Key", "").strip()
        if caller:
            _service.record_report(caller, str(body.get("status") or ""), str(body.get("summary") or ""))
    except Exception:  # noqa: BLE001
        _ctx.log.debug("work_report tap failed", exc_info=True)
    return result


SESSION_DELETE_ROUTE = "route:DELETE /api/sessions/{key}"  # the host's permanent history delete
SESSIONS_CLEAR_ROUTE = "route:DELETE /api/sessions"  # … and its bulk "clear closed sessions"


def _response_ok(result) -> bool:
    if getattr(result, "status", 200) != 200:
        return False
    try:
        body = json.loads(getattr(result, "text", "") or "{}")
    except (TypeError, ValueError):
        return True
    return bool(body.get("ok", True)) if isinstance(body, dict) else True


async def _after_session_delete(result, request, *args, **kwargs):
    """The user deleted a chat for good (not just closed its tab). If it was a run's orchestrator, the run's record
    goes with it — the whole reason the page stopped showing a deleted run as ``archived (sessions gone)``."""
    try:
        if not _cfg().get("follow_session_delete") or not _response_ok(result):
            return result
        key = G.creator_key_of((getattr(request, "match_info", None) or {}).get("key", ""))
        if not key or _store is None or _graph is None:
            return result
        slot = G.slot_key_of(key)
        node = _graph.nodes.get(slot)
        run = _graph.run_of(slot) if node is not None else ""
        if node is not None:  # the host has dropped the slot; the next slots push may not have reached the reducer yet
            node.gone = True
            node.running = False
        if _store.meta(key) is not None or run == key:
            _remove_run(key, "orchestrator chat deleted")  # refused while a worker tab is still open (see below)
        elif run and not _graph.is_live(run) and _hub_transcript_gone(run):
            _remove_run(run, "last worker chat deleted after its orchestrator")  # the orchestrator went first
    except Exception:  # noqa: BLE001
        _ctx.log.debug("session delete tap failed", exc_info=True)
    return result


async def _after_sessions_clear(result, request, *args, **kwargs):
    """Bulk clear names no keys in its response: ask the host which orchestrator transcripts are gone."""
    try:
        if _cfg().get("follow_session_delete") and _response_ok(result):
            _maybe_sweep(force=True)
    except Exception:  # noqa: BLE001
        _ctx.log.debug("sessions clear tap failed", exc_info=True)
    return result


# --- KAS identity bridge -----------------------------------------------------------------------------------

KAS_PROJECT_TARGET = "kiro_crew.acp.kas_agents:_project_mcp_servers"
BUS_SERVER = "agent-orchestrator-bus"


def _after_kas_project_mcp_servers(result, spec=None, agent_id="", stub_server_names=None, session_key="", *args, **kwargs):
    """Give OUR bus server, and only it, the session identity the host gives its own servers on KAS.

    On the kiro-cli backend the MCP children inherit the process environment, which carries
    ``KIROCREW_SESSION_KEY`` — the bus identifies its caller from it. On the KAS backend the host
    projects a spec's ``mcpServers`` onto the wire (``kas_agents._project_mcp_servers``), withholds
    ``env`` for every server it does not own ("env routinely holds tokens") and injects
    ``KIROCREW_SESSION_KEY`` into its managed servers alone — so without this hook every bus call on
    KAS fails ``caller_unidentified`` and the run falls back to the ledger (seen on the test box).
    The entry is recognised by its argv (the mod's own ``mcp/server.py``), never by name alone.
    """
    key = str(session_key or kwargs.get("session_key") or "")
    if not key or not isinstance(result, dict):
        return result
    entry = result.get(BUS_SERVER)
    if not isinstance(entry, dict):
        return result
    ours = str(Path(_ctx.mod_dir) / "mcp" / "server.py") if _ctx is not None else ""
    if not ours or ours not in [str(a) for a in (entry.get("args") or [])]:
        return result  # a different server wearing our name is not ours to vouch for
    env = entry.get("env")
    if not isinstance(env, dict):
        env = {}
        entry["env"] = env
    env["KIROCREW_SESSION_KEY"] = key
    return result


async def _replace_session_create(call_next, request, *args, **kwargs):
    """Refuse a create by one of OUR orchestrators that would exceed ``max_workers``; everyone else passes."""
    cfg = _cfg()
    if cfg.get("enforce_cap") and _graph is not None:
        caller = request.headers.get("X-Session-Key", "").strip()
        node = _graph.nodes.get(G.slot_key_of(caller)) if caller else None
        if node is not None and node.agent == ORCH_AGENT:
            live = [n for n in _graph.children(caller) if not n.gone]
            cap = int(cfg.get("max_workers", 6))
            if len(live) >= cap:
                from aiohttp import web  # the host's own HTTP library

                return web.json_response(
                    {"error": f"agent-orchestrator cap: {len(live)} live workers already (max_workers={cap}). "
                              "Do not retry; reuse an idle worker with session_send or wait for one to finish.",
                     "code": "orchestrator_cap_reached"},
                    status=429,
                )
    return await call_next(request, *args, **kwargs)


def _resolve_target_slot(target: str) -> str:
    if not target:
        return ""
    if _graph is not None:
        key = G.slot_key_of(target)
        if key in _graph.nodes:
            return key
        for n in _graph.nodes.values():
            if n.title == target:
                return n.slot
    return G.slot_key_of(target)


# --- wake ------------------------------------------------------------------------------------------------


async def _wake(slot_key: str, notice: str, msg_id: str) -> str:
    """Nudge the recipient: steer a running kiro/KAS turn, start a turn on an idle slot, else queue."""
    cfg = _cfg()
    state = _state_ref
    if state is None:
        return "no-state"
    slot = state.get_slot(slot_key) if hasattr(state, "get_slot") else None
    if slot is None:
        return "no-slot"
    if getattr(slot, "running", False):
        if cfg.get("steer_running"):
            try:
                from kiro_crew.dashboard.chat_delivery import STEER_STEERED, steer_into_running_turn

                outcome = await steer_into_running_turn(state, slot, notice)
                if outcome == STEER_STEERED:
                    return "steered"
            except Exception:  # noqa: BLE001
                _ctx.log.debug("steer failed for %s", slot_key, exc_info=True)
        return "busy"  # the recipient's own bus_inbox call picks it up at its next step
    if not cfg.get("wake_idle"):
        return "idle-nowake"
    try:
        from kiro_crew.dashboard.chat_runner import _run_chat

        started = slot.enqueue_or_run_prompt(notice, _run_chat, state)
        try:
            state.push_slots_update()
        except Exception:  # noqa: BLE001
            pass
        return "started" if started else "queued"
    except Exception:  # noqa: BLE001
        _ctx.log.debug("wake failed for %s", slot_key, exc_info=True)
        return "error"


# --- routes for the page ----------------------------------------------------------------------------------


def _route_runs(request):
    if _graph is None or _store is None:
        return {"runs": []}
    out = []
    for run in _graph.runs():
        meta = _store.meta(run)
        hub = _graph.nodes.get(G.slot_key_of(run))
        out.append({"run": run, "title": hub.title if hub else run, "agent": hub.agent if hub else "",
                    "goal": meta.goal if meta else "", "closed": bool(meta and meta.closed_at),
                    "workers": len(_graph.children(run)), "messages": (meta.counters.get("messages", 0) if meta else 0),
                    "live": _graph.is_live(run)})  # False once every tab of the run is closed: the picker shows ○
    known = {r["run"] for r in out}
    for meta in _store.list_runs():
        if meta.run not in known:
            out.append({"run": meta.run, "title": meta.goal or meta.run, "agent": "", "goal": meta.goal, "closed": bool(meta.closed_at),
                        "workers": sum(1 for n in meta.nicks.values() if n != "orchestrator"),
                        "messages": meta.counters.get("messages", 0), "live": False})
    return {"runs": out}


def _route_runs_remove(request):
    """``POST runs/remove {"run"}`` — the page's ✕: delete a run's record once its tabs are gone (closed or deleted).
    A run with an open orchestrator or worker tab is refused: its sessions would recreate the record on the next
    bus call, and the picker would keep listing it from the reducer anyway."""
    if _graph is None or _store is None:
        return 503, {"error": "not ready"}
    try:
        body = request.json() if callable(getattr(request, "json", None)) else None
    except ValueError:
        body = None
    run = str((body or {}).get("run") or "").strip() if isinstance(body, dict) else ""
    if not run:
        return 400, {"error": "body needs {\"run\": \"<run key>\"}", "code": "missing_run"}
    run = G.creator_key_of(run)
    if _graph.is_live(run):
        return 409, {"error": "the run still has an open chat (orchestrator or worker); close or delete those tabs first",
                     "code": "run_live", "run": run}
    known = _store.meta(run) is not None or G.slot_key_of(run) in _graph.nodes
    if not known:
        return 404, {"error": "no such run", "code": "unknown_run", "run": run}
    _remove_run(run, "removed from the page")
    return {"ok": True, "run": run}


def _route_graph(request):
    if _graph is None or _store is None:
        return 503, {"error": "not ready"}
    q = request.query or {}
    run = (q.get("run") or [""])[0] if isinstance(q.get("run"), list) else (q.get("run") or "")
    since = (q.get("since") or [""])[0] if isinstance(q.get("since"), list) else (q.get("since") or "")
    if not run:
        runs = _graph.runs()
        if not runs:
            return {"run": "", "orchestrator": None, "workers": [], "messages": [], "counters": {}, "cursor": ""}
        run = runs[0]
    snap = _graph.snapshot(run) or _snapshot_from_disk(run)
    meta = _store.meta(run)
    msgs = _store.messages(run, since=since or None, limit=200)
    statuses = _store.statuses(run)
    snap.update({
        "goal": meta.goal if meta else "", "closed": bool(meta and meta.closed_at),
        "messages": [m.to_dict() for m in msgs], "cursor": msgs[-1].id if msgs else since,
        "message_total": meta.counters.get("messages", 0) if meta else 0,
        "statuses": statuses, "now": time.time(),
        # every tab of the run is closed or deleted (drawn from the reducer's memory or from disk): the page shows
        # it as archived and offers ✕ remove — a live run never does
        "sessions_gone": not _graph.is_live(run),
    })
    return snap


def _snapshot_from_disk(run: str) -> dict[str, Any]:
    """A run whose sessions the live reducer no longer knows (tabs closed, or a reload before the next slots
    push): the picker lists it from ``meta.json``, so the graph draws it from disk too — members from the nick
    map, each worker's last ``bus_status`` line, no liveness. Without this the page shows its empty state for a
    run it has just listed."""
    meta = _store.meta(run) if _store else None
    if meta is None:
        return {"run": run, "orchestrator": None, "workers": [], "counters": {}}
    statuses = _store.statuses(run)
    done_states = {"done"}

    def view(key: str, nick: str) -> dict[str, Any]:
        row = statuses.get(nick) or {}
        state = str(row.get("state") or "")
        progress = 100 if state in done_states else row.get("progress")
        return {"slot": G.slot_key_of(key), "key": key, "nick": nick, "role": "orchestrator" if nick == "orchestrator" else "worker",
                "title": meta.goal if nick == "orchestrator" else nick, "agent": "", "model": "", "running": False,
                "needs_input": False, "gone": True, "state": "done" if state in done_states else "gone",
                "summary": str(row.get("text") or ""), "summary_source": "bus_status" if row else "",
                "progress": progress, "progress_source": "bus_status" if row else "", "liveness": "gone",
                "last_activity": float(row.get("ts") or 0.0), "age_secs": max(0.0, (meta.closed_at or time.time()) - meta.created_at),
                "stalled": False, "todo": None, "ledger": None, "href": f"/chat?slot={G.slot_key_of(key)}"}

    workers = [view(key, nick) for key, nick in sorted(meta.nicks.items(), key=lambda kv: _nick_order(kv[1])) if nick != "orchestrator"]
    return {
        "run": run, "orchestrator": view(run, "orchestrator"), "workers": workers, "from_disk": True,
        "counters": {"workers": len(workers), "done": sum(1 for w in workers if w["state"] == "done"), "needs_input": 0, "working": 0},
    }


def _nick_order(nick: str) -> tuple[int, str]:
    try:
        return int(nick.rsplit("-", 1)[1]), nick
    except (IndexError, ValueError):
        return 10**6, nick


def _route_status(request):
    return {
        "active": _service is not None, "socket": str(_socket.path) if _socket else "",
        "config": _cfg(), "runs": len(_graph.runs()) if _graph else 0,
        "specs": [str(p) for p in _installed_specs],
    }


# --- agent specs -----------------------------------------------------------------------------------------------


def _agents_dir() -> Path:
    return Path(os.environ.get("KIRO_HOME") or (Path.home() / ".kiro")) / "agents"


def _host_mcp_servers() -> dict[str, Any]:
    """Copy the host's own first-party server entries from its conductor spec (the host's rendering wins)."""
    wanted = ("kirocrew-core", "kirocrew-dashboard", "kirocrew-work")
    src = _agents_dir() / "kirocrew-conductor.json"
    try:
        spec = json.loads(src.read_text(encoding="utf-8"))
        found = {k: v for k, v in (spec.get("mcpServers") or {}).items() if k in wanted}
        if len(found) == len(wanted):
            return found
    except (OSError, ValueError):
        pass
    kirocrew = shutil.which("kirocrew") or "kirocrew"
    return {"kirocrew-core": {"command": kirocrew, "args": ["mcp-core"]},
            "kirocrew-dashboard": {"command": kirocrew, "args": ["mcp-dashboard"]},
            "kirocrew-work": {"command": kirocrew, "args": ["mcp-work"]}}


def _render_spec(template_path: Path, sock: Path) -> dict[str, Any]:
    text = template_path.read_text(encoding="utf-8")
    interpreter = getattr(_ctx.host, "interpreter", "") or sys.executable
    server = Path(_ctx.mod_dir) / "mcp" / "server.py"
    text = (text.replace("{{interpreter}}", json.dumps(str(interpreter))[1:-1])
                .replace("{{server_path}}", json.dumps(str(server))[1:-1])
                .replace("{{sock}}", json.dumps(str(sock))[1:-1]))
    spec = json.loads(text)
    servers = spec.setdefault("mcpServers", {})
    for name, entry in _host_mcp_servers().items():
        if name in spec.get("tools", []) or f"@{name}" in spec.get("tools", []):
            servers.setdefault(name, entry)
    return spec


def _install_specs(sock: Path) -> None:
    agents_dir = _agents_dir()
    agents_dir.mkdir(parents=True, exist_ok=True)
    for name in (ORCH_AGENT, WORKER_AGENT):
        tmpl = Path(_ctx.mod_dir) / "agents" / f"{name}.json.tmpl"
        target = agents_dir / f"{name}.json"
        owner = target.with_name(target.name + OWNER_SUFFIX)
        if target.exists() and not _owned_by_us(owner):
            _ctx.log.warning("agent spec %s exists and is not ours; leaving it alone", target)
            _ctx.state.setdefault("spec_conflicts", []).append(str(target))
            continue
        spec = _render_spec(tmpl, sock)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
        owner.write_text(json.dumps({"mod": _ctx.mod_id}) + "\n", encoding="utf-8")
        _installed_specs.append(target)


def _owned_by_us(owner: Path) -> bool:
    try:
        return json.loads(owner.read_text(encoding="utf-8")).get("mod") == _ctx.mod_id
    except (OSError, ValueError, AttributeError):
        return False


def _remove_specs() -> None:
    for target in list(_installed_specs):
        owner = target.with_name(target.name + OWNER_SUFFIX)
        if _owned_by_us(owner):
            for p in (target, owner):
                try:
                    p.unlink()
                except OSError:
                    pass
    _installed_specs.clear()


def _socket_path(data_dir: Path) -> Path:
    """``<data_dir>/bus.sock`` unless that exceeds the AF_UNIX path cap (~104 bytes), then a 0700 dir under /tmp."""
    candidate = data_dir / SOCK_NAME
    if len(str(candidate).encode("utf-8")) < 100:
        return candidate
    import tempfile

    short = Path(tempfile.gettempdir()) / f"floofy-agent-orchestrator-{os.getuid() if hasattr(os, 'getuid') else 'u'}"
    short.mkdir(mode=0o700, parents=True, exist_ok=True)
    return short / SOCK_NAME


# --- lifecycle -----------------------------------------------------------------------------------------------


def activate(ctx):
    global _ctx, _store, _graph, _service, _socket
    _ctx = ctx
    cfg = _cfg()
    data_dir = Path(ctx.data_dir)
    _store = B.BusStore(data_dir / "runs", caps={"message_max_chars": int(cfg["message_max_chars"])})
    _graph = G.Graph(orchestrator_agent=ORCH_AGENT, stall_secs=float(cfg["stall_secs"]))
    _service = R.BusService(_store, _graph, config=cfg, wake=_wake, config_provider=_cfg)
    sock = _socket_path(data_dir)

    ctx.hooks.before("kiro_crew.dashboard.state:DashboardState.broadcast_ws", _tap_broadcast_ws)
    ctx.hooks.before("kiro_crew.dashboard.state:DashboardState._broadcast", _tap_broadcast)
    ctx.hooks.after("route:POST /api/session-control/send", _after_session_send)
    ctx.hooks.after("route:POST /api/work-ledger/report", _after_work_report)
    ctx.hooks.replace("route:POST /api/session-control/create", _replace_session_create)
    for target, fn in ((SESSION_DELETE_ROUTE, _after_session_delete), (SESSIONS_CLEAR_ROUTE, _after_sessions_clear)):
        try:
            ctx.hooks.after(target, fn)
        except Exception as exc:  # noqa: BLE001 - a host without the route: records still age out on the sweep
            ctx.log.warning("%s not hookable on host %s (%s): deleted chats will not remove their run until the "
                            "hourly sweep or the page's remove button", target, ctx.host.version, exc)
    try:
        ctx.hooks.after(KAS_PROJECT_TARGET, _after_kas_project_mcp_servers)
        ctx.state["kas_identity_bridge"] = True
    except Exception as exc:  # noqa: BLE001 - a host without the seam: the bus still works on the kiro-cli backend
        ctx.state["kas_identity_bridge"] = False
        ctx.log.warning("KAS identity bridge unavailable on host %s (%s): bus calls from KAS sessions will be "
                        "caller_unidentified; the kiro-cli backend is unaffected", ctx.host.version, exc)

    ctx.routes.add("GET", "runs", _route_runs)
    ctx.routes.add("POST", "runs/remove", _route_runs_remove)
    ctx.routes.add("GET", "graph", _route_graph)
    ctx.routes.add("GET", "status", _route_status)

    try:
        _install_specs(sock)
    except Exception as exc:  # noqa: BLE001 - a spec problem must not take the bus down
        ctx.log.error("agent spec install failed: %s", exc)
        ctx.state["spec_error"] = f"{type(exc).__name__}: {exc}"

    global _last_sweep, _swept_with_state
    _last_sweep = 0.0
    _swept_with_state = False
    _maybe_sweep(force=True)  # retention only here: no DashboardState yet, so no transcript check (first slots push)

    _socket = R.SocketServer(_service, sock)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        loop.create_task(_start_socket(ctx))
    else:
        ctx.log.warning("no running loop at activate; bus socket not started")

    ctx.state.update({"active": True, "orchestrator_agent": ORCH_AGENT, "worker_agent": WORKER_AGENT,
                      "socket": str(sock), "max_workers": cfg["max_workers"], "specs": [str(p) for p in _installed_specs]})
    ctx.log.info("agent-orchestrator active on host %s (socket %s)", ctx.host.version, sock)


async def _start_socket(ctx):
    try:
        await _socket.start()
        ctx.state["socket_listening"] = True
    except Exception as exc:  # noqa: BLE001
        ctx.log.error("bus socket failed to start: %s", exc)
        ctx.state["socket_listening"] = False
        ctx.state["socket_error"] = f"{type(exc).__name__}: {exc}"


def deactivate(ctx):
    global _service, _socket, _graph, _store, _state_ref
    sock = _socket
    _socket = None
    if sock is not None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(sock.stop())
        except RuntimeError:
            try:
                sock.path.unlink()
            except OSError:
                pass
    _remove_specs()
    _service = None
    _graph = None
    _store = None
    _state_ref = None
    ctx.state["active"] = False
