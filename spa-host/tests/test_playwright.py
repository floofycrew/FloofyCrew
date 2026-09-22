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
   marker that the host's own importers evaluate (spike 1.4 replay);
5. **the manager App** (5, 5b, 5c) — the shell and its pages, every CLI
   confirmation as a modal that changes nothing until answered, mod ``ui``
   pages isolated by the error boundary;
6. **the manager App smoke** (Requirement 16.7) — add a local signed registry,
   install ``settings-demo`` from its record with *apply now*, enable it, open
   its settings page, round-trip a value through the mod's backend, disable it
   through the page's button.

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
from spa_testing import API, CUSTOM_THEMES_DIR, RIMURU_DIR, consent, dashboard_url, install_loader_app, install_mod, write_test_mod

pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.skipif(scratch_payload() is None, reason="no payload copy under .scratch (set FLOOFY_SCRATCH_PAYLOAD)")

SHOTS = REPO_ROOT / ".scratch" / "loader-tests" / "playwright"
RIMURU_VARIABLES = json.loads((RIMURU_DIR / "theme" / "variables.json").read_text(encoding="utf-8"))

PROBE_INIT = """
window.__probe = { bootNode: null };
new MutationObserver((records) => {
  for (const record of records) for (const node of record.addedNodes) {
    if (node.nodeType === 1 && node.id === 'floofy-custom-themes-boot' && !window.__probe.bootNode) window.__probe.bootNode = node.tagName;
  }
}).observe(document, { childList: true, subtree: true });
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
    # FLOOFY_ALLOW_LOCAL_GIT: the confirmations test installs from a file:// bare repository (the test-suite switch of task 7.7)
    gw = ScratchGateway(payload, scratch, extra_env={"FLOOFY_ALLOW_LOCAL_GIT": "1"})
    guard = ShellGuard(payload, gw.data_home)
    FloofyPaths(gw.data_home).ensure()
    install_mod(gw, CUSTOM_THEMES_DIR)  # the first-frame sheet + the reset guard every custom theme needs
    install_mod(gw, RIMURU_DIR)  # a pure theme pack on top of it (2.0.0)
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
    """The Rimuru pack paints on the first frame through custom-themes. A COLD client (empty storage) gets the boot
    hook's render-blocking link to the sheet the hook keeps for every installed custom theme: the palette is in force
    by DOMContentLoaded. A WARM client (the runtime part cached the compiled sheet) gets it inlined before anything
    else in <head>, so even the very first animation frame carries the pack's colours. Then the runtime part's own
    <style> takes over and drops the boot element."""
    dark = RIMURU_VARIABLES["dark"]
    context, page = harness.page(init=PROBE_INIT)
    try:
        # cold
        page.goto(dashboard_url(harness.gw), wait_until="domcontentloaded", timeout=60000)
        page.screenshot(path=str(SHOTS / "first-frame-domcontentloaded.png"))
        page.wait_for_selector('[data-testid="dashboard-shell"]', timeout=60000)
        page.wait_for_function("() => document.getElementById('mc-custom-theme-rimuru') !== null", timeout=30000)
        page.wait_for_function("() => (document.getElementById('floofy-custom-themes') || {}).textContent", timeout=30000)
        page.wait_for_timeout(500)
        page.screenshot(path=str(SHOTS / "first-frame-hydrated.png"))
        probe = page.evaluate("() => window.__probe")
        early = probe["atDomContentLoaded"]
        assert early["rootChildren"] == 0, "React had not mounted yet at DOMContentLoaded"
        assert early["theme"] == "custom-rimuru-dark" and early["bg"] == dark["--bg"] and early["accent"] == dark["--accent"], early
        assert probe["bootNode"] == "LINK", "a cold client is served the render-blocking link"
        assert probe["firstFrame"]["theme"] == "custom-rimuru-dark"
        after = page.evaluate(
            """() => ({
              theme: document.documentElement.getAttribute('data-theme'),
              bg: getComputedStyle(document.documentElement).getPropertyValue('--bg').trim(),
              hostCss: document.getElementById('mc-custom-theme-rimuru').textContent,
              modCss: document.getElementById('floofy-custom-themes').textContent,
              bootNode: document.getElementById('floofy-custom-themes-boot') !== null,
              cached: localStorage.getItem('floofy-custom-themes-css:rimuru'),
              boot: window.__floofyBoot,
              favicons: [...document.querySelectorAll('link[rel="icon"]')].map((l) => l.id),
              logo: document.querySelector('nav[role="navigation"] button[aria-expanded] img')?.getAttribute('src'),
            })"""
        )
        assert after["theme"] == "custom-rimuru-dark" and after["bg"] == dark["--bg"]
        assert after["boot"]["applied"] == ["custom-themes"] and after["boot"]["settled"] is True and after["boot"]["errors"] == []
        assert after["hostCss"].strip().replace('[data-theme="custom-rimuru-', 'html[data-theme="custom-rimuru-') in after["modCss"], "the mod's compiled sheet carries the host's own generated palette CSS (values byte for byte, one type selector more)"
        assert after["bootNode"] is False, "the runtime part drops the boot element once its <style> holds the same rules"
        assert after["cached"] == after["modCss"], "the compiled sheet is cached for the next load of this client"
        assert "mc-theme-favicon" in after["favicons"]
        assert after["logo"] == "/api/theme/rimuru/assets/branding/logo.png"
        sheet = harness.gw.client().get("/apps/floofycrew/ui/boot/custom-themes/themes.css")
        assert sheet.status == 200 and 'html[data-theme="custom-rimuru-dark"]{' in sheet.body.decode("utf-8"), "the first-frame sheet is served by the Loader app's ui route"

        # warm: the same client again
        page.goto(dashboard_url(harness.gw), wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector('[data-testid="dashboard-shell"]', timeout=60000)
        page.wait_for_function("() => (document.getElementById('floofy-custom-themes') || {}).textContent", timeout=30000)
        probe = page.evaluate("() => window.__probe")
        assert probe["bootNode"] == "STYLE", "a warm client gets the cached sheet inlined"
        assert probe["atDomContentLoaded"]["bg"] == dark["--bg"]
        assert probe["firstFrame"] == {"theme": "custom-rimuru-dark", "bg": dark["--bg"]}, "not even the first animation frame shows the stock colours"
        assert page.evaluate("() => document.getElementById('floofy-custom-themes-boot') === null")
    finally:
        context.close()


def test_2_boundary_isolation(harness: Harness):
    client = harness.gw.client()
    context, page = harness.page()
    try:
        # A fresh browser profile is not the same as a fresh page: on its first load the host's entry chunk
        # fetches /api/ui-prefs and, when the server holds prefs this profile lacks (test_1 left the Rimuru
        # theme set), writes them and does one location.reload(). That first load hits the two faults and
        # reports them, the self-reload then reads /state with the mods already disabled and never attempts
        # them — `faulted()` is empty and the assertions below see nothing. So: open once and let the
        # profile settle (the host marks it synced in localStorage), THEN reset the Loader and reload the
        # page; the second load is the one that hits the faults, on a profile the host will not reload again.
        harness.open(page)
        page.wait_for_function("() => localStorage.getItem('mc-ui-prefs-synced') !== null", timeout=30000)
        # an earlier page load (or this settling load) already faulted the two mods and the Loader remembers:
        # re-run the boot so the load below is the one that hits the faults
        assert client.post(f"{API}/reload").status == 200
        faults_before = len([f for f in client.get(f"{API}/state").json()["faults"] if f["source"] == "spa"])
        page.reload(wait_until="load", timeout=60000)
        page.wait_for_selector('[data-testid="dashboard-shell"]', timeout=60000)
        page.wait_for_function("() => window.floofy && typeof window.floofy.report === 'function'", timeout=30000)
        page.wait_for_function("() => window.floofy.events.inspect().history.some((h) => h.name === 'host.ready')", timeout=30000)
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
        assert sorted(seen["loaded"]) == ["custom-themes", "spa-healthy"] and sorted(seen["faulted"]) == ["spa-activate-fail", "spa-import-fail"]
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
        assert "patch:custom-themes#4#importMap" in report["matched"] and "patch:spa-remap#0#ops/0" in report["matched"]
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
        assert sorted(seen["rows"]) == [["useIsMobile", "spa-remap", True], ["useTheme", "custom-themes", True]]
    finally:
        context.close()
    original = harness.gw.payload / "lib" / "python3.12" / "site-packages" / "kiro_crew" / "static" / "dist" / "assets"
    chunk = next(original.glob("useIsMobile-*.js"))
    assert "__floofy_remapped" not in chunk.read_text(encoding="utf-8"), "the original chunk is untouched"



def test_5_manager_page_renders_and_toggles_a_mod(harness: Harness):
    """The manager page (task 6.7, 7.10): unofficial banner, mod rows with state/seam/compat/tier badges, disable through the in-process CLI."""
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
        assert "rimuru-branding 2.0.0" in row and "active" in row and "theme/gateway" in row and "modifies payload files" not in row, row
        themes_row = page.inner_text('[data-testid="floofycrew-mod-row-custom-themes"]')
        assert "custom-themes 1.0.1" in themes_row and "active" in themes_row and "modifies payload files" in themes_row, themes_row
        assert page.inner_text('[data-testid="floofycrew-compat-rimuru-branding"]') == "in-range"
        assert page.inner_text('[data-testid="floofycrew-tier-rimuru-branding"]') == "tier: unlisted", "copied in without a registry record: the source tier badge says so (Requirement 8.11)"
        assert "acknowledged" in page.inner_text('[data-testid="floofycrew-consent"]')
        healthy = page.inner_text('[data-testid="floofycrew-mod-row-spa-healthy"]')
        assert "spa/spa" in healthy and "SPA host" in healthy
        assert page.inner_text('[data-testid="floofycrew-enabled-spa-healthy"]').startswith("enabled")
        # disable through the UI → POST /mods/spa-healthy/disable (the same handler as `floofy disable`) → enabled.json flips → the Loader reloads
        page.click('[data-testid="floofycrew-toggle-spa-healthy"]')
        page.wait_for_function("() => document.querySelector('[data-testid=\"floofycrew-enabled-spa-healthy\"]')?.textContent.startsWith('disabled')", timeout=60000)
        assert json.loads(enabled_path.read_text(encoding="utf-8"))["spa-healthy"] is False
        output = page.inner_text('[data-testid="floofycrew-output"]')
        assert "$ floofy disable spa-healthy" in output and "Loader reloaded" in output, output
        state = client.get(f"{API}/state").json()
        assert state["mods"]["spa-healthy"]["active"] is False and state["mods"]["spa-healthy"]["reason"] == "UserDisabled"
        assert state["mods"]["rimuru-branding"]["active"] is True, "the other mods survived the reload"
        rows = [json.loads(line) for line in (harness.gw.data_home / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
        assert any(r["op"] == "disable" and r["mod"] == "spa-healthy" and r["actor"] == "app" for r in rows)
        # /cli is read-only now: a mutation names its typed route (task 11.1)
        refused = client.post(f"{API}/cli", {"argv": ["init", "--i-accept-the-risk"]})
        assert refused.status == 403 and "/consent" in refused.json()["error"]
        # a typed route through the real gateway: the same handler, the 409 question first, then the row with actor app
        asked = client.post(f"{API}/mods/spa-healthy/uninstall", {"now": True})
        assert asked.status == 409 and asked.json()["confirmation"]["kind"] == "yes-no", asked.body[:300]
        assert (harness.gw.data_home / "mods" / "spa-healthy").is_dir(), "a 409 has no side effect"
        # re-enable so the module's other tests see the original state
        page.click('[data-testid="floofycrew-toggle-spa-healthy"]')
        page.wait_for_function("() => document.querySelector('[data-testid=\"floofycrew-enabled-spa-healthy\"]')?.textContent.startsWith('enabled')", timeout=60000)
        assert json.loads(enabled_path.read_text(encoding="utf-8"))["spa-healthy"] is True
        # task 11.2 — the App shell: FloofyCrew by name, the branding mark, six sections with Mods as the landing page
        assert page.inner_text('[data-testid="floofycrew-header"]').startswith("FloofyCrew")
        art = client.get("/apps/floofycrew/art/art/icon.svg")  # /apps/<name>/art/<iconPath verbatim>
        assert art.status == 200 and art.body == (REPO_ROOT / "branding" / "logo.svg").read_bytes(), f"the host serves the declared iconPath: HTTP {art.status} {art.body[:200]!r}"
        page.wait_for_function("() => { const img = document.querySelector('[data-testid=\"floofycrew-header\"] img'); return img && img.complete && img.naturalWidth > 0; }", timeout=30000)
        assert page.locator('[data-testid="floofycrew-page-mods"]').count() == 1, "the manager page is the landing page"
        nav = page.inner_text('[data-testid="floofycrew-nav"]')
        assert [w for w in nav.split() if w] == ["Mods", "Registry", "Profiles", "Doctor", "Audit", "Settings"], nav
        for key, marker in (("registry", "floofycrew-sources"), ("profiles", "floofycrew-profiles"), ("doctor", "floofycrew-doctor"), ("audit", "floofycrew-audit"), ("settings", "floofycrew-settings-terminal")):
            page.click(f'[data-testid="floofycrew-nav-{key}"]')
            page.wait_for_selector(f'[data-testid="floofycrew-page-{key}"] [data-testid="{marker}"]', timeout=60000)
            assert page.evaluate("() => location.hash") == f"#/{key}"
        assert "floofy init" in page.inner_text('[data-testid="floofycrew-settings-terminal"]'), "a capability the App lacks shows its command (Requirement 16.2)"
        page.wait_for_selector('[data-testid="floofycrew-banner-art"]', timeout=30000)
        page.click('[data-testid="floofycrew-nav-audit"]')
        page.wait_for_function("() => document.querySelector('[data-testid=\"floofycrew-audit\"]')?.textContent.includes('disable spa-healthy')", timeout=30000)
        page.click('[data-testid="floofycrew-nav-doctor"]')
        page.wait_for_function("() => document.querySelector('[data-testid=\"floofycrew-doctor-consent\"]')?.textContent.includes('ok')", timeout=60000)
        page.click('[data-testid="floofycrew-nav-mods"]')
        page.wait_for_selector('[data-testid="floofycrew-mod-row-rimuru-branding"]', timeout=30000)
        assert page.inner_text('[data-testid="floofycrew-banner"]').count("UNOFFICIAL") == 1, "the banner stays on every page"
        # task 11.4 — update availability with the release-notes link, the Registry page's Updates card and search badges
        index_cache = FloofyPaths(harness.gw.data_home).index_cache
        index_cache.write_text(json.dumps({"schema": 1, "source": "seeded", "mods": [{"id": "spa-healthy", "name": "spa-healthy", "description": "browser test mod", "authors": ["tests"], "tags": ["test"], "repo": "https://forge.example/spa-healthy", "versions": [{"version": "1.0.0", "kirocrew": ">=0.7.0 <0.9.0", "compat": {}, "files": [{"url": "https://example.invalid/a.zip", "sha256": "0" * 64}]}, {"version": "1.1.0", "kirocrew": ">=0.7.0 <0.9.0", "compat": {"0.7.0.5": "expected"}, "files": [{"url": "https://example.invalid/b.zip", "sha256": "1" * 64}], "changelog": "https://forge.example/spa-healthy/releases/1.1.0"}]}]}), encoding="utf-8")
        try:
            page.reload(wait_until="load")
            page.wait_for_selector('[data-testid="floofycrew-update-spa-healthy"]', timeout=60000)
            assert "update available: 1.1.0 (expected here)" in page.inner_text('[data-testid="floofycrew-update-spa-healthy"]')
            assert page.get_attribute('[data-testid="floofycrew-changelog-spa-healthy"]', "href") == "https://forge.example/spa-healthy/releases/1.1.0"
            assert page.locator('[data-testid="floofycrew-update-button-spa-healthy"]').count() == 1 and page.locator('[data-testid="floofycrew-update-now-spa-healthy"]').count() == 1
            page.click('[data-testid="floofycrew-nav-registry"]')
            page.wait_for_selector('[data-testid="floofycrew-update-row-spa-healthy"]', timeout=60000)
            assert page.get_attribute('[data-testid="floofycrew-update-changelog-spa-healthy"]', "href") == "https://forge.example/spa-healthy/releases/1.1.0"
            assert "Update all 1" in page.inner_text('[data-testid="floofycrew-update-all"]')
            page.wait_for_selector('[data-testid="floofycrew-hit-spa-healthy"]', timeout=60000)
            versions = page.inner_text('[data-testid="floofycrew-hit-versions-spa-healthy"]')
            assert "1.0.0: untested" in versions and "1.1.0: expected" in versions, versions
            assert page.inner_text('[data-testid="floofycrew-hit-tier-spa-healthy"]') == "tier: listed" and "works here: 1.1.0 (expected)" in page.inner_text('[data-testid="floofycrew-hit-compat-spa-healthy"]')
        finally:
            index_cache.unlink()
    finally:
        context.close()




def _audit_rows(harness: Harness) -> list[dict]:
    path = harness.gw.data_home / "audit.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []


def _write_mod(root: Path, mod_id: str, parts: list[dict], files: dict[str, str]) -> Path:
    import hashlib

    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    manifest = {"schema": 1, "id": mod_id, "name": mod_id, "version": "1.0.0", "description": "confirmations test mod", "authors": ["tests"], "license": "MIT", "kirocrew": {"version": ">=0.7.0 <0.9.0"}, "dependsOn": {"floofycrew": ">=0.0.0"}, "parts": parts, "files": [{"path": rel, "sha256": hashlib.sha256((root / rel).read_bytes()).hexdigest()} for rel in sorted(files)]}
    (root / "floofy.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return root


def test_5b_confirmations_need_the_user(harness: Harness):
    """Task 11.3 (Requirement 16.3, 16.5, 11.1, 11.4, 8.8): every question the CLI would ask is a modal the user answers;
    nothing changes before the answer, a typed governance path is typed, the consent is the same text with [ I AGREE ]."""
    import subprocess

    client = harness.gw.client()
    data_home = harness.gw.data_home
    govy = _write_mod(harness.gw.scratch / "src" / "govy", "govy", [{"kind": "agent", "side": "gateway", "path": "agents/a.json"}], {"agents/a.json": json.dumps({"name": "govy-agent"}), "security_policy.json": "{}"})
    context, page = harness.page()
    try:
        page.goto(dashboard_url(harness.gw, "/apps/floofycrew"), wait_until="load", timeout=60000)
        page.wait_for_selector('[data-testid="floofycrew-install-form"]', timeout=60000)
        rows_before = len(_audit_rows(harness))

        # --- a governance-altering install: the disclosure, the flags question, then the typed path (staged by default) ---
        page.fill('[data-testid="floofycrew-install-source"]', str(govy))
        page.click('[data-testid="floofycrew-install-submit"]')
        page.wait_for_selector('[data-testid="floofycrew-yesno-modal"]', timeout=60000)
        disclosure = page.inner_text('[data-testid="floofycrew-disclosure"]')
        assert "govy 1.0.0" in disclosure and "agent/gateway" in disclosure and "GOVERNANCE-ALTERING" in disclosure and "security_policy.json" in disclosure, disclosure
        assert "no remote hosts declared" in page.inner_text('[data-testid="floofycrew-disclosure-network"]')
        assert "Accept the 1 flag(s)" in page.inner_text('[data-testid="floofycrew-yesno-prompt"]')
        assert page.is_checked('[data-testid="floofycrew-staging-staged"]'), "staged for the next gateway start is the default (Requirement 16.5)"
        assert not (data_home / "pending" / "govy").exists() and not (data_home / "mods" / "govy").exists()
        # No closes the question and nothing happened
        page.click('[data-testid="floofycrew-yesno-no"]')
        page.wait_for_selector('[data-testid="floofycrew-modal"]', state="detached", timeout=30000)
        assert "not confirmed (yes-no); nothing was changed" in page.inner_text('[data-testid="floofycrew-output"]')
        assert len(_audit_rows(harness)) == rows_before and not (data_home / "pending" / "govy").exists()
        # again: Yes, then the typed field — the submit stays disabled until the exact path is typed
        page.click('[data-testid="floofycrew-install-submit"]')
        page.wait_for_selector('[data-testid="floofycrew-yesno-yes"]', timeout=60000)
        page.click('[data-testid="floofycrew-yesno-yes"]')
        page.wait_for_selector('[data-testid="floofycrew-typed-modal"]', timeout=60000)
        assert "Type the exact path" in page.inner_text('[data-testid="floofycrew-typed-prompt"]')
        assert page.is_disabled('[data-testid="floofycrew-typed-submit"]')
        page.fill('[data-testid="floofycrew-typed-0"]', "security_policy")
        assert page.is_disabled('[data-testid="floofycrew-typed-submit"]'), "a partial path does not unlock the confirmation"
        assert page.locator('[data-testid="floofycrew-typed-submit"]').evaluate("b => b.tagName") == "BUTTON" and page.locator('[data-testid="floofycrew-typed-0"]').evaluate("i => i.tagName") == "INPUT", "typed in a text field, never a button (Requirement 11.4)"
        assert len(_audit_rows(harness)) == rows_before and not (data_home / "pending" / "govy").exists(), "still nothing changed"
        page.fill('[data-testid="floofycrew-typed-0"]', "security_policy.json")
        page.click('[data-testid="floofycrew-typed-submit"]')
        page.wait_for_function("() => document.querySelector('[data-testid=\"floofycrew-pending\"]')?.textContent.includes('govy')", timeout=60000)
        assert (data_home / "pending" / "govy").is_dir() and not (data_home / "mods" / "govy").exists(), "staged into pending/ (Requirement 7.6)"
        rows = _audit_rows(harness)[rows_before:]
        assert [r["op"] for r in rows] == ["governance-target-confirm", "agent-install", "install"], rows
        assert rows[0]["actor"] == "app" and rows[0]["detail"] == "typed at the prompt" and rows[0]["files"] == ["security_policy.json"]
        assert rows[-1]["actor"] == "app" and "GovernanceAltering" in rows[-1]["governanceFlags"] and "staged into" in rows[-1]["detail"]
        # apply now = the Loader's reload (POST /reload): the staged mod lands in mods/
        page.click('[data-testid="floofycrew-apply-now"]')
        deadline = time.time() + 120
        while time.time() < deadline and not (data_home / "mods" / "govy").is_dir():
            time.sleep(0.5)
        if not (data_home / "mods" / "govy").is_dir():
            raise AssertionError(f"apply now did not land the staged mod; output pane: {page.inner_text('[data-testid=\"floofycrew-output\"]')!r}; state pending: {client.get(f'{API}/state').json().get('pending')}")
        page.wait_for_function("() => !document.querySelector('[data-testid=\"floofycrew-pending\"]')", timeout=90000)
        removed = client.post(f"{API}/mods/govy/uninstall", {"now": True, "confirmations": {"yes": True}})
        assert removed.status == 200, removed.body[:300]

        # --- the one-time consent, on request: the same text, [ I AGREE ]; Esc declines ---
        consent_path = data_home / "consent.json"
        before = json.loads(consent_path.read_text(encoding="utf-8"))
        page.click('[data-testid="floofycrew-nav-settings"]')
        page.wait_for_selector('[data-testid="floofycrew-consent-open"]', timeout=60000)
        page.click('[data-testid="floofycrew-consent-open"]')
        page.wait_for_selector('[data-testid="floofycrew-consent-modal"]', timeout=60000)
        text = page.inner_text('[data-testid="floofycrew-consent-text"]')
        assert "FloofyCrew is UNOFFICIAL" in text and "the risk of every mod you install is yours" in text, "the same warning text as the CLI"
        assert page.inner_text('[data-testid="floofycrew-consent-agree"]') == "[ I AGREE ]"
        page.keyboard.press("Escape")
        page.wait_for_selector('[data-testid="floofycrew-modal"]', state="detached", timeout=30000)
        assert json.loads(consent_path.read_text(encoding="utf-8")) == before, "Esc declines: the record is untouched"
        page.click('[data-testid="floofycrew-consent-open"]')
        page.wait_for_selector('[data-testid="floofycrew-consent-agree"]', timeout=60000)
        page.click('[data-testid="floofycrew-consent-agree"]')
        page.wait_for_function("() => document.querySelector('[data-testid=\"floofycrew-settings-consent\"]')?.textContent.includes('through app')", timeout=60000)
        after = json.loads(consent_path.read_text(encoding="utf-8"))
        assert after["how"] == "app" and after["warningVersion"] == before["warningVersion"]
        assert any(r["op"] == "consent" and r["actor"] == "app" and r.get("how") == "app" for r in _audit_rows(harness))

        # --- a git reference: the unlisted-source line, Cancel first, then the explicit accept ---
        work = harness.gw.scratch / "src" / "gitmod"
        _write_mod(work, "unlisted-demo", [{"kind": "agent", "side": "gateway", "path": "agents/u.json"}], {"agents/u.json": json.dumps({"name": "unlisted-agent"})})
        env = {"GIT_AUTHOR_NAME": "tests", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "tests", "GIT_COMMITTER_EMAIL": "t@example.invalid", "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(work)}
        for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "release"], ["tag", "1.0.0"]):
            subprocess.run(["git", *args], cwd=str(work), env=env, check=True, capture_output=True)
        bare = harness.gw.scratch / "src" / "forge.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], env=env, check=True, capture_output=True)
        reference = f"{bare.as_uri()}@1.0.0"
        rows_before = len(_audit_rows(harness))
        page.click('[data-testid="floofycrew-nav-registry"]')
        page.wait_for_selector('[data-testid="floofycrew-reference-input"]', timeout=60000)
        page.fill('[data-testid="floofycrew-reference-input"]', reference)
        page.click('[data-testid="floofycrew-reference-submit"]')
        page.wait_for_selector('[data-testid="floofycrew-unlisted-modal"]', timeout=90000)
        assert "no curator review" in page.inner_text('[data-testid="floofycrew-disclosure-unlisted"]')
        assert "UNLISTED SOURCE" in page.inner_text('[data-testid="floofycrew-unlisted-prompt"]')
        page.click('[data-testid="floofycrew-unlisted-cancel"]')
        page.wait_for_selector('[data-testid="floofycrew-modal"]', state="detached", timeout=30000)
        assert len(_audit_rows(harness)) == rows_before and not (data_home / "pending" / "unlisted-demo").exists()
        page.click('[data-testid="floofycrew-reference-submit"]')
        page.wait_for_selector('[data-testid="floofycrew-unlisted-accept"]', timeout=90000)
        assert page.inner_text('[data-testid="floofycrew-unlisted-accept"]').startswith("I ACCEPT")
        page.click('[data-testid="floofycrew-unlisted-accept"]')
        page.wait_for_function("() => (document.querySelector('[data-testid=\"floofycrew-output\"]')?.textContent || '').includes('staged in pending/')", timeout=90000)
        row = _audit_rows(harness)[-1]
        assert row["op"] == "install" and row["mod"] == "unlisted-demo" and row["actor"] == "app" and row["tier"] == "unlisted" and row["unlistedSource"]["how"] == "typed"
        assert (data_home / "pending" / "unlisted-demo").is_dir()
        assert client.post(f"{API}/reload").status == 200
        removed = client.post(f"{API}/mods/unlisted-demo/uninstall", {"now": True, "confirmations": {"yes": True}})
        assert removed.status == 200, removed.body[:300]

        # --- --allow-unsigned on a registry source: the loosening warning, Keep first, then the explicit accept ---
        rows_before = len(_audit_rows(harness))
        page.fill('[data-testid="floofycrew-add-source-url"]', "http://127.0.0.1:9/registry/")
        page.fill('[data-testid="floofycrew-add-source-name"]', "loopdemo")
        page.check('[data-testid="floofycrew-add-source-unsigned"]')
        page.uncheck('[data-testid="floofycrew-add-source-refresh"]')
        page.click('[data-testid="floofycrew-add-source-submit"]')
        page.wait_for_selector('[data-testid="floofycrew-unsigned-modal"]', timeout=60000)
        assert "TRUST LOOSENED for http://127.0.0.1:9/registry/" in page.inner_text('[data-testid="floofycrew-unsigned-text"]')
        page.click('[data-testid="floofycrew-unsigned-keep"]')
        page.wait_for_selector('[data-testid="floofycrew-modal"]', state="detached", timeout=30000)
        assert len(_audit_rows(harness)) == rows_before
        registries = data_home / "registries.json"
        assert not registries.exists() or all(s.get("name") != "loopdemo" for s in json.loads(registries.read_text(encoding="utf-8"))["sources"])
        page.click('[data-testid="floofycrew-add-source-submit"]')
        page.wait_for_selector('[data-testid="floofycrew-unsigned-accept"]', timeout=60000)
        page.click('[data-testid="floofycrew-unsigned-accept"]')
        page.wait_for_selector('[data-testid="floofycrew-source-loopdemo"]', timeout=60000)
        assert "unsigned indexes accepted" in page.inner_text('[data-testid="floofycrew-source-loopdemo"]').lower()
        ops = [r["op"] for r in _audit_rows(harness)[rows_before:]]
        assert ops == ["registry-add", "registry-trust-loosened"], ops
        assert client.request("DELETE", f"{API}/registries/loopdemo").status == 200
    finally:
        context.close()




HEALTHY_PAGE = """
export default async function mount(container, api) {
  const config = await api.config.get();
  const input = document.createElement("input");
  input.setAttribute("data-testid", "ui-healthy-input");
  input.value = config.greeting || "";
  input.addEventListener("change", async () => {
    const saved = await api.config.set({ greeting: input.value });
    saved_el.textContent = "saved: " + JSON.stringify(saved);
  });
  const saved_el = document.createElement("div");
  saved_el.setAttribute("data-testid", "ui-healthy-saved");
  const button = document.createElement("button");
  button.setAttribute("data-testid", "ui-healthy-echo");
  button.textContent = "echo";
  const echo_el = document.createElement("pre");
  echo_el.setAttribute("data-testid", "ui-healthy-echo-out");
  button.addEventListener("click", async () => {
    const response = await api.routes.fetch("echo", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ from: "page" }) });
    echo_el.textContent = JSON.stringify(await response.json());
  });
  const tokens = document.createElement("div");
  tokens.setAttribute("data-testid", "ui-healthy-theme");
  tokens.textContent = JSON.stringify({ current: api.theme.current(), hasBg: Object.keys(api.theme.tokens()).length > 0, routes: await api.routes.list(), state: (await api.state()).active });
  container.append(input, saved_el, button, echo_el, tokens);
  window.__uiHealthyMounted = (window.__uiHealthyMounted || 0) + 1;
  return () => { window.__uiHealthyUnmounted = (window.__uiHealthyUnmounted || 0) + 1; };
}
"""

HEALTHY_HOOK = """
def activate(ctx):
    def echo(request):
        return {"echo": request.json(), "config": ctx.config.to_dict(), "host": ctx.host.version}

    ctx.routes.add("POST", "echo", echo)


