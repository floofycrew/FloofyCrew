#!/usr/bin/env python3
"""live-probe.py -- is the installed bus alive on THIS host? Read-only.

Starts the mod's own stdio MCP server exactly as the rendered worker spec does, runs the MCP handshake,
lists the tools and calls bus_roster with a session key that belongs to no run. Expected: six tools and
a clean ``not_in_run`` refusal -- which proves the whole path (spec -> server -> unix socket -> gateway
service -> membership check) is wired without creating any session or row.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys


def main() -> int:
    spec_path = os.path.expanduser("~/.kiro/agents/agent-orchestrator-worker.json")
    spec = json.load(open(spec_path, encoding="utf-8"))
    srv = spec["mcpServers"]["agent-orchestrator-bus"]
    env = {**os.environ, **srv.get("env", {}), "KIROCREW_SESSION_KEY": "dashboard:live-probe-not-a-run"}
    proc = subprocess.Popen([srv["command"], *srv["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env)

    def rpc(i, method, params=None):
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}}) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        return json.loads(line) if line else {"error": "no reply"}

    ok = True
    init = rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "live-probe", "version": "0"}})
    print("initialize:", init.get("result", {}).get("serverInfo") or init)
    tools = rpc(2, "tools/list")
    names = sorted(t["name"] for t in tools.get("result", {}).get("tools", []))
    print("tools:", names)
    ok &= names == ["bus_inbox", "bus_post", "bus_roster", "bus_run", "bus_status", "bus_wait"]
    call = rpc(3, "tools/call", {"name": "bus_roster", "arguments": {}})
    content = call.get("result", {}).get("content", [{}])[0].get("text", "")
    try:
        body = json.loads(content)
    except ValueError:
        body = {"raw": content}
    print("bus_roster (probe key, not in any run):", body)
    ok &= body.get("code") == "not_in_run"
    proc.stdin.close()
    proc.wait(timeout=5)
    err = proc.stderr.read().strip()
    if err:
        print("server stderr:", err[-400:])
    print("LIVE-OK" if ok else "LIVE-FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
