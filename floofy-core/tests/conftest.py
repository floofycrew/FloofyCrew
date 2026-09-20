"""Pytest fixtures for the floofy-core tests; shared helpers live in ``floofy_testing``."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

import pytest

from floofy_testing import EXAMPLES_DIR, live_dropin_sidecars, live_fingerprint  # noqa: F401 (the import also arms the isolation guard)


@pytest.fixture(autouse=True, scope="session")
def _live_installs_untouched():
    """Fail the session loudly if any test changed a live dashboard shell or dropped a seam file into the live Kiro home."""
    before = live_fingerprint()
    sidecars_before = live_dropin_sidecars()
    yield
    after = live_fingerprint()
    assert after == before, f"a test modified a live host install: {set(after.items()) ^ set(before.items())}"
    leaked = live_dropin_sidecars() - sidecars_before
    assert not leaked, f"a test installed a drop-in part into the live Kiro home (set KIRO_HOME in its fixture): {sorted(leaked)}"


@pytest.fixture
def example_mod(tmp_path: Path) -> Callable[[str], Path]:
    """Copy an example mod into a scratch directory and return its root."""

    def factory(kind: str) -> Path:
        destination = tmp_path / kind
        shutil.copytree(EXAMPLES_DIR / kind, destination)
        return destination

    return factory
