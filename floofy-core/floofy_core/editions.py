"""Edition-adapter discovery without naming an edition (Requirement 10.1, 10.2).

The core never imports an adapter by name. Any importable top-level module
``floofy_edition_<name>`` is an adapter; it exposes ``EDITION`` (``"internal"`` /
``"external"``), ``providers()`` (payload providers), ``probe_edition(package_dir)``
(``edition`` or ``None``) and, later, the governance locations and trigger
installers. The CLI combines whatever adapters are installed: on a machine with
both, both sets of payload roots are searched and the probe asks each adapter in
turn before falling back to :func:`floofy_core.payloads.default_edition_probe`.
"""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from types import ModuleType
from typing import Callable

from .governance import GovernanceLocations, default_locations
from .payloads import EditionProbe, Payload, PayloadProvider, default_edition_probe

__all__ = ["ADAPTER_PREFIX", "combined_edition_probe", "edition_providers", "governance_locations", "load_edition_adapters"]

ADAPTER_PREFIX = "floofy_edition_"


def load_edition_adapters() -> list[ModuleType]:
    """Import every ``floofy_edition_*`` module on ``sys.path`` (sorted by name)."""
    adapters: list[ModuleType] = []
    names = sorted({m.name for m in pkgutil.iter_modules() if m.name.startswith(ADAPTER_PREFIX)})
    for name in names:
        try:
            adapters.append(importlib.import_module(name))
        except Exception:  # noqa: BLE001 - a broken adapter must not take the CLI down
            continue
    return adapters


def edition_providers(adapters: list[ModuleType] | None = None) -> list[PayloadProvider]:
    """Every payload provider the installed adapters offer."""
    providers: list[PayloadProvider] = []
    for adapter in load_edition_adapters() if adapters is None else adapters:
        factory: Callable[[], list[PayloadProvider]] | None = getattr(adapter, "providers", None)
        if callable(factory):
            try:
                providers.extend(factory())
            except Exception:  # noqa: BLE001
                continue
    return providers


def combined_edition_probe(adapters: list[ModuleType] | None = None) -> EditionProbe:
    """Ask each adapter's ``probe_edition`` in turn, then the core fallback."""
    probes = [getattr(a, "probe_edition") for a in (load_edition_adapters() if adapters is None else adapters) if callable(getattr(a, "probe_edition", None))]

    def probe(package_dir: Path) -> str:
        for candidate in probes:
            try:
                verdict = candidate(package_dir)
            except Exception:  # noqa: BLE001
                verdict = None
            if verdict:
                return str(verdict)
        return default_edition_probe(package_dir)

    return probe


def governance_locations(host_home: Path, payload: Payload | None = None, adapters: list[ModuleType] | None = None) -> GovernanceLocations:
    """The governance locations for ``payload``: the adapter matching its edition, else the neutral defaults."""
    for adapter in load_edition_adapters() if adapters is None else adapters:
        if payload is not None and getattr(adapter, "EDITION", None) != payload.edition:
            continue
        factory = getattr(adapter, "governance_locations", None)
        if callable(factory):
            try:
                return factory(host_home, payload)
            except Exception:  # noqa: BLE001
                continue
    return default_locations(host_home)
