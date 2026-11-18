"""Kind ``config`` (Requirement 2.2): host ``config.json`` keys with ``kirocrew config set`` semantics, previous values recorded.

The part carries its key/value pairs inline (``values``) and/or in a JSON file
(``path``, an object of dotted key → value). Install applies them the way the
host's setter does — dotted keys, intermediate objects created, one locked
read-modify-write of ``<host home>/config.json`` (``cli_config.py`` L41–L235
``_config_cmd``, ``config/loader.py`` L1460 ``update_config_locked``) — through
the host launcher (``kirocrew config set <key> <json>``, which also validates the
key against the host's schema) when one is available, else through the
byte-equivalent direct edit in :mod:`floofy_core.hostcli`. The previous value of
every key (or ``"<absent>"``) is recorded in
``mods/<id>/.floofy/config-previous.json`` so uninstall restores it exactly;
restoring an absent key deletes it, which ``config set`` cannot express, so the
restore always uses the direct edit.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..hostcli import config_get, config_set_many, run_host_cli
from . import SEAMS, KindContext, PartOutcome

__all__ = ["ConfigHandler", "PREVIOUS_FILE"]

PREVIOUS_FILE = "config-previous.json"


def _values(mod_dir: Path, part: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    path = part.get("path")
    if isinstance(path, str) and path:
        document = json.loads((Path(mod_dir) / path).read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError(f"{path}: config values must be a JSON object of dotted key -> value")
        values.update(document)
    inline = part.get("values")
    if isinstance(inline, dict):
        values.update(inline)
    if not values:
        raise ValueError("config part declares no values")
    return values


def _previous_path(mod_dir: Path, index: int) -> Path:
    return Path(mod_dir) / ".floofy" / (PREVIOUS_FILE if index == 0 else f"config-previous-{index}.json")


@dataclass
class ConfigHandler:
    kind: str = "config"
    modifies_payload: bool = False

    @property
    def seam(self) -> str:
        return SEAMS["config"]

    def _config_path(self, ctx: KindContext) -> Path:
        return Path(ctx.host_home) / "config.json"

    def install(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome:
        outcome = PartOutcome(self.kind, index, self.seam, False)
        try:
            values = _values(Path(mod_dir), part)
        except (OSError, ValueError) as exc:
            outcome.ok, outcome.status, outcome.detail = False, "error", str(exc)
            return outcome
        config_path = self._config_path(ctx)
        previous_path = _previous_path(mod_dir, index)
        if previous_path.is_file():
            # re-install/update: the first recorded originals stand (they are the pre-mod values)
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
        else:
            previous = {key: config_get(config_path, key) for key in values}
        via = "direct-edit"
        failures: list[str] = []
        if ctx.launcher is not None and not ctx.in_gateway:
            via = "kirocrew config set"
            for key, value in values.items():
                result = run_host_cli(ctx.launcher, ["config", "set", key, json.dumps(value)], host_home=ctx.host_home, extra_env=ctx.host_cli_env)
                if not result.ok:
                    failures.append(f"{key}: {result.output[-200:]}")
            if failures:
                ctx.warn(f"config: the host refused {len(failures)} key(s) via `kirocrew config set` ({'; '.join(failures)}); applying the rest directly")
                config_set_many(config_path, values)
                via = "kirocrew config set + direct-edit"
        else:
            config_set_many(config_path, values)
        previous_path.parent.mkdir(parents=True, exist_ok=True)
        previous_path.write_text(json.dumps(previous, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        outcome.files = [str(config_path)]
        outcome.detail = f"set {', '.join(sorted(values))} in {config_path} via {via}; previous values recorded in {previous_path.name}"
        outcome.extra.update({"values": values, "previous": previous, "via": via, "refused": failures})
        ctx.record("config-set", mod=mod_id, files=outcome.files, result="ok", detail=outcome.detail, keys=sorted(values))
        return outcome

    def uninstall(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome:
        outcome = PartOutcome(self.kind, index, self.seam, False, status="removed")
        previous_path = _previous_path(mod_dir, index)
        try:
            previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.is_file() else None
        except (OSError, ValueError):
            previous = None
        if not isinstance(previous, dict):
            outcome.status, outcome.detail = "skipped", "no record of previous values; config.json left as is"
            return outcome
        config_path = self._config_path(ctx)
        edit = config_set_many(config_path, previous)
        previous_path.unlink(missing_ok=True)
        outcome.files = [str(config_path)]
        outcome.detail = f"restored {', '.join(sorted(previous))} in {config_path} ({edit.detail})"
        outcome.extra["restored"] = previous
        ctx.record("config-restore", mod=mod_id, files=outcome.files, result="ok", detail=outcome.detail, keys=sorted(previous))
        return outcome

    def status(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome:
        outcome = PartOutcome(self.kind, index, self.seam, False)
        try:
            values = _values(Path(mod_dir), part)
        except (OSError, ValueError) as exc:
            outcome.status, outcome.detail = "error", str(exc)
            return outcome
        config_path = self._config_path(ctx)
        current = {key: config_get(config_path, key) for key in values}
        applied = all(current[key] == value for key, value in values.items())
        outcome.status = "present" if applied else "absent"
        outcome.detail = "every key holds the mod's value" if applied else "some keys differ from the mod's values: " + ", ".join(k for k, v in values.items() if current[k] != v)
        outcome.extra.update({"values": values, "current": current, "recorded": _previous_path(mod_dir, index).is_file()})
        return outcome
