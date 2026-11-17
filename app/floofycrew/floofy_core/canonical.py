"""Canonical JSON — the bytes a detached registry signature is computed over (Requirement 8.3).

Design "Registry index and compat.json": *detached signature over canonical JSON
(sorted keys, no whitespace)*. The form is the one ``json.dumps`` produces with
``sort_keys=True``, ``separators=(",", ":")``, ``ensure_ascii=False`` and
``allow_nan=False``, encoded as UTF-8 — named ``json-c14n-1`` in the signature
document so a future canonicalisation can coexist. Signing and verifying both
re-derive the bytes from the *parsed* document, so the whitespace and key order
of the served ``index.json`` never matter and a pretty-printed index verifies
against a signature made over a compact one.

Only JSON values are accepted (``dict``, ``list``, ``str``, ``int``, ``float``,
``bool``, ``None``); non-string keys and NaN/Infinity raise so an unsignable
document is refused rather than silently coerced.
"""
from __future__ import annotations

import json
from typing import Any

__all__ = ["CANONICAL_FORM", "CanonicalError", "canonical_bytes", "canonical_bytes_of_json"]

#: The identifier written into signature documents (``"canonical"``).
CANONICAL_FORM = "json-c14n-1"


class CanonicalError(ValueError):
    """The value is not a canonicalisable JSON document."""


def _check(value: Any, path: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalError(f"{path or '/'}: object key {key!r} is not a string")
            _check(item, f"{path}/{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _check(item, f"{path}/{index}")
    elif isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise CanonicalError(f"{path or '/'}: {value!r} has no JSON representation")
    elif value is not None and not isinstance(value, (str, int, bool)):
        raise CanonicalError(f"{path or '/'}: {type(value).__name__} is not a JSON value")


def canonical_bytes(document: Any) -> bytes:
    """The ``json-c14n-1`` encoding of a parsed JSON document."""
    _check(document, "")
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def canonical_bytes_of_json(raw: bytes | str) -> bytes:
    """Parse serialized JSON and return its canonical bytes (``CanonicalError`` when it is not JSON)."""
    try:
        document = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise CanonicalError(f"not a JSON document: {exc}") from exc
    return canonical_bytes(document)
