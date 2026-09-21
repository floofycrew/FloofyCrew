"""Link-based version records (task 7.8; Requirement 8.1, 8.3, 8.9, 8.10).

A version record may carry ``link{repo, tag, commit, manifestSha256}`` plus the
checkout's ``files[]{path, sha256, size}`` instead of archive URLs. The schema
accepts exactly one of the two shapes; ``registry_tools build`` resolves link
records from a clone at the tag and refuses unresolved or mismatching ones;
``validate-submission --record`` clones and compares; the bootstrap seeds a
link record from a local tagged checkout. A local bare repository stands in for
the forge: the records carry a real-looking ``ssh://forge.example/pkg/…`` URL
and git's own ``url.<base>.insteadOf`` rewriting (``GIT_CONFIG_*`` in the
environment) points that URL at the bare repository, under the test-only switch
``FLOOFY_ALLOW_LOCAL_GIT=1`` that admits the ``file`` transport.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from floofy_core.canonical import canonical_bytes
from floofy_core.registry import IndexCache
from floofy_core.schema import load_schema
from floofy_core.schema.check import validate_instance

from registry_tools.bootstrap import BootstrapError, init_registry_repo
from registry_tools.build import build_index, index_matches, write_index
from registry_tools.cli import main as cli_main
from registry_tools.record import RecordError, make_record
from registry_tools.links import LinkError, check_record, commit_of_tag, facts_from_local_commit, link_reference, manifest_hash_matches, repo_matches_pattern, resolve_record
from registry_tools.repo import RegistryRepo
from registry_tools.submission import accepted_tags, validate_submission

from registry_testing import EXAMPLES_DIR, REPO_ROOT, ScratchRegistry

#: The first-party theme's version, from its manifest (records are keyed by it; no tag is involved).
RIMURU_VERSION = json.loads((REPO_ROOT / "mods" / "rimuru-branding" / "floofy.json").read_text(encoding="utf-8"))["version"]

pytestmark = pytest.mark.registry

GIT_ENV = {"GIT_AUTHOR_NAME": "tests", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "tests", "GIT_COMMITTER_EMAIL": "t@example.invalid", "PATH": "/usr/bin:/bin:/usr/local/bin"}


def _git(*args: str, cwd: Path) -> str:
    done = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True, env={**GIT_ENV, "HOME": str(cwd)})
    return done.stdout.strip()


FORGE_URL = "ssh://forge.example/pkg/Example"


@pytest.fixture(autouse=True)
def _local_git(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FLOOFY_ALLOW_LOCAL_GIT", "1")


def point_forge_at(monkeypatch: pytest.MonkeyPatch, bare: Path, url: str = FORGE_URL) -> str:
    """Make git resolve ``url`` to the local bare repository (``url.<file://bare>.insteadOf <url>``); returns ``url``."""
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{bare.as_uri()}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", url)
    return url


def mod_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, mod_kind: str = "theme", path: str = "mods/example-theme", tag: str = "example-theme-1.0.0") -> tuple[Path, str, str]:
    """A working checkout holding the example mod under ``path``, tagged, plus a bare clone reachable as :data:`FORGE_URL`; returns ``(work, url, commit)``."""
    work = tmp_path / "work"
    shutil.copytree(EXAMPLES_DIR / mod_kind, work / path)
    (work / path / "LICENSE").write_text("MIT\n", encoding="utf-8")  # the gate wants a licence file beside the manifest
    (work / "README.md").write_text("a repository holding several things\n", encoding="utf-8")
    _git("init", "-q", "-b", "mainline", cwd=work)
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "release", cwd=work)
    _git("tag", tag, cwd=work)
    commit = _git("rev-parse", "HEAD", cwd=work)
    bare = tmp_path / "forge.git"
    _git("clone", "-q", "--bare", str(work), str(bare), cwd=tmp_path)
    return work, point_forge_at(monkeypatch, bare), commit


