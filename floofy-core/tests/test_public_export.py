"""Tests for scripts/export_public.py (Requirement 10.2): the filtered public export.

Like test_de_amazon.py, the test data never spells an internal identifier
literally — planted tokens are taken from the scanner's ``DENYLIST`` at runtime,
so this file stays clean under the very scan it exercises.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_no_internal_identifiers as checker  # noqa: E402
import export_public as exporter  # noqa: E402

pytestmark = pytest.mark.skipif(not checker.DENYLIST, reason="the internal identifier list (scripts/internal-identifiers.txt) is not part of the public tree")
HOSTNAME_TOKEN = checker.DENYLIST[0]  # a hostname fragment
ADAPTER_DIR = "editions/" + "tool" + "box"  # split so this file stays scanner-clean


@pytest.fixture(scope="module")
def export_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One real export of the current tree, shared by the module's tests."""
    target = tmp_path_factory.mktemp("public-export") / "export"
    report = exporter.export(target)
    assert report["target"] == str(target)
    return target


def test_the_public_set_is_copied_and_nothing_else(export_tree: Path) -> None:
    for rel in ("floofy-core", "loader-app", "spa-host", "registry-tools", "docs",
                "editions/public", "packaging/public", "scripts", "branding", "mods",
                "README.md", "CHANGELOG.md", "pyproject.toml", "uv.lock",
                ".github/workflows/release.yml"):
        assert (export_tree / rel).exists(), f"public-set path missing from the export: {rel}"
    for rel in (ADAPTER_DIR, "packaging/internal", "forge", "research", ".kiro",
                "tools", "Config", "packageInfo", "FloofyCrew.code-workspace",
                ".github/workflows/ci.yml"):
        assert not (export_tree / rel).exists(), f"internal path leaked into the export: {rel}"


def test_excluded_mods_are_absent_and_readme_rows_dropped(export_tree: Path) -> None:
    for mod in exporter.DROPPED_MODS:
        assert not (export_tree / "mods" / mod).exists(), f"excluded mod exported: {mod}"
    readme = (export_tree / "mods" / "README.md").read_text(encoding="utf-8")
    for mod in exporter.EXCLUDED_MODS:
        assert mod not in readme, f"mods/README.md still names the excluded mod {mod}"
    for mod in exporter.WITHHELD_MODS:  # its row goes; other rows may still refer to it
        assert f"[`{mod}/`]" not in readme, f"mods/README.md still lists the withheld mod {mod}"
    assert "custom-themes" in readme and "settings-demo" in readme


def test_withheld_mods_are_a_separate_set() -> None:
    """A withheld mod is held back for now, not internal-only by nature: the two
    tuples must not overlap, every member must exist in the source tree, and the
    union is what the export drops."""
    assert not set(exporter.EXCLUDED_MODS) & set(exporter.WITHHELD_MODS)
    for mod in exporter.DROPPED_MODS:
        assert (REPO_ROOT / "mods" / mod / "floofy.json").is_file(), f"dropped mod is not in the tree: {mod}"
    assert set(exporter.DROPPED_MODS) == set(exporter.EXCLUDED_MODS) | set(exporter.WITHHELD_MODS)


def test_every_exported_file_is_git_tracked(export_tree: Path) -> None:
    """The export ships what is committed and nothing else. The audit before the
    first 1.3.6 sync found a dev-linked mod's ``.floofy/`` runtime state (its log,
    journal and config, gitignored in the source tree) copied into the public tree
    because the exporter walked the working tree; now an untracked path never
    reaches the export, wherever it sits inside a public-set directory."""
    tracked = set(subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "-z"], capture_output=True,
                                 text=True, check=True).stdout.split("\0"))
    untracked = sorted(p.relative_to(export_tree).as_posix() for p in export_tree.rglob("*")
                       if p.is_file() and p.relative_to(export_tree).as_posix() not in tracked)
    assert untracked == [], f"untracked files reached the export: {untracked[:10]}"
    assert not list(export_tree.rglob(".floofy")), "a mod's .floofy/ runtime state reached the export"


def test_internal_only_files_are_dropped_with_their_index_rows(export_tree: Path) -> None:
    """A tracked file written for the internal audience alone (the migration guide
    for the standalone patcher that only ever existed internally) is left out, and
    the docs index row that pointed at it goes with it."""
    for rel in exporter.INTERNAL_ONLY_FILES:
        assert (REPO_ROOT / rel).is_file(), f"internal-only file is not in the tree: {rel}"
        assert not (export_tree / rel).exists(), f"internal-only file exported: {rel}"
        index_file = export_tree / Path(rel).parent / "README.md"
        if index_file.exists():  # a doc's index row goes with it; scripts/ has no index
            assert Path(rel).name not in index_file.read_text(encoding="utf-8"), f"the exported index still links {rel}"
    assert (export_tree / "docs" / "README.md").is_file()


