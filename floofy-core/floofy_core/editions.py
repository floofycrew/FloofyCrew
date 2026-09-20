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

__all__ = ["ADAPTER_PREFIX", "combined_edition_probe", "default_sources", "edition_providers", "governance_locations", "load_edition_adapters", "pinned_registry_ids", "registry_keys", "registry_opener", "release_feed"]

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



# --- registry hooks (Requirement 8.3, 8.6; design DR-3 "Registry" and "Network identity" rows) --------------


def _adapters_for(edition: str | None, adapters: list[ModuleType] | None) -> list[ModuleType]:
    found = load_edition_adapters() if adapters is None else adapters
    if edition in (None, "", "unknown"):
        return found
    matching = [a for a in found if getattr(a, "EDITION", None) == edition]
    return matching or found


def registry_keys(adapters: list[ModuleType] | None = None, *, edition: str | None = None) -> dict[str, bytes]:
    """key id → public key pinned by the installed adapters (``registry_keys()`` hooks); ``edition`` narrows to one adapter when it is installed."""
    keys: dict[str, bytes] = {}
    for adapter in _adapters_for(edition, adapters):
        factory = getattr(adapter, "registry_keys", None)
        if callable(factory):
            try:
                for kid, public in (factory() or {}).items():
                    if isinstance(kid, str) and isinstance(public, (bytes, bytearray)):
                        keys[kid] = bytes(public)
            except Exception:  # noqa: BLE001 - a broken adapter must not take verification down; it just pins nothing
                continue
    return keys


def default_sources(adapters: list[ModuleType] | None = None, *, edition: str | None = None) -> list[dict]:
    """The registry source rows the adapters ship (``default_sources()`` hooks), each tagged with its adapter's ``EDITION``."""
    rows: list[dict] = []
    for adapter in _adapters_for(edition, adapters):
        factory = getattr(adapter, "default_sources", None)
        if callable(factory):
            try:
                for row in factory() or []:
                    if isinstance(row, dict) and isinstance(row.get("url"), str):
                        rows.append({**row, "edition": getattr(adapter, "EDITION", None)})
            except Exception:  # noqa: BLE001
                continue
    return rows


def registry_opener(url: str, adapters: list[ModuleType] | None = None) -> Callable[..., object] | None:
    """The first adapter-supplied ``urllib`` opener for ``url`` (network identity, e.g. an SSO cookie jar), or ``None`` for anonymous HTTPS."""
    for adapter in load_edition_adapters() if adapters is None else adapters:
        factory = getattr(adapter, "registry_opener", None)
        if callable(factory):
            try:
                opener = factory(url)
            except Exception:  # noqa: BLE001
                opener = None
            if opener is not None:
                return opener
    return None


def pinned_registry_ids(adapters: list[ModuleType] | None = None, *, edition: str | None = None) -> list[str]:
    """Host app-registry ids an edition pins (``pinned_registry_ids()`` hooks); the manager never reuses them (Requirement 8.6)."""
    ids: list[str] = []
    for adapter in _adapters_for(edition, adapters):
        factory = getattr(adapter, "pinned_registry_ids", None)
        if callable(factory):
            try:
                ids.extend(str(i) for i in factory() or [])
            except Exception:  # noqa: BLE001
                continue
    return ids


def release_feed(adapters: list[ModuleType] | None = None, *, edition: str | None = None) -> dict | None:
    """The edition's FloofyCrew release feed (``release_feed()`` hook; :class:`floofy_core.selfupdate.ReleaseFeed` document), or ``None``.

    Requirement 7.7: where FloofyCrew's own releases are published is an
    edition concern (GitHub releases vs the internal package), so the core asks
    the adapter matching the running edition and names no endpoint itself.
    """
    for adapter in _adapters_for(edition, adapters):
        factory = getattr(adapter, "release_feed", None)
        if callable(factory):
            try:
                document = factory()
            except Exception:  # noqa: BLE001 - a broken adapter means "no feed", never a failed command
                continue
            if isinstance(document, dict) and document.get("url"):
                return {**document, "label": str(document.get("label") or f"{getattr(adapter, 'EDITION', 'edition')} release feed")}
    return None
