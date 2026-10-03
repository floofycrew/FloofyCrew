"""Headless-browser probe of ui/page.mjs against a stateful fake ``floofy.mod`` api (no gateway).

Run: ``python tests/page_probe.py`` (needs playwright + a chromium build in the cache).

The fake api is the Loader's contract in miniature: ``config.get/set`` hit a
server-side document (``set`` merges top-level keys, ``null`` deletes — what
``PUT /mods/{id}/config`` does) and the ``status`` route is computed from that
document with the real ``hook/policy.py`` (``merged_config`` + ``describe``),
so the page is driven by what the hook would actually read back. The sequence
is the bug report: pick a mechanism, toggle the policy off and on, set a count,
create a profile, reload — every selection must show and survive.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_policy():
    spec = importlib.util.spec_from_file_location("mrp_policy_probe", ROOT / "hook" / "policy.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


P = _load_policy()

STORE: dict = {}
LOCK = threading.Lock()

HTML = """<!doctype html><html><body><div id="root"></div>
<script type="module">
  const calls = [];
  window.__calls = calls;
  const json = async (r) => r.json();
  const api = {
    theme: { tokens: () => ({}) },
    config: {
      get: async () => json(await fetch("/fake/config")),
      set: async (p) => { calls.push(["config.set", p]); return json(await fetch("/fake/config", { method: "PUT", body: JSON.stringify(p) })); },
    },
    routes: { fetch: async (path, init) => { calls.push(["route", path]); return fetch("/fake/route/" + path, init); } },
    dialog: {
      confirm: async () => true,
      prompt: async () => null,
      form: async ({ title }) => title.startsWith("New fallback profile") ? { name: "Night shift", primary_retries: "4" } : { model: "claude-sonnet-5", retries: "2" },
    },
    log: { warn: (...a) => console.warn(...a) },
  };
  const mod = await import("/ui/page.mjs");
  window.__unmount = await mod.default(document.getElementById("root"), api);
  window.__ready = true;
