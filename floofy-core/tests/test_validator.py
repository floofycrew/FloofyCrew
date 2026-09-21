"""Validator tests (Requirement 1.6, 1.8, 1.9, 1.10, 5.10, 11.4, 11.6; task 2.4).

Temp-dir mods derived from the examples: hash mismatch → ``MissingFiles``; unsafe
archives rejected before extraction; engineering-rule targets are errors while
governance targets are accepted-able warnings; the network scan flags plaintext
non-loopback URLs and undeclared hosts only.
"""
from __future__ import annotations

import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from floofy_core import cli_validate
from floofy_core.archive import ArchiveError, inspect_archive, open_archive_safely
from floofy_core.netscan import host_matches, is_loopback_host, split_authority
from floofy_core.targets import DEFAULT_TARGET_POLICY, TargetClass, TargetPolicy
from floofy_core.validator import Finding, validate_mod

from floofy_testing import KINDS, EXAMPLES_DIR, read_manifest, rehash, write_manifest


def _codes(findings: list[Finding]) -> list[str]:
    return [f.code for f in findings]


def _set_patch_target(root: Path, target: str) -> None:
    descriptor_path = root / "patches" / "index-boot.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    descriptor["target"] = target
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
    rehash(root)


def _write_spa(root: Path, code: str, hosts: list[str] | None = None, credentials: bool | None = None) -> None:
    (root / "spa" / "main.js").write_text(code, encoding="utf-8")
    manifest = read_manifest(root)
    if hosts is not None:
        manifest.setdefault("network", {})["hosts"] = hosts
    if credentials is not None:
        manifest.setdefault("network", {})["credentials"] = credentials
    write_manifest(root, manifest)
    rehash(root)


# --- examples and the happy path -----------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_examples_validate_clean(kind: str) -> None:
    report = validate_mod(EXAMPLES_DIR / kind)
    assert report.ok and report.warnings == [], report.format()
    assert report.mod_id == f"example-{kind}"


