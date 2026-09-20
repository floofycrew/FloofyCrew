"""The FloofyCrew data home layout (design "Data layout on the user machine").

Everything FloofyCrew reads or writes on the user's machine lives under
``<host home>/floofy/``; nothing is ever written under a host payload root
(Requirement 3.7). One definition, shared by the ``floofy`` CLI, the Loader
(``floofy_loader.paths.FloofyPaths`` is this class) and the Forge::

    <host home>/floofy/
      consent.json            one-time acknowledgement (Requirement 11.1)
      mods/<id>/              unpacked mods; <id>/.floofy/ is runtime state (config, log, source)
      enabled.json            {"<id>": true|false}
      pending/<id>/ | <id>.remove   staged installs / removals applied at gateway start (Requirement 7.6)
      pending/self-update/ + pending/self-update.json   a verified Loader app archive from `floofy self-update`, installed when no gateway runs (Requirement 7.7)
      config.json             FloofyCrew's own settings (`floofy config`; today `updates.check`)
      spa/<id>/               spa part files of active mods (served by the Loader)
      quarantine/<hostver>/<id>/    yeeted mods + enabled.json with their flags (Requirement 6.2)
      quarantine/requests/<id>.json  the Loader asks the manager to yeet
      deploy/<payload>.json   deployment manifests (Requirement 5.2)
      registries.json         sources + per-source trust (Requirement 8.4)
      cache/{index,compat}.json      merged registry caches; cache/sources/<key>/ per source
      cache/self-update.json  the daily FloofyCrew release check (Requirement 7.7)
      cache/sources/git/<key>/       shallow clones of git references and link records (Requirement 8.8, 8.9)
      profiles/<name>.lock.json      named mod sets (Requirement 7.5)
      audit.jsonl             every mutating operation (Requirement 11.5)
      early.json              early-activation list (optional, Requirement 3.2)
      loader-state.json / loader-failure.json   what the Loader published / why it failed
      host-state.json         the payload set seen by the last apply --if-changed (Requirement 6.2)
      hostchange-<ver>.json   the outcome of a host-version change, for the UI
      vanilla-once            marker: the next gateway boot disables every mod once (floofy --vanilla)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

__all__ = ["DataHome", "default_host_home"]

#: The host's own default data home; ``KIROCREW_HOME`` overrides it (``kiro_crew/config/paths.py`` ``config_dir``).
DEFAULT_HOST_HOME = Path.home() / ".kiro" / "crew"


def default_host_home(env: dict[str, str] | None = None) -> Path:
    """``KIROCREW_HOME`` or ``~/.kiro/crew`` — the host's rule without importing the host."""
    environment = os.environ if env is None else env
    override = (environment.get("KIROCREW_HOME") or "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_HOST_HOME


@dataclass(frozen=True)
class DataHome:
    """Paths under one data home. Directories are created lazily by :meth:`ensure`."""

    data_home: Path

    @classmethod
    def for_host_home(cls, host_home: Path) -> "DataHome":
        return cls(Path(host_home) / "floofy")

    @property
    def host_home(self) -> Path:
        return self.data_home.parent

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

    def staged_mods(self) -> list[str]:
        """The staged mod entries of ``pending/`` — ``<id>/`` (a ``floofy.json`` inside) and ``<id>.remove`` markers.

        FloofyCrew's own staged self-update (``pending/self-update/`` and its
        marker ``pending/self-update.json``, task 10.7) is not a mod and is left out.
        """
        if not self.pending.is_dir():
            return []
        names: list[str] = []
        for entry in sorted(self.pending.iterdir()):
            if entry.is_dir() and (entry / "floofy.json").is_file():
                names.append(entry.name)
            elif entry.is_file() and entry.name.endswith(".remove"):
                names.append(entry.name)
        return names

    @property
    def pending_self_update(self) -> Path:
        """``pending/self-update.json``: the staged Loader app update of ``floofy self-update`` (task 10.7)."""
        return self.pending / "self-update.json"

    @property
    def settings_file(self) -> Path:
        """``config.json``: FloofyCrew's own settings (``floofy config``; :mod:`floofy_core.settings`)."""
        return self.data_home / "config.json"

    @property
    def self_update_cache(self) -> Path:
        """``cache/self-update.json``: the daily FloofyCrew release check (:mod:`floofy_core.selfupdate`)."""
        return self.cache / "self-update.json"

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
    def index_cache(self) -> Path:
        return self.cache / "index.json"

    @property
    def cache_sources(self) -> Path:
        return self.cache / "sources"

    @property
    def cache_git(self) -> Path:
        """Shallow clones of git references and link records, one directory per repository + pinned point (Requirement 8.8, 8.9)."""
        return self.cache_sources / "git"

    @property
    def registries(self) -> Path:
        return self.data_home / "registries.json"

    @property
    def profiles(self) -> Path:
        return self.data_home / "profiles"

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

    @property
    def host_state(self) -> Path:
        return self.data_home / "host-state.json"

    @property
    def vanilla_marker(self) -> Path:
        return self.data_home / "vanilla-once"

    def hostchange(self, host_version: str) -> Path:
        return self.data_home / f"hostchange-{host_version}.json"

    def mod_dir(self, mod_id: str) -> Path:
        return self.mods / mod_id

    def mod_runtime_dir(self, mod_id: str) -> Path:
        """``mods/<id>/.floofy/`` — config, log and the install source record (invisible to ``floofy validate``)."""
        return self.mod_dir(mod_id) / ".floofy"

    def spa_dir(self, mod_id: str) -> Path:
        return self.spa / mod_id

    def quarantine_dir(self, host_version: str) -> Path:
        return self.quarantine / host_version

    def profile(self, name: str) -> Path:
        return self.profiles / f"{name}.lock.json"

    def ensure(self) -> "DataHome":
        for directory in (self.data_home, self.mods, self.spa, self.pending, self.quarantine, self.deploy, self.cache):
            directory.mkdir(parents=True, exist_ok=True)
        return self
