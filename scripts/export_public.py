#!/usr/bin/env python3
"""Build the filtered public (GitHub) export of the FloofyCrew repository.

The repository has one source of truth, hosted internally; the public GitHub
repository receives a FILTERED export, never the tree as-is (design DR-3,
Requirement 10.2). This script is that filter, and the only sanctioned way to
produce a tree for the public remote:

1. **Copy the public set** — an explicit include list (`PUBLIC_SET`). Anything
   not named is out by construction: internal-only directories are never
   consulted, so the exporter needs no exclude list of them. Inside the set,
   only files **git tracks** are copied: an unreleased mod, in-flight scratch
   work or a dev-linked mod's `.floofy/` runtime state never ship, and the few
   tracked files written for the internal audience alone (`INTERNAL_ONLY_FILES`)
   are dropped with their index rows.
2. **Drop internal-only mods** (`EXCLUDED_MODS`): mods that patch internal-only
   hosts or ship fan artwork stay out, and their rows are removed from
   `mods/README.md`. `WITHHELD_MODS` are dropped the same way but for now only:
   a mod whose code still spells an internal name waits there until it learns
   the name from the host at runtime.
3. **Scan the WHOLE exported tree** with `check_no_internal_identifiers.py`
   (`--paths .`), against `scripts/public-export-allow.txt` — a separate,
   justified allowlist whose every line must name an existing exported path.
   Any other hit fails the export. A second pass (`scan_secret_shapes`) refuses
   credential-shaped literals — AWS key ids, private key blocks, API tokens —
   which GitHub's push protection declines on sight, made-up test values
   included; a fixture that needs the shape assembles it at runtime.
4. **Refuse internal remotes**: with `--out` pointing into a git clone, every
   configured remote is checked; an internal host fails the export before a
   single file is written.
5. **Pin the public identity**: with `--out` pointing into a git clone, the
   clone's own `user.name` / `user.email` are set to `PUBLIC_GIT_NAME` /
   `PUBLIC_GIT_EMAIL`, so the commit the operator makes there is attributed
   to the maintainer's GitHub account — not to the internal address the source
   history is written under. `check_public_identity` (run by the pre-push
   review) refuses a clone whose effective identity, or any commit a push would
   publish, says otherwise.

The script never pushes. It prints the tree (or writes it into `--out`) and the
operator runs `git` themselves.

Usage:
    python scripts/export_public.py --check            # temp dir, scan, delete (CI)
    python scripts/export_public.py --check --keep DIR # keep the tree to inspect
    python scripts/export_public.py --out DIR          # write into DIR (a public clone)
    python scripts/export_public.py --out DIR --force  # clear DIR's contents (not .git) first

Standard library only, so it runs on any Python 3.12 without a virtualenv.
"""
from __future__ import annotations

import argparse
import functools
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNER = REPO_ROOT / "scripts" / "check_no_internal_identifiers.py"
ALLOWLIST = "scripts/public-export-allow.txt"

#: Credential-shaped literals that GitHub's push protection — and any secret scanner a
#: fork's CI runs — rejects on sight, fake or not: a push carrying one is declined
#: before it lands. Fixtures that need the shape at runtime assemble it from adjacent
#: literals (the doctor's redaction tests do); the shipped source never spells it.
#: Each pattern is written so that this file, which contains it, does not match it.
SECRET_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS access key id", re.compile(r"(?<![A-Za-z0-9])(?:A3T[A-Z0-9]|AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[A-Z0-9]{16}(?![A-Za-z0-9])")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("OpenAI/Anthropic API key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}")),
)
#: GitHub pairs an AWS secret with an access key id in the SAME file: a 40-character
#: base64 run is reported only there, and only when it mixes cases and digits (a hex
#: commit hash is 40 characters too, but never upper-case).
_AWS_SECRET_RUN = re.compile(r"(?<![A-Za-z0-9/+])[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+])")

#: Everything the public repository receives, relative to the repository root.
#: A directory takes its whole subtree (minus SKIP_NAMES and EXCLUDED_MODS).
#: This list is the boundary: a path not named here never reaches GitHub.
PUBLIC_SET: tuple[str, ...] = (
    # the shared core and its neighbours (identical across editions)
    "floofy-core",
    "loader-app",
    "spa-host",
    "registry-tools",
    "docs",
    "mods",
    "branding",
    "scripts",
    # the public edition only
    "editions/public",
    "packaging/public",
    "packaging/tests",
    "packaging/README.md",
    # repository files
    "README.md",
    "CHANGELOG.md",
    "pyproject.toml",
    "uv.lock",
    ".gitignore",
    # the public release workflow; ci.yml stays internal (it builds both editions)
    ".github/workflows/release.yml",
)

