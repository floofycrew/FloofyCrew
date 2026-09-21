"""Read and rewrite Electron ``asar`` archives (the desktop shell's ``app.asar``), standard library only.

The Electron shell of a desktop payload ships its main-process JavaScript inside
``Contents/Resources/app.asar`` (macOS) or ``resources/app.asar`` (Linux,
Windows). A ``patch`` part whose ``target`` is ``electron:<member>`` rewrites one
member of that archive (Requirement 5.11); everything else about the patch — the
whole-archive ``.floofybak`` backup, the deployment-manifest hashes, drift
classification, restore — is the ordinary file path of the Patcher, applied to
``app.asar`` as one file. This module is only the container format.

The format (``@electron/asar``)::

    uint32 4 | uint32 headerPickleSize | uint32 headerPayloadSize | uint32 jsonLength | json … pad to 4 | file data …

Offsets in the header JSON are decimal strings relative to the first data byte
(``8 + headerPickleSize``). An entry is a directory (``{"files": {…}}``), a file
(``size``, ``offset``, optional ``executable``, optional ``integrity``), an
**unpacked** file (``"unpacked": true`` — the bytes live beside the archive in
``app.asar.unpacked/``, never inside it) or a symlink (``"link"``). Rewriting a
member changes its size, so every later offset moves: :meth:`AsarArchive.with_member`
re-lays the data blob in the original offset order and recomputes the changed
member's ``integrity`` (SHA-256 over the whole member and per ``blockSize`` block)
when the archive carries integrity records. Nothing else in the header is touched.

Electron validates those integrity records only when the fuse
``EnableEmbeddedAsarIntegrityValidation`` is on; the Patcher reads the fuse wire
(:func:`read_fuses`) and refuses to rewrite an archive the shell would then refuse
to load. The archive is codesign-sealed on macOS: rewriting it is a deliberate,
consented, reversible change to a signed bundle (design DR-5; see
``docs/patching.md`` → "Electron shell targets").
"""
from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

__all__ = [
    "ASAR_FUSE_SENTINEL",
    "AsarArchive",
    "AsarError",
    "FUSE_EMBEDDED_ASAR_INTEGRITY",
    "FUSE_ONLY_LOAD_APP_FROM_ASAR",
    "Fuses",
    "read_fuses",
]

_UINT32 = struct.Struct("<I")
_HEADER_PREFIX = struct.Struct("<IIII")
_DEFAULT_BLOCK_SIZE = 4 * 1024 * 1024

#: ``@electron/fuses`` sentinel: the fuse wire follows it in the Electron binary.
ASAR_FUSE_SENTINEL = b"dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX"
#: FuseV1Options indexes (``@electron/fuses``): position in the wire after the sentinel + version + length bytes.
FUSE_RUN_AS_NODE = 0
FUSE_COOKIE_ENCRYPTION = 1
FUSE_NODE_OPTIONS = 2
FUSE_NODE_CLI_INSPECT = 3
FUSE_EMBEDDED_ASAR_INTEGRITY = 4
FUSE_ONLY_LOAD_APP_FROM_ASAR = 5
FUSE_BROWSER_PROCESS_V8_SNAPSHOT = 6
FUSE_FILE_PROTOCOL_EXTRA_PRIVILEGES = 7


class AsarError(ValueError):
    """The bytes are not an asar archive, or the member cannot be read or rewritten."""


def _read_u32(blob: bytes, offset: int) -> int:
    if offset + 4 > len(blob):
        raise AsarError(f"truncated header at byte {offset}")
    return _UINT32.unpack_from(blob, offset)[0]


def _integrity(content: bytes, block_size: int) -> dict[str, Any]:
    blocks = [hashlib.sha256(content[i : i + block_size]).hexdigest() for i in range(0, len(content), block_size)] or [hashlib.sha256(b"").hexdigest()]
    return {"algorithm": "SHA256", "hash": hashlib.sha256(content).hexdigest(), "blockSize": block_size, "blocks": blocks}


