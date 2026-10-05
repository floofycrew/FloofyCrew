"""Headless Chromium probe of ``ui/page.mjs`` — END TO END through the real seams.

Run: ``python tests/page_probe.py [--screenshot out.png]`` (needs playwright + a chromium build in the cache).

What is real here (nothing between the page and the data is stubbed):

* the page is mounted with the real ``createModApi`` from ``spa-host/src/modapi.mjs`` (so ``routes.url`` /
  ``routes.fetch`` behave exactly as in the dashboard — including percent-encoding every path segment);
* ``/api/apps/floofycrew/mods/agent-orchestrator/api/...`` is answered by the real Loader
  ``ModRouteRegistry.dispatch`` (``floofy_loader.modroutes``) with the query parsed the way aiohttp hands it over;
* the routes are the real hook's, fed by the real reducer and bus service: a run is seeded through the host taps
  (``_broadcast`` slots snapshot, ``session_send`` / ``work_report`` after-hooks) and the bus ops
  (``bus_run`` goal, ``bus_status`` lines, ``bus_post`` asks/answers relayed hub-and-spoke).

The first version of this probe faked ``api.routes.fetch`` and parsed the query itself, which hid the one bug that
mattered on the live host: ``routes.fetch("graph?run=…")`` encodes the ``?`` into the path segment, the Loader
rejects the segment with a 404 and the page sat on its empty state for a run it had just listed. This probe would
have failed on that page; it asserts the graph request reaches the server as ``…/api/graph?run=…``.

Asserts the hub card, one card per worker, six spokes and NO peer chord (hub-and-spoke is the default), the bus
log rows (relayed ask → answer chain, the host's send and report rows), footer and counters; that a message
posted after the first render appends a row on the next poll; that shift-click filters the log; that a
worker → worker post is refused by the service; that the host's ``--text`` token is honoured (light theme); and
that no console error or page error fires.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
sys.path.insert(0, str(REPO / "loader-app"))
from floofy_loader.modroutes import ModRouteRegistry  # noqa: E402  - the real Loader dispatch

MOD = "agent-orchestrator"
API = f"/api/apps/floofycrew/mods/{MOD}/api/"
RUN = "dashboard:orch"
GOAL = "port billing to rust"

# ---------------------------------------------------------------------------------------------------------------
# the real hook on a real route registry, seeded through the host taps and the bus ops


class _Log:
    def __init__(self):
        self.lines: list[str] = []

    def _w(self, level, msg, *args, **kw):
        self.lines.append(f"{level}: {msg % args if args else msg}")

    info = lambda self, *a, **k: self._w("info", *a, **k)
    warning = lambda self, *a, **k: self._w("warning", *a, **k)
    error = lambda self, *a, **k: self._w("error", *a, **k)
    debug = lambda self, *a, **k: self._w("debug", *a, **k)


class _Hooks:
    def __init__(self):
        self.regs: list[tuple[str, str, object]] = []

    def before(self, target, fn):
        self.regs.append(("before", target, fn))

    def after(self, target, fn):
        self.regs.append(("after", target, fn))

    def replace(self, target, fn):
        self.regs.append(("replace", target, fn))

    def find(self, kind, target):
        return next(fn for k, t, fn in self.regs if k == kind and t == target)


class _Cfg(dict):
    def to_dict(self):
        return dict(self)


class _Req:
    def __init__(self, headers=None, body=None):
        self.headers = headers or {}
        self._body = body or {}

    async def json(self):
        return self._body


def load_hook():
    spec = importlib.util.spec_from_file_location("ao_hookpkg", ROOT / "hook" / "__init__.py", submodule_search_locations=[str(ROOT / "hook")])
    module = importlib.util.module_from_spec(spec)
    sys.modules["ao_hookpkg"] = module
    spec.loader.exec_module(module)
    return module


def slots():
    titles = ["billing", "queue", "ledger", "notify", "db", "search"]
    out = [{"key": "orch", "title": GOAL, "agent": "agent-orchestrator", "model": "claude-fable-5.1", "running": True, "created_by": ""}]
    for i, t in enumerate(titles):
        out.append({"key": f"w{i}", "title": f"worker-{i + 1}: {t}", "agent": "agent-orchestrator-worker", "model": "claude-fable-5.1",
                    "running": True, "created_by": RUN})
    return out


class Seam:
    """The hook activated on a background event loop; ``op()`` runs a bus op on it; ``dispatch()`` is the Loader's."""

    def __init__(self, tmp: Path):
        os.environ["KIRO_HOME"] = str(tmp / "kiro")  # the rendered agent specs land here, never in ~/.kiro
        (tmp / "kiro" / "agents").mkdir(parents=True)
        (tmp / "data").mkdir()
        self.registry = ModRouteRegistry()
        self.log = _Log()
        self.ctx = types.SimpleNamespace(
            mod_id=MOD, version="0.2.0", mod_dir=str(ROOT), data_dir=str(tmp / "data"),
            host=types.SimpleNamespace(version="0.8.0.6", interpreter=sys.executable),
            log=self.log, config=_Cfg({}), hooks=_Hooks(), routes=self.registry.for_mod(MOD), state={},
        )
        self.mod = load_hook()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.run(self._activate())
        self.paths: list[str] = []  # every mod-api request path the page made, as the server saw it

    async def _activate(self):
        self.mod.activate(self.ctx)
        await asyncio.sleep(0.05)

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=10)

    def op(self, name: str, identity: str, **args):
        return self.run(getattr(self.mod._service, self.mod._service.OPS[name])(identity, args))

    def seed(self):
        self.ctx.hooks.find("before", "kiro_crew.dashboard.state:DashboardState._broadcast")(object(), {"_type": "slots", "_slots_list": slots()})
        self.op("bus_run", RUN, action="goal", goal=GOAL)
        for nick, text, pct, st in [("worker-1", "porting invoice.rs", 32, "working"), ("worker-2", "borrow checker fight in queue.rs", 94, "working"),
                                    ("worker-3", "ledger types", 79, "working"), ("worker-4", "notify channels", 85, "working"),
                                    ("worker-6", "needs the User type from worker-2", 76, "blocked")]:
            self.op("bus_status", f"dashboard:w{int(nick[-1]) - 1}", text=text, progress=pct, state=st)
        # hub-and-spoke: worker-3's question goes UP, the orchestrator relays it and relays the answer back
        m1 = self.op("bus_post", "dashboard:w2", to="orchestrator", kind="ask", text="your User type changed, mirror it?")
        m2 = self.op("bus_post", RUN, to="worker-2", kind="ask", text="worker-3 asks: did your User type change? current shape?")
        self.op("bus_post", "dashboard:w1", to="orchestrator", kind="answer", text="yes: User { id: u64 }", reply_to=m2["id"])
        self.op("bus_post", RUN, to="worker-3", kind="answer", text="from worker-2: User { id: u64 }", reply_to=m1["id"])
        send = self.ctx.hooks.find("after", "route:POST /api/session-control/send")
        report = self.ctx.hooks.find("after", "route:POST /api/work-ledger/report")
        ok = types.SimpleNamespace(status=200)
        self.run(send(ok, _Req({"X-Session-Key": RUN}, {"target": "dashboard:w0", "message": "bump edition 2021 → 2024"})))
        self.run(report(ok, _Req({"X-Session-Key": "dashboard:w4"}, {"status": "done", "summary": "db ported; 40 tests green"})))

    def refused_peer_post(self) -> str:
        try:
            self.op("bus_post", "dashboard:w2", to="worker-2", kind="ask", text="smuggled")
        except Exception as exc:  # noqa: BLE001 - BusError from the hook package
            return getattr(exc, "code", "") or str(exc)
        return ""

    def dispatch(self, method: str, segments: list[str], query: dict[str, list[str]], body: bytes = b""):
        return self.registry.dispatch_sync(MOD, method, "/".join(segments), query=query, body=body)

    def close_every_tab(self):
        """The slots push after the user closed (or deleted) every chat of the run: the reducer marks them gone."""
        self.ctx.hooks.find("before", "kiro_crew.dashboard.state:DashboardState._broadcast")(object(), {"_type": "slots", "_slots_list": []})

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)


