"""``installer.place`` keeps a mod's ``.floofy/`` runtime state across an update — including when a
python-hook left a live unix socket in it (seen updating agent-orchestrator on the test box, 2026-10-04:
``shutil.copytree`` raised ``[Errno 6] No such device or address`` on ``.floofy/bus.sock`` and the whole
install failed). Sockets, fifos and device nodes are skipped; regular files and directories survive."""
from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
from pathlib import Path

import pytest

from floofy_core.datahome import DataHome
from floofy_core.installer import ResolvedSource, place


def _mod_source(tmp_path: Path, version: str) -> tuple[Path, dict]:
    root = tmp_path / f"src-{version}"
    root.mkdir()
    manifest = {"schema": 1, "id": "sock-mod", "name": "Sock", "version": version, "parts": [], "files": []}
    (root / "floofy.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "README.md").write_text(f"v{version}\n", encoding="utf-8")
    return root, manifest


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="unix sockets only")
def test_update_keeps_runtime_state_and_skips_a_live_socket(tmp_path: Path) -> None:
    # the data home needs a SHORT path: AF_UNIX caps a socket path at ~104 bytes and pytest's tmp_path is deeper
    short = Path(tempfile.mkdtemp(prefix="fl-"))
    try:
        _run(short, tmp_path)
    finally:
        shutil.rmtree(short, ignore_errors=True)


def _run(short: Path, tmp_path: Path) -> None:
    home = DataHome(short / "h" / "floofy")
    root1, m1 = _mod_source(tmp_path, "1.0.0")
    installed, state = place(home, ResolvedSource(kind="path", ref=str(root1), root=root1), m1, stage=False)
    assert state == "installed" and (installed / "README.md").read_text() == "v1.0.0\n"

    # the running hook leaves its state behind: a config, a runs dir with a row, a log — and a bound socket
    runtime = installed / ".floofy"
    (runtime / "runs" / "r1").mkdir(parents=True)
    (runtime / "config.json").write_text('{"peer_messaging": true}', encoding="utf-8")
    (runtime / "runs" / "r1" / "bus.jsonl").write_text('{"id":"m1"}\n', encoding="utf-8")
    os.mkfifo(runtime / "pipe")
    sock_path = runtime / "bus.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(sock_path))
    try:
        root2, m2 = _mod_source(tmp_path, "1.1.0")
        installed2, _ = place(home, ResolvedSource(kind="path", ref=str(root2), root=root2), m2, stage=False)
    finally:
        listener.close()

    assert installed2 == installed and (installed / "README.md").read_text() == "v1.1.0\n"
    kept = installed / ".floofy"
    assert json.loads((kept / "config.json").read_text()) == {"peer_messaging": True}
    assert (kept / "runs" / "r1" / "bus.jsonl").read_text() == '{"id":"m1"}\n'
    assert not (kept / "bus.sock").exists() and not (kept / "pipe").exists(), "special files are not carried over; the hook recreates them"