#: Copied when present, skipped when not (a missing entry is NOT an error):
#: the license/notice files land with the in-flight licensing work. Once they
#: are committed, move them into PUBLIC_SET so the export REFUSES to build
#: without them — a public repository must not ship unlicensed.
PUBLIC_SET_OPTIONAL: tuple[str, ...] = ("LICENSE", "NOTICE.md")

#: Mods that must not ship publicly: patches of internal-only hosts, fan artwork.
#: The exported ``mods/README.md`` must not name them at all.
EXCLUDED_MODS: tuple[str, ...] = ("mochi-pet-zoom-fix", "rimuru-branding")

#: Mods held back from the public tree for now, not by nature: their code still
#: spells an internal name the scan refuses (agent-backend-router imports the
#: internal edition's companion package for the credential broker's contract keys
#: and the edition's Claude Code factory and client classes). Dropped like
#: EXCLUDED_MODS — directory and README row — but other rows may still refer to
#: them; the mod ships publicly once it learns those names from the host at
#: runtime, by removing it from this tuple.
WITHHELD_MODS: tuple[str, ...] = ("agent-backend-router",)

#: Every mod the export leaves out, for the copy and the README prune.
DROPPED_MODS: tuple[str, ...] = EXCLUDED_MODS + WITHHELD_MODS

#: Region markers for internal-AUDIENCE content in shared files (e.g. the
#: internal edition's install block in README.md): everything between a line
#: containing the start marker and a line containing the end marker — marker
#: lines included — is dropped from the export. This is about audience fit,
#: not leak prevention: the scanner still guards identifiers; the markers
#: remove instructions and links that only make sense inside the internal
#: network and would confuse a public reader.
#: The markers are assembled at runtime so THIS file never contains them
#: literally — the stripper walks every exported text file, and this script is
#: itself exported; a literal marker here would make the stripper eat its own
#: source. Spelled out they read floofy&#58;internal-only&#58;start / end / line.
INTERNAL_ONLY_START = ":".join(("floofy", "internal-only", "start"))
INTERNAL_ONLY_END = ":".join(("floofy", "internal-only", "end"))
#: Single-line variant for places where a block marker would break the file's
#: own syntax (a markdown table row carries it as an inline HTML comment).
INTERNAL_ONLY_LINE = ":".join(("floofy", "internal-only", "line"))

#: Directory names never copied (caches, build output, environments, and the
#: ``.floofy/`` runtime state a dev-linked mod keeps beside its sources).
SKIP_NAMES: frozenset[str] = frozenset(
    {".git", "__pycache__", "node_modules", ".venv", ".scratch", "dist", "build",
     ".pytest_cache", ".hypothesis", ".floofy-run", ".builder-mcp", ".cache", ".floofy"}
)

#: Files inside a public-set directory that are for the internal audience only
#: and have no public counterpart (the migration guide is for the standalone
#: patcher that only ever existed internally; the scanner's identifier list).
#: A dropped doc's index row carries the single-line marker so the stripper
#: removes it too.
INTERNAL_ONLY_FILES: tuple[str, ...] = (
    "docs/migrating-from-the-standalone-patcher.md",
    # the scanner's denylist: a list of the names kept out of the public tree is itself such a list
    "scripts/internal-identifiers.txt",
)

#: The identity every commit bound for the public remote carries. GitHub links a
#: commit to an account through the author/committer e-mail, so this is the
#: maintainer's GitHub address (verified on that account); the internal address
#: the source history is written under must never reach the public remote.
#: Spelled in exactly one place: the exporter writes it into the public clone
#: and the pre-push review refuses anything else.
PUBLIC_GIT_NAME = "Oscar Tseng"
PUBLIC_GIT_EMAIL = "oscar217b@gmail.com"

def _internal_remote_markers() -> tuple[str, ...]:
    """The scanner's denylist doubles as the internal-remote check: a git remote
    URL carrying any denylisted identifier is an internal remote. Loaded from the
    scanner so the hostnames are spelled in exactly one file."""
    spec = importlib.util.spec_from_file_location("check_no_internal_identifiers", SCANNER)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)  # dataclasses resolve the module during exec
    spec.loader.exec_module(module)
    return tuple(module.DENYLIST)


class ExportError(RuntimeError):
    """A failed precondition or a dirty scan; the message says which."""


