"""Read a package's bundled data file wherever the package lives.

``floofy`` ships as a directory (the Loader app, a wheel) *and* as a single-file
zipapp (``dist/floofy.pyz``). ``Path(__file__).with_name(...)`` only works in the
first case: inside the zipapp ``__file__`` names a member of the archive and
``read_text`` raises ``FileNotFoundError``. :mod:`importlib.resources` reads both,
so every bundled JSON the core and the adapters carry goes through here
(``anchors.json``, the schemas, each adapter's ``registry.json``).

Standard library only, like the rest of the core.
"""
from __future__ import annotations

from importlib import resources
from pathlib import Path

__all__ = ["read_package_text"]


def read_package_text(package: str, name: str, *, fallback: Path | None = None) -> str:
    """The text of ``name`` bundled in ``package``.

    ``fallback`` is the plain filesystem path the caller used to read (kept for
    callers running from a checkout with an unusual layout); it is tried only when
    the package resource cannot be read.
    """
    try:
        return resources.files(package).joinpath(name).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, TypeError, OSError):
        if fallback is not None and fallback.is_file():
            return fallback.read_text(encoding="utf-8")
        raise
