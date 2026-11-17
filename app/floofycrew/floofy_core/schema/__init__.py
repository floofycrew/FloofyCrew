"""The FloofyCrew JSON Schemas and their loader (Requirement 1.9, 8.1, 9.1).

Five schemas ship with the core, all JSON Schema draft 2020-12:

* ``floofy`` — the mod manifest ``floofy.json`` (schema 1, Requirement 1).
* ``patch`` — the patch descriptor referenced by ``kind: patch`` parts
  (design "Data Models → Patch descriptor", Requirement 5.5).
* ``index`` — a registry's ``index.json`` (design "Registry index", Requirement 8.1).
* ``compat`` — the compatibility matrix ``compat.json`` (Requirement 9.1).
* ``signature`` — the detached ``*.sig`` document beside ``index.json`` /
  ``compat.json`` (Requirement 8.3; :mod:`floofy_core.sigverify`).

They are data files inside this package so the stdlib-only CLI, the Loader and
the registry tools all read the same bytes. See ``README.md`` next to them for
the field reference.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..resources import read_package_text

#: Directory holding the ``*.schema.json`` files.
SCHEMA_DIR: Path = Path(__file__).resolve().parent

#: Short names accepted by :func:`load_schema`, mapped to their file names.
SCHEMA_FILES: dict[str, str] = {
    "floofy": "floofy.schema.json",
    "patch": "patch.schema.json",
    "index": "index.schema.json",
    "compat": "compat.schema.json",
    "signature": "signature.schema.json",
}

#: ``$id`` values, useful for embedding in generated documents.
SCHEMA_IDS: dict[str, str] = {
    "floofy": "https://floofycrew.dev/schema/floofy-1.json",
    "patch": "https://floofycrew.dev/schema/patch-1.json",
    "index": "https://floofycrew.dev/schema/index-1.json",
    "compat": "https://floofycrew.dev/schema/compat-1.json",
    "signature": "https://floofycrew.dev/schema/signature-1.json",
}


def schema_path(name: str) -> Path:
    """Return the on-disk path of a schema by short name or file name.

    ``name`` is ``"floofy"``/``"patch"`` or the file name itself
    (``"floofy.schema.json"``). Unknown names raise ``KeyError``.
    """
    if name in SCHEMA_FILES:
        return SCHEMA_DIR / SCHEMA_FILES[name]
    if name in SCHEMA_FILES.values():
        return SCHEMA_DIR / name
    raise KeyError(f"unknown schema {name!r}; known: {sorted(SCHEMA_FILES)}")


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict[str, Any]:
    """Return the parsed schema (Requirement 1.9).

    The result is cached and shared: treat it as read-only.
    """
    path = schema_path(name)
    # importlib.resources reads the schema from a directory install and from inside the zipapp alike
    document = json.loads(read_package_text(__package__, path.name, fallback=path))
    if not isinstance(document, dict):
        raise ValueError(f"schema {name!r} is not a JSON object")
    return document


__all__ = ["SCHEMA_DIR", "SCHEMA_FILES", "SCHEMA_IDS", "load_schema", "schema_path"]
