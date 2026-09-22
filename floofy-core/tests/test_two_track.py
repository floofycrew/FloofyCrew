"""Two tracks, one codebase (task 9.3; Requirement 10.3, 10.4).

The shared core, the Loader app and the SPA host must ship byte-identical on both
editions (>= 80 % of shipped files; only the edition adapters may differ), and the
same mod archive must install and activate on both. The two repository scripts
are driven in-process; ``docs/two-track.md`` must quote the measured numbers.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import artifact_identity_report
import install_both_editions

from floofy_testing import REPO_ROOT

DOC = REPO_ROOT / "docs" / "two-track.md"


@pytest.fixture(scope="module")
def identity() -> artifact_identity_report.IdentityReport:
    return artifact_identity_report.measure()


def test_at_least_80_percent_of_shipped_files_are_byte_identical(identity: artifact_identity_report.IdentityReport):
    assert identity.ok, identity.render()
    assert identity.file_percent >= 80.0 and identity.byte_percent >= 80.0
    assert set(identity.files) == {"internal", "external"}
    assert identity.total_files > 150, "both trees hold the zipapp contents and the Loader app directory"


def test_only_the_edition_adapters_differ(identity: artifact_identity_report.IdentityReport):
    assert identity.unexpected == []
    assert set(identity.differing) <= {"adapter modules", "edition stamps"}
    for path in identity.differing.get("adapter modules", []):
        assert re.match(r"^(zipapp|loader-app)/floofy_edition_[a-z]+/", path), path
    core_paths = [p for p in identity.union if "/floofy_core/" in p or p.startswith("loader-app/floofy_loader/") or p.startswith("loader-app/ui/")]
    assert core_paths and all(p in identity.identical for p in core_paths), "the core, the Loader runtime and the SPA host never fork per edition"
    assert "loader-app/app.json" in identity.identical and "zipapp/__main__.py" in identity.identical


def test_docs_quote_the_measured_numbers(identity: artifact_identity_report.IdentityReport):
    text = DOC.read_text(encoding="utf-8")
    files = re.search(r"\| files \| (\d+) \| (\d+) \| ([\d.]+) % \|", text)
    assert files, "docs/two-track.md carries the files row"
    assert (int(files.group(1)), int(files.group(2))) == (identity.identical_files, identity.total_files)
    assert float(files.group(3)) == round(identity.file_percent, 1)
    byte_row = re.search(r"\| bytes \| ([\d ]+) \| ([\d ]+) \| ([\d.]+) % \|", text)
    assert byte_row and float(byte_row.group(3)) == round(identity.byte_percent, 1)
    assert int(byte_row.group(1).replace(" ", "")) == identity.identical_bytes and int(byte_row.group(2).replace(" ", "")) == identity.total_bytes
    assert text.rstrip().endswith("Covers Requirements 10.1, 10.2, 10.3, 10.4.")


def test_the_same_mod_archives_install_and_activate_on_both_editions(tmp_path: Path):
    report = install_both_editions.run_both(tmp_path / "work", external_dist=install_both_editions.FIXTURE_DIST)
    assert report["ok"], install_both_editions.render(report)
    assert [m["id"] for m in report["mods"]] == ["custom-themes", "rimuru-branding", "settings-demo"], "the first-party mods travel as one archive each"
    for edition in ("internal", "external"):
        record = report["editions"][edition]
        assert record["detectedEdition"] == edition
        assert all(step["exit"] == 0 for step in record["steps"].values()), record["steps"]
        assert record["boot"]["loader"] == "ok" and record["boot"]["hostEdition"] == edition
        themes = record["mods"]["custom-themes"]
        assert themes["ok"] and themes["boot"]["active"]
        assert themes["boot"]["patcherRan"] and themes["boot"]["patcherOk"] and themes["boot"]["bootScriptInjected"]
        assert [tuple(p) for p in themes["boot"]["parts"]] == [("ui", "active"), ("python-hook", "active"), ("spa", "active"), ("spa", "active"), ("patch", "inactive")]
        assert all(s["code"] == "FingerprintMiss" for s in themes["boot"]["skipped"]), "a stub chunk is skipped with a diagnostic, never half-patched"
        assert {c["route"] for c in themes["boot"]["routeCalls"]} == {"GET status", "GET themes", "GET presets", "GET css"} and all(c["ok"] for c in themes["boot"]["routeCalls"]), themes["boot"]["routeCalls"]
        rimuru = record["mods"]["rimuru-branding"]
        assert rimuru["ok"] and rimuru["boot"]["active"], "a pure theme part on top of custom-themes (2.0.0)"
        assert [tuple(p) for p in rimuru["boot"]["parts"]] == [("theme", "inactive")] and "patcherRan" not in rimuru["boot"]
        demo = record["mods"]["settings-demo"]
        assert demo["ok"] and demo["boot"]["active"]
        assert demo["steps"]["install"]["landedEnabled"] is True, "a confirmed install lands enabled (Requirement 11.7); the script's `enable` is an idempotent no-op"
        assert [tuple(p) for p in demo["boot"]["parts"]] == [("ui", "active"), ("python-hook", "active")]
        assert demo["boot"]["routes"] == [{"method": "GET", "path": "echo"}, {"method": "POST", "path": "echo"}]
        assert demo["boot"]["routeCalls"] == [{"route": "GET echo", "status": 200, "ok": True}], "the hook's route answers on this edition"
    text = DOC.read_text(encoding="utf-8")
    for mod in report["mods"]:
        again = install_both_editions.build_mod_archive(install_both_editions.REPO_ROOT / "mods" / mod["id"], tmp_path / "again")
        assert again[1] == mod["archive"]["sha256"] and again[2] == mod["archive"]["size"], f"{mod['id']}: the archive is deterministic: the same bytes on both editions"
        assert mod["archive"]["sha256"] in text, f"docs/two-track.md quotes {mod['id']}'s archive sha256"
    assert report["mod"] == "custom-themes" and report["archive"] == report["mods"][0]["archive"], "the single-mod report shape still reads"
