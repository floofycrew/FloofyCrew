"""Governance locations of the public edition (Requirement 10.1, 11.2).

The public edition has no bundled ceiling: governance is whatever the operator
put in ``~/.kiro/crew/security_policy.json`` (or ``KIROCREW_SECURITY_POLICY``),
``config.json`` and the admission files under the host data home.
"""
from __future__ import annotations

from pathlib import Path

from floofy_core.governance import GovernanceLocations, default_locations
from floofy_core.payloads import Payload

__all__ = ["locations"]


def locations(host_home: Path, payload: Payload | None = None) -> GovernanceLocations:  # noqa: ARG001 - same signature on both editions
    return default_locations(host_home)