def deactivate(ctx):
    pass
"""

BROKEN_PAGE = """
export default function mount(container, api) {
  container.textContent = "about to explode";
  throw new Error("this page explodes on mount, on purpose");
}
"""


def test_5c_mod_pages_are_isolated(harness: Harness):
    """Task 11.5 (Requirement 16.4): a mod's ui part is served same-origin and mounted with floofy.mod(id) — config get/set
    persisted in .floofy/config.json, the mod's own route, theme tokens, state — and a page that throws is isolated by the
    error boundary with a Disable button; the manager page never goes down."""
    client = harness.gw.client()
    data_home = harness.gw.data_home
    write_test_mod(harness.gw, "ui-healthy", {"ui/page.mjs": HEALTHY_PAGE, "hook.py": HEALTHY_HOOK}, [{"kind": "ui", "side": "spa", "path": "ui/", "entry": "ui/page.mjs", "title": "Healthy settings"}, {"kind": "python-hook", "side": "gateway", "path": "hook.py", "module": "hook"}])
    write_test_mod(harness.gw, "ui-broken", {"ui/page.mjs": BROKEN_PAGE}, [{"kind": "ui", "side": "spa", "path": "ui/", "entry": "ui/page.mjs", "title": "Broken page"}])
    assert client.post(f"{API}/reload").status == 200
    state = client.get(f"{API}/state").json()
    assert state["mods"]["ui-healthy"]["active"] is True and state["mods"]["ui-healthy"]["routes"] == [{"method": "POST", "path": "echo"}]
    assert state["mods"]["ui-broken"]["active"] is True, "a ui part is inert until opened: the mod lands active"
    served = client.get(f"{API}/ui/mods/ui-healthy/ui/page.mjs")
    assert served.status == 200 and served.headers["content-type"].startswith("text/javascript") and "api.config.get()" in served.body.decode("utf-8")
    assert client.get(f"{API}/ui/mods/ui-healthy/floofy.json").status == 404 and client.get(f"{API}/ui/mods/ui-healthy/hook.py").status == 200, "manifest-listed files only"
    context, page = harness.page()
    try:
        page.goto(dashboard_url(harness.gw, "/apps/floofycrew"), wait_until="load", timeout=60000)
        page.wait_for_selector('[data-testid="floofycrew-mod-pages"]', timeout=60000)
        listing = page.inner_text('[data-testid="floofycrew-mod-pages"]')
        assert "Open Healthy settings" in listing and "Open Broken page" in listing, listing
        # the healthy page: mounted with floofy.mod(id); config round-trips through the Loader and reaches the mod's Python side
        page.click('[data-testid="floofycrew-open-page-ui-healthy"]')
        page.wait_for_selector('[data-testid="ui-healthy-input"]', timeout=60000)
        assert page.evaluate("() => location.hash") == "#/mods/ui-healthy"
        theme = json.loads(page.inner_text('[data-testid="ui-healthy-theme"]'))
        assert theme["state"] is True and theme["routes"] == [{"method": "POST", "path": "echo"}] and theme["hasBg"] is True and theme["current"]["theme"], theme
        page.fill('[data-testid="ui-healthy-input"]', "hello from the page")
        page.press('[data-testid="ui-healthy-input"]', "Tab")
        page.wait_for_function("() => document.querySelector('[data-testid=\"ui-healthy-saved\"]')?.textContent.includes('hello from the page')", timeout=30000)
        assert json.loads((data_home / "mods" / "ui-healthy" / ".floofy" / "config.json").read_text(encoding="utf-8")) == {"greeting": "hello from the page"}
        page.click('[data-testid="ui-healthy-echo"]')
        page.wait_for_function("() => document.querySelector('[data-testid=\"ui-healthy-echo-out\"]')?.textContent.includes('hello from the page')", timeout=30000)
        echoed = json.loads(page.inner_text('[data-testid="ui-healthy-echo-out"]'))
        assert echoed == {"echo": {"from": "page"}, "config": {"greeting": "hello from the page"}, "host": "0.7.0.5"}, echoed
        # leaving the page runs the cleanup; coming back re-mounts with the saved value
        page.click('[data-testid="floofycrew-modpage-back"]')
        page.wait_for_selector('[data-testid="floofycrew-mod-pages"]', timeout=30000)
        page.click('[data-testid="floofycrew-open-page-ui-healthy"]')
        page.wait_for_selector('[data-testid="ui-healthy-input"]', timeout=60000)
        assert page.input_value('[data-testid="ui-healthy-input"]') == "hello from the page"
        assert page.evaluate("() => [window.__uiHealthyMounted, window.__uiHealthyUnmounted]") == [2, 1]
        # the broken page: caught by the boundary, shown with a Disable button; the shell stays up
        page.click('[data-testid="floofycrew-modpage-back"]')
        page.wait_for_selector('[data-testid="floofycrew-open-page-ui-broken"]', timeout=30000)
        page.click('[data-testid="floofycrew-open-page-ui-broken"]')
        page.wait_for_selector('[data-testid="floofycrew-modpage-fault"]', timeout=60000)
        fault = page.inner_text('[data-testid="floofycrew-modpage-fault"]')
        assert "failed during mount" in fault and "explodes on mount, on purpose" in fault, fault
        assert page.locator('[data-testid="floofycrew-nav"]').count() == 1 and page.locator('[data-testid="floofycrew-banner"]').count() == 1, "the manager page is still there"
        page.click('[data-testid="floofycrew-nav-registry"]')
        page.wait_for_selector('[data-testid="floofycrew-sources"]', timeout=30000)
        page.click('[data-testid="floofycrew-nav-mods"]')
        page.wait_for_selector('[data-testid="floofycrew-open-page-ui-broken"]', timeout=30000)
        page.click('[data-testid="floofycrew-open-page-ui-broken"]')
        page.wait_for_selector('[data-testid="floofycrew-modpage-disable"]', timeout=60000)
        page.click('[data-testid="floofycrew-modpage-disable"]')
        page.wait_for_selector('[data-testid="floofycrew-mod-pages"]', timeout=60000)
        page.wait_for_function("() => document.querySelector('[data-testid=\"floofycrew-open-page-ui-broken\"]')?.disabled === true", timeout=60000)
        state = client.get(f"{API}/state").json()
        assert state["mods"]["ui-broken"]["active"] is False and state["mods"]["ui-broken"]["reason"] == "UserDisabled"
        assert state["mods"]["ui-healthy"]["active"] is True, "the other page's mod is unaffected"
        assert client.get(f"{API}/ui/mods/ui-broken/ui/page.mjs").status == 404, "a disabled mod's page is no longer served"
        disabled = [r for r in _audit_rows(harness) if r["op"] == "disable" and r["mod"] == "ui-broken"]
        assert disabled and disabled[-1]["actor"] == "app", "the Disable button ran `floofy disable` (the reload's Patcher rows follow it)"
    finally:
        context.close()
    for mod_id in ("ui-healthy", "ui-broken"):
        removed = client.post(f"{API}/mods/{mod_id}/uninstall", {"now": True, "confirmations": {"yes": True}})
        assert removed.status == 200, removed.body[:300]




SETTINGS_DEMO_DIR = REPO_ROOT / "mods" / "settings-demo"


class _LocalRegistry:
    """A signed registry on the loopback (index.json + index.json.sig + the mod archive), the way the App adds a source."""

    def __init__(self, mod_dir: Path, scratch: Path):
        import io
        import threading
        import zipfile
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        from floofy_core.signing import generate_keypair, sign_detached

        manifest = json.loads((mod_dir / "floofy.json").read_text(encoding="utf-8"))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for path in sorted(mod_dir.rglob("*")):
                if path.is_file() and ".floofy" not in path.parts and "__pycache__" not in path.parts:
                    archive.write(path, f"{manifest['id']}/{path.relative_to(mod_dir).as_posix()}")
        payload = buffer.getvalue()
        import hashlib

        self.pair = generate_keypair("manager App smoke test")
        self.public_key = self.pair.public_record()["publicKey"]
        files: dict[str, bytes] = {}
        server = ThreadingHTTPServer(("127.0.0.1", 0), type("Handler", (BaseHTTPRequestHandler,), {"log_message": lambda *_a: None, "do_GET": lambda h: _serve(h, files)}))
        self.server = server
        self.base = f"http://127.0.0.1:{server.server_address[1]}/registry/"
        archive_name = f"{manifest['id']}-{manifest['version']}.zip"
        index = {
            "schema": 1,
            "source": "local-smoke",
            "generatedAt": "2026-09-20T00:00:00Z",
            "mods": [
                {
                    "id": manifest["id"],
                    "name": manifest["name"],
                    "description": manifest["description"],
                    "authors": manifest["authors"],
                    "tags": manifest["tags"],
                    "repo": "https://forge.example/settings-demo",
                    "versions": [{"version": manifest["version"], "kirocrew": manifest["kirocrew"]["version"], "editions": manifest["kirocrew"]["editions"], "compat": {"0.7.0.5": "tested"}, "dependencies": manifest["dependsOn"], "kinds": sorted({p["kind"] for p in manifest["parts"]}), "files": [{"url": f"{self.base}{archive_name}", "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}], "changelog": "https://forge.example/settings-demo/releases/1.0.0"}],
                }
            ],
        }
        files["/registry/index.json"] = json.dumps(index).encode("utf-8")
        files["/registry/index.json.sig"] = json.dumps(sign_detached(index, self.pair).to_dict()).encode("utf-8")
        files[f"/registry/{archive_name}"] = payload
        threading.Thread(target=server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _serve(handler, files: dict[str, bytes]) -> None:
    data = files.get(handler.path)
    if data is None:
        handler.send_response(404)
        handler.end_headers()
        return
    handler.send_response(200)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def test_6_manager_app_smoke(harness: Harness):
    """Task 11.7 (Requirement 16.7): open the App, add a local signed registry, install settings-demo from its record
    through the confirmation modal with "apply now", enable it, open its settings page, set a value and see the mod's
    backend echo it, then disable the mod through the page's button."""
    client = harness.gw.client()
    data_home = harness.gw.data_home
    registry = _LocalRegistry(SETTINGS_DEMO_DIR, harness.gw.scratch)
    context, page = harness.page()
    started = time.time()
    try:
        page.goto(dashboard_url(harness.gw, "/apps/floofycrew"), wait_until="load", timeout=60000)
        page.wait_for_selector('[data-testid="floofycrew-nav"]', timeout=60000)
        assert page.locator('[data-testid="floofycrew-mod-row-settings-demo"]').count() == 0

        # 1. the local registry, added through the Registry page with its public key pinned; the index verifies
        page.click('[data-testid="floofycrew-nav-registry"]')
        page.wait_for_selector('[data-testid="floofycrew-add-source-url"]', timeout=60000)
        page.fill('[data-testid="floofycrew-add-source-url"]', registry.base)
        page.fill('[data-testid="floofycrew-add-source-name"]', "local")
        page.fill('[data-testid="floofycrew-add-source-key"]', registry.public_key)
        assert page.is_checked('[data-testid="floofycrew-add-source-refresh"]')
        page.click('[data-testid="floofycrew-add-source-submit"]')
        page.wait_for_selector('[data-testid="floofycrew-source-local"]', timeout=60000)
        source_text = page.inner_text('[data-testid="floofycrew-source-local"]')
        assert "UNSIGNED" not in source_text, source_text
        registries = json.loads((data_home / "registries.json").read_text(encoding="utf-8"))
        local = next(s for s in registries["sources"] if s.get("name") == "local")
        assert local["url"] == registry.base and local.get("publicKey") == registry.public_key and not local.get("allowUnsigned")
        summary = client.get(f"{API}/registry").json()
        fetched = next(s for s in summary["sources"] if s.get("name") == "local" or s.get("label") == "local")
        assert fetched["cache"]["usable"] is True and fetched["cache"]["signature"]["status"] == "verified" and fetched["cache"]["signature"]["keyId"] == registry.pair.key_id, fetched

        # 2. install from the record: the disclosure, the python-hook confirmation, "apply now"
        page.fill('[data-testid="floofycrew-search-input"]', "settings")
        page.click('[data-testid="floofycrew-search-submit"]')
        page.wait_for_selector('[data-testid="floofycrew-hit-settings-demo"]', timeout=60000)
        assert "tier: tested" in page.inner_text('[data-testid="floofycrew-hit-tier-settings-demo"]')
        assert "works here: 1.0.0 (tested)" in page.inner_text('[data-testid="floofycrew-hit-compat-settings-demo"]')
        page.click('[data-testid="floofycrew-hit-install-settings-demo"]')
        page.wait_for_selector('[data-testid="floofycrew-yesno-modal"]', timeout=90000)
        disclosure = page.inner_text('[data-testid="floofycrew-disclosure"]')
        assert "settings-demo 1.0.0" in disclosure and "ui/spa" in disclosure and "python-hook/gateway" in disclosure, disclosure
        assert "no remote hosts declared" in page.inner_text('[data-testid="floofycrew-disclosure-network"]')
        assert "installs the mod enabled" in disclosure, "the disclosure says the confirmed install lands enabled (Requirement 11.7)"
        assert page.is_checked('[data-testid="floofycrew-staging-staged"]'), "staged is the default (Requirement 16.5)"
        page.check('[data-testid="floofycrew-staging-now"]')
        page.click('[data-testid="floofycrew-yesno-yes"]')
        deadline = time.time() + 120
        while time.time() < deadline and not (data_home / "mods" / "settings-demo" / "floofy.json").is_file():
            time.sleep(0.5)
        assert (data_home / "mods" / "settings-demo" / "floofy.json").is_file(), f"apply now did not land the mod; output: {page.inner_text('[data-testid=\"floofycrew-output\"]')!r}"
        assert not (data_home / "pending" / "settings-demo").exists()
        source = json.loads((data_home / "mods" / "settings-demo" / ".floofy" / "source.json").read_text(encoding="utf-8"))
        assert source["source"] == "registry" and source["ref"] == "settings-demo@1.0.0" and source["tier"] == "listed", source  # `tested` is what the matrix row lifts it to
        install_rows = [r for r in _audit_rows(harness) if r["op"] == "install" and r["mod"] == "settings-demo"]
        assert install_rows and install_rows[-1]["actor"] == "app" and install_rows[-1]["tier"] == "listed" and install_rows[-1]["source"]["ref"] == "settings-demo@1.0.0", install_rows[-1]
        state = client.get(f"{API}/state").json()
        assert state["mods"]["settings-demo"]["active"] is True, "a confirmed install lands enabled and active (Requirement 11.7)"

        # 3. already enabled on the Mods page (no manual step, Requirement 11.7); open its settings page
        page.click('[data-testid="floofycrew-nav-mods"]')
        page.wait_for_selector('[data-testid="floofycrew-mod-row-settings-demo"]', timeout=60000)
        page.wait_for_function("() => document.querySelector('[data-testid=\"floofycrew-enabled-settings-demo\"]')?.textContent.startsWith('enabled')", timeout=90000)
        page.wait_for_function("() => document.querySelector('[data-testid=\"floofycrew-open-page-settings-demo\"]')?.disabled === false", timeout=60000)
        state = client.get(f"{API}/state").json()["mods"]["settings-demo"]
        assert state["active"] is True and state["routes"] == [{"method": "GET", "path": "echo"}, {"method": "POST", "path": "echo"}]
        page.click('[data-testid="floofycrew-open-page-settings-demo"]')
        page.wait_for_selector('[data-testid="settings-demo-page"]', timeout=60000)
        assert page.evaluate("() => location.hash") == "#/mods/settings-demo"
        assert page.get_attribute('[data-testid="floofycrew-modpage-container-settings-demo"]', "data-mounted") == "1"
        assert "Settings demo" in page.inner_text('[data-testid="floofycrew-modpage-settings-demo"]')
        assert "Loader verdict: active" in page.inner_text('[data-testid="settings-demo-state"]')
        assert page.input_value('[data-testid="settings-demo-greeting"]') == "Hello from settings-demo", "the default before anything is stored"

        # 4. set a value: config.set through the Loader; the mod's backend echoes what the page stored
        page.fill('[data-testid="settings-demo-greeting"]', "hello from the smoke test")
        page.press('[data-testid="settings-demo-greeting"]', "Tab")
        page.wait_for_function("() => document.querySelector('[data-testid=\"settings-demo-status\"]')?.textContent.startsWith('saved:')", timeout=30000)
        page.check('[data-testid="settings-demo-notify"]')
        page.wait_for_function("() => document.querySelector('[data-testid=\"settings-demo-status\"]')?.textContent.includes('\"notify\":true')", timeout=30000)
        assert json.loads((data_home / "mods" / "settings-demo" / ".floofy" / "config.json").read_text(encoding="utf-8")) == {"greeting": "hello from the smoke test", "notify": True}
        page.click('[data-testid="settings-demo-echo"]')
        page.wait_for_function("() => document.querySelector('[data-testid=\"settings-demo-echo-out\"]')?.textContent.includes('hello from the smoke test')", timeout=30000)
        echoed = json.loads(page.inner_text('[data-testid="settings-demo-echo-out"]'))
        assert echoed["mod"] == "settings-demo" and echoed["config"] == {"greeting": "hello from the smoke test", "notify": True} and echoed["echo"] == {"from": "settings page"} and echoed["host"]["version"] == "0.7.0.5", echoed

        # 5. disable the mod through the page's own button: back at Mods, disabled, the page no longer served
        page.click('[data-testid="floofycrew-modpage-disable-mod"]')
        page.wait_for_selector('[data-testid="floofycrew-mod-row-settings-demo"]', timeout=90000)
        page.wait_for_function("() => document.querySelector('[data-testid=\"floofycrew-enabled-settings-demo\"]')?.textContent.startsWith('disabled')", timeout=90000)
        assert page.evaluate("() => location.hash") == "#/mods"
        state = client.get(f"{API}/state").json()["mods"]["settings-demo"]
        assert state["active"] is False and state["reason"] == "UserDisabled" and state["routes"] == []
        assert client.get(f"{API}/ui/mods/settings-demo/ui/page.mjs").status == 404
        assert client.get(f"{API}/mods/settings-demo/config").json()["config"] == {"greeting": "hello from the smoke test", "notify": True}, "the settings survive the disable"
        ops = [(r["op"], r["actor"]) for r in _audit_rows(harness) if r.get("mod") == "settings-demo"]
        assert ("install", "app") in ops and ("disable", "app") in ops, ops  # no enable row: the install landed enabled (Requirement 11.7)
        assert page.locator('[data-testid="floofycrew-nav"]').count() == 1 and page.locator('[data-testid="floofycrew-banner"]').count() == 1
        print(f"manager App smoke: {time.time() - started:.1f}s")
    finally:
        context.close()
        registry.close()
        if (data_home / "mods" / "settings-demo").exists() or (data_home / "pending" / "settings-demo").exists():
            removed = client.post(f"{API}/mods/settings-demo/uninstall", {"now": True, "confirmations": {"yes": True}})
            assert removed.status == 200, removed.body[:300]
        client.request("DELETE", f"{API}/registries/local")



