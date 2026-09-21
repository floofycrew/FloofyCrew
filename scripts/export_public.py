#!/usr/bin/env python3
"""Build the filtered public (GitHub) export of the FloofyCrew repository.

The repository has one source of truth, hosted internally; the public GitHub
repository receives a FILTERED export, never the tree as-is (design DR-3,
Requirement 10.2). This script is that filter, and the only sanctioned way to
produce a tree for the public remote:

1. **Copy the public set** — an explicit include list (`PUBLIC_SET`). Anything
   not named is out by construction: internal-only directories are never
   consulted, so the exporter needs no exclude list of them.
2. **Drop internal-only mods** (`EXCLUDED_MODS`): mods that patch internal-only
   hosts or ship fan artwork stay out, and their rows are removed from
   `mods/README.md`.
3. **Scan the WHOLE exported tree** with `check_no_internal_identifiers.py`
   (`--paths .`), against `scripts/public-export-allow.txt` — a separate,
   justified allowlist whose every line must name an existing exported path.
   Any other hit fails the export.
4. **Refuse internal remotes**: with `--out` pointing into a git clone, every
   configured remote is checked; an internal host fails the export before a
   single file is written.

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
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNER = REPO_ROOT / "scripts" / "check_no_internal_identifiers.py"
ALLOWLIST = "scripts/public-export-allow.txt"

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
PUBLIC_SET_OPTIONAL: tuple[str, ...] = ("LICENSE", "NOTICE.md", "LICENSES")

#: Mods that must not ship publicly: patches of internal-only hosts, fan artwork.
EXCLUDED_MODS: tuple[str, ...] = ("mochi-pet-zoom-fix", "rimuru-branding")

#: Directory names never copied (caches, build output, environments).
SKIP_NAMES: frozenset[str] = frozenset(
    {".git", "__pycache__", "node_modules", ".venv", ".scratch", "dist", "build",
     ".pytest_cache", ".hypothesis", ".floofy-run", ".builder-mcp", ".cache"}
)

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


def _ignore(directory: str, names: list[str]) -> set[str]:
    ignored = {n for n in names if n in SKIP_NAMES}
    if Path(directory) == REPO_ROOT / "mods":
        ignored.update(n for n in names if n in EXCLUDED_MODS)
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
    markers = tuple(f"[`{mod}/`]" for mod in EXCLUDED_MODS)
    for line in readme.read_text(encoding="utf-8").splitlines(keepends=True):
        if line.lstrip().startswith("|") and any(marker in line for marker in markers):
            dropped += 1
            continue
        kept.append(line)
    readme.write_text("".join(kept), encoding="utf-8")
    return dropped


def check_excluded_mods_absent(target: Path) -> None:
    for mod in EXCLUDED_MODS:
        if (target / "mods" / mod).exists():
            raise ExportError(f"excluded mod was copied: mods/{mod}")
    remaining = sorted(p.name for p in (target / "mods").iterdir() if p.is_dir())
    if not remaining:
        raise ExportError("no mods left in the export; the exclude list ate everything")


def scan_export(target: Path) -> None:
    """Run the identifier scan over the WHOLE exported tree; raise on any hit."""
    allowlist = target / ALLOWLIST
    if not allowlist.exists():
        raise ExportError(f"the exported tree is missing {ALLOWLIST}")
    result = subprocess.run(
        [sys.executable, str(target / "scripts" / "check_no_internal_identifiers.py"),
         "--root", str(target), "--paths", ".", "--allowlist", str(allowlist)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ExportError("the exported tree is NOT clean:\n" + result.stdout + result.stderr)


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
    check_excluded_mods_absent(target)
    scan_export(target)
    return {"target": str(target), "paths": written, "readmeRowsDropped": dropped}


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
        print("next: run the pre-push review, then inspect `git status` there and push yourself")
        print(f"  python packaging/internal/review_public_export.py --clone {report['target']}")
        return 0
    except ExportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
