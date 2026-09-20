"""The compatibility-matrix cache — re-exported from the shared core (Requirement 9.2).

The format is the registry's ``compat.json``; the reader lives in
:mod:`floofy_core.compat` so the Loader (boot step 7), the ``floofy`` CLI
(``doctor``, ``update``, host-version changes) and the Forge share it.
"""
from __future__ import annotations

from floofy_core.compat import VERDICTS, CompatCache, CompatRow

__all__ = ["CompatCache", "CompatRow", "VERDICTS"]
