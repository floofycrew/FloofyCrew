"""registry-tools: build, sign/verify, validate-submission, compat-merge on a scratch registry repository (task 7.2, 7.6; Requirement 8.7, 13.3)."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from floofy_core.compat import CompatCache
from floofy_core.registry import IndexCache
from floofy_core.schema import load_schema
from floofy_core.schema.check import validate_instance
from floofy_core.scaffold import refresh_files
from floofy_core.signing import load_private_key
from floofy_core.sigverify import verify_detached

from registry_tools.build import build_index, fold_compat, index_matches
from registry_tools.cli import main
from registry_tools.compat_merge import CompatMergeError, load_compat, merge_row, override_cell
from registry_tools.masquerade import check_masquerade, normalize
from registry_tools.repo import RegistryRepo, RepoError
from registry_tools.submission import validate_submission

from registry_testing import EXAMPLES_DIR, ScratchRegistry, zip_mod

pytestmark = pytest.mark.registry


def _licensed_copy(registry: ScratchRegistry, kind: str, target: Path, **overrides) -> Path:
    """The shipped example mod with a LICENSE file and a widened framework range, ``files[]`` rehashed."""
    root = registry.copy_example(kind, target)
    (root / "LICENSE").write_text("MIT License\n\nCopyright (c) 2026 tests\n", encoding="utf-8")
    manifest = json.loads((root / "floofy.json").read_text(encoding="utf-8"))
    manifest["dependsOn"] = {"floofycrew": ">=0.0.0"}
    manifest.update(overrides)
    (root / "floofy.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    refresh_files(root)
    return root


@pytest.fixture
def registry(tmp_path: Path) -> ScratchRegistry:
    scratch = ScratchRegistry(tmp_path / "registry")
    theme = _licensed_copy(scratch, "theme", tmp_path / "mods" / "example-theme")
    scratch.add_version(theme, curated={"tags": ["theme", "example"]})
    newer = _licensed_copy(scratch, "theme", tmp_path / "mods" / "example-theme-2", version="1.1.0")
    scratch.add_version(newer, published_at="2026-09-20T00:00:00Z")
    app = _licensed_copy(scratch, "app", tmp_path / "mods" / "example-app")
    scratch.add_version(app)
    scratch.write_compat([
        {"edition": "internal", "channel": "beta", "hostVersion": "0.7.0.5", "framework": {"loader": "ok"}, "mods": {"example-theme@1.0.0": {"verdict": "tested", "run": "https://forge.example/1"}, "example-theme@1.1.0": "expected", "example-app@1.0.0": "broken"}},
        {"edition": "internal", "channel": "stable", "hostVersion": "0.7.0.5", "framework": {"loader": "ok"}, "mods": {"example-theme@1.1.0": "tested"}},
        {"edition": "external", "channel": "stable", "hostVersion": "0.7.0", "framework": {"loader": "ok"}, "mods": {"example-theme@1.0.0": "expected"}, "overrides": [{"cell": "example-theme@1.0.0", "verdict": "broken", "reason": "flashes", "by": "tests"}]},
    ])
    return scratch


# --- build -------------------------------------------------------------------------------------------------------


def test_build_index_from_records_folds_the_matrix_and_passes_the_schema(registry: ScratchRegistry, tmp_path: Path):
    repo = RegistryRepo.load(registry.root)
    result = build_index(repo, floofycrew_version="0.0.0-test")
    assert result.ok, result.problems
    index = result.index
    assert validate_instance(index, load_schema("index")) == []
    assert index["source"] == "test:scratch-registry" and index["floofycrew"] == "0.0.0-test"
    theme = next(m for m in index["mods"] if m["id"] == "example-theme")
    assert theme["repo"] == "https://git.example/mods/example-theme" and theme["tags"] == ["example", "theme"] and theme["license"] == "MIT"
    assert [v["version"] for v in theme["versions"]] == ["1.0.0", "1.1.0"], "versions sorted ascending"
    v100, v110 = theme["versions"]
    assert v100["compat"] == {"0.7.0": "broken", "0.7.0.5": "tested"}, "the override wins on 0.7.0; the run cell on 0.7.0.5"
    assert v110["compat"] == {"0.7.0.5": "expected"}, "two rows for one host version: the most cautious verdict wins"
    assert v100["kirocrew"] == ">=0.7.0 <0.9.0" and v100["editions"] == ["internal", "external"] and v100["dependencies"] == {"floofycrew": ">=0.0.0"}
    assert v100["files"][0]["url"].startswith("https://assets.example/") and len(v100["files"][0]["sha256"]) == 64 and v100["files"][0]["size"] > 0
    assert v100["channel"] == "stable" and v100["publishedAt"] == "2026-09-19T00:00:00Z" and v100["kinds"] == ["theme"] and "tag" not in v100
    app = next(m for m in index["mods"] if m["id"] == "example-app")
    assert app["versions"][0]["app"] == {"name": "example-app", "subdirectory": "app"} and app["versions"][0]["compat"] == {"0.7.0.5": "broken"}
    assert app["repo"] == "https://git.example/mods/example-app"
    # the client reads what the tool wrote
    out = tmp_path / "index.json"
    assert main(["build", str(registry.root), "--out", str(out)]) == 0
    cache = IndexCache.load_path(out)
    assert sorted(m.id for m in cache.mods) == ["example-app", "example-theme"]
    best = cache.best_version("example-theme", base_version="0.7.0", edition="internal", host_version="0.7.0.5")
    assert best.version.version == "1.0.0" and best.verdict == "tested", "tested 1.0.0 beats expected 1.1.0 from the inline cells alone"
    assert cache.best_version("example-app", base_version="0.7.0", edition="internal", host_version="0.7.0.5").version is None
    # --check: the committed index must match the records
    assert main(["build", str(registry.root)]) == 0 and main(["build", str(registry.root), "--check"]) == 0
    stale = json.loads(repo.index_path.read_text(encoding="utf-8"))
    stale["mods"][0]["description"] = "edited by hand"
    repo.index_path.write_text(json.dumps(stale), encoding="utf-8")
    assert main(["build", str(registry.root), "--check"]) == 1
    ok, message = index_matches(repo.index_path, result)
    assert not ok and "stale" in message
    assert fold_compat(None, "x", "1.0.0") == {}
    # the publisher's release stamp never makes the CI gate fail: `build --floofycrew 1.0.0`
    # then a plain `build --check` (the templates' gate) passes, an explicit different stamp does not
    assert main(["build", str(registry.root), "--floofycrew", "1.0.0"]) == 0
    assert json.loads(repo.index_path.read_text(encoding="utf-8"))["floofycrew"] == "1.0.0"
    assert main(["build", str(registry.root), "--check"]) == 0
    assert main(["build", str(registry.root), "--check", "--floofycrew", "1.0.0"]) == 0
    assert main(["build", str(registry.root), "--check", "--floofycrew", "1.0.1"]) == 1


def test_build_carries_the_release_notes_link_and_the_client_reads_it(registry: ScratchRegistry, tmp_path: Path):
    """Requirement 16.6: a version's `changelog` (release.json, else the manifest's links.changelog) is indexed, https only."""
    release_path = registry.root / "mods" / "example-theme" / "1.1.0" / "release.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release["changelog"] = "https://git.example/mods/example-theme/releases/1.1.0"
    release_path.write_text(json.dumps(release, indent=2), encoding="utf-8")
    plain_path = registry.root / "mods" / "example-theme" / "1.0.0" / "release.json"
    plain = json.loads(plain_path.read_text(encoding="utf-8"))
    plain["changelog"] = "http://plain.example/notes"  # plaintext: never indexed
    plain_path.write_text(json.dumps(plain, indent=2), encoding="utf-8")
    repo = RegistryRepo.load(registry.root)
    result = build_index(repo)
    assert result.ok, result.problems
    assert validate_instance(result.index, load_schema("index")) == []
    theme = next(m for m in result.index["mods"] if m["id"] == "example-theme")
    v100, v110 = theme["versions"]
    assert v110["changelog"] == "https://git.example/mods/example-theme/releases/1.1.0" and "changelog" not in v100
    out = tmp_path / "index.json"
    assert main(["build", str(registry.root), "--out", str(out)]) == 0
    cache = IndexCache.load_path(out)
    entry = cache.find("example-theme")
    assert entry.version("1.1.0").changelog == "https://git.example/mods/example-theme/releases/1.1.0" and entry.version("1.0.0").changelog is None
    assert entry.version("1.1.0").to_dict()["changelog"].startswith("https://")


def test_build_reports_record_defects(registry: ScratchRegistry, tmp_path: Path):
    repo = RegistryRepo.load(registry.root)
    (registry.root / "mods" / "example-app" / "mod.json").unlink()
    result = build_index(repo)
    assert result.ok, "the manifest's links.source is the fallback repository"
    manifest_path = registry.root / "mods" / "example-app" / "1.0.0" / "floofy.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("links")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = build_index(repo)
    assert not result.ok and any("no repository" in p for p in result.problems)
    (registry.root / "mods" / "example-theme" / "1.0.0" / "release.json").write_text(json.dumps({"files": [{"url": "http://plain.example/x.zip", "sha256": "zz", "size": -1}]}), encoding="utf-8")
    result = build_index(repo)
    messages = " ".join(result.problems)
    assert "files/0/url" in messages and "files/0/sha256" in messages and "files/0/size" in messages and "publishedAt" in messages
    bad_dir = registry.root / "mods" / "example-theme" / "9.9.9"
    bad_dir.mkdir()
    (bad_dir / "floofy.json").write_text(json.dumps({"id": "example-theme", "version": "1.0.0"}), encoding="utf-8")
    (bad_dir / "release.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RepoError, match="does not match the directory"):
        repo.read_mod("example-theme")
    assert main(["build", str(registry.root)]) == 1
    with pytest.raises(RepoError, match="no registry.json"):
        RegistryRepo.load(tmp_path / "nowhere")
    assert main(["build", str(tmp_path / "nowhere")]) == 2


# --- sign / verify -------------------------------------------------------------------------------------------------


def test_keygen_build_sign_verify_round_trip(registry: ScratchRegistry, tmp_path: Path, capsys):
    key = tmp_path / "keys" / "reg.json"
    assert main(["keygen", "--out", str(key), "--comment", "scratch"]) == 0
    pair = load_private_key(key)
    config = json.loads((registry.root / "registry.json").read_text(encoding="utf-8"))
    config["keyId"] = pair.key_id
    (registry.root / "registry.json").write_text(json.dumps(config), encoding="utf-8")
    assert main(["build", str(registry.root), "--sign", str(key)]) == 0
    index, signature = registry.root / "index.json", registry.root / "index.json.sig"
    assert signature.is_file() and json.loads(signature.read_text(encoding="utf-8"))["keyId"] == pair.key_id
    public_record = key.with_name(key.name + ".pub.json")
    assert main(["verify", str(index), "--public-key", str(public_record)]) == 0
    assert main(["verify", str(index), "--public-key", str(public_record), "--key-id", "0" * 16]) == 1
    other = tmp_path / "keys" / "other.json"
    assert main(["keygen", "--out", str(other)]) == 0
    assert main(["verify", str(index), "--public-key", str(other.with_name(other.name + ".pub.json"))]) == 1, "an unknown key never verifies"
    assert main(["build", str(registry.root), "--sign", str(other)]) == 1, "registry.json pins the signing key id"
    tampered = json.loads(index.read_text(encoding="utf-8"))
    tampered["mods"][0]["versions"][0]["files"][0]["sha256"] = "00" * 32
    index.write_text(json.dumps(tampered, indent=4), encoding="utf-8")
    assert main(["verify", str(index), "--public-key", str(public_record)]) == 1
    assert "invalid" in capsys.readouterr().out
    # sign any document: compat.json too
    assert main(["sign", str(registry.root / "compat.json"), "--key", str(key)]) == 0
    assert verify_detached((registry.root / "compat.json").read_bytes(), (registry.root / "compat.json.sig").read_bytes(), {pair.key_id: pair.public_key}).verified
    assert main(["verify", str(registry.root / "compat.json")]) == 2, "no keys given"
    assert main(["sign", str(index), "--key", str(tmp_path / "missing.json")]) == 2


# --- validate-submission -------------------------------------------------------------------------------------------


def test_validate_submission_accepts_a_consistent_record_and_names_every_defect(registry: ScratchRegistry, tmp_path: Path, capsys):
    archive = tmp_path / "assets" / "example-theme-1.0.0.zip"
    report = validate_submission(archive, registry=registry.root, tag="1.0.0")
    assert report.ok, report.format()
    assert report.mod_id == "example-theme" and report.version == "1.0.0" and report.validation["ok"] is True
    assert validate_submission(archive, registry=registry.root, tag="v1.0.0").ok, "a v-prefixed tag is accepted"
    assert main(["validate-submission", str(archive), "--registry", str(registry.root), "--tag", "1.0.0", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    wrong_tag = validate_submission(archive, registry=registry.root, tag="2.0.0")
    assert [f.code for f in wrong_tag.errors] == ["ReleaseTag"]
    # the directory form, without a registry: licence and masquerade checks still run
    root = tmp_path / "mods" / "example-theme"
    assert validate_submission(root).ok
    (root / "LICENSE").unlink()
    manifest = json.loads((root / "floofy.json").read_text(encoding="utf-8"))
    manifest.pop("license")
    (root / "floofy.json").write_text(json.dumps(manifest), encoding="utf-8")
    refresh_files(root)
    codes = {f.code for f in validate_submission(root).errors}
    assert codes == {"License", "LicenseFile", "Validate:SchemaViolation"}, codes
    # the archive's bytes must be the record's bytes
    (archive.parent / "tampered.zip").write_bytes(archive.read_bytes() + b"\x00")
    bad_archive = validate_submission(archive.parent / "tampered.zip", registry=registry.root)
    assert any(f.code == "ArchiveHash" for f in bad_archive.errors) or any(f.code.startswith("Validate:") for f in bad_archive.errors)
    # the record's manifest must equal the submitted one
    record_manifest = registry.root / "mods" / "example-theme" / "1.0.0" / "floofy.json"
    edited = json.loads(record_manifest.read_text(encoding="utf-8"))
    edited["description"] = "changed after the release"
    record_manifest.write_text(json.dumps(edited), encoding="utf-8")
    assert "RecordManifest" in {f.code for f in validate_submission(archive, registry=registry.root).errors}
    # an unknown version has no record
    missing = validate_submission(tmp_path / "assets" / "example-app-1.0.0.zip", registry=tmp_path / "elsewhere")
    assert missing.errors[0].code == "Registry"
    (registry.root / "mods" / "example-app" / "1.0.0" / "release.json").unlink()
    assert "Record" in {f.code for f in validate_submission(tmp_path / "assets" / "example-app-1.0.0.zip", registry=registry.root).errors}
    # unsafe archives are refused before extraction
    unsafe = tmp_path / "unsafe.zip"
    import zipfile

    with zipfile.ZipFile(unsafe, "w") as z:
        z.writestr("../escape.txt", "x")
    assert validate_submission(unsafe).errors[0].code == "UnsafeArchive"
    assert validate_submission(tmp_path / "nothing").errors[0].code == "NotAMod"
    assert main(["validate-submission", str(root)]) == 1


def test_masquerade_check_normalises_homoglyphs_and_honours_the_allowlist(tmp_path: Path):
    assert normalize("K1r0-Crew") == "klrocrew" and normalize("Кiro") == "kiro" and normalize("ｋiro crew") == "kirocrew"
    assert [h.term for h in check_masquerade("k1r0crew-official", None)] == ["kirocrew"]
    assert [h.term for h in check_masquerade("amaz0n-tools", None)] == ["amazon"]
    assert [h.field for h in check_masquerade("my-theme", "Official KiroCrew Update")] == ["name"]
    assert check_masquerade("my-theme", "A theme for KiroCrew") , "the host's full name inside a word/name is flagged; curators allowlist legitimate 'for KiroCrew' names"
    assert check_masquerade("rimuru-branding", "Rimuru branding") == []
    assert check_masquerade("screwdriver", "Screwdriver") != [], "substring on the id is deliberate (curated registry)"
    assert check_masquerade("screwdriver", "Screwdriver", allowlist={"screwdriver"}) == []
    assert check_masquerade("paws", "Paws", terms=("kirocrew", "kiro")) == [], "the term list is the registry's choice"
    # through the submission gate and the repository's allowlist file
    scratch = ScratchRegistry(tmp_path / "registry")
    root = _licensed_copy(scratch, "theme", tmp_path / "mods" / "kiro-theme", id="kiro-theme", name="Kiro theme")
    scratch.add_version(root)
    report = validate_submission(root, registry=scratch.root)
    assert [f.code for f in report.errors] == ["Masquerade", "Masquerade"] and "kiro" in report.errors[0].message, "the id and the display name are both flagged"
    (scratch.root / "masquerade-allow.txt").write_text("# cleared by the curators\nkiro-theme  # first-party\n", encoding="utf-8")
    assert validate_submission(root, registry=scratch.root).ok
    assert main(["validate-submission", str(root), "--terms", "amazon,aws"]) == 0


def test_app_kind_record_checks_the_app_name(registry: ScratchRegistry, tmp_path: Path):
    archive = tmp_path / "assets" / "example-app-1.0.0.zip"
    assert validate_submission(archive, registry=registry.root).ok, "app.json's name equals the mod id, the default row name"
    release = registry.root / "mods" / "example-app" / "1.0.0" / "release.json"
    record = json.loads(release.read_text(encoding="utf-8"))
    record["app"] = {"name": "renamed-app"}
    release.write_text(json.dumps(record), encoding="utf-8")
    report = validate_submission(archive, registry=registry.root)
    assert [f.code for f in report.errors] == ["AppName"]
    repo = RegistryRepo.load(registry.root)
    assert repo.read_version("example-app", "1.0.0").app_facts() == {"name": "renamed-app", "subdirectory": "app"}
    assert repo.read_version("example-theme", "1.0.0").app_facts() is None


# --- compat-merge ------------------------------------------------------------------------------------------------


def test_compat_merge_replaces_rows_keeps_overrides_and_validates(registry: ScratchRegistry, tmp_path: Path):
    compat_path = registry.root / "compat.json"
    document = load_compat(compat_path)
    run = {"edition": "external", "channel": "stable", "hostVersion": "0.7.0", "framework": {"loader": "degraded", "spaFingerprints": {"matched": 40, "total": 42, "missed": ["a", "b"]}, "pythonAnchors": {"matched": 12, "total": 12}, "shimVersion": None}, "mods": {"example-theme@1.0.0": {"verdict": "tested", "run": "https://forge.example/9"}}, "run": "https://forge.example/9", "checkedAt": "2026-09-20T01:00:00Z", "_ledger": {"ignored": True}}
    merged = merge_row(document, run, source="test:scratch-registry")
    replaced = next(r for r in merged["rows"] if r["hostVersion"] == "0.7.0")
    assert replaced["framework"]["loader"] == "degraded" and "_ledger" not in replaced and replaced["overrides"][0]["cell"] == "example-theme@1.0.0", "the human override survives a re-run"
    assert merged["source"] == "test:scratch-registry" and len(merged["rows"]) == 3
    assert [(r["edition"], r["channel"], r["hostVersion"]) for r in merged["rows"]] == [("external", "stable", "0.7.0"), ("internal", "beta", "0.7.0.5"), ("internal", "stable", "0.7.0.5")]
    cache = CompatCache.from_dict(merged)
    assert cache.row_for("external", "stable", "0.7.0").verdict("example-theme", "1.0.0") == "broken", "the override still wins in the reader"
    appended = merge_row(merged, {**run, "hostVersion": "0.7.1", "overrides": [], "checkedAt": "2026-09-20T02:00:00Z"})
    assert len(appended["rows"]) == 4 and [r["hostVersion"] for r in appended["rows"] if r["edition"] == "external"] == ["0.7.0", "0.7.1"]
    with pytest.raises(CompatMergeError, match="framework/loader"):
        merge_row(document, {**run, "framework": {"loader": "meh"}})
    with pytest.raises(CompatMergeError, match="lacks"):
        merge_row(document, {"edition": "internal"})
    overridden = override_cell(merged, edition="internal", channel="beta", host_version="0.7.0.5", cell="example-theme@1.1.0", verdict="broken", reason="regression seen", by="tests")
    row = next(r for r in overridden["rows"] if r["channel"] == "beta")
    assert row["overrides"][-1]["cell"] == "example-theme@1.1.0" and row["overrides"][-1]["at"]
    with pytest.raises(CompatMergeError, match="no row"):
        override_cell(merged, edition="internal", channel="nightly", host_version="0.7.0.5", cell="x@1.0.0", verdict="broken", reason="r", by="b")
    # the CLI: merge a run file, then a human override, then sign
    run_file = tmp_path / "run.json"
    run_file.write_text(json.dumps({**run, "hostVersion": "0.7.2"}), encoding="utf-8")
    assert main(["compat-merge", str(compat_path), str(run_file)]) == 0
    assert any(r["hostVersion"] == "0.7.2" for r in load_compat(compat_path)["rows"])
    assert main(["compat-merge", str(compat_path), "--override", "example-theme@1.0.0=broken", "--edition", "external", "--channel", "stable", "--host-version", "0.7.2", "--reason", "boot flash", "--by", "tests"]) == 0
    assert main(["compat-merge", str(compat_path), "--override", "x=broken"]) == 2
    assert main(["compat-merge", str(compat_path)]) == 2
    key = tmp_path / "k.json"
    assert main(["keygen", "--out", str(key)]) == 0
    fresh = tmp_path / "fresh-compat.json"
    assert main(["compat-merge", str(fresh), str(run_file), "--sign", str(key)]) == 0
    assert fresh.with_name("fresh-compat.json.sig").is_file() and load_compat(fresh)["rows"][0]["hostVersion"] == "0.7.2"
    assert validate_instance(load_compat(compat_path), load_schema("compat")) == []


def test_zip_helper_and_repo_layout_edges(tmp_path: Path):
    scratch = ScratchRegistry(tmp_path / "registry", key_id="a" * 16)
    repo = RegistryRepo.load(scratch.root)
    assert repo.key_id == "a" * 16 and list(repo.mods()) == [] and repo.allowlist() == set()
    assert repo.archive_url(mod_id="m", version="1.0.0", tag="v1.0.0", file="m.zip") == "https://assets.example/m-v1.0.0/m.zip"
    root = _licensed_copy(scratch, "skill", tmp_path / "mods" / "example-skill")
    payload = zip_mod(root)
    assert payload[:2] == b"PK" and base64.b64encode(payload)
    with pytest.raises(RepoError, match="not a valid mod id"):
        (scratch.root / "mods" / "Bad Id").mkdir(parents=True)
        repo.read_mod("Bad Id")
