"""The FloofyCrew data home layout — the core's :class:`floofy_core.datahome.DataHome`.

Everything the Loader reads or writes lives under ``<host home>/floofy/``;
nothing is ever written under a host payload root (Requirement 3.7). The layout
is defined once in the shared core (the ``floofy`` CLI and the Forge use the
same class); ``FloofyPaths`` is the Loader's historical name for it.
"""
from __future__ import annotations

from floofy_core.datahome import DataHome

__all__ = ["FloofyPaths"]

FloofyPaths = DataHome
