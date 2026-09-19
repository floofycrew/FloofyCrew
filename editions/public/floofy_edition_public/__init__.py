"""FloofyCrew edition adapter for the public open-source distribution.

Owns payload discovery (pipx / managed venvs / desktop bundles), host-version
derivation (``kiro_crew.__version__`` + channel marker), the venv-site early
shim location, the re-apply triggers (user timer, ``kirocrew`` PATH wrapper) and
the public registry endpoint.

The core finds this module by its ``floofy_edition_`` prefix
(:mod:`floofy_core.editions`) and uses :func:`providers` and :func:`probe_edition`.
"""
from __future__ import annotations

from pathlib import Path

EDITION = "external"


def providers() -> list:
    """The payload providers of this edition (lazy import keeps ``import`` cheap)."""
    from .payloads import default_providers

    return default_providers()


def probe_edition(package_dir: Path) -> str | None:
    from .payloads import probe_edition as _probe

    return _probe(package_dir)
