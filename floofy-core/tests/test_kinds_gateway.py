"""The theme handler against a real gateway (task 6.3; Requirement 2.1, 2.8): route vs direct write are byte-equivalent.

Skipped without the payload copy under ``.scratch/``. One scratch gateway
(``loader_testing.ScratchGateway`` — scratch homes, ``FLOOFY_NO_ADAPTERS=1``):

1. the manager's authenticated session mints a token with the host's local
   secret (``/api/token/local``) — the CLI's real path to the Loader's routes;
2. the theme handler installs the Rimuru pack through ``POST /api/themes/install``
   (the route copies, validates and renames into ``themes/rimuru/``);
3. the same pack written by ``direct_write_theme`` into another directory has
   exactly the same files with exactly the same bytes;
4. the handler removes the installed pack through ``DELETE /api/themes/rimuru``.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from floofy_core.audit import AuditLog
from floofy_core.gateway import GatewaySession, read_local_secret
from floofy_core.governance import GovernanceSnapshot
from floofy_core.kinds import KindContext
from floofy_core.kinds.theme import ThemeHandler
from floofy_core.themes import direct_write_theme

from floofy_testing import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "loader-app" / "tests"))
from loader_testing import ScratchGateway, fresh_scratch, scratch_payload  # noqa: E402

pytestmark = pytest.mark.skipif(scratch_payload() is None, reason="no payload copy under .scratch (set FLOOFY_SCRATCH_PAYLOAD)")

RIMURU = REPO_ROOT / "mods" / "rimuru-branding"


def tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture(scope="module")
def gateway():
    payload = scratch_payload()
    assert payload is not None
    gw = ScratchGateway(payload, fresh_scratch("kinds-gateway"))
    gw.start()
    try:
        yield gw
    finally:
        gw.stop()


def test_theme_route_and_direct_write_are_byte_equivalent(gateway: ScratchGateway, tmp_path: Path):
    port = int(gateway.ready["port"])
    assert read_local_secret(gateway.home, port), "the test-mode gateway writes its local secret into the scratch home"
    session = GatewaySession(gateway.home, port)
    token = session.mint()
    assert token and session.get("/api/apps").status == 200, "the minted session authenticates like the dashboard does"

    said: list[str] = []
    kctx = KindContext(host_home=gateway.home, kiro_home=gateway.scratch / "kiro", data_home=gateway.data_home, session=session, governance=GovernanceSnapshot(), say=said.append, warn=said.append, audit=AuditLog(gateway.data_home).record)
    part = {"kind": "theme", "side": "gateway", "path": "theme/theme.json"}
    outcome = ThemeHandler().install(kctx, "rimuru-branding", RIMURU, part, 0)
    assert outcome.ok and outcome.extra["via"] == "route", (outcome.detail, said)
    routed = gateway.home / "themes" / "rimuru"
    assert routed.is_dir()

    direct_dir, summary = direct_write_theme(RIMURU / "theme", tmp_path / "themes")
    assert summary.slug == "rimuru"
    assert tree(routed) == tree(direct_dir), "the direct write produces the host route's tree, byte for byte (Requirement 2.1, 2.8)"
    listed = session.get("/api/themes").json()
    assert any(t.get("slug") == "rimuru" for t in listed.get("themes", [])), listed

    removed = ThemeHandler().uninstall(kctx, "rimuru-branding", RIMURU, part, 0)
    assert removed.status == "removed" and "DELETE /api/themes/rimuru" in removed.detail
    assert not routed.exists()