def test_the_identifier_list_stays_internal_and_the_exported_scanner_knows(export_tree: Path) -> None:
    """The denylist is a list of the names kept out of the public tree, so it is
    itself kept out: the exported scanner has no list, says so and exits 0 instead
    of either guessing or spelling the names in a public file."""
    assert (REPO_ROOT / "scripts" / "internal-identifiers.txt").is_file()
    assert not (export_tree / "scripts" / "internal-identifiers.txt").exists(), "the identifier list reached the export"
    exported_scanner = (export_tree / "scripts" / "check_no_internal_identifiers.py").read_text(encoding="utf-8")
    for term in checker.DENYLIST:
        assert term.lower() not in exported_scanner.lower(), f"the exported scanner spells {term!r}"
    result = subprocess.run([sys.executable, str(export_tree / "scripts" / "check_no_internal_identifiers.py"), "--paths", "."],
                            capture_output=True, text=True, cwd=str(export_tree))
    assert result.returncode == 0 and "no identifier list" in result.stdout, result.stdout + result.stderr


def test_internal_only_regions_are_stripped(export_tree: Path) -> None:
    """The README's internal-edition install block and marked table rows are for
    the internal audience only; the export must carry neither them nor any marker."""
    readme = (export_tree / "README.md").read_text(encoding="utf-8")
    assert "git clone <the FloofyCrew package>" not in readme, "the internal install block confuses public readers"
    assert ADAPTER_DIR + "/" not in readme, "the internal adapter's table row is marked internal-only"
    source = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "git clone <the FloofyCrew package>" in source, "the source README keeps the internal block"
    marker_stem = exporter.INTERNAL_ONLY_START.rsplit(":", 1)[0]  # built at runtime: a literal here would trip the stripper on this very file
    for path in export_tree.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            assert marker_stem not in text, f"marker survived the export: {path}"


def test_unbalanced_internal_only_markers_fail_the_export(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "doc.md").write_text(f"public\n<!-- {exporter.INTERNAL_ONLY_START} -->\ninternal\n", encoding="utf-8")
    with pytest.raises(exporter.ExportError, match="never closed"):
        exporter.strip_internal_only_regions(scratch)
    (scratch / "doc.md").write_text(f"public\n<!-- {exporter.INTERNAL_ONLY_END} -->\n", encoding="utf-8")
    with pytest.raises(exporter.ExportError, match="without a start"):
        exporter.strip_internal_only_regions(scratch)


def test_no_caches_or_git_metadata_in_the_export(export_tree: Path) -> None:
    leftovers = [p for p in export_tree.rglob("*") if p.name in exporter.SKIP_NAMES]
    assert leftovers == [], f"skip-set names copied: {[str(p) for p in leftovers[:5]]}"


def test_the_real_tree_exports_clean(export_tree: Path) -> None:
    """What CI runs: the full-tree scan of the export passes with the committed allowlist."""
    exporter.scan_export(export_tree)  # raises ExportError on any hit


def test_a_planted_leak_fails_the_scan(export_tree: Path) -> None:
    planted = export_tree / "docs" / "planted-leak-test.md"
    planted.write_text(f"an internal hostname: {HOSTNAME_TOKEN}\n", encoding="utf-8")
    try:
        with pytest.raises(exporter.ExportError, match="planted-leak-test"):
            exporter.scan_export(export_tree)
    finally:
        planted.unlink()


def test_the_real_tree_has_no_credential_shaped_literals(export_tree: Path) -> None:
    """GitHub's push protection declined the 1.3.6 export over a made-up key pair in a test; never again."""
    exporter.scan_secret_shapes(export_tree)  # raises ExportError on any hit


def test_a_planted_credential_shape_fails_the_export(export_tree: Path) -> None:
    # the planted values are assembled at runtime so this test file itself stays clean under the gate
    key_id = "AKIA" + "ABCDEFGHIJKLMNOP"
    secret = "abcdefghijklmnopqrstuvwxyz" + "0123456789ABCD"
    planted = export_tree / "docs" / "planted-credential-test.md"
    planted.write_text(f"commit 0123456789abcdef0123456789abcdef01234567\nid {key_id}\nsecret {secret}\n", encoding="utf-8")
    try:
        with pytest.raises(exporter.ExportError) as excinfo:
            exporter.scan_secret_shapes(export_tree)
    finally:
        planted.unlink()
    message = str(excinfo.value)
    assert "planted-credential-test.md:2: AWS access key id" in message
    assert "planted-credential-test.md:3: AWS secret access key" in message
    assert ":1:" not in message, "a 40-character hex commit hash is not a secret"
    # a lone 40-character run without a key id in the same file is not reported (GitHub's pair rule)
    planted.write_text(f"secret {secret}\n", encoding="utf-8")
    try:
        exporter.scan_secret_shapes(export_tree)
    finally:
        planted.unlink()
    # a private key block is reported on its own
    planted.write_text("-----BEGIN " + "RSA PRIVATE KEY-----\nMIIE\n", encoding="utf-8")
    try:
        with pytest.raises(exporter.ExportError, match="private key block"):
            exporter.scan_secret_shapes(export_tree)
    finally:
        planted.unlink()


