#!/usr/bin/env python3
"""Assert that the edition-neutral parts of the repository carry no internal identifiers.

FloofyCrew ships one codebase to two editions (design DR-3, Requirement 10.2): the
shared core must be publishable as-is, so every hostname, package name, account or
person that only makes sense inside the internal edition has to live in the edition
adapters. This script scans the public-clean directories for a case-insensitive
denylist of such identifiers and fails the build on any hit.

The denylist lives in ``scripts/internal-identifiers.txt`` (one entry per line),
not in this file: the public tree does not carry that file, so the public copy of
this scanner does not itself spell the names it keeps out. Two ways to scan without
the plain-text list beside the script:

* ``--denylist FILE`` reads the plain-text list from another path.
* ``--hashed FILE`` reads a *hashed* list (``scripts/internal-identifiers.sha256``,
  written by ``tools/hash-identifiers.py`` from the plain list, one
  ``<sha256 of the lowercase entry>  <length>`` line per entry): the scanner hashes
  candidate substrings of the text and looks the digests up, so the public tree can
  carry the gate without carrying the names. See :func:`scan_text_hashed` for the
  candidate rule and its one stated limit.

Run with neither flag in a tree without the plain list (a public tree) the scanner
says so and exits 0 — the authoritative plain scan runs where the list lives.

Usage:
    python scripts/check_no_internal_identifiers.py [--root DIR] [--allowlist FILE]
                                                    [--paths P [P ...]] [--list]
                                                    [--denylist FILE | --hashed FILE]

Exit status: 0 clean, 1 hits found, 2 usage/allowlist/list error.

Allowlist (default ``scripts/de-amazon-allow.txt``): one exception per line as
``path:pattern:reason`` — ``path`` is a repository-relative POSIX path (a directory
prefix ending in ``/`` covers its subtree), ``pattern`` is the denylist entry (or
``*`` for every entry), ``reason`` is free text and mandatory. Blank lines and
``#`` comments are ignored. Every allowlist line must name an existing path so
stale exceptions are noticed. In hashed mode a named pattern is checked against
the hashed list, so the allowlist may still spell the entries it excuses.

Standard library only, so it runs on any Python 3.12 without a virtualenv.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator, Mapping

#: Directories that must stay free of internal identifiers (repository-relative).
DEFAULT_SCAN_PATHS: tuple[str, ...] = (
    "floofy-core",
    "loader-app",
    "spa-host",
    "registry-tools",
    "docs",
)

#: The denylist's home: one case-insensitive substring per line, ``#`` comments
#: and blank lines ignored, kept as a flat tuple in file order so the unit tests
#: and the allowlists can name entries exactly (the first entry is a hostname
#: fragment, the last an account name). Internal-only: the public tree drops it.
TERMS_FILE = Path(__file__).resolve().parent / "internal-identifiers.txt"


def load_denylist(path: Path = TERMS_FILE) -> tuple[str, ...]:
    """The identifiers in ``path`` in file order; empty when the file is absent (a public tree)."""
    if not path.is_file():
        return ()
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            entries.append(line)
    return tuple(entries)


DENYLIST: tuple[str, ...] = load_denylist()

#: Directory names never descended into.
SKIP_DIRS: frozenset[str] = frozenset(
    {".git", "__pycache__", "node_modules", ".venv", ".scratch", "dist", "build", ".pytest_cache", ".hypothesis"}
)

#: File suffixes treated as binary and skipped without reading.
BINARY_SUFFIXES: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".zip", ".gz", ".br", ".tar", ".whl", ".pyc", ".so", ".pdf"}
)

#: Upstream legal notices are reproduced verbatim, as their licenses require: a
#: NOTICE/COPYING file (or anything under a licenses/ directory, as in a wheel's
#: dist-info) may carry an upstream copyright line naming a company. That is public
#: attribution from a public repository, not an internal identifier, so these files
#: are not scanned — including in the release workflow's ``--allowlist /dev/null``
#: passes, which a path allowlist could not cover. A ``LICENSE`` file in the tree is
#: NOT exempt: those are our own license texts (Apache-2.0, with our copyright
#: line), and a copyright line there must not spell an internal account name (the
#: public export audit found three that did).
def is_legal_notice(path: Path) -> bool:
    stem = path.name.upper()
    if stem.startswith(("NOTICE", "COPYING")):
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


def parse_allowlist(text: str, root: Path | None = None, denylist: "Iterable[str] | HashList" = DENYLIST) -> list[AllowRule]:
    """Parse ``path:pattern:reason`` lines. With ``root`` given, every path must exist.

    ``denylist`` is what a named pattern must belong to: the plain entries, or a
    :class:`HashList` (which answers ``in`` by hashing the pattern).
    """
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
        if pattern != "*" and pattern not in denylist:
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


# --- hashed mode -------------------------------------------------------------------
#
# The public tree carries ``scripts/internal-identifiers.sha256`` instead of the plain
# list: one ``<sha256 of the lowercase entry>  <length>`` line per entry under the
# header below (design "Hashed identifier list"). Scanning against it means hashing
# candidate substrings of every lowercased line and looking the digests up; the hit
# for a match carries the matched text, which *is* the lowercase entry, so both
# modes report the same ``(line, pattern)`` pairs (design Property 1).
#
# Candidates. Hashing every substring of every recorded length over 10 MB of text is
# ~100 M sha256 calls in CPython — minutes, not seconds. Two observations make it
# cheap without losing a match of the identifiers the list protects:
#
# * An identifier (hostname, package, account, person, handle, path) never contains
#   whitespace or one of the BREAK characters (quotes, brackets, ``,;=|\*!?`^&%$``).
#   So an entry without such a character can only occur inside one maximal *run* of
#   non-break characters, and hashing every window of each recorded length within
#   each run finds it wherever plain mode does — exactly, in both directions. Runs
#   are short in code and prose, so this is a few million hashes for a repository,
#   and a run seen before (``self``, ``return``, a repeated URL) is not hashed twice.
# * An entry *with* whitespace (a person's full name, ``first last``) is matched as
#   a *word-aligned span*: the text from the start of one word to the end of a later
#   one whose length is recorded. That finds ``First Last,`` and ``"first last"``
#   but not ``xfirst last`` — the one stated limit, which only touches entries that
#   are not identifiers in the first place. :func:`hash_list` refuses an entry
#   hashed mode could never find: one with a BREAK character, or with whitespace
#   that does not start and end with a word character.
#
# The design's candidate rule ("every run of [a-z0-9._-]{4,} and every substring of
# each recorded length") is the special case of the first point for its alphabet;
# runs here admit every character an identifier may carry (``/``, ``@``, ``:``, ``#``…).

#: The first line of a generated hashed list (design "Hashed identifier list").
HASH_LIST_HEADER = "# generated by tools/hash-identifiers.py from the overlay's scripts/internal-identifiers.txt; do not edit"

#: Characters that never appear inside an identifier; together with whitespace they
#: delimit the runs whose windows hashed mode hashes.
BREAK_CHARS = "\"'()[]{}<>,;=|\\*!?`^&%$"
_RUN_RE = re.compile("[^\\s" + re.escape(BREAK_CHARS) + "]+")
_BREAK_RE = re.compile("[" + re.escape(BREAK_CHARS) + "]")
_SPACE_RE = re.compile(r"\s")
_WORD_RE = re.compile(r"\w+")
_HASH_LINE_RE = re.compile(r"^([0-9a-fA-F]{64})\s+([0-9]+)$")
_sha256 = hashlib.sha256

#: How many runs a scan remembers (run -> its matches) so a repeated run is hashed
#: once; bounded so a tree of unique tokens cannot grow the cache without limit.
_RUN_CACHE_LIMIT = 300_000


class HashListError(ValueError):
    """Raised for a malformed hashed identifier list."""


def hashed_mode_limit(entry: str) -> str | None:
    """Why hashed mode could never find ``entry`` (already lowercased), or ``None``."""
    if not entry:
        return "empty entry"
    if len(entry.splitlines()) != 1:
        return "spans more than one line"
    if _BREAK_RE.search(entry) is not None:
        return "contains a break character (" + BREAK_CHARS + "), which hashed mode treats as a separator"
    if _SPACE_RE.search(entry) is None:
        return None  # break-free: found wherever plain mode finds it
    if _WORD_RE.match(entry) and _WORD_RE.match(entry[-1]):
        return None  # a phrase: found where it is word-aligned
    return "contains whitespace and does not start and end with a word character; hashed mode cannot find it"


def hash_list(entries: Iterable[str]) -> Iterator[str]:
    """The lines of a hashed identifier list for ``entries`` (no line terminators).

    The header first, then one ``<sha256 of the lowercase entry>  <length>`` line per
    entry in order, ``length`` being the character count of the lowercase entry.
    Raises ``ValueError`` for an entry hashed mode could never find (see
    :func:`hashed_mode_limit`) so the list is refused loudly rather than guarded
    partially; the message gives the entry's position and length, not its text.
    """
    yield HASH_LIST_HEADER
    for number, entry in enumerate(entries, start=1):
        lowered = entry.lower()
        problem = hashed_mode_limit(lowered)
        if problem is not None:
            raise ValueError(f"entry {number} (length {len(lowered)}): {problem}")
        yield f"{_sha256(lowered.encode('utf-8')).hexdigest()}  {len(lowered)}"


@dataclass(frozen=True)
class HashList:
    """A parsed hashed identifier list.

    ``digests`` maps the raw sha256 digest of a lowercase entry to its position in the
    list (the first, for a duplicate); ``lengths`` are the distinct recorded lengths,
    ascending; ``entries`` keeps ``(hex digest, length)`` in file order. ``pattern in
    hash_list`` hashes ``pattern`` (already lowercase) and answers whether it is an
    entry, which is how the allowlist names entries without the plain list.
    """

    digests: Mapping[bytes, int]
    lengths: tuple[int, ...]
    entries: tuple[tuple[str, int], ...]

    @classmethod
    def parse(cls, lines: Iterable[str] | str) -> HashList:
        """Parse the lines of a hashed list; blank lines and ``#`` comments are ignored."""
        if isinstance(lines, str):
            lines = lines.splitlines()
        digests: dict[bytes, int] = {}
        entries: list[tuple[str, int]] = []
        for number, raw in enumerate(lines, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = _HASH_LINE_RE.match(line)
            if match is None or int(match.group(2)) < 1:
                raise HashListError(f"hashed list line {number}: expected '<sha256> <length>', got {raw!r}")
            hexdigest, length = match.group(1).lower(), int(match.group(2))
            digests.setdefault(bytes.fromhex(hexdigest), len(entries))
            entries.append((hexdigest, length))
        return cls(digests=digests, lengths=tuple(sorted({length for _, length in entries})), entries=tuple(entries))

    def __len__(self) -> int:
        return len(self.entries)

    def __contains__(self, pattern: object) -> bool:
        if not isinstance(pattern, str):
            return False
        index = self.digests.get(_sha256(pattern.encode("utf-8")).digest())
        return index is not None and self.entries[index][1] == len(pattern)

    def lines(self) -> list[str]:
        """The entry lines as written (``<hex digest>  <length>``), for ``--list``."""
        return [f"{hexdigest}  {length}" for hexdigest, length in self.entries]


def _run_matches(run: str, hash_list: HashList) -> tuple[tuple[int, str], ...]:
    """``(position, entry)`` for every recorded-length window of ``run`` that is an entry."""
    digests = hash_list.digests
    size = len(run)
    found: list[tuple[int, str]] = []
    if run.isascii():
        buf = run.encode("ascii")
        for length in hash_list.lengths:
            if length > size:
                break
            for start in range(size - length + 1):
                window = buf[start:start + length]
                index = digests.get(_sha256(window).digest())
                if index is not None:
                    found.append((index, window.decode("ascii")))
    else:
        for length in hash_list.lengths:
            if length > size:
                break
            for start in range(size - length + 1):
                window = run[start:start + length]
                index = digests.get(_sha256(window.encode("utf-8")).digest())
                if index is not None:
                    found.append((index, window))
    return tuple(found)


def scan_text_hashed(
    rel_path: str,
    text: str,
    hash_list: HashList | Iterable[str],
    _cache: dict[str, tuple[tuple[int, str], ...]] | None = None,
) -> list[Hit]:
    """Return every hashed-list occurrence in ``text``, shaped like :func:`scan_text`.

    ``hash_list`` is a parsed :class:`HashList` or the lines :func:`hash_list` yields
    (a generated ``.sha256`` file's content). A hit's ``pattern`` is the matched text,
    which is the lowercase entry, so ``(line, pattern)`` pairs equal plain mode's for
    every entry without whitespace (Property 1) and for a whitespace-carrying entry
    wherever it is word-aligned (see the module comment on hashed mode). Hits on one
    line come in list order, as plain mode's do. ``_cache`` lets one scan reuse the
    per-run results across files.
    """
    if not isinstance(hash_list, HashList):
        hash_list = HashList.parse(hash_list)
    if not hash_list.lengths:
        return []
    lengths = set(hash_list.lengths)
    shortest, longest = hash_list.lengths[0], hash_list.lengths[-1]
    digests = hash_list.digests
    cache = _cache if _cache is not None else {}
    hits: list[Hit] = []
    for number, line in enumerate(text.splitlines(), start=1):
        lowered = line.lower()
        if len(lowered) < shortest:
            continue
        found: set[tuple[int, str]] = set()
        for match in _RUN_RE.finditer(lowered):
            run = match.group()
            if len(run) < shortest:
                continue
            matches = cache.get(run)
            if matches is None:
                matches = _run_matches(run, hash_list)
                if len(cache) < _RUN_CACHE_LIMIT:
                    cache[run] = matches
            if matches:
                found.update(matches)
        # Word-aligned spans for entries that carry whitespace (a person's name): from
        # the start of one word to the end of a later one, when that length is listed.
        words = [(m.start(), m.end()) for m in _WORD_RE.finditer(lowered)]
        for first in range(len(words) - 1):
            start = words[first][0]
            for later in range(first + 1, len(words)):
                length = words[later][1] - start
                if length > longest:
                    break
                if length in lengths:
                    span = lowered[start:start + length]
                    index = digests.get(_sha256(span.encode("utf-8")).digest())
                    if index is not None:
                        found.add((index, span))
        if found:
            stripped = line.strip()
            for _, pattern in sorted(found):
                hits.append(Hit(path=rel_path, line=number, pattern=pattern, text=stripped))
    return hits


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None  # binary content without a known suffix


def _rel_path(root: Path, path: Path) -> str:
    return PurePosixPath(path.relative_to(root).as_posix()).as_posix()


# Hashed scans of a whole tree may run on several processes (``scan(..., jobs=N)``):
# the hashed list and a run cache live per worker, set up once by the initializer.
_WORKER_HASH_LIST: HashList | None = None
_WORKER_CACHE: dict[str, tuple[tuple[int, str], ...]] = {}


def _worker_init(hash_list: HashList) -> None:
    global _WORKER_HASH_LIST
    _WORKER_HASH_LIST = hash_list
    _WORKER_CACHE.clear()


def _worker_scan(job: tuple[str, str]) -> list[Hit]:
    root, rel = job
    text = _read_text(Path(root) / rel)
    if text is None or _WORKER_HASH_LIST is None:
        return []
    return scan_text_hashed(rel, text, _WORKER_HASH_LIST, _WORKER_CACHE)


def _scan_hashed_parallel(root: Path, files: list[Path], hash_list: HashList, jobs: int) -> list[list[Hit]]:
    from concurrent.futures import ProcessPoolExecutor

    work = [(str(root), _rel_path(root, path)) for path in files]
    with ProcessPoolExecutor(max_workers=min(jobs, len(files)), initializer=_worker_init, initargs=(hash_list,)) as pool:
        return list(pool.map(_worker_scan, work, chunksize=max(1, len(files) // (jobs * 8))))


def _scan_hashed(root: Path, files: list[Path], hash_list: HashList, jobs: int) -> Iterator[list[Hit]]:
    """Per-file hit lists in ``files`` order, on ``jobs`` processes when that helps."""
    if jobs > 1 and len(files) > 1:
        try:
            yield from _scan_hashed_parallel(root, files, hash_list, jobs)
            return
        except (OSError, ImportError, RuntimeError, ValueError) as exc:  # no process support here: scan serially
            print(f"note: scanning on one process ({exc})", file=sys.stderr)
    cache: dict[str, tuple[tuple[int, str], ...]] = {}
    for path in files:
        text = _read_text(path)
        yield [] if text is None else scan_text_hashed(_rel_path(root, path), text, hash_list, cache)


def scan(
    root: Path,
    scan_paths: Iterable[str] = DEFAULT_SCAN_PATHS,
    allow: Iterable[AllowRule] = (),
    denylist: Iterable[str] = DENYLIST,
    *,
    hash_list: HashList | None = None,
    jobs: int = 1,
) -> list[Hit]:
    """Scan the tree; return the hits not covered by the allowlist.

    With ``hash_list`` given the scan is hashed (``denylist`` is ignored) and may use
    ``jobs`` processes; the result is the same whatever ``jobs`` is.
    """
    allow = list(allow)
    hits: list[Hit] = []
    if hash_list is not None:
        files = list(iter_text_files(root, scan_paths))
        for file_hits in _scan_hashed(root, files, hash_list, jobs):
            hits.extend(hit for hit in file_hits if not any(rule.covers(hit.path, hit.pattern) for rule in allow))
        return hits
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


def _default_jobs() -> int:
    return max(1, min(8, os.cpu_count() or 1))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None, help="repository root (default: parent of scripts/)")
    parser.add_argument("--allowlist", default=None, help="allowlist file (default: scripts/de-amazon-allow.txt)")
    parser.add_argument("--paths", nargs="+", default=None, help="paths to scan instead of the defaults")
    parser.add_argument("--list", action="store_true", help="print the denylist (hashed mode: its digests and lengths) and exit")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--denylist", default=None, metavar="FILE", help="plain identifier list to scan against (default: scripts/internal-identifiers.txt)")
    source.add_argument("--hashed", default=None, metavar="FILE", help="hashed identifier list (scripts/internal-identifiers.sha256) to scan against")
    parser.add_argument("--jobs", type=int, default=None, metavar="N",
                        help="processes for a hashed scan (default: the CPU count, at most 8; plain scans use one)")
    args = parser.parse_args(argv)

    denylist: tuple[str, ...] = DENYLIST
    hashed: HashList | None = None
    if args.hashed:
        hashed_path = Path(args.hashed)
        try:
            hashed = HashList.parse(hashed_path.read_text(encoding="utf-8"))
        except (OSError, HashListError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if not hashed.entries:
            print(f"error: no identifiers in {hashed_path}", file=sys.stderr)
            return 2
    elif args.denylist:
        denylist_path = Path(args.denylist)
        denylist = load_denylist(denylist_path)
        if not denylist:
            print(f"error: no identifiers in {denylist_path}", file=sys.stderr)
            return 2
    elif not DENYLIST:
        print(f"OK: no identifier list at {TERMS_FILE.name} (a public export carries none); "
              "nothing to scan against here — the authoritative scan runs in the source tree before export")
        return 0
    if args.list:
        print("\n".join(hashed.lines() if hashed is not None else denylist))
        return 0

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    allow_path = Path(args.allowlist) if args.allowlist else root / "scripts" / "de-amazon-allow.txt"
    known: Iterable[str] | HashList = hashed if hashed is not None else denylist
    try:
        rules = parse_allowlist(allow_path.read_text(encoding="utf-8"), root, known) if allow_path.exists() else []
    except AllowlistError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    scan_paths = tuple(args.paths) if args.paths else DEFAULT_SCAN_PATHS
    if hashed is not None:
        hits = scan(root, scan_paths, rules, hash_list=hashed, jobs=args.jobs if args.jobs is not None else _default_jobs())
        against = f"; hashed list of {len(hashed)} entries"
    else:
        hits = scan(root, scan_paths, rules, denylist)
        against = ""
    scanned = ", ".join(scan_paths)
    if hits:
        print(f"{len(hits)} internal identifier(s) found in [{scanned}]:")
        for hit in hits:
            print("  " + hit.format())
        print("Move edition-specific values into editions/* or add a justified line to", allow_path.name)
        return 1
    print(f"OK: no internal identifiers in [{scanned}] ({len(rules)} allowlist rule(s){against})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
