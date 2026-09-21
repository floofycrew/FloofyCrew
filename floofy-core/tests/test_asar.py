"""``floofy_core.asar`` against a reference archive written by ``@electron/asar`` (Requirement 5.11).

``fixtures/electron/app.asar`` was packed by the real tool from ``fixtures/electron/src``
(see the fixture README); the tests read it back member by member, rewrite one
member and check the re-laid archive parses, keeps every other member byte-identical,
recomputes the integrity record, and — when the reference tool is on this machine —
is accepted by it. The fuse reader is tested on synthetic binaries.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from floofy_core.asar import (
    ASAR_FUSE_SENTINEL,
    FUSE_EMBEDDED_ASAR_INTEGRITY,
    AsarArchive,
    AsarError,
    read_fuses,
)

FIXTURES = Path(__file__).parent / "fixtures" / "electron"
REFERENCE = FIXTURES / "app.asar"
SRC = FIXTURES / "src"
#: The dev-scratch install of the reference tool (not shipped, not required); ``FLOOFY_ASAR_REFERENCE_TOOL`` overrides.
REFERENCE_TOOL = Path(os.environ.get("FLOOFY_ASAR_REFERENCE_TOOL") or Path(__file__).resolve().parents[2] / ".scratch" / "asar-tool" / "node_modules" / "@electron" / "asar")


def src_members() -> dict[str, bytes]:
    return {p.relative_to(SRC).as_posix(): p.read_bytes() for p in sorted(SRC.rglob("*")) if p.is_file()}


def test_reference_archive_reads_every_member() -> None:
    archive = AsarArchive.load(REFERENCE)
    expected = src_members()
    assert set(archive.members()) == set(expected)
    for member, content in expected.items():
        assert archive.read(member) == content, member
        entry = archive.entry(member)
        assert entry is not None and entry["size"] == len(content)
        assert entry["integrity"]["hash"] == hashlib.sha256(content).hexdigest()
    assert archive.data_offset == 8 + struct.unpack("<I", REFERENCE.read_bytes()[4:8])[0]


def test_pack_round_trips_and_is_byte_identical_to_the_reference() -> None:
    blob = REFERENCE.read_bytes()
    archive = AsarArchive.parse(blob)
    repacked = archive.pack()
    assert AsarArchive.parse(repacked).members() == archive.members()
    for member in archive.members():
        assert AsarArchive.parse(repacked).read(member) == archive.read(member)
    # Node's JSON.stringify and the compact json.dumps agree on this header: the bytes match exactly.
    assert repacked == blob


def test_with_member_relays_offsets_and_recomputes_integrity() -> None:
    archive = AsarArchive.load(REFERENCE)
    member = "mochi/petOverlays.js"
    original = archive.read(member)
    content = original.replace(b"webPreferences: {", b'webPreferences: {\n      partition: "persist:mochi-pet",\n      zoomFactor: 1.0,')
    assert len(content) > len(original)
    patched = archive.with_member(member, content)
    blob = patched.pack()
    reread = AsarArchive.parse(blob)
    assert reread.read(member) == content
    for other in archive.members():
        if other != member:
            assert reread.read(other) == archive.read(other), other
    entry = reread.entry(member)
    assert entry["size"] == len(content)
    assert entry["integrity"] == {
        "algorithm": "SHA256",
        "hash": hashlib.sha256(content).hexdigest(),
        "blockSize": 4194304,
        "blocks": [hashlib.sha256(content).hexdigest()],
    }
    # offsets are contiguous and in the original order
    entries = sorted(((int(reread.entry(m)["offset"]), int(reread.entry(m)["size"])) for m in reread.members()))
    cursor = 0
    for offset, size in entries:
        assert offset == cursor
        cursor += size
    assert cursor == len(reread.data)
    # the original is untouched (frozen, value semantics)
    assert archive.read(member) == original


def test_with_member_shrinking_and_unpacked_entries_survive() -> None:
    archive = AsarArchive.load(REFERENCE)
    header = json.loads(json.dumps(archive.header))
    header["files"]["sub"]["files"]["ghost.node"] = {"size": 5, "unpacked": True}
    header["files"]["sub"]["files"]["alias"] = {"link": "u.txt"}
    with_extras = AsarArchive(header=header, data=archive.data)
    shrunk = with_extras.with_member("main.js", b"1")
    reread = AsarArchive.parse(shrunk.pack())
    assert reread.read("main.js") == b"1"
    assert reread.entry("sub/ghost.node") == {"size": 5, "unpacked": True}
    assert reread.entry("sub/alias") == {"link": "u.txt"}
    assert "sub/alias" not in reread.members() and "sub/ghost.node" in reread.members()
    with pytest.raises(AsarError, match="unpacked"):
        reread.read("sub/ghost.node")
    with pytest.raises(AsarError, match="symlink"):
        reread.read("sub/alias")


@pytest.mark.parametrize(
    ("member", "match"),
    [("nope.js", "not in the archive"), ("mochi", "is a directory"), ("../x", "not a member path"), ("", "not a member path")],
)
def test_read_errors_are_typed(member: str, match: str) -> None:
    archive = AsarArchive.load(REFERENCE)
    with pytest.raises(AsarError, match=match):
        archive.read(member)
    with pytest.raises(AsarError):
        archive.with_member(member, b"x")


def test_member_paths_normalise() -> None:
    archive = AsarArchive.load(REFERENCE)
    assert archive.read("/mochi/petOverlays.js") == archive.read("./mochi//petOverlays.js") == archive.read("mochi\\petOverlays.js")


@pytest.mark.parametrize("blob", [b"", b"\x00" * 15, b"\x05\x00\x00\x00" + b"\x00" * 12, struct.pack("<IIII", 4, 100, 96, 90) + b"{"])
def test_parse_rejects_non_archives(blob: bytes) -> None:
    with pytest.raises(AsarError):
        AsarArchive.parse(blob)


@pytest.mark.skipif(not (REFERENCE_TOOL / "bin" / "asar.js").is_file() or shutil.which("node") is None, reason="reference @electron/asar not installed")
def test_reference_tool_reads_the_repacked_archive(tmp_path: Path) -> None:
    archive = AsarArchive.load(REFERENCE)
    content = archive.read("mochi/petOverlays.js") + b"\n// repacked by floofy\n"
    out = tmp_path / "patched.asar"
    out.write_bytes(archive.with_member("mochi/petOverlays.js", content).pack())
    listing = subprocess.run(["node", str(REFERENCE_TOOL / "bin" / "asar.js"), "list", str(out)], capture_output=True, text=True, check=True).stdout
    assert "/mochi/petOverlays.js" in listing
    subprocess.run(["node", str(REFERENCE_TOOL / "bin" / "asar.js"), "extract", str(out), str(tmp_path / "x")], check=True)
    assert (tmp_path / "x" / "mochi" / "petOverlays.js").read_bytes() == content
    assert (tmp_path / "x" / "sub" / "blob.bin").read_bytes() == (SRC / "sub" / "blob.bin").read_bytes()


# --- fuses -------------------------------------------------------------------------------------


def fuse_binary(wire: str, *, before: int = 100, after: int = 100) -> bytes:
    return b"\x90" * before + ASAR_FUSE_SENTINEL + bytes([1, len(wire)]) + wire.encode("ascii") + b"\x00" * after


def test_read_fuses_finds_the_wire(tmp_path: Path) -> None:
    binary = tmp_path / "Electron Framework"
    binary.write_bytes(fuse_binary("101100011"))
    fuses = read_fuses(binary)
    assert fuses is not None and fuses.wire == "101100011" and fuses.source == binary
    assert fuses.asar_integrity_enforced is False and fuses.only_load_app_from_asar is False
    assert fuses.state(FUSE_EMBEDDED_ASAR_INTEGRITY) == "0"


def test_read_fuses_across_a_chunk_boundary_and_states(tmp_path: Path) -> None:
    binary = tmp_path / "kirocrew"
    binary.write_bytes(fuse_binary("1011r1010", before=1000, after=5))
    fuses = read_fuses(binary, chunk_size=1010)  # the sentinel straddles the first read
    assert fuses is not None and fuses.wire == "1011r1010"
    assert fuses.asar_integrity_enforced is None  # 'r' = removed: the wire does not say
    assert fuses.only_load_app_from_asar is True
    locked = tmp_path / "locked"
    locked.write_bytes(fuse_binary("100010000"))
    assert read_fuses(locked, chunk_size=64).asar_integrity_enforced is True


def test_read_fuses_absent_or_unreadable(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.write_bytes(b"no sentinel here" * 100)
    assert read_fuses(plain) is None
    assert read_fuses(tmp_path / "missing") is None
