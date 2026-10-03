"""Graph reducer: runs from created_by, nick stability, summary/progress priority, liveness and stalls."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ao_graph", ROOT / "hook" / "graph.py")
G = importlib.util.module_from_spec(spec)
sys.modules["ao_graph"] = G
spec.loader.exec_module(G)

ORCH = "dashboard:orch"


def base_slots():
    return [
        {"key": "orch", "title": "port to rust", "agent": "agent-orchestrator", "running": True, "created_by": ""},
        {"key": "w1", "title": "worker-1: billing", "agent": "agent-orchestrator-worker", "running": True, "created_by": ORCH,
         "todo": {"completed": 2, "total": 5, "current": "port invoice.rs"}},
        {"key": "w2", "title": "worker-2: queue", "agent": "agent-orchestrator-worker", "running": True, "created_by": ORCH},
        {"key": "lonely", "title": "chat", "agent": "o3r_default", "running": False, "created_by": ""},
        {"key": "cron-child", "title": "cron thing", "agent": "x", "running": False, "created_by": "cron:abc"},
    ]


def make(clock):
    g = G.Graph(clock=lambda: clock["t"], stall_secs=100)
    g.on_slots(base_slots())
    return g


def test_runs_are_hubs_that_created_children_and_exist_as_slots():
    g = make({"t": 1000.0})
    assert g.runs() == [ORCH]  # cron:abc created a child but is not a slot → not a hub
    assert g.run_of("w1") == ORCH and g.run_of("orch") == ORCH and g.run_of("lonely") == ""
    assert g.member_of("dashboard:w2") == (ORCH, "worker-2", "worker")
    assert g.member_of(ORCH) == (ORCH, "orchestrator", "orchestrator")
    assert g.member_of("dashboard:lonely") == ("", "", "")


def test_lonely_orchestrator_agent_is_a_run_before_dispatch():
    g = G.Graph()
    g.on_slots([{"key": "o", "agent": "agent-orchestrator", "running": True, "created_by": ""}])
    assert g.run_of("o") == "dashboard:o" and g.runs() == []  # a run for identity purposes, not yet drawn
    g.on_slots([{"key": "o", "agent": "agent-orchestrator", "running": True, "created_by": ""},
                {"key": "k", "agent": "agent-orchestrator-worker", "running": True, "created_by": "dashboard:o"}])
    assert g.runs() == ["dashboard:o"]


def test_nicks_follow_first_seen_order_and_seed_survives():
    g = make({"t": 1000.0})
    assert g.nick_for(ORCH, "w1") == "worker-1" and g.nick_for(ORCH, "w2") == "worker-2"
    g2 = G.Graph()
    g2.seed_nicks(ORCH, {"dashboard:w2": "worker-2", "dashboard:w1": "worker-1"})
    g2.on_slots(list(reversed(base_slots())))  # reversed arrival: the seed still decides
    assert g2.nick_for(ORCH, "w2") == "worker-2" and g2.nick_for(ORCH, "w1") == "worker-1"
    assert g2.slot_for_nick(ORCH, "worker-2") == "w2" and g2.slot_for_nick(ORCH, "orchestrator") == "orch"


def test_summary_and_progress_priority():
    clock = {"t": 1000.0}
    g = make(clock)
    snap = g.snapshot(ORCH)
    w1 = next(w for w in snap["workers"] if w["nick"] == "worker-1")
    assert (w1["summary"], w1["summary_source"]) == ("port invoice.rs", "todo")
    assert (w1["progress"], w1["progress_source"]) == (40, "todo")
    g.on_frame("tool_call", {"slot": "w2", "tool": "execute_bash", "kind": "shell"})
    w2 = next(w for w in g.snapshot(ORCH)["workers"] if w["nick"] == "worker-2")
    assert w2["summary"] == "running execute_bash" and w2["progress"] is None
    g.on_status(ORCH, "worker-2", {"text": "writing queue.rs", "progress": 55, "state": "working", "ts": clock["t"]})
    w2 = next(w for w in g.snapshot(ORCH)["workers"] if w["nick"] == "worker-2")
    assert (w2["summary"], w2["summary_source"], w2["progress"]) == ("writing queue.rs", "bus_status", 55)
    clock["t"] += 61  # a stale bus_status yields to the live tool signal
    g.on_frame("tool_call", {"slot": "w2", "tool": "fs_write"})
    w2 = next(w for w in g.snapshot(ORCH)["workers"] if w["nick"] == "worker-2")
    assert w2["summary"] == "running fs_write" and w2["progress"] == 55
    g.on_report(ORCH, "worker-2", "done", "queue ported")
    w2 = next(w for w in g.snapshot(ORCH)["workers"] if w["nick"] == "worker-2")
    assert w2["state"] == "done" and w2["progress"] == 100 and w2["liveness"] == "done"


def test_states_liveness_and_stall():
    clock = {"t": 1000.0}
    g = make(clock)
    views = {w["nick"]: w for w in g.snapshot(ORCH)["workers"]}
    assert views["worker-1"]["state"] == "working" and views["worker-1"]["liveness"] == "live"
    clock["t"] += 45
    assert g.node_view(ORCH, g.nodes["w1"])["liveness"] == "quiet"
    clock["t"] += 60  # 105 s idle while running > stall_secs 100
    v = g.node_view(ORCH, g.nodes["w1"])
    assert v["state"] == "stalled" and v["stalled"] is True
    g.on_frame("question_card", {"slot": "w1", "ask_id": "q1"})
    assert g.node_view(ORCH, g.nodes["w1"])["state"] == "needs_input"
    g.on_frame("question_card_resolved", {"slot": "w1"})
    g.on_frame("chat_done", {"slot": "w1"})
    assert g.node_view(ORCH, g.nodes["w1"])["state"] == "idle"
    g.on_slots([s for s in base_slots() if s["key"] != "w2"])
    assert g.node_view(ORCH, g.nodes["w2"])["state"] == "gone"
    assert g.snapshot(ORCH)["counters"]["workers"] == 2  # gone nodes stay on the ring, greyed out


def test_events_fire_for_idle_needs_input_and_gone():
    g = make({"t": 1000.0})
    seen = []
    g.subscribe(lambda run, e: seen.append((run, e["type"], e.get("nick"))))
    g.on_slots([{**s, "running": False} if s["key"] == "w1" else s for s in base_slots()])
    g.on_frame("approval", {"slot": "w2", "request_id": "r"})
    g.on_slots([s for s in base_slots() if s["key"] != "w2"])
    assert (ORCH, "idle", "worker-1") in seen and (ORCH, "needs_input", "worker-2") in seen and (ORCH, "gone", "worker-2") in seen


def test_frames_for_unknown_slots_are_ignored():
    g = make({"t": 1000.0})
    g.on_frame("tool_call", {"slot": "nope", "tool": "x"})
    g.on_frame("tool_call", "not a dict")
    assert "nope" not in g.nodes
