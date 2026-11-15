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


def governance_locations(host_home: Path, payload=None):
    """Where this edition's governance lives (core ``floofy_core.editions.governance_locations``)."""
    from .governance import locations

    return locations(host_home, payload)


def reapply_triggers(host_home: Path, command: list[str], **kwargs):
    """The edition's re-apply trigger managers (core ``floofy_core.triggers.trigger_managers``).

    Named ``reapply_triggers`` on purpose: importing the ``triggers`` submodule
    would rebind a package attribute of the same name to the module.
    """
    from .triggers import triggers as _triggers

    return _triggers(host_home, command, **kwargs)


def early_shim_kind(payload) -> str:
    """A venv payload processes only its venv site; a bundle/desktop payload its user site (spike 1.5)."""
    return "venv-site" if payload is not None and (payload.root / "pyvenv.cfg").is_file() else "user-site"


def update_hold(host_home: Path, *, duration: str | None, release: bool) -> dict:  # noqa: ARG001
    """``floofy hold``: the public edition has no pause switch; explain how to pin instead (Requirement 6.6)."""
    return {
        "ok": True,
        "detail": (
            "the public edition has no update pause: pin the host instead — `pipx install kirocrew==<version> --force` for a pipx install, "
            "`<venv>/bin/pip install kirocrew==<version>` for a managed venv, or disable auto-update in the desktop app's settings; "
            "`floofy apply` re-applies after any update"
        ),
        "command": None,
    }


def registry_keys() -> dict[str, bytes]:
    """The Ed25519 public keys pinned for this edition's registry indexes (core ``floofy_core.editions.registry_keys``; Requirement 8.3)."""
    from .registry import registry_keys as _keys

    return _keys()


def default_sources() -> list[dict]:
    """The registry source rows this edition ships with (core ``floofy_core.editions.default_sources``)."""
    from .registry import default_sources as _sources

    return _sources()


def registry_opener(url: str):  # noqa: ARG001 - the public registries need no identity
    """No network identity on this edition: anonymous HTTPS (design DR-3 "Network identity: none")."""
    return None


def release_feed() -> dict | None:
    """The FloofyCrew release feed of this edition — the GitHub releases API (core ``floofy_core.editions.release_feed``; Requirement 7.7)."""
    from .registry import release_feed as _feed

    return _feed()
