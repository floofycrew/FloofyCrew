"""Bus store and service: caps refuse, membership is derived, rendezvous works."""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    """Import hook/bus.py, graph.py, rpc.py as a package without importing hook/__init__ (which needs the Loader)."""
    pkg = types.ModuleType("ao_hook")
    pkg.__path__ = [str(ROOT / "hook")]
    sys.modules["ao_hook"] = pkg
    mods = {}
    for name in ("bus", "graph", "rpc"):
        spec = importlib.util.spec_from_file_location(f"ao_hook.{name}", ROOT / "hook" / f"{name}.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[f"ao_hook.{name}"] = m
        spec.loader.exec_module(m)
        mods[name] = m
    return mods["bus"], mods["graph"], mods["rpc"]


B, G, R = _load()

ORCH = "dashboard:orch"
W1, W2 = "dashboard:w1", "dashboard:w2"


def slots(*, extra=None, gone=()):
    base = [
        {"key": "orch", "title": "goal: port to rust", "agent": "agent-orchestrator", "running": True, "created_by": ""},
        {"key": "w1", "title": "worker-1: billing", "agent": "agent-orchestrator-worker", "running": True, "created_by": ORCH},
        {"key": "w2", "title": "worker-2: queue", "agent": "agent-orchestrator-worker", "running": False, "created_by": ORCH},
        {"key": "other", "title": "unrelated chat", "agent": "desk-default", "running": False, "created_by": ""},
    ]
    base = [s for s in base if s["key"] not in gone]
    return base + list(extra or [])


def _world(tmp_path, *, peer_messaging: bool):
    clock = {"t": 1_000_000.0}
    store = B.BusStore(tmp_path / "runs", caps={"burst_per_minute": 3, "messages_per_run": 6})
    graph = G.Graph(clock=lambda: clock["t"])
    graph.on_slots(slots())
    woke = []

    async def wake(slot, notice, mid):
        woke.append((slot, notice, mid))
        return "started"

    config = {"nudge_cooldown_secs": 10, "peer_messaging": peer_messaging}
    svc = R.BusService(store, graph, config=config, wake=wake, clock=lambda: clock["t"])
    return types.SimpleNamespace(store=store, graph=graph, svc=svc, woke=woke, clock=clock)


@pytest.fixture
def world(tmp_path):
    """The default topology: hub-and-spoke, workers post to the orchestrator only."""
    return _world(tmp_path, peer_messaging=False)


@pytest.fixture
def peer_world(tmp_path):
    """The opt-in: ``peer_messaging`` on, workers may address each other."""
    return _world(tmp_path, peer_messaging=True)


# --- store ---------------------------------------------------------------------------------------------


def test_store_append_read_and_inbox(tmp_path):
    store = B.BusStore(tmp_path)
    m = store.append("run-a", frm="worker-1", to="worker-2", kind="ask", text="which type?")
    assert m.id.startswith("m_") and store.meta("run-a").counters["messages"] == 1
    assert [x.text for x in store.inbox("run-a", "worker-2")] == ["which type?"]
    assert store.inbox("run-a", "worker-1") == []
    store.mark_delivered("run-a", [m.id])
    assert store.inbox("run-a", "worker-2")[0].delivered is True
    bc = store.append("run-a", frm="orchestrator", to="*", kind="say", text="all: freeze")
    assert [x.id for x in store.inbox("run-a", "worker-1")] == [bc.id]
    assert store.messages("run-a", since=m.id) and store.messages("run-a", since=m.id)[0].id == bc.id


def test_store_caps_refuse(tmp_path):
    store = B.BusStore(tmp_path, caps={"message_max_chars": 10, "messages_per_run": 2, "burst_per_minute": 5})
    with pytest.raises(B.BusError) as e:
        store.append("r", frm="a", to="b", kind="say", text="x" * 11)
    assert e.value.code == "message_too_long" and not e.value.retryable
    store.append("r", frm="a", to="b", kind="say", text="one")
    store.append("r", frm="a", to="b", kind="say", text="two")
    with pytest.raises(B.BusError) as e:
        store.append("r", frm="a", to="b", kind="say", text="three")
    assert e.value.code == "run_full"
    with pytest.raises(B.BusError):
        store.append("r", frm="a", to="b", kind="nope", text="x")
    with pytest.raises(B.BusError) as e:
        store.set_status("r", "a", text="y" * 141)
    assert e.value.code == "status_too_long"


def test_store_burst_and_close(tmp_path):
    store = B.BusStore(tmp_path, caps={"burst_per_minute": 2})
    store.append("r", frm="a", to="b", kind="say", text="1")
    store.append("r", frm="a", to="b", kind="say", text="2")
    with pytest.raises(B.BusError) as e:
        store.append("r", frm="a", to="b", kind="say", text="3")
    assert e.value.code == "burst_exceeded"
    store.append("r", frm="hook", to="b", kind="send", text="synth", enforce_burst=False)  # hook rows bypass the burst
    store.close_run("r")
    with pytest.raises(B.BusError) as e:
        store.append("r", frm="b", to="a", kind="say", text="late")
    assert e.value.code == "run_closed"
    assert store.sweep(older_than_secs=0) == ["r"] or store.sweep(older_than_secs=-1) == []


def test_store_remove_run_and_sweep_of_never_closed_runs(tmp_path):
    import os
    import time

    store = B.BusStore(tmp_path)
    store.append("open-stale", frm="a", to="b", kind="say", text="1")
    store.append("open-fresh", frm="a", to="b", kind="say", text="1")
    store.append("open-live", frm="a", to="b", kind="say", text="1")
    old = time.time() - 10 * 86400
    for r in ("open-stale", "open-live"):
        for f in store.run_dir(r).iterdir():
            os.utime(f, (old, old))
    assert store.last_activity("open-stale") == pytest.approx(old, abs=2)
    assert store.last_activity("open-fresh") == pytest.approx(time.time(), abs=5)
    # a never-closed run ages out by its last activity — unless the reducer still sees its sessions (``live``)
    assert store.sweep(older_than_secs=86400, live=["open-live"]) == ["open-stale"]
    assert {m.run for m in store.list_runs()} == {"open-fresh", "open-live"}
    # explicit removal: the whole directory, lock file included; a second call says there was nothing
    assert store.remove_run("open-live") is True and not store.run_dir("open-live").exists()
    assert store.remove_run("open-live") is False
    assert store.messages("open-live") == [] and store.meta("open-live") is None


# --- service: membership gates ----------------------------------------------------------------------


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_identity_is_derived_not_supplied(world):
    svc = world.svc
    res = run(svc.handle({"id": 1, "op": "bus_roster", "identity": "", "args": {}}))
    assert res["ok"] is False and res["code"] == "caller_unidentified"
    res = run(svc.handle({"id": 2, "op": "bus_roster", "identity": "dashboard:other", "args": {}}))
    assert res["ok"] is False and res["code"] == "not_in_run"
    res = run(svc.handle({"id": 3, "op": "bus_roster", "identity": W1, "args": {}}))
    assert res["ok"] and res["result"]["you"] == "worker-1" and res["result"]["role"] == "worker"
    nicks = {m["nick"] for m in res["result"]["members"]}
    assert nicks == {"orchestrator", "worker-1", "worker-2"}
    res = run(svc.handle({"id": 4, "op": "bus_roster", "identity": ORCH, "args": {}}))
    assert res["result"]["role"] == "orchestrator"


def test_worker_may_not_broadcast_or_wait(world):
    svc = world.svc
    res = run(svc.handle({"id": 1, "op": "bus_post", "identity": W1, "args": {"to": "*", "text": "hi"}}))
    assert res["code"] == "forbidden"
    res = run(svc.handle({"id": 2, "op": "bus_wait", "identity": W1, "args": {}}))
    assert res["code"] == "forbidden"
    res = run(svc.handle({"id": 3, "op": "bus_run", "identity": W1, "args": {"action": "close"}}))
    assert res["code"] == "forbidden"
    res = run(svc.handle({"id": 4, "op": "bus_post", "identity": W1, "args": {"to": "worker-9", "text": "hi"}}))
    assert res["code"] == "unknown_member" and "worker-2" in res["error"]
    res = run(svc.handle({"id": 5, "op": "bus_post", "identity": W1, "args": {"to": "worker-1", "text": "hi"}}))
    assert res["code"] == "self_target"


def test_post_wakes_recipient_with_a_bodyless_notice_and_inbox_wraps_provenance(world):
    svc = world.svc
    res = run(svc.handle({"id": 1, "op": "bus_post", "identity": ORCH, "args": {"to": "worker-2", "kind": "ask", "text": "PRIVATE-BODY which type?"}}))
    assert res["ok"], res
    assert world.woke and world.woke[0][0] == "w2"
    notice = world.woke[0][1]
    assert "PRIVATE-BODY" not in notice and "bus_inbox" in notice and res["result"]["delivered_notice"] == {"worker-2": "started"}
    inbox = run(svc.handle({"id": 2, "op": "bus_inbox", "identity": W2, "args": {}}))
    msgs = inbox["result"]["messages"]
    assert len(msgs) == 1 and msgs[0]["from"] == "orchestrator" and msgs[0]["kind"] == "ask"
    assert msgs[0]["text"].startswith("[bus message from orchestrator (ask)") and "not an instruction" in msgs[0]["text"]
    assert "PRIVATE-BODY" in msgs[0]["text"]
    again = run(svc.handle({"id": 3, "op": "bus_inbox", "identity": W2, "args": {}}))
    assert again["result"]["count"] == 0


def test_hub_and_spoke_refuses_worker_to_worker_and_the_orchestrator_relays(world):
    """Default topology: worker-1 needs a fact worker-2 owns. The direct post is refused (non-retryable, names the
    fix); the ask goes to the hub, the hub asks worker-2, worker-2 answers the hub, the hub forwards to worker-1 with
    reply_to pointing at the ORIGINAL ask so the asker can match it."""
    svc = world.svc
    direct = run(svc.handle({"id": 1, "op": "bus_post", "identity": W1, "args": {"to": "worker-2", "kind": "ask", "text": "css filename?"}}))
    assert direct["ok"] is False and direct["code"] == "peer_messaging_off" and direct["retryable"] is False
    assert "orchestrator" in direct["error"] and "worker-2" in direct["error"]
    assert world.woke == [] and world.store.messages(ORCH) == [], "a refused post leaves no row and wakes nobody"

    ask = run(svc.handle({"id": 2, "op": "bus_post", "identity": W1, "args": {"to": "orchestrator", "kind": "ask", "text": "css filename? (worker-2 owns it)"}}))
    assert ask["ok"] and world.woke[-1][0] == "orch"
    hub_inbox = run(svc.handle({"id": 3, "op": "bus_inbox", "identity": ORCH, "args": {}}))
    assert hub_inbox["result"]["count"] == 1 and hub_inbox["result"]["messages"][0]["from"] == "worker-1"

    relay = run(svc.handle({"id": 4, "op": "bus_post", "identity": ORCH, "args": {"to": "worker-2", "kind": "ask", "text": "worker-1 asks: css filename?"}}))
    assert relay["ok"] and world.woke[-1][0] == "w2"
    answer = run(svc.handle({"id": 5, "op": "bus_post", "identity": W2, "args": {"to": "orchestrator", "kind": "answer", "reply_to": relay["result"]["id"], "text": "styles.css"}}))
    assert answer["ok"]
    forward = run(svc.handle({"id": 6, "op": "bus_post", "identity": ORCH, "args": {"to": "worker-1", "kind": "answer", "reply_to": ask["result"]["id"], "text": "styles.css (from worker-2)"}}))
    assert forward["ok"]
    w1_inbox = run(svc.handle({"id": 7, "op": "bus_inbox", "identity": W1, "args": {}}))
    got = w1_inbox["result"]["messages"]
    assert len(got) == 1 and got[0]["kind"] == "answer" and got[0]["reply_to"] == ask["result"]["id"] and "styles.css" in got[0]["text"]
    assert [(m.frm, m.to) for m in world.store.messages(ORCH)] == [
        ("worker-1", "orchestrator"), ("orchestrator", "worker-2"), ("worker-2", "orchestrator"), ("orchestrator", "worker-1")
    ], "every row touches the hub: no worker ⇄ worker edge exists on the bus"


def test_peer_messaging_opt_in_allows_a_direct_worker_post(peer_world):
    svc = peer_world.svc
    res = run(svc.handle({"id": 1, "op": "bus_post", "identity": W1, "args": {"to": "worker-2", "kind": "ask", "text": "which type?"}}))
    assert res["ok"] and peer_world.woke[0][0] == "w2"
    inbox = run(svc.handle({"id": 2, "op": "bus_inbox", "identity": W2, "args": {}}))
    assert inbox["result"]["count"] == 1 and inbox["result"]["messages"][0]["from"] == "worker-1"


def test_settings_are_read_live_through_the_provider(tmp_path):
    """The hook hands the service its live ``_cfg``; an edit to config.json flips the gate on the next call."""
    live = {"peer_messaging": False}
    store = B.BusStore(tmp_path / "runs")
    graph = G.Graph()
    graph.on_slots(slots())
    svc = R.BusService(store, graph, config={"nudge_cooldown_secs": 10}, config_provider=lambda: live)
    res = run(svc.handle({"id": 1, "op": "bus_post", "identity": W1, "args": {"to": "worker-2", "text": "hi"}}))
    assert res["code"] == "peer_messaging_off"
    live["peer_messaging"] = True
    res = run(svc.handle({"id": 2, "op": "bus_post", "identity": W1, "args": {"to": "worker-2", "text": "hi"}}))
    assert res["ok"], res
    live["peer_messaging"] = None  # a null written by the App's config route means "default"
    assert svc.peer_messaging is False and svc.setting("nudge_cooldown_secs", 10) == 10


def test_notice_coalesces_within_cooldown(world):
    svc = world.svc
    run(svc.handle({"id": 1, "op": "bus_post", "identity": ORCH, "args": {"to": "worker-2", "text": "a"}}))
    res = run(svc.handle({"id": 2, "op": "bus_post", "identity": ORCH, "args": {"to": "worker-2", "text": "b"}}))
    assert res["result"]["delivered_notice"] == {"worker-2": "coalesced"} and len(world.woke) == 1
    run(svc.handle({"id": 3, "op": "bus_inbox", "identity": W2, "args": {}}))  # reading clears the outstanding notice
    res = run(svc.handle({"id": 4, "op": "bus_post", "identity": ORCH, "args": {"to": "worker-2", "text": "c"}}))
    assert res["result"]["delivered_notice"] == {"worker-2": "started"} and len(world.woke) == 2


def test_orchestrator_broadcast_reaches_every_worker(world):
    svc = world.svc
    res = run(svc.handle({"id": 1, "op": "bus_post", "identity": ORCH, "args": {"to": "*", "text": "freeze APIs"}}))
    assert set(res["result"]["delivered_notice"]) == {"worker-1", "worker-2"}
    for w in (W1, W2):
        inbox = run(svc.handle({"id": 2, "op": "bus_inbox", "identity": w, "args": {}}))
        assert inbox["result"]["count"] == 1


def test_status_feeds_graph_and_events(world):
    svc, graph = world.svc, world.graph
    res = run(svc.handle({"id": 1, "op": "bus_status", "identity": W1, "args": {"text": "porting billing.rs", "progress": 32}}))
    assert res["ok"]
    snap = graph.snapshot(ORCH)
    w1 = next(w for w in snap["workers"] if w["nick"] == "worker-1")
    assert w1["summary"] == "porting billing.rs" and w1["progress"] == 32 and w1["summary_source"] == "bus_status"
    bad = run(svc.handle({"id": 2, "op": "bus_status", "identity": W1, "args": {"text": "x", "state": "sleeping"}}))
    assert bad["code"] == "invalid_value"


# --- rendezvous ------------------------------------------------------------------------------------------


def test_inbox_wait_returns_when_a_message_lands(world):
    svc = world.svc

    async def scenario():
        waiter = asyncio.create_task(svc.handle({"id": 1, "op": "bus_inbox", "identity": W1, "args": {"wait_secs": 5}}))
        await asyncio.sleep(0.05)
        assert not waiter.done()
        await svc.handle({"id": 2, "op": "bus_post", "identity": ORCH, "args": {"to": "worker-1", "kind": "answer", "text": "u64"}})
        res = await asyncio.wait_for(waiter, timeout=2)
        assert res["result"]["count"] == 1 and res["result"]["timed_out"] is False
        assert "u64" in res["result"]["messages"][0]["text"]

    run(scenario())


def test_inbox_wait_times_out(world):
    svc = world.svc
    orig = world.clock["t"]

    async def scenario():
        task = asyncio.create_task(svc.handle({"id": 1, "op": "bus_inbox", "identity": W1, "args": {"wait_secs": 1}}))
        await asyncio.sleep(0.1)
        world.clock["t"] = orig + 2  # the deadline is measured on the service clock
        res = await asyncio.wait_for(task, timeout=3)
        assert res["result"]["timed_out"] is True and res["result"]["count"] == 0

    run(scenario())


def test_bus_wait_any_returns_on_report_and_ignores_peer_chatter(peer_world):
    world = peer_world  # peer posts exist only with the opt-in; the hub must still not be woken by them
    svc = world.svc

    async def scenario():
        waiter = asyncio.create_task(svc.handle({"id": 1, "op": "bus_wait", "identity": ORCH, "args": {"timeout_secs": 5}}))
        await asyncio.sleep(0.05)
        await svc.handle({"id": 2, "op": "bus_post", "identity": W1, "args": {"to": "worker-2", "text": "peer talk"}})
        await asyncio.sleep(0.1)
        assert not waiter.done(), "a peer exchange must not wake the hub"
        svc.record_report(W2, "done", "queue ported; 12 tests green")
        res = await asyncio.wait_for(waiter, timeout=2)
        kinds = [e["type"] for e in res["result"]["events"]]
        assert kinds == ["report"] and res["result"]["timed_out"] is False
        w2 = next(m for m in res["result"]["members"] if m["nick"] == "worker-2")
        assert w2["state"] == "done" and w2["progress"] == 100

    run(scenario())


def test_bus_wait_wakes_on_worker_idle_from_slots_snapshot(world):
    svc, graph = world.svc, world.graph

    async def scenario():
        waiter = asyncio.create_task(svc.handle({"id": 1, "op": "bus_wait", "identity": ORCH, "args": {"timeout_secs": 5, "targets": ["worker-1"]}}))
        await asyncio.sleep(0.05)
        graph.on_slots([{**s, "running": False} if s["key"] == "w1" else s for s in slots()])
        res = await asyncio.wait_for(waiter, timeout=2)
        assert [e["type"] for e in res["result"]["events"]] == ["idle"] and res["result"]["events"][0]["nick"] == "worker-1"

    run(scenario())


def test_record_send_and_report_rows_are_logged_as_delivered(world):
    svc, store = world.svc, world.store
    m = svc.record_send(ORCH, "w1", "please port billing first")
    assert m is not None and m.kind == "send" and m.to == "worker-1" and m.delivered is False
    assert store.messages(ORCH)[-1].delivered is True
    assert svc.record_send("dashboard:other", "w1", "x") is None
    r = svc.record_report(W1, "blocked", "need the queue type")
    assert r.kind == "report" and r.to == "orchestrator"
    assert svc.record_report(ORCH, "done", "hub cannot report") is None


def test_run_goal_and_close(world):
    svc = world.svc
    res = run(svc.handle({"id": 1, "op": "bus_run", "identity": ORCH, "args": {"action": "goal", "goal": "port billing to rust"}}))
    assert res["result"]["goal"] == "port billing to rust"
    res = run(svc.handle({"id": 2, "op": "bus_run", "identity": ORCH, "args": {"action": "close"}}))
    assert res["result"]["closed"] is True
    res = run(svc.handle({"id": 3, "op": "bus_post", "identity": W1, "args": {"to": "orchestrator", "text": "late"}}))
    assert res["code"] == "run_closed"


def test_gone_target_is_refused(world):
    svc, graph = world.svc, world.graph
    graph.on_slots(slots(gone=("w2",)))
    res = run(svc.handle({"id": 1, "op": "bus_post", "identity": ORCH, "args": {"to": "worker-2", "text": "there?"}}))
    assert res["code"] == "target_gone"
