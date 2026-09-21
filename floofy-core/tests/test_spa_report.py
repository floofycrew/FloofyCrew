"""The SPA reporter's Python side (Requirement 4.5, 9.1): registry, offline bundle check, CLI."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from floofy_core import cli_spa, spa_report
from floofy_core.spa_report import SpaReport, bundle_stems, check_bundle_fingerprints, discover_chunks, load_registry, match_bundle

from floofy_testing import REPO_ROOT

REGISTRY_PATH = REPO_ROOT / "spa-host" / "src" / "surfaces.json"
SCRATCH_DIST = Path(os.environ.get("FLOOFY_SCRATCH_PAYLOAD") or REPO_ROOT / ".scratch" / "payload-0.7.0.5") / "lib" / "python3.12" / "site-packages" / "kiro_crew" / "static" / "dist"


def fake_dist(root: Path) -> Path:
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        '<html><head><script type="module" crossorigin src="/assets/main-AAAAAAAA.js"></script>\n'
        '<link rel="modulepreload" crossorigin href="/assets/useTheme-BBBBBBBB.js"></head><body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    (dist / "assets" / "main-AAAAAAAA.js").write_text('import("./App-CCCCCCCC.js");import"./terminal-DDDDDDDD.js";const marker="in-main";', encoding="utf-8")
    (dist / "assets" / "App-CCCCCCCC.js").write_text('"data-testid":`dashboard-shell`; path:`/apps`; path:`/apps`;', encoding="utf-8")
    (dist / "assets" / "useTheme-BBBBBBBB.js").write_text("r.dataset.theme=U(e,t);mc-theme-favicon", encoding="utf-8")
    (dist / "assets" / "terminal-DDDDDDDD.js").write_text("r.dataset.theme=ne(e,t);mc-theme-favicon;mc-custom-theme-", encoding="utf-8")
    return dist


def public_0_6_0_dist(root: Path) -> Path:
    """The public 0.6.0 shape: no useTheme chunk; the hook lives in the preloaded `terminal` chunk."""
    dist = fake_dist(root)
    (dist / "assets" / "useTheme-BBBBBBBB.js").unlink()
    (dist / "index.html").write_text(
        '<html><head><script type="module" crossorigin src="/assets/main-AAAAAAAA.js"></script>\n'
        '<link rel="modulepreload" crossorigin href="/assets/terminal-DDDDDDDD.js"></head><body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    return dist


def test_default_registry_is_found_and_well_formed():
    registry = load_registry()
    assert spa_report.default_registry_path() == REGISTRY_PATH
    assert registry["schema"] == 1 and len(registry["surfaces"]) >= 8
    names = [s["name"] for s in registry["surfaces"]]
    assert len(set(names)) == len(names)
    for surface in registry["surfaces"]:
        assert surface.get("dom") or surface.get("bundle"), surface["name"]
        bundle = surface.get("bundle")
        if bundle:
            stems = bundle_stems(bundle)
            assert stems, f"{surface['name']}: at least one chunk stem"
            for stem in stems:
                assert "-" not in stem or not any(ch.isdigit() for ch in stem), f"{surface['name']}: a chunk is named by its stem, never by hash"
    # the theme hook is its own useTheme chunk on 0.7.0.x and folded into `terminal` on public 0.6.0
    for name in ("favicon", "theme.tokens", "theme.custom-style", "theme.reset-site"):
        surface = next(s for s in registry["surfaces"] if s["name"] == name)
        assert bundle_stems(surface["bundle"]) == ["useTheme", "terminal"], name


def test_bundle_stems_accepts_a_string_or_a_list():
    assert bundle_stems({"chunk": "App"}) == ["App"]
    assert bundle_stems({"chunk": ["useTheme", "terminal"]}) == ["useTheme", "terminal"]
    assert bundle_stems({"chunk": ["", None, "main"]}) == ["main"]
    assert bundle_stems({}) == []


def test_discover_chunks_reads_the_shell_and_expands_lazy_stems(tmp_path: Path):
    dist = fake_dist(tmp_path)
    assert discover_chunks(dist) == {"main": "main-AAAAAAAA.js", "useTheme": "useTheme-BBBBBBBB.js"}
    assert discover_chunks(dist, wanted={"App"})["App"] == "App-CCCCCCCC.js"
    assert discover_chunks(tmp_path / "nowhere") == {}


def test_match_bundle_counts():
    assert match_bundle({"contains": "x", "count": "once"}, "axb") == ("matched", "1 occurrence(s)")
    assert match_bundle({"contains": "x", "count": "once"}, "axbx")[0] == "missed"
    assert match_bundle({"contains": "x"}, "axbx")[0] == "matched"
    assert match_bundle({"regex": r"path:`/app\w`"}, "path:`/apps`")[0] == "matched"
    assert match_bundle({}, "anything")[0] == "missed"
    assert match_bundle({"contains": "zz"}, "abc")[0] == "missed"


def test_check_bundle_fingerprints_reports_matches_misses_and_the_matrix_shape(tmp_path: Path):
    dist = fake_dist(tmp_path)
    registry = {
        "schema": 1,
        "surfaces": [
            {"name": "dashboard.shell", "fromBuild": "0.7.0.5", "bundle": {"chunk": "App", "contains": '"data-testid":`dashboard-shell`', "count": "once"}},
            {"name": "apps.page", "fromBuild": "0.7.0.5", "bundle": {"chunk": "App", "contains": "path:`/apps`"}},
            {"name": "theme.tokens", "fromBuild": "0.6.0", "dom": {"selector": "html[data-theme]"}, "bundle": {"chunk": "useTheme", "contains": ".dataset.theme=", "count": "once"}},
            {"name": "dom-only", "fromBuild": "0.6.0", "dom": {"selector": "#root"}},
            {"name": "gone", "fromBuild": "0.7.0.5", "bundle": {"chunk": "Nowhere", "contains": "x"}},
            {"name": "stale", "fromBuild": "0.7.0.5", "bundle": {"chunk": "main", "contains": "removed"}},
        ],
    }
    report = check_bundle_fingerprints(dist, registry, host_version="9.9.9")
    assert report.matched == ["dashboard.shell#bundle", "apps.page#bundle", "theme.tokens#bundle"]
    assert [m["name"] for m in report.missed] == ["gone#bundle", "stale#bundle"]
    assert report.missed[0]["chunk"] is None and report.missed[1]["chunk"] == "main-AAAAAAAA.js"
    data = report.to_dict()
    assert data["spaFingerprints"] == {"matched": 3, "total": 5, "missed": ["gone#bundle", "stale#bundle"]}
    assert data["hostVersion"] == "9.9.9" and data["source"] == "offline" and data["chunks"]["App"] == "App-CCCCCCCC.js"
    assert not report.ok


def test_check_bundle_fingerprints_tries_fallback_stems_in_order(tmp_path: Path):
    """public 0.6.0: the useTheme hook is folded into `terminal`; the registry lists both stems."""
    dist = public_0_6_0_dist(tmp_path)
    registry = {
        "schema": 1,
        "surfaces": [
            {"name": "theme.tokens", "fromBuild": "0.6.0", "bundle": {"chunk": ["useTheme", "terminal"], "contains": ".dataset.theme=", "count": "once"}},
            {"name": "favicon", "fromBuild": "0.6.0", "bundle": {"chunk": ["useTheme", "terminal"], "contains": "mc-theme-favicon"}},
            {"name": "moved-away", "fromBuild": "0.6.0", "bundle": {"chunk": ["useTheme", "Nowhere"], "contains": ".dataset.theme="}},
            {"name": "wrong-in-both", "fromBuild": "0.6.0", "bundle": {"chunk": ["main", "App"], "contains": "absent"}},
            {"name": "first-wins", "fromBuild": "0.6.0", "bundle": {"chunk": ["main", "terminal"], "contains": "in-main"}},
        ],
    }
    report = check_bundle_fingerprints(dist, registry, host_version="0.6.0")
    assert report.matched == ["theme.tokens#bundle", "favicon#bundle", "first-wins#bundle"]
    assert [m["name"] for m in report.missed] == ["moved-away#bundle", "wrong-in-both#bundle"]
    assert report.missed[0] == {"name": "moved-away#bundle", "kind": "bundle", "reason": "no chunk with stem useTheme|Nowhere is referenced by the shell", "chunk": None}
    assert report.missed[1]["chunk"] == "App-CCCCCCCC.js" and report.missed[1]["reason"] == "no occurrence in the chunk text"
    assert "useTheme" not in report.chunks and report.chunks["terminal"] == "terminal-DDDDDDDD.js"
    # the 0.7.0.x shape still resolves through the first stem
    older = check_bundle_fingerprints(fake_dist(tmp_path / "older"), registry, host_version="0.7.0.5")
    assert older.matched == ["theme.tokens#bundle", "favicon#bundle", "moved-away#bundle", "first-wins#bundle"]
    assert older.chunks["useTheme"] == "useTheme-BBBBBBBB.js"


def test_from_browser_keeps_reasons_and_meta():
    report = SpaReport.from_browser(
        {"hostVersion": "0.7.0.5", "matched": ["a#dom"], "missed": [{"name": "b#bundle", "kind": "bundle", "reason": "gone", "chunk": "x.js"}, "plain"], "notApplicable": [{"name": "c#dom", "reason": "route"}], "chunks": {"main": "main-1.js"}, "route": "/apps", "surfaces": ["a", "b", "c"]},
        extra={"hostTag": "injected"},
    )
    data = report.to_dict()
    assert data["source"] == "browser" and data["hostTag"] == "injected" and data["route"] == "/apps"
    assert data["spaFingerprints"] == {"matched": 1, "total": 3, "missed": ["b#bundle", "plain"]}
    assert data["notApplicable"][0]["name"] == "c#dom"


class FakePage:
    """A dashboard page as Playwright exposes it, on a virtual clock.

    Models the 0.7.0.5 boot: ``load`` has fired, ``window.floofy`` appears at
    ``floofy_at`` (a loader tag) or only after ``add_script_tag`` (injection),
    the React root renders at ``mount_at`` (after the ``/api/ui-prefs``
    round-trip), and the page may ``location.reload()`` at ``reload_at`` — which
    wipes an injected host. ``report()`` answers with the DOM fingerprints that
    exist at the moment it is called.
    """

    def __init__(self, *, mount_at: float, floofy_at: float | None = None, reload_at: float | None = None, host_delay: float = 0.2):
        self.now = 0.0
        self.mount_at = mount_at
        self.floofy_at = floofy_at
        self.reload_at = reload_at
        self.host_delay = host_delay
        self.injected_at: float | None = None
        self.reloaded = False
        self.calls: list[tuple[str, Any]] = []

    # -- the page's world at ``self.now``
    def _tick(self) -> None:
        if self.reload_at is not None and not self.reloaded and self.now >= self.reload_at:
            self.reloaded = True
            self.injected_at = None  # an injected module tag does not survive a reload

    def _has_floofy(self) -> bool:
        self._tick()
        if self.floofy_at is not None and self.now >= self.floofy_at:
            return True
        return self.injected_at is not None and self.now >= self.injected_at + self.host_delay

    def _mounted(self) -> bool:
        self._tick()
        if self.reload_at is not None and self.now >= self.reload_at:
            return self.now >= self.reload_at + self.mount_at
        return self.now >= self.mount_at

    def _wait(self, predicate, timeout_ms: int) -> None:
        deadline = self.now + timeout_ms / 1000
        while self.now < deadline:
            if predicate():
                return
            self.now = min(deadline, self.now + 0.05)
        if not predicate():
            raise TimeoutError(f"timeout after {timeout_ms} ms")

    # -- the Playwright surface drive_report_page uses
    def wait_for_function(self, expression: str, timeout: int) -> None:
        assert "window.floofy" in expression
        self.calls.append(("wait_for_function", timeout))
        self._wait(self._has_floofy, timeout)

    def wait_for_selector(self, selector: str, state: str, timeout: int) -> None:
        assert selector == spa_report.MOUNT_SELECTOR and state == "attached"
        self.calls.append(("wait_for_selector", timeout))
        self._wait(self._mounted, timeout)

    def add_script_tag(self, url: str, type: str) -> None:  # noqa: A002 - Playwright's keyword
        assert url == spa_report.HOST_MODULE_URL and type == "module"
        self.calls.append(("add_script_tag", url))
        self.injected_at = self.now

    def wait_for_timeout(self, ms: int) -> None:
        self.calls.append(("wait_for_timeout", ms))
        self.now += ms / 1000

    def evaluate(self, expression: str, arg: Any = None) -> Any:
        self.calls.append(("evaluate", expression))
        if "__floofyHostStatus" in expression:
            return {"stage": "booting"}
        assert self._has_floofy(), "report() called without window.floofy"
        matched = ["shell.root#dom", "dashboard.shell#bundle"]
        missed = []
        if self._mounted():
            matched.append("dashboard.shell#dom")
        else:
            missed.append({"name": "dashboard.shell#dom", "kind": "dom", "reason": 'selector [data-testid="dashboard-shell"] matches nothing'})
        return {"hostVersion": "0.7.0.5", "matched": matched, "missed": missed, "notApplicable": [], "chunks": {"App": "App-X.js"}, "route": "/", "surfaces": ["shell.root", "dashboard.shell"], "extraSurfaces": arg.get("extraSurfaces") if isinstance(arg, dict) else None}


def test_drive_report_page_waits_for_the_deferred_mount():
    """0.7.0.5 renders React only after /api/ui-prefs answers: the report must wait for #root to fill, not just for load + 500 ms."""
    page = FakePage(mount_at=6.0, floofy_at=0.1)
    document, host_tag = spa_report.drive_report_page(page, timeout_s=30.0, clock=lambda: page.now)
    assert host_tag == "present" and document["mounted"] is True
    assert "dashboard.shell#dom" in document["matched"] and document["missed"] == []
    kinds = [c[0] for c in page.calls]
    assert "add_script_tag" not in kinds and kinds.index("wait_for_selector") < kinds.index("wait_for_timeout") < kinds.index("evaluate")
    report = SpaReport.from_browser(document, extra={"hostTag": host_tag})
    assert report.ok and report.to_dict()["mounted"] is True


