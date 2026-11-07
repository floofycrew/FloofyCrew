"""Example tests for ``scripts/cut-release.sh`` — the public half of a FloofyCrew release (task 10.5; Requirements 2.6, 7.5).

The script is driven as a subprocess against a fixture clone of a bare "public"
repository built under ``tmp_path``: two release commits (a human's ``1.3.11`` and
the release train's ``1.3.12``, whose commit never touched ``CHANGELOG.md``), the
train's annotated ``v1.3.12`` tag on the remote only, and copies of the real
``scripts/cut-release.sh`` and ``scripts/fold_forge_releases.py``. ``uv`` and ``gh``
are stand-ins on ``PATH``: ``uv run python`` runs this interpreter, ``uv lock``
rewrites the lock's version line, ``gh release view --json body`` answers from a
table; the three gate commands are replaced (``CUT_RELEASE_*``) by a recorder that
snapshots the tree as the gates see it. Nothing reaches the network or a real remote.

What is checked: ``--check-only`` fetches the remote's tags, folds the pending
release-train tag into ``CHANGELOG.md`` **before** the gates run, bumps every version
file, computes ``next`` from the remote's tags, and then puts every file back; a
version already tagged on the remote is refused before anything is edited; the
plainer refusals (dirty tree, the train's identity, nothing to release); and the
script itself passes the identifier scanner in both modes.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "cut-release.sh"
FOLD = REPO_ROOT / "scripts" / "fold_forge_releases.py"
SCANNER = REPO_ROOT / "scripts" / "check_no_internal_identifiers.py"

TRAIN_EMAIL = "forge@floofycrew.invalid"  # the release train's identity (fold_forge_releases.FORGE_EMAIL)
HUMAN = {"GIT_AUTHOR_NAME": "Maintainer", "GIT_AUTHOR_EMAIL": "maintainer@example.org", "GIT_COMMITTER_NAME": "Maintainer", "GIT_COMMITTER_EMAIL": "maintainer@example.org"}
TRAIN = {"GIT_AUTHOR_NAME": "FloofyCrew Forge", "GIT_AUTHOR_EMAIL": TRAIN_EMAIL, "GIT_COMMITTER_NAME": "FloofyCrew Forge", "GIT_COMMITTER_EMAIL": TRAIN_EMAIL}
TRAIN_TAG_DATE = "2026-10-30T12:00:00Z"
#: Every file a release edits (the script's ``TOUCHED``), relative to the clone.
TOUCHED = ("pyproject.toml", "uv.lock", "floofy-core/floofy_core/__init__.py", "loader-app/app.json", "loader-app/floofy_loader/__init__.py", "CHANGELOG.md", "README.md")
GATES = ("identifiers", "packaging", "pytest")
H2 = re.compile(r"^## .*$", re.MULTILINE)

TRAIN_NOTES = """# floofycrew v1.3.12 (nightly)

Commit `0123abc` on `main`, tag `v1.3.12`.

## Supports (host versions with `loader: ok` in the matrix)

- internal/beta: 0.7.0.5
- external/stable: 0.9.0

## Artifacts

- `floofy.pyz` — 1 bytes, sha256 `aa`
"""

pytestmark = pytest.mark.skipif(not SCRIPT.is_file() or not FOLD.is_file(), reason="scripts/cut-release.sh and scripts/fold_forge_releases.py are the subjects")


# --- the fixture repository ------------------------------------------------------------------------------------------


def _version_files(version: str) -> dict[str, str]:
    """The seven files a release edits, all saying ``version``; the changelog is the caller's."""
    return {
        "pyproject.toml": f'[project]\nname = "floofycrew"\nversion = "{version}"\nrequires-python = ">=3.12"\n\n[tool.floofycrew.supports.internal]\nbeta = ["0.7.0.5", "0.8.0.6"]\n\n[tool.floofycrew.supports.external]\nstable = ["0.9.0"]\n',
        "uv.lock": f'version = 1\nrequires-python = ">=3.12"\n\n[[package]]\nname = "floofycrew"\nversion = "{version}"\nsource = {{ editable = "." }}\n',
        "floofy-core/floofy_core/__init__.py": f'"""core"""\n\n__version__ = "{version}"\n',
        "loader-app/floofy_loader/__init__.py": f'"""loader"""\n\n__version__ = "{version}"\n',
        "loader-app/app.json": json.dumps({"name": "floofycrew", "version": version, "ui": {"entry": "index.mjs"}}, indent=2) + "\n",
        "README.md": f"# FloofyCrew\n\nAn unofficial modding ecosystem.\n\nCurrent release: **{version}** &mdash; see CHANGELOG.md.\n",
        ".gitignore": "__pycache__/\n*.pyc\n",
    }


