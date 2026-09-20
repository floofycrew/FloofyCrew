#!/usr/bin/env python3
"""Measure how much of the two editions' release trees is byte-identical (task 9.3; Requirement 10.4).

FloofyCrew ships one codebase on two tracks (design DR-3): the shared core, the
Loader app, the SPA host, the registry client and the docs are the same files on
both; only the edition adapter differs. Requirement 10.4 asks for that reuse to be
*measured and reported*, with a target of at least 80 % of shipped files
byte-identical. This script builds the two release trees exactly as the packaging
scripts do — the zipapp's contents (``scripts/build_zipapp.py``) and the Loader
app directory (``scripts/build_loader_app.py``) for ``--edition toolbox`` and
``--edition public`` — into temporary directories, hashes every shipped file, and
reports::

    identical files / total files (percent), identical bytes / total bytes (percent)
    the files that differ, grouped: adapter modules, edition stamps, other

The denominator is the union of shipped paths (a file present in one tree only
counts as differing). Every differing file must be an edition adapter module
(``floofy_edition_*/**``, which includes the adapter's ``registry.json`` — the
registry endpoints and pinned keys) or an edition stamp; anything else differing
is a bug in the packaging (the core must not fork per edition) and fails the
report, as does a percentage below ``--min-percent`` (default 80). Exit status:
0 ok, 1 below target or an unexpected difference, 2 usage.

Standard library only; CI runs it (``.github/workflows/ci.yml`` job ``two-track``)
and ``floofy-core/tests/test_two_track.py`` asserts the same numbers.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import build_loader_app  # noqa: E402
import build_zipapp  # noqa: E402

EDITIONS = {"internal": "toolbox", "external": "public"}
#: Paths allowed to differ between the editions' trees (fnmatch against the shipped path).
ALLOWED_DIFFERENCES: dict[str, tuple[str, ...]] = {
    "adapter modules": ("zipapp/floofy_edition_*/*", "loader-app/floofy_edition_*/*"),
    "edition stamps": ("loader-app/EDITION", "loader-app/edition.json", "zipapp/EDITION"),
}
DEFAULT_MIN_PERCENT = 80.0

__all__ = ["IdentityReport", "build_tree", "measure", "shipped_files"]


@dataclass
class IdentityReport:
    files: dict[str, dict[str, tuple[str, int]]]  # edition -> path -> (sha256, size)
    identical: list[str] = field(default_factory=list)
    differing: dict[str, list[str]] = field(default_factory=dict)  # group -> paths
    min_percent: float = DEFAULT_MIN_PERCENT

    @property
    def union(self) -> list[str]:
        return sorted({p for tree in self.files.values() for p in tree})

    @property
    def total_files(self) -> int:
        return len(self.union)

    @property
    def identical_files(self) -> int:
        return len(self.identical)

    def _size(self, path: str) -> int:
        for tree in self.files.values():
            if path in tree:
                return tree[path][1]
        return 0

    @property
    def total_bytes(self) -> int:
        return sum(self._size(p) for p in self.union)

    @property
    def identical_bytes(self) -> int:
        return sum(self._size(p) for p in self.identical)

    @property
    def file_percent(self) -> float:
        return 100.0 * self.identical_files / self.total_files if self.total_files else 0.0

    @property
    def byte_percent(self) -> float:
        return 100.0 * self.identical_bytes / self.total_bytes if self.total_bytes else 0.0

    @property
    def unexpected(self) -> list[str]:
        return list(self.differing.get("other", []))

    @property
    def ok(self) -> bool:
        return self.file_percent >= self.min_percent and not self.unexpected

    def to_dict(self) -> dict:
        return {
            "editions": sorted(self.files),
            "totalFiles": self.total_files,
            "identicalFiles": self.identical_files,
            "filePercent": round(self.file_percent, 2),
            "totalBytes": self.total_bytes,
            "identicalBytes": self.identical_bytes,
            "bytePercent": round(self.byte_percent, 2),
            "minPercent": self.min_percent,
            "differing": {group: list(paths) for group, paths in self.differing.items()},
            "perEdition": {edition: {"files": len(tree), "bytes": sum(size for _, size in tree.values())} for edition, tree in self.files.items()},
            "ok": self.ok,
        }

    def render(self) -> str:
        lines = [
            "Artifact byte-identity across editions (Requirement 10.4)",
            "",
            f"  trees: {', '.join(f'{e} ({len(t)} files)' for e, t in sorted(self.files.items()))}",
            f"  identical files: {self.identical_files}/{self.total_files} = {self.file_percent:.1f} % (target >= {self.min_percent:.0f} %)",
            f"  identical bytes: {self.identical_bytes}/{self.total_bytes} = {self.byte_percent:.1f} %",
            "",
            "  differing files:",
        ]
        for group, paths in self.differing.items():
            lines.append(f"    {group} ({len(paths)}):")
            lines.extend(f"      {p}" for p in paths)
        if not any(self.differing.values()):
            lines.append("    none")
        lines.append("")
        lines.append("OK: the shared core, Loader app and SPA host ship byte-identical on both editions" if self.ok else ("FAIL: unexpected differences outside the adapters: " + ", ".join(self.unexpected) if self.unexpected else f"FAIL: {self.file_percent:.1f} % is below the {self.min_percent:.0f} % target"))
        return "\n".join(lines)


def build_tree(edition: str, out: Path) -> Path:
    """Write the edition's release tree under ``out``: ``zipapp/`` (the unpacked ``floofy.pyz``) and ``loader-app/`` (the app directory)."""
    adapter = EDITIONS[edition]
    out.mkdir(parents=True, exist_ok=True)
    payload = build_zipapp.build_bytes([adapter], "/usr/bin/env python3.12")
    archive_bytes = payload[payload.index(b"\n") + 1 :]  # strip the shebang line
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        archive.extractall(out / "zipapp")
    build_loader_app.build(out / "loader-app", [adapter])
    return out