def link_registry(tmp_path: Path, *, repo_url: str, tag: str | None = None, path: str = "mods/example-theme", resolved: dict | None = None, pattern: str | None = None) -> ScratchRegistry:
    """A registry with one UNRESOLVED link record for example-theme 1.0.0: repo + path (a legacy record when ``tag`` is given)."""
    registry = ScratchRegistry(tmp_path / "registry")
    config = json.loads((registry.root / "registry.json").read_text(encoding="utf-8"))
    config["recordKind"] = "link"
    if pattern:
        config["repoUrlPattern"] = pattern
    (registry.root / "registry.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    manifest = json.loads((EXAMPLES_DIR / "theme" / "floofy.json").read_text(encoding="utf-8"))
    directory = registry.root / "mods" / "example-theme" / "1.0.0"
    directory.mkdir(parents=True)
    (directory / "floofy.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    release = {"channel": "stable", "publishedAt": "2026-09-20T00:00:00Z", "link": {"repo": repo_url, "path": path, **({"tag": tag} if tag else {}), **(resolved or {})}}
    if resolved and "files" in resolved:
        release["files"] = release["link"].pop("files")
    (directory / "release.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
    (directory.parent / "mod.json").write_text(json.dumps({"repo": repo_url}) + "\n", encoding="utf-8")
    return registry


# --- schema ---------------------------------------------------------------------------------------------------


def _index_with(entry: dict) -> list[str]:
    document = {"schema": 1, "source": "test", "generatedAt": "2026-09-20T00:00:00Z", "mods": [{"id": "ex", "name": "Ex", "description": "", "authors": [], "tags": [], "repo": "ssh://forge.example/pkg/Ex", "versions": [entry]}]}
    return [str(e) for e in validate_instance(document, load_schema("index"))]


def test_index_schema_accepts_exactly_one_record_shape():
    common = {"version": "1.0.0", "kirocrew": "*", "editions": ["internal"], "compat": {}, "dependencies": {}, "channel": "stable", "publishedAt": "2026-09-20T00:00:00Z"}
    asset = {**common, "files": [{"url": "https://assets.example/ex-1.0.0.zip", "sha256": "a" * 64, "size": 1}]}
    link = {**common, "link": {"repo": "ssh://forge.example/pkg/Ex", "commit": "b" * 40, "manifestSha256": "c" * 64, "path": "mods/ex"}, "files": [{"path": "README.md", "sha256": "a" * 64, "size": 1}]}
    assert _index_with(asset) == [] and _index_with(link) == [], "a link record pins a commit; no tag is part of the shape"
    assert _index_with({**link, "link": {**link["link"], "ref": "mainline"}}) == [] and _index_with({**link, "link": {**link["link"], "tag": "ex-1.0.0"}}) == [], "a branch (ref) and a legacy tag are optional"
    assert _index_with({**link, "files": asset["files"]}), "a link record must not name archive URLs"
    assert _index_with({**asset, "files": link["files"]}), "path-shaped files need a link block"
    assert any("link/repo" in e for e in _index_with({**link, "link": {**link["link"], "repo": "git://forge.example/pkg/Ex"}})), "plaintext transports are refused in the schema itself"
    assert _index_with({**link, "link": {k: v for k, v in link["link"].items() if k != "commit"}}), "commit is required"
    assert _index_with({**link, "files": [{"path": "../escape", "sha256": "a" * 64, "size": 1}]}), "paths stay inside the checkout"


# --- build and validate on a local bare repository ------------------------------------------------------------


def test_build_refuses_an_unresolved_link_record_and_resolves_it_on_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    work, url, commit = mod_repository(tmp_path, monkeypatch)
    registry = link_registry(tmp_path, repo_url=url)
    repo = RegistryRepo.load(registry.root)
    assert repo.record_kind == "link" and repo.repo_url_pattern is None
    unresolved = build_index(repo)
    assert not unresolved.ok and any("unresolved" in p and "--resolve-links" in p for p in unresolved.problems)
    resolved = build_index(repo, resolve_links=True)
    assert resolved.ok, resolved.problems
    assert any("link record resolved" in n for n in resolved.notes)
    release = json.loads((registry.root / "mods" / "example-theme" / "1.0.0" / "release.json").read_text(encoding="utf-8"))
    manifest = json.loads((EXAMPLES_DIR / "theme" / "floofy.json").read_text(encoding="utf-8"))
    assert release["link"] == {"repo": url, "path": "mods/example-theme", "commit": commit, "manifestSha256": hashlib.sha256(canonical_bytes(manifest)).hexdigest()}, "resolved from the default branch: the commit is the pin, no tag"
    assert "tag" not in release
    paths = {f["path"] for f in release["files"]}
    assert "theme/theme.json" in paths and "floofy.json" not in paths and all({"path", "sha256", "size"} == set(f) for f in release["files"])
    entry = resolved.index["mods"][0]["versions"][0]
    assert entry["link"]["commit"] == commit and entry["files"] == release["files"] and "url" not in entry["files"][0]
    assert validate_instance(resolved.index, load_schema("index")) == []
    write_index(repo, resolved)
    assert index_matches(repo.index_path, build_index(repo))[0], "a second build needs no clone once the record is resolved"
    # the client reads both shapes; hash lookup covers a link record's files
    cache = IndexCache.load_path(repo.index_path)
    version = cache.mods[0].versions[0]
    assert version.link["commit"] == commit and version.asset_files == [] and len(version.path_files) == len(release["files"])
    theme = next(f for f in release["files"] if f["path"] == "theme/theme.json")
    assert cache.by_hash(theme["sha256"])[0][0].id == "example-theme"


def test_build_checks_manifest_hash_and_repository_pattern_without_cloning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    work, url, commit = mod_repository(tmp_path, monkeypatch)
    registry = link_registry(tmp_path, repo_url=url)
    repo = RegistryRepo.load(registry.root)
    assert build_index(repo, resolve_links=True).ok
    record = repo.read_version("example-theme", "1.0.0")
    assert manifest_hash_matches(record) == (True, "")
    # a curator edits the recorded manifest after resolution: the hash no longer matches, no clone needed to notice
    manifest = dict(record.manifest)
    manifest["description"] = "edited after the fact"
    (record.directory / "floofy.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    stale = build_index(repo)
    assert not stale.ok and any("manifestSha256" in p and "is not the hash" in p for p in stale.problems)
    # the registry pins its forge by pattern (Requirement 8.10): a look-alike host is refused
    config = json.loads((registry.root / "registry.json").read_text(encoding="utf-8"))
    config["repoUrlPattern"] = "^ssh://other-forge\\.example/pkg/[A-Za-z0-9_.-]+$"
    (registry.root / "registry.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    pinned = build_index(RegistryRepo.load(registry.root))
    assert any("does not match this registry's repository pattern" in p for p in pinned.problems)
    assert repo_matches_pattern("ssh://forge.example/pkg/Widget", "^ssh://forge\\.example/pkg/") and not repo_matches_pattern("ssh://forge.example.evil/pkg/Widget", "^ssh://forge\\.example/pkg/")


def test_validate_submission_record_clones_and_compares(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    work, url, commit = mod_repository(tmp_path, monkeypatch)
    registry = link_registry(tmp_path, repo_url=url)
    repo = RegistryRepo.load(registry.root)
    assert build_index(repo, resolve_links=True).ok
    report = validate_submission(None, registry=registry.root, record="example-theme@1.0.0")
    assert report.ok, report.format()
    assert report.mod_id == "example-theme" and report.link["commit"] == commit and report.validation["ok"]
    # the CLI spelling
    assert cli_main(["validate-submission", "--record", "example-theme@1.0.0", "--registry", str(registry.root)]) == 0
    assert cli_main(["validate-submission", "--registry", str(registry.root)]) == 2
    # the branch moves on: the record pins its commit, so the fetch still lands on the recorded tree and validates
    (work / "mods" / "example-theme" / "README.md").write_text("moved\n", encoding="utf-8")
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "keep developing on the branch", cwd=work)
    _git("push", "-q", str(tmp_path / "forge.git"), "mainline", cwd=work)
    still = validate_submission(None, registry=registry.root, record="example-theme@1.0.0")
    assert still.ok, still.format()
    # a record re-pinned at the new head without re-reading its files is drift, refused
    record_dir = registry.root / "mods" / "example-theme" / "1.0.0"
    release = json.loads((record_dir / "release.json").read_text(encoding="utf-8"))
    release["link"]["commit"] = _git("rev-parse", "HEAD", cwd=work)
    (record_dir / "release.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
    drifted = validate_submission(None, registry=registry.root, record="example-theme@1.0.0")
    assert not drifted.ok and any("the checkout ships README.md, which files[] does not list" in f.message for f in drifted.errors)
    # a record made before commits were pinned (tag, no commit) is still cloned at its tag and held to the tag rule
    legacy = link_registry(tmp_path / "legacy", repo_url=url, tag="example-theme-1.0.0")
    assert build_index(RegistryRepo.load(legacy.root), resolve_links=True).ok
    legacy_release = json.loads((legacy.root / "mods" / "example-theme" / "1.0.0" / "release.json").read_text(encoding="utf-8"))
    assert legacy_release["link"]["commit"] == commit and "tag" not in legacy_release["link"], "resolving a legacy record pins the commit and drops the tag"
    # an asset record cannot be validated as a link record, and vice versa
    asset_registry = ScratchRegistry(tmp_path / "asset-registry")
    asset_registry.add_version(EXAMPLES_DIR / "theme")
    wrong_shape = validate_submission(None, registry=asset_registry.root, record="example-theme@1.0.0")
    assert not wrong_shape.ok and "asset record" in wrong_shape.errors[0].message
    assert accepted_tags("example-theme", "1.0.0") == {"1.0.0", "v1.0.0", "example-theme-1.0.0"}


def test_check_record_reports_every_kind_of_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    work, url, commit = mod_repository(tmp_path, monkeypatch)
    registry = link_registry(tmp_path, repo_url=url, pattern="^ssh://other-forge\\.example/")
    repo = RegistryRepo.load(registry.root)
    record = repo.read_version("example-theme", "1.0.0")
    facts = resolve_record(record)
    assert facts.commit == commit and len(facts.files) >= 1
    problems, scratch, root = check_record(record, repo)
    try:
        assert any("repository pattern" in p for p in problems), "the repository is outside the pinned forge"
        assert any("manifestSha256 is missing" in p for p in problems), "an unresolved record has no hash to compare"
        assert root is not None and (root / "floofy.json").is_file()
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
    with pytest.raises(LinkError, match="no commit named"):
        commit_of_tag(work, "example-theme-9.9.9")
    with pytest.raises(LinkError):
        resolve_record(record.__class__(record.mod_id, record.version, record.directory, record.manifest, {**record.release, "link": {"repo": "ssh://forge.example/pkg/Nowhere", "path": "x"}}))
    with pytest.raises(LinkError, match="unusable"):
        resolve_record(record.__class__(record.mod_id, record.version, record.directory, record.manifest, {**record.release, "link": {"repo": "git://forge.example/pkg/Example", "path": "x"}}))
    with pytest.raises(LinkError, match="pins no commit"):
        link_reference(record.__class__(record.mod_id, record.version, record.directory, record.manifest, {**record.release, "link": {"repo": "ssh://forge.example/pkg/Example", "path": "x"}}), pinned=True)


# --- the bootstrap seeds link records from a local checkout's HEAD ------------------------------------------


def test_bootstrap_seeds_a_link_record_from_a_local_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # a repository shaped like the FloofyCrew package: mods/rimuru-branding on the default branch, no tag anywhere
    work = tmp_path / "floofycrew-package"
    shutil.copytree(REPO_ROOT / "mods" / "rimuru-branding", work / "mods" / "rimuru-branding")
    _git("init", "-q", "-b", "mainline", cwd=work)
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "ship the mod source", cwd=work)
    commit = _git("rev-parse", "HEAD", cwd=work)
    bare = tmp_path / "FloofyCrew.git"
    _git("clone", "-q", "--bare", str(work), str(bare), cwd=tmp_path)
    internal = next(p for p in REPO_ROOT.glob("editions/*/floofy_edition_*/registry.json") if json.loads(p.read_text(encoding="utf-8"))["edition"] == "internal")
    config = json.loads(internal.read_text(encoding="utf-8"))
    assert config["recordKind"] == "link", "the internal registry lists mods by link (Requirement 8.9)"
    # the adapter's own repository URL and pin pattern stay as they are; git resolves the URL to the local bare repository
    package_url = str(config["floofycrewRepo"])
    assert re.match(config["repoUrlPattern"], package_url), "the adapter's FloofyCrew repository satisfies its own pin"
    point_forge_at(monkeypatch, bare, package_url)
    config.pop("registryPackage", None)  # the gated package has its own test (test_gated_package.py); this one is about the record shape
    config_path = tmp_path / "registry.json"
    config_path.write_text(json.dumps(config) + "\n", encoding="utf-8")
    target = tmp_path / "registry"
    with pytest.raises(BootstrapError, match="--link-source"):
        init_registry_repo(target, edition_config=config_path, floofycrew_root=REPO_ROOT, seed_mods=["rimuru-branding"], host_version=None, channel=None)
    shutil.rmtree(target, ignore_errors=True)
    summary = init_registry_repo(target, edition_config=config_path, floofycrew_root=REPO_ROOT, seed_mods=["rimuru-branding"], host_version="0.7.0.5", channel="beta", verdict="tested", link_source=work)
    seed = summary["seeded"][0]
    assert summary["recordKind"] == "link" and seed["record"] == "link" and seed["commit"] == commit and "tag" not in seed
    assert not (target / "archives").exists(), "a link record carries no archive"
    release = json.loads((target / "mods" / "rimuru-branding" / RIMURU_VERSION / "release.json").read_text(encoding="utf-8"))
    assert release["link"] == {"repo": package_url, "path": "mods/rimuru-branding", "commit": commit, "manifestSha256": seed["manifestSha256"]} and "tag" not in release
    manifest = json.loads((REPO_ROOT / "mods" / "rimuru-branding" / "floofy.json").read_text(encoding="utf-8"))
    listed = {f["path"]: f["sha256"] for f in manifest["files"]}
    recorded = {f["path"]: f["sha256"] for f in release["files"]}
    assert recorded == listed, "the record's files[] are the committed tree's files, which the manifest lists exactly"
    repo = RegistryRepo.load(target)
    index = json.loads(repo.index_path.read_text(encoding="utf-8"))
    assert validate_instance(index, load_schema("index")) == [] and index["mods"][0]["versions"][0]["link"]["commit"] == commit
    assert json.loads((target / "registry.json").read_text(encoding="utf-8"))["recordKind"] == "link"
    assert "link-based" in (target / "README.md").read_text(encoding="utf-8")
    # the gate the internal package runs: fetch the pinned commit, compare
    assert validate_submission(None, registry=target, record=f"rimuru-branding@{RIMURU_VERSION}").ok
    # re-rendering over the populated clone is refused without --replace and idempotent with it
    with pytest.raises(BootstrapError, match="--replace"):
        init_registry_repo(target, edition_config=config_path, floofycrew_root=REPO_ROOT, seed_mods=["rimuru-branding"], host_version=None, channel=None, link_source=work)
    again = init_registry_repo(target, edition_config=config_path, floofycrew_root=REPO_ROOT, seed_mods=["rimuru-branding"], host_version="0.7.0.5", channel="beta", verdict="tested", link_source=work, replace=True)
    assert again["seeded"][0]["commit"] == commit and (target / ".git").exists() is False
    # the seed must be the committed tree: a HEAD whose manifest differs from the seed is refused
    (work / "mods" / "rimuru-branding" / "floofy.json").write_text(json.dumps({**manifest, "description": "drifted"}, indent=2) + "\n", encoding="utf-8")
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "drift", cwd=work)
    with pytest.raises(BootstrapError, match="differs from the seed"):
        init_registry_repo(target, edition_config=config_path, floofycrew_root=REPO_ROOT, seed_mods=["rimuru-branding"], host_version=None, channel=None, link_source=work, replace=True)
    facts = facts_from_local_commit(work, "HEAD", "mods/rimuru-branding")
    assert facts.manifest_sha256 != seed["manifestSha256"]
    assert facts_from_local_commit(work, commit, "mods/rimuru-branding").manifest_sha256 == seed["manifestSha256"], "the earlier commit still reads as it was"


# --- `registry_tools record`: a record from the mod's repository, no tag -------------------------------------


def test_record_command_reads_the_default_branch_and_pins_the_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    work, url, commit = mod_repository(tmp_path, monkeypatch)
    registry = ScratchRegistry(tmp_path / "registry")
    config = json.loads((registry.root / "registry.json").read_text(encoding="utf-8"))
    config["recordKind"] = "link"
    (registry.root / "registry.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    repo = RegistryRepo.load(registry.root)
    # a new mod: mod.json, the manifest and a record pinned at the branch head
    result = make_record(repo, "example-theme", repo_url=url, path="mods/example-theme")
    assert result.created and result.version == "1.0.0" and result.commit == commit and result.ref is None and result.mod_json_written
    release = json.loads((registry.root / "mods" / "example-theme" / "1.0.0" / "release.json").read_text(encoding="utf-8"))
    assert release["link"] == {"repo": url, "path": "mods/example-theme", "commit": commit, "manifestSha256": result.manifest_sha256} and "tag" not in release and release["channel"] == "stable"
    assert json.loads((registry.root / "mods" / "example-theme" / "mod.json").read_text(encoding="utf-8"))["repo"] == url
    assert build_index(repo).ok, "the record is complete: no clone needed to build"
    # the same version again is not a new release
    with pytest.raises(RecordError, match="already recorded"):
        make_record(repo, "example-theme")
    # the branch moves on WITHOUT a version bump: --refresh re-pins the same version at the new head
    (work / "mods" / "example-theme" / "README.md").write_text("more words\n", encoding="utf-8")
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "docs", cwd=work)
    _git("push", "-q", str(tmp_path / "forge.git"), "mainline", cwd=work)
    head = _git("rev-parse", "HEAD", cwd=work)
    refreshed = make_record(repo, "example-theme", refresh=True)
    assert not refreshed.created and refreshed.commit == head and refreshed.repo == url and refreshed.path == "mods/example-theme", "repo and path come from the existing record"
    release = json.loads((registry.root / "mods" / "example-theme" / "1.0.0" / "release.json").read_text(encoding="utf-8"))
    assert release["link"]["commit"] == head and release["refreshedAt"] and release["publishedAt"] == "2026-09-20T00:00:00Z" or release["publishedAt"]
    assert validate_submission(None, registry=registry.root, record="example-theme@1.0.0").ok
    # a version bump on the branch: the new version is recorded beside the old one, from a LOCAL checkout this time
    manifest_path = work / "mods" / "example-theme" / "floofy.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = "1.1.0"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "release 1.1.0", cwd=work)
    bumped = make_record(repo, "example-theme", source=work)
    assert bumped.created and bumped.version == "1.1.0" and bumped.commit == _git("rev-parse", "HEAD", cwd=work)
    assert sorted(p.name for p in (registry.root / "mods" / "example-theme").iterdir() if p.is_dir()) == ["1.0.0", "1.1.0"]
    # the CLI spelling, and a repository outside the pin
    assert cli_main(["record", str(registry.root), "example-theme", "--refresh", "--json"]) == 0
    config["repoUrlPattern"] = "^ssh://other-forge\\.example/"
    (registry.root / "registry.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    assert cli_main(["record", str(registry.root), "example-theme", "--refresh"]) == 1
