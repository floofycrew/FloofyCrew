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
* ``ctx.routes``: ``GET runs``, ``GET graph``, ``GET status`` for the page.

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
    "stall_secs": 600,
    "close_workers_on_done": False,
}

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


_nick_cache: dict[str, dict[str, str]] = {}


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
    live = _graph.runs()
    out = []
    for run in live:
        meta = _store.meta(run)
        hub = _graph.nodes.get(G.slot_key_of(run))
        out.append({"run": run, "title": hub.title if hub else run, "agent": hub.agent if hub else "",
                    "goal": meta.goal if meta else "", "closed": bool(meta and meta.closed_at),
                    "workers": len(_graph.children(run)), "messages": (meta.counters.get("messages", 0) if meta else 0),
                    "live": True})
    known = {r["run"] for r in out}
    for meta in _store.list_runs():
        if meta.run not in known:
            out.append({"run": meta.run, "title": meta.run, "agent": "", "goal": meta.goal, "closed": bool(meta.closed_at),
                        "workers": len(meta.nicks), "messages": meta.counters.get("messages", 0), "live": False})
    return {"runs": out}


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
    snap = _graph.snapshot(run) or {"run": run, "orchestrator": None, "workers": [], "counters": {}}
    meta = _store.meta(run)
    msgs = _store.messages(run, since=since or None, limit=200)
    statuses = _store.statuses(run)
    snap.update({
        "goal": meta.goal if meta else "", "closed": bool(meta and meta.closed_at),
        "messages": [m.to_dict() for m in msgs], "cursor": msgs[-1].id if msgs else since,
        "message_total": meta.counters.get("messages", 0) if meta else 0,
        "statuses": statuses, "now": time.time(),
    })
    return snap


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

    ctx.routes.add("GET", "runs", _route_runs)
    ctx.routes.add("GET", "graph", _route_graph)
    ctx.routes.add("GET", "status", _route_status)

    try:
        _install_specs(sock)
    except Exception as exc:  # noqa: BLE001 - a spec problem must not take the bus down
        ctx.log.error("agent spec install failed: %s", exc)
        ctx.state["spec_error"] = f"{type(exc).__name__}: {exc}"

    try:
        swept = _store.sweep(older_than_secs=float(cfg["run_retention_days"]) * 86400)
        if swept:
            ctx.log.info("swept %d closed runs", len(swept))
    except Exception:  # noqa: BLE001
        ctx.log.debug("sweep failed", exc_info=True)

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