# ---------------------------------------------------------------------------------------------------------------
# the document: a light host theme (the user's screenshot), the real modapi, the page

HTML = """<!doctype html><html><head><style>
:root{--bg:#ffffff;--bg-elevated:#f4f6f8;--card:#ffffff;--text:#111111;--text-muted:#5b6672;--accent:#2563eb;--border:#d7dde3}
body{margin:0;background:var(--bg)}
</style></head><body><div id="root" style="width:1100px;height:760px"></div>
<script type="module">
  const { createModApi } = await import("/spa/modapi.mjs");
  const api = createModApi("agent-orchestrator", { state: async () => ({}) });
  const mod = await import("/ui/page.mjs");
  window.__unmount = await mod.default(document.getElementById("root"), api);
  window.__ready = true;
</script></body></html>"""


def make_handler(seam: Seam):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _send(self, status: int, body: bytes, ctype: str, headers: dict[str, str] | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                if k.lower() not in ("content-type", "content-length"):
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == "/":
                return self._send(200, HTML.encode(), "text/html")
            if url.path == "/ui/page.mjs":
                return self._send(200, (ROOT / "ui" / "page.mjs").read_bytes(), "text/javascript")
            if url.path.startswith("/spa/"):
                src = REPO / "spa-host" / "src" / url.path[len("/spa/"):]
                if src.is_file():
                    return self._send(200, src.read_bytes(), "text/javascript")
            if url.path.startswith(API):
                seam.paths.append(self.path)
                # aiohttp hands match_info segments percent-DECODED and the query as a multidict
                segments = [unquote(s) for s in url.path[len(API):].split("/") if s]
                query = parse_qs(url.query, keep_blank_values=True)
                status, headers, payload = seam.dispatch("GET", segments, query)
                return self._send(status, payload, headers.get("Content-Type", "application/json"), headers)
            self._send(404, b"", "text/plain")

        def do_POST(self):
            url = urlsplit(self.path)
            if url.path.startswith(API):
                seam.paths.append(f"POST {self.path}")
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                segments = [unquote(s) for s in url.path[len(API):].split("/") if s]
                status, headers, payload = seam.dispatch("POST", segments, parse_qs(url.query, keep_blank_values=True), body)
                return self._send(status, payload, headers.get("Content-Type", "application/json"), headers)
            self._send(404, b"", "text/plain")

    return Handler


def main() -> int:
    from playwright.sync_api import sync_playwright

    tmp = Path(tempfile.mkdtemp(prefix="ao-probe-"))
    seam = Seam(tmp)
    seam.seed()
    failures: list[str] = []
    console: list[str] = []

    def check(cond: bool, what: str) -> None:
        if not cond:
            failures.append(what)

    check(seam.refused_peer_post() == "peer_messaging_off", "a worker → worker post is refused by the real service (hub-and-spoke default)")

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(seam))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1200, "height": 900})
            page.on("console", lambda m: console.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_function("window.__ready === true", timeout=10000)
            page.wait_for_function("document.querySelectorAll('.ao-card').length >= 7", timeout=8000)

            graph_paths = [x for x in seam.paths if "/api/graph" in x]
            check(any(x.startswith(f"{API}graph?run=") for x in graph_paths), f"the graph request carries its query AFTER the path segment (saw {graph_paths[:2]})")
            check(any(x == f"{API}runs" for x in seam.paths), "the runs route is requested bare")

            check(page.locator(".ao-card.hub").count() == 1, "one hub card")
            check(page.locator(".ao-card:not(.hub)").count() == 6, f"six worker cards (got {page.locator('.ao-card:not(.hub)').count()})")
            hub_text = page.locator(".ao-card.hub").inner_text()
            check("orchestrator" in hub_text and "6 workers" in hub_text and "leading" in hub_text, f"hub card text: {hub_text!r}")
            cards = page.locator(".ao-card:not(.hub)").all_inner_texts()
            check(any("billing" in c and "32%" in c for c in cards), "worker-1 shows task + 32% (bus_status)")
            check(any("db" in c and "✓" in c and "100%" in c for c in cards), "worker-5 shows done glyph + 100% (work_report)")
            check(any("search" in c and "⚠" in c for c in cards), "worker-6 shows the blocked glyph")
            lines = page.locator(".ao-stage svg line").count()
            check(lines == 6, f"six spokes and no peer chord (got {lines})")
            rows = page.locator(".ao-log .row")
            check(rows.count() == 6, f"six bus rows (got {rows.count()})")
            log_text = page.locator(".ao-log").inner_text()
            check("worker-3 → orchestrator" in log_text and "your User type changed" in log_text, "the worker's ask went UP to the orchestrator")
            check("orchestrator → worker-2" in log_text and "orchestrator → worker-3" in log_text, "the orchestrator relayed the ask and the answer")
            check("worker-2 → worker-3" not in log_text and "worker-3 → worker-2" not in log_text, "no worker → worker row")
            check("orchestrator → worker-1" in log_text and "bump edition" in log_text, "the host's session_send shows as a send row")
            check("worker-5 → orchestrator" in log_text and "db ported" in log_text, "the host's work_report shows as a report row")
            check("message bus — orchestrator ⇄ 6 agents" in log_text, "bus title shows the member count")
            foot = page.locator(".ao-foot").inner_text()
            check(f"6 agents {GOAL} · 6 messages on the bus" in foot and "done 1/6" in foot, f"footer: {foot!r}")
            check(f"goal: {GOAL}" in page.locator(".ao-bar").inner_text(), "goal in the bar")
            check("● live" in page.locator(".ao-live").inner_text(), f"live indicator: {page.locator('.ao-live').inner_text()!r}")
            check(page.locator(".ao-card.hub .live.live").count() == 1, "hub liveness dot is live")
            check(page.locator(".ao-card .live.done").count() == 1, "the done worker has a frozen dot")
            colour = page.evaluate("getComputedStyle(document.querySelector('.ao')).color")
            check(colour == "rgb(17, 17, 17)", f"the host's --text token is honoured on the light theme (got {colour})")

            # a message after the first render: the next poll appends it (and only it)
            seam.op("bus_post", "dashboard:w5", to="orchestrator", kind="say", text="unblocked, resuming search")
            page.wait_for_function("document.querySelectorAll('.ao-log .row').length === 7", timeout=8000)
            if "--screenshot" in sys.argv:
                page.wait_for_timeout(250)
                page.screenshot(path=sys.argv[sys.argv.index("--screenshot") + 1])
            last = page.locator(".ao-log .row").last.inner_text()
            check("worker-6 → orchestrator" in last and "unblocked" in last, f"the next poll appended the new row: {last!r}")
            check("[bus message" not in last, "provenance header is never shown in the log")
            check(page.locator(".ao-stage svg line").count() == 6, "still six spokes after the new message")
            check("7 messages on the bus" in page.locator(".ao-foot").inner_text(), "footer counter follows the bus")

            page.locator(".ao-card:not(.hub)", has_text="worker-2").first.click(modifiers=["Shift"])
            visible = page.evaluate("[...document.querySelectorAll('.ao-log .row')].filter(r => r.style.display !== 'none').length")
            check(visible == 2, f"shift-click filters the log to worker-2's two rows (got {visible})")
            check("filter: worker-2" in page.locator(".ao-log h4").inner_text(), "filter shown in the bus title")

            # --- removing a run: never while a tab is open; the ✕ appears once every chat of the run is closed
            check(page.locator("[data-testid=ao-remove]").is_hidden(), "no remove button while the run has open tabs")
            seam.close_every_tab()
            page.wait_for_function("!document.querySelector('[data-testid=ao-remove]').hidden", timeout=8000)
            check("archived (chats gone)" in page.locator(".ao-live").inner_text(), f"status after the tabs closed: {page.locator('.ao-live').inner_text()!r}")
            check(page.locator(".ao-card").count() == 7, "the remembered run is still drawn (archived) until removed")
            page.locator("[data-testid=ao-remove]").click()
            page.wait_for_selector("[data-testid=floofycrew-mod-dialog-ok]", timeout=5000)
            check("Remove" in page.locator("[data-testid=floofycrew-mod-dialog-ok]").inner_text(), "the in-document confirm offers Remove (never window.confirm)")
            page.locator("[data-testid=floofycrew-mod-dialog-cancel]").click()
            page.wait_for_timeout(300)
            check(seam.mod._store.meta(RUN) is not None and page.locator(".ao-card").count() == 7, "Keep leaves the run alone")
            page.locator("[data-testid=ao-remove]").click()
            page.wait_for_selector("[data-testid=floofycrew-mod-dialog-ok]", timeout=5000)
            page.locator("[data-testid=floofycrew-mod-dialog-ok]").click()
            page.wait_for_function("document.querySelectorAll('.ao-card').length === 0", timeout=8000)
            check(any(x.startswith(f"POST {API}runs/remove") for x in seam.paths), "the page posted runs/remove through the real mod api")
            check(seam.mod._store.meta(RUN) is None and not seam.mod._store.run_dir(RUN).exists(), "the run's record is gone from disk")
            check(seam.mod._graph.runs() == [], "the reducer forgot the run")
            check(page.locator(".ao-empty").count() == 1 and "No run on screen" in page.locator(".ao-empty").inner_text(), "the page falls back to the empty state")
            check("no orchestration runs yet" in page.locator(".ao-bar select").inner_text(), "the picker is empty")
            check(page.locator("[data-testid=ao-remove]").is_hidden(), "the remove button hides again")

            page.evaluate("window.__unmount()")
            check(page.locator(".ao").count() == 0, "unmount clears the container")
            browser.close()
    finally:
        server.shutdown()
        seam.close()
    bad = [c for c in console if "favicon" not in c and "WebSocket" not in c]
    if bad:
        failures.append("console noise: " + " | ".join(bad))
    hook_errors = [l for l in seam.log.lines if l.startswith("error:")]
    if hook_errors:
        failures.append("hook errors: " + " | ".join(hook_errors))
    for f in failures:
        print("FAIL", f)
    print(f"page probe: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
