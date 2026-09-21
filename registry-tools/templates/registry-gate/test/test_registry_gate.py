"""The gate of {{PACKAGE_NAME}} (Requirement 8.7, 8.10): the committed registry passes, and every rule still bites.

The first test is the one that matters operationally: it runs the gate over the
registry as committed, cloning every link record at its tag (set
``{{GATE_ENV_PREFIX}}_SKIP_CLONE=1`` for an offline local run — the merge gate never
does). The others copy the repository, break one thing at a time and prove the
gate names it; a rule that only ever passes is indistinguishable from a rule
that is not wired up.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from {{GATE_MODULE}} import validate as gate

ROOT = gate.find_repository_root(Path(__file__).resolve().parent)


def _copy(tmp_path: Path) -> Path:
    target = tmp_path / "registry"
    shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns(".git", "build", "__pycache__", ".pytest_cache"))
    return target


def _first_record(root: Path) -> tuple[Path, dict, dict]:
    mods = sorted(p for p in (root / "mods").iterdir() if p.is_dir())
    assert mods, "the registry lists at least one mod"
    versions = sorted(p for p in mods[0].iterdir() if p.is_dir())
    release = json.loads((versions[0] / "release.json").read_text(encoding="utf-8"))
    manifest = json.loads((versions[0] / "floofy.json").read_text(encoding="utf-8"))
    return versions[0], release, manifest


def test_the_committed_registry_passes_the_gate():
    report = gate.validate_registry(ROOT)
    assert report.ok, report.format()
    assert report.records >= 1
    if os.environ.get(gate.SKIP_CLONE_ENV) != "1":
        assert report.cloned == sum(1 for _ in _link_records(ROOT)), "every link record was cloned at its tag and compared"


def _link_records(root: Path):
    for mod in sorted(p for p in (root / "mods").iterdir() if p.is_dir()):
        for version in sorted(p for p in mod.iterdir() if p.is_dir()):
            release = json.loads((version / "release.json").read_text(encoding="utf-8"))
            if isinstance(release.get("link"), dict):
                yield version


def test_reserved_names_match_exactly_or_as_the_leading_token():
    reserved = gate.load_reserved_names(ROOT)
    assert reserved.leading_token and reserved.exact_only
    token = reserved.leading_token[0]
    assert gate.reserved_name_hit(token, reserved) == token
    assert gate.reserved_name_hit(f"{token}-sync", reserved) == token, "a leading token claims ownership"
    assert gate.reserved_name_hit(f"migrate-to-{token}", reserved) is None, "mentioning a product is not claiming to be it"
    exact = reserved.exact_only[0]
    assert gate.reserved_name_hit(exact, reserved) == exact
    assert gate.reserved_name_hit(f"{exact}-game-tracker", reserved) is None, "an ordinary word only matches by itself"
    assert gate.reserved_name_hit("rimuru-branding", reserved) is None


def test_a_reserved_or_masquerading_name_is_refused(tmp_path: Path):
    root = _copy(tmp_path)
    directory, release, manifest = _first_record(root)
    reserved = gate.load_reserved_names(root)
    curated_path = directory.parent / "mod.json"
    curated = json.loads(curated_path.read_text(encoding="utf-8"))
    curated["name"] = f"{reserved.leading_token[0]}-helper"
    curated_path.write_text(json.dumps(curated), encoding="utf-8")
    report = gate.validate_registry(root, clone=False)
    assert any("claims the reserved product name" in p for p in report.problems), report.format()


def test_a_missing_contact_is_refused(tmp_path: Path):
    root = _copy(tmp_path)
    directory, release, manifest = _first_record(root)
    curated_path = directory.parent / "mod.json"
    curated = json.loads(curated_path.read_text(encoding="utf-8"))
    curated.pop("contact", None)
    curated_path.write_text(json.dumps(curated), encoding="utf-8")
    report = gate.validate_registry(root, clone=False)
    assert any("`contact`" in p for p in report.problems), report.format()


def test_a_repository_outside_the_forge_pattern_and_a_legacy_wrong_tag_are_refused(tmp_path: Path):
    root = _copy(tmp_path)
    directory, release, manifest = _first_record(root)
    if not isinstance(release.get("link"), dict):
        pytest.skip("the first record is an archive record")
    release["link"]["repo"] = "ssh://git.example.evil/pkg/Impostor"
    (directory / "release.json").write_text(json.dumps(release), encoding="utf-8")
    report = gate.validate_registry(root, clone=False)
    assert any("outside this registry's forge pattern" in p for p in report.problems), report.format()
    assert not any("is not the version" in p for p in report.problems), "a commit-pinned record carries no tag and no tag rule applies to it"
    # a record made before commits were pinned is still located by its tag, so a wrong tag on one is refused
    release["link"].pop("commit", None)
    release["link"]["tag"] = "nightly"
    (directory / "release.json").write_text(json.dumps(release), encoding="utf-8")
    report = gate.validate_registry(root, clone=False)
    assert any("is not the version" in p for p in report.problems), report.format()


def test_an_edited_manifest_or_a_stale_index_or_readme_is_refused(tmp_path: Path):
    root = _copy(tmp_path)
    directory, release, manifest = _first_record(root)
    manifest["description"] = "edited after the record was resolved"
    (directory / "floofy.json").write_text(json.dumps(manifest), encoding="utf-8")
    report = gate.validate_registry(root, clone=False)
    assert any("manifestSha256" in p or "is stale" in p for p in report.problems), report.format()
    root2 = _copy(tmp_path / "second")
    readme = root2 / "README.md"
    text = readme.read_text(encoding="utf-8")
    start, end = text.index("<!-- mods:begin -->"), text.index("<!-- mods:end -->")
    readme.write_text(text[:start] + "<!-- mods:begin -->\n| stale | | | | | |\n" + text[end:], encoding="utf-8")
    report = gate.validate_registry(root2, clone=False)
    assert any("mod table is stale" in p for p in report.problems), report.format()


def test_an_unsigned_or_resigned_index_is_refused(tmp_path: Path):
    root = _copy(tmp_path)
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    index["generatedAt"] = "2000-01-01T00:00:00Z"  # the index content changed; the committed signature no longer covers it
    (root / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    report = gate.validate_registry(root, clone=False)
    assert any("index.json: signature" in p for p in report.problems), report.format()


def test_the_module_entry_point_reports_and_exits_nonzero_on_a_problem(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    root = _copy(tmp_path)
    (root / "schema" / "reserved-names.json").unlink()
    assert gate.main([str(root), "--no-clone"]) == 1
    out = capsys.readouterr().out
    assert "reserved names" in out and "FAIL" in out
