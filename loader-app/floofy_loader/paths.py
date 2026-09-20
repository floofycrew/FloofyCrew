"""The FloofyCrew data home layout (design "Data layout on the user machine").

Everything the Loader reads or writes lives under ``<host home>/floofy/``;
nothing is ever written under a host payload root (Requirement 3.7).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["FloofyPaths"]


@dataclass(frozen=True)
class FloofyPaths:
    """Paths under one data home. Directories are created lazily by :meth:`ensure`."""

    data_home: Path

    @property
    def consent(self) -> Path:
        return self.data_home / "consent.json"

    @property
    def mods(self) -> Path:
        return self.data_home / "mods"

    @property
    def spa(self) -> Path:
        return self.data_home / "spa"

    @property
    def pending(self) -> Path:
        return self.data_home / "pending"

    @property
    def quarantine(self) -> Path:
        return self.data_home / "quarantine"

    @property
    def quarantine_requests(self) -> Path:
        """Markers the Loader writes for the manager (the Loader never moves mod dirs)."""
        return self.quarantine / "requests"

    @property
    def deploy(self) -> Path:
        return self.data_home / "deploy"

    @property
    def cache(self) -> Path:
        return self.data_home / "cache"

    @property
    def compat_cache(self) -> Path:
        return self.cache / "compat.json"

    @property
    def enabled(self) -> Path:
        return self.data_home / "enabled.json"

    @property
    def audit(self) -> Path:
        return self.data_home / "audit.jsonl"

    @property
    def early(self) -> Path:
        return self.data_home / "early.json"

    @property
    def early_log(self) -> Path:
        return self.data_home / "early.log"

    @property
    def loader_state(self) -> Path:
        return self.data_home / "loader-state.json"

    @property
    def loader_failure(self) -> Path:
        return self.data_home / "loader-failure.json"

    def mod_dir(self, mod_id: str) -> Path:
        return self.mods / mod_id

    def spa_dir(self, mod_id: str) -> Path:
        return self.spa / mod_id

    def ensure(self) -> "FloofyPaths":
        for directory in (self.data_home, self.mods, self.spa, self.pending, self.quarantine, self.deploy, self.cache):
            directory.mkdir(parents=True, exist_ok=True)
        return self
