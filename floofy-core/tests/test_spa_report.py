"""The SPA reporter's Python side (Requirement 4.5, 9.1): registry, offline bundle check, CLI."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from floofy_core import cli_spa, spa_report
from floofy_core.spa_report import SpaReport, check_bundle_fingerprints, discover_chunks, load_registry, match_bundle

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
    (dist / "assets" / "main-AAAAAAAA.js").write_text('import("./App-CCCCCCCC.js");const marker="in-main";', encoding="utf-8")
    (dist / "assets" / "App-CCCCCCCC.js").write_text('"data-testid":`dashboard-shell`; path:`/apps`; path:`/apps`;', encoding="utf-8")
    (dist / "assets" / "useTheme-BBBBBBBB.js").write_text("r.dataset.theme=U(e,t);mc-theme-favicon", encoding="utf-8")
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
            assert "-" not in bundle["chunk"] or not any(ch.isdigit() for ch in bundle["chunk"]), f"{surface['name']}: a chunk is named by its stem, never by hash"


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


def test_from_browser_keeps_reasons_and_meta():
    report = SpaReport.from_browser(
        {"hostVersion": "0.7.0.5", "matched": ["a#dom"], "missed": [{"name": "b#bundle", "kind": "bundle", "reason": "gone", "chunk": "x.js"}, "plain"], "notApplicable": [{"name": "c#dom", "reason": "route"}], "chunks": {"main": "main-1.js"}, "route": "/apps", "surfaces": ["a", "b", "c"]},
        extra={"hostTag": "injected"},
    )
    data = report.to_dict()
    assert data["source"] == "browser" and data["hostTag"] == "injected" and data["route"] == "/apps"
    assert data["spaFingerprints"] == {"matched": 1, "total": 3, "missed": ["b#bundle", "plain"]}
    assert data["notApplicable"][0]["name"] == "c#dom"


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