def shipped_files(tree: Path) -> dict[str, tuple[str, int]]:
    """``{relative path: (sha256, size)}`` for every file under ``tree``."""
    result: dict[str, tuple[str, int]] = {}
    for path in sorted(p for p in tree.rglob("*") if p.is_file()):
        data = path.read_bytes()
        result[path.relative_to(tree).as_posix()] = (hashlib.sha256(data).hexdigest(), len(data))
    return result


def _group(path: str) -> str:
    for group, patterns in ALLOWED_DIFFERENCES.items():
        if any(fnmatch.fnmatch(path, pattern) for pattern in patterns):
            return group
    return "other"


def measure(min_percent: float = DEFAULT_MIN_PERCENT, keep: Path | None = None) -> IdentityReport:
    """Build both trees (under ``keep`` when given, else a temporary directory) and compare them."""
    trees: dict[str, dict[str, tuple[str, int]]] = {}
    if keep is not None:
        for edition in EDITIONS:
            trees[edition] = shipped_files(build_tree(edition, keep / edition))
    else:
        with tempfile.TemporaryDirectory(prefix="floofy-identity-") as scratch:
            for edition in EDITIONS:
                trees[edition] = shipped_files(build_tree(edition, Path(scratch) / edition))
    report = IdentityReport(trees, min_percent=min_percent)
    internal, external = trees["internal"], trees["external"]
    for path in report.union:
        if path in internal and path in external and internal[path][0] == external[path][0]:
            report.identical.append(path)
        else:
            report.differing.setdefault(_group(path), []).append(path)
    report.differing = {group: report.differing[group] for group in (*ALLOWED_DIFFERENCES, "other") if group in report.differing}
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min-percent", type=float, default=DEFAULT_MIN_PERCENT, help=f"fail below this share of byte-identical files (default {DEFAULT_MIN_PERCENT:.0f})")
    parser.add_argument("--keep", default=None, metavar="DIR", help="build the two trees under DIR instead of a temporary directory (for inspection)")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    args = parser.parse_args(argv)
    report = measure(args.min_percent, Path(args.keep) if args.keep else None)
    print(json.dumps(report.to_dict(), indent=2) if args.json else report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
