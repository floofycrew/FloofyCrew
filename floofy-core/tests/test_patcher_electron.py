"""``electron:`` patch targets end to end (Requirement 5.11): shell discovery, the Patcher's asar path, restore.

A desktop-bundle-shaped payload (``fake_payload(layout="nested")``) gets the reference
``app.asar`` under ``Contents/Resources`` and an Electron Framework stand-in carrying a
fuse wire. The mod under test is the shipped ``mods/mochi-pet-zoom-fix``, so its two
descriptors are exercised exactly as users get them.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from floofy_core.asar import ASAR_FUSE_SENTINEL, AsarArchive
from floofy_core.deploy import BACKUP_SUFFIX, DeployManifest
from floofy_core.electron import find_electron_shell
from floofy_core.patcher import Patcher, PlannedPatch, VerifyStatus
from floofy_core.patches import PatchDescriptor
from floofy_core.payloads import make_payload

from floofy_testing import fake_payload

FIXTURE = Path(__file__).parent / "fixtures" / "electron" / "app.asar"
MOD_DIR = Path(__file__).resolve().parents[2] / "mods" / "mochi-pet-zoom-fix"
MOCHI = MOD_DIR / "patches" / "mochi-pet-overlay.json"
COMPANION = MOD_DIR / "patches" / "companion-pet-overlay.json"


def fuse_wire(wire: str) -> bytes:
    return b"\x90" * 64 + ASAR_FUSE_SENTINEL + bytes([1, len(wire)]) + wire.encode("ascii") + b"\x00" * 64


def desktop_payload(root: Path, *, wire: str | None = "101100011", version: str = "0.7.0", current: bool = True):
    """A macOS-bundle-shaped payload: kiro_crew under backend-dist, app.asar and a framework binary beside it."""
    package_dir = fake_payload(root, version, layout="nested")
    resources = root / "Contents" / "Resources"
    shutil.copy(FIXTURE, resources / "app.asar")
    if wire is not None:
        framework = root / "Contents" / "Frameworks" / "Electron Framework.framework" / "Versions" / "A"
        framework.mkdir(parents=True)
        (framework / "Electron Framework").write_bytes(fuse_wire(wire))
    payload = make_payload(kind="app", root=root, package_dir=package_dir, source="t", current=current)
    return payload, resources / "app.asar"


def planned(path: Path, mod: str = "mochi-pet-zoom-fix", part: str = "0") -> PlannedPatch:
    return PlannedPatch(mod, part, PatchDescriptor.load(path))


def member(asar: Path, name: str) -> str:
    return AsarArchive.load(asar).read(name).decode("utf-8")


# --- discovery -----------------------------------------------------------------------------


def test_find_electron_shell_macos_layout(tmp_path: Path) -> None:
    payload, asar = desktop_payload(tmp_path / "KiroCrew.app")
    shell = find_electron_shell(payload.package_dir)
    assert shell is not None and shell.asar == asar and shell.platform == "macos"
    assert shell.fuses is not None and shell.fuses.wire == "101100011" and not shell.integrity_locked
    assert shell.binary is not None and shell.binary.name == "Electron Framework"
    assert "fuses 101100011" in shell.describe()


def test_find_electron_shell_flat_layout_and_none(tmp_path: Path) -> None:
    install = tmp_path / "opt" / "kirocrew"
    package_dir = fake_payload(install / "resources" / "backend", "0.7.0", layout="flat")
    (install / "resources" / "app.asar").write_bytes(FIXTURE.read_bytes())
    (install / "kirocrew").write_bytes(b"\x00" * (8 * 1024 * 1024) + fuse_wire("100011000"))
    shell = find_electron_shell(package_dir)
    assert shell is not None and shell.platform == "linux" and shell.integrity_locked is True
    assert shell.binary == install / "kirocrew"
    # gateway-only payload: nothing above it
    assert find_electron_shell(fake_payload(tmp_path / "plain", "0.7.0", layout="flat")) is None


def test_find_electron_shell_without_a_readable_binary(tmp_path: Path) -> None:
    payload, _asar = desktop_payload(tmp_path / "KiroCrew.app", wire=None)
    shell = find_electron_shell(payload.package_dir)
    assert shell is not None and shell.fuses is None and shell.binary is None and not shell.integrity_locked


# --- apply ----------------------------------------------------------------------------------


def test_apply_rewrites_the_archive_member_with_backup_and_manifest(tmp_path: Path) -> None:
    payload, asar = desktop_payload(tmp_path / "KiroCrew.app")
    before = asar.read_bytes()
    home = tmp_path / "home" / "floofy"
    patcher = Patcher(home, [payload], endpoints=[])
    report = patcher.apply([planned(MOCHI), planned(COMPANION, part="1")], verify=False)
    assert report.ok, report.payloads[0].errors
    result = report.payloads[0]
    assert str(asar) in result.written
    assert sorted(result.applied) == [
        "mochi-pet-zoom-fix#0 -> electron:mochi/petOverlays.js#ops/0",
        "mochi-pet-zoom-fix#0 -> electron:mochi/petOverlays.js#ops/1",
        "mochi-pet-zoom-fix#1 -> electron:crew-companion/petOverlay.js#ops/0",
        "mochi-pet-zoom-fix#1 -> electron:crew-companion/petOverlay.js#ops/1",
    ]
    assert result.skipped == []
    assert asar.with_name(asar.name + BACKUP_SUFFIX).read_bytes() == before
    patched = member(asar, "mochi/petOverlays.js")
    assert patched.count('partition: "persist:mochi-pet"') == 1 and patched.count("setZoomFactor(1)") == 1
    companion = member(asar, "crew-companion/petOverlay.js")
    assert companion.count('partition: "persist:mochi-pet"') == 1 and companion.count("setZoomFactor(1)") == 1
    # the decoys are untouched: the brain window's loadURL did not get the handler
    assert companion.index("setZoomFactor(1)") < companion.index("function createBrainWindow")
    assert member(asar, "main.js") == 'console.log("main");\n'
    manifest = DeployManifest.load(patcher.manifest_path(payload))
    record = manifest.find_file(str(asar))
    assert record is not None and record.orig_sha256 != record.patched_sha256 and "mochi-pet-zoom-fix" in record.mod

    # idempotent: revert-then-patch re-derives the same bytes from the backup and rewrites nothing
    again = patcher.apply([planned(MOCHI), planned(COMPANION, part="1")], verify=False).payloads[0]
    assert again.ok and again.written == [] and str(asar) in again.unchanged
    assert sorted(again.applied) == sorted(result.applied) and again.skipped == []
    assert asar.read_bytes() == AsarArchive.parse(asar.read_bytes()).pack()


def test_apply_skips_gateway_only_payloads_and_missing_members(tmp_path: Path) -> None:
    package_dir = fake_payload(tmp_path / "plain", "0.7.0", layout="flat")
    payload = make_payload(kind="fake", root=tmp_path / "plain", package_dir=package_dir, source="t", current=True)
    patcher = Patcher(tmp_path / "home" / "floofy", [payload], endpoints=[])
    result = patcher.apply([planned(MOCHI)], verify=False).payloads[0]
    assert result.ok and result.written == []
    assert [s["code"] for s in result.skipped] == ["NotApplicable"] and "no Electron shell" in result.skipped[0]["message"]

    desktop, asar = desktop_payload(tmp_path / "KiroCrew.app")
    ghost = PatchDescriptor.from_dict({"schema": 1, "target": "electron:mochi/gone.js", "ops": [{"op": "append-head", "content": "x"}]})
    patcher = Patcher(tmp_path / "home2" / "floofy", [desktop], endpoints=[])
    result = patcher.apply([PlannedPatch("m", "0", ghost)], verify=False).payloads[0]
    assert result.ok and result.written == [] and not asar.with_name(asar.name + BACKUP_SUFFIX).exists()
    assert [s["code"] for s in result.skipped] == ["NotApplicable"] and "not in the archive" in result.skipped[0]["message"]


def test_apply_refuses_an_integrity_locked_shell(tmp_path: Path) -> None:
    payload, asar = desktop_payload(tmp_path / "KiroCrew.app", wire="100010000")
    before = asar.read_bytes()
    patcher = Patcher(tmp_path / "home" / "floofy", [payload], endpoints=[])
    result = patcher.apply([planned(MOCHI)], verify=False).payloads[0]
    assert result.ok and result.written == [] and asar.read_bytes() == before
    assert [s["code"] for s in result.skipped] == ["IntegrityLocked"]
    assert "EnableEmbeddedAsarIntegrityValidation" in result.skipped[0]["message"]
    assert not asar.with_name(asar.name + BACKUP_SUFFIX).exists()


def test_apply_errors_on_a_corrupt_archive_without_writing(tmp_path: Path) -> None:
    payload, asar = desktop_payload(tmp_path / "KiroCrew.app")
    asar.write_bytes(b"not an asar at all")
    patcher = Patcher(tmp_path / "home" / "floofy", [payload], endpoints=[])
    result = patcher.apply([planned(MOCHI)], verify=False).payloads[0]
    assert not result.ok and "not an asar archive" in result.errors[0]
    assert asar.read_bytes() == b"not an asar at all" and not asar.with_name(asar.name + BACKUP_SUFFIX).exists()


# --- restore / verify -------------------------------------------------------------------------


def test_restore_by_manifest_and_by_sweep(tmp_path: Path) -> None:
    payload, asar = desktop_payload(tmp_path / "KiroCrew.app")
    before = asar.read_bytes()
    home = tmp_path / "home" / "floofy"
    patcher = Patcher(home, [payload], endpoints=[])
    patcher.apply([planned(MOCHI)], verify=False)
    assert asar.read_bytes() != before
    report = patcher.restore()
    assert asar.read_bytes() == before and not asar.with_name(asar.name + BACKUP_SUFFIX).exists()
    assert str(asar) in report.payloads[payload.id]["restored"]

    # a lost manifest: the sweep reaches the shell's Resources directory too
    patcher.apply([planned(MOCHI)], verify=False)
    patcher.manifest_path(payload).unlink()
    patcher.restore()
    assert asar.read_bytes() == before and not asar.with_name(asar.name + BACKUP_SUFFIX).exists()


def test_dropping_the_mod_restores_the_archive_on_the_next_apply(tmp_path: Path) -> None:
    payload, asar = desktop_payload(tmp_path / "KiroCrew.app")
    before = asar.read_bytes()
    patcher = Patcher(tmp_path / "home" / "floofy", [payload], endpoints=[])
    patcher.apply([planned(MOCHI)], verify=False)
    result = patcher.apply([], verify=False).payloads[0]
    assert asar.read_bytes() == before and str(asar) in result.restored
    assert not patcher.manifest_path(payload).exists()


def test_verify_reports_the_shell_as_checked_by_hash_only(tmp_path: Path) -> None:
    payload, asar = desktop_payload(tmp_path / "KiroCrew.app")
    patcher = Patcher(tmp_path / "home" / "floofy", [payload], endpoints=[])
    patcher.apply([planned(MOCHI)], verify=False)

    class Endpoint:
        label = "fake"

    from floofy_core import patcher as patcher_module

    monkey = pytest.MonkeyPatch()
    monkey.setattr(patcher_module, "served_version", lambda endpoint: "0.7.0")
    try:
        report = patcher.verify(payload, endpoint=Endpoint())  # type: ignore[arg-type]
    finally:
        monkey.undo()
    assert report.status is VerifyStatus.NOTHING and "app.asar" in report.detail and "Electron shell" in report.detail


def test_status_lists_the_archive(tmp_path: Path) -> None:
    payload, asar = desktop_payload(tmp_path / "KiroCrew.app")
    patcher = Patcher(tmp_path / "home" / "floofy", [payload], endpoints=[])
    patcher.apply([planned(MOCHI)], verify=False)
    entry = patcher.status().payloads[0].to_dict()
    assert entry["patched"] == 1 and entry["backups_present"] == 1 and entry["mods"] == ["mochi-pet-zoom-fix"]
    assert entry["drift"].get("clean") == 1, entry["drift"]