def test_7_ui_polish_and_the_restart_button(harness: Harness):
    """1.1.6: the tab frame stays transparent after a tab was clicked and left, pages are gapped columns of cards, Sources is a
    table, Profiles explains itself, and the red Restart KiroCrew button asks first — declining leaves no trace."""
    client = harness.gw.client()
    context, page = harness.page()
    registry = None
    try:
        page.goto(dashboard_url(harness.gw, "/apps/floofycrew"), wait_until="load", timeout=60000)
        page.wait_for_selector('[data-testid="floofycrew-nav"]', timeout=60000)
        transparent = {"rgba(0, 0, 0, 0)", "transparent"}
        # the tab border bug: click Registry, come back to Mods, the Registry tab must not keep a frame
        page.click('[data-testid="floofycrew-nav-registry"]')
        page.wait_for_selector('[data-testid="floofycrew-page-registry"] [data-testid="floofycrew-sources"]', timeout=60000)
        page.click('[data-testid="floofycrew-nav-mods"]')
        page.wait_for_selector('[data-testid="floofycrew-page-mods"]', timeout=60000)
        left_tab = page.evaluate("() => { const s = getComputedStyle(document.querySelector('[data-testid=\"floofycrew-nav-registry\"]')); return [s.borderTopColor, s.borderLeftColor, s.borderRightColor, s.borderBottomColor]; }")
        assert all(c in transparent for c in left_tab), f"a tab that was clicked once keeps a grey frame: {left_tab}"
        active_tab = page.evaluate("() => getComputedStyle(document.querySelector('[data-testid=\"floofycrew-nav-mods\"]')).borderTopColor")
        assert active_tab not in transparent, "the active tab is framed in the accent colour"
        # pages are flex columns with the card gap; the page leaves room below the last card
        assert page.evaluate("() => getComputedStyle(document.querySelector('[data-testid=\"floofycrew-page-mods\"]')).display") == "flex"
        gap = page.evaluate("() => parseFloat(getComputedStyle(document.querySelector('[data-testid=\"floofycrew-page-mods\"]')).rowGap)")
        assert gap >= 16, gap
        bottom = page.evaluate("() => parseFloat(getComputedStyle(document.querySelector('[data-testid=\"floofycrew-manager\"]')).paddingBottom)")
        assert bottom >= 48, bottom
        page.screenshot(path=str(SHOTS / "manager-mods-1.1.6.png"), full_page=True)
        # Sources is a table with the CLI's columns (a local signed registry as the row); Profiles carries its one-line intro
        registry = _LocalRegistry(SETTINGS_DEMO_DIR, harness.gw.scratch)
        added = client.post(f"{API}/registries", {"url": registry.base, "name": "polish", "publicKey": registry.public_key, "refresh": True})
        assert added.status == 200, added.body[:300]
        page.click('[data-testid="floofycrew-nav-registry"]')
        page.wait_for_selector('[data-testid="floofycrew-sources-table"]', timeout=60000)
        heads = page.eval_on_selector_all('[data-testid="floofycrew-sources-table"] th', "els => els.map(e => e.textContent)")
        assert heads == ["Source", "Trust", "Signature policy", "Last refresh", "Actions"], heads
        row = page.inner_text('[data-testid="floofycrew-source-polish"]')
        assert "signature required" in row and "verified" in row, row
        page.screenshot(path=str(SHOTS / "manager-registry-1.1.6.png"), full_page=True)
        page.click('[data-testid="floofycrew-nav-profiles"]')
        page.wait_for_selector('[data-testid="floofycrew-profiles-intro"]', timeout=60000)
        assert "named snapshot" in page.inner_text('[data-testid="floofycrew-profiles-intro"]')
        # squared corners: cards, the banner, buttons and badges all sit on the one small radius
        radii = page.evaluate("() => ['[data-testid=\"floofycrew-profiles\"]', '[data-testid=\"floofycrew-banner\"]', '[data-testid=\"floofycrew-restart-header\"]', '[data-testid=\"floofycrew-nav-mods\"]'].map(s => parseFloat(getComputedStyle(document.querySelector(s)).borderTopLeftRadius))")
        assert all(0 <= r <= 4 for r in radii), radii
        # the footer: a rule, the unofficial statement, the author; "don't show this again" hides the banner and survives a reload
        footer = page.inner_text('[data-testid="floofycrew-footer"]')
        assert "not affiliated" in footer and "author: Oscar Tseng" in footer, footer
        assert page.evaluate("() => getComputedStyle(document.querySelector('[data-testid=\"floofycrew-footer\"]')).borderTopStyle") == "solid"
        page.click('[data-testid="floofycrew-banner-dismiss"]')
        page.wait_for_selector('[data-testid="floofycrew-banner"]', state="detached", timeout=30000)
        page.reload(wait_until="load")
        page.wait_for_selector('[data-testid="floofycrew-footer"]', timeout=60000)
        assert page.locator('[data-testid="floofycrew-banner"]').count() == 0, "the preference is remembered on this browser"
        assert "not affiliated" in page.inner_text('[data-testid="floofycrew-footer"]'), "the unofficial statement stays (Requirement 11.8)"
        page.click('[data-testid="floofycrew-nav-settings"]')
        page.wait_for_selector('[data-testid="floofycrew-banner-toggle"]', timeout=60000)
        page.click('[data-testid="floofycrew-banner-toggle"]')
        page.wait_for_selector('[data-testid="floofycrew-banner"]', timeout=30000)
        # the red button: header, Settings; it asks through the modal and a decline changes nothing
        rows_before = len(_audit_rows(harness))
        assert page.locator('[data-testid="floofycrew-restart-header"]').count() == 1
        colour = page.evaluate("() => getComputedStyle(document.querySelector('[data-testid=\"floofycrew-restart-header\"]')).backgroundColor")
        assert colour == "rgb(255, 95, 115)", colour  # PALETTE.danger
        page.click('[data-testid="floofycrew-nav-settings"]')
        page.wait_for_selector('[data-testid="floofycrew-settings-restart"]', timeout=60000)
        page.screenshot(path=str(SHOTS / "manager-settings-1.1.6.png"), full_page=True)
        page.click('[data-testid="floofycrew-restart-card"]')
        page.wait_for_selector('[data-testid="floofycrew-yesno-modal"]', timeout=60000)
        assert "Restart the KiroCrew gateway now?" in page.inner_text('[data-testid="floofycrew-yesno-prompt"]')
        page.screenshot(path=str(SHOTS / "manager-restart-modal-1.1.6.png"))
        page.keyboard.press("Escape")
        page.wait_for_selector('[data-testid="floofycrew-yesno-modal"]', state="detached", timeout=30000)
        assert page.locator('[data-testid="floofycrew-restarting"]').count() == 0
        assert len(_audit_rows(harness)) == rows_before, "a declined restart leaves no audit row"
        assert "not confirmed" in page.inner_text('[data-testid="floofycrew-output"]')
        # the route itself, without the answer: the question, not a restart
        asked = client.post(f"{API}/host/restart", {})
        assert asked.status == 409 and asked.json()["confirmation"]["kind"] == "yes-no", asked.body[:300]
        assert client.get(f"{API}/health").status == 200, "the gateway is untouched"
    finally:
        context.close()
        if registry is not None:
            registry.close()
            client.request("DELETE", f"{API}/registries/polish")



