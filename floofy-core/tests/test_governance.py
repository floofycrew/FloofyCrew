"""Governance awareness tests (Requirement 2.8, 5.9, 5.10, 11.2; task 3.6).

The snapshot reads policy, config, admission files and ``/api/status``; every
warning code is produced by the matching host state; the update-survival question
goes through the injected confirmer and a *no* is ``UserDeclined``; and — the
owner decision — no governance condition ever refuses an apply. The theme half:
the ported host validator accepts/refuses like the host and the direct write
produces a byte-identical tree from a copy of a real installed pack.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from floofy_core.gateway import GatewayEndpoint
from floofy_core.governance import (
    WARNING_CODES,
    AlwaysConfirm,
    CallableConfirmer,
    GovernanceLocations,
    GovernanceSnapshot,
    NeverConfirm,
    default_locations,
    survival_check,
    warnings_for,
)
from floofy_core.patcher import Patcher, PlannedPatch
from floofy_core.patches import PatchDescriptor
from floofy_core.payloads import make_payload
from floofy_core.themes import (
    ThemeValidationError,
    direct_write_theme,
    pack_files,
    sanitize_css_value,
    slugify_theme_name,
    validate_overrides_css,
    validate_theme_data,
    validate_theme_dir,
)

from floofy_testing import FakeGateway, fake_payload

LIVE_RIMURU = Path.home() / ".kiro" / "crew" / "themes" / "rimuru"

BOOT = {
    "schema": 1,
    "target": "kiro_crew/static/dist/index.html",
    "ops": [{"op": "append-head", "content": '<meta name="floofy-gov">\n', "marker": 'name="floofy-gov"'}],
}


def _host_home(tmp_path: Path, *, policy: dict | None = None, config: dict | None = None, admission: dict | None = None) -> Path:
    home = tmp_path / "hosthome"
    home.mkdir(exist_ok=True)
    if policy is not None:
        (home / "security_policy.json").write_text(json.dumps(policy), encoding="utf-8")
    if config is not None:
        (home / "config.json").write_text(json.dumps(config), encoding="utf-8")
    if admission is not None:
        (home / "app_admission.json").write_text(json.dumps(admission), encoding="utf-8")
    return home


# --- snapshot ----------------------------------------------------------------------------


def test_snapshot_reads_every_source(tmp_path: Path) -> None:
    home = _host_home(
        tmp_path,
        policy={"capabilities": {"theme_install": {"enabled": False}, "browse": {"enabled": True}}, "updates": {"source": "github.com/x/*", "apply_command": "fleet-update --apply"}},
        config={"agent": {"apps_allow_third_party": False, "apps_trusted": ["other"], "apps_trusted_local": []}},
        admission={"mode": "enforce", "banned": ["floofycrew", "evil"]},
    )
    snapshot = GovernanceSnapshot.read(default_locations(home))
    assert snapshot.theme_install_enabled is False and snapshot.capabilities["browse"] is True
    assert snapshot.update_pins.source == "github.com/x/*" and snapshot.update_pins.has_commands and snapshot.update_pins.pinned
    assert snapshot.apps_allow_third_party is False and snapshot.app_execution_allowed() is False
    assert snapshot.admission_mode == "enforce" and snapshot.admission_banned == ["evil", "floofycrew"]
    assert len(snapshot.sources) == 3 and snapshot.errors == []
    assert snapshot.to_dict()["updatePins"]["hasCommands"] is True


def test_snapshot_tolerates_absence_and_garbage(tmp_path: Path) -> None:
    home = tmp_path / "empty"
    home.mkdir()
    snapshot = GovernanceSnapshot.read(default_locations(home))
    assert snapshot.theme_install_enabled is None and snapshot.app_execution_allowed() is None and not snapshot.update_pins.pinned
    assert warnings_for(snapshot) == [] and snapshot.sources == []
    (home / "security_policy.json").write_text("{nope", encoding="utf-8")
    snapshot = GovernanceSnapshot.read(default_locations(home))
    assert snapshot.errors and "security_policy.json" in snapshot.errors[0]


def test_lower_tier_only_narrows(tmp_path: Path) -> None:
    ceiling = tmp_path / "ceiling.json"
    ceiling.write_text(json.dumps({"capabilities": {"theme_install": {"enabled": False}}}), encoding="utf-8")
    home = _host_home(tmp_path, policy={"capabilities": {"theme_install": {"enabled": True}}})
    locations = default_locations(home).with_bundled_ceiling(ceiling)
    assert locations.policy_files[0] == ceiling
    assert GovernanceSnapshot.read(locations).theme_install_enabled is False
    assert default_locations(home).with_bundled_ceiling(None) == default_locations(home)


def test_env_policy_tier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_policy = tmp_path / "env.json"
    env_policy.write_text(json.dumps({"updates": {"min_version": "0.7.0"}}), encoding="utf-8")
    monkeypatch.setenv("KIROCREW_SECURITY_POLICY", str(env_policy))
    locations = default_locations(tmp_path / "home")
    assert locations.policy_files[0] == env_policy
    assert GovernanceSnapshot.read(locations).update_pins.min_version == "0.7.0"


def test_gateway_status_is_read_when_reachable(tmp_path: Path) -> None:
    fake_payload(tmp_path / "p", "0.7.0")
    dist = tmp_path / "p" / "kiro_crew" / "static" / "dist"
    with FakeGateway(dist, "0.7.0", status={"governance": "healthy"}) as gw:
        snapshot = GovernanceSnapshot.read(default_locations(tmp_path / "none"), endpoint=GatewayEndpoint(port=gw.port))
        assert snapshot.gateway_governance == "healthy" and any("/api/status" in s for s in snapshot.sources)
    with FakeGateway(dist, "0.7.0") as gw:  # 401 without the credential over TCP
        snapshot = GovernanceSnapshot.read(default_locations(tmp_path / "none"), endpoint=GatewayEndpoint(port=gw.port))
        assert snapshot.gateway_governance is None and any("401" in e for e in snapshot.errors)


# --- warnings ----------------------------------------------------------------------------


def _snapshot_for(code: str) -> GovernanceSnapshot:
    snapshot = GovernanceSnapshot()
    if code == "ThemeInstallClosed":
        snapshot.capabilities["theme_install"] = False
    elif code == "CapabilityDisabled":
        snapshot.capabilities["browse"] = False
    elif code == "ThirdPartyAppsDisabled":
        snapshot.apps_allow_third_party = False
    elif code == "AppAdmissionEnforced":
        snapshot.admission_mode = "enforce"
    elif code == "AppAdmissionBanned":
        snapshot.admission_banned = ["floofycrew"]
    elif code in ("UpdatePinWillWipePatches",):
        snapshot.update_pins = type(snapshot.update_pins)(apply_command="update")
    elif code == "UpdateSourcePinned":
        snapshot.update_pins = type(snapshot.update_pins)(source="github.com/*")
    return snapshot


def _warnings(code: str, snapshot: GovernanceSnapshot):
    if code == "NoReapplyTrigger":
        return survival_check(False, snapshot)
    if code == "UpdatePinWillWipePatches":
        return survival_check(True, snapshot)
    return warnings_for(snapshot, theme_targets=["rimuru-branding"], app_targets=["some-app"], capabilities_used=["browse"])


@pytest.mark.parametrize("code", WARNING_CODES)
def test_every_warning_code_is_produced(code: str) -> None:
    found = _warnings(code, _snapshot_for(code))
    assert [w.code for w in found] == [code], found
    assert found[0].format().startswith(f"governance {code}")
    assert found[0].to_dict()["code"] == code


def test_warning_targets_name_the_mods() -> None:
    snapshot = _snapshot_for("ThemeInstallClosed")
    [warning] = warnings_for(snapshot, theme_targets=["a", "b"])
    assert warning.affected_targets == ("a", "b") and "Requirement 2.8" in warning.message
    snapshot = GovernanceSnapshot(apps_allow_third_party=False, apps_trusted=["floofycrew"])
    assert snapshot.app_execution_allowed() is True and warnings_for(snapshot) == []
    assert survival_check(True, None) == [] and survival_check(True, GovernanceSnapshot()) == []


# --- the Patcher asks, never refuses (5.9, 11.2) ------------------------------------------


def _payload(tmp_path: Path):
    package_dir = fake_payload(tmp_path / "p", "0.7.0")
    return make_payload(kind="fake", root=tmp_path / "p", package_dir=package_dir, source="t", current=True)


def test_survival_question_declined_writes_nothing(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    before = payload.index_html.read_bytes()
    asked: list[str] = []

    def ask(prompt: str, default: bool) -> bool:
        asked.append(prompt)
        return False

    patcher = Patcher(tmp_path / "home", [payload], endpoints=[], confirmer=CallableConfirmer(ask), triggers_installed=False)
    report = patcher.apply([PlannedPatch("m", "0", PatchDescriptor.from_dict(BOOT))], verify=False)
    assert report.declined and report.payloads == [] and payload.index_html.read_bytes() == before
    assert [w["code"] for w in report.governance_warnings] == ["NoReapplyTrigger"]
    assert len(asked) == 1 and "survive" in asked[0]
    assert "UserDeclined" in report.notes[0]
    audit = [json.loads(line) for line in (tmp_path / "home" / "audit.jsonl").read_text().splitlines()]
    assert audit[-1]["op"] == "apply-confirm" and audit[-1]["result"] == "declined"


def test_survival_question_confirmed_proceeds(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    snapshot = _snapshot_for("UpdatePinWillWipePatches")
    patcher = Patcher(tmp_path / "home", [payload], endpoints=[], confirmer=AlwaysConfirm(), triggers_installed=False, governance=snapshot)
    report = patcher.apply([PlannedPatch("m", "0", PatchDescriptor.from_dict(BOOT))], verify=False)
    assert report.confirmation == "confirmed" and report.ok
    assert sorted(w["code"] for w in report.governance_warnings) == ["NoReapplyTrigger", "UpdatePinWillWipePatches"]
    assert b'name="floofy-gov"' in payload.index_html.read_bytes()


def test_unknown_trigger_state_asks_nothing(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    patcher = Patcher(tmp_path / "home", [payload], endpoints=[], confirmer=NeverConfirm())  # would decline if asked
    report = patcher.apply([PlannedPatch("m", "0", PatchDescriptor.from_dict(BOOT))], verify=False)
    assert report.confirmation is None and report.ok and report.payloads


@pytest.mark.parametrize("code", WARNING_CODES)
def test_governance_never_refuses(tmp_path: Path, code: str) -> None:
    """DR-5: with every governance condition present, a consenting user's apply still writes."""
    payload = _payload(tmp_path)
    snapshot = _snapshot_for(code)
    patcher = Patcher(tmp_path / "home", [payload], endpoints=[], governance=snapshot, confirmer=AlwaysConfirm(), triggers_installed=code != "NoReapplyTrigger")
    report = patcher.apply([PlannedPatch("m", "0", PatchDescriptor.from_dict(BOOT))], verify=False)
    assert report.ok and not report.declined and report.payloads[0].written == [str(payload.index_html)]
    assert all(s["code"] != code for p in report.payloads for s in p.skipped)  # a warning code is never a skip reason
    assert not any("Governance" in e or "governance" in e for p in report.payloads for e in p.errors)


