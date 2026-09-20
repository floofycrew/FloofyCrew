"""registry-tools — build, sign and validate FloofyCrew mod registries (Requirement 8.1, 8.3, 8.6, 8.7).

One tool for both registries: it takes the registry's identity (``source`` label,
signing key, archive URL base) from the registry repository's own
``registry.json`` and never hard-codes either edition. Standard library plus
``floofy_core`` (itself stdlib-only), so it runs in CI on a bare Python 3.12.

Modules:

* :mod:`registry_tools.repo` — the registry repository layout
  (``mods/<id>/<version>/{floofy.json, release.json}``, ``mods/<id>/mod.json``);
* :mod:`registry_tools.build` — ``index.json`` from the records, the matrix cells
  folded in from ``compat.json``, schema-checked;
* :mod:`registry_tools.submission` — the CI gate for a pull request: schema,
  ``floofy validate`` with zero errors, every ``files[]`` hash, licence in the
  manifest **and** as a file, no host-name masquerade (homoglyph-normalised), the
  release tag equal to the version, the record consistent with the archive;
* :mod:`registry_tools.masquerade` — the normalisation behind that check;
* :mod:`registry_tools.compat_merge` — add or replace a ``compat.json`` row from a
  Forge run file;
* :mod:`registry_tools.app_registry` — the KiroCrew-compatible ``app-registry.json``
  for app-kind mods (task 7.4);
* :mod:`registry_tools.cli` — ``python -m registry_tools <command>``.
"""
from __future__ import annotations

__version__ = "0.1.0"
