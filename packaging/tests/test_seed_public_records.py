"""Tests for packaging/internal/seed_public_records.py — the public registry's release seed.

INTERNAL-ONLY tooling: in the public export ``packaging/internal`` does not exist,
so the whole file skips there. Everything runs on scratch clones under ``tmp_path``;
the only real tree touched is read-only (the floofy-core example mods).
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SEEDER = REPO_ROOT / "packaging" / "internal" / "seed_public_records.py"
EXAMPLE_MOD = REPO_ROOT / "floofy-core" / "examples" / "python-hook"
pytestmark = pytest.mark.skipif(not SEEDER.exists(), reason="internal-only tooling; absent from the public export")

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def _load():
    spec = importlib.util.spec_from_file_location("seed_public_records", SEEDER)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


def _git(cwd: Path, *args: str) -> str:
    import os

    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env={**os.environ, **GIT_ENV}).stdout


def _write_mod(root: Path, mod_id: str, version: str, *, name: str | None = None) -> Path:
    """A copy of the python-hook example renamed to ``mod_id``@``version``."""
    import shutil

    shutil.copytree(EXAMPLE_MOD, root)
    manifest = json.loads((root / "floofy.json").read_text(encoding="utf-8"))
    manifest.update({"id": mod_id, "version": version, "name": name or mod_id, "description": f"{mod_id} test mod"})
    (root / "floofy.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return root


@pytest.fixture()
def seeder():
    return _load()


@pytest.fixture()
def registry(tmp_path: Path) -> Path:
    root = tmp_path / "registry"
    root.mkdir()
    (root / "registry.json").write_text(json.dumps({
        "schema": 1, "source": "github:example/registry", "edition": "external", "recordKind": "archive",
        "archiveUrlTemplate": "https://github.com/example/FloofyCrew/releases/download/{id}-{version}/{file}",
        "floofycrew": "https://github.com/example/FloofyCrew",
    }) + "\n", encoding="utf-8")
    return root


@pytest.fixture()
def export(tmp_path: Path) -> Path:
    """A fake public FloofyCrew export with one mod under mods/."""
    root = tmp_path / "FloofyCrew"
    _write_mod(root / "mods" / "alpha-mod", "alpha-mod", "1.0.0")
    return root


@pytest.fixture()
def own_repo_clone(tmp_path: Path) -> Path:
    """A git clone of a mod that lives in its own repository, with an untracked file that must not ship."""
    root = _write_mod(tmp_path / "kirocrew-ide", "kirocrew-ide", "0.23.3", name="KiroCrew IDE")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "remote", "add", "origin", "git@github.com:example/kirocrew-ide.git")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "mod")
    (root / "scratch.txt").write_text("never shipped\n", encoding="utf-8")
    return root


def test_seeds_export_mods_and_own_repo_mods(seeder, export: Path, registry: Path, own_repo_clone: Path):
    rows = seeder.seed(export, registry, {"kirocrew-ide": own_repo_clone})
    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {"alpha-mod", "kirocrew-ide"}
    for row in rows:
        assert row["record"] == "archive" and Path(row["archive"]).is_file()
        assert (registry / "mods" / row["id"] / row["version"] / "release.json").is_file()
    ide = by_id["kirocrew-ide"]
    assert ide["tag"] == "kirocrew-ide-0.23.3"
    assert ide["url"] == "https://github.com/example/FloofyCrew/releases/download/kirocrew-ide-0.23.3/kirocrew-ide-0.23.3.zip"
    # the own-repo mod's curated metadata points at its repository; the export mod's at FloofyCrew
    ide_meta = json.loads((registry / "mods" / "kirocrew-ide" / "mod.json").read_text(encoding="utf-8"))
    assert ide_meta["repo"] == "https://github.com/example/kirocrew-ide"
    assert ide_meta["links"] == {"source": "https://github.com/example/kirocrew-ide", "docs": "https://github.com/example/kirocrew-ide#readme"}
    alpha_meta = json.loads((registry / "mods" / "alpha-mod" / "mod.json").read_text(encoding="utf-8"))
    assert alpha_meta["repo"] == "https://github.com/example/FloofyCrew"
    assert alpha_meta["contact"] == ide_meta["contact"] == seeder.CONTACT


def test_own_repo_archive_holds_the_committed_tree_only(seeder, export: Path, registry: Path, own_repo_clone: Path):
    import zipfile

    rows = seeder.seed(export, registry, {"kirocrew-ide": own_repo_clone})
    archive = next(r["archive"] for r in rows if r["id"] == "kirocrew-ide")
    names = zipfile.ZipFile(archive).namelist()
    assert all(n.startswith("kirocrew-ide/") for n in names)
    assert "kirocrew-ide/floofy.json" in names
    assert not any("/.git/" in n or n.endswith("scratch.txt") for n in names), names


def test_existing_records_and_curated_mod_json_are_kept(seeder, export: Path, registry: Path, own_repo_clone: Path):
    first = seeder.seed(export, registry, {"kirocrew-ide": own_repo_clone})
    assert all("skipped" not in r for r in first)
    # a curator edits the metadata; a later version must not overwrite it
    mod_json = registry / "mods" / "kirocrew-ide" / "mod.json"
    curated = json.loads(mod_json.read_text(encoding="utf-8"))
    curated["tags"] = ["editor", "hand-picked"]
    mod_json.write_text(json.dumps(curated, indent=2) + "\n", encoding="utf-8")
    second = seeder.seed(export, registry, {"kirocrew-ide": own_repo_clone})
    assert all(r.get("skipped") == "record exists" for r in second)
    manifest = json.loads((own_repo_clone / "floofy.json").read_text(encoding="utf-8"))
    manifest["version"] = "0.24.0"
    (own_repo_clone / "floofy.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _git(own_repo_clone, "commit", "-qam", "0.24.0")
    third = seeder.seed(export, registry, {"kirocrew-ide": own_repo_clone})
    assert {r["id"]: r.get("version") for r in third if "skipped" not in r} == {"kirocrew-ide": "0.24.0"}
    assert json.loads(mod_json.read_text(encoding="utf-8"))["tags"] == ["editor", "hand-picked"]


def test_refuses_a_clone_whose_id_differs(seeder, export: Path, registry: Path, own_repo_clone: Path):
    with pytest.raises(seeder.SeedError, match="expected 'other-id'"):
        seeder.seed(export, registry, {"other-id": own_repo_clone})
    assert not (registry / "mods").exists() or not (registry / "mods" / "other-id").exists()


def test_cli_defaults_extras_next_to_the_export(seeder, export: Path, registry: Path, own_repo_clone: Path, capsys):
    assert own_repo_clone.parent == export.parent  # the layout the default relies on
    assert seeder.main(["--export", str(export), "--registry", str(registry)]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["id"] for r in rows] == ["alpha-mod", "kirocrew-ide"]


def test_cli_no_extras(seeder, export: Path, registry: Path, capsys):
    assert seeder.main(["--export", str(export), "--registry", str(registry), "--no-extras"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["id"] for r in rows] == ["alpha-mod"]
