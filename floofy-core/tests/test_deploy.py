"""Deployment manifest, backups, added-file naming and drift tests (Requirement 5.2, 5.3; task 3.2)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from floofy_core.deploy import (
    ADDED_MARKER,
    BACKUP_SUFFIX,
    DEFAULT_ACTIONS,
    Backups,
    DeployManifest,
    DriftAction,
    DriftClass,
    added_name,
    atomic_write_text,
    classify_added,
    classify_drift,
    classify_file,
    classify_sidelined,
    is_floofy_added,
    iter_manifests,
    manifest_filename,
    sha256_bytes,
    sha256_file,
)

# --- naming --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,tag,expected",
    [
        ("App-x1.js", None, "App-x1-floofy.js"),
        ("App-x1.js", "1a2b3c4d", "App-x1-1a2b3c4d-floofy.js"),
        ("logo.png", None, "logo-floofy.png"),
        ("a.tar.gz", None, "a.tar-floofy.gz"),
        ("noext", None, "noext-floofy"),
        ("already-floofy.js", None, "already-floofy.js"),
    ],
)
def test_added_name(name: str, tag: str | None, expected: str) -> None:
    assert added_name(name, tag) == expected
    assert is_floofy_added(expected)


@pytest.mark.parametrize("name", ["main-abc.js", "index.html", "x.js.floofybak", "floofy.json", "a-floofy.js.floofybak", "-floofyx.js"])
def test_not_added(name: str) -> None:
    assert not is_floofy_added(name)


def test_manifest_filename_is_safe() -> None:
    assert manifest_filename("kind:0.7.0.5") == "kind_0.7.0.5.json"
    assert manifest_filename("venv:crew-venv-0.7.0:0.7.0") == "venv_crew-venv-0.7.0_0.7.0.json"
    assert manifest_filename("app:/x/y z:1.0.0") == "app_x_y_z_1.0.0.json"


# --- manifest ------------------------------------------------------------------------


def test_manifest_round_trip_and_shape(tmp_path: Path) -> None:
    manifest = DeployManifest(payload="kind:0.7.0.5", host_version="0.7.0.5", edition="external", payload_root=str(tmp_path))
    manifest.record_patch(tmp_path / "index.html", "aa", "bb", mod="rimuru-branding", part="2")
    manifest.record_added(tmp_path / "assets" / "App-x-floofy.js", "cc", mod="rimuru-branding")
    manifest.record_sidelined(tmp_path / "assets" / "main.js.br", tmp_path / "assets" / "main.js.br.floofybak", mod="rimuru-branding")
    path = DeployManifest.path_for(tmp_path / "home", "kind:0.7.0.5")
    assert path == tmp_path / "home" / "deploy" / "kind_0.7.0.5.json"
    manifest.save(path)

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema"] == 1 and "floofycrewVersion" in document
    assert document["payload"] == "kind:0.7.0.5" and document["hostVersion"] == "0.7.0.5" and document["edition"] == "external"
    assert set(document["files"][0]) == {"path", "orig_sha256", "patched_sha256", "mod", "part", "ts", "backup"}
    assert set(document["added"][0]) == {"path", "sha256", "mod", "ts"}
    assert document["sidelined"][0]["moved_to"].endswith(BACKUP_SUFFIX)

    loaded = DeployManifest.load(path)
    assert loaded.to_dict() == manifest.to_dict()
    assert loaded.find_file(tmp_path / "index.html").orig_sha256 == "aa"
    assert not loaded.is_empty
    assert [m.payload for _, m in iter_manifests(tmp_path / "home")] == ["kind:0.7.0.5"]


def test_record_patch_keeps_first_original(tmp_path: Path) -> None:
    manifest = DeployManifest("p", "1.0.0", "external")
    first = manifest.record_patch("/f", "orig", "p1", "m", "0")
    second = manifest.record_patch("/f", "other", "p2", "m", "1")
    assert first is second and second.orig_sha256 == "orig" and second.patched_sha256 == "p2" and second.part == "1"
    manifest.record_added("/a", "x", "m")
    manifest.record_added("/a", "y", "m2")
    assert [a.sha256 for a in manifest.added] == ["y"]
    manifest.forget("/f")
    manifest.forget("/a")
    assert manifest.is_empty


def test_load_rejects_wrong_schema(tmp_path: Path) -> None:
    bad = tmp_path / "x.json"
    bad.write_text(json.dumps({"schema": 2, "payload": "p", "hostVersion": "1"}), encoding="utf-8")
    with pytest.raises(ValueError):
        DeployManifest.load(bad)
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "junk.json").write_text("{not json", encoding="utf-8")
    assert list(iter_manifests(tmp_path)) == []
    assert DeployManifest.load_if_exists(tmp_path / "missing.json") is None


def test_atomic_write_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "file.txt"
    atomic_write_text(target, "one")
    atomic_write_text(target, "two")
    assert target.read_text() == "two"
    assert sorted(p.name for p in (tmp_path / "sub").iterdir()) == ["file.txt"]


# --- backups -------------------------------------------------------------------------


def test_backups_first_write_only(tmp_path: Path) -> None:
    file = tmp_path / "chunk.js"
    file.write_text("original")
    backups = Backups()
    backup = backups.ensure(file)
    assert backup == tmp_path / ("chunk.js" + BACKUP_SUFFIX) and backup.read_text() == "original"
    file.write_text("patched")
    backups.ensure(file)
    assert backup.read_text() == "original"  # never overwritten
    assert backups.has_backup(file) and backups.is_backup(backup) and backups.original_of(backup) == file
    assert backups.restore_file(file) and file.read_text() == "original" and not backup.exists()
    assert not backups.restore_file(file)


def test_backups_refresh_and_discard(tmp_path: Path) -> None:
    file = tmp_path / "chunk.js"
    file.write_text("v1")
    backups = Backups(suffix=".bak2")
    backups.ensure(file)
    file.write_text("v2-from-host")
    assert backups.refresh(file).read_text() == "v2-from-host"
    assert backups.discard(file) and not backups.discard(file)


# --- drift table ---------------------------------------------------------------------


ORIG, PATCHED, OTHER = "o" * 64, "p" * 64, "x" * 64


@pytest.mark.parametrize(
    "disk,backup,changed,expected",
    [
        (None, True, False, DriftClass.MOD_DELETED),
        (None, False, True, DriftClass.MOD_DELETED),
        (PATCHED, True, False, DriftClass.CLEAN),
        (PATCHED, False, True, DriftClass.CLEAN),
        (ORIG, True, False, DriftClass.HOST_UPDATED),
        (ORIG, False, False, DriftClass.HOST_UPDATED),
        (OTHER, True, True, DriftClass.HOST_UPDATED),
        (OTHER, False, False, DriftClass.HOST_UPDATED),
        (OTHER, True, False, DriftClass.USER_EDITED),
    ],
)
def test_decision_table(disk: str | None, backup: bool, changed: bool, expected: DriftClass) -> None:
    result = classify_file(orig_sha256=ORIG, patched_sha256=PATCHED, disk_sha256=disk, backup_present=backup, host_version_changed=changed)
    assert result is expected


def test_default_actions() -> None:
    assert DEFAULT_ACTIONS[DriftClass.CLEAN] is DriftAction.NONE
    assert DEFAULT_ACTIONS[DriftClass.HOST_UPDATED] is DriftAction.RE_DERIVE
    assert DEFAULT_ACTIONS[DriftClass.USER_EDITED] is DriftAction.SKIP
    assert DEFAULT_ACTIONS[DriftClass.MOD_DELETED] is DriftAction.RE_ADD


def test_added_and_sidelined_tables() -> None:
    assert classify_added(sha256="a", disk_sha256=None) is DriftClass.MOD_DELETED
    assert classify_added(sha256="a", disk_sha256="a") is DriftClass.CLEAN
    assert classify_added(sha256="a", disk_sha256="b") is DriftClass.USER_EDITED
    assert classify_sidelined(original_present=False, moved_present=True) is DriftClass.CLEAN
    assert classify_sidelined(original_present=True, moved_present=True) is DriftClass.HOST_UPDATED
    assert classify_sidelined(original_present=False, moved_present=False) is DriftClass.MOD_DELETED


def test_classify_drift_against_disk(tmp_path: Path) -> None:
    clean = tmp_path / "clean.js"
    edited = tmp_path / "edited.js"
    relaid = tmp_path / "relaid.js"
    gone = tmp_path / "gone.js"
    added = tmp_path / "x-floofy.js"
    for f in (clean, edited, relaid, gone):
        f.write_text("orig:" + f.name)
    backups = Backups()
    for f in (clean, edited, relaid, gone):
        backups.ensure(f)
        f.write_text("patched:" + f.name)
    added.write_text("added")

    manifest = DeployManifest("p", "0.7.0", "external")
    for f in (clean, edited, relaid, gone):
        manifest.record_patch(f, sha256_bytes(f"orig:{f.name}".encode()), sha256_file(f), "m", "0")
    manifest.record_added(added, sha256_file(added), "m")
    manifest.record_sidelined(tmp_path / "main.js.br", tmp_path / ("main.js.br" + BACKUP_SUFFIX), "m")
    (tmp_path / ("main.js.br" + BACKUP_SUFFIX)).write_bytes(b"br")

    edited.write_text("hand edit")
    relaid.write_text("orig:relaid.js")
    gone.unlink()
    added.write_text("tampered")

    report = classify_drift(manifest, current_host_version="0.7.0", backups=backups)
    assert report.classes[str(clean)] is DriftClass.CLEAN
    assert report.classes[str(edited)] is DriftClass.USER_EDITED and report.actions[str(edited)] is DriftAction.SKIP
    assert report.classes[str(relaid)] is DriftClass.HOST_UPDATED and report.actions[str(relaid)] is DriftAction.RE_DERIVE
    assert report.classes[str(gone)] is DriftClass.MOD_DELETED and report.actions[str(gone)] is DriftAction.RE_ADD
    assert report.classes[str(added)] is DriftClass.USER_EDITED
    assert report.classes[str(tmp_path / "main.js.br")] is DriftClass.CLEAN
    assert not report.clean and report.summary()["user-edited"] == 2
    assert report.paths(DriftClass.MOD_DELETED) == [str(gone)]

    # A payload that now reports another version turns hand edits into host updates.
    report = classify_drift(manifest, current_host_version="0.7.1", backups=backups)
    assert report.classes[str(edited)] is DriftClass.HOST_UPDATED


# --- properties ----------------------------------------------------------------------

_hash = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)


@settings(max_examples=200, deadline=None)
@given(orig=_hash, patched=_hash, disk=st.one_of(st.none(), _hash), backup=st.booleans(), changed=st.booleans())
def test_classifier_total_and_consistent(orig: str, patched: str, disk: str | None, backup: bool, changed: bool) -> None:
    result = classify_file(orig_sha256=orig, patched_sha256=patched, disk_sha256=disk, backup_present=backup, host_version_changed=changed)
    assert result in DriftClass
    if disk is None:
        assert result is DriftClass.MOD_DELETED
    elif disk == patched:
        assert result is DriftClass.CLEAN
    elif disk == orig or changed or not backup:
        assert result is DriftClass.HOST_UPDATED
    else:
        assert result is DriftClass.USER_EDITED
    # user-edited requires our backup present and no host movement: never claimed otherwise
    if result is DriftClass.USER_EDITED:
        assert backup and not changed and disk not in (orig, patched)


@given(
    st.text(alphabet="abcXYZ019._-", min_size=1, max_size=30).filter(lambda s: s.strip(".") != ""),
    st.one_of(st.none(), st.text(alphabet="0123456789abcdef", min_size=8, max_size=8)),
)
def test_added_name_is_recognised_and_idempotent(name: str, tag: str | None) -> None:
    result = added_name(name, tag)
    assert is_floofy_added(result)
    assert added_name(result) == result
    assert ADDED_MARKER in result



# --- canonical paths (the $HOME-symlink split-brain) ----------------------------------
#
# The gateway reaches a payload through a symlinked $HOME (/home/x -> /local/home/x)
# while a shell spells the canonical path. Keyed by raw string, one file collected two
# manifest entries; each process classified the other's write as user-edited, and inside
# one apply index.html became two work items, the later commit dropping the earlier
# one's ops (the custom-themes first-frame boot script). One canonical spelling per
# file closes it.


def _linked_home(tmp_path: Path) -> tuple[Path, Path]:
    real = tmp_path / "real-home"
    real.mkdir()
    link = tmp_path / "home-link"
    link.symlink_to(real, target_is_directory=True)
    return real, link


def test_manifest_records_and_finds_one_entry_across_spellings(tmp_path: Path) -> None:
    real, link = _linked_home(tmp_path)
    (real / "index.html").write_text("original", encoding="utf-8")
    manifest = DeployManifest("venv:crew-venv:0.7.0.5", "0.7.0.5", "external")
    manifest.record_patch(link / "index.html", "orig", "patched-1", "floofycrew", "boot")
    # the second writer (another process) spells the same file canonically
    entry = manifest.record_patch(real / "index.html", "orig", "patched-2", "custom-themes", "4")
    assert len(manifest.files) == 1
    assert entry.patched_sha256 == "patched-2"
    assert manifest.find_file(link / "index.html") is manifest.find_file(real / "index.html")
    manifest.forget(link / "index.html")
    assert manifest.is_empty


def test_manifest_load_heals_duplicate_spellings(tmp_path: Path) -> None:
    real, link = _linked_home(tmp_path)
    (real / "index.html").write_text("patched-by-b", encoding="utf-8")
    document = {
        "schema": 1,
        "payload": "venv:crew-venv:0.7.0.5",
        "hostVersion": "0.7.0.5",
        "edition": "external",
        "payloadRoot": str(real),
        "updatedAt": "2026-09-22T00:00:00Z",
        "files": [
            {"path": str(link / "index.html"), "orig_sha256": "orig-true", "patched_sha256": "sha-a", "mod": "floofycrew", "part": "boot", "ts": "2026-09-21T20:25:31Z", "backup": None},
            {"path": str(real / "index.html"), "orig_sha256": "orig-true", "patched_sha256": "sha-b", "mod": "custom-themes", "part": "4", "ts": "2026-09-22T15:46:33Z", "backup": None},
        ],
        "added": [
            {"path": str(link / "a-floofy.js"), "sha256": "s1", "mod": "m", "ts": "2026-09-21T00:00:00Z"},
            {"path": str(real / "a-floofy.js"), "sha256": "s2", "mod": "m", "ts": "2026-09-22T00:00:00Z"},
        ],
        "sidelined": [],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    manifest = DeployManifest.load(path)
    assert [f.path for f in manifest.files] == [str(real / "index.html")]
    kept = manifest.files[0]
    assert kept.patched_sha256 == "sha-b" and kept.part == "4", "the newest record describes the last actual write"
    assert kept.orig_sha256 == "orig-true"
    assert [a.path for a in manifest.added] == [str(real / "a-floofy.js")]
    assert manifest.added[0].sha256 == "s2"
    # drift over the healed manifest sees ONE clean file, no phantom user-edit
    report = classify_drift(manifest, current_host_version="0.7.0.5", hasher=lambda p: "sha-b")
    assert report.classes[str(real / "index.html")] is DriftClass.CLEAN
