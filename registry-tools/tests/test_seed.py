"""``registry_tools.seed.seed_release`` (the release's archive records as a library, task 6.5) and ``python -m registry_tools stamp``.

Everything runs on scratch trees under ``tmp_path``: a fake public FloofyCrew export, a standalone
mod's git clone, a registry directory (git-initialised for ``stamp --commit``). The only real tree
read is the floofy-core example mod, read-only.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from floofy_core.signing import generate_keypair
from floofy_core.sigverify import verify_detached

from registry_tools.bootstrap import deterministic_zip
from registry_tools.cli import StampError, main, stamp_index
from registry_tools.repo import RegistryRepo
from registry_tools.seed import CONTACT, SeedError, committed_tree, mod_entry, release_tag, seed_release

from registry_testing import EXAMPLES_DIR, ScratchRegistry

pytestmark = pytest.mark.registry

GIT_ENV = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
FLOOFYCREW = "https://github.com/example/FloofyCrew"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=GIT_ENV, timeout=120).stdout.strip()


def _write_mod(root: Path, mod_id: str, version: str) -> Path:
    """The python-hook example renamed to ``mod_id``@``version``."""
    shutil.copytree(EXAMPLES_DIR / "python-hook", root)
    manifest = json.loads((root / "floofy.json").read_text(encoding="utf-8"))
    manifest.update({"id": mod_id, "version": version, "name": mod_id, "description": f"{mod_id} test mod"})
    (root / "floofy.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return root


@pytest.fixture()
def registry(tmp_path: Path) -> Path:
    root = tmp_path / "registry"
    root.mkdir()
    (root / "registry.json").write_text(json.dumps({"schema": 1, "source": "github:example/registry", "edition": "external", "recordKind": "archive", "archiveUrlTemplate": f"{FLOOFYCREW}/releases/download/{{id}}-{{version}}/{{file}}", "floofycrew": FLOOFYCREW}) + "\n", encoding="utf-8")
    return root


@pytest.fixture()
def export(tmp_path: Path) -> Path:
    """A fake public tree: two mods under mods/, plus a directory without a manifest that is not a mod."""
    root = tmp_path / "FloofyCrew"
    _write_mod(root / "mods" / "alpha-mod", "alpha-mod", "1.0.0")
    _write_mod(root / "mods" / "beta-mod", "beta-mod", "2.0.0")
    (root / "mods" / "README.md").write_text("# mods\n", encoding="utf-8")
    (root / "mods" / "not-a-mod").mkdir()
    return root


@pytest.fixture()
def standalone(tmp_path: Path) -> Path:
    """A standalone mod's clone with an untracked file that must never ship."""
    root = _write_mod(tmp_path / "kirocrew-ide", "kirocrew-ide", "0.23.3")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "mod")
    (root / "scratch.txt").write_text("never shipped\n", encoding="utf-8")
    return root


