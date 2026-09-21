"""Opt-in smoke test: the verify protocol against a real gateway serving a COPY of a host payload.

Skipped unless ``FLOOFY_REAL_PAYLOAD=1``. It never touches a live install: the
payload is a copy of a bundle version directory (2.1 GB, ``cp -a`` it yourself)
and the gateway runs with scratch homes:

    cp -a ~/<install-root>/<ver> .scratch/payload-<ver>
    FLOOFY_REAL_PAYLOAD=1 FLOOFY_REAL_PAYLOAD_DIR=.scratch/payload-<ver> \\
        .venv/bin/python -m pytest -q floofy-core/tests/test_real_gateway.py -s

The test execs the copy's own ``bin/kirocrew gateway --test-mode --no-crons
--no-tunnel`` with ``KIROCREW_HOME`` / ``KIRO_HOME`` under ``.scratch`` (design
"Spike outcomes" 1.3/1.5 recipe). An edition whose boot asks for identity on a
fresh home needs its skip variable: pass it as ``FLOOFY_REAL_PAYLOAD_ENV="K=V,K=V"``
(the internal adapter's README names it). It reads the
``KIROCREW_READY:{port,…}`` line, applies a marker-only descriptor to the copy's
``index.html`` through the Patcher with that gateway as the only endpoint, verifies
over the loopback port and the ``dashboard-0.sock`` unix socket, restores, and
kills the gateway. Requirement 5.7 end to end.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from floofy_core.gateway import GatewayEndpoint, find_endpoints, served_version
from floofy_core.patcher import Patcher, PlannedPatch, VerifyStatus
from floofy_core.patches import PatchDescriptor
from floofy_core.payloads import discover_payloads

from floofy_testing import REPO_ROOT

pytestmark = pytest.mark.skipif(os.environ.get("FLOOFY_REAL_PAYLOAD") != "1", reason="set FLOOFY_REAL_PAYLOAD=1 to run the real-gateway smoke")

PAYLOAD_DIR = Path(os.environ.get("FLOOFY_REAL_PAYLOAD_DIR", REPO_ROOT / ".scratch" / "payload-0.7.0.5")).resolve()
MARKER = {
    "schema": 1,
    "target": "kiro_crew/static/dist/index.html",
    "ops": [{"op": "append-head", "content": '<meta name="floofy-smoke" content="1">\n', "marker": 'name="floofy-smoke"'}],
}


def _start_gateway(scratch: Path) -> tuple[subprocess.Popen, dict]:
    home = scratch / "home"
    home.mkdir(parents=True, exist_ok=True)
    (scratch / "kiro").mkdir(exist_ok=True)
    env = dict(
        os.environ,
        KIROCREW_HOME=str(home),
        KIRO_HOME=str(scratch / "kiro"),
        KIROCREW_SKIP_MODEL_DOWNLOAD="1",
        PYTHONUNBUFFERED="1",
    )
    for pair in filter(None, os.environ.get("FLOOFY_REAL_PAYLOAD_ENV", "").split(",")):
        key, _, value = pair.partition("=")
        env[key.strip()] = value.strip()
    out = (scratch / "gateway.out").open("w", encoding="utf-8")
    err = (scratch / "gateway.err").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [str(PAYLOAD_DIR / "bin" / "kirocrew"), "gateway", "--test-mode", "--no-crons", "--no-tunnel"],
        env=env,
        stdout=out,
        stderr=err,
        cwd=str(PAYLOAD_DIR),
        start_new_session=True,
    )
    deadline = time.time() + 180
    ready: dict | None = None
    while time.time() < deadline and ready is None:
        if process.poll() is not None:
            raise RuntimeError(f"gateway exited early: {(scratch / 'gateway.err').read_text()[-2000:]}")
        for line in (scratch / "gateway.out").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("KIROCREW_READY:"):
                ready = json.loads(line[len("KIROCREW_READY:") :])
                break
        time.sleep(1)
    if ready is None:
        raise RuntimeError("gateway did not print KIROCREW_READY within 180 s")
    return process, ready


def _stop(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=30)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_verify_against_a_real_gateway(tmp_path: Path) -> None:
    if not (PAYLOAD_DIR / "bin" / "kirocrew").is_file():
        pytest.skip(f"no payload copy at {PAYLOAD_DIR} (cp -a a version dir there first)")
    discovery = discover_payloads([], extra_roots=[PAYLOAD_DIR], include_find_spec=False)
    assert discovery.payloads, discovery.format_miss()
    # a bundle ships several interpreter farms; the launcher runs the highest minor that has an interpreter
    farms = sorted(discovery.payloads, key=lambda p: p.package_dir.parent.parent.name)
    payload = farms[-1]
    scratch = REPO_ROOT / ".scratch" / "real-gateway"
    scratch.mkdir(parents=True, exist_ok=True)
    process, ready = _start_gateway(scratch)
    try:
        tcp = GatewayEndpoint(port=int(ready["port"]))
        assert served_version(tcp) == payload.host_version.text, "the copy's gateway must report the copy's version"
        sockets = find_endpoints(scratch / "home")
        assert sockets and sockets[0].port == 0, "test mode binds dashboard-0.sock in KIROCREW_HOME"

        data_home = tmp_path / "floofy"
        patcher = Patcher(data_home, [payload], endpoints=[tcp], reporter=print)
        report = patcher.apply([PlannedPatch("smoke", "0", PatchDescriptor.from_dict(MARKER, source="smoke"))])
        result = report.payloads[0]
        assert result.ok and not result.dormant and result.written == [str(payload.index_html)]
        assert result.verify is not None and result.verify.status == VerifyStatus.VERIFIED, result.verify
        shell = tcp.get("/")
        assert shell.status == 200 and b'name="floofy-smoke"' in shell.body

        over_socket = Patcher(data_home, [payload], endpoints=sockets, reporter=print).verify(payload)
        assert over_socket.status == VerifyStatus.VERIFIED and over_socket.endpoint == str(sockets[0].socket_path)

        patcher.restore()
        assert b'name="floofy-smoke"' not in tcp.get("/").body
        assert patcher.verify(payload).status == VerifyStatus.NOTHING
    finally:
        _stop(process)
