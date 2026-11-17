"""The **source tier** of a mod (Requirement 8.11): where it came from and how much anyone vouched for it.

Three values, from least to most vouched for:

* ``unlisted`` — installed from a git reference (Requirement 8.8), a local
  directory or an archive (a path or an ``https://`` download): no curator
  reviewed it, no compatibility row exists for it. The manager verified only what
  the mod says about itself (``floofy validate``, ``files[]`` hashes) and the
  user consented; nothing else is known;
* ``listed`` — installed from a registry record (asset or link, Requirement 8.2,
  8.9): a curator merged the record, the index the record came from verified
  against the edition's pinned key (or the user allowed that source unsigned);
* ``tested`` — ``listed`` **and** the compatibility matrix row for the running
  host (``edition × channel × hostVersion``) grades this ``mod@version`` ``tested``
  (a Forge run passed on exactly this host version, Requirement 9).

The first two are a property of the install and are written into the mod's
``.floofy/source.json`` at install time (:mod:`floofy_core.installer`); the
third depends on the host the mod sits on and is derived when shown
(:func:`tier_of`). A registry search result has the tier the install *would*
have (:func:`tier_for_candidate`). Shown by ``floofy search`` / ``info`` /
``list`` / ``status``, the Loader's ``/state`` and ``/registry`` payloads and
the manager page (a badge beside the host-compat badge).
"""
from __future__ import annotations

from typing import Any

__all__ = ["TIERS", "TIER_LISTED", "TIER_TESTED", "TIER_UNLISTED", "describe", "tier_for_candidate", "tier_for_source_kind", "tier_of"]

TIER_UNLISTED = "unlisted"
TIER_LISTED = "listed"
TIER_TESTED = "tested"
TIERS = (TIER_UNLISTED, TIER_LISTED, TIER_TESTED)

_DESCRIPTIONS = {
    TIER_UNLISTED: "installed from a git reference, a local directory or an archive — no curator review, no compatibility data",
    TIER_LISTED: "installed from a registry record: a curator merged it and the index verified (or you allowed the source unsigned)",
    TIER_TESTED: "listed, and the compatibility matrix grades this version `tested` on this host version",
}


def tier_for_source_kind(kind: str | None) -> str:
    """The install-time tier of a source kind: only a registry record is ``listed``; everything else is ``unlisted``."""
    return TIER_LISTED if kind == "registry" else TIER_UNLISTED


def tier_of(record: dict[str, Any] | None, *, mod_id: str, version: str, compat_row: Any | None = None) -> str:
    """The tier of an installed mod from its ``source.json`` record and the matrix row for the running host.

    ``record`` is what :func:`floofy_core.modstore.read_source` returns (``{}`` for
    a mod without a record — a ``floofy dev`` link, or a directory copied in by
    hand — which is ``unlisted``); records written before the tier existed are
    graded by their ``source`` kind. ``compat_row`` is a
    :class:`floofy_core.compat.CompatRow` (or anything with ``verdict(mod_id,
    version)``); a ``tested`` cell lifts ``listed`` to ``tested`` and nothing else.
    """
    base = record.get("tier") if isinstance(record, dict) else None
    if base not in TIERS:
        base = tier_for_source_kind(record.get("source") if isinstance(record, dict) else None)
    if base == TIER_LISTED and compat_row is not None:
        verdict = compat_row.verdict(mod_id, version) if hasattr(compat_row, "verdict") else None
        if verdict == "tested":
            return TIER_TESTED
    return base


def tier_for_candidate(verdict: str | None) -> str:
    """The tier a registry install would have here: ``tested`` when the graded verdict is ``tested``, else ``listed``."""
    return TIER_TESTED if verdict == "tested" else TIER_LISTED


def describe(tier: str) -> str:
    """One line a user can read next to the tier."""
    return _DESCRIPTIONS.get(tier, "unknown source tier")