def test_seed_release_writes_export_and_standalone_records_from_committed_trees(export: Path, registry: Path, standalone: Path, tmp_path: Path) -> None:
    """One archive record per export mod and per standalone entry (in that order), ``_seed_mod``'s shape, the archive the deterministic
    zip of the committed tree; the standalone entry is accepted as a mapping or as an attribute object (the Forge's ``Mod``)."""
    entry = {"id": "kirocrew-ide", "repo": "https://github.com/example/kirocrew-ide", "branch": "main"}
    rows = seed_release(export, [entry], registry=registry, clones={"kirocrew-ide": standalone})
    assert [(r["id"], r["version"], r["tag"], r["record"]) for r in rows] == [("alpha-mod", "1.0.0", "alpha-mod-1.0.0", "archive"), ("beta-mod", "2.0.0", "beta-mod-2.0.0", "archive"), ("kirocrew-ide", "0.23.3", "kirocrew-ide-0.23.3", "archive")]
    repo = RegistryRepo.load(registry)
    for row in rows:
        record = json.loads((registry / "mods" / row["id"] / row["version"] / "release.json").read_text(encoding="utf-8"))
        assert record["tag"] == row["tag"] == release_tag(repo, row["id"], row["version"]) and record["channel"] == "stable"
        assert record["files"] == [{"url": f"{FLOOFYCREW}/releases/download/{row['tag']}/{row['tag']}.zip", "sha256": row["sha256"], "size": row["size"], "name": f"{row['tag']}.zip"}]
        assert row["url"] == record["files"][0]["url"] and Path(row["archive"]) == registry / ".dist" / f"{row['tag']}.zip" and Path(row["archive"]).is_file()
        assert row["description"] == f"{row['id']} test mod"
        assert json.loads((registry / "mods" / row["id"] / row["version"] / "floofy.json").read_text(encoding="utf-8"))["version"] == row["version"]
    # the standalone archive: the committed tree under <id>/ — no .git, nothing untracked — byte-identical to the deterministic zip of a clean checkout
    ide = rows[2]
    with zipfile.ZipFile(ide["archive"]) as archive:
        names = archive.namelist()
    assert "kirocrew-ide/floofy.json" in names and all(n.startswith("kirocrew-ide/") for n in names) and not any("/.git/" in n or n.endswith("scratch.txt") for n in names), names
    clean = tmp_path / "clean"
    subprocess.run(["git", "clone", "--quiet", str(standalone), str(clean)], check=True, capture_output=True, env=GIT_ENV, timeout=120)
    shutil.rmtree(clean / ".git")  # the committed files only, as a release asset would hold them
    assert deterministic_zip(clean, tmp_path / "again.zip", top="kirocrew-ide") == (ide["sha256"], ide["size"])
    # the curated mod.json: the standalone mod links to its own repository, the export mods to FloofyCrew; both name the contact
    ide_meta = json.loads((registry / "mods" / "kirocrew-ide" / "mod.json").read_text(encoding="utf-8"))
    assert ide_meta["repo"] == entry["repo"] and ide_meta["links"] == {"source": entry["repo"], "docs": f"{entry['repo']}#readme"} and ide_meta["contact"] == CONTACT
    alpha_meta = json.loads((registry / "mods" / "alpha-mod" / "mod.json").read_text(encoding="utf-8"))
    assert alpha_meta["repo"] == FLOOFYCREW and alpha_meta["contact"] == CONTACT
    assert not (registry / "mods" / "not-a-mod").exists(), "a directory without floofy.json is not a mod"
    # the Forge's shape: an object with id and repo attributes, and an already loaded RegistryRepo
    again = seed_release(export, [SimpleNamespace(id="kirocrew-ide", repo=entry["repo"], branch="main")], registry=repo, clones={"kirocrew-ide": standalone})
    assert all(r.get("skipped") == "record exists" for r in again) and [r["tag"] for r in again] == [r["tag"] for r in rows]
    assert mod_entry(SimpleNamespace(id="x", repo="https://e/x")) == mod_entry({"id": "x", "repo": "https://e/x"})
    with pytest.raises(SeedError, match="not a standalone mod entry"):
        mod_entry({"id": "x"})


def test_seed_release_skips_versions_without_a_release_and_keeps_curated_metadata(export: Path, registry: Path) -> None:
    """With a ``released`` predicate a version whose ``<id>-<version>`` release does not exist gets no record (the predicate sees the id
    and the version); an existing record is kept, and a curator's ``mod.json`` survives a later version's seed."""
    asked: list[tuple[str, str]] = []

    def released(mod_id: str, version: str) -> bool:
        asked.append((mod_id, version))
        return mod_id == "alpha-mod"

    rows = seed_release(export, registry=registry, released=released)
    assert asked == [("alpha-mod", "1.0.0"), ("beta-mod", "2.0.0")]
    assert [(r["id"], r.get("skipped")) for r in rows] == [("alpha-mod", None), ("beta-mod", "no release beta-mod-2.0.0")]
    assert rows[1] == {"id": "beta-mod", "version": "2.0.0", "tag": "beta-mod-2.0.0", "skipped": "no release beta-mod-2.0.0"}
    assert (registry / "mods" / "alpha-mod" / "1.0.0" / "release.json").is_file() and not (registry / "mods" / "beta-mod").exists(), "nothing is written for the unreleased version"
    assert not (registry / ".dist" / "beta-mod-2.0.0.zip").exists()
    # the curator edits alpha's metadata; a later alpha version is recorded without touching it, and the old record is kept as is
    mod_json = registry / "mods" / "alpha-mod" / "mod.json"
    curated = {**json.loads(mod_json.read_text(encoding="utf-8")), "tags": ["hand-picked"]}
    mod_json.write_text(json.dumps(curated, indent=2) + "\n", encoding="utf-8")
    manifest_path = export / "mods" / "alpha-mod" / "floofy.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_path.write_text(json.dumps({**manifest, "version": "1.1.0"}, indent=2) + "\n", encoding="utf-8")
    later = seed_release(export, registry=registry)
    assert [(r["id"], r["version"], r.get("skipped")) for r in later] == [("alpha-mod", "1.1.0", None), ("beta-mod", "2.0.0", None)], "no predicate: every version without a record is seeded"
    assert json.loads(mod_json.read_text(encoding="utf-8"))["tags"] == ["hand-picked"]
    assert (registry / "mods" / "alpha-mod" / "1.0.0" / "release.json").is_file() and (registry / "mods" / "alpha-mod" / "1.1.0" / "release.json").is_file()
    assert all(r.get("skipped") == "record exists" for r in seed_release(export, registry=registry, released=lambda i, v: False)), "an existing record is kept before the predicate is asked"