CHANGELOG_WITH_ENTRY = "# Changelog\n\nFloofyCrew follows SemVer.\n\n## Unreleased\n\n- **Something new** for the next release.\n\n## 1.3.11 — 2026-10-24\n\nThe previous hand-cut release.\n\n### Supports\n\n| Edition | Channel | Host versions |\n|---|---|---|\n| internal | beta | 0.7.0.5 |\n"
CHANGELOG_EMPTY = "# Changelog\n\n## Unreleased\n\n## 1.3.11 — 2026-10-24\n\nThe previous hand-cut release.\n"


@dataclass
class Clone:
    """The fixture: ``root`` is the working clone, ``remote`` its bare origin, ``bin`` the fakes, ``snap`` the gate recorder's output."""

    root: Path
    remote: Path
    bin: Path
    snap: Path
    env: dict[str, str] = field(default_factory=dict)
    head: str = ""

    def git(self, *args: str, identity: dict[str, str] = HUMAN, cwd: Path | None = None) -> str:
        return _git(cwd or self.root, *args, env={**self.env, **identity})

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(self.root / "scripts" / "cut-release.sh"), *args], cwd=self.root, env=self.env, capture_output=True, text=True, timeout=300, check=False)

    def gate_runs(self) -> list[str]:
        log = self.snap / "gates.log"
        return log.read_text(encoding="utf-8").split() if log.is_file() else []

    def gh_calls(self) -> list[list[str]]:
        log = self.bin / "gh-calls.jsonl"
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()] if log.is_file() else []

    def uv_calls(self) -> list[list[str]]:
        log = self.bin / "uv-calls.jsonl"
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()] if log.is_file() else []

    def snapshot(self, gate: str, path: str) -> str:
        return (self.snap / gate / path).read_text(encoding="utf-8")

    def tracked(self, path: str) -> str:
        return (self.root / path).read_text(encoding="utf-8")


def _git(cwd: Path, *args: str, env: dict[str, str]) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed in {cwd}: {done.stderr.strip()}")
    return done.stdout.strip()