# --- themes: the ported validator and the direct write (2.1, 2.8) -----------------------------


def _pack(root: Path, *, level: int = 0, slug: str | None = "sample", extra: dict[str, bytes] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest = {"formatVersion": 1, "level": level, "name": "Sample Theme", "emoji": "🎨"}
    if slug:
        manifest["slug"] = slug
    (root / "theme.json").write_text(json.dumps(manifest), encoding="utf-8")
    palette = {"--bg": "#101010", "--text": "#eeeeee", "--accent": "#00aaff"}
    (root / "variables.json").write_text(json.dumps({"dark": palette, "light": palette}), encoding="utf-8")
    for rel, data in (extra or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    return root


def test_validate_pack_levels_and_caps(tmp_path: Path) -> None:
    summary = validate_theme_dir(_pack(tmp_path / "l0"))
    assert summary.slug == "sample" and summary.level == 0 and summary.dark["--bg"] == "#101010" and summary.files == ["theme.json", "variables.json"]
    with pytest.raises(ThemeValidationError, match="requires level 1"):  # the directory gate fires first, as in the host
        validate_theme_dir(_pack(tmp_path / "l0b", extra={"branding/logo.png": b"\x89PNG"}))
    with pytest.raises(ThemeValidationError, match="Level 1 asset"):
        validate_theme_dir(_pack(tmp_path / "l0c", extra={"styles/overrides.css": b"body{}"}))
    summary = validate_theme_dir(_pack(tmp_path / "l1", level=1, extra={"branding/logo.png": b"\x89PNG", "readme.md": b"# hi", "LICENSE": b"MIT"}))
    assert summary.level == 1 and "LICENSE" not in summary.files and "branding/logo.png" in summary.files
    with pytest.raises(ThemeValidationError, match="unexpected file"):
        validate_theme_dir(_pack(tmp_path / "bad", extra={"evil.js": b"x"}))
    with pytest.raises(ThemeValidationError, match="unexpected directory"):
        validate_theme_dir(_pack(tmp_path / "baddir", extra={"scripts/x.txt": b"x"}))
    with pytest.raises(ThemeValidationError, match="too large"):
        validate_theme_dir(_pack(tmp_path / "big", level=1, extra={"branding/favicon.png": b"0" * (50 * 1024 + 1)}))
    with pytest.raises(ThemeValidationError, match="symlinks"):
        root = _pack(tmp_path / "link")
        (root / "readme.md").symlink_to(root / "theme.json")
        validate_theme_dir(root)


def test_validate_manifest_rules(tmp_path: Path) -> None:
    root = _pack(tmp_path / "m")
    (root / "theme.json").write_text(json.dumps({"level": 0, "name": "x"}), encoding="utf-8")
    with pytest.raises(ThemeValidationError, match="formatVersion"):
        validate_theme_dir(root)
    (root / "theme.json").write_text(json.dumps({"formatVersion": 9, "name": "x"}), encoding="utf-8")
    with pytest.raises(ThemeValidationError, match="newer version"):
        validate_theme_dir(root)
    (root / "theme.json").write_text(json.dumps({"formatVersion": 1, "level": 3, "name": "x"}), encoding="utf-8")
    with pytest.raises(ThemeValidationError, match="'level' must be"):
        validate_theme_dir(root)
    (root / "theme.json").write_text(json.dumps({"formatVersion": 1, "name": "x", "fonts": [{"family": "F", "role": "monospace"}]}), encoding="utf-8")
    with pytest.raises(ThemeValidationError, match="valid roles"):
        validate_theme_dir(root)
    (root / "theme.json").write_text(json.dumps({"formatVersion": 1, "name": "No Slug Here"}), encoding="utf-8")
    assert validate_theme_dir(root).slug == "no-slug-here"
    (root / "variables.json").write_text(json.dumps({"dark": {"--bg": "#000"}}), encoding="utf-8")
    with pytest.raises(ThemeValidationError, match="missing required variable"):
        validate_theme_dir(root)


def test_data_and_css_rules() -> None:
    assert sanitize_css_value(" #fff ") == "#fff" and sanitize_css_value("url(x)") is None and sanitize_css_value("a;b") is None
    assert validate_theme_data({"name": "n", "dark": {"--bg": "#000", "--text": "#fff", "--accent": "red"}, "light": {"--bg": "#fff", "--text": "#000", "--accent": "expression(1)"}}) == "'light' variable '--accent' has an invalid value"
    assert validate_theme_data({"name": "n", "dark": {"--bg": "#000", "--text": "#fff", "--accent": "red", "--nope": "1"}, "light": {}}) == "'dark' key '--nope' is not a recognized theme variable"
    assert validate_overrides_css("body{color:red}") is None
    assert validate_overrides_css("@import url(x)") is not None
    assert validate_overrides_css("ur/**/l(//evil)") is not None  # comment evasion normalised away
    assert validate_overrides_css("\\75 rl(//evil)") is not None  # escape evasion
    assert validate_overrides_css("iframe{display:none}") == "overrides.css targets a forbidden selector: iframe"
    assert slugify_theme_name("Hello World!!") == "hello-world" and slugify_theme_name("###") == "custom"


def test_direct_write_is_byte_identical_and_staged(tmp_path: Path) -> None:
    source = _pack(tmp_path / "src", level=1, extra={"branding/logo.png": b"\x89PNG\r\n", "readme.md": b"# r", ".gitignore": b"x", "LICENSE": b"MIT"})
    themes_home = tmp_path / "themes"
    installed, summary = direct_write_theme(source, themes_home)
    assert installed == themes_home / "sample" and summary.level == 1
    copied = {p.relative_to(installed).as_posix(): p.read_bytes() for p in installed.rglob("*") if p.is_file()}
    expected = {rel: (source / rel).read_bytes() for rel in pack_files(source)}
    assert copied == expected and ".gitignore" not in copied and "LICENSE" not in copied
    assert sorted(p.name for p in themes_home.iterdir()) == ["sample"]  # no staging residue
    # re-install replaces (the update path); an editor-created record is a hard collision
    (source / "readme.md").write_bytes(b"# changed")
    direct_write_theme(source, themes_home)
    assert (installed / "readme.md").read_bytes() == b"# changed"
    (themes_home / "other.json").write_text("{}", encoding="utf-8")
    (source / "theme.json").write_text(json.dumps({"formatVersion": 1, "level": 1, "name": "Other", "slug": "other"}), encoding="utf-8")
    with pytest.raises(ThemeValidationError, match="already exists"):
        direct_write_theme(source, themes_home)
    assert sorted(p.name for p in themes_home.iterdir()) == ["other.json", "sample"]
    with pytest.raises(ThemeValidationError, match="must not contain"):
        direct_write_theme(themes_home.parent, themes_home)


def test_direct_write_refuses_invalid_pack_without_residue(tmp_path: Path) -> None:
    source = _pack(tmp_path / "src", extra={"evil.js": b"x"})
    themes_home = tmp_path / "themes"
    with pytest.raises(ThemeValidationError):
        direct_write_theme(source, themes_home)
    assert list(themes_home.iterdir()) == []


@pytest.mark.skipif(not (LIVE_RIMURU / "theme.json").is_file(), reason="no installed rimuru pack on this machine")
def test_direct_write_matches_installed_live_pack(tmp_path: Path) -> None:
    """The host's own install of this pack is on disk (read-only); the direct write reproduces it byte for byte."""
    source = tmp_path / "rimuru-src"
    shutil.copytree(LIVE_RIMURU, source)
    installed, summary = direct_write_theme(source, tmp_path / "themes")
    assert summary.slug == "rimuru" and summary.level == 1
    ours = {p.relative_to(installed).as_posix(): p.read_bytes() for p in installed.rglob("*") if p.is_file()}
    theirs = {p.relative_to(LIVE_RIMURU).as_posix(): p.read_bytes() for p in LIVE_RIMURU.rglob("*") if p.is_file() and p.name.lower() not in ("license", "license.md", "license.txt", ".gitignore", ".ds_store")}
    assert ours == theirs


# --- properties ------------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(st.text(min_size=1, max_size=80))
def test_slugify_is_safe_and_idempotent(name: str) -> None:
    slug = slugify_theme_name(name)
    assert 1 <= len(slug) <= 40 and slugify_theme_name(slug) == slug
    assert slug == "custom" or all(c.isalnum() or c == "-" for c in slug) and not slug.startswith("-") and not slug.endswith("-")


@given(st.text(max_size=60))
def test_css_sanitizer_never_admits_escapes(value: str) -> None:
    clean = sanitize_css_value(value)
    if clean is not None:
        assert not any(c in clean for c in ";{}<>\"'\\@:") and "url(" not in clean.lower()