def test_export_refuses_a_target_with_an_internal_remote(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    subprocess.run(["git", "init", "-q", str(clone)], check=True)
    subprocess.run(["git", "-C", str(clone), "remote", "add", "origin",
                    f"ssh://{HOSTNAME_TOKEN.strip('.')}/pkg/Whatever"], check=True)
    with pytest.raises(exporter.ExportError, match="internal remote"):
        exporter.export(clone)


def test_export_refuses_a_non_empty_target_without_force(tmp_path: Path) -> None:
    target = tmp_path / "dirty"
    target.mkdir()
    (target / "existing.txt").write_text("x\n", encoding="utf-8")
    with pytest.raises(exporter.ExportError, match="not empty"):
        exporter.export(target)
    exporter.export(target, force=True)  # --force replaces the contents
    assert not (target / "existing.txt").exists() and (target / "README.md").exists()


def test_export_refuses_a_target_inside_the_repository(tmp_path: Path) -> None:
    with pytest.raises(exporter.ExportError, match="outside the repository"):
        exporter.export(REPO_ROOT / "build" / "public-export")


def _git(clone: Path, *args: str, identity: tuple[str, str] | None = None) -> str:
    """Run git in ``clone`` with the desk's identity variables stripped, so what a
    commit carries comes from the clone's config — or from ``identity`` when given."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}
    if identity:
        env.update(GIT_AUTHOR_NAME=identity[0], GIT_AUTHOR_EMAIL=identity[1],
                   GIT_COMMITTER_NAME=identity[0], GIT_COMMITTER_EMAIL=identity[1])
    return subprocess.run(["git", "-C", str(clone), "-c", "commit.gpgsign=false", *args],
                          check=True, capture_output=True, text=True, env=env).stdout


def test_export_into_a_clone_pins_the_public_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A commit made in the exported clone belongs to the maintainer's GitHub
    account, whatever the desk's global config or environment says."""
    for var in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    clone = tmp_path / "clone"
    subprocess.run(["git", "init", "-q", "-b", "main", str(clone)], check=True)
    _git(clone, "config", "user.name", "Desk User")
    _git(clone, "config", "user.email", "desk@example.invalid")  # a stale or default identity
    report = exporter.export(clone)
    assert report["identity"] == f"{exporter.PUBLIC_GIT_NAME} <{exporter.PUBLIC_GIT_EMAIL}>"
    assert _git(clone, "config", "--get", "user.email").strip() == exporter.PUBLIC_GIT_EMAIL
    assert _git(clone, "config", "--get", "user.name").strip() == exporter.PUBLIC_GIT_NAME
    assert exporter.effective_identity(clone) == (exporter.PUBLIC_GIT_NAME, exporter.PUBLIC_GIT_EMAIL)
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", "export")
    assert _git(clone, "-P", "log", "-1", "--format=%an <%ae> %cn <%ce>").strip() == \
        f"{exporter.PUBLIC_GIT_NAME} <{exporter.PUBLIC_GIT_EMAIL}> {exporter.PUBLIC_GIT_NAME} <{exporter.PUBLIC_GIT_EMAIL}>"
    exporter.check_public_identity(clone)  # the unpushed commit passes the pre-push gate
    assert exporter.export(tmp_path / "plain-dir")["identity"] is None, "a plain directory has nothing to commit"


def test_check_public_identity_refuses_a_wrong_config_or_a_wrong_unpushed_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    clone = tmp_path / "clone"
    subprocess.run(["git", "init", "-q", "-b", "main", str(clone)], check=True)
    (clone / "a.md").write_text("x\n", encoding="utf-8")
    _git(clone, "add", "-A")
    # a commit under the desk's identity, as a plain `git commit` before the pin would make
    _git(clone, "commit", "-q", "-m", "wrong author", identity=("Desk User", "desk@example.invalid"))
    _git(clone, "config", "user.name", "Desk User")
    _git(clone, "config", "user.email", "desk@example.invalid")
    with pytest.raises(exporter.ExportError, match="next commit .*desk@example.invalid"):
        exporter.check_public_identity(clone)
    exporter.configure_public_identity(clone)  # the config is fixed; the commit is still wrong
    with pytest.raises(exporter.ExportError, match="1 unpushed commit.*desk@example.invalid"):
        exporter.check_public_identity(clone)
    _git(clone, "commit", "-q", "--amend", "--reset-author", "--no-edit")  # the fix the message names
    exporter.check_public_identity(clone)
    # a wrongly-attributed commit that is already PUSHED is not ours to rewrite: it is not reported
    upstream = tmp_path / "upstream.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(upstream)], check=True)
    _git(clone, "remote", "add", "origin", "../upstream.git")
    (clone / "b.md").write_text("y\n", encoding="utf-8")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", "pushed under a placeholder", identity=("Oscar Tseng", "someone@users.noreply.github.com"))
    _git(clone, "push", "-q", "-u", "origin", "main")
    assert exporter.unpublished_commits(clone) == []
    exporter.check_public_identity(clone)


def test_cli_check_passes_on_the_current_tree() -> None:
    result = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "export_public.py"), "--check"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "public export is clean" in result.stdout