def test_validate_mod_argument_contract(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        validate_mod()
    with pytest.raises(TypeError):
        validate_mod(tmp_path, archive=tmp_path / "x.zip")
    report = validate_mod(tmp_path / "missing")
    assert _codes(report.errors) == ["InvalidManifest"]


# --- files[] and hashes (Requirement 1.6) --------------------------------------------


def test_hash_mismatch_is_missing_files(example_mod) -> None:
    root = example_mod("theme")
    (root / "theme" / "variables.json").write_text("{}", encoding="utf-8")
    report = validate_mod(root)
    assert [(f.code, f.path) for f in report.errors] == [("MissingFiles", "/files/1")]
    assert "sha256 mismatch" in report.errors[0].message
    rehash(root)
    assert validate_mod(root).ok


def test_missing_listed_file_is_missing_files(example_mod) -> None:
    root = example_mod("theme")
    (root / "theme" / "variables.json").unlink()
    assert [(f.code, f.path) for f in validate_mod(root).errors] == [("MissingFiles", "/files/1")]


def test_unlisted_and_unverified_files(example_mod) -> None:
    root = example_mod("python-hook")
    (root / "hook" / "helper.py").write_text("x = 1\n", encoding="utf-8")
    report = validate_mod(root)
    assert _codes(report.errors) == ["UnverifiedPart"]  # a file inside a code part must be hashed
    assert [(f.code, f.path, f.accept) for f in report.warnings] == [("UnlistedFile", "hook/helper.py", True)]
    root2 = example_mod("theme")
    (root2 / "README.txt").write_text("hello\n", encoding="utf-8")
    report2 = validate_mod(root2)
    assert report2.ok and _codes(report2.warnings) == ["UnlistedFile"]


def test_manifest_problems(example_mod) -> None:
    root = example_mod("theme")
    manifest = read_manifest(root)
    del manifest["dependsOn"]["floofycrew"]
    manifest["kirocrew"]["version"] = ">=1.0.0 foo"
    manifest["conflicts"] = {"example-theme": "*"}
    manifest["files"].append({"path": "floofy.json", "sha256": "0" * 64})
    manifest["files"].append(dict(manifest["files"][0]))
    write_manifest(root, manifest)
    report = validate_mod(root)
    assert sorted(_codes(report.errors)) == sorted(
        ["MissingFrameworkDependency", "InvalidRange", "SelfReference", "InvalidManifest", "DuplicateFile"]
    )
    (root / "floofy.json").write_text("{not json", encoding="utf-8")
    assert _codes(validate_mod(root).errors) == ["InvalidManifest"]


def test_part_path_must_exist_and_be_json_when_json(example_mod) -> None:
    root = example_mod("theme")
    (root / "theme" / "theme.json").write_text("nope", encoding="utf-8")
    rehash(root)
    assert _codes(validate_mod(root).errors) == ["InvalidPart"]
    manifest = read_manifest(root)
    manifest["parts"][0]["path"] = "other/theme.json"
    write_manifest(root, manifest)
    assert "MissingFiles" in _codes(validate_mod(root).errors)


# --- archives (Requirement 1.8) ---------------------------------------------------------


def _tar_with(path: Path, members: list[tuple[str, str]]) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        for name, kind in members:
            info = tarfile.TarInfo(name)
            if kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/hostname"
                archive.addfile(info)
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            else:
                data = b"{}"
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return path


@pytest.mark.parametrize(
    "members, reason",
    [
        ([("mod/floofy.json", "file"), ("mod/../escape.json", "file")], "'..'"),
        ([("/abs/floofy.json", "file")], "absolute"),
        ([("mod/floofy.json", "file"), ("mod/link", "symlink")], "symlink"),
        ([("mod/floofy.json", "file"), ("mod/a\\b.json", "file")], "backslash"),
    ],
)
def test_unsafe_tar_rejected_before_extraction(tmp_path: Path, members, reason) -> None:
    archive = _tar_with(tmp_path / "mod.tgz", members)
    destination = tmp_path / "out"
    with pytest.raises(ArchiveError) as excinfo:
        inspect_archive(archive)
    assert reason in str(excinfo.value)
    with pytest.raises(ArchiveError):
        open_archive_safely(archive, destination)
    assert not destination.exists() or not any(destination.iterdir()), "nothing may be extracted from an unsafe archive"
    report = validate_mod(archive=archive)
    assert _codes(report.errors) == ["UnsafeArchive"] and reason in report.errors[0].message


def test_unsafe_zip_rejected(tmp_path: Path) -> None:
    absolute = tmp_path / "abs.zip"
    with zipfile.ZipFile(absolute, "w") as archive:
        archive.writestr("/abs/floofy.json", "{}")
    assert _codes(validate_mod(absolute).errors) == ["UnsafeArchive"]
    linked = tmp_path / "link.zip"
    with zipfile.ZipFile(linked, "w") as archive:
        info = zipfile.ZipInfo("mod/link")
        info.external_attr = 0o120777 << 16
        archive.writestr(info, "/etc/hostname")
    assert _codes(validate_mod(linked).errors) == ["UnsafeArchive"]
    with pytest.raises(ArchiveError):
        inspect_archive(tmp_path / "missing.rar")


def test_safe_archives_validate_like_the_directory(tmp_path: Path) -> None:
    root = EXAMPLES_DIR / "theme"
    tgz = tmp_path / "theme.tgz"
    with tarfile.open(tgz, "w:gz") as archive:
        archive.add(root, arcname="theme")
    zipped = tmp_path / "theme.zip"
    with zipfile.ZipFile(zipped, "w") as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(root.parent).as_posix())
    for source in (tgz, zipped):
        report = validate_mod(source)
        assert report.ok and report.warnings == [] and report.mod_id == "example-theme", report.format()
    extracted = open_archive_safely(zipped, tmp_path / "extracted")
    assert extracted == tmp_path / "extracted" / "theme" and (extracted / "floofy.json").is_file()


# --- targets (Requirement 5.10, 11.4) ---------------------------------------------------


def test_engineering_rule_target_is_an_error(example_mod) -> None:
    root = example_mod("patch")
    _set_patch_target(root, "kiro_crew/dashboard/server.py")
    report = validate_mod(root)
    assert [(f.code, f.path) for f in report.errors] == [("EngineeringRuleTarget", "patches/index-boot.json#/target")]
    _set_patch_target(root, "venv/bin/kirocrew")
    assert _codes(validate_mod(root).errors) == ["EngineeringRuleTarget"]


