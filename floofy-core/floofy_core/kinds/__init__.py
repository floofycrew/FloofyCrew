"""Kind handlers: each part kind lands through the cleanest host seam (Requirement 2, design DR-2).

One handler per ``parts[].kind`` implements ``install`` / ``uninstall`` /
``status`` and reports, for the manager's disclosure and ``status`` (Requirement
2.7), which **seam** it uses, whether it **modifies payload files** and any
**governance warning** attached (Requirement 2.8, 11.2 — a warning, never a gate):

| kind | seam | module |
|---|---|---|
| ``theme`` | the host's theme route (``POST /api/themes/install``) when a gateway runs and ``theme_install`` is open, else the byte-equivalent direct write | :mod:`floofy_core.kinds.theme` |
| ``agent`` | ``<kiro home>/agents/`` | :mod:`floofy_core.kinds.dropins` |
| ``skill`` | ``<host home>/skills/<name>/`` | :mod:`floofy_core.kinds.dropins` |
| ``appearance`` | ``<host home>/appearance-library/appearances/<id>/`` | :mod:`floofy_core.kinds.dropins` |
| ``config`` | ``config.json`` with ``kirocrew config set`` semantics, previous values recorded | :mod:`floofy_core.kinds.config` |
| ``app`` | the host App Kit (``kirocrew app install``) plus the offered ``agent.apps_trusted`` grant | :mod:`floofy_core.kinds.app` |
| ``python-hook`` / ``spa`` | the Loader activates them inside the gateway / the SPA host serves them — nothing to do at install | :class:`LoaderManagedHandler` |
| ``ui`` | the FloofyCrew App mounts the mod's own page on request (Requirement 16.4); inert until opened, so it never lands a mod disabled | :class:`LoaderManagedHandler` |
| ``patch`` | the Patcher, at ``floofy apply`` and on every gateway start — the only kind touching payload files | :class:`PatcherManagedHandler` |

Handlers are stdlib-only and never import the host; they talk to it through the
launcher (:mod:`floofy_core.hostcli`) or the loopback session
(:mod:`floofy_core.gateway`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

__all__ = ["KindContext", "KindHandler", "LoaderManagedHandler", "PatcherManagedHandler", "PartOutcome", "SEAMS", "handler_for", "handlers"]

#: The seam label per kind (Requirement 2.7); mirrors ``floofy_loader.state.SEAM_BY_KIND``.
SEAMS: dict[str, str] = {
    "theme": "themes directory (host validator route, or byte-equivalent direct write when closed)",
    "agent": "agents directory (<kiro home>/agents)",
    "skill": "skills directory (<host home>/skills/<name>)",
    "appearance": "appearance library (<host home>/appearance-library/appearances/<id>)",
    "config": "config.json (kirocrew config set semantics, previous values recorded)",
    "app": "App Kit (kirocrew app install; agent.apps_trusted grant offered)",
    "python-hook": "Loader (in-process activation inside the gateway)",
    "spa": "SPA host (served from the Loader app; boot parts inlined by the Patcher)",
    "patch": "Patcher overlay (modifies payload files; reversible, fingerprint-gated)",
    "ui": "FloofyCrew App (the mod's own page, mounted same-origin on request with floofy.mod(id))",
}


@dataclass
class PartOutcome:
    """What happened to one part (install/uninstall/status)."""

    kind: str
    index: int
    seam: str
    modifies_payload: bool
    ok: bool = True
    status: str = "installed"  # installed | removed | present | absent | skipped | error | loader | patcher
    detail: str = ""
    governance: list[dict[str, Any]] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "index": self.index, "seam": self.seam, "modifiesPayload": self.modifies_payload, "ok": self.ok, "status": self.status, "detail": self.detail, "governance": list(self.governance), "files": list(self.files), **self.extra}


@dataclass
class KindContext:
    """What a handler may use: homes, the host launcher, a gateway session, governance, the console."""

    host_home: Path
    kiro_home: Path
    data_home: Path
    launcher: Path | None = None
    host_cli_env: dict[str, str] = field(default_factory=dict)
    session: Any = None  # floofy_core.gateway.GatewaySession | None
    governance: Any = None  # floofy_core.governance.GovernanceSnapshot | None
    confirm: Callable[[str], bool] = lambda _prompt: False
    say: Callable[[str], None] = lambda _message: None
    warn: Callable[[str], None] = lambda _message: None
    audit: Callable[..., Any] | None = None
    in_gateway: bool = False

    def record(self, op: str, **fields: Any) -> None:
        if self.audit is not None:
            try:
                self.audit(op, **fields)
            except Exception:  # noqa: BLE001 - never fail a seam operation on the audit log
                pass


class KindHandler(Protocol):
    kind: str
    seam: str
    modifies_payload: bool

    def install(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome: ...

    def uninstall(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome: ...

    def status(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome: ...


@dataclass
class LoaderManagedHandler:
    """``python-hook`` and ``spa``: the Loader activates them at gateway start; installing is copying the files."""

    kind: str
    modifies_payload: bool = False

    @property
    def seam(self) -> str:
        return SEAMS[self.kind]

    def _outcome(self, index: int, status: str, detail: str) -> PartOutcome:
        return PartOutcome(self.kind, index, self.seam, self.modifies_payload, status=status, detail=detail)

    def install(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome:
        if self.kind == "ui":
            return self._outcome(index, "loader", f"the FloofyCrew App lists the page {part.get('title')!r} under Mods and mounts it when you open it (inert until then; Requirement 16.4)")
        return self._outcome(index, "loader", "activated by the Loader at the next gateway start once the mod is enabled (lands disabled — Requirement 11.7)")

    def uninstall(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome:
        if self.kind == "ui":
            return self._outcome(index, "loader", "the App stops listing and serving the page when the mod directory goes away")
        return self._outcome(index, "loader", "deactivated by the Loader when the mod directory goes away")

    def status(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome:
        present = (Path(mod_dir) / str(part.get("entry" if self.kind == "ui" else "path", ""))).exists()
        return self._outcome(index, "present" if present else "absent", "files in place; the Loader state says whether it is active" if present else f"part file missing: {part.get('entry' if self.kind == 'ui' else 'path')}")


@dataclass
class PatcherManagedHandler:
    """``patch``: applied by the Patcher (``floofy apply``, every gateway start); the only kind touching payload files."""

    kind: str = "patch"
    modifies_payload: bool = True

    @property
    def seam(self) -> str:
        return SEAMS["patch"]

    def _outcome(self, index: int, status: str, detail: str) -> PartOutcome:
        return PartOutcome(self.kind, index, self.seam, True, status=status, detail=detail)

    def install(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome:
        return self._outcome(index, "patcher", "applied to every payload by the Patcher (floofy apply, and on every gateway start) while the mod is enabled")

    def uninstall(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome:
        return self._outcome(index, "patcher", "reverted by the Patcher's next revert-then-patch pass")

    def status(self, ctx: KindContext, mod_id: str, mod_dir: Path, part: dict[str, Any], index: int) -> PartOutcome:
        present = (Path(mod_dir) / str(part.get("path", ""))).is_file()
        return self._outcome(index, "present" if present else "absent", "descriptor in place; `floofy doctor` shows the deployment manifests" if present else f"descriptor missing: {part.get('path')}")


def handlers() -> dict[str, Any]:
    """Every kind handler by kind name (seam handlers imported lazily so a missing one degrades to a note)."""
    table: dict[str, Any] = {
        "python-hook": LoaderManagedHandler("python-hook"),
        "spa": LoaderManagedHandler("spa"),
        "ui": LoaderManagedHandler("ui"),
        "patch": PatcherManagedHandler(),
    }
    for kind, module_name, class_name in (
        ("theme", "theme", "ThemeHandler"),
        ("agent", "dropins", "AgentHandler"),
        ("skill", "dropins", "SkillHandler"),
        ("appearance", "dropins", "AppearanceHandler"),
        ("config", "config", "ConfigHandler"),
        ("app", "app", "AppHandler"),
    ):
        try:
            module = __import__(f"{__name__}.{module_name}", fromlist=[class_name])
            table[kind] = getattr(module, class_name)()
        except (ImportError, AttributeError):
            continue
    return table


def handler_for(kind: str) -> Any | None:
    return handlers().get(kind)
