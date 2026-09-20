"""registry-tools test fixtures: the same live-install guard as the core suite, plus a scratch registry repository."""
from __future__ import annotations

import pytest

from floofy_testing import live_fingerprint  # noqa: F401 (the import also arms the isolation guard)


@pytest.fixture(autouse=True, scope="session")
def _live_installs_untouched():
    before = live_fingerprint()
    yield
    assert live_fingerprint() == before, "a test modified a live host install"
