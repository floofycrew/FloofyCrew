"""Playwright tests of the ``display-comfort`` mod against a real dashboard.

Own scratch gateway (payload copy, scratch homes, ``FLOOFY_NO_ADAPTERS=1``) with
only ``display-comfort`` installed, one headless Chromium:

1. **runtime + settings page round trip** — the runtime part holds the rules
   ``<style>``; the page's page-zoom input zooms the document live, persists to
   the mod's config and the localStorage cache; the chat-scale input sets the
   custom property; a WARM reload has the zoom back at ``DOMContentLoaded``
   (the boot hook, before React) — no flash of the default sizes;
2. **cold client and disable** — an empty-storage client paints stock at
   ``DOMContentLoaded`` (fail-open) and catches up to the authoritative config
   once the host is ready; disabling the mod clears every lever.

Skipped without a payload copy. The copy's ``index.html`` is patched on purpose
and restored byte-identically at teardown (``ShellGuard``).
"""
from __future__ import annotations

import json
import time

import pytest

from floofy_loader.paths import FloofyPaths

from loader_testing import REPO_ROOT, ScratchGateway, ShellGuard, build_loader_app, fresh_scratch, scratch_payload
from spa_testing import API, consent, dashboard_url, install_loader_app, install_mod

pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.skipif(scratch_payload() is None, reason="no payload copy under .scratch (set FLOOFY_SCRATCH_PAYLOAD)")

MOD_DIR = REPO_ROOT / "mods" / "display-comfort"

#: What the document looked like at PARSE END (``readyState`` → ``interactive``):
#: after the inline boot hook (which runs during parse) but before the deferred SPA
#: bundle — so the snapshot isolates the boot hook's work from the runtime part's
#: catch-up, which on a fast localhost can beat even DOMContentLoaded. The snapshot
#: is per-document; the sessionStorage list accumulates one per document because a
#: cold visit navigates more than once (the host's token handshake reloads, then
#: routes to /chat) — only the FIRST document of a visit is genuinely cold.
PROBE_INIT = """
window.__dcProbe = {};
document.addEventListener('readystatechange', () => {
  if (document.readyState !== 'interactive' || window.__dcProbe.atParseEnd) return;
  const el = document.documentElement;
  const snap = {
    zoom: el.style.zoom || '',
    chatScale: el.style.getPropertyValue('--floofy-dc-chat-scale').trim(),
    composer: el.getAttribute('data-floofy-dc-composer') || '',
  };
  window.__dcProbe.atParseEnd = snap;
  try {
    const frames = JSON.parse(sessionStorage.getItem('__dcProbeFrames') || '[]');
    frames.push(snap);
    sessionStorage.setItem('__dcProbeFrames', JSON.stringify(frames));
  } catch (storageError) {}
});
"""


class Harness:
    def __init__(self, gw: ScratchGateway, guard: ShellGuard, browser):
        self.gw = gw
        self.guard = guard
        self.browser = browser

    def page(self, *, init: str | None = None):
        context = self.browser.new_context(viewport={"width": 1280, "height": 800}, color_scheme="dark")
        page = context.new_page()
        if init:
            page.add_init_script(init)
        return context, page

    def open(self, page, path: str = "/"):
        page.goto(dashboard_url(self.gw, path), wait_until="load", timeout=60000)
        page.wait_for_function("() => window.floofy && window.floofy.events.inspect().history.some((h) => h.name === 'host.ready')", timeout=30000)

    def probe(self, page) -> dict:
        """The current document's first-frame probe, once it exists.

        A cold first visit navigates more than once (the host's token handshake
        reloads the same URL before routing), so an immediate ``evaluate`` can land
        on a document still parsing — wait for the listener to have fired.
        """
        page.wait_for_function("() => window.__dcProbe && window.__dcProbe.atParseEnd !== undefined", timeout=15000)
        return page.evaluate("() => window.__dcProbe.atParseEnd")


@pytest.fixture(scope="module")
def harness():
    from playwright.sync_api import sync_playwright

    payload = scratch_payload()
    assert payload is not None
    scratch = fresh_scratch("display-comfort")
    app_dir = build_loader_app(scratch / "app" / "floofycrew")
    gw = ScratchGateway(payload, scratch)
    guard = ShellGuard(payload, gw.data_home)
    FloofyPaths(gw.data_home).ensure()
    install_mod(gw, MOD_DIR)
    consent(gw)
    install_loader_app(gw, app_dir)
    gw.start()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            yield Harness(gw, guard, browser)
        finally:
            browser.close()
            gw.stop()
            guard.check()