@functools.cache
def _tracked() -> tuple[frozenset[str], frozenset[str]]:
    """``(files, directories)`` git tracks, as repository-relative POSIX paths.

    The export ships what is committed, nothing else: an unreleased mod, an
    agent's in-flight scratch work, or the ``.floofy/`` runtime state a
    dev-linked mod writes beside its sources are all untracked, so they never
    reach the public tree even when they sit inside a public-set directory.
    """
    result = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "-z"], capture_output=True, text=True)
    if result.returncode != 0:
        raise ExportError("git ls-files failed; the export needs the repository's index to tell tracked files apart")
    files = frozenset(p for p in result.stdout.split("\0") if p)
    directories = frozenset(parent.as_posix() for p in files for parent in PurePosixPath(p).parents if parent.as_posix() != ".")
    return files, directories


def _ignore(directory: str, names: list[str]) -> set[str]:
    ignored = {n for n in names if n in SKIP_NAMES}
    tracked_files, tracked_dirs = _tracked()
    relative_dir = Path(directory).resolve().relative_to(REPO_ROOT)
    for name in names:
        rel = (relative_dir / name).as_posix()
        if rel in INTERNAL_ONLY_FILES or (rel not in tracked_files and rel not in tracked_dirs):
            ignored.add(name)
    if Path(directory) == REPO_ROOT / "mods":
        ignored.update(n for n in names if n in DROPPED_MODS)
    return ignored


def copy_public_set(target: Path) -> list[str]:
    """Copy the public set into ``target``; return the top-level paths written."""
    written: list[str] = []
    for rel in (*PUBLIC_SET, *PUBLIC_SET_OPTIONAL):
        source = REPO_ROOT / rel
        if not source.exists():
            if rel in PUBLIC_SET_OPTIONAL:
                continue
            raise ExportError(f"public-set path does not exist: {rel}")
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination, ignore=_ignore, dirs_exist_ok=False)
        else:
            shutil.copy2(source, destination)
        written.append(rel)
    return written


def prune_mods_readme(target: Path) -> int:
    """Drop the excluded mods' table rows from the exported ``mods/README.md``."""
    readme = target / "mods" / "README.md"
    if not readme.exists():
        return 0
    kept: list[str] = []
    dropped = 0
    markers = tuple(f"[`{mod}/`]" for mod in DROPPED_MODS)
    for line in readme.read_text(encoding="utf-8").splitlines(keepends=True):
        if line.lstrip().startswith("|") and any(marker in line for marker in markers):
            dropped += 1
            continue
        kept.append(line)
    readme.write_text("".join(kept), encoding="utf-8")
    return dropped


def strip_internal_only_regions(target: Path) -> int:
    """Drop every marked internal-only region from the exported text files.

    Returns the number of regions removed. Unbalanced markers in a file fail
    the export: a dangling start would silently ship everything after it.
    """
    removed = 0
    for path in sorted(target.rglob("*")):
        if not path.is_file() or path.is_symlink() or ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if all(marker not in text for marker in (INTERNAL_ONLY_START, INTERNAL_ONLY_END, INTERNAL_ONLY_LINE)):
            continue
        kept: list[str] = []
        depth = 0
        for line in text.splitlines(keepends=True):
            if INTERNAL_ONLY_LINE in line:
                removed += 1
                continue
            if INTERNAL_ONLY_START in line:
                depth += 1
                removed += 1
                continue
            if INTERNAL_ONLY_END in line:
                if depth == 0:
                    raise ExportError(f"{path.relative_to(target)}: internal-only END marker without a start")
                depth -= 1
                continue
            if depth == 0:
                kept.append(line)
        if depth != 0:
            raise ExportError(f"{path.relative_to(target)}: internal-only START marker never closed")
        path.write_text("".join(kept), encoding="utf-8")
    return removed


def check_excluded_mods_absent(target: Path) -> None:
    for mod in DROPPED_MODS:
        if (target / "mods" / mod).exists():
            raise ExportError(f"excluded mod was copied: mods/{mod}")
    remaining = sorted(p.name for p in (target / "mods").iterdir() if p.is_dir())
    if not remaining:
        raise ExportError("no mods left in the export; the exclude list ate everything")