def test_8_mod_page_dialogs_are_in_document(harness: Harness):
    """A mod page never uses the browser's pop-ups (the desktop shell has none): custom-themes' New blank theme asks
    for the name and slug through `api.dialog` — the App's own overlay — Escape cancels, Enter creates, the slug follows
    the name, and Delete confirms the same way. window.prompt/confirm/alert are poisoned for the whole run."""
    client = harness.gw.client()
    poison = "for (const name of ['alert', 'confirm', 'prompt']) window[name] = () => { throw new Error('browser pop-up ' + name + ' used'); };"
    context, page = harness.page(init=poison)
    modal = '[data-testid="floofycrew-modal"][data-mod="custom-themes"]'
    try:
        page.goto(dashboard_url(harness.gw, "/apps/floofycrew"), wait_until="load", timeout=60000)
        page.wait_for_selector('[data-testid="floofycrew-open-page-custom-themes"]', timeout=60000)
        page.click('[data-testid="floofycrew-open-page-custom-themes"]')
        page.wait_for_selector('[data-testid="custom-themes-new-blank"]', timeout=60000)
        library_before = client.get(f"{API}/mods/custom-themes/api/themes").json()["library"]
        assert not any(t["slug"] == "dialog-smoke" for t in library_before)

        # Escape cancels: nothing created
        page.click('[data-testid="custom-themes-new-blank"]')
        page.wait_for_selector(modal, timeout=30000)
        assert page.get_attribute(modal, "data-kind") == "mod-prompt"
        assert page.evaluate("() => document.activeElement?.dataset.testid") == "floofycrew-mod-dialog-field-name", "focus lands in the first field"
        page.keyboard.press("Escape")
        page.wait_for_selector(modal, state="detached", timeout=30000)
        assert len(client.get(f"{API}/mods/custom-themes/api/themes").json()["library"]) == len(library_before)

        # the slug follows the name; an invalid slug disables Create; Enter submits
        page.click('[data-testid="custom-themes-new-blank"]')
        page.wait_for_selector(modal, timeout=30000)
        page.screenshot(path=str(SHOTS / "custom-themes-new-dialog.png"))
        page.fill('[data-testid="floofycrew-mod-dialog-field-name"]', "Dialog Smoke")
        assert page.input_value('[data-testid="floofycrew-mod-dialog-field-slug"]') == "dialog-smoke"
        page.fill('[data-testid="floofycrew-mod-dialog-field-slug"]', "Not Valid!")
        assert page.is_disabled('[data-testid="floofycrew-mod-dialog-ok"]')
        assert page.inner_text('[data-testid="floofycrew-mod-dialog-error-slug"]').startswith("lower-case")
        page.fill('[data-testid="floofycrew-mod-dialog-field-slug"]', "dialog-smoke")
        page.fill('[data-testid="floofycrew-mod-dialog-field-name"]', "Dialog Smoke Two")
        assert page.input_value('[data-testid="floofycrew-mod-dialog-field-slug"]') == "dialog-smoke", "an edited slug stops following the name"
        assert page.is_enabled('[data-testid="floofycrew-mod-dialog-ok"]')
        page.press('[data-testid="floofycrew-mod-dialog-field-slug"]', "Enter")
        page.wait_for_selector(modal, state="detached", timeout=30000)
        page.wait_for_selector('[data-testid="custom-themes-library"] [data-slug="dialog-smoke"]', timeout=30000)
        created = client.get(f"{API}/mods/custom-themes/api/themes/dialog-smoke").json()["theme"]
        assert created["manifest"]["name"] == "Dialog Smoke Two" and created["slug"] == "dialog-smoke", created

        # Delete: the confirmation is the same overlay; No keeps it, Yes removes it
        page.wait_for_selector('[data-testid="custom-themes-delete"]', timeout=30000)
        page.click('[data-testid="custom-themes-delete"]')
        page.wait_for_selector(modal, timeout=30000)
        assert page.get_attribute(modal, "data-kind") == "mod-confirm"
        assert "Delete dialog-smoke from the library?" in page.inner_text('[data-testid="floofycrew-mod-dialog-text"]')
        page.click('[data-testid="floofycrew-mod-dialog-cancel"]')
        page.wait_for_selector(modal, state="detached", timeout=30000)
        assert client.get(f"{API}/mods/custom-themes/api/themes/dialog-smoke").status == 200
        page.click('[data-testid="custom-themes-delete"]')
        page.wait_for_selector(modal, timeout=30000)
        page.click('[data-testid="floofycrew-mod-dialog-ok"]')
        page.wait_for_selector(modal, state="detached", timeout=30000)
        page.wait_for_selector('[data-testid="custom-themes-library"] [data-slug="dialog-smoke"]', state="detached", timeout=30000)
        assert client.get(f"{API}/mods/custom-themes/api/themes/dialog-smoke").status == 404
        assert "browser pop-up" not in page.inner_text('[data-testid="floofycrew-modpage-custom-themes"]')
    finally:
        context.close()
        if client.get(f"{API}/mods/custom-themes/api/themes/dialog-smoke").status == 200:
            client.request("DELETE", f"{API}/mods/custom-themes/api/themes/dialog-smoke")
