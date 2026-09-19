"""Pytest fixtures for the floofy-core tests; shared helpers live in ``floofy_testing``."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

import pytest

from floofy_testing import EXAMPLES_DIR, live_fingerprint  # noqa: F401 (the import also arms the isolation guard)


@pytest.fixture(autouse=True, scope="session")
def _live_installs_untouched():
    """Fail the session loudly if any test changed a live dashboard shell (the live host is read-only)."""
    before = live_fingerprint()
    yield
    after = live_fingerprint()
    assert after == before, f"a test modified a live host install: {set(after.items()) ^ set(before.items())}"


@pytest.fixture
def example_mod(tmp_path: Path) -> Callable[[str], Path]:
    """Copy an example mod into a scratch directory and return its root."""

    def factory(kind: str) -> Path:
        destination = tmp_path / kind
        shutil.copytree(EXAMPLES_DIR / kind, destination)
        return destination

    return factory
