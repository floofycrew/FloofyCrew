"""The stdio MCP server end to end: JSON-RPC on stdin/stdout, identity + args forwarded over the unix socket."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "mcp" / "server.py"


class FakeBus(threading.Thread):
    """A unix-socket echo of what the mod's SocketServer would do, recording every request."""

    def __init__(self, path: Path):
        super().__init__(daemon=True)
        self.path = path
        self.requests: list[dict] = []
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()

    def run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main())

    async def _main(self):
        server = await asyncio.start_unix_server(self._serve, path=str(self.path))
        self.ready.set()
        async with server:
            await self._stop_evt().wait()

    def _stop_evt(self):
        if not hasattr(self, "_halt"):  # not ``_stop``: threading.Thread owns that name
            self._halt = asyncio.Event()
        return self._halt

    async def _serve(self, reader, writer):
        line = await reader.readline()
        req = json.loads(line)
        self.requests.append(req)
        if req["op"] == "bus_post" and req["args"].get("text") == "boom":
            reply = {"id": req["id"], "ok": False, "error": "message_too_long: do not retry", "code": "message_too_long", "retryable": False}
        elif req["op"] == "bus_inbox" and req["args"].get("wait_secs"):
            await asyncio.sleep(0.3)  # a long-poll that returns late but inside the client's budget
            reply = {"id": req["id"], "ok": True, "result": {"messages": [], "count": 0, "timed_out": True}}
        else:
            reply = {"id": req["id"], "ok": True, "result": {"echo": req["op"], "identity": req["identity"], "args": req["args"]}}
        writer.write((json.dumps(reply) + "\n").encode())
        await writer.drain()
        writer.close()

    def stop(self):
        self.loop.call_soon_threadsafe(self._stop_evt().set)


@pytest.fixture
def short_tmp():
    """AF_UNIX paths are capped (~104 bytes); pytest's tmp_path is often longer than that."""
    d = tempfile.mkdtemp(prefix="ao-", dir="/tmp")
    yield Path(d)
    for f in Path(d).iterdir():
        f.unlink()
    Path(d).rmdir()


def rpc(proc, msg):
    proc.stdin.write((json.dumps(msg) + "\n").encode())
    proc.stdin.flush()
    line = proc.stdout.readline()
    return json.loads(line) if line else None


def test_mcp_server_handshake_tools_and_forwarding(short_tmp):
    sock = short_tmp / "bus.sock"
    bus = FakeBus(sock)
    bus.start()
    assert bus.ready.wait(5)
    env = {**os.environ, "KIROCREW_SESSION_KEY": "dashboard:w7", "AGENT_ORCHESTRATOR_SOCK": str(sock)}
    proc = subprocess.Popen([sys.executable, str(SERVER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    try:
        init = rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}})
        assert init["result"]["serverInfo"]["name"] == "agent-orchestrator-bus" and "tools" in init["result"]["capabilities"]
        proc.stdin.write((json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n").encode()); proc.stdin.flush()
        tools = rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [t["name"] for t in tools["result"]["tools"]]
        assert names == ["bus_roster", "bus_post", "bus_inbox", "bus_status", "bus_wait", "bus_run"]
        for t in tools["result"]["tools"]:
            assert t["inputSchema"]["type"] == "object" and t["description"]
        call = rpc(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "bus_post", "arguments": {"to": "worker-2", "text": "hi", "kind": "ask"}}})
        assert call["result"]["isError"] is False
        payload = json.loads(call["result"]["content"][0]["text"])
        assert payload == {"echo": "bus_post", "identity": "dashboard:w7", "args": {"to": "worker-2", "text": "hi", "kind": "ask"}}
        assert bus.requests[-1]["identity"] == "dashboard:w7"  # identity comes from the environment, never from the model
        err = rpc(proc, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "bus_post", "arguments": {"to": "x", "text": "boom"}}})
        assert err["result"]["isError"] is True
        body = json.loads(err["result"]["content"][0]["text"])
        assert body["code"] == "message_too_long" and body["retryable"] is False
        wait = rpc(proc, {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "bus_inbox", "arguments": {"wait_secs": 1}}})
        assert json.loads(wait["result"]["content"][0]["text"])["timed_out"] is True
        unknown = rpc(proc, {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "nope", "arguments": {}}})
        assert unknown["error"]["code"] == -32602
        ping = rpc(proc, {"jsonrpc": "2.0", "id": 7, "method": "ping"})
        assert ping["result"] == {}
    finally:
        proc.stdin.close()
        proc.wait(timeout=5)
        bus.stop()


def test_mcp_server_reports_bus_down_when_socket_missing(short_tmp):
    env = {**os.environ, "KIROCREW_SESSION_KEY": "dashboard:w1", "AGENT_ORCHESTRATOR_SOCK": str(short_tmp / "none.sock")}
    proc = subprocess.Popen([sys.executable, str(SERVER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    try:
        res = rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "bus_roster", "arguments": {}}})
        body = json.loads(res["result"]["content"][0]["text"])
        assert res["result"]["isError"] and body["code"] == "bus_down" and "mod active" in body["error"]
    finally:
        proc.stdin.close()
        proc.wait(timeout=5)