</script></body></html>"""


def _status() -> dict:
    with LOCK:
        cfg = P.merged_config(json.loads(json.dumps(STORE)))
    return {
        "config": cfg, "effective": P.describe(cfg),
        "host": {"retries": P.HOST_RETRIES, "delay_base_s": P.HOST_DELAY_BASE, "candidate_attempts": P.HOST_CANDIDATE_ATTEMPTS, "version": "probe"},
        "budget_overrides": [], "hooks": 0, "presets": list(P.PRESETS), "infinite": P.INFINITE, "counters": {}, "recent": [],
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):  # quiet
        pass

    def _send(self, body: bytes, ctype: str = "application/json", status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self._send(HTML.encode(), "text/html")
        elif self.path == "/ui/page.mjs":
            self._send((ROOT / "ui" / "page.mjs").read_bytes(), "text/javascript")
        elif self.path == "/fake/config":
            with LOCK:
                self._send(json.dumps(STORE).encode())
        elif self.path == "/api/models":   # the dashboard's own model list, fetched same-origin by the page
            self._send(json.dumps({"models": [{"model_id": "claude-sonnet-5"}, {"model_id": "gpt-5.6-sol"}]}).encode())
        elif self.path == "/fake/route/status":
            self._send(json.dumps(_status()).encode())
        else:
            self._send(b"not found", "text/plain", 404)

    def do_PUT(self):
        if self.path != "/fake/config":
            return self._send(b"not found", "text/plain", 404)
        patch = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0")) or 0) or b"{}")
        with LOCK:
            for key, value in patch.items():   # the Loader's PUT: merge key by key, null deletes
                if value is None:
                    STORE.pop(key, None)
                else:
                    STORE[key] = value
            self._send(json.dumps(STORE).encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        self.rfile.read(length)
        if self.path == "/fake/route/preview":
            self._send(json.dumps({"timeline": [], "effective": _status()["effective"]}).encode())
        elif self.path in ("/fake/route/apply", "/fake/route/reset"):
            self._send(b'{"ok": true}')
        else:
            self._send(b"not found", "text/plain", 404)


def main() -> int:
    from playwright.sync_api import sync_playwright

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    failures: list[str] = []
    console: list[str] = []
    total = 0

    def check(cond: bool, what: str) -> None:
        nonlocal total
        total += 1
        if not cond:
            failures.append(what)

    def selected(page) -> str:
        return page.locator('[data-testid="mrp-mechanism"] [aria-checked="true"]').get_attribute("data-mechanism") or ""

    def wait_selected(page, name: str) -> None:
        page.wait_for_function(
            "name => document.querySelector('[data-testid=\"mrp-mechanism\"] [aria-checked=\"true\"]')?.dataset.mechanism === name",
            arg=name, timeout=5000)

    def stored() -> dict:
        with LOCK:
            return json.loads(json.dumps(STORE))

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.on("console", lambda m: console.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_function("window.__ready === true", timeout=10000)

            check(selected(page) == "host", f"fresh config selects the host card (got {selected(page)!r})")
            check(page.locator('[data-testid="mrp-enabled"]').is_checked(), "policy switch starts on")
            check("Nothing to set" in page.locator('[data-testid="mrp-page"]').inner_text(), "host preset shows no parameters")

            # 1. pick a mechanism: it must show as selected and its parameters must appear
            page.locator('[data-mechanism="infinite_exponential"]').click()
            wait_selected(page, "infinite_exponential")
            check(page.locator('[data-testid="mrp-delay-mode"]').input_value() == "exponential", "delay selector follows the picked mechanism")
            check(page.locator('[data-testid="mrp-delay-base_s"]').is_visible() and page.locator('[data-testid="mrp-delay-max_s"]').is_visible(), "exponential parameters are shown")
            doc = stored()
            check(doc.get("retries", {}).get("mode") == "infinite" and doc.get("delay", {}).get("mode") == "exponential", f"stored document carries the mechanism ({doc})")
            check("enabled" not in doc or isinstance(doc["enabled"], bool), f"enabled is never stored as a non-boolean ({doc.get('enabled')!r})")
            check(page.locator('[data-testid="mrp-note"]').inner_text().startswith("saved"), "the note says saved")

            # 2. switch the policy off and on: off = host card, on = the mechanism comes back
            page.locator('[data-testid="mrp-enabled"]').click()
            wait_selected(page, "host")
            check(not page.locator('[data-testid="mrp-enabled"]').is_checked(), "switch off: checkbox reads off")
            check(stored().get("enabled") is False, f"switch off: stored enabled is boolean false ({stored().get('enabled')!r})")
            check(stored().get("retries", {}).get("mode") == "infinite", "switch off: the chosen mechanism is retained underneath")
            page.locator('[data-testid="mrp-enabled"]').click()
            wait_selected(page, "infinite_exponential")
            check(page.locator('[data-testid="mrp-enabled"]').is_checked(), "switch on: checkbox reads on")
            check(stored().get("enabled") is True, f"switch on: stored enabled is boolean true ({stored().get('enabled')!r})")

            # 3. retry count: the card, then the count field, and the typed value sticks
            page.locator('[data-mechanism="count"]').click()
            wait_selected(page, "count")
            check(page.locator('[data-testid="mrp-retries-count"]').is_visible(), "count preset shows the count field")
            page.fill('[data-testid="mrp-retries-count"]', "7")
            page.locator('[data-testid="mrp-retries-count"]').dispatch_event("change")
            # save() = config.set → apply → refresh(); wait for the refreshed in-force line, not just the write
            page.wait_for_function("document.querySelector('[data-testid=\"mrp-effective\"]').innerText.includes('7')", timeout=5000)
            check(stored().get("retries", {}).get("count") == 7, f"typed count is stored ({stored().get('retries')})")
            check(stored().get("retries", {}).get("mode") == "count", "count mode is stored")
            check(page.locator('[data-testid="mrp-retries-count"]').input_value() == "7", "the count field still shows the typed value after the refresh")

            # 4. a fallback profile created through the dialog becomes the active mechanism
            page.locator('[data-mechanism="fallback"]').click()
            wait_selected(page, "fallback")
            profiles = page.locator('[data-testid="mrp-profiles"]').inner_text()
            check("Night shift" in profiles and "active" in profiles, "the new profile renders as active")
            fb = stored().get("fallback", {})
            check(fb.get("enabled") is True and fb.get("profile") in fb.get("profiles", {}), f"stored fallback points at the new profile ({fb})")
            check(fb["profiles"][fb["profile"]]["primary_retries"] == 4, "the dialog's primary retries are stored")
            check(page.locator(f'[data-testid="mrp-primary-{fb["profile"]}"]').input_value() == "4", "the profile card shows its primary retries")

            # 5. a reload (fresh mount) shows the same selection — nothing lives only in the page
            page.reload()
            page.wait_for_function("window.__ready === true", timeout=10000)
            check(selected(page) == "fallback", f"after reload the fallback card is selected (got {selected(page)!r})")
            check("Night shift" in page.locator('[data-testid="mrp-profiles"]').inner_text(), "after reload the profile is still there")

            # 6. back to the host default: nothing overridden, the retained count is still on disk for next time
            page.locator('[data-mechanism="host"]').click()
            wait_selected(page, "host")
            check(stored()["fallback"]["enabled"] is False and stored()["retries"]["mode"] == "host", "host card writes the host preset")
            check(stored()["retries"]["count"] == 7, "the count value is retained for the next time it is picked")

            page.evaluate("window.__unmount()")
            check(page.locator('[data-testid="mrp-page"]').count() == 0, "unmount removes the page")
            browser.close()
    finally:
        server.shutdown()
    errors = [c for c in console if c.startswith("error") or c.startswith("pageerror")]
    for f in failures:
        print("FAIL:", f)
    for e in errors:
        print("CONSOLE:", e)
    print(f"{'OK' if not failures and not errors else 'FAILED'}: {total - len(failures)}/{total} checks, {len(errors)} console error(s)")
    return 0 if not failures and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
