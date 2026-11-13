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

A second mode compares two directories that are already built — ``--tree A B``,
with ``--names X,Y`` for the labels (default: the directories' basenames). It
answers a different question: do two ways of producing the *same* artifact agree?
The public-first cut-over used it to show that the tree the old export path
produced and the tree the public repository now carries are the same bytes for
the same source. Differences are grouped ``expected`` / ``only in <A>`` /
``only in <B>`` / ``other``; ``--expect PATTERN[=REGEX]`` (repeatable) moves a
path into ``expected``, and with ``=REGEX`` only if the file exists in both trees
and is identical once the lines matching REGEX are removed from both — that is
how a file that may differ *on one stated line* (a vendored tag, a timestamp) is
admitted without admitting the whole file. Exit status is the same contract: 0
when nothing lands in ``other`` and the identical share is at or above
``--min-percent``.

Standard library only; CI runs it (``.github/workflows/ci.yml`` job ``two-track``)
and ``floofy-core/tests/test_two_track.py`` asserts the same numbers.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
import re
import sys
import tempfile
import zipfile
from collections.abc import Sequence
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
#: The group a difference lands in when ``--expect`` admits it (two-tree mode).
EXPECTED_GROUP = "expected"
#: The group that fails the report, in both modes.
OTHER_GROUP = "other"
EDITIONS_TITLE = "Artifact byte-identity across editions (Requirement 10.4)"
EDITIONS_OK_NOTE = "OK: the shared core, Loader app and SPA host ship byte-identical on both editions"

__all__ = ["Expectation", "IdentityReport", "build_tree", "compare_trees", "measure", "shipped_files"]


@dataclass(frozen=True)
class Expectation:
    """One ``--expect PATTERN[=REGEX]``: paths that are allowed to differ between two trees.

    ``pattern`` is fnmatched against the tree-relative POSIX path. Without a
    regex any difference on a matching path is expected, including a file that
    exists in only one tree. With a regex the difference is expected only if the
    file exists in *both* trees and the two are identical once every line
    matching the regex is dropped from both — so the regex states exactly which
    lines may differ, and a change anywhere else still fails.
    """

    pattern: str
    regex: re.Pattern[str] | None = None

    @classmethod
    def parse(cls, spec: str) -> Expectation:
        pattern, separator, regex = spec.partition("=")
        if not pattern:
            raise ValueError(f"--expect needs a path pattern: {spec!r}")
        if separator and not regex:
            raise ValueError(f"--expect {spec!r} has an empty regex; drop the '=' to expect the whole file to differ")
        return cls(pattern, re.compile(regex) if separator else None)


@dataclass
class IdentityReport:
    files: dict[str, dict[str, tuple[str, int]]]  # tree label -> path -> (sha256, size)
    identical: list[str] = field(default_factory=list)
    differing: dict[str, list[str]] = field(default_factory=dict)  # group -> paths
    min_percent: float = DEFAULT_MIN_PERCENT
    title: str = EDITIONS_TITLE
    ok_note: str = EDITIONS_OK_NOTE

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
        return list(self.differing.get(OTHER_GROUP, []))

    @property
    def ok(self) -> bool:
        return self.file_percent >= self.min_percent and not self.unexpected

    def to_dict(self) -> dict:
        return {
            "trees": sorted(self.files),
            "totalFiles": self.total_files,
            "identicalFiles": self.identical_files,
            "filePercent": round(self.file_percent, 2),
            "totalBytes": self.total_bytes,
            "identicalBytes": self.identical_bytes,
            "bytePercent": round(self.byte_percent, 2),
            "minPercent": self.min_percent,
            "differing": {group: list(paths) for group, paths in self.differing.items()},
            "perTree": {label: {"files": len(tree), "bytes": sum(size for _, size in tree.values())} for label, tree in self.files.items()},
            "ok": self.ok,
        }

    def render(self) -> str:
        lines = [
            self.title,
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
        lines.append(self.ok_note if self.ok else ("FAIL: unexpected differences: " + ", ".join(self.unexpected) if self.unexpected else f"FAIL: {self.file_percent:.1f} % is below the {self.min_percent:.0f} % target"))
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
    return OTHER_GROUP


def _without(path: Path, regex: re.Pattern[str]) -> str:
    """The file's text with every line matching ``regex`` removed."""
    text = path.read_bytes().decode("utf-8", errors="surrogateescape")
    return "".join(line for line in text.splitlines(keepends=True) if not regex.search(line))


def compare_trees(
    first: Path,
    second: Path,
    names: tuple[str, str] | None = None,
    expect: Sequence[Expectation] = (),
    min_percent: float = DEFAULT_MIN_PERCENT,
) -> IdentityReport:
    """Compare two already-built directories file by file (the ``--tree`` mode).

    Groups every non-identical path as ``expected`` (an ``--expect`` admitted it),
    ``only in <label>`` (present in one tree only and not expected) or ``other``
    (differing content, or an ``--expect`` regex that did not confine the
    difference). Only ``other`` fails the report.
    """
    labels = names or (first.name, second.name)
    if labels[0] == labels[1]:
        raise ValueError(f"both trees are labelled {labels[0]!r}; pass --names X,Y")
    for root in (first, second):
        if not root.is_dir():
            raise ValueError(f"not a directory: {root}")
    trees = {labels[0]: shipped_files(first), labels[1]: shipped_files(second)}
    report = IdentityReport(
        trees,
        min_percent=min_percent,
        title=f"Artifact identity between two trees: {labels[0]} vs {labels[1]}",
        ok_note="OK: the two trees are identical except where expected",
    )
    left, right = trees[labels[0]], trees[labels[1]]
    for path in report.union:
        in_left, in_right = path in left, path in right
        if in_left and in_right and left[path][0] == right[path][0]:
            report.identical.append(path)
            continue
        group = None
        for expectation in expect:
            if not fnmatch.fnmatch(path, expectation.pattern):
                continue
            if expectation.regex is None:
                group = EXPECTED_GROUP
            elif in_left and in_right and _without(first / path, expectation.regex) == _without(second / path, expectation.regex):
                group = EXPECTED_GROUP
            else:
                group = OTHER_GROUP  # the stated lines did not account for the difference
            break
        if group is None:
            group = OTHER_GROUP if in_left and in_right else f"only in {labels[0] if in_left else labels[1]}"
        report.differing.setdefault(group, []).append(path)
    order = (EXPECTED_GROUP, f"only in {labels[0]}", f"only in {labels[1]}", OTHER_GROUP)
    report.differing = {group: report.differing[group] for group in order if group in report.differing}
    return report


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
    report.differing = {group: report.differing[group] for group in (*ALLOWED_DIFFERENCES, OTHER_GROUP) if group in report.differing}
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min-percent", type=float, default=DEFAULT_MIN_PERCENT, help=f"fail below this share of byte-identical files (default {DEFAULT_MIN_PERCENT:.0f})")
    parser.add_argument("--keep", default=None, metavar="DIR", help="editions mode: build the two trees under DIR instead of a temporary directory (for inspection)")
    parser.add_argument("--tree", nargs=2, default=None, metavar=("A", "B"), help="compare these two directories instead of building the two editions' release trees")
    parser.add_argument("--names", default=None, metavar="X,Y", help="--tree mode: the two trees' labels (default: their directory names)")
    parser.add_argument("--expect", action="append", default=[], metavar="PATTERN[=REGEX]", help="--tree mode, repeatable: a differing or one-sided path fnmatching PATTERN is expected; with =REGEX only if it exists in both trees and is identical once the lines matching REGEX are removed")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    args = parser.parse_args(argv)
    if args.tree:
        if args.keep:
            parser.error("--keep belongs to the editions mode; --tree compares directories that already exist")
        names = None
        if args.names:
            names = tuple(args.names.split(","))
            if len(names) != 2:
                parser.error(f"--names takes two comma-separated labels, got {args.names!r}")
        try:
            expect = [Expectation.parse(spec) for spec in args.expect]
            report = compare_trees(Path(args.tree[0]), Path(args.tree[1]), names, expect, args.min_percent)
        except (ValueError, re.error) as error:
            parser.error(str(error))
    else:
        for flag in ("names", "expect"):
            if getattr(args, flag):
                parser.error(f"--{flag} only applies with --tree")
        report = measure(args.min_percent, Path(args.keep) if args.keep else None)

    print(json.dumps(report.to_dict(), indent=2) if args.json else report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