def test_seed_release_refuses_strays_missing_clones_and_a_tree_that_is_not_a_registry(export: Path, registry: Path, standalone: Path, tmp_path: Path) -> None:
    entry = {"id": "kirocrew-ide", "repo": "https://github.com/example/kirocrew-ide"}
    with pytest.raises(SeedError, match="not in the standalone list: stray"):
        seed_release(export, [entry], registry=registry, clones={"kirocrew-ide": standalone, "stray": standalone})
    with pytest.raises(SeedError, match="kirocrew-ide: no clone given"):
        seed_release(export, [entry], registry=registry)
    with pytest.raises(SeedError, match="expected 'other-id'"):
        seed_release(export, [{"id": "other-id", "repo": "https://e/o"}], registry=registry, clones={"other-id": standalone})
    assert not (registry / "mods" / "other-id").exists()
    with pytest.raises(SeedError, match="no registry.json"):
        seed_release(export, registry=tmp_path / "nowhere")
    with pytest.raises(SeedError, match="no mods/ directory"):
        seed_release(tmp_path / "empty-export", registry=registry)
    # committed_tree extracts a sub-path of a commit, and names a ref git does not have
    tree = committed_tree(standalone, tmp_path / "tree", paths=("hook",))
    assert sorted(p.name for p in tree.rglob("*") if p.is_file()) == ["__init__.py"] and (tree / "hook" / "__init__.py").is_file()
    with pytest.raises(SeedError, match="git archive nope failed"):
        committed_tree(standalone, tmp_path / "tree2", ref="nope")


# --- stamp -----------------------------------------------------------------------------------------------------------