def _write(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _fakes(bin_dir: Path, snap: Path, bodies: dict[str, str]) -> None:
    """``uv`` (runs this interpreter; ``lock`` rewrites the lock's version), ``gh`` (``release view --json body``) and the gate recorder."""
    bin_dir.mkdir()
    python = sys.executable
    _executable(
        bin_dir / "uv",
        f"#!{python}\n"
        "import json, pathlib, re, subprocess, sys\n"
        "here = pathlib.Path(__file__).resolve().parent\n"
        "argv = sys.argv[1:]\n"
        "with (here / 'uv-calls.jsonl').open('a') as log:\n"
        "    log.write(json.dumps(argv) + '\\n')\n"
        "if argv[:1] == ['lock']:\n"
        "    version = re.search(r'^version = \"([^\"]*)\"$', pathlib.Path('pyproject.toml').read_text(encoding='utf-8'), re.M).group(1)\n"
        "    lines = pathlib.Path('uv.lock').read_text(encoding='utf-8').split('\\n')\n"
        "    at = lines.index('name = \"floofycrew\"')\n"
        "    lines[at + 1] = f'version = \"{version}\"'\n"
        "    pathlib.Path('uv.lock').write_text('\\n'.join(lines), encoding='utf-8')\n"
        "    sys.exit(0)\n"
        "if argv[:1] == ['run']:\n"
        "    rest = argv[1:]\n"
        "    while rest and rest[0].startswith('-'):\n"
        "        rest = rest[1:]\n"
        "    if rest[:1] == ['python']:\n"
        f"        sys.exit(subprocess.call([{python!r}, *rest[1:]]))\n"
        "print(f'fake uv: unsupported: {argv}', file=sys.stderr)\n"
        "sys.exit(1)\n",
    )
    (bin_dir / "gh-releases.json").write_text(json.dumps(bodies) + "\n", encoding="utf-8")
    _executable(
        bin_dir / "gh",
        f"#!{python}\n"
        "import json, pathlib, sys\n"
        "here = pathlib.Path(__file__).resolve().parent\n"
        "argv = sys.argv[1:]\n"
        "with (here / 'gh-calls.jsonl').open('a') as log:\n"
        "    log.write(json.dumps(argv) + '\\n')\n"
        "releases = json.loads((here / 'gh-releases.json').read_text(encoding='utf-8'))\n"
        "if argv[:2] == ['release', 'view'] and '--json' in argv:\n"
        "    tag = argv[2]\n"
        "    if tag not in releases:\n"
        "        print(f'release not found: {tag}', file=sys.stderr)\n"
        "        sys.exit(1)\n"
        "    print(json.dumps({'body': releases[tag]}))\n"
        "    sys.exit(0)\n"
        "print(f'fake gh: unsupported: {argv}', file=sys.stderr)\n"
        "sys.exit(1)\n",
    )
    # the gate recorder: `gate NAME` appends NAME to gates.log and copies the touched files into snap/NAME/
    copies = " ".join(TOUCHED)
    _executable(
        bin_dir / "gate",
        "#!/bin/bash\nset -euo pipefail\n"
        f'snap={str(snap)!r}\nmkdir -p "$snap/$1"\necho "$1" >>"$snap/gates.log"\n'
        f"for f in {copies}; do mkdir -p \"$snap/$1/$(dirname \"$f\")\"; cp \"$f\" \"$snap/$1/$f\"; done\n"
        'echo "gate $1 ok"\n',
    )


def make_clone(tmp_path: Path, *, train_release: bool = True, changelog: str = CHANGELOG_WITH_ENTRY) -> Clone:
    """A clone of a bare public repository holding the script, ``1.3.11`` by a human and, with ``train_release``, ``1.3.12`` by the train."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("", encoding="utf-8")
    snap = tmp_path / "snap"
    snap.mkdir()
    bin_dir = tmp_path / "bin"
    base_env = {k: v for k, v in os.environ.items() if not k.startswith(("CUT_RELEASE_", "GIT_", "FORGE_"))}
    env = {
        **base_env,
        "PATH": f"{bin_dir}{os.pathsep}{base_env.get('PATH', '')}",
        "GIT_CONFIG_GLOBAL": str(gitconfig),  # no global settings (signing, hooks, identity) reach the fixture
        "GIT_CONFIG_NOSYSTEM": "1",
        "TMPDIR": str(tmp_path),
        "CUT_RELEASE_SCAN": f"{bin_dir}/gate identifiers",
        "CUT_RELEASE_BUILD_CHECK": f"{bin_dir}/gate packaging",
        "CUT_RELEASE_PYTEST": f"{bin_dir}/gate pytest",
    }
    _fakes(bin_dir, snap, {"v1.3.12": TRAIN_NOTES})

    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main", env=env)
    _write(seed, {**_version_files("1.3.11"), "CHANGELOG.md": changelog})
    (seed / "scripts").mkdir()
    for name in ("cut-release.sh", "fold_forge_releases.py"):
        target = seed / "scripts" / name
        target.write_bytes((REPO_ROOT / "scripts" / name).read_bytes())
        target.chmod(target.stat().st_mode | stat.S_IXUSR)
    _git(seed, "add", "-A", env=env)
    _git(seed, "commit", "-q", "-m", "chore(release): Release floofycrew 1.3.11", env={**env, **HUMAN})
    _git(seed, "tag", "-a", "v1.3.11", "-m", "floofycrew v1.3.11", env={**env, **HUMAN})
    if train_release:
        # the train's release commit: the version files and the README line only; CHANGELOG.md is never opened (Requirement 7.5)
        _write(seed, _version_files("1.3.12"))
        _git(seed, "commit", "-q", "-am", "chore(release): Release floofycrew 1.3.12", env={**env, **TRAIN})
    remote = tmp_path / "origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(seed), str(remote), env=env)
    root = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(remote), str(root), env=env)
    _git(root, "config", "user.name", "Maintainer", env=env)
    _git(root, "config", "user.email", "maintainer@example.org", env=env)
    if train_release:
        # the train tagged its release on the remote after this clone was taken: `git tag -l` here has v1.3.11 only
        head = _git(root, "rev-parse", "HEAD", env=env)
        _git(remote, "tag", "-a", "v1.3.12", "-m", "floofycrew v1.3.12", head, env={**env, **TRAIN, "GIT_COMMITTER_DATE": TRAIN_TAG_DATE})
    clone = Clone(root=root, remote=remote, bin=bin_dir, snap=snap, env=env)
    clone.head = clone.git("rev-parse", "HEAD")
    return clone


@pytest.fixture
def clone(tmp_path: Path) -> Clone:
    return make_clone(tmp_path)


def _remote_refs(clone: Clone) -> list[str]:
    return sorted(line.split("\t", 1)[1] for line in clone.git("ls-remote", str(clone.remote)).splitlines() if "\t" in line)


def _locked_version(lock: str) -> str:
    """The ``version`` the lock records for ``floofycrew`` (the line after ``name = "floofycrew"``, as the script reads it)."""
    lines = lock.split("\n")
    return lines[lines.index('name = "floofycrew"') + 1].removeprefix("version = ").strip('"')


def _assert_untouched(clone: Clone, *, refs_before: list[str]) -> None:
    """Nothing committed, tagged or left edited: HEAD, the tree and the remote are as they were."""
    assert clone.git("status", "--porcelain") == ""
    assert clone.git("rev-parse", "HEAD") == clone.head
    assert "v1.3.13" not in clone.git("tag", "-l").split()
    assert _remote_refs(clone) == refs_before


# --- --check-only folds first, gates second, restores last -------------------------------------------------------------


def test_check_only_folds_the_train_tags_before_the_gates_and_restores_the_tree(clone: Clone) -> None:
    """``next`` counts from the remote's tags (v1.3.12 is on the remote only), the pending train tag is folded
    into CHANGELOG.md with its GitHub notes before the three gates see the tree, every version file carries
    the new version at gate time, and afterwards nothing is committed, tagged or left edited (Requirement 7.5)."""
    refs_before = _remote_refs(clone)
    assert clone.git("tag", "-l").split() == ["v1.3.11"], "the train's tag is not in the clone yet: the script must fetch it"

    done = clone.run("next", "--check-only")

    assert done.returncode == 0, done.stdout + done.stderr
    assert "next: the highest tag on origin is v1.3.12, so this release is 1.3.13" in done.stdout
    assert "releasing 1.3.12 -> 1.3.13: 1 unreleased entries; Forge releases to fold: v1.3.12" in done.stdout
    assert "check-only: floofycrew 1.3.13 would get through; nothing committed or tagged" in done.stdout
    assert f"working tree restored ({len(TOUCHED)} files back to HEAD)" in done.stdout

    # the fold happened before the first gate, and the three gates ran in order on the bumped tree
    assert clone.gate_runs() == list(GATES)
    changelog = clone.snapshot("identifiers", "CHANGELOG.md")
    headings = H2.findall(changelog)
    assert headings[0] == "## Unreleased"
    assert re.fullmatch(r"## 1\.3\.13 — \d{4}-\d{2}-\d{2}", headings[1]), headings
    assert headings[2:] == [f"## 1.3.12 — {TRAIN_TAG_DATE[:10]}", "## 1.3.11 — 2026-10-24"]
    new_section = changelog[changelog.index("## 1.3.13"):changelog.index("## 1.3.12")]
    assert "### Supports" in new_section and "| internal | beta | 0.7.0.5, 0.8.0.6 |" in new_section and "| external (public) | stable | 0.9.0 |" in new_section
    assert "- **Something new** for the next release." in new_section, "the Unreleased bullets become the new release's"
    folded = changelog[changelog.index("## 1.3.12"):changelog.index("## 1.3.11")]
    assert "Commit `0123abc` on `main`, tag `v1.3.12`." in folded, "the body came from gh release view --json body"
    assert "### Supports\n\n| Edition | Channel | Host versions |" in folded and "| internal | beta | 0.7.0.5 |" in folded
    assert "### Artifacts" in folded and "# floofycrew v1.3.12 (nightly)" not in folded, "the title line is dropped, the headings nested"
    for gate in GATES:
        assert 'version = "1.3.13"' in clone.snapshot(gate, "pyproject.toml")
        assert _locked_version(clone.snapshot(gate, "uv.lock")) == "1.3.13", "uv lock ran after the bump"
        assert '__version__ = "1.3.13"' in clone.snapshot(gate, "floofy-core/floofy_core/__init__.py")
        assert '__version__ = "1.3.13"' in clone.snapshot(gate, "loader-app/floofy_loader/__init__.py")
        assert json.loads(clone.snapshot(gate, "loader-app/app.json"))["version"] == "1.3.13"
        assert "Current release: **1.3.13**" in clone.snapshot(gate, "README.md")
        assert clone.snapshot(gate, "CHANGELOG.md") == changelog
    assert clone.gh_calls() == [["release", "view", "v1.3.12", "--json", "body"]]
    assert ["lock", "-q"] in clone.uv_calls()

    # the fetch brought the train's tag; the check-only run put every file back and cut nothing
    assert clone.git("tag", "-l").split() == ["v1.3.11", "v1.3.12"]
    _assert_untouched(clone, refs_before=refs_before)
    assert clone.tracked("CHANGELOG.md") == CHANGELOG_WITH_ENTRY
    assert 'version = "1.3.12"' in clone.tracked("pyproject.toml")


def test_an_explicit_version_folds_too_and_a_notes_file_opens_the_section(clone: Clone, tmp_path: Path) -> None:
    notes = tmp_path / "notes.md"
    notes.write_text("The intro paragraph of this release.\n", encoding="utf-8")
    done = clone.run("1.4.0", "--notes", str(notes), "--check-only")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "releasing 1.3.12 -> 1.4.0" in done.stdout
    changelog = clone.snapshot("pytest", "CHANGELOG.md")
    assert [h.split(" — ")[0] for h in H2.findall(changelog)] == ["## Unreleased", "## 1.4.0", "## 1.3.12", "## 1.3.11"]
    section = changelog[changelog.index("## 1.4.0"):changelog.index("## 1.3.12")]
    assert "\n\nThe intro paragraph of this release.\n\n### Supports\n" in section
    assert clone.git("status", "--porcelain") == "" and clone.git("rev-parse", "HEAD") == clone.head


# --- refusals -------------------------------------------------------------------------------------------------------------


def test_refuses_a_version_already_tagged_on_the_remote(clone: Clone) -> None:
    """The remote's tags are the truth: a version it already carries is refused before any edit, fold or gate."""
    clone.git("tag", "v1.3.13", clone.head, cwd=clone.remote)  # tagged on the remote only, after the clone was taken
    refs_before = _remote_refs(clone)

    done = clone.run("1.3.13", "--check-only")

    assert done.returncode == 1
    assert "FAILED: v1.3.13 already exists on origin" in done.stderr
    assert "working tree restored" not in done.stdout, "nothing was edited, so nothing had to be put back"
    assert clone.gate_runs() == [] and clone.gh_calls() == [] and clone.uv_calls() == []
    assert clone.git("status", "--porcelain") == "" and clone.git("rev-parse", "HEAD") == clone.head
    assert _remote_refs(clone) == refs_before
    # `next` steps over it: the remote's highest tag is now v1.3.13
    again = clone.run("next", "--check-only")
    assert again.returncode == 0, again.stdout + again.stderr
    assert "so this release is 1.3.14" in again.stdout


def test_refuses_a_dirty_tree_and_the_trains_identity_before_touching_anything(clone: Clone) -> None:
    (clone.root / "README.md").write_text(clone.tracked("README.md") + "\nlocal edit\n", encoding="utf-8")
    dirty = clone.run("1.3.13", "--check-only")
    assert dirty.returncode == 1 and "FAILED: uncommitted tracked changes:" in dirty.stderr and " M README.md" in dirty.stderr
    clone.git("checkout", "--", "README.md")

    clone.git("config", "user.email", TRAIN_EMAIL)
    impostor = clone.run("1.3.13", "--check-only")
    assert impostor.returncode == 1 and f"FAILED: user.email is the Forge's identity ({TRAIN_EMAIL}); a hand-cut release is a person's" in impostor.stderr
    clone.git("config", "--unset", "user.email")
    unset = clone.run("1.3.13", "--check-only")
    assert unset.returncode == 1 and "user.name and user.email are not set in this clone's .git/config" in unset.stderr

    assert clone.gate_runs() == [] and clone.gh_calls() == [] and clone.uv_calls() == []
    assert clone.git("status", "--porcelain") == "" and clone.git("rev-parse", "HEAD") == clone.head


def test_refuses_when_nothing_is_to_release_and_releases_when_only_train_tags_wait(tmp_path: Path) -> None:
    """An empty ``## Unreleased`` and no pending train tag is nothing to release; the train's tags alone are enough."""
    quiet = make_clone(tmp_path / "quiet", train_release=False, changelog=CHANGELOG_EMPTY)
    done = quiet.run("1.3.12", "--check-only")
    assert done.returncode == 1
    assert "FAILED: nothing to release: CHANGELOG.md's '## Unreleased' has no entries and no Forge release is waiting to be folded" in done.stderr
    assert quiet.gate_runs() == [] and quiet.git("status", "--porcelain") == ""
    none_yet = quiet.run("next", "--check-only")  # v1.3.11 is the remote's highest tag; nothing waits to be folded, so the refusal is the same
    assert none_yet.returncode == 1 and "so this release is 1.3.12" in none_yet.stdout and "FAILED: nothing to release" in none_yet.stderr

    waiting = make_clone(tmp_path / "waiting", train_release=True, changelog=CHANGELOG_EMPTY)
    done = waiting.run("next", "--check-only")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "releasing 1.3.12 -> 1.3.13: 0 unreleased entries; Forge releases to fold: v1.3.12" in done.stdout
    assert [h.split(" — ")[0] for h in H2.findall(waiting.snapshot("identifiers", "CHANGELOG.md"))] == ["## Unreleased", "## 1.3.13", "## 1.3.12", "## 1.3.11"]
    assert waiting.gate_runs() == list(GATES) and waiting.git("status", "--porcelain") == ""
    assert waiting.tracked("CHANGELOG.md") == CHANGELOG_EMPTY


def test_a_failing_gate_restores_the_tree_and_names_the_gate(clone: Clone) -> None:
    clone.env["CUT_RELEASE_BUILD_CHECK"] = f"{clone.bin}/gate packaging && echo 'NOT reproducible' && false"
    done = clone.run("next", "--check-only")
    assert done.returncode == 1
    assert "FAILED: gate packaging failed" in done.stderr and "NOT reproducible" in done.stderr
    assert clone.gate_runs() == ["identifiers", "packaging"], "the pytest gate never ran"
    assert "working tree restored" in done.stdout
    assert clone.git("status", "--porcelain") == "" and clone.git("rev-parse", "HEAD") == clone.head
    assert clone.tracked("CHANGELOG.md") == CHANGELOG_WITH_ENTRY


def test_usage_errors_exit_2_before_any_precondition(clone: Clone) -> None:
    assert clone.run().returncode == 2
    assert clone.run("1.3").returncode == 2
    assert clone.run("1.3.13", "--bogus").returncode == 2
    assert clone.run("1.3.13", "--notes", str(clone.root / "missing.md")).returncode == 2
    assert clone.gate_runs() == [] and clone.uv_calls() == []


# --- the script is public-tree clean --------------------------------------------------------------------------------------


def test_the_script_and_this_test_pass_the_identifier_scanner() -> None:
    """``scripts/cut-release.sh`` lives in the public tree: the hashed scan the public CI runs passes over it (and over this file);
    the plain scan too where the plain list is present (an overlay checkout; a public clone carries only the hashed list)."""
    paths = ["scripts/cut-release.sh", "packaging/tests/test_cut_release.py"]
    allowlist = REPO_ROOT / "scripts" / "public-export-allow.txt"
    hashed = REPO_ROOT / "scripts" / "internal-identifiers.sha256"
    assert hashed.is_file(), "the public tree carries the hashed identifier list"
    common = [sys.executable, str(SCANNER), "--paths", *paths, "--allowlist", str(allowlist)]
    done = subprocess.run([*common, "--hashed", str(hashed)], cwd=REPO_ROOT, capture_output=True, text=True, timeout=300, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "OK: no internal identifiers" in done.stdout
    plain = REPO_ROOT / "scripts" / "internal-identifiers.txt"
    if plain.is_file():
        done = subprocess.run([*common, "--denylist", str(plain)], cwd=REPO_ROOT, capture_output=True, text=True, timeout=300, check=False)
        assert done.returncode == 0, done.stdout + done.stderr
