"""``init_registry_repo`` — the registry repository template instantiated for both editions (task 7.5; Requirement 8.2, 10.5).

The edition configs are found by their ``edition`` field under ``editions/*/floofy_edition_*/registry.json``
(this test names no edition); the produced repositories must pass the tooling's own gates and be
consumable by the client.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from floofy_core.registry import IndexCache
from floofy_core.compat import CompatCache
from floofy_core.schema import load_schema
from floofy_core.schema.check import validate_instance
from floofy_core.signing import generate_keypair
from floofy_core.sigverify import verify_detached

from registry_tools.bootstrap import BootstrapError, deterministic_zip, init_registry_repo, main
from registry_tools.build import build_index, index_matches
from registry_tools.repo import RegistryRepo
from registry_tools.submission import validate_submission

from registry_testing import REPO_ROOT

#: The first-party theme's version, from its manifest (the seeded record is ``<id>@<version>``).
RIMURU_VERSION = json.loads((REPO_ROOT / "mods" / "rimuru-branding" / "floofy.json").read_text(encoding="utf-8"))["version"]

pytestmark = pytest.mark.registry

EDITION_CONFIGS = {json.loads(p.read_text(encoding="utf-8"))["edition"]: p for p in REPO_ROOT.glob("editions/*/floofy_edition_*/registry.json")}
FIXTURE_DIST = REPO_ROOT / "floofy-core" / "tests" / "fixtures" / "dist-0.7.0.5"


def _fake_payload(root: Path) -> Path:
    """A payload-shaped directory: the trimmed real 0.7.0.5 dist fixture plus an empty kiro_crew package (anchors miss on purpose)."""
    package = root / "lib" / "python3.12" / "site-packages" / "kiro_crew"
    (package / "static").mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "0.7.0"\n', encoding="utf-8")
    import shutil

    shutil.copytree(FIXTURE_DIST, package / "static" / "dist")
    return root


@pytest.mark.parametrize("edition", sorted(EDITION_CONFIGS))
def test_init_registry_repo_for_each_edition(edition: str, tmp_path: Path):
    config_path = EDITION_CONFIGS[edition]
    config = json.loads(config_path.read_text(encoding="utf-8"))
    pinned_key_id = config["sources"][0]["keyId"]
    target = tmp_path / f"registry-{edition}"
    payload = _fake_payload(tmp_path / "payload") if FIXTURE_DIST.is_dir() else None
    # archive records on both editions here (the link shape has its own test in test_link_records.py)
    summary = init_registry_repo(target, edition_config=config_path, floofycrew_root=REPO_ROOT, seed_mods=["rimuru-branding"], host_version="0.7.0.5", channel="beta", payload=payload, verdict="expected", run="https://forge.example/run/1", record_kind="archive")
    assert summary["edition"] == edition and summary["keyId"] == pinned_key_id and summary["source"] == config["sourceLabel"] and summary["recordKind"] == "archive"
    repo = RegistryRepo.load(target)
    assert repo.source == config["sourceLabel"] and repo.key_id == pinned_key_id and repo.config["hostRegistry"]["repo"] == config["sources"][0]["repo"]
    # the template rendered without leftovers, per edition
    for name in ("README.md", "CONTRIBUTING.md", "registry.json", "masquerade-allow.txt", ".gitignore"):
        text = (target / name).read_text(encoding="utf-8")
        assert "{{" not in text, name
    assert config["sourceLabel"] in (target / "README.md").read_text(encoding="utf-8")
    github = "github.com" in config["sources"][0]["repo"]
    assert (target / ".github" / "workflows" / "validate-submission.yml").is_file() is github
    # the seeded record and its deterministic archive
    seed = summary["seeded"][0]
    assert seed["id"] == "rimuru-branding" and seed["url"].startswith("https://") and "{" not in seed["url"]
    archive = Path(seed["archive"])
    assert archive.is_file() and hashlib.sha256(archive.read_bytes()).hexdigest() == seed["sha256"] and archive.stat().st_size == seed["size"]
    inside_repo = archive.is_relative_to(target / "archives")
    assert inside_repo == ("/archives/" in config["archiveUrlTemplate"]), "archives are committed only where the edition's URL template points into the repository"
    if not inside_repo:
        assert archive.is_relative_to(target / ".dist") and ".dist/" in (target / ".gitignore").read_text(encoding="utf-8")
    release = json.loads((target / "mods" / "rimuru-branding" / seed["version"] / "release.json").read_text(encoding="utf-8"))
    assert release["files"][0] == {"url": seed["url"], "sha256": seed["sha256"], "size": seed["size"], "name": f"rimuru-branding-{seed['version']}.zip"} and release["tag"] == seed["tag"]
    mod_record = json.loads((target / "mods" / "rimuru-branding" / "mod.json").read_text(encoding="utf-8"))
    assert mod_record["repo"] == config["floofycrewRepo"]
    # the gates the tooling itself applies
    assert validate_submission(archive, registry=target, tag=seed["tag"]).ok
    assert index_matches(repo.index_path, build_index(repo))[0]
    index = json.loads(repo.index_path.read_text(encoding="utf-8"))
    assert validate_instance(index, load_schema("index")) == [] and index["mods"][0]["versions"][0]["compat"] == {"0.7.0.5": "expected"}
    compat = json.loads(repo.compat_path.read_text(encoding="utf-8"))
    assert validate_instance(compat, load_schema("compat")) == []
    row = compat["rows"][0]
    assert (row["edition"], row["channel"], row["hostVersion"]) == (edition, "beta", "0.7.0.5") and row["mods"][f"rimuru-branding@{RIMURU_VERSION}"] == {"verdict": "expected", "run": "https://forge.example/run/1"}
    if payload is not None:
        spa = row["framework"]["spaFingerprints"]
        assert spa["total"] > 0 and spa["matched"] == 0 and len(spa["missed"]) == spa["total"], "the trimmed fixture stubs the App chunk, so the offline reporter measured every fingerprint as missed"
        assert row["framework"]["pythonAnchors"]["matched"] == 0 and row["framework"]["loader"] == "degraded", "an empty kiro_crew package misses every anchor"
    assert json.loads(repo.app_registry_path.read_text(encoding="utf-8")) == [], "rimuru-branding is not an app-kind mod"
    # the client consumes the result
    cache = IndexCache.load_path(repo.index_path)
    best = cache.best_version("rimuru-branding", base_version="0.7.0", edition=edition, host_version="0.7.0.5", channel="beta", compat=CompatCache.load(repo.compat_path))
    assert best.version.version == RIMURU_VERSION and best.verdict == "expected"
    # a non-empty target is refused; a fresh clone holding only .git/ is fine
    with pytest.raises(BootstrapError, match="not empty"):
        init_registry_repo(target, edition_config=config_path, floofycrew_root=REPO_ROOT, seed_mods=[], host_version=None, channel=None)
    clone = tmp_path / "clone"
    (clone / ".git").mkdir(parents=True)
    init_registry_repo(clone, edition_config=config_path, floofycrew_root=REPO_ROOT, seed_mods=[], host_version=None, channel=None)
    assert (clone / "registry.json").is_file() and (clone / ".git").is_dir()


def test_signing_and_the_shell_wrapper(tmp_path: Path):
    edition, config_path = sorted(EDITION_CONFIGS.items())[0]
    config = json.loads(config_path.read_text(encoding="utf-8"))
    # signing with a key other than the adapter's pin is refused
    stranger = generate_keypair()
    stranger.save_private(tmp_path / "stranger.json")
    with pytest.raises(BootstrapError, match="pins key"):
        init_registry_repo(tmp_path / "r1", edition_config=config_path, floofycrew_root=REPO_ROOT, seed_mods=[], host_version=None, channel=None, sign_key=tmp_path / "stranger.json")
    # a config whose pinned key we hold: sign index.json and compat.json
    pair = generate_keypair()
    pair.save_private(tmp_path / "key.json")
    custom = {**config, "sources": [{**config["sources"][0], "keyId": pair.key_id}], "keys": [pair.public_record()]}
    custom_path = tmp_path / "registry.json"
    custom_path.write_text(json.dumps(custom), encoding="utf-8")
    summary = init_registry_repo(tmp_path / "r2", edition_config=custom_path, floofycrew_root=REPO_ROOT, seed_mods=["rimuru-branding"], host_version="0.7.0.5", channel=None, sign_key=tmp_path / "key.json")
    assert summary["signedWith"] == pair.key_id
    for name in ("index.json", "compat.json"):
        assert verify_detached((tmp_path / "r2" / name).read_bytes(), (tmp_path / "r2" / f"{name}.sig").read_bytes(), {pair.key_id: pair.public_key}).verified
    assert json.loads((tmp_path / "r2" / "compat.json").read_text(encoding="utf-8"))["rows"][0]["channel"] == ("beta" if edition == "internal" else "stable")
    # deterministic archives: the same bytes twice
    a = deterministic_zip(REPO_ROOT / "mods" / "rimuru-branding", tmp_path / "a.zip", top="rimuru-branding")
    b = deterministic_zip(REPO_ROOT / "mods" / "rimuru-branding", tmp_path / "b.zip", top="rimuru-branding")
    assert a == b and (tmp_path / "a.zip").read_bytes() == (tmp_path / "b.zip").read_bytes()
    # the shell wrapper picks the adapter config by edition and passes options through
    script = REPO_ROOT / "registry-tools" / "scripts" / "init_registry_repo.sh"
    flag = "public" if edition == "external" else "internal"
    completed = subprocess.run(["bash", str(script), str(tmp_path / "r3"), "--edition", flag, "--no-seed", "--json"], env={**os.environ, "PYTHON": sys.executable}, capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    produced = json.loads(completed.stdout)
    assert produced["edition"] == edition and produced["seeded"] == [] and (tmp_path / "r3" / "index.json").is_file()
    assert subprocess.run(["bash", str(script), str(tmp_path / "r4")], capture_output=True, text=True).returncode == 2, "--edition is required"
    assert main([str(tmp_path / "r5"), "--edition-config", str(custom_path), "--floofycrew-root", str(REPO_ROOT), "--no-seed"]) == 0



def test_byte_code_caches_next_to_templates_and_adapter_files_are_never_rendered(tmp_path: Path):
    """A ``__pycache__/*.pyc`` beside ``setup.py`` (left by any tool that imported it in place) is
    neither UTF-8 nor part of the package; before, the internal edition's gated-package render
    read it as text and failed with ``UnicodeDecodeError``."""
    from registry_tools.bootstrap import source_files

    config_path = EDITION_CONFIGS["internal"]
    config = json.loads(config_path.read_text(encoding="utf-8"))
    extras = (config_path.parent / config["registryPackage"]["files"]).resolve()
    cache = extras / "__pycache__"
    planted = not cache.is_dir()
    cache.mkdir(exist_ok=True)
    pyc = cache / "setup.cpython-312.pyc"
    pyc.write_bytes(b"\xcb\x0d\x0d\x0a" + b"\x00" * 12)
    try:
        assert pyc not in source_files(extras) and all("__pycache__" not in p.parts for p in source_files(extras))
        target = tmp_path / "registry-internal"
        init_registry_repo(target, edition_config=config_path, floofycrew_root=REPO_ROOT, seed_mods=[], host_version="0.7.0.5", channel="beta")
        assert not list(target.rglob("*.pyc")) and not list(target.rglob("__pycache__"))
    finally:
        pyc.unlink(missing_ok=True)
        if planted:
            cache.rmdir()
