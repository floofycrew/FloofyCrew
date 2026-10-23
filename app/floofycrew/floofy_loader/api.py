"""The mod-facing ``floofy`` module (Requirement 3.3, design "Interfaces exposed to mods").

Mods write ``import floofy`` and read:

* ``floofy.host`` — :class:`floofy_loader.host.HostFacts` (``version``,
  ``build_version``, ``base_version``, ``channel``, ``edition``, ``profile``,
  ``payload_root``, ``data_home`` …);
* ``floofy.api_version`` — the SemVer string mods pin on
  (:data:`floofy_core.API_VERSION`); deprecated names are kept for two
  FloofyCrew minors and warn naming the caller (:func:`deprecated_name`);
* ``floofy.fetch`` — the sanctioned network client (task 4.5, encrypted-only
  with the loopback exemption; attributed to the calling mod);
* ``floofy.events`` — the Loader's event bus (task 4.6).

The runtime registers this module under ``sys.modules["floofy"]`` before any
mod is activated (:func:`install_alias`), so the plain name works inside the
gateway without a ``floofy`` package on ``sys.path``. Before the Loader binds
the facts, ``host`` describes an unknown host (``read_host_facts()`` outside
a host); tests use :func:`bind`.
"""
from __future__ import annotations

import inspect
import logging
import sys
import warnings
from typing import Any, Callable

from floofy_core import API_VERSION, __version__ as FRAMEWORK_VERSION

from .host import HostFacts, read_host_facts

__all__ = [
    "API_VERSION",
    "FRAMEWORK_VERSION",
    "api_version",
    "bind",
    "caller_module",
    "deprecated_name",
    "events",
    "fetch",
    "framework_version",
    "host",
    "install_alias",
    "unofficial",
]

logger = logging.getLogger("floofy.api")

#: The name mods import.
ALIAS = "floofy"

api_version: str = API_VERSION
framework_version: str = FRAMEWORK_VERSION
#: FloofyCrew identifies itself as unofficial everywhere (Requirement 11.8).
unofficial: bool = True

host: HostFacts = read_host_facts(adapters=[])

#: Bound by the runtime (task 4.5 / 4.6); until then they explain themselves.
events: Any = None


def _not_bound(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("floofy.fetch is available once the Loader has booted (the runtime binds it before activating mods)")


fetch: Callable[..., Any] = _not_bound

#: ``old name -> (new name, FloofyCrew version that removes the old one)``. A name
#: stays here for two FloofyCrew minors after its replacement ships (design
#: "Interfaces exposed to mods"); reading it warns with the caller's module.
DEPRECATED_NAMES: dict[str, tuple[str, str]] = {}


def bind(*, host_facts: HostFacts | None = None, fetch_impl: Callable[..., Any] | None = None, event_bus: Any = None) -> None:
    """Runtime hook: publish the live facts and the bound clients (also used by tests)."""
    global host, fetch, events  # noqa: PLW0603 - module attributes are the API surface
    if host_facts is not None:
        host = host_facts
    if fetch_impl is not None:
        fetch = fetch_impl
    if event_bus is not None:
        events = event_bus


def install_alias() -> None:
    """Make ``import floofy`` resolve to this module (idempotent)."""
    sys.modules.setdefault(ALIAS, sys.modules[__name__])


def caller_module(depth: int = 2) -> str:
    """The ``__name__`` of the module ``depth`` frames up (the mod calling into the API)."""
    frame = inspect.currentframe()
    try:
        for _ in range(depth):
            if frame is None:
                break
            frame = frame.f_back
        if frame is None:
            return "<unknown>"
        return str(frame.f_globals.get("__name__", "<unknown>"))
    finally:
        del frame


def deprecated_name(old: str, new: str, removed_in: str, *, stacklevel: int = 2) -> None:
    """Warn that ``old`` is deprecated in favour of ``new``, naming the calling mod's module.

    Emits a :class:`DeprecationWarning` (so mod authors see it in tests) and a
    log line attributed to the caller; never raises. ``removed_in`` is the
    FloofyCrew version that drops the old name (two minors after the rename).
    ``stacklevel`` is the warnings-style distance to the frame to blame (2 =
    whoever called this function).
    """
    caller = caller_module(depth=stacklevel)
    message = f"floofy.{old} is deprecated, use floofy.{new} (removed in FloofyCrew {removed_in}); called from {caller}"
    warnings.warn(message, DeprecationWarning, stacklevel=stacklevel)
    logger.warning(message)


def __getattr__(name: str) -> Any:
    """Module-level deprecated aliases resolve to their replacement with a warning."""
    if name in DEPRECATED_NAMES:
        new, removed_in = DEPRECATED_NAMES[name]
        deprecated_name(name, new, removed_in, stacklevel=3)
        return globals()[new]
    raise AttributeError(f"module 'floofy' has no attribute {name!r}")