def test_drive_report_page_injects_the_host_then_waits_for_the_mount():
    """An unpatched shell: inject host.mjs, then still wait for the React root before reporting."""
    page = FakePage(mount_at=7.0)
    document, host_tag = spa_report.drive_report_page(page, timeout_s=30.0, clock=lambda: page.now)
    assert host_tag == "injected" and document["mounted"] is True and document["missed"] == []
    kinds = [c[0] for c in page.calls]
    assert kinds.index("add_script_tag") < kinds.index("wait_for_selector") < kinds.index("evaluate")


def test_drive_report_page_reattaches_the_host_after_a_self_reload():
    """The ui-prefs sync may location.reload() once; an injected window.floofy is gone afterwards and must be re-added."""
    page = FakePage(mount_at=1.0, reload_at=4.5)  # reload lands after the 4 s floofy probe + injection, during the mount wait
    document, host_tag = spa_report.drive_report_page(page, timeout_s=30.0, clock=lambda: page.now)
    assert page.reloaded and host_tag == "reinjected"
    assert document["mounted"] is True and document["missed"] == []
    assert len([c for c in page.calls if c[0] == "add_script_tag"]) == 2


def test_drive_report_page_reports_the_misses_when_the_mount_never_comes():
    """No mount within the budget is not an error: the report is taken and names the misses (what the matrix records)."""
    page = FakePage(mount_at=10_000.0, floofy_at=0.0)
    document, host_tag = spa_report.drive_report_page(page, timeout_s=5.0, clock=lambda: page.now)
    assert host_tag == "present" and document["mounted"] is False
    assert [m["name"] for m in document["missed"]] == ["dashboard.shell#dom"]
    assert page.now < 8.0  # bounded by the budget, no runaway wait


