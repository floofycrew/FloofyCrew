"""FloofyCrew's own user settings — ``<data home>/config.json`` (task 10.7; Requirement 7.7).

A small nested JSON document the user edits through ``floofy config get|set``;
every key has a safe default and a type, and only known keys can be set (a typo
never lands silently). Nothing here is host configuration: the host's
``config.json`` lives in the host home and is a *seam* (the ``config`` mod kind).

Known keys:

* ``updates.check`` (bool, default ``true``) — the daily check for a newer
  FloofyCrew release (``floofy_core.selfupdate``). ``floofy config set
  updates.check false`` switches it off; the check is a plain GET that sends no
  identifier, but it is the user's call whether FloofyCrew talks to the release
  endpoint at all (design DR-5: user controls with safe defaults).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["CONFIG_NAME", "KNOWN_SETTINGS", "Settings", "SettingsError", "parse_value"]

CONFIG_NAME = "config.json"

#: key → (type, default, description)
KNOWN_SETTINGS: dict[str, tuple[type, Any, str]] = {
    "updates.check": (bool, True, "check once a day for a newer FloofyCrew release supporting this host (GET, no identifiers; off: never contact the release endpoint)"),
}


class SettingsError(ValueError):
    """An unknown key or a value of the wrong type."""


def parse_value(text: str, kind: type) -> Any:
    """``"true"``/``"false"``/``"1"``/``"0"``/``"on"``/``"off"``/``"yes"``/``"no"`` for a bool, ``int()`` for an int, the text otherwise."""
    if kind is bool:
        lowered = text.strip().lower()
        if lowered in ("true", "1", "on", "yes"):
            return True
        if lowered in ("false", "0", "off", "no"):
            return False
        raise SettingsError(f"expected true or false, got {text!r}")
    if kind is int:
        try:
            return int(text.strip())
        except ValueError as exc:
            raise SettingsError(f"expected an integer, got {text!r}") from exc
    return text


@dataclass
class Settings:
    """The document as nested dicts; :meth:`get`/:meth:`set` speak dotted keys."""

    path: Path
    document: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Settings":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        return cls(Path(path), raw if isinstance(raw, dict) else {})

    @classmethod
    def for_data_home(cls, data_home: Path) -> "Settings":
        return cls.load(Path(data_home) / CONFIG_NAME)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def raw(self, key: str) -> Any:
        """The stored value for a dotted key, or ``None`` when unset."""
        node: Any = self.document
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    def get(self, key: str) -> Any:
        """The effective value: the stored one when it has the right type, else the default."""
        if key not in KNOWN_SETTINGS:
            raise SettingsError(f"unknown setting {key!r} (known: {', '.join(sorted(KNOWN_SETTINGS))})")
        kind, default, _ = KNOWN_SETTINGS[key]
        value = self.raw(key)
        return value if isinstance(value, kind) and not (kind is not bool and isinstance(value, bool)) else default

    def set(self, key: str, value: Any) -> Any:
        """Store ``value`` (a string is parsed for the key's type); returns the stored value. Does not save."""
        if key not in KNOWN_SETTINGS:
            raise SettingsError(f"unknown setting {key!r} (known: {', '.join(sorted(KNOWN_SETTINGS))})")
        kind, _default, _ = KNOWN_SETTINGS[key]
        if isinstance(value, str):
            value = parse_value(value, kind)
        if not isinstance(value, kind) or (kind is not bool and isinstance(value, bool)):
            raise SettingsError(f"{key} expects {kind.__name__}, got {type(value).__name__}")
        node = self.document
        parts = key.split(".")
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value
        return value

    def effective(self) -> dict[str, Any]:
        """Every known key with its effective value."""
        return {key: self.get(key) for key in sorted(KNOWN_SETTINGS)}
