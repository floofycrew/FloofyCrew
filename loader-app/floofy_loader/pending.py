"""Staged installs and removals applied at gateway start (Requirement 7.6, design boot step 4).

The manager stages into ``<data home>/pending/`` while a gateway may be running
(ModAssistant's ``IPA/Pending`` pattern); the Loader applies the stage before it
scans ``mods/``. Stage format (the manager, task 6.x, writes exactly this):

* ``pending/<id>/`` — a complete unpacked mod directory (``floofy.json`` at its
  root). Applied by replacing ``mods/<id>/`` with it.
* ``pending/<id>.remove`` — a marker file (content ignored) asking for
  ``mods/<id>/`` and ``spa/<id>/`` to be removed and the ``enabled.json`` entry
  dropped.

Both are applied atomically per mod (rename into place; the previous directory
is moved aside first and deleted last). Enabled state is **not** staged:
``enabled.json`` is a flag file the manager edits directly.

After the stage is applied, the ``patch`` parts of every enabled mod are handed
to the Patcher (revert-then-patch, idempotent — Requirement 5.4) when anything
changed or when a payload has no deployment manifest yet (a fresh host version).
The Loader is one of the re-apply triggers (Requirement 6.1). Inside the
gateway there is no one to ask, so the Patcher runs pre-consented
(``AlwaysConfirm``, ``triggers_installed=True``) and without the live
``verify`` (the Loader *is* the gateway; the CLI verifies).
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .paths import FloofyPaths

__all__ = ["PendingReport", "apply_pending", "read_enabled", "write_enabled"]

REMOVE_SUFFIX = ".remove"


@dataclass
class PendingReport:
    installed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.installed or self.removed)

    def to_dict(self) -> dict[str, Any]:
        return {"installed": list(self.installed), "removed": list(self.removed), "errors": list(self.errors), "changed": self.changed}


def read_enabled(path: Path) -> dict[str, bool]:
    """``enabled.json`` → ``{id: bool}``; anything unreadable is an empty map."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(document, dict):
        return {}
    return {str(k): bool(v) for k, v in document.items()}


def write_enabled(path: Path, enabled: dict[str, bool]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(path).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(dict(sorted(enabled.items())), indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _replace_dir(source: Path, target: Path) -> None:
    """Move ``source`` to ``target`` atomically with respect to readers of ``target``."""
    aside = target.with_name(target.name + ".floofy-old")
    if aside.exists():
        shutil.rmtree(aside)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.rename(aside)
    try:
        source.rename(target)
    except OSError:
        shutil.move(str(source), str(target))  # across filesystems
    if aside.exists():
        shutil.rmtree(aside, ignore_errors=True)


def apply_pending(paths: FloofyPaths, *, log: Callable[[str], None] | None = None) -> PendingReport:
    """Apply every staged install and removal; errors are collected, never raised."""
    say = log or (lambda _m: None)
    report = PendingReport()
    pending = paths.pending
    if not pending.is_dir():
        return report
    for entry in sorted(pending.iterdir()):
        try:
            if entry.is_dir() and (entry / "floofy.json").is_file():
                mod_id = _staged_id(entry)
                _replace_dir(entry, paths.mod_dir(mod_id))
                report.installed.append(mod_id)
                say(f"pending: installed {mod_id}")
            elif entry.is_file() and entry.name.endswith(REMOVE_SUFFIX):
                mod_id = entry.name[: -len(REMOVE_SUFFIX)]
                for victim in (paths.mod_dir(mod_id), paths.spa_dir(mod_id)):
                    if victim.exists():
                        shutil.rmtree(victim)
                enabled = read_enabled(paths.enabled)
                if mod_id in enabled:
                    del enabled[mod_id]
                    write_enabled(paths.enabled, enabled)
                entry.unlink()
                report.removed.append(mod_id)
                say(f"pending: removed {mod_id}")
            elif entry.is_dir() and entry.name.endswith(".floofy-old"):
                shutil.rmtree(entry, ignore_errors=True)
        except (OSError, ValueError) as exc:
            report.errors.append(f"{entry.name}: {type(exc).__name__}: {exc}")
    return report


def _staged_id(directory: Path) -> str:
    """The staged mod's id: the manifest's ``id`` must match the directory name (defence against a mis-staged dir)."""
    manifest = json.loads((directory / "floofy.json").read_text(encoding="utf-8"))
    mod_id = manifest.get("id") if isinstance(manifest, dict) else None
    if not isinstance(mod_id, str) or mod_id != directory.name:
        raise ValueError(f"staged directory {directory.name!r} holds manifest id {mod_id!r}")
    return mod_id


def plan_patches(mod_dirs: Iterable[tuple[str, Path, dict[str, Any]]]):
    """``PlannedPatch`` for every ``patch`` part of the given (id, dir, manifest) mods; descriptor errors are returned as strings."""
    from floofy_core.patcher import PlannedPatch  # noqa: PLC0415 - keeps import cost off the no-patch path
    from floofy_core.patches import PatchDescriptor, PatchDescriptorError  # noqa: PLC0415

    planned: list[PlannedPatch] = []
    problems: list[str] = []
    for mod_id, mod_dir, manifest in mod_dirs:
        for index, part in enumerate(manifest.get("parts") or []):
            if not isinstance(part, dict) or part.get("kind") != "patch":
                continue
            try:
                planned.append(PlannedPatch(mod_id, str(index), PatchDescriptor.load(mod_dir / str(part.get("path", "")))))
            except (PatchDescriptorError, OSError, ValueError) as exc:
                problems.append(f"{mod_id}#{index}: {exc}")
    return planned, problems
