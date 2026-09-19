"""Patcher tests on trimmed fixture dists from three real host versions (Requirement 13.3; task 3.7).

``tests/fixtures/dist-<ver>/`` are derived by ``scripts/make_fixture_payload.py``
from the 0.7.0.4 and 0.7.0.5 bundle dists and the public 0.6.0 wheel: the real
``index.html``, one real seed chunk with its sidecars, skeletons of its importer
closure and 0-byte stubs of everything else the shell references. Each fixture is
wrapped into a fake payload and driven through apply → restore (byte-identical
tree), idempotent double apply, every drift class with its default action, the
alias closure against ``FIXTURE.json``, the lost-manifest sweep and a theme direct
write compared through the ported host validator.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from floofy_core.aliasgraph import build_graph
from floofy_core.deploy import BACKUP_SUFFIX, DeployManifest, DriftClass, is_floofy_added
from floofy_core.patcher import Patcher, PlannedPatch
from floofy_core.patches import PatchDescriptor, fingerprint_report
from floofy_core.payloads import make_payload
from floofy_core.themes import direct_write_theme, validate_theme_dir

from floofy_testing import EXAMPLES_DIR, REPO_ROOT

FIXTURES = sorted(p for p in (REPO_ROOT / "floofy-core" / "tests" / "fixtures").glob("dist-*") if (p / "FIXTURE.json").is_file())
FIXTURE_IDS = [p.name for p in FIXTURES]


def snapshot(root: Path) -> dict[str, str]:
    from floofy_core.deploy import sha256_file

    return {p.relative_to(root).as_posix(): sha256_file(p) for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture(params=FIXTURES, ids=FIXTURE_IDS)
def fixture_payload(request: pytest.FixtureRequest, tmp_path: Path):
    """A fake payload whose static/dist is a copy of the fixture; returns (payload, record, home)."""
    fixture: Path = request.param
    record = json.loads((fixture / "FIXTURE.json").read_text(encoding="utf-8"))
    package_dir = tmp_path / "payload" / "kiro_crew"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text(f'__version__ = "{record["sourceVersion"].rsplit(".", 1)[0] if record["sourceVersion"].count(".") == 3 else record["sourceVersion"]}"\n', encoding="utf-8")
    if record["sourceVersion"].count(".") == 3:
        (package_dir / "BUILD_VERSION").write_text(record["sourceVersion"], encoding="utf-8")
    shutil.copytree(fixture, package_dir / "static" / "dist", ignore=shutil.ignore_patterns("FIXTURE.json"))
    payload = make_payload(kind="fixture", root=tmp_path / "payload", package_dir=package_dir, source="fixture", current=True, edition=record["edition"])
    assert str(payload.host_version) == record["sourceVersion"]
    return payload, record, tmp_path / "home"


def boot_descriptor() -> PatchDescriptor:
    """The example boot op without its appliesTo gate, so the 0.6.0 fixture takes it too."""
    document = json.loads((EXAMPLES_DIR / "patch" / "patches" / "index-boot.json").read_text(encoding="utf-8"))
    document.pop("appliesTo", None)
    return PatchDescriptor.from_dict(document, source="example-boot")


def seed_descriptor(record: dict) -> PatchDescriptor:
    """Prepend a marker comment to the real seed chunk (anchored on its first byte via regex)."""
    return PatchDescriptor.from_dict(
        {
            "schema": 1,
            "target": f"kiro_crew/static/dist/assets/{record['seed']}",
            "cacheBust": True,
            "ops": [{"op": "insert-before", "fingerprint": r"\A", "regex": True, "content": "/*floofy-seed*/", "marker": "/*floofy-seed*/"}],
        },
        source="seed",
    )


def patch_set(record: dict) -> list[PlannedPatch]:
    return [PlannedPatch("example-patch", "0", boot_descriptor()), PlannedPatch("seed-mod", "0", seed_descriptor(record))]


# --- fixture integrity ---------------------------------------------------------------------


def test_three_host_versions_are_present() -> None:
    assert {p.name for p in FIXTURES} >= {"dist-0.7.0.4", "dist-0.7.0.5", "dist-0.6.0"}
    total = sum(p.stat().st_size for f in FIXTURES for p in f.rglob("*") if p.is_file())
    assert total < 2 * 1024 * 1024


def test_fixture_graph_matches_record(fixture_payload) -> None:
    payload, record, _ = fixture_payload
    graph = build_graph(payload.assets_dir)
    assert graph.closure([record["seed"]]) == record["closure"]
    assert sorted(graph.importers[record["seed"]]) == record["seedImporters"] and len(record["seedImporters"]) >= 2
    assert record["entry"] in record["closure"] and record["entry"] in payload.index_html.read_text()
    assert (payload.assets_dir / (record["seed"] + ".br")).is_file() or (payload.assets_dir / (record["seed"] + ".gz")).is_file()
    assert record["files"][f"assets/{record['seed']}"]["kind"] == "verbatim"


def test_example_boot_fingerprint_matches_every_shell(fixture_payload) -> None:
    payload, _, _ = fixture_payload
    report = fingerprint_report(payload.index_html.read_text(encoding="utf-8"), [boot_descriptor()])
    assert report.missed == [] and report.total == 1
    gated = PatchDescriptor.load(EXAMPLES_DIR / "patch" / "patches" / "index-boot.json")
    applies = gated.applicability(payload.host_version) is None
    assert applies == (payload.host_version.base >= type(payload.host_version.base).parse("0.7.0"))


# --- apply → restore, idempotency, sweep ------------------------------------------------------


def test_apply_then_restore_is_byte_identical(fixture_payload) -> None:
    payload, record, home = fixture_payload
    before = snapshot(payload.root)
    patcher = Patcher(home, [payload], endpoints=[])
    report = patcher.apply(patch_set(record), verify=False)
    result = report.payloads[0]
    assert result.ok and result.alias_graph and len(result.written) >= 2 + len(record["closure"])
    html = payload.index_html.read_text(encoding="utf-8")
    assert 'id="floofy-example-boot"' in html and record["entry"] not in html  # the entry is repointed at its alias
    assert len([p for p in payload.assets_dir.iterdir() if is_floofy_added(p)]) == len(record["closure"])
    assert not (payload.assets_dir / (record["seed"] + ".br")).exists() or not any(p.name == record["seed"] + ".br" for p in payload.assets_dir.iterdir())
    manifest = DeployManifest.load(patcher.manifest_path(payload))
    assert manifest.host_version == record["sourceVersion"] and manifest.edition == record["edition"]
    patcher.restore()
    assert snapshot(payload.root) == before
    assert not patcher.manifest_path(payload).exists()


def test_double_apply_is_a_no_op(fixture_payload) -> None:
    payload, record, home = fixture_payload
    patcher = Patcher(home, [payload], endpoints=[])
    patcher.apply(patch_set(record), verify=False)
    once = snapshot(payload.root)
    second = patcher.apply(patch_set(record), verify=False).payloads[0]
    assert second.written == [] and second.restored == [] and second.removed == []
    assert set(second.drift.values()) == {DriftClass.CLEAN.value}
    assert snapshot(payload.root) == once


def test_lost_manifest_sweep_restores_vanilla(fixture_payload) -> None:
    payload, record, home = fixture_payload
    before = snapshot(payload.root)
    patcher = Patcher(home, [payload], endpoints=[])
    patcher.apply(patch_set(record), verify=False)
    patcher.manifest_path(payload).unlink()
    report = patcher.restore()
    assert snapshot(payload.root) == before
    assert len(report.payloads[payload.id]["removed"]) == len(record["closure"])


def test_every_drift_class_and_default_action(fixture_payload) -> None:
    payload, record, home = fixture_payload
    patcher = Patcher(home, [payload], endpoints=[])
    patches = patch_set(record)
    patcher.apply(patches, verify=False)
    seed = payload.assets_dir / record["seed"]
    aliases = sorted(p for p in payload.assets_dir.iterdir() if is_floofy_added(p))
    # user-edited shell → skip; host re-laid the seed → re-derive; an alias deleted → re-added
    payload.index_html.write_text(payload.index_html.read_text(encoding="utf-8") + "<!--hand-->", encoding="utf-8")
    (payload.assets_dir / (record["seed"] + BACKUP_SUFFIX)).unlink()
    seed.write_bytes(b"export const relaid=1;\n")
    aliases[0].unlink()
    result = patcher.apply(patches, verify=False).payloads[0]
    assert result.drift[str(payload.index_html)] == DriftClass.USER_EDITED.value
    assert payload.index_html.read_text(encoding="utf-8").endswith("<!--hand-->")
    assert result.drift[str(seed)] == DriftClass.HOST_UPDATED.value and seed.read_bytes() == b"/*floofy-seed*/export const relaid=1;\n"
    assert result.drift[str(aliases[0])] == DriftClass.MOD_DELETED.value
    assert any(is_floofy_added(p) for p in payload.assets_dir.iterdir())
    # clean again on the next run except the hand-edited shell
    again = patcher.apply(patches, verify=False).payloads[0]
    assert {v for k, v in again.drift.items() if k != str(payload.index_html)} == {DriftClass.CLEAN.value}


# --- theme direct write through the host validator -------------------------------------------


def test_theme_direct_write_matches_host_validator(tmp_path: Path) -> None:
    """Without the host route offline, compare the ported validator's view of source and installed tree."""
    source = tmp_path / "pack"
    shutil.copytree(EXAMPLES_DIR / "theme" / "theme", source) if (EXAMPLES_DIR / "theme" / "theme").is_dir() else None
    if not (source / "theme.json").is_file():
        source.mkdir(exist_ok=True)
        (source / "theme.json").write_text(json.dumps({"formatVersion": 1, "level": 0, "name": "Fixture", "slug": "fixture"}), encoding="utf-8")
        (source / "variables.json").write_text(json.dumps({"dark": {"--bg": "#000", "--text": "#fff", "--accent": "#0af"}, "light": {"--bg": "#fff", "--text": "#000", "--accent": "#0af"}}), encoding="utf-8")
    expected = validate_theme_dir(source)
    installed, summary = direct_write_theme(source, tmp_path / "themes")
    assert summary.to_dict() == expected.to_dict() and validate_theme_dir(installed).to_dict() == expected.to_dict()
    assert {p.relative_to(installed).as_posix(): p.read_bytes() for p in installed.rglob("*") if p.is_file()} == {rel: (source / rel).read_bytes() for rel in expected.files}