@dataclass(frozen=True)
class AsarArchive:
    """A parsed archive: the header document plus the data blob it indexes."""

    header: dict[str, Any]
    data: bytes
    #: Where the data blob started in the original bytes (diagnostics only).
    data_offset: int = 0

    # -- parsing / packing ------------------------------------------------------------

    @classmethod
    def parse(cls, blob: bytes) -> "AsarArchive":
        if len(blob) < 16:
            raise AsarError("too short to be an asar archive")
        size_field, header_size, payload_size, json_length = _HEADER_PREFIX.unpack_from(blob, 0)
        if size_field != 4:
            raise AsarError(f"not an asar archive (size pickle says {size_field}, expected 4)")
        data_offset = 8 + header_size
        if payload_size + 4 != header_size or json_length > payload_size or data_offset > len(blob):
            raise AsarError("asar header sizes are inconsistent")
        try:
            header = json.loads(blob[16 : 16 + json_length].decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise AsarError(f"asar header is not JSON: {exc}") from exc
        if not isinstance(header, dict) or not isinstance(header.get("files"), dict):
            raise AsarError("asar header has no files map")
        return cls(header=header, data=blob[data_offset:], data_offset=data_offset)

    @classmethod
    def load(cls, path: Path | str) -> "AsarArchive":
        return cls.parse(Path(path).read_bytes())

    def pack(self) -> bytes:
        """The archive bytes: pickle-framed header then the data blob."""
        encoded = json.dumps(self.header, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        padded = encoded + b"\0" * ((4 - len(encoded) % 4) % 4)
        payload = _UINT32.pack(len(encoded)) + padded
        header_pickle = _UINT32.pack(len(payload)) + payload
        return _UINT32.pack(4) + _UINT32.pack(len(header_pickle)) + header_pickle + self.data

    # -- members ------------------------------------------------------------------------

    @staticmethod
    def normalize(member: str) -> str:
        parts = [p for p in member.replace("\\", "/").split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts) or not parts:
            raise AsarError(f"not a member path: {member!r}")
        return "/".join(parts)

    def entry(self, member: str) -> dict[str, Any] | None:
        node: dict[str, Any] = self.header
        for part in self.normalize(member).split("/"):
            files = node.get("files")
            if not isinstance(files, dict) or part not in files or not isinstance(files[part], dict):
                return None
            node = files[part]
        return node

    def members(self) -> list[str]:
        """Every file member (packed or unpacked), POSIX paths, header order."""

        def walk(node: dict[str, Any], prefix: str) -> Iterator[str]:
            for name, child in (node.get("files") or {}).items():
                if not isinstance(child, dict):
                    continue
                path = f"{prefix}{name}"
                if isinstance(child.get("files"), dict):
                    yield from walk(child, path + "/")
                elif "link" not in child:
                    yield path

        return list(walk(self.header, ""))

    def _slice(self, entry: dict[str, Any], member: str) -> tuple[int, int]:
        if isinstance(entry.get("files"), dict):
            raise AsarError(f"{member} is a directory")
        if "link" in entry:
            raise AsarError(f"{member} is a symlink inside the archive")
        if entry.get("unpacked"):
            raise AsarError(f"{member} is unpacked (lives beside the archive, not inside it)")
        try:
            offset, size = int(entry["offset"]), int(entry["size"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AsarError(f"{member} has no offset/size") from exc
        if offset < 0 or size < 0 or offset + size > len(self.data):
            raise AsarError(f"{member} points outside the archive data")
        return offset, size

    def read(self, member: str) -> bytes:
        entry = self.entry(member)
        if entry is None:
            raise AsarError(f"{member} is not in the archive")
        offset, size = self._slice(entry, member)
        return self.data[offset : offset + size]

    def with_member(self, member: str, content: bytes) -> "AsarArchive":
        """A new archive with ``member`` replaced by ``content``; offsets re-laid, integrity recomputed for it."""
        key = self.normalize(member)
        target = self.entry(key)
        if target is None:
            raise AsarError(f"{member} is not in the archive")
        self._slice(target, key)  # must be a packed file

        header = json.loads(json.dumps(self.header))  # deep copy, JSON-shaped
        packed: list[tuple[int, dict[str, Any], str]] = []

        def collect(node: dict[str, Any], prefix: str) -> None:
            for name, child in (node.get("files") or {}).items():
                if not isinstance(child, dict):
                    continue
                path = f"{prefix}{name}"
                if isinstance(child.get("files"), dict):
                    collect(child, path + "/")
                elif "link" not in child and not child.get("unpacked") and "offset" in child:
                    packed.append((int(child["offset"]), child, path))

        collect(header, "")
        packed.sort(key=lambda item: item[0])
        chunks: list[bytes] = []
        cursor = 0
        for offset, entry, path in packed:
            size = int(entry["size"])
            if path == key:
                bytes_ = content
                if isinstance(entry.get("integrity"), dict):
                    block_size = int(entry["integrity"].get("blockSize") or _DEFAULT_BLOCK_SIZE)
                    entry["integrity"] = _integrity(content, block_size)
            else:
                bytes_ = self.data[offset : offset + size]
            entry["offset"] = str(cursor)
            entry["size"] = len(bytes_)
            chunks.append(bytes_)
            cursor += len(bytes_)
        return AsarArchive(header=header, data=b"".join(chunks), data_offset=self.data_offset)


# --- fuses ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Fuses:
    """The fuse wire of an Electron binary: one character per fuse (``0`` off, ``1`` on, ``r`` removed)."""

    wire: str
    source: Path | None = None
    unknown: tuple[str, ...] = field(default=())

    def state(self, index: int) -> str | None:
        return self.wire[index] if 0 <= index < len(self.wire) else None

    @property
    def asar_integrity_enforced(self) -> bool | None:
        """``True`` when the shell refuses a rewritten archive; ``None`` when the wire does not say."""
        state = self.state(FUSE_EMBEDDED_ASAR_INTEGRITY)
        return None if state is None or state == "r" else state == "1"

    @property
    def only_load_app_from_asar(self) -> bool | None:
        state = self.state(FUSE_ONLY_LOAD_APP_FROM_ASAR)
        return None if state is None or state == "r" else state == "1"


def read_fuses(binary: Path | str, *, chunk_size: int = 8 * 1024 * 1024) -> Fuses | None:
    """Scan an Electron binary for the fuse sentinel and return its wire, or ``None`` when absent."""
    path = Path(binary)
    try:
        with path.open("rb") as handle:
            carry = b""
            while True:
                block = handle.read(chunk_size)
                if not block:
                    return None
                window = carry + block
                index = window.find(ASAR_FUSE_SENTINEL)
                if index >= 0:
                    start = index + len(ASAR_FUSE_SENTINEL)
                    tail = window[start : start + 2 + 64]
                    if len(tail) < 2:
                        tail += handle.read(66)
                    if len(tail) < 2:
                        return None
                    _version, length = tail[0], tail[1]
                    wire_bytes = tail[2 : 2 + length]
                    if len(wire_bytes) < length:
                        wire_bytes += handle.read(length - len(wire_bytes))
                    wire = "".join(chr(b) if chr(b) in "01r" else "?" for b in wire_bytes)
                    return Fuses(wire=wire, source=path)
                carry = window[-(len(ASAR_FUSE_SENTINEL) + 66) :]
    except OSError:
        return None
