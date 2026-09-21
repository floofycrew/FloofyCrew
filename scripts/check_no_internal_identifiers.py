#!/usr/bin/env python3
"""Assert that the edition-neutral parts of the repository carry no internal identifiers.

FloofyCrew ships one codebase to two editions (design DR-3, Requirement 10.2): the
shared core must be publishable as-is, so every hostname, package name, account or
person that only makes sense inside the internal edition has to live in the edition
adapters. This script scans the public-clean directories for a case-insensitive
denylist of such identifiers and fails the build on any hit.

Usage:
    python scripts/check_no_internal_identifiers.py [--root DIR] [--allowlist FILE]
                                                    [--paths P [P ...]] [--list]

Exit status: 0 clean, 1 hits found, 2 usage/allowlist error.

Allowlist (default ``scripts/de-amazon-allow.txt``): one exception per line as
``path:pattern:reason`` — ``path`` is a repository-relative POSIX path (a directory
prefix ending in ``/`` covers its subtree), ``pattern`` is the denylist entry (or
``*`` for every entry), ``reason`` is free text and mandatory. Blank lines and
``#`` comments are ignored. Every allowlist line must name an existing path so
stale exceptions are noticed.

Standard library only, so it runs on any Python 3.12 without a virtualenv.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator

#: Directories that must stay free of internal identifiers (repository-relative).
DEFAULT_SCAN_PATHS: tuple[str, ...] = (
    "floofy-core",
    "loader-app",
    "spa-host",
    "registry-tools",
    "docs",
)

#: Case-insensitive substrings that only make sense inside the internal edition.
#: Kept as a flat tuple so the unit tests and the allowlist can name entries exactly.
DENYLIST: tuple[str, ...] = (
    # hostnames and URL fragments
    "amazon.com",
    "amazon.dev",
    "a2z.com",
    "aws.dev",
    "code.amazon.com",
    "w.amazon.com",
    "issues.amazon.com",
    "t.corp",
    "buildertoolbox",
    # identity and credential tooling
    "midway",
    "mwinit",
    "isengard",
    "mcscli",
    # internal build and distribution systems
    "brazil",
    "toolbox",
    "gitfarm",
    "aim install",
    # internal package and repository names
    "o3r-",
    "kirocrewamazoninternal",
    "kirocrew_amazon",
    "kirocrewmirror",
    "kirocrewappregistry",
    "kirocrewcommunityappregistry",
    "gardnerkirocrewappregistry",
    "@amzn/",
    # people and accounts
    "liyent",
)

#: Directory names never descended into.
SKIP_DIRS: frozenset[str] = frozenset(
    {".git", "__pycache__", "node_modules", ".venv", ".scratch", "dist", "build", ".pytest_cache", ".hypothesis"}
)

#: File suffixes treated as binary and skipped without reading.
BINARY_SUFFIXES: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".zip", ".gz", ".br", ".tar", ".whl", ".pyc", ".so", ".pdf"}
)

#: Legal notices are reproduced verbatim, as their licenses require: a LICENSE/NOTICE
#: file (or anything under a licenses/ directory, as in a wheel's dist-info) may carry
#: an upstream copyright line naming a company. That is public attribution from a
#: public repository, not an internal identifier, so these files are not scanned —
#: including in the release workflow's ``--allowlist /dev/null`` passes, which a
#: path allowlist could not cover.
def is_legal_notice(path: Path) -> bool:
    stem = path.name.upper()
    if stem.startswith(("LICENSE", "NOTICE", "COPYING")):
        return True
    return any(part.lower() in ("licenses", "license-files") for part in path.parts[:-1])


@dataclass(frozen=True)
class Hit:
    """One occurrence of a denylisted identifier."""

    path: str  # repository-relative POSIX path
    line: int  # 1-based
    pattern: str
    text: str  # the offending line, stripped

    def format(self) -> str:
        return f"{self.path}:{self.line}: {self.pattern!r} in: {self.text[:160]}"


@dataclass(frozen=True)
class AllowRule:
    path: str
    pattern: str  # a DENYLIST entry (lower-case) or "*"
    reason: str

    def covers(self, rel_path: str, pattern: str) -> bool:
        if self.pattern != "*" and self.pattern != pattern:
            return False
        if self.path.endswith("/"):
            return rel_path.startswith(self.path)
        return rel_path == self.path


class AllowlistError(ValueError):
    """Raised for a malformed or stale allowlist line."""


def parse_allowlist(text: str, root: Path | None = None) -> list[AllowRule]:
    """Parse ``path:pattern:reason`` lines. With ``root`` given, every path must exist."""
    rules: list[AllowRule] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":", 2)
        if len(parts) != 3 or not all(p.strip() for p in parts):
            raise AllowlistError(f"allowlist line {number}: expected 'path:pattern:reason', got {raw!r}")
        path, pattern, reason = (p.strip() for p in parts)
        pattern = pattern.lower()
        if pattern != "*" and pattern not in DENYLIST:
            raise AllowlistError(f"allowlist line {number}: {pattern!r} is not a denylist entry")
        if root is not None and not (root / path).exists():
            raise AllowlistError(f"allowlist line {number}: path {path!r} does not exist (stale exception)")
        rules.append(AllowRule(path=path, pattern=pattern, reason=reason))
    return rules


def iter_text_files(root: Path, scan_paths: Iterable[str]) -> Iterator[Path]:
    """Yield every non-binary file under the scan paths, deterministic order."""
    for rel in scan_paths:
        base = root / rel
        if base.is_file():
            yield base
            continue
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                path = Path(dirpath) / name
                if path.suffix.lower() in BINARY_SUFFIXES or path.is_symlink() or is_legal_notice(path):
                    continue
                yield path


def scan_text(rel_path: str, text: str, denylist: Iterable[str] = DENYLIST) -> list[Hit]:
    """Return every denylist occurrence in ``text`` (one hit per pattern per line)."""
    hits: list[Hit] = []
    patterns = [p.lower() for p in denylist]
    for number, line in enumerate(text.splitlines(), start=1):
        lowered = line.lower()
        for pattern in patterns:
            if pattern in lowered:
                hits.append(Hit(path=rel_path, line=number, pattern=pattern, text=line.strip()))
    return hits


def scan(
    root: Path,
    scan_paths: Iterable[str] = DEFAULT_SCAN_PATHS,
    allow: Iterable[AllowRule] = (),
    denylist: Iterable[str] = DENYLIST,
) -> list[Hit]:
    """Scan the tree; return the hits not covered by the allowlist."""
    allow = list(allow)
    hits: list[Hit] = []
    for path in iter_text_files(root, scan_paths):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # binary content without a known suffix
        rel = PurePosixPath(path.relative_to(root).as_posix()).as_posix()
        for hit in scan_text(rel, text, denylist):
            if not any(rule.covers(rel, hit.pattern) for rule in allow):
                hits.append(hit)
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None, help="repository root (default: parent of scripts/)")
    parser.add_argument("--allowlist", default=None, help="allowlist file (default: scripts/de-amazon-allow.txt)")
    parser.add_argument("--paths", nargs="+", default=None, help="paths to scan instead of the defaults")
    parser.add_argument("--list", action="store_true", help="print the denylist and exit")
    args = parser.parse_args(argv)

    if args.list:
        print("\n".join(DENYLIST))
        return 0

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    allow_path = Path(args.allowlist) if args.allowlist else root / "scripts" / "de-amazon-allow.txt"
    try:
        rules = parse_allowlist(allow_path.read_text(encoding="utf-8"), root) if allow_path.exists() else []
    except AllowlistError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    scan_paths = tuple(args.paths) if args.paths else DEFAULT_SCAN_PATHS
    hits = scan(root, scan_paths, rules)
    scanned = ", ".join(scan_paths)
    if hits:
        print(f"{len(hits)} internal identifier(s) found in [{scanned}]:")
        for hit in hits:
            print("  " + hit.format())
        print("Move edition-specific values into editions/* or add a justified line to", allow_path.name)
        return 1
    print(f"OK: no internal identifiers in [{scanned}] ({len(rules)} allowlist rule(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