def test_drive_report_page_respects_no_inject():
    page = FakePage(mount_at=0.0)
    with pytest.raises(RuntimeError, match="--no-inject"):
        spa_report.drive_report_page(page, inject_host=False, timeout_s=2.0, clock=lambda: page.now)


def test_cli_bundle_check_and_ready_file(tmp_path: Path, capsys):
    dist = fake_dist(tmp_path)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema": 1, "surfaces": [{"name": "s", "fromBuild": "0.7.0.5", "bundle": {"chunk": "main", "contains": "in-main"}}]}), encoding="utf-8")
    assert cli_spa.main(["--registry", str(registry), "--json", "bundle-check", "--dist", str(dist), "--host-version", "1.2.3"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["spaFingerprints"] == {"matched": 1, "total": 1, "missed": []} and out["hostVersion"] == "1.2.3"
    registry.write_text(json.dumps({"schema": 1, "surfaces": [{"name": "s", "fromBuild": "0.7.0.5", "bundle": {"chunk": "main", "contains": "absent"}}]}), encoding="utf-8")
    assert cli_spa.main(["--registry", str(registry), "bundle-check", "--dist", str(dist)]) == 1
    assert "MISS  s#bundle" in capsys.readouterr().out
    ready = tmp_path / "gateway.out"
    ready.write_text('noise\nKIROCREW_READY:{"port": 4242, "token": "abc", "extra": 1}\n', encoding="utf-8")
    assert cli_spa.read_ready_file(ready) == {"port": 4242, "token": "abc", "extra": 1}
    bare = tmp_path / "ready.json"
    bare.write_text('{"port": 1, "token": "t"}', encoding="utf-8")
    assert cli_spa.read_ready_file(bare)["token"] == "t"
    with pytest.raises(ValueError):
        cli_spa.read_ready_file(dist / "index.html")
    with pytest.raises(SystemExit):
        cli_spa.main(["verify"])  # needs --url/--port/--ready-file


@pytest.mark.skipif(not (SCRATCH_DIST / "index.html").is_file(), reason="no payload copy under .scratch")
def test_shipped_registry_matches_the_0_7_0_5_bundle_offline():
    """Every bundle fingerprint in surfaces.json holds against the real 0.7.0.5 dist (a copy, read-only here)."""
    report = check_bundle_fingerprints(SCRATCH_DIST, load_registry(), host_version="0.7.0.5")
    assert report.missed == [], json.dumps(report.missed, indent=2)
    assert {"App", "main", "useTheme"} <= set(report.chunks)
    assert report.to_dict()["spaFingerprints"]["total"] >= 10
