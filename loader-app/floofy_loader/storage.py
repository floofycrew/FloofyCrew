"""Per-mod config storage and logger (Requirement 3.7).

Both live under the mod's own directory in the data home,
``<data home>/mods/<id>/.floofy/`` — a dot-directory the validator ignores
(``floofy_core.validator._shipped_files`` skips dotted path parts), so runtime
files never show up as unlisted mod files. Nothing here can land under a host
payload root: :func:`mod_data_dir` asserts containment in the data home and
refuses any path under the running payload.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

__all__ = ["ModConfig", "RUNTIME_DIR", "mod_data_dir", "mod_logger"]

#: The dot-directory inside ``mods/<id>/`` holding runtime files (ignored by the validator).
RUNTIME_DIR = ".floofy"
LOG_MAX_BYTES = 256 * 1024
LOG_BACKUPS = 1


def mod_data_dir(data_home: Path, mod_id: str, *, payload_root: Path | None = None) -> Path:
    """``<data home>/mods/<id>/.floofy/`` — created, contained in the data home, never under a payload.

    A ``floofy dev`` link (``mods/<id>`` → a checkout) is honoured: the runtime
    state then lives in the checkout's ``.floofy/`` (the developer's own tree),
    still never under a host payload.
    """
    if not mod_id or mod_id in (".", "..") or "/" in mod_id or "\\" in mod_id:
        raise ValueError(f"not a mod id: {mod_id!r}")
    home = Path(data_home).resolve()
    mod_root = home / "mods" / mod_id
    target = (mod_root / RUNTIME_DIR).resolve()
    linked = mod_root.is_symlink() and mod_root.resolve().is_dir()
    if not linked and target.parent.parent != home / "mods":
        raise ValueError(f"mod data dir {target} escapes {home / 'mods'}")
    if linked and target.parent != mod_root.resolve():
        raise ValueError(f"mod data dir {target} escapes the linked mod directory {mod_root.resolve()}")
    if payload_root is not None and target.is_relative_to(Path(payload_root).resolve()):
        raise ValueError(f"mod data dir {target} lies under the host payload {payload_root}")
    target.mkdir(parents=True, exist_ok=True)
    return target


@dataclass
class ModConfig:
    """A small JSON key/value store per mod (``ctx.config``), written atomically."""

    data_home: Path
    mod_id: str
    payload_root: Path | None = None
    _cache: dict[str, Any] | None = field(default=None, repr=False)
    #: ``(mtime_ns, size)`` of the file the cache was read from; another writer (the App's config route, task 11.5)
    #: changes it, and the next read picks the file up again so ``ctx.config`` and ``floofy.mod(id).config`` agree.
    _stamp: tuple[int, int] | None = field(default=None, repr=False)

    @property
    def path(self) -> Path:
        return mod_data_dir(self.data_home, self.mod_id, payload_root=self.payload_root) / "config.json"

    def _signature(self) -> tuple[int, int] | None:
        try:
            info = self.path.stat()
        except OSError:
            return None
        return (info.st_mtime_ns, info.st_size)

    def _load(self) -> dict[str, Any]:
        signature = self._signature()
        if self._cache is None or signature != self._stamp:
            try:
                document = json.loads(self.path.read_text(encoding="utf-8"))
                self._cache = dict(document) if isinstance(document, dict) else {}
            except (OSError, ValueError):
                self._cache = {}
            self._stamp = signature
        return self._cache

    def _save(self) -> None:
        path = self.path
        payload = json.dumps(self._load(), indent=2, sort_keys=True, default=str) + "\n"
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp, path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._stamp = self._signature()

    def get(self, key: str, default: Any = None) -> Any:
        return self._load().get(key, default)

    def set(self, key: str, value: Any) -> None:
        json.dumps(value)  # must be JSON-representable (raises TypeError otherwise)
        self._load()[key] = value
        self._save()

    def update(self, values: dict[str, Any]) -> None:
        self._load().update(values)
        self._save()

    def delete(self, key: str) -> bool:
        present = key in self._load()
        if present:
            del self._load()[key]
            self._save()
        return present

    def clear(self) -> None:
        self._cache = {}
        self._save()

    def items(self) -> Iterator[tuple[str, Any]]:
        return iter(sorted(self._load().items()))

    def to_dict(self) -> dict[str, Any]:
        return dict(self._load())

    def __contains__(self, key: object) -> bool:
        return key in self._load()

    def __getitem__(self, key: str) -> Any:
        return self._load()[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)


class _PrefixFilter(logging.Filter):
    """Prefix every record of a mod logger with ``[floofy:<id>]`` once, so the gateway log attributes it."""

    def __init__(self, mod_id: str):
        super().__init__()
        self.prefix = f"[floofy:{mod_id}] "

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "floofy_prefixed", False):
            record.msg = f"{self.prefix}{record.msg}"
            record.floofy_prefixed = True  # type: ignore[attr-defined]
        return True


def mod_logger(data_home: Path, mod_id: str, *, payload_root: Path | None = None, level: int = logging.INFO) -> logging.Logger:
    """``logging.getLogger("floofy.mods.<id>")`` writing to ``mods/<id>/.floofy/mod.log`` (small, rotating) and propagating to the gateway log."""
    logger = logging.getLogger(f"floofy.mods.{mod_id}")
    logger.setLevel(level)
    logger.propagate = True
    if not any(isinstance(f, _PrefixFilter) for f in logger.filters):
        logger.addFilter(_PrefixFilter(mod_id))
    log_path = mod_data_dir(data_home, mod_id, payload_root=payload_root) / "mod.log"
    for handler in logger.handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler) and Path(handler.baseFilename) == log_path:
            return logger
    file_handler = logging.handlers.RotatingFileHandler(log_path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)
    return logger
