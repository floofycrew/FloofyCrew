"""Headless-browser probe of ui/page.mjs against a fake ``floofy.mod`` api (no gateway).

Run: ``python tests/page_probe.py`` (needs playwright + a chromium build in the cache).
Mounts the page on a realistic ``status`` payload and drives the three actions.
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = "/home/u"

STATUS = {
    "host_version": "0.8.0.6", "hooks": {"node_adapters": "active", "plain_binaries": "active"},
    "search_path": [f"{HOME}/.local/bin", f"{HOME}/.cli/bin", "/usr/bin"], "search_path_count": 3,
    "node": f"{HOME}/.local/node/current/bin/node", "codex_path": None, "codex_path_default": f"{HOME}/.cli/bin/codex", "local_bin": f"{HOME}/.local/bin",
    "config": {}, "host_rows_error": None, "ts": 1790000000,
    "prefixes": [{"rung": "the tree node lives in", "prefix": f"{HOME}/.local/node/current", "node_modules": [f"{HOME}/.local/node/current/lib/node_modules"]}, {"rung": "~/.npm-global", "prefix": f"{HOME}/.npm-global", "node_modules": []}],
    "repairs": [{"name": "codex-acp", "link": f"{HOME}/.local/bin/codex-acp", "target": f"{HOME}/.local/node/current/bin/codex-acp", "exists": False, "backend": "codex"}],
    "backends": [
        {"id": "", "name": "kiro", "namespace": "acp", "selectable": True, "probe": {"installed": "installed", "missing_components": [], "install_command": "", "restart_required": False},
         "cache": {"state": "none", "caches": {}}, "components": {"launcher": "kiro-cli", "launcher_on_host_path": f"{HOME}/.local/bin/kiro-cli"}, "advertised": [], "finding": None, "host_row": None},
        {"id": "kas", "name": "kas", "namespace": "acp", "selectable": True, "probe": {"installed": "installed", "missing_components": [], "install_command": "", "restart_required": False},
         "cache": {"state": "none", "caches": {}}, "components": {"launcher": "kiro-cli", "launcher_on_host_path": f"{HOME}/.local/bin/kiro-cli"}, "advertised": [], "finding": None, "host_row": None},
        {"id": "claude", "name": "claude", "namespace": "claude_code", "selectable": True, "probe": {"installed": "installed", "missing_components": [], "install_command": "", "restart_required": True},
         "cache": {"state": "negative", "caches": {"_claude_acp_argv_cache": {"state": "negative", "value": None}}},
         "components": {"launcher": "claude-agent-acp", "launcher_on_host_path": f"{HOME}/.local/bin/claude-agent-acp", "adapter": None, "launchers": [], "cli": {"name": "claude", "path": f"{HOME}/.cli/bin/claude", "env": "CLAUDE_CODE_EXECUTABLE", "forwarded": True}},
         "advertised": ["default", "fable", "claude-opus-5[1m]"], "finding": {"host": "found", "argv": ["node", "x"], "ts": 1790000000}, "host_row": {"installed": "installed", "restart_required": True}},
        {"id": "codex", "name": "codex", "namespace": "codex", "selectable": True, "probe": {"installed": "missing", "missing_components": ["codex-acp"], "install_command": "npm i -g @agentclientprotocol/codex-acp", "restart_required": False},
         "cache": {"state": "unresolved", "caches": {"_codex_acp_argv_cache": {"state": "unresolved"}}},
         "components": {"launcher": "codex-acp", "launcher_on_host_path": None, "adapter": {"entry": f"{HOME}/.local/node/current/lib/node_modules/@agentclientprotocol/codex-acp/dist/index.js", "node": f"{HOME}/.local/node/current/bin/node", "prefix": f"{HOME}/.local/node/current", "rung": "the tree node lives in"},
                        "launchers": [f"{HOME}/.local/node/current/bin/codex-acp"], "cli": {"name": "codex", "path": None, "env": "CODEX_PATH", "forwarded": False, "installed_codex": f"{HOME}/.cli/bin/codex", "note": ""}},
         "advertised": [], "finding": {"host": "missing", "doctor": {"entry": f"{HOME}/.local/node/current/lib/node_modules/@agentclientprotocol/codex-acp/dist/index.js", "node": f"{HOME}/.local/node/current/bin/node", "prefix": f"{HOME}/.local/node/current", "rung": "the tree node lives in"}, "ts": 1790000000}, "host_row": {"installed": "missing"}},
        {"id": "opencode", "name": "opencode", "namespace": "opencode", "selectable": False, "probe": {"installed": "missing", "missing_components": ["opencode"], "install_command": "npm i -g opencode-ai", "restart_required": False},
         "cache": {"state": "unresolved", "caches": {}}, "components": {"launcher": "opencode", "launcher_on_host_path": None, "launchers": []}, "advertised": [], "finding": None, "host_row": None},
        {"id": "pi", "name": "pi", "namespace": "pi", "selectable": True, "probe": {"installed": "unknown", "missing_components": [], "install_command": "", "restart_required": False},
         "cache": {"state": "unresolved", "caches": {}}, "components": {"launcher": "pi-acp", "launcher_on_host_path": None, "adapter": None, "launchers": [], "cli": {"name": "pi", "path": None, "env": "PI_ACP_PI_COMMAND", "forwarded": True}}, "advertised": [], "finding": None, "host_row": None},
    ],
}

HTML = """<!doctype html><html><body><div id="root"></div>
<script type="module">
  const calls = [];
  window.__calls = calls;
  const status = %(status)s;
  const api = {
    theme: { tokens: () => ({}) },
    config: { get: async () => ({}), set: async (p) => p },
    routes: { fetch: async (path, init) => { const body = init && init.body ? JSON.parse(init.body) : null; calls.push([path, body]);
      if (path === "status") return new Response(JSON.stringify(status), { status: 200 });
      if (path === "reresolve") return new Response(JSON.stringify({ ok: true, reset: [], probes: { codex: { installed: "installed", restart_required: false, cache: { state: "ok" } } } }), { status: 200 });
      if (path === "codex-path") { status.codex_path = body.enabled ? body.path : null; return new Response(JSON.stringify({ ok: true, enabled: body.enabled, codex_path: status.codex_path }), { status: 200 }); }
      if (path === "symlink") { status.repairs[0].exists = true; return new Response(JSON.stringify({ ok: true, results: [{ name: "codex-acp", ok: true }] }), { status: 200 }); }
      return new Response("{}", { status: 404 }); } },
    dialog: { confirm: async (text, opts) => { calls.push(["confirm", { text, opts }]); return true; } },
    log: { warn: (...a) => console.warn(...a) },
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
            body, ctype = (HTML % {"status": json.dumps(STATUS)}).encode(), "text/html"
        elif self.path == "/ui/page.mjs":
            body, ctype = (ROOT / "ui" / "page.mjs").read_bytes(), "text/javascript"
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
            page = browser.new_page()
            page.on("console", lambda m: console.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_function("window.__ready === true", timeout=10000)

            def check(cond: bool, what: str) -> None:
                if not cond:
                    failures.append(what)

            rows = page.locator('[data-testid="abd-backends"] tbody tr')
            check(rows.count() == 6, f"six backend rows (got {rows.count()})")
            text = page.locator('[data-testid="abd-backends"]').inner_text()
            check("gateway still has a cached miss" in text, "claude row shows the restart_required verdict")
            check("cached: not found" in text, "claude row shows the cached negative")
            check("found by the doctor" in text and "the tree node lives in" in text, "codex row shows the doctor's finding and rung")
            check("npm i -g @agentclientprotocol/codex-acp" in text, "codex row shows the host's install command")
            check("not selectable by policy" in text, "opencode row shows the policy state")
            check("3 advertised" in text, "claude row shows the advertised-model count")
            check("adapter's own Codex" in text, "codex CLI cell explains the default Codex")
            env = page.locator('[data-testid="abd-env"]').inner_text()
            check("KiroCrew 0.8.0.6" in env and "node adapters active" in env and "~/.npm-global (no node_modules)" in env, "header shows host, hooks and prefixes")
            check("~/.local/bin/codex-acp → ~/.local/node/current/bin/codex-acp" in page.locator('[data-testid="abd-repairs"]').inner_text(), "repair plan lists the symlink with ~ paths")
            check("doctor answered ~/.local/node" in page.locator('[data-testid="abd-findings"]').inner_text(), "findings list the doctor's answer")

            page.click('[data-testid="abd-reresolve-codex"]')
            page.wait_for_function("window.__calls.some((c) => c[0] === 'reresolve')", timeout=5000)
            calls = page.evaluate("window.__calls")
            check(any(c[0] == "reresolve" and c[1] == {"backend": "codex"} for c in calls), "per-row re-resolve posts the backend id")
            page.click('[data-testid="abd-reresolve-all"]')
            page.wait_for_function("window.__calls.filter((c) => c[0] === 'reresolve').length >= 2", timeout=5000)
            calls = page.evaluate("window.__calls")
            check(any(c[0] == "reresolve" and c[1] == {} for c in calls), "re-resolve all posts an empty body")

            page.check('[data-testid="abd-codex-toggle"]')
            page.wait_for_function("window.__calls.some((c) => c[0] === 'codex-path')", timeout=5000)
            calls = page.evaluate("window.__calls")
            check(any(c[0] == "codex-path" and c[1] == {"enabled": True, "path": f"{HOME}/.cli/bin/codex"} for c in calls), "codex toggle posts enabled + the default path")
            page.wait_for_function("document.querySelector('[data-testid=\"abd-env\"]').innerText.includes('CODEX_PATH: ~/.cli/bin/codex')", timeout=5000)
            check(page.locator('[data-testid="abd-codex-toggle"]').is_checked(), "toggle stays on after the refresh")

            page.click('[data-testid="abd-symlink"]')
            page.wait_for_function("window.__calls.some((c) => c[0] === 'symlink')", timeout=5000)
            calls = page.evaluate("window.__calls")
            check(any(c[0] == "confirm" and "~/.local/bin/codex-acp" in c[1]["text"] for c in calls), "symlink asks through api.dialog.confirm with the plan")
            check(any(c[0] == "symlink" and c[1] == {"confirm": True, "entries": [{"name": "codex-acp"}]} for c in calls), "symlink posts confirm + entries")
            page.wait_for_function("document.querySelector('[data-testid=\"abd-repairs\"]').innerText.includes('already linked')", timeout=5000)
            check(page.locator('[data-testid="abd-symlink"]').count() == 0, "symlink button disappears once everything is linked")
            page.evaluate("window.__unmount && window.__unmount()")
            check(page.locator('[data-testid="abd-page"]').count() == 0, "unmount clears the container")
            browser.close()
    finally:
        server.shutdown()
    for line in console:
        failures.append(f"console: {line}")
    print(f"page probe: {len(failures)} failure(s)")
    for f in failures:
        print("  FAIL:", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