def _config_path(harness: Harness):
    return FloofyPaths(harness.gw.data_home).mod_dir("display-comfort") / ".floofy" / "config.json"


def _wait_config(harness: Harness, predicate, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        path = _config_path(harness)
        if path.is_file():
            last = json.loads(path.read_text(encoding="utf-8"))
            if predicate(last):
                return last
        time.sleep(0.2)
    raise AssertionError(f"config.json never matched; last: {last}")


def test_1_runtime_page_round_trip_and_warm_first_frame(harness: Harness):
    context, page = harness.page(init=PROBE_INIT)
    try:
        # the runtime part is up and holds the chat rules even before any patch apply
        harness.open(page)
        page.wait_for_selector("#floofy-display-comfort", state="attached", timeout=30000)
        assert "message-bubble" in page.evaluate("() => document.getElementById('floofy-display-comfort').textContent")

        # the settings page: page zoom 80 % applies live and persists
        page.goto(dashboard_url(harness.gw, "/apps/floofycrew"), wait_until="load", timeout=60000)
        page.wait_for_selector('[data-testid="floofycrew-mod-row-display-comfort"]', timeout=60000)
        page.click('[data-testid="floofycrew-open-page-display-comfort"]')
        page.wait_for_selector('[data-testid="display-comfort-page"]', timeout=60000)
        page.fill('[data-testid="display-comfort-page-zoom-value"]', "80")
        page.press('[data-testid="display-comfort-page-zoom-value"]', "Tab")
        page.wait_for_function("() => document.documentElement.style.zoom === '0.8'", timeout=15000)
        page.fill('[data-testid="display-comfort-chat-scale-value"]', "130")
        page.press('[data-testid="display-comfort-chat-scale-value"]', "Tab")
        page.wait_for_function("() => document.documentElement.style.getPropertyValue('--floofy-dc-chat-scale') === '1.3'", timeout=15000)
        stored = _wait_config(harness, lambda c: c.get("pageZoom") == 80 and c.get("chatScale") == 130)
        assert stored["composerToo"] is False
        cache = page.evaluate("() => localStorage.getItem('floofy-display-comfort')")
        assert json.loads(cache) == {"chatScale": 130, "composerToo": False, "pageZoom": 80}

        # WARM reload: the boot hook re-asserts both levers before React (no flash)
        page.reload(wait_until="load", timeout=60000)
        probe = harness.probe(page)
        assert probe["zoom"] == "0.8" and probe["chatScale"] == "1.3" and probe["composer"] == "", probe
    finally:
        context.close()


def test_2_cold_client_fail_open_config_catch_up_and_disable(harness: Harness):
    context, page = harness.page(init=PROBE_INIT)
    try:
        # COLD client (empty storage): the FIRST document of the visit paints stock at
        # DOMContentLoaded — fail-open, nothing guessed. (Later documents of the same
        # visit may already carry the sizes: the runtime part caches the authoritative
        # config as soon as it runs, and the host navigates during its token handshake.)
        harness.open(page)
        harness.probe(page)
        frames = json.loads(page.evaluate("() => sessionStorage.getItem('__dcProbeFrames') || '[]'"))
        assert frames and frames[0] == {"zoom": "", "chatScale": "", "composer": ""}, frames
        # …then the runtime part catches up to the authoritative config once the host is ready
        page.wait_for_function("() => document.documentElement.style.zoom === '0.8' && document.documentElement.style.getPropertyValue('--floofy-dc-chat-scale') === '1.3'", timeout=30000)

        # disable through the Loader: every lever clears without a reload
        client = harness.gw.client()
        answer = client.post(f"{API}/mods/display-comfort/disable", {})
        assert answer.status == 200, answer.body[:300]
        page.wait_for_function("() => document.documentElement.style.zoom === '' && document.documentElement.style.getPropertyValue('--floofy-dc-chat-scale') === '' && !document.getElementById('floofy-display-comfort')", timeout=30000)
        # the settings survive the disable for the next enable
        assert json.loads(_config_path(harness).read_text(encoding="utf-8"))["pageZoom"] == 80
    finally:
        context.close()
