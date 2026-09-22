"""Safe handling of mod archives (Requirement 1.8).

A mod is a plain directory or a ``.zip`` / ``.tar.gz`` / ``.tgz`` of one. Before
anything is extracted the archive *listing* is inspected and the whole archive is
rejected if any entry is a symlink or hard link, an absolute path, contains a
``..`` segment, uses backslashes or is not a regular file or directory. Only a
listing that passes is extracted, into a fresh temporary directory, and the
extracted tree is re-checked for symlinks afterwards.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import tarfile
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator

__all__ = [
    "ArchiveError",
    "SUPPORTED_ARCHIVE_SUFFIXES",
    "archive_kind",
    "extracted_archive",
    "inspect_archive",
    "is_archive",
    "open_archive_safely",
]

#: Archive file suffixes the validator and the manager accept.
SUPPORTED_ARCHIVE_SUFFIXES: tuple[str, ...] = (".zip", ".tar.gz", ".tgz", ".tar")

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


class ArchiveError(ValueError):
    """The archive is unsafe or unreadable; ``entry`` names the offending member when known."""

    def __init__(self, message: str, entry: str | None = None):
        super().__init__(message if entry is None else f"{message}: {entry!r}")
        self.entry = entry
        self.reason = message


def archive_kind(path: Path) -> str | None:
    """``"zip"``, ``"tar"`` or ``None`` when ``path`` does not look like a supported archive."""
    name = path.name.lower()
    if name.endswith(".zip"):
        return "zip"
    if name.endswith((".tar.gz", ".tgz", ".tar")):
        return "tar"
    return None


def is_archive(path: Path) -> bool:
    return path.is_file() and archive_kind(path) is not None


def _check_entry_name(name: str) -> None:
    """Reject absolute paths, ``..`` segments, backslashes and NUL bytes (Requirement 1.8)."""
    if not name:
        raise ArchiveError("empty entry name")
    if "\x00" in name:
        raise ArchiveError("NUL byte in entry name", name)
    if "\\" in name:
        raise ArchiveError("backslash in entry name", name)
    if name.startswith("/") or _WINDOWS_DRIVE.match(name):
        raise ArchiveError("absolute path in archive", name)
    if any(part == ".." for part in PurePosixPath(name).parts):
        raise ArchiveError("'..' segment in archive", name)


@dataclass(frozen=True)
class ArchiveEntry:
    name: str
    is_dir: bool
    size: int


def inspect_archive(path: Path) -> list[ArchiveEntry]:
    """Return the listing of ``path`` or raise :class:`ArchiveError` on the first unsafe member.

    Nothing is extracted. Symlinks, hard links, devices, FIFOs, absolute paths and
    ``..`` segments are all refused before any byte of content is read.
    """
    kind = archive_kind(path)
    if kind is None:
        raise ArchiveError(f"unsupported archive type (expected one of {', '.join(SUPPORTED_ARCHIVE_SUFFIXES)})", str(path))
    if kind == "zip":
        return _inspect_zip(path)
    return _inspect_tar(path)


def _inspect_zip(path: Path) -> list[ArchiveEntry]:
    entries: list[ArchiveEntry] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                _check_entry_name(info.filename)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise ArchiveError("symlink in archive", info.filename)
                if mode and mode not in (stat.S_IFREG, stat.S_IFDIR):
                    raise ArchiveError("special file in archive", info.filename)
                entries.append(ArchiveEntry(info.filename, info.is_dir(), info.file_size))
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"not a valid zip file ({exc})", str(path)) from exc
    return entries


def _inspect_tar(path: Path) -> list[ArchiveEntry]:
    entries: list[ArchiveEntry] = []
    try:
        with tarfile.open(path, mode="r:*") as archive:
            for member in archive.getmembers():
                _check_entry_name(member.name)
                if member.issym() or member.islnk():
                    raise ArchiveError("symlink or hard link in archive", member.name)
                if not (member.isfile() or member.isdir()):
                    raise ArchiveError("special file in archive", member.name)
                entries.append(ArchiveEntry(member.name, member.isdir(), member.size))
    except tarfile.TarError as exc:
        raise ArchiveError(f"not a valid tar file ({exc})", str(path)) from exc
    return entries


def open_archive_safely(archive: Path, destination: Path | None = None) -> Path:
    """Inspect, then extract ``archive`` and return the mod root inside the extraction.

    The listing must pass :func:`inspect_archive` first (Requirement 1.8); only then
    is anything written. Extraction goes to ``destination`` (created if missing) or
    to a new temporary directory the caller owns. When the archive wraps a single
    top-level directory that holds ``floofy.json``, that directory is returned;
    otherwise the extraction root is.
    """
    archive = Path(archive)
    inspect_archive(archive)
    root = Path(destination) if destination is not None else Path(tempfile.mkdtemp(prefix="floofy-mod-"))
    root.mkdir(parents=True, exist_ok=True)
    if archive_kind(archive) == "zip":
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(root)
    else:
        with tarfile.open(archive, mode="r:*") as tarred:
            tarred.extractall(root, filter="data")
    _assert_no_symlinks(root)
    return _mod_root(root)


def _assert_no_symlinks(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            if (Path(dirpath) / name).is_symlink():
                raise ArchiveError("symlink appeared after extraction", os.path.relpath(Path(dirpath) / name, root))


def _mod_root(root: Path) -> Path:
    if (root / "floofy.json").is_file():
        return root
    children = [child for child in root.iterdir() if child.name != "__MACOSX"]
    if len(children) == 1 and children[0].is_dir() and (children[0] / "floofy.json").is_file():
        return children[0]
    return root


@contextmanager
def extracted_archive(archive: Path) -> Iterator[Path]:
    """Context manager: safely extract ``archive`` to a temp dir, yield the mod root, clean up."""
    temp_root = Path(tempfile.mkdtemp(prefix="floofy-mod-"))
    try:
        yield open_archive_safely(archive, temp_root)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
