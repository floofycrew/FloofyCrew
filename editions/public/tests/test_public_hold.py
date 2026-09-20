"""`floofy hold` on the public edition (Requirement 6.6): no pause switch — the answer explains how to pin the host."""
from __future__ import annotations

from pathlib import Path

import floofy_edition_public


def test_hold_explains_pinning(tmp_path: Path):
    answer = floofy_edition_public.update_hold(tmp_path / "home", duration="7d", release=False)
    assert answer["ok"] is True and answer["command"] is None and "pin the host" in answer["detail"] and "pipx" in answer["detail"]
