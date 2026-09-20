"""Playwright tests of the SPA host against a real dashboard (Requirement 13.3; task 5.6).

One scratch gateway (``kirocrew gateway --test-mode`` on the payload COPY under
``.scratch/``, scratch homes, ``FLOOFY_NO_ADAPTERS=1``) and one headless Chromium
serve every test in this module:

1. **no first-frame flash** — with the Rimuru boot part baked into the copy's
   ``index.html``, ``data-theme`` and the computed ``--bg`` token already equal
   the mod's values at ``DOMContentLoaded`` (React not mounted) and after
   hydration; a screenshot pair lands under ``.scratch/loader-tests/playwright/``;
2. **boundary isolation** — three runtime ``spa`` mods: one failing on import, one
   throwing inside ``activate``, one healthy — the healthy one runs, the host UI
   renders, two fault reports reach the Loader;
3. **reporter on a broken fingerprint** — an impossible DOM surface and an
   impossible bundle surface appear in ``missed`` with reasons, everything else
   matches; ``floofy verify --spa`` against the same gateway exits 1 with JSON;
4. **import-map remap** — a ``mode: import-map`` patch on a small chunk sets a
   marker that the host's own importers evaluate (spike 1.4 replay).

Skipped without a payload copy. The copy's ``index.html`` is patched on purpose
(that is the point) and restored byte-identically at teardown (``ShellGuard``).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from floofy_loader.paths import FloofyPaths

from loader_testing import REPO_ROOT, ScratchGateway, ShellGuard, build_loader_app, fresh_scratch, scratch_payload, shell_path
from spa_testing import API, RIMURU_DIR, consent, dashboard_url, install_loader_app, install_mod, write_test_mod

pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.skipif(scratch_payload() is None, reason="no payload copy under .scratch (set FLOOFY_SCRATCH_PAYLOAD)")

SHOTS = REPO_ROOT / ".scratch" / "loader-tests" / "playwright"
RIMURU_VARIABLES = json.loads((RIMURU_DIR / "theme" / "variables.json").read_text(encoding="utf-8"))

PROBE_INIT = """
window.__probe = {};
document.addEventListener('DOMContentLoaded', () => {
  const cs = getComputedStyle(document.documentElement);
  const root = document.getElementById('root');
  window.__probe.atDomContentLoaded = {
    theme: document.documentElement.getAttribute('data-theme'),
    bg: cs.getPropertyValue('--bg').trim(),
    accent: cs.getPropertyValue('--accent').trim(),
    rootChildren: root ? root.childElementCount : -1,
    favicon: [...document.querySelectorAll('link[rel="icon"]')].map((l) => l.getAttribute('href')),
    logo: document.documentElement.style.getPropertyValue('--theme-logo'),
  };
});
requestAnimationFrame(() => {
  window.__probe.firstFrame = { theme: document.documentElement.getAttribute('data-theme'), bg: getComputedStyle(document.documentElement).getPropertyValue('--bg').trim() };
});
"""

REMAP_PATCH = {
    "schema": 1,
    "target": "kiro_crew/static/dist/assets/useIsMobile-*.js",
    "mode": "import-map",
    "find": "(max-width: 767px)",
    "description": "test: mark the useIsMobile module so the remapped copy proves it is what the host evaluates",
    "ops": [
        {
            "op": "insert-before",
            "fingerprint": "(?<=;)function [a-z]\\(\\)\\{let e=typeof window<`u`\\?window\\.matchMedia",
            "regex": True,
            "content": "window.__floofy_remapped=1;",
            "marker": "__floofy_remapped",
        }
    ],
}


class Harness:
    def __init__(self, gw: ScratchGateway, guard: ShellGuard, browser):
        self.gw = gw
        self.guard = guard
        self.browser = browser

    def page(self, *, init: str | None = None, color_scheme: str = "dark"):
        context = self.browser.new_context(viewport={"width": 1280, "height": 800}, color_scheme=color_scheme)
        page = context.new_page()
        if init:
            page.add_init_script(init)
        return context, page

    def open(self, page, path: str = "/", *, wait_shell: bool = True):
        page.goto(dashboard_url(self.gw, path), wait_until="load", timeout=60000)
        if wait_shell:
            page.wait_for_selector('[data-testid="dashboard-shell"]', timeout=60000)
        page.wait_for_function("() => window.floofy && typeof window.floofy.report === 'function'", timeout=30000)
        page.wait_for_function("() => window.floofy.events.inspect().history.some((h) => h.name === 'host.ready')", timeout=30000)


@pytest.fixture(scope="module")
def harness():
    from playwright.sync_api import sync_playwright

    payload = scratch_payload()
    assert payload is not None
    scratch = fresh_scratch("playwright")
    app_dir = build_loader_app(scratch / "app" / "floofycrew")
    gw = ScratchGateway(payload, scratch)
    guard = ShellGuard(payload, gw.data_home)
    FloofyPaths(gw.data_home).ensure()
    install_mod(gw, RIMURU_DIR)
    write_test_mod(gw, "spa-import-fail", {"spa/main.js": 'import "./missing.js";\nwindow.__importFail = 1;\n'}, [{"kind": "spa", "side": "spa", "path": "spa/main.js"}])
    write_test_mod(gw, "spa-activate-fail", {"spa/main.js": 'export function activate(ctx) {\n  ctx.log.info("about to explode");\n  throw new Error("activate exploded on purpose");\n}\nexport function deactivate() { window.__activateFailDeactivated = 1; }\n'}, [{"kind": "spa", "side": "spa", "path": "spa/main.js"}])
    write_test_mod(
        gw,
        "spa-healthy",
        {
            "spa/main.js": 'import { tag } from "./lib/tag.js";\nexport default function activate(ctx) {\n  window.__spaHealthy = ctx.modId;\n  window.__spaHealthyFaults = [];\n  ctx.events.on("mod.faulted", (name, payload) => window.__spaHealthyFaults.push(payload.mod));\n  const badge = document.createElement("span");\n  badge.id = "floofy-healthy-badge";\n  badge.textContent = tag(ctx.host.version);\n  document.body.appendChild(badge);\n}\n',
            "spa/lib/tag.js": 'export const tag = (version) => `modded ${version}`;\n',
        },
        [{"kind": "spa", "side": "spa", "path": "spa/main.js"}],
    )
    write_test_mod(gw, "spa-remap", {"patches/remap.json": json.dumps(REMAP_PATCH, indent=2) + "\n"}, [{"kind": "patch", "side": "spa", "path": "patches/remap.json"}])
    consent(gw)
    install_loader_app(gw, app_dir)
    gw.start()
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            yield Harness(gw, guard, browser)
        finally:
            browser.close()
            gw.stop()
            guard.check()


def test_1_no_first_frame_flash(harness: Harness):
    dark = RIMURU_VARIABLES["dark"]
    context, page = harness.page(init=PROBE_INIT)
    try:
        page.goto(dashboard_url(harness.gw), wait_until="domcontentloaded", timeout=60000)
        page.screenshot(path=str(SHOTS / "first-frame-domcontentloaded.png"))
        page.wait_for_selector('[data-testid="dashboard-shell"]', timeout=60000)
        page.wait_for_function("() => document.getElementById('mc-custom-theme-rimuru') !== null", timeout=30000)
        page.wait_for_timeout(500)
        page.screenshot(path=str(SHOTS / "first-frame-hydrated.png"))
        probe = page.evaluate("() => window.__probe")
        early = probe["atDomContentLoaded"]
        assert early["rootChildren"] == 0, "React had not mounted yet at DOMContentLoaded"
        assert early["theme"] == "custom-rimuru-dark" and early["bg"] == dark["--bg"] and early["accent"] == dark["--accent"], early
        assert "/apps/floofycrew/ui/boot/rimuru-branding/favicon.png" in early["favicon"] and "boot/rimuru-branding/logo.png" in early["logo"]
        assert probe["firstFrame"] == {"theme": "custom-rimuru-dark", "bg": dark["--bg"]}
        after = page.evaluate(
            """() => ({
              theme: document.documentElement.getAttribute('data-theme'),
              bg: getComputedStyle(document.documentElement).getPropertyValue('--bg').trim(),
              hostCss: document.getElementById('mc-custom-theme-rimuru').textContent,
              boot: window.__floofyBoot,
              favicons: [...document.querySelectorAll('link[rel="icon"]')].map((l) => l.id),
              logo: document.querySelector('nav[role="navigation"] button[aria-expanded] img')?.getAttribute('src'),
            })"""
        )
        assert after["theme"] == "custom-rimuru-dark" and after["bg"] == dark["--bg"]
        assert after["hostCss"].strip() == (RIMURU_DIR / "spa" / "boot.css").read_text(encoding="utf-8").strip(), "the baked CSS is byte-identical to the host's runtime stylesheet"
        assert after["boot"]["applied"] == ["rimuru-branding"] and after["boot"]["settled"] is True and after["boot"]["errors"] == []
        assert "mc-theme-favicon" in after["favicons"] and "floofy-boot-favicon-rimuru-branding" not in after["favicons"], "the boot favicon yields to the host's once it lands"
        assert after["logo"] == "/api/theme/rimuru/assets/branding/logo.png"
    finally:
        context.close()


def test_2_boundary_isolation(harness: Harness):
    # an earlier page load may already have faulted the two mods (the Loader remembers); re-run the boot
    # so this page load is the one that hits the faults
    client = harness.gw.client()
    assert client.post(f"{API}/reload").status == 200
    faults_before = len([f for f in client.get(f"{API}/state").json()["faults"] if f["source"] == "spa"])
    context, page = harness.page()
    try:
        harness.open(page)
        try:
            page.wait_for_function("() => window.__spaHealthy === 'spa-healthy' && window.floofy.faulted().length === 2", timeout=30000)
        except Exception as exc:  # noqa: BLE001 - show what the host saw before failing
            diagnostics = page.evaluate("() => ({healthy: window.__spaHealthy, loaded: window.floofy && window.floofy.loaded(), faulted: window.floofy && window.floofy.faulted(), events: window.floofy && window.floofy.events.inspect().history.map((h) => h.name)})")
            raise AssertionError(f"SPA host state: {diagnostics}") from exc
        seen = page.evaluate(
            """() => ({
              loaded: window.floofy.loaded(), faulted: window.floofy.faulted(),
              badge: document.getElementById('floofy-healthy-badge')?.textContent,
              importFail: window.__importFail, deactivated: window.__activateFailDeactivated,
              healthySawFaults: window.__spaHealthyFaults,
              shell: !!document.querySelector('[data-testid="dashboard-shell"]'),
              mods: window.floofy.mods.map((m) => [m.id, m.active]),
            })"""
        )
        assert seen["loaded"] == ["spa-healthy"] and sorted(seen["faulted"]) == ["spa-activate-fail", "spa-import-fail"]
        assert seen["badge"] == "modded 0.7.0.5" and seen["importFail"] is None and seen["deactivated"] == 1
        assert seen["shell"] is True, "the host UI is unaffected"
        assert set(seen["healthySawFaults"]) <= {"spa-activate-fail", "spa-import-fail"} and seen["healthySawFaults"], "the healthy mod's scoped bus saw the faults raised after it subscribed"
        # the Loader recorded both faults and stopped serving the mods' files
        deadline = time.time() + 15
        while time.time() < deadline:
            state = client.get(f"{API}/state").json()
            faults = [(f["mod"], f["source"]) for f in state["faults"]]
            if len([f for f in faults if f[1] == "spa"]) >= faults_before + 2:
                break
            time.sleep(0.5)
        assert len([f for f in faults if f[1] == "spa"]) == faults_before + 2, faults
        assert ("spa-import-fail", "spa") in faults and ("spa-activate-fail", "spa") in faults, faults
        for mod_id in ("spa-import-fail", "spa-activate-fail"):
            assert state["mods"][mod_id]["reason"] == "Error" and state["mods"][mod_id]["active"] is False
            assert client.get(f"{API}/spa/{mod_id}/main.js").status == 404
        assert state["mods"]["spa-healthy"]["active"] is True and client.get(f"{API}/spa/spa-healthy/main.js").status == 200
        assert "activate exploded on purpose" in state["mods"]["spa-activate-fail"]["detail"]
    finally:
        context.close()


def test_3_reporter_flags_a_broken_fingerprint(harness: Harness):
    extra = [
        {"name": "test.impossible-dom", "fromBuild": "0.7.0.5", "dom": {"selector": "[data-testid=\"floofy-no-such-surface\"]"}},
        {"name": "test.impossible-bundle", "fromBuild": "0.7.0.5", "bundle": {"chunk": "App", "contains": "floofy-this-literal-is-not-in-the-bundle", "count": "once"}},
        {"name": "test.control", "fromBuild": "0.7.0.5", "dom": {"selector": "main#main-content"}, "bundle": {"chunk": "useTheme", "contains": "mc-theme-favicon"}},
    ]
    context, page = harness.page()
    try:
        harness.open(page)
        report = page.evaluate("(extra) => window.floofy.report({ extraSurfaces: extra })", extra)
        missed = {m["name"]: m for m in report["missed"]}
        assert set(missed) == {"test.impossible-dom#dom", "test.impossible-bundle#bundle"}, report["missed"]
        assert "matches nothing" in missed["test.impossible-dom#dom"]["reason"]
        assert "no occurrence" in missed["test.impossible-bundle#bundle"]["reason"] and missed["test.impossible-bundle#bundle"]["chunk"].startswith("App-")
        assert "test.control#dom" in report["matched"] and "test.control#bundle" in report["matched"]
        assert report["spaFingerprints"]["total"] >= 24 and report["spaFingerprints"]["missed"] == sorted(missed) or set(report["spaFingerprints"]["missed"]) == set(missed)
        assert all(not n.startswith("test.") for n in report["matched"] if "impossible" in n)
        # the patch rows of the two import-map patches are matched too
        assert "patch:rimuru-branding#2#importMap" in report["matched"] and "patch:spa-remap#0#ops/0" in report["matched"]
    finally:
        context.close()

    extra_file = harness.gw.scratch / "extra-surfaces.json"
    extra_file.write_text(json.dumps({"surfaces": extra[:2]}), encoding="utf-8")
    ready_file = harness.gw.scratch / "gateway.out"
    cmd = [sys.executable, "-m", "floofy_core.cli_spa", "--json", "verify", "--spa", "--ready-file", str(ready_file), "--extra-surfaces", str(extra_file), "--timeout", "90"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=240, cwd=str(REPO_ROOT / "floofy-core"))
    assert result.returncode == 1, result.stdout[-2000:] + result.stderr[-2000:]
    document = json.loads(result.stdout)
    assert document["source"] == "browser" and document["hostVersion"] == "0.7.0.5" and document["hostTag"] == "present"
    assert set(document["spaFingerprints"]["missed"]) == {"test.impossible-dom#dom", "test.impossible-bundle#bundle"}
    assert document["spaFingerprints"]["matched"] >= 24

    clean = subprocess.run(cmd[:-4] + ["--timeout", "90"], capture_output=True, text=True, timeout=240, cwd=str(REPO_ROOT / "floofy-core"))
    assert clean.returncode == 0, clean.stdout[-2000:] + clean.stderr[-2000:]
    assert json.loads(clean.stdout)["spaFingerprints"]["missed"] == []


def test_4_import_map_remap_is_what_the_host_evaluates(harness: Harness):
    shell = shell_path(harness.gw.payload).read_text(encoding="utf-8")
    assert "/apps/floofycrew/ui/patched/useIsMobile-" in shell and 'href="/assets/useIsMobile-' not in shell, "the import map is remapped and the modulepreload hint dropped"
    context, page = harness.page()
    try:
        harness.open(page)
        seen = page.evaluate(
            """async () => ({
              remapped: window.__floofy_remapped,
              applied: await window.floofy.patches.isApplied('spa-remap'),
              rows: (await window.floofy.patches.list()).map((r) => [r.chunk.split('-')[0], r.mod, r.mapped]),
              original: document.querySelector('script[type="importmap"]').textContent.includes('/apps/floofycrew/ui/patched/useIsMobile-'),
            })"""
        )
        assert seen["remapped"] == 1, "the host's own importers evaluated the patched copy"
        assert seen["applied"] is True and seen["original"] is True
        assert sorted(seen["rows"]) == [["useIsMobile", "spa-remap", True], ["useTheme", "rimuru-branding", True]]
    finally:
        context.close()
    original = harness.gw.payload / "lib" / "python3.12" / "site-packages" / "kiro_crew" / "static" / "dist" / "assets"
    chunk = next(original.glob("useIsMobile-*.js"))
    assert "__floofy_remapped" not in chunk.read_text(encoding="utf-8"), "the original chunk is untouched"



def test_5_manager_page_renders_and_toggles_a_mod(harness: Harness):
    """The manager page (task 6.7): unofficial banner, mod rows with state/seam/compat, disable through the in-process CLI."""
    client = harness.gw.client()
    assert client.post(f"{API}/reload").status == 200
    enabled_path = FloofyPaths(harness.gw.data_home).enabled
    assert json.loads(enabled_path.read_text(encoding="utf-8"))["spa-healthy"] is True
    context, page = harness.page()
    try:
        page.goto(dashboard_url(harness.gw, "/apps/floofycrew"), wait_until="load", timeout=60000)
        page.wait_for_selector('[data-testid="floofycrew-banner"]', timeout=60000)
        banner = page.inner_text('[data-testid="floofycrew-banner"]')
        assert "UNOFFICIAL" in banner and "not affiliated" in banner, banner
        page.wait_for_selector('[data-testid="floofycrew-mod-row-rimuru-branding"]', timeout=30000)
        row = page.inner_text('[data-testid="floofycrew-mod-row-rimuru-branding"]')
        assert "rimuru-branding 1.0.0" in row and "active" in row and "theme/gateway" in row and "modifies payload files" in row, row
        assert page.inner_text('[data-testid="floofycrew-compat-rimuru-branding"]') == "in-range"
        assert "acknowledged" in page.inner_text('[data-testid="floofycrew-consent"]')
        healthy = page.inner_text('[data-testid="floofycrew-mod-row-spa-healthy"]')
        assert "spa/spa" in healthy and "SPA host" in healthy
        assert page.inner_text('[data-testid="floofycrew-enabled-spa-healthy"]').startswith("enabled")
        # disable through the UI → POST /cli ["disable", "spa-healthy"] → enabled.json flips → the Loader reloads
        page.click('[data-testid="floofycrew-toggle-spa-healthy"]')
        page.wait_for_function("() => document.querySelector('[data-testid=\"floofycrew-enabled-spa-healthy\"]')?.textContent.startsWith('disabled')", timeout=60000)
        assert json.loads(enabled_path.read_text(encoding="utf-8"))["spa-healthy"] is False
        output = page.inner_text('[data-testid="floofycrew-output"]')
        assert "$ floofy disable spa-healthy" in output and "Loader reloaded" in output, output
        state = client.get(f"{API}/state").json()
        assert state["mods"]["spa-healthy"]["active"] is False and state["mods"]["spa-healthy"]["reason"] == "UserDisabled"
        assert state["mods"]["rimuru-branding"]["active"] is True, "the other mods survived the reload"
        rows = [json.loads(line) for line in (harness.gw.data_home / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
        assert any(r["op"] == "disable" and r["mod"] == "spa-healthy" and r["actor"] == "ui" for r in rows)
        # the UI cannot run what needs a keyboard: init is refused by the route
        refused = client.post(f"{API}/cli", {"argv": ["init", "--i-accept-the-risk"]})
        assert refused.status == 403 and "CLI-only" in refused.json()["error"]
        # re-enable so the module's other tests see the original state
        page.click('[data-testid="floofycrew-toggle-spa-healthy"]')
        page.wait_for_function("() => document.querySelector('[data-testid=\"floofycrew-enabled-spa-healthy\"]')?.textContent.startsWith('enabled')", timeout=60000)
        assert json.loads(enabled_path.read_text(encoding="utf-8"))["spa-healthy"] is True
    finally:
        context.close()