@pytest.fixture()
def stamped_registry(tmp_path: Path) -> tuple[ScratchRegistry, Path]:
    """A registry clone (a git repository with one commit) holding one recorded mod, its key, a README with the table markers."""
    pair = generate_keypair("stamp test key")
    key = pair.save_private(tmp_path / "keys" / "registry.json")
    scratch = ScratchRegistry(tmp_path / "registry", key_id=pair.key_id)
    scratch.add_version(_write_mod(tmp_path / "mods" / "alpha-mod", "alpha-mod", "1.0.0"))
    (scratch.root / "README.md").write_text("# registry (fixture)\n\n<!-- mods:begin -->\n<!-- mods:end -->\n", encoding="utf-8")
    (scratch.root / ".gitignore").write_text(".dist/\n", encoding="utf-8")
    _git(scratch.root, "init", "-q", "-b", "main")
    _git(scratch.root, "add", "-A")
    _git(scratch.root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed")
    return scratch, key


def test_stamp_cli_rebuilds_signs_commits_and_never_pushes(stamped_registry: tuple[ScratchRegistry, Path], capsys: pytest.CaptureFixture[str]) -> None:
    """``stamp REPO --floofycrew V --sign KEY --commit``: ``index.json`` carries ``floofycrew: V`` and verifies with the key, ``app-registry.json``
    and the README table are written, one ``chore(index): Stamp floofycrew V`` commit holds exactly those files; a second run has nothing
    to commit; a key the registry does not name is refused; the default version is the clone's ``floofy_core.__version__``."""
    from floofy_core import __version__
    from floofy_core.signing import load_private_key

    scratch, key = stamped_registry
    root = scratch.root
    before = _git(root, "rev-parse", "HEAD")
    assert main(["stamp", str(root), "--floofycrew", "9.9.9", "--sign", str(key), "--commit"]) == 0
    out = capsys.readouterr().out
    assert "floofycrew 9.9.9" in out and "committed " in out and "never pushes" in out
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    assert index["floofycrew"] == "9.9.9" and [m["id"] for m in index["mods"]] == ["alpha-mod"]
    pair = load_private_key(key)
    assert verify_detached((root / "index.json").read_bytes(), (root / "index.json.sig").read_bytes(), {pair.key_id: pair.public_key}).verified
    assert (root / "app-registry.json").is_file() and "alpha-mod" in (root / "README.md").read_text(encoding="utf-8")
    head = _git(root, "rev-parse", "HEAD")
    assert head != before and _git(root, "log", "-1", "--format=%s") == "chore(index): Stamp floofycrew 9.9.9"
    assert set(_git(root, "show", "--format=", "--name-only", "HEAD").split()) == {"index.json", "index.json.sig", "app-registry.json", "README.md"}
    assert _git(root, "status", "--porcelain") == "", "the commit holds everything the stamp wrote; nothing is left behind"
    assert _git(root, "remote") == "", "no remote was added, nothing could have been pushed"
    # idempotent: the same stamp again changes nothing and commits nothing (generatedAt aside, which the writer does refresh)
    assert main(["stamp", str(root), "--floofycrew", "9.9.9", "--sign", str(key), "--commit"]) == 0
    text = capsys.readouterr().out
    if "nothing to commit" in text:
        assert _git(root, "rev-parse", "HEAD") == head
    else:
        assert _git(root, "log", "-1", "--format=%s") == "chore(index): Stamp floofycrew 9.9.9" and json.loads((root / "index.json").read_text(encoding="utf-8"))["floofycrew"] == "9.9.9"
    # the default version is the clone's floofy_core.__version__ (the public clone this tool runs from); no --commit → the tree is dirty, nothing committed
    assert main(["stamp", str(root)]) == 0
    assert json.loads((root / "index.json").read_text(encoding="utf-8"))["floofycrew"] == __version__
    assert "index.json" in _git(root, "status", "--porcelain") and _git(root, "log", "-1", "--format=%s") == "chore(index): Stamp floofycrew 9.9.9"
    assert not (root / "index.json.sig").read_text(encoding="utf-8") == "" and capsys.readouterr().out.count("signed") == 0, "no --sign: the stale signature is left for the caller to notice; nothing is signed"
    # a key the registry does not name never signs the index
    other = generate_keypair("other").save_private(root.parent / "keys" / "other.json")
    assert main(["stamp", str(root), "--floofycrew", "9.9.10", "--sign", str(other)]) == 1
    assert "expects key" in capsys.readouterr().err
    assert json.loads((root / "index.json").read_text(encoding="utf-8"))["floofycrew"] == __version__, "a refused key writes nothing"
    with pytest.raises(StampError, match="expects key"):
        stamp_index(RegistryRepo.load(root), "9.9.10", sign=other)
    assert main(["stamp", str(root.parent / "not-a-registry")]) == 2


def test_stamp_index_leaves_a_readme_without_markers_alone(stamped_registry: tuple[ScratchRegistry, Path]) -> None:
    scratch, key = stamped_registry
    (scratch.root / "README.md").write_text("# a human's README\n", encoding="utf-8")
    stamp = stamp_index(RegistryRepo.load(scratch.root), "1.2.3", sign=key, app_registry=False)
    assert stamp.version == "1.2.3" and stamp.mods == 1 and stamp.versions == 1 and stamp.key_id == json.loads(key.read_text(encoding="utf-8"))["keyId"]
    assert [p.name for p in stamp.written] == ["index.json", "index.json.sig"], "no app registry, no README rewrite"
    assert (scratch.root / "README.md").read_text(encoding="utf-8") == "# a human's README\n" and not (scratch.root / "app-registry.json").exists()
    assert any("1 mod(s), 1 version(s), floofycrew 1.2.3" in note for note in stamp.notes)
