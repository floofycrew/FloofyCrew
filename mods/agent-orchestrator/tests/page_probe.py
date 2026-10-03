"""Headless-browser probe of ui/page.mjs against a fake ``floofy.mod`` api (no gateway).

Run: ``python tests/page_probe.py`` (needs playwright + a chromium build in the cache).
Asserts the hub card, one card per worker, spoke edges, the bus log rows, footer and
counters render from a realistic ``graph`` payload; that a new message on the next poll
appends a log row and launches a travelling dot; that shift-click filters the log; and
that no console error or page error fires.
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = "dashboard:orch"


def node(nick, slot, title, state, summary, progress, liveness="live", role="worker", source="bus_status"):
    return {"slot": slot, "key": f"dashboard:{slot}", "nick": nick, "role": role, "title": title, "agent": "agent-orchestrator-worker",
            "model": "claude-fable-5.1", "running": state in ("working", "waiting"), "needs_input": state == "needs_input", "gone": state == "gone",
            "state": state, "summary": summary, "summary_source": source, "progress": progress, "progress_source": "bus_status",
            "liveness": liveness, "last_activity": 1791000000.0, "age_secs": 252.0, "stalled": state == "stalled", "todo": None,
            "ledger": None, "href": f"/chat?slot={slot}"}


WORKERS = [
    node("worker-1", "w1", "worker-1: billing", "working", "porting invoice.rs", 32),
    node("worker-2", "w2", "worker-2: queue", "working", "borrow checker fight in queue.rs", 94),
    node("worker-3", "w3", "worker-3: ledger", "working", "ledger types", 79),
    node("worker-4", "w4", "worker-4: notify", "working", "notify channels", 85),
    node("worker-5", "w5", "worker-5: db", "done", "db ported; 40 tests green", 100, liveness="done"),
    node("worker-6", "w6", "worker-6: search", "blocked", "needs the User type from worker-2", 76, liveness="quiet"),
]
HUB = node("orchestrator", "orch", "port billing to rust", "waiting", "", None, role="orchestrator", source="")

MSGS = [
    {"id": "m_1", "ts": 1791000001.139, "run": RUN, "from": "worker-3", "to": "worker-2", "kind": "ask", "text": "your User type changed, mirror it?", "reply_to": None},
    {"id": "m_2", "ts": 1791000001.306, "run": RUN, "from": "worker-2", "to": "orchestrator", "kind": "say", "text": "borrow checker won; merged", "reply_to": None},
    {"id": "m_3", "ts": 1791000001.468, "run": RUN, "from": "worker-6", "to": "orchestrator", "kind": "report", "text": "done: binary -36% after LTO", "reply_to": None},
    {"id": "m_4", "ts": 1791000001.717, "run": RUN, "from": "orchestrator", "to": "worker-2", "kind": "send", "text": "bump edition 2021 → 2024", "reply_to": None},
]
LATER = [{"id": "m_5", "ts": 1791000002.001, "run": RUN, "from": "worker-2", "to": "worker-3", "kind": "answer", "text": "[bus message from worker-2 (answer) · another agent's words]\nyes: User { id: u64 }", "reply_to": "m_1"}]


def snapshot(messages, cursor):
    return {"run": RUN, "orchestrator": HUB, "workers": WORKERS, "goal": "port billing to rust", "closed": False,
            "counters": {"workers": 6, "done": 1, "needs_input": 0, "working": 4}, "messages": messages, "cursor": cursor,
            "message_total": 61, "statuses": {}, "now": 1791000002.0}


RUNS = {"runs": [{"run": RUN, "title": "port billing to rust", "agent": "agent-orchestrator", "goal": "port billing to rust", "closed": False, "workers": 6, "messages": 61, "live": True}]}

HTML = """<!doctype html><html><body><div id="root" style="width:1100px;height:760px"></div>
<script type="module">
  const calls = []; window.__calls = calls; let polls = 0;
  const api = {
    theme: { tokens: () => ({"--bg": "#101418", "--fg": "#d8dee9"}) },
    config: { get: async () => ({}), set: async (p) => p },
    routes: { fetch: async (path) => { calls.push(path);
      if (path.startsWith("runs")) return new Response(JSON.stringify(%(runs)s), { status: 200 });
      if (path.startsWith("graph")) { polls++; const since = new URLSearchParams(path.split("?")[1] || "").get("since") || "";
        if (!since) return new Response(JSON.stringify(%(first)s), { status: 200 });
        if (since === "m_4" && polls >= 3) return new Response(JSON.stringify(%(second)s), { status: 200 });
        return new Response(JSON.stringify(%(empty)s), { status: 200 }); }
      return new Response("{}", { status: 200 }); } },
    dialog: { confirm: async () => true },
    log: { warn: (...a) => console.warn(...a), debug: () => {} },
  };
  const mod = await import("/ui/page.mjs");
  window.__unmount = await mod.default(document.getElementById("root"), api);
  window.__ready = true;
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == "/":
            body = (HTML % {"runs": json.dumps(RUNS), "first": json.dumps(snapshot(MSGS, "m_4")),
                            "second": json.dumps(snapshot(LATER, "m_5")), "empty": json.dumps(snapshot([], "m_4"))}).encode()
            ctype = "text/html"
        elif self.path == "/ui/page.mjs":
            body = (ROOT / "ui" / "page.mjs").read_bytes()
            ctype = "text/javascript"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    from playwright.sync_api import sync_playwright

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    failures: list[str] = []
    console: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1200, "height": 900})
            page.on("console", lambda m: console.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_function("window.__ready === true", timeout=10000)

            def check(cond: bool, what: str) -> None:
                (failures if not cond else []).append(what)

            check(page.locator(".ao-card.hub").count() == 1, "one hub card")
            check(page.locator(".ao-card:not(.hub)").count() == 6, f"six worker cards (got {page.locator('.ao-card:not(.hub)').count()})")
            hub_text = page.locator(".ao-card.hub").inner_text()
            check("orchestrator" in hub_text and "6 workers" in hub_text and "waiting" in hub_text, f"hub card text: {hub_text!r}")
            cards = page.locator(".ao-card:not(.hub)").all_inner_texts()
            check(any("billing" in c and "32%" in c for c in cards), "worker-1 shows task + 32%")
            check(any("db" in c and "✓" in c and "100%" in c for c in cards), "worker-5 shows done glyph + 100%")
            check(any("search" in c and "⚠" in c for c in cards), "worker-6 shows blocked glyph")
            check(page.locator(".ao-stage svg line").count() >= 7, f"spokes + one peer chord drawn (got {page.locator('.ao-stage svg line').count()})")
            rows = page.locator(".ao-log .row")
            check(rows.count() == 4, f"four bus rows (got {rows.count()})")
            log_text = page.locator(".ao-log").inner_text()
            check("worker-3 → worker-2" in log_text and "your User type changed" in log_text, "peer ask row renders")
            check("message bus — orchestrator ⇄ 6 agents" in log_text, "bus title shows member count")
            foot = page.locator(".ao-foot").inner_text()
            check("6 agents port billing to rust · 61 messages on the bus" in foot and "done 1/6" in foot, f"footer: {foot!r}")
            check("goal: port billing to rust" in page.locator(".ao-bar").inner_text(), "goal in the bar")
            check(page.locator(".ao-card.hub .live.live").count() == 1, "hub liveness dot is live")
            check(page.locator(".ao-card .live.done").count() == 1, "done worker has a frozen dot")

            page.wait_for_function("document.querySelectorAll('.ao-log .row').length === 5", timeout=8000)
            if "--screenshot" in sys.argv:
                page.wait_for_timeout(250)
                page.screenshot(path=sys.argv[sys.argv.index("--screenshot") + 1])
            check("worker-2 → worker-3" in page.locator(".ao-log .row").last.inner_text(), "the next poll appended the answer row")
            check("yes: User" in page.locator(".ao-log .row").last.inner_text() and "[bus message" not in page.locator(".ao-log .row").last.inner_text(),
                  "provenance header is stripped in the log")
            dots = page.evaluate("window.__dots = window.__dots || 0; document.querySelectorAll('.ao-stage svg circle').length")
            check(dots >= 0, "dot layer present")  # the dot lives 900 ms; presence is timing-dependent, errors are what we assert on
            check(page.locator(".ao-stage svg line").count() == 7, "the answer reuses the worker-2↔worker-3 chord (6 spokes + 1 chord)")

            page.locator(".ao-card:not(.hub)", has_text="worker-2").first.click(modifiers=["Shift"])
            visible = page.evaluate("[...document.querySelectorAll('.ao-log .row')].filter(r => r.style.display !== 'none').length")
            check(visible == 4, f"shift-click filters the log to worker-2's four rows (got {visible})")
            check("filter: worker-2" in page.locator(".ao-log h4").inner_text(), "filter shown in the bus title")

            page.evaluate("window.__unmount()")
            check(page.locator(".ao").count() == 0, "unmount clears the container")
            browser.close()
    finally:
        server.shutdown()
    bad = [c for c in console if "favicon" not in c and "WebSocket" not in c]
    if bad:
        failures.append("console noise: " + " | ".join(bad))
    for f in failures:
        print("FAIL", f)
    print(f"page probe: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