def scan_export(target: Path) -> None:
    """Run the identifier scan over the WHOLE exported tree; raise on any hit.

    The SOURCE tree's scanner does the scanning: it sits next to the identifier
    list, which the export deliberately lacks. The exported copy would find no
    list and pass vacuously — so a vacuous pass is refused here too.
    """
    allowlist = target / ALLOWLIST
    if not allowlist.exists():
        raise ExportError(f"the exported tree is missing {ALLOWLIST}")
    result = subprocess.run(
        [sys.executable, str(SCANNER), "--root", str(target), "--paths", ".", "--allowlist", str(allowlist)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ExportError("the exported tree is NOT clean:\n" + result.stdout + result.stderr)
    if not result.stdout.startswith("OK: no internal identifiers"):
        raise ExportError("the identifier scan did not run against a list (is scripts/internal-identifiers.txt present "
                          "in the source tree?):\n" + result.stdout + result.stderr)


def _text_files(target: Path):
    """Every UTF-8 text file in ``target`` (``.git`` skipped; binaries — images, archives, signatures — skipped)."""
    for path in sorted(target.rglob("*")):
        if not path.is_file() or ".git" in path.relative_to(target).parts:
            continue
        try:
            yield path, path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue


def scan_secret_shapes(target: Path) -> None:
    """Refuse any credential-shaped literal in the exported tree (``SECRET_SHAPES``).

    GitHub's push protection declines a push that carries one — a made-up test key
    included — so a tree with one in it cannot be published. The gate says so
    here, with file and line, instead of the remote saying so after the push.
    """
    hits: list[str] = []
    for path, text in _text_files(target):
        rel = path.relative_to(target).as_posix()
        lines = text.splitlines()
        key_id_seen = False
        for number, line in enumerate(lines, 1):
            for family, rx in SECRET_SHAPES:
                if rx.search(line):
                    hits.append(f"{rel}:{number}: {family}")
                    key_id_seen = key_id_seen or family == "AWS access key id"
        if key_id_seen:
            for number, line in enumerate(lines, 1):
                for run in _AWS_SECRET_RUN.findall(line):
                    if any(c.islower() for c in run) and any(c.isupper() for c in run) and any(c.isdigit() for c in run):
                        hits.append(f"{rel}:{number}: AWS secret access key (paired with the key id in this file)")
    if hits:
        raise ExportError("credential-shaped literals in the exported tree — GitHub's push protection would decline the "
                          "push; assemble such test fixtures at runtime instead:\n  " + "\n  ".join(hits))


def check_target_remotes(target: Path) -> None:
    """Refuse a target inside a git clone that has an internal remote."""
    result = subprocess.run(["git", "-C", str(target), "remote", "-v"], capture_output=True, text=True)
    if result.returncode != 0:
        return  # not a git repository: nothing to refuse
    markers = _internal_remote_markers()
    for line in result.stdout.splitlines():
        lowered = line.lower()
        for marker in markers:
            if marker in lowered:
                raise ExportError(
                    f"refusing to export into a clone with an internal remote ({line.split()[0]}: {marker}); "
                    "the public export must never sit next to an internal push target"
                )


def _is_clone(target: Path) -> bool:
    """True when ``target`` itself is the top level of a git repository (a
    worktree's ``.git`` file counts; a directory *inside* some other repository does not)."""
    result = subprocess.run(["git", "-C", str(target), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return result.returncode == 0 and Path(result.stdout.strip()).resolve() == Path(target).resolve()


def configure_public_identity(target: Path) -> tuple[str, str] | None:
    """Write ``PUBLIC_GIT_NAME``/``PUBLIC_GIT_EMAIL`` into the clone's own config.

    A plain ``git commit`` in the public clone then carries the maintainer's
    GitHub identity whatever the desk's global config says. Returns the identity
    written, or ``None`` when ``target`` is not a git clone (a plain directory
    export has nothing to commit).
    """
    if not _is_clone(target):
        return None
    for key, value in (("user.name", PUBLIC_GIT_NAME), ("user.email", PUBLIC_GIT_EMAIL)):
        subprocess.run(["git", "-C", str(target), "config", key, value], check=True, capture_output=True, text=True)
    return PUBLIC_GIT_NAME, PUBLIC_GIT_EMAIL


def effective_identity(clone: Path) -> tuple[str, str]:
    """``(name, email)`` a commit made in ``clone`` would carry: the clone's config,
    the desk's global config, or the ``GIT_AUTHOR_*`` environment when set."""
    values: list[str] = []
    for var, key in (("GIT_AUTHOR_NAME", "user.name"), ("GIT_AUTHOR_EMAIL", "user.email")):
        value = os.environ.get(var) or ""
        if not value:
            result = subprocess.run(["git", "-C", str(clone), "config", "--get", key], capture_output=True, text=True)
            value = result.stdout.strip() if result.returncode == 0 else ""
        values.append(value)
    return values[0], values[1]


def unpublished_commits(clone: Path) -> list[tuple[str, str, str]]:
    """``(short sha, author email, committer email)`` of every commit a push would
    publish: ``@{upstream}..HEAD`` when the branch tracks one, else all of HEAD's
    history (a branch never pushed publishes everything)."""
    upstream = subprocess.run(["git", "-C", str(clone), "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
                              capture_output=True, text=True)
    rev = f"{upstream.stdout.strip()}..HEAD" if upstream.returncode == 0 and upstream.stdout.strip() else "HEAD"
    result = subprocess.run(["git", "-C", str(clone), "-P", "log", "--format=%h%x00%ae%x00%ce", rev], capture_output=True, text=True)
    if result.returncode != 0:
        return []  # no commit yet: the first export
    return [tuple(line.split("\0", 2)) for line in result.stdout.splitlines() if line.count("\0") == 2]  # type: ignore[misc]


def check_public_identity(clone: Path) -> None:
    """Refuse a clone that would publish a commit under anything but the public identity.

    Two things are checked: the identity the *next* commit would carry (what the
    operator is about to ``git commit`` with) and every commit already made but
    not yet pushed. The internal address, a ``noreply`` placeholder or a desk
    default all fail — the fix is ``export_public.py --out <clone>`` (which
    re-pins the config) and, for a wrong commit, amending it before it is pushed.
    """
    name, email = effective_identity(clone)
    if email != PUBLIC_GIT_EMAIL or name != PUBLIC_GIT_NAME:
        raise ExportError(
            f"the next commit in {clone} would be attributed to {name or '?'} <{email or '?'}>, "
            f"not {PUBLIC_GIT_NAME} <{PUBLIC_GIT_EMAIL}>; re-run export_public.py --out on the clone "
            "(it pins the identity in the clone's config)"
        )
    wrong = [(sha, author, committer) for sha, author, committer in unpublished_commits(clone)
             if author != PUBLIC_GIT_EMAIL or committer != PUBLIC_GIT_EMAIL]
    if wrong:
        listed = ", ".join(f"{sha} ({author} / {committer})" for sha, author, committer in wrong[:5])
        raise ExportError(
            f"{len(wrong)} unpushed commit(s) in {clone} carry an identity other than {PUBLIC_GIT_EMAIL}: {listed}; "
            "amend them (git commit --amend --reset-author, with the pinned config) before pushing — or, if they are "
            "already on the public remote, set the branch's upstream (git branch --set-upstream-to=origin/main) "
            "so pushed history is not counted"
        )


def export(target: Path, *, force: bool = False) -> dict[str, object]:
    target = target.resolve()
    if target == REPO_ROOT or REPO_ROOT in target.parents:
        raise ExportError("the export target must be outside the repository")
    target.mkdir(parents=True, exist_ok=True)
    check_target_remotes(target)
    existing = [p for p in target.iterdir() if p.name != ".git"]
    if existing:
        if not force:
            raise ExportError(f"target {target} is not empty (use --force to replace its contents, .git kept)")
        for path in existing:
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    written = copy_public_set(target)
    dropped = prune_mods_readme(target)
    stripped = strip_internal_only_regions(target)
    check_excluded_mods_absent(target)
    scan_export(target)
    scan_secret_shapes(target)
    identity = configure_public_identity(target)
    return {"target": str(target), "paths": written, "readmeRowsDropped": dropped, "internalRegionsStripped": stripped,
            "identity": f"{identity[0]} <{identity[1]}>" if identity else None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--out", default=None, help="write the export into this directory (a public clone)")
    group.add_argument("--check", action="store_true", help="export to a temporary directory, scan, delete")
    parser.add_argument("--force", action="store_true", help="with --out: replace the target's contents (keeps .git)")
    parser.add_argument("--keep", default=None, help="with --check: keep the tree at this path to inspect")
    args = parser.parse_args(argv)

    try:
        if args.check:
            if args.keep:
                report = export(Path(args.keep), force=args.force)
            else:
                with tempfile.TemporaryDirectory(prefix="floofycrew-public-") as tmp:
                    report = export(Path(tmp) / "export")
            print(f"OK: public export is clean ({len(report['paths'])} top-level paths, "
                  f"{report['readmeRowsDropped']} mods/README.md row(s) dropped)")
            if args.keep:
                print(f"kept at {report['target']}")
            return 0
        report = export(Path(args.out), force=args.force)
        print(f"OK: exported to {report['target']} and the full-tree scan is clean")
        if report["identity"]:
            print(f"commits there are attributed to {report['identity']} (pinned in the clone's config)")
        print("next: run the pre-push review, then inspect `git status` there and push yourself")
        print(f"  python packaging/internal/review_public_export.py --clone {report['target']}")
        return 0
    except ExportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
