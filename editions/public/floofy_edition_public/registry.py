"""The public edition's registry endpoint and pinned signing keys (Requirement 8.3, 10.1; design DR-3).

Everything edition-specific about the registry lives in ``registry.json`` next
to this module — the default source (the ``floofycrew-registry`` GitHub
repository served raw from its ``main`` branch), the host-registry row the
same repository doubles as (``repo`` / ``branch`` for the KiroCrew federated
app registry, task 7.4), the archive URL template the registry bootstrap uses
(task 7.5) and the Ed25519 public key records the manager trusts for this
edition's indexes. The core reads them through the adapter hooks
:func:`registry_keys` and :func:`default_sources`
(:mod:`floofy_core.editions`); it never names an endpoint itself.

The private halves of the keys are NOT in the repository (see the owner note
``.scratch/keys/KEYS.md`` from task 7.1).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

__all__ = ["archive_url_template", "default_sources", "registry_config", "registry_keys", "release_feed"]

CONFIG_PATH = Path(__file__).resolve().with_name("registry.json")


@lru_cache(maxsize=1)
def registry_config() -> dict[str, Any]:
    from floofy_core.resources import read_package_text  # noqa: PLC0415 - stdlib-only core module

    # importlib.resources, not CONFIG_PATH.read_text(): the zipapp form of floofy cannot read a path inside itself
    document = json.loads(read_package_text(__package__, "registry.json", fallback=CONFIG_PATH))
    if not isinstance(document, dict):
        raise ValueError(f"{CONFIG_PATH} is not a JSON object")
    return document


def registry_keys() -> dict[str, bytes]:
    """key id → 32-byte Ed25519 public key for every pinned record (ids re-derived from the key bytes)."""
    from floofy_core.signing import load_public_key_record  # noqa: PLC0415 - stdlib-only core module

    keys: dict[str, bytes] = {}
    for record in registry_config().get("keys") or []:
        if isinstance(record, dict):
            kid, public = load_public_key_record(record)
            keys[kid] = public
    return keys


def default_sources() -> list[dict[str, Any]]:
    """The ``registries.json`` rows this edition ships with (``url``, ``name``, ``trust``, ``keyId``, plus the host-registry ``repo``/``branch``)."""
    return [dict(row) for row in registry_config().get("sources") or [] if isinstance(row, dict) and isinstance(row.get("url"), str)]


def archive_url_template() -> str | None:
    template = registry_config().get("archiveUrlTemplate")
    return template if isinstance(template, str) else None


def release_feed() -> dict[str, Any] | None:
    """Where this edition publishes FloofyCrew itself (Requirement 7.7; core ``floofy_core.editions.release_feed``).

    ``registry.json`` ``selfUpdate``: the GitHub releases API of the FloofyCrew
    repository (``kind: github-releases`` — the tag names the version, the release
    body is the build's ``RELEASE.md``) and the release assets ``floofy.pyz``,
    ``SHA256SUMS`` and the Loader app archive. Anonymous HTTPS, a generic agent,
    nothing identifying in the request.
    """
    feed = registry_config().get("selfUpdate")
    return dict(feed) if isinstance(feed, dict) and isinstance(feed.get("url"), str) else None