def test_governance_target_is_an_accepted_warning(example_mod) -> None:
    root = example_mod("patch")
    _set_patch_target(root, "crew/security_policy.json")
    report = validate_mod(root)
    assert report.ok, report.format()
    assert [(f.code, f.accept, f.severity) for f in report.warnings] == [("GovernanceAltering", True, "warning")]
    manifest = read_manifest(root)
    (root / "trust").mkdir()
    (root / "trust" / "keys.json").write_text("{}", encoding="utf-8")
    manifest["files"].append({"path": "trust/keys.json", "sha256": "0" * 64})
    write_manifest(root, manifest)
    rehash(root)
    report = validate_mod(root)
    assert report.ok and _codes(report.warnings) == ["GovernanceAltering", "GovernanceAltering"]
    assert report.warnings[1].path == "/files/1/path"


def test_adapter_extended_policy(example_mod) -> None:
    root = example_mod("patch")
    _set_patch_target(root, "edition_pkg/compose.py")
    assert validate_mod(root).ok
    policy = DEFAULT_TARGET_POLICY.extended(engineering_rule=["edition_pkg/**/*.py"], governance=["bundle-manifest.json"])
    assert _codes(validate_mod(root, targets=policy).errors) == ["EngineeringRuleTarget"]
    assert policy.classify("x/bundle-manifest.json") is TargetClass.GOVERNANCE
    assert policy.extended(engineering_rule=["edition_pkg/**/*.py"]) == policy  # duplicates dropped
    assert TargetPolicy().classify("kiro_crew/static/dist/index.html") is TargetClass.ALLOWED
    assert TargetPolicy().classify("kiro_crew/static/dist/assets/main-abc.js") is TargetClass.ALLOWED
    assert TargetPolicy().classify("trust") is TargetClass.GOVERNANCE
    assert TargetPolicy().matching_pattern("kiro_crew/a.py") == "kiro_crew/**/*.py"


def test_patch_descriptor_semantics(example_mod) -> None:
    root = example_mod("patch")
    descriptor_path = root / "patches" / "index-boot.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    descriptor["appliesTo"] = "not a range!"
    descriptor["fromBuild"] = "0.7"
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
    rehash(root)
    schema_stage = validate_mod(root).errors
    assert set(_codes(schema_stage)) == {"InvalidPatch"} and len(schema_stage) == 3  # pattern, minLength, pattern
    assert {f.path for f in schema_stage} == {"patches/index-boot.json#/appliesTo", "patches/index-boot.json#/fromBuild"}
    descriptor["appliesTo"] = ">=0.7.0 <<0.9.0"  # passes the character-set pattern, fails the parser
    descriptor["fromBuild"] = "0.7.0.x"
    descriptor["ops"].append({"op": "replace", "fingerprint": "([", "regex": True, "content": "x"})
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
    rehash(root)
    semantic_stage = validate_mod(root).errors
    assert sorted((f.code, f.path) for f in semantic_stage) == [
        ("InvalidPatch", "patches/index-boot.json#/fromBuild"),
        ("InvalidPatch", "patches/index-boot.json#/ops/1/fingerprint"),
        ("InvalidRange", "patches/index-boot.json#/appliesTo"),
    ]


# --- network scan (Requirement 1.10, 11.6) ----------------------------------------------


def test_plaintext_non_loopback_is_flagged(example_mod) -> None:
    root = example_mod("spa")
    _write_spa(root, 'fetch("http://example.com/api");\nnew WebSocket("ws://example.org/s");\n')
    report = validate_mod(root)
    assert report.ok
    assert [(f.code, f.path, f.accept) for f in report.warnings] == [
        ("PlaintextNetwork", "spa/main.js:1", True),
        ("PlaintextNetwork", "spa/main.js:2", True),
    ]


@pytest.mark.parametrize("url", ["http://127.0.0.1:8080/x", "http://localhost/y", "http://[::1]:9/z", "ws://127.1.2.3/s", "http://LOCALHOST:3000"])
def test_loopback_plaintext_is_allowed(example_mod, url: str) -> None:
    root = example_mod("spa")
    _write_spa(root, f'fetch("{url}");\n')
    assert validate_mod(root).warnings == []


