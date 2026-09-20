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
third depends on the host the mod sits on and is derived when shown.
"""
from __future__ import annotations

__all__ = ["TIERS", "TIER_LISTED", "TIER_TESTED", "TIER_UNLISTED", "tier_for_source_kind"]

TIER_UNLISTED = "unlisted"
TIER_LISTED = "listed"
TIER_TESTED = "tested"
TIERS = (TIER_UNLISTED, TIER_LISTED, TIER_TESTED)


def tier_for_source_kind(kind: str) -> str:
    """The install-time tier of a source kind: only a registry record is ``listed``; everything else is ``unlisted``."""
    return TIER_LISTED if kind == "registry" else TIER_UNLISTED
