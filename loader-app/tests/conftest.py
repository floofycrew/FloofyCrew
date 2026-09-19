"""Pytest fixtures for the Loader tests; shared helpers live in ``loader_testing``."""
from __future__ import annotations

import pytest

from loader_testing import live_fingerprint  # noqa: F401 (the import also arms the isolation guard)


@pytest.fixture(autouse=True, scope="session")
def _live_installs_untouched():
    """Fail the session loudly if any test changed a live dashboard shell (the live host is read-only)."""
    before = live_fingerprint()
    yield
    after = live_fingerprint()
    assert after == before, f"a test modified a live host install: {set(after.items()) ^ set(before.items())}"