def test_undeclared_and_declared_hosts(example_mod) -> None:
    root = example_mod("spa")
    code = 'fetch("https://api.example.com/v1");\nnew WebSocket("wss://api.example.com:8443/s");\nfetch("https://cdn.other.org/a");\n'
    _write_spa(root, code, hosts=[])
    assert [(f.code, f.path) for f in validate_mod(root).warnings] == [
        ("UndeclaredHost", "spa/main.js:1"),
        ("UndeclaredHost", "spa/main.js:2"),  # wss is a distinct scheme, reported once per file/scheme/host
        ("UndeclaredHost", "spa/main.js:3"),
    ]
    _write_spa(root, code, hosts=["*.example.com"])
    assert [(f.code, f.path) for f in validate_mod(root).warnings] == [("UndeclaredHost", "spa/main.js:3")]
    _write_spa(root, code, hosts=["*.example.com", "cdn.other.org"])
    assert validate_mod(root).warnings == []
    _write_spa(root, 'fetch("https://example.com/");\n', hosts=["*.example.com"])
    assert _codes(validate_mod(root).warnings) == ["UndeclaredHost"]  # the wildcard does not cover the apex
    _write_spa(root, 'fetch("https://api.example.com:9443/");\n', hosts=["api.example.com:8443"])
    assert _codes(validate_mod(root).warnings) == ["UndeclaredHost"]  # port mismatch


def test_identifier_urls_and_docs_are_ignored(example_mod) -> None:
    root = example_mod("spa")
    _write_spa(root, 'const svg = "http://www.w3.org/2000/svg";\n')
    (root / "NOTES.md").write_text("see http://example.com/docs\n", encoding="utf-8")
    manifest = read_manifest(root)
    manifest["files"].append({"path": "NOTES.md", "sha256": "0" * 64})
    write_manifest(root, manifest)
    rehash(root)
    assert validate_mod(root).warnings == []


def test_credentials_hint(example_mod) -> None:
    root = example_mod("spa")
    _write_spa(root, 'fetch("https://api.example.com", {headers: {Authorization: "Bearer x"}});\n', hosts=["api.example.com"])
    report = validate_mod(root)
    assert [(f.code, f.severity, f.accept, f.path) for f in report.warnings] == [("CredentialsHint", "info", False, "spa/main.js:1")]
    _write_spa(root, 'fetch("https://api.example.com", {headers: {Authorization: "Bearer x"}});\n', hosts=["api.example.com"], credentials=True)
    assert validate_mod(root).warnings == []


# --- CLI ----------------------------------------------------------------------------------


