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
    # FLOOFY_ALLOW_LOCAL_GIT: the confirmations test installs from a file:// bare repository (the test-suite switch of task 7.7)
    gw = ScratchGateway(payload, scratch, extra_env={"FLOOFY_ALLOW_LOCAL_GIT": "1"})
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
        assert "rimuru-branding 1.0.0" in row and "active" in row and "theme/gateway" in row and "modifies payload files" in row, row
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
        # apply now = the Loader's reload: the staged mod lands
        page.click('[data-testid="floofycrew-apply-now"]')
        page.wait_for_function("() => !document.querySelector('[data-testid=\"floofycrew-pending\"]')", timeout=60000)
        assert (data_home / "mods" / "govy").is_dir()
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
        assert "UNSIGNED INDEXES ACCEPTED" in page.inner_text('[data-testid="floofycrew-source-loopdemo"]')
        ops = [r["op"] for r in _audit_rows(harness)[rows_before:]]
        assert ops == ["registry-add", "registry-trust-loosened"], ops
        assert client.request("DELETE", f"{API}/registries/loopdemo").status == 200
    finally:
        context.close()
