"""Kinds ``agent``, ``skill``, ``appearance`` — official data drop-ins (Requirement 2.2, design DR-2 rung 1).

These land where the host already looks for user content, survive host updates
and touch no payload file:

* ``agent`` → ``<kiro home>/agents/<file>`` — the host reads agent specs from
  ``kiro_agents_dir()`` = ``kiro_home()/agents`` (``config/paths.py`` L786–L811;
  ``KIRO_HOME`` overrides ``~/.kiro``, L624). The part names one spec file
  (``.json`` or ``.md``); it is copied under its own name with a FloofyCrew
  marker sidecar so uninstall removes exactly what it installed.
* ``skill`` → ``<host home>/skills/<name>/`` — ``config_dir()/skills``
  (``apps/bridges.py`` L88 ``SKILLS_DIR_NAME``, L111 ``_skills_dir``;
  ``skills.py`` L61). The part names ``…/<name>/SKILL.md``; the whole skill
  directory is copied.
* ``appearance`` → ``<host home>/appearance-library/appearances/<id>/`` — the
  dashboard's crew library (``dashboard/appearances.py`` L49 ``LIBRARY_DIRNAME``,
  ``appearance_packs/store.py`` L46 ``PACKS_DIRNAME``); ``<id>`` is the pack
  manifest's ``id`` (else the directory name), a single safe path segment.

Every copy is a plain byte copy of regular files (symlinks refused), recorded in
the part outcome; the ``.floofy-owner`` sidecar next to a skill/appearance
directory (or the agent file) carries the owning mod id so a directory a user
created is never removed by an uninstall.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import SEAMS, KindContext, PartOutcome

__all__ = ["AgentHandler", "AppearanceHandler", "SkillHandler"]

OWNER_SUFFIX = ".floofy-owner"
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _owner_path(target: Path) -> Path:
    return target.with_name(target.name + OWNER_SUFFIX)


def _write_owner(target: Path, mod_id: str) -> None:
    _owner_path(target).write_text(json.dumps({"mod": mod_id}) + "\n", encoding="utf-8")


def _owned_by(target: Path, mod_id: str) -> bool:
    try:
        return json.loads(_owner_path(target).read_text(encoding="utf-8")).get("mod") == mod_id
    except (OSError, ValueError, AttributeError):
        return False


def _copy_dir(source: Path, target: Path) -> list[str]:
    if any(p.is_symlink() for p in source.rglob("*")):
        raise ValueError(f"{source} contains symlinks; refusing to copy")
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, symlinks=False, ignore=shutil.ignore_patterns("__pycache__", ".git"))
    return [str(p) for p in sorted(target.rglob("*")) if p.is_file()]


def _safe_segment(name: str) -> str:
    if not _SAFE_SEGMENT.match(name) or name in (".", ".."):
        raise ValueError(f"{name!r} is not a safe directory name")
    return name


@dataclass
class _DirDropin:
    """A directory copied under a host content root, owned by the mod (skills, appearances)."""

    kind: str
    modifies_payload: bool = False

    @property
    def seam(self) -> str:
        return SEAMS[self.kind]

    def root(self, ctx: KindContext) -> Path:
        raise NotImplementedError

    def source_and_name(self, mod_dir: Path, part: dict[str, Any]) -> tuple[Path, str]:
        raise NotImplementedError

    def install(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome:
        outcome = PartOutcome(self.kind, index, self.seam, False)
        try:
            source, name = self.source_and_name(Path(mod_dir), part)
            target = self.root(ctx) / _safe_segment(name)
            if target.exists() and not _owned_by(target, mod_id):
                outcome.ok, outcome.status, outcome.detail = False, "error", f"{target} exists and was not installed by {mod_id}; not overwriting it"
                return outcome
            target.parent.mkdir(parents=True, exist_ok=True)
            outcome.files = _copy_dir(source, target)
            _write_owner(target, mod_id)
        except (OSError, ValueError) as exc:
            outcome.ok, outcome.status, outcome.detail = False, "error", str(exc)
            return outcome
        outcome.detail = f"copied into {target}"
        outcome.extra["target"] = str(target)
        ctx.record(f"{self.kind}-install", mod=mod_id, files=[str(target)], result="ok", detail=outcome.detail)
        return outcome

    def uninstall(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome:
        outcome = PartOutcome(self.kind, index, self.seam, False, status="removed")
        try:
            _source, name = self.source_and_name(Path(mod_dir), part)
            target = self.root(ctx) / _safe_segment(name)
        except (OSError, ValueError) as exc:
            outcome.status, outcome.detail = "skipped", str(exc)
            return outcome
        if not target.exists():
            outcome.status, outcome.detail = "absent", f"{target} was not installed"
        elif not _owned_by(target, mod_id):
            outcome.status, outcome.detail = "skipped", f"{target} is not owned by {mod_id}; left in place"
        else:
            shutil.rmtree(target, ignore_errors=True)
            _owner_path(target).unlink(missing_ok=True)
            outcome.files = [str(target)]
            outcome.detail = f"removed {target}"
            ctx.record(f"{self.kind}-uninstall", mod=mod_id, files=outcome.files, result="ok", detail=outcome.detail)
        return outcome

    def status(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome:
        outcome = PartOutcome(self.kind, index, self.seam, False)
        try:
            _source, name = self.source_and_name(Path(mod_dir), part)
            target = self.root(ctx) / _safe_segment(name)
        except (OSError, ValueError) as exc:
            outcome.status, outcome.detail = "error", str(exc)
            return outcome
        outcome.extra["target"] = str(target)
        if target.exists():
            owned = _owned_by(target, mod_id)
            outcome.status = "present" if owned else "present-foreign"
            outcome.detail = f"installed at {target}" + ("" if owned else " (not owned by this mod)")
            outcome.files = [str(target)]
        else:
            outcome.status, outcome.detail = "absent", f"not installed ({target} missing)"
        return outcome


@dataclass
class SkillHandler(_DirDropin):
    kind: str = "skill"

    def root(self, ctx: KindContext) -> Path:
        return Path(ctx.host_home) / "skills"

    def source_and_name(self, mod_dir: Path, part: dict[str, Any]) -> tuple[Path, str]:
        skill_md = (mod_dir / str(part.get("path", ""))).resolve()
        if skill_md.name != "SKILL.md" or not skill_md.is_file():
            raise ValueError(f"skill part must name a SKILL.md file: {part.get('path')}")
        return skill_md.parent, skill_md.parent.name


@dataclass
class AppearanceHandler(_DirDropin):
    kind: str = "appearance"

    def root(self, ctx: KindContext) -> Path:
        return Path(ctx.host_home) / "appearance-library" / "appearances"

    def source_and_name(self, mod_dir: Path, part: dict[str, Any]) -> tuple[Path, str]:
        manifest_path = (mod_dir / str(part.get("path", ""))).resolve()
        if not manifest_path.is_file():
            raise ValueError(f"appearance manifest missing: {part.get('path')}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValueError(f"appearance manifest is not JSON: {exc}") from exc
        pack_id = manifest.get("id") if isinstance(manifest, dict) and isinstance(manifest.get("id"), str) else manifest_path.parent.name
        return manifest_path.parent, pack_id


@dataclass
class AgentHandler:
    """One agent spec file into ``<kiro home>/agents/``."""

    kind: str = "agent"
    modifies_payload: bool = False

    @property
    def seam(self) -> str:
        return SEAMS["agent"]

    def _target(self, ctx: KindContext, mod_dir: Path, part: dict[str, Any]) -> tuple[Path, Path]:
        source = (Path(mod_dir) / str(part.get("path", ""))).resolve()
        if not source.is_file() or source.suffix not in (".json", ".md"):
            raise ValueError(f"agent part must name a .json or .md spec file: {part.get('path')}")
        return source, Path(ctx.kiro_home) / "agents" / _safe_segment(source.name)

    def install(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome:
        outcome = PartOutcome(self.kind, index, self.seam, False)
        try:
            source, target = self._target(ctx, mod_dir, part)
            if target.exists() and not _owned_by(target, mod_id):
                outcome.ok, outcome.status, outcome.detail = False, "error", f"{target} exists and was not installed by {mod_id}; not overwriting it"
                return outcome
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            _write_owner(target, mod_id)
        except (OSError, ValueError) as exc:
            outcome.ok, outcome.status, outcome.detail = False, "error", str(exc)
            return outcome
        outcome.files = [str(target)]
        outcome.detail = f"copied to {target}"
        outcome.extra["target"] = str(target)
        ctx.record("agent-install", mod=mod_id, files=outcome.files, result="ok", detail=outcome.detail)
        return outcome

    def uninstall(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome:
        outcome = PartOutcome(self.kind, index, self.seam, False, status="removed")
        try:
            _source, target = self._target(ctx, mod_dir, part)
        except (OSError, ValueError) as exc:
            outcome.status, outcome.detail = "skipped", str(exc)
            return outcome
        if not target.exists():
            outcome.status, outcome.detail = "absent", f"{target} was not installed"
        elif not _owned_by(target, mod_id):
            outcome.status, outcome.detail = "skipped", f"{target} is not owned by {mod_id}; left in place"
        else:
            target.unlink()
            _owner_path(target).unlink(missing_ok=True)
            outcome.files, outcome.detail = [str(target)], f"removed {target}"
            ctx.record("agent-uninstall", mod=mod_id, files=outcome.files, result="ok", detail=outcome.detail)
        return outcome

    def status(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome:
        outcome = PartOutcome(self.kind, index, self.seam, False)
        try:
            _source, target = self._target(ctx, mod_dir, part)
        except (OSError, ValueError) as exc:
            outcome.status, outcome.detail = "error", str(exc)
            return outcome
        outcome.extra["target"] = str(target)
        if target.exists():
            owned = _owned_by(target, mod_id)
            outcome.status, outcome.detail, outcome.files = ("present" if owned else "present-foreign"), f"installed at {target}", [str(target)]
        else:
            outcome.status, outcome.detail = "absent", f"not installed ({target} missing)"
        return outcome