def test_cli_exit_codes_and_json(example_mod, capsys) -> None:
    root = example_mod("theme")
    assert cli_validate.main([str(root)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("floofy validate:") and out.rstrip().endswith("OK: 0 error(s), 0 warning(s)")
    (root / "theme" / "variables.json").write_text("{}", encoding="utf-8")
    assert cli_validate.main([str(root), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and payload["errors"][0]["code"] == "MissingFiles" and payload["id"] == "example-theme"
    assert cli_validate.main([str(root), "--quiet"]) == 1
    assert capsys.readouterr().out.strip() == "FAIL: 1 error(s), 0 warning(s)"
    with pytest.raises(SystemExit) as excinfo:
        cli_validate.main([str(root / "nowhere")])
    assert excinfo.value.code == 2


def test_cli_warnings_alone_pass_and_adapter_flags(example_mod, capsys) -> None:
    root = example_mod("patch")
    _set_patch_target(root, "crew/admission_policy.json")
    assert cli_validate.main([str(root)]) == 0
    assert "GovernanceAltering [accept]" in capsys.readouterr().out
    _set_patch_target(root, "edition_pkg/x.py")
    assert cli_validate.main([str(root), "--engineering-rule-target", "edition_pkg/**/*.py"]) == 1


# --- properties -------------------------------------------------------------------------------

_label = st.from_regex(r"[a-z][a-z0-9]{0,6}", fullmatch=True)
_labels = st.lists(_label, min_size=1, max_size=3)


@settings(max_examples=150, deadline=None)
@given(_labels, _labels)
def test_wildcard_host_semantics(prefix: list[str], base: list[str]) -> None:
    """**Validates: Requirements 1.10** — ``*.<base>`` matches one or more extra labels in front
    of ``<base>`` and nothing else; an exact pattern matches only itself."""
    base_host = ".".join(base)
    sub_host = ".".join(prefix + base)
    assert host_matches(sub_host, f"*.{base_host}")
    assert not host_matches(base_host, f"*.{base_host}")
    assert host_matches(base_host, base_host) and host_matches(base_host.upper(), base_host)
    assert host_matches(sub_host, base_host) == (not prefix)
    assert not host_matches(sub_host + "x", f"*.{base_host}")


@settings(max_examples=100, deadline=None)
@given(st.integers(0, 255), st.integers(0, 255), st.integers(0, 255), st.integers(0, 65535))
def test_loopback_detection(a: int, b: int, c: int, port: int) -> None:
    """**Validates: Requirements 11.6** — every 127.x.y.z address and only those IPv4 addresses are loopback."""
    assert is_loopback_host(f"127.{a}.{b}.{c}")
    assert is_loopback_host(f"{a}.{b}.{c}.1") == (a == 127)
    host, parsed_port = split_authority(f"user@127.{a}.{b}.{c}:{port}")
    assert host == f"127.{a}.{b}.{c}" and parsed_port == port
    assert split_authority("[::1]:8080") == ("::1", 8080) and is_loopback_host("::1")


_segment = st.from_regex(r"[a-z][a-z0-9_]{0,8}", fullmatch=True)


@settings(max_examples=150, deadline=None)
@given(st.lists(_segment, max_size=3), _segment, st.sampled_from([".py", ".pyc", ".html", ".js", ".css", ".json"]))
def test_target_classification(prefix: list[str], name: str, suffix: str) -> None:
    """**Validates: Requirements 5.10** — any Python file under kiro_crew/ at any depth is an
    engineering-rule target; static assets under it are allowed."""
    path = "/".join(prefix + ["kiro_crew", name + suffix])
    expected = TargetClass.ENGINEERING_RULE if suffix in (".py", ".pyc") else TargetClass.ALLOWED
    assert DEFAULT_TARGET_POLICY.classify(path) is expected
    assert DEFAULT_TARGET_POLICY.classify("/".join(prefix + [name + suffix])) is TargetClass.ALLOWED


@settings(max_examples=25, deadline=None)
@given(st.binary(min_size=1, max_size=200), st.integers(0, 199))
def test_hash_check_detects_any_byte_flip(tmp_path_factory, payload: bytes, position: int) -> None:
    """**Validates: Requirements 1.6** — a correctly hashed file passes; flipping one byte gives MissingFiles."""
    import shutil

    root = tmp_path_factory.mktemp("mod") / "theme"
    shutil.copytree(EXAMPLES_DIR / "theme", root)
    target = root / "theme" / "variables.json"
    target.write_bytes(payload)
    rehash(root)
    report = validate_mod(root)
    assert "MissingFiles" not in _codes(report.errors)
    index = position % len(payload)
    flipped = payload[:index] + bytes([payload[index] ^ 0xFF]) + payload[index + 1 :]
    target.write_bytes(flipped)
    assert "MissingFiles" in _codes(validate_mod(root).errors)



# --- electron targets (Requirement 5.11) --------------------------------------------------


def _set_patch_side(root: Path, side: str) -> None:
    manifest = read_manifest(root)
    for part in manifest["parts"]:
        if part["kind"] == "patch":
            part["side"] = side
    write_manifest(root, manifest)
    rehash(root)


def test_electron_target_validates_with_side_electron(example_mod) -> None:
    root = example_mod("patch")
    _set_patch_target(root, "electron:mochi/petOverlays.js")
    _set_patch_side(root, "electron")
    report = validate_mod(root)
    assert report.ok and report.warnings == [], report.format()


def test_electron_target_side_mismatch_is_an_accepted_warning(example_mod) -> None:
    root = example_mod("patch")
    _set_patch_target(root, "electron:mochi/petOverlays.js")  # the example part declares side spa
    report = validate_mod(root)
    assert report.ok
    assert [(f.code, f.accept) for f in report.warnings] == [("SideMismatch", True)]
    _set_patch_target(root, "kiro_crew/static/dist/index.html")
    _set_patch_side(root, "electron")
    report = validate_mod(root)
    assert report.ok and _codes(report.warnings) == ["SideMismatch"]


def test_electron_target_needs_a_member_and_stays_in_place(example_mod) -> None:
    root = example_mod("patch")
    _set_patch_side(root, "electron")
    _set_patch_target(root, "electron:")
    assert [(f.code, f.path) for f in validate_mod(root).errors] == [("InvalidPatch", "patches/index-boot.json#/target")]
    descriptor_path = root / "patches" / "index-boot.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    descriptor.update({"target": "electron:main.js", "mode": "import-map"})
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
    rehash(root)
    assert [(f.code, f.path) for f in validate_mod(root).errors] == [("InvalidPatch", "patches/index-boot.json#/mode")]
    # host Python inside the archive is not host Python of the payload: the engineering rule is about the gateway
    _set_patch_target(root, "electron:node_modules/x/y.js")
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    descriptor.pop("mode", None)
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
    rehash(root)
    assert validate_mod(root).ok
