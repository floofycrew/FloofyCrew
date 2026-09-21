"""The Loader boot sequence (design "Loader — Boot sequence" steps 1–9).

1. host facts (:mod:`floofy_loader.host`);
2. ``consent.json`` — without a current acknowledgement the Loader is **inert**:
   every step below still runs read-only and is reported, but nothing is
   activated, no stage is applied, no patch is written, and every mod that would
   have activated carries the Loader-level reason ``ConsentRequired``
   (Requirement 11.1, 3.5);
3. host governance, read for **information** — warnings attached per mod, never
   a reason (Requirement 11.2, DR-5);
4. ``pending/`` applied (:mod:`floofy_loader.pending`), then ``mods/*/floofy.json``
   and ``enabled.json`` scanned;
5. ``files[]`` hashes and the rest of ``floofy validate`` → ``MissingFiles`` /
   ``Error`` (Requirement 1.6);
6. the resolver (graded dependencies, conflicts, order, cycles → ``Conflict``,
   ``strict`` → ``Unsupported``) — :func:`floofy_core.resolver.resolve`;
7. the compat cache: a ``broken`` cell → ``Quarantined`` plus a quarantine
   request for the manager (Requirement 9.2, 6.2);
8. ``python-hook`` parts activated in order under ``floofy_mods.<id>.<module>``,
   each inside ``try/except`` — an exception disables that mod with ``Error``
   and the others continue (Requirement 2.4, 3.4);
9. the :class:`floofy_loader.state.LoaderState` published (routes,
   ``loader-state.json``, the manager UI).

The Loader-itself-fails case (Requirement 3.6) is handled one level up, in
``floofy_loader.hooks`` / :mod:`floofy_loader.runtime`: :func:`boot` may raise
and the caller records ``loader-failure.json`` while the gateway boots vanilla.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from floofy_core import __version__ as FRAMEWORK_VERSION
from floofy_core.governance import LOADER_APP_NAME, GovernanceSnapshot, GovernanceWarning, warnings_for
from floofy_core.resolver import HostFacts as ResolverHost
from floofy_core.modstore import read_source, sync_early_list
from floofy_core.resolver import ModRecord, Reason, resolve
from floofy_core.tier import tier_of
from floofy_core.validator import validate_mod

from .activation import ActivationError, ModContext, PartHandle, activate_part, deactivate_part, drop_mod_modules, load_part_module
from .compat import CompatCache
from .consent import REASON_CONSENT_REQUIRED, read_consent
from .host import HostFacts
from .paths import FloofyPaths
from .pending import PendingReport, apply_pending, plan_boot, plan_patches, read_enabled
from .state import LoaderState, ModState, PartState, utc_now

__all__ = ["BootDeps", "BootResult", "ModActivation", "PatchJob", "boot", "deactivate_all", "default_governance_snapshot", "default_patch_runner", "run_deferred_patches"]

logger = logging.getLogger("floofy.loader.boot")

#: Kinds whose code runs — they land disabled until the user enables them (Requirement 11.7).
CODE_KINDS = frozenset({"python-hook", "spa"})

ContextFactory = Callable[[str, str, Path, dict[str, Any]], ModContext]
GovernanceReader = Callable[[HostFacts], GovernanceSnapshot]
PatchRunner = Callable[[list, HostFacts, FloofyPaths], dict[str, Any]]


@dataclass
class ModActivation:
    """A mod's live handles for deactivation (kept by the runtime)."""

    mod_id: str
    ctx: ModContext
    handles: list[PartHandle] = field(default_factory=list)


@dataclass
class BootDeps:
    """Everything :func:`boot` needs; the runtime wires the real collaborators, tests pass stand-ins."""

    paths: FloofyPaths
    facts: HostFacts
    make_context: ContextFactory
    log: logging.Logger = logger
    governance_reader: GovernanceReader | None = None
    patch_runner: PatchRunner | None = None
    #: Called after a mod deactivates so the runtime can unwind its hooks (task 4.4).
    on_deactivated: Callable[[str], None] | None = None
    #: Publish spa parts into ``spa/<id>/`` for the SPA host route (task 4.6 serves them).
    publish_spa: bool = True
    #: Leave the Patcher pass (step 4b) as a :class:`PatchJob` on the result instead of running it inside :func:`boot`:
    #: a request handler boots on the event loop and runs the file work in a worker thread afterwards
    #: (:func:`run_deferred_patches`), so the host's loop watchdog keeps its heartbeat (task 11.5).
    defer_patches: bool = False


@dataclass
class PatchJob:
    """The Patcher pass a deferred boot left for :func:`run_deferred_patches`."""

    planned: list
    vanilla: bool = False


@dataclass
class BootResult:
    state: LoaderState
    activations: dict[str, ModActivation] = field(default_factory=dict)
    #: Set by a boot with ``defer_patches``; ``None`` once the pass ran (or when nothing was planned).
    patch_job: PatchJob | None = None

    def refresh_exports(self) -> None:
        for mod_id, activation in self.activations.items():
            if mod_id in self.state.mods:
                self.state.mods[mod_id].exports = dict(activation.ctx.state)


# --- collaborators with defaults -----------------------------------------------------------


def default_governance_snapshot(facts: HostFacts) -> GovernanceSnapshot:
    """Read the host's governance files through the edition adapters (Requirement 11.2) — information only."""
    from floofy_core.editions import governance_locations  # noqa: PLC0415
    from floofy_core.payloads import Payload  # noqa: PLC0415

    payload = None
    if facts.package_dir is not None and facts.payload_root is not None:
        payload = Payload(
            id=facts.payload_id or f"running:{facts.version}",
            root=facts.payload_root,
            package_dir=facts.package_dir,
            dist_dir=facts.package_dir / "static" / "dist",
            host_version=facts.host_version,
            edition=facts.edition,
            channel=facts.channel,
            interpreter=facts.interpreter,
            current=True,
            source="loader",
        )
    return GovernanceSnapshot.read(governance_locations(facts.host_home, payload))


def default_patch_runner(planned: list, facts: HostFacts, paths: FloofyPaths) -> dict[str, Any]:
    """Hand the enabled ``patch`` parts to the Patcher (pre-consented, no live verify — see :mod:`floofy_loader.pending`)."""
    from floofy_core.editions import edition_providers  # noqa: PLC0415
    from floofy_core.governance import AlwaysConfirm  # noqa: PLC0415
    from floofy_core.patcher import Patcher  # noqa: PLC0415
    from floofy_core.payloads import discover_payloads  # noqa: PLC0415

    providers = [] if os.environ.get("FLOOFY_NO_ADAPTERS") == "1" else edition_providers()
    discovery = discover_payloads(providers, include_find_spec=True)
    if not discovery.payloads:
        return {"ran": False, "detail": "no payload found", "searched": [str(r) for r in discovery.searched_roots]}
    patcher = Patcher(paths.data_home, discovery.payloads, host_home=facts.host_home, confirmer=AlwaysConfirm(), triggers_installed=True)
    report = patcher.apply(planned, verify=False)
    return {"ran": True, "ok": report.ok, "report": report.to_dict()}


# --- the sequence -----------------------------------------------------------------------------


def _scan_mods(paths: FloofyPaths, state: LoaderState, enabled: dict[str, bool]) -> list[tuple[str, Path, dict[str, Any]]]:
    """``mods/*/floofy.json`` → (id, dir, manifest); unreadable manifests become ``Error`` entries."""
    found: list[tuple[str, Path, dict[str, Any]]] = []
    if not paths.mods.is_dir():
        return found
    for mod_dir in sorted(p for p in paths.mods.iterdir() if p.is_dir() and not p.name.endswith(".floofy-old")):
        manifest_path = mod_dir / "floofy.json"
        if not manifest_path.is_file():
            continue
        mod_id = mod_dir.name
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("floofy.json is not a JSON object")
        except (OSError, ValueError) as exc:
            broken = ModState(mod_id, "0.0.0", mod_dir=str(mod_dir), enabled=enabled.get(mod_id, True))
            broken.set_reason(Reason.Error.value, f"floofy.json unreadable: {exc}")
            state.mods[mod_id] = broken
            continue
        if manifest.get("id") != mod_id:
            broken = ModState(mod_id, str(manifest.get("version") or "0.0.0"), name=str(manifest.get("name") or ""), mod_dir=str(mod_dir), enabled=enabled.get(mod_id, True))
            broken.set_reason(Reason.Error.value, f"directory {mod_id!r} holds a manifest with id {manifest.get('id')!r}")
            state.mods[mod_id] = broken
            continue
        found.append((mod_id, mod_dir, manifest))
    return found


def _parts(manifest: dict[str, Any]) -> list[PartState]:
    parts: list[PartState] = []
    for index, raw in enumerate(manifest.get("parts") or []):
        if not isinstance(raw, dict):
            continue
        part = PartState(index, str(raw.get("kind", "")), str(raw.get("side", "")), str(raw.get("path", "")))
        if part.kind == "spa":
            part.activation = str(raw.get("activation") or "runtime")
        if part.kind == "ui":
            part.entry = str(raw.get("entry") or "") or None
            part.title = str(raw.get("title") or "") or None
            part.icon = str(raw.get("icon") or "") or None
        parts.append(part)
    return parts


def _is_enabled(mod_id: str, manifest: dict[str, Any], enabled: dict[str, bool]) -> tuple[bool, str]:
    """``enabled.json`` decides; an unlisted code mod is off until the user enables it (Requirement 11.7)."""
    if mod_id in enabled:
        return enabled[mod_id], "disabled by the user" if not enabled[mod_id] else ""
    kinds = {str(p.get("kind")) for p in manifest.get("parts") or [] if isinstance(p, dict)}
    if kinds & CODE_KINDS:
        return False, "not enabled yet: mods with python-hook or spa parts land disabled after install"
    return True, ""


def _validate(mod_dir: Path, mod_state: ModState) -> tuple[bool, str | None]:
    """Run ``floofy validate`` on the installed directory → (files_ok, fatal error detail).

    A missing or tampered file is ``MissingFiles`` (Requirement 1.6) even when it
    drags other findings along (a part whose file vanished); other errors alone
    (schema, bad descriptor) are ``Error``.
    """
    report = validate_mod(mod_dir)
    for finding in report.warnings:
        mod_state.warnings.append({"code": finding.code, "message": finding.message, "path": finding.path})
    files_ok = not any(f.code == "MissingFiles" for f in report.errors)
    mod_state.errors.extend(f"{f.code}: {f.message}" for f in report.errors)
    if not files_ok:
        return False, None
    if report.errors:
        return True, "; ".join(f"{f.code}: {f.message}" for f in report.errors[:3])
    return True, None


def _write_quarantine_request(paths: FloofyPaths, mod_id: str, version: str, facts: HostFacts, verdict: str, run: str | None) -> None:
    paths.quarantine_requests.mkdir(parents=True, exist_ok=True)
    record = {
        "mod": mod_id,
        "version": version,
        "hostVersion": facts.version,
        "edition": facts.edition,
        "channel": facts.channel,
        "verdict": verdict,
        "run": run,
        "reason": "compatibility matrix marks this mod broken on this host version",
        "ts": utc_now(),
    }
    (paths.quarantine_requests / f"{mod_id}.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def _governance(deps: BootDeps, state: LoaderState, manifests: dict[str, dict[str, Any]]) -> None:
    """Attach governance **warnings** (never reasons) to the mods they concern (Requirement 11.2)."""
    reader = deps.governance_reader or default_governance_snapshot
    try:
        snapshot = reader(deps.facts)
    except Exception as exc:  # noqa: BLE001 - governance is informational; a read failure is a note
        state.errors.append(f"governance unreadable: {type(exc).__name__}: {exc}")
        return
    state.governance_snapshot = snapshot.to_dict()
    theme_targets = [mod_id for mod_id, m in manifests.items() if any(isinstance(p, dict) and p.get("kind") == "theme" for p in m.get("parts") or [])]
    app_targets = [mod_id for mod_id, m in manifests.items() if any(isinstance(p, dict) and p.get("kind") == "app" for p in m.get("parts") or [])]
    warnings: list[GovernanceWarning] = warnings_for(snapshot, theme_targets=theme_targets, app_targets=app_targets)
    for warning in warnings:
        entry = warning.to_dict()
        targets = set(warning.affected_targets)
        touched = False
        for mod_id in manifests:
            if mod_id in targets:
                state.mods[mod_id].governance.append(entry)
                touched = True
        if not touched or LOADER_APP_NAME in targets or not targets:
            state.governance.append(entry)


def _publish_spa_parts(paths: FloofyPaths, mod_id: str, mod_dir: Path, manifest: dict[str, Any], mod_state: ModState) -> None:
    """Mirror an active mod's ``spa`` part files into ``spa/<id>/`` (Requirement 4.1: only enabled parts are served)."""
    target = paths.spa_dir(mod_id)
    if target.exists():
        shutil.rmtree(target)
    listed = [str(f.get("path")) for f in manifest.get("files") or [] if isinstance(f, dict) and isinstance(f.get("path"), str)]
    for part in mod_state.parts:
        if part.kind != "spa":
            continue
        source = (mod_dir / part.path).resolve()
        try:
            source.relative_to(mod_dir.resolve())
        except ValueError:
            part.status, part.detail = "error", "spa part path escapes the mod directory"
            continue
        if not source.is_file():
            part.status, part.detail = "error", f"spa part file missing: {part.path}"
            continue
        base = Path(part.path).parent
        own = source.relative_to(mod_dir.resolve())
        if base == Path("."):
            members = [own, *(Path(p) for p in listed if Path(p).parent == Path("."))]
        else:
            members = [own, *(Path(p) for p in listed if Path(p).is_relative_to(base))]
        for rel in dict.fromkeys(members):
            src = mod_dir / rel
            if not src.is_file():
                continue
            dest = target / (rel.relative_to(base) if base != Path(".") else rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        part.status = "active"
        if part.activation == "boot":
            part.detail = f"boot activation: inlined into index.html by the Patcher; files mirrored to spa/{mod_id}/"
        else:
            part.detail = f"served from spa/{mod_id}/{Path(part.path).name}"


def _remove_spa(paths: FloofyPaths, mod_id: str) -> None:
    target = paths.spa_dir(mod_id)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


def _activate(deps: BootDeps, state: LoaderState, mod_id: str, mod_dir: Path, manifest: dict[str, Any]) -> ModActivation | None:
    """Load and activate every ``python-hook`` part of one mod; any failure → ``Error`` for that mod only."""
    mod_state = state.mods[mod_id]
    ctx = deps.make_context(mod_id, mod_state.version, mod_dir, manifest)
    activation = ModActivation(mod_id, ctx)
    try:
        for part in mod_state.parts:
            raw = (manifest.get("parts") or [])[part.index]
            if part.kind == "python-hook":
                handle = load_part_module(mod_id, mod_dir, raw, part.index)
                activation.handles.append(handle)
                activate_part(handle, ctx)
                part.status, part.detail = "active", f"activated as {handle.module_name}"
            elif part.kind == "ui":
                # inert until opened: the App imports the entry same-origin from /ui/mods/<id>/ while the mod is active
                part.status, part.detail = "active", f"page {part.title or part.entry!r}: served from ui/mods/{mod_id}/, mounted by the FloofyCrew App on request"
            elif part.kind in ("theme", "agent", "skill", "appearance", "config", "app", "patch"):
                part.status, part.detail = "inactive", "installed by the manager through its seam; nothing to activate at boot"
    except ActivationError as exc:
        deps.log.error("mod %s failed to activate: %s", mod_id, exc)
        for handle in activation.handles:
            deactivate_part(handle, ctx)
        drop_mod_modules(mod_id)
        if deps.on_deactivated is not None:
            deps.on_deactivated(mod_id)
        for part in mod_state.parts:
            if part.status == "active" or part.kind == "python-hook":
                part.status = "error"
        mod_state.errors.append(str(exc))
        if exc.traceback:
            mod_state.errors.append(exc.traceback[-2000:])
        mod_state.set_reason(Reason.Error.value, str(exc))
        return None
    mod_state.active = True
    return activation


def boot(deps: BootDeps) -> BootResult:
    """Run the sequence and return the state plus the live activations."""
    started = time.monotonic()
    paths = deps.paths.ensure()
    facts = deps.facts
    state = LoaderState(data_home=str(paths.data_home), host=facts.to_dict())
    result = BootResult(state)

    consent = read_consent(paths.consent)
    state.consent = consent.to_dict()
    inert = not consent.ok
    state.loader = "inert" if inert else "ok"

    # 4. pending (only on consent), scan, enabled flags
    if inert:
        state.pending = PendingReport().to_dict() | {"skipped": "consent required"}
    else:
        pending = apply_pending(paths, log=deps.log.info)
        state.pending = pending.to_dict()
        state.errors.extend(f"pending: {e}" for e in pending.errors)
    enabled = read_enabled(paths.enabled)
    # Keep the early-activation list (Requirement 3.2) in step with the enabled set, so the
    # shim reads the right list at the NEXT host start even when the last writer was not us.
    if not inert:
        try:
            sync_early_list(paths)
        except OSError as exc:
            state.errors.append(f"early.json: {exc}")
    # floofy --vanilla: one boot with every mod disabled (Requirement 7.1); the marker is consumed here
    vanilla = not inert and paths.vanilla_marker.is_file()
    if vanilla:
        state.vanilla = True
        try:
            paths.vanilla_marker.unlink()
        except OSError as exc:
            state.errors.append(f"vanilla marker could not be removed: {exc}")
        deps.log.info("vanilla boot requested (floofy --vanilla): every mod is disabled for this boot only")
    scanned = _scan_mods(paths, state, enabled)
    manifests: dict[str, dict[str, Any]] = {}
    dirs: dict[str, Path] = {}

    # 5. validate; 7. compat
    compat = CompatCache.load(paths.compat_cache)
    row = compat.row_for(facts.edition, facts.channel, facts.version)
    state.compat = compat.to_dict(row)
    records: list[ModRecord] = []
    for mod_id, mod_dir, manifest in scanned:
        version = str(manifest.get("version") or "0.0.0")
        mod_state = ModState(mod_id, version, name=str(manifest.get("name") or ""), mod_dir=str(mod_dir), parts=_parts(manifest))
        mod_state.tier = tier_of(read_source(mod_dir), mod_id=mod_id, version=version, compat_row=row)
        is_enabled, why = _is_enabled(mod_id, manifest, enabled)
        if vanilla:
            is_enabled, why = False, "vanilla boot requested (floofy --vanilla): every mod is disabled for this boot only"
        mod_state.enabled = is_enabled
        if why:
            mod_state.detail = why
        state.mods[mod_id] = mod_state
        manifests[mod_id] = manifest
        dirs[mod_id] = mod_dir
        files_ok, fatal = _validate(mod_dir, mod_state)
        mod_state.files_ok = files_ok
        if fatal is not None:
            mod_state.set_reason(Reason.Error.value, fatal)
            continue
        quarantined = False
        if row is not None and row.verdict(mod_id, version) == "broken":
            quarantined = True
            mod_state.quarantined = True
            if not inert:
                _write_quarantine_request(paths, mod_id, version, facts, "broken", row.runs.get(f"{mod_id}@{version}"))
        records.append(ModRecord(mod_id, version, manifest, enabled=is_enabled, files_ok=files_ok, quarantined=quarantined))

    # 6. resolve
    resolution = resolve(records, ResolverHost(facts.base_version, facts.edition, facts.channel or "unknown", FRAMEWORK_VERSION))
    for mod_id, decision in resolution.decisions.items():
        mod_state = state.mods[mod_id]
        mod_state.warnings.extend(w.to_dict() for w in decision.warnings)
        if not decision.active:
            reason = decision.reason.value if decision.reason else Reason.Error.value
            keep_detail = decision.reason is Reason.UserDisabled and bool(mod_state.detail)
            mod_state.set_reason(reason, mod_state.detail if keep_detail else decision.detail)
    for duplicate in resolution.duplicates:
        state.errors.append(f"duplicate mod entry {duplicate.id}@{duplicate.version}: {duplicate.detail}")
    state.order = list(resolution.order)

    # 3. governance warnings (informational)
    _governance(deps, state, manifests)

    # 8. activate (or report ConsentRequired)
    for mod_id in resolution.order:
        mod_state = state.mods[mod_id]
        if inert:
            mod_state.set_reason(REASON_CONSENT_REQUIRED, "no consent record: the Loader is inert (floofy init)")
            for part in mod_state.parts:
                part.status = "inactive"
            continue
        activation = _activate(deps, state, mod_id, dirs[mod_id], manifests[mod_id])
        if activation is not None:
            result.activations[mod_id] = activation
            if deps.publish_spa:
                try:
                    _publish_spa_parts(paths, mod_id, dirs[mod_id], manifests[mod_id], mod_state)
                except OSError as exc:
                    mod_state.errors.append(f"spa publish failed: {exc}")
    for mod_id in state.mods:
        if inert or not state.mods[mod_id].active:
            _remove_spa(paths, mod_id)

    # 4b. patches for enabled mods (revert-then-patch; the Loader is a re-apply trigger)
    if not inert:
        active_dirs = [(mod_id, dirs[mod_id], manifests[mod_id]) for mod_id in resolution.order if state.mods[mod_id].active]
        planned, problems = plan_patches(active_dirs)
        state.errors.extend(f"patch descriptor: {p}" for p in problems)
        boot_plan, boot_problems = plan_boot(active_dirs, facts.host_home)
        state.errors.extend(f"boot descriptor: {p}" for p in boot_problems)
        if boot_plan is not None:
            planned.append(boot_plan)
        if planned or vanilla:
            if deps.defer_patches:
                result.patch_job = PatchJob(planned, vanilla)
                state.patches = {"ran": False, "detail": "deferred: the Patcher pass runs off the event loop after this boot"}
            else:
                _patch_step(deps, state, planned, vanilla)
        else:
            state.patches = {"ran": False, "detail": "no patch parts enabled"}

    result.refresh_exports()
    state.duration_ms = int((time.monotonic() - started) * 1000)
    return result


def _patch_step(deps: BootDeps, state: LoaderState, planned: list, vanilla: bool) -> None:
    """Step 4b: hand the enabled set to the Patcher (revert-then-patch every payload); a vanilla boot hands it an EMPTY set."""
    runner = deps.patch_runner or default_patch_runner
    try:
        state.patches = runner(planned, deps.facts, deps.paths)
    except Exception as exc:  # noqa: BLE001 - never fatal for the boot
        state.patches = {"ran": False, "error": f"{type(exc).__name__}: {exc}"}
        state.errors.append(f"patcher: {type(exc).__name__}: {exc}")
    if vanilla and isinstance(state.patches, dict):
        state.patches["vanilla"] = True


def run_deferred_patches(result: BootResult, deps: BootDeps) -> bool:
    """Run the Patcher pass a ``defer_patches`` boot left behind; returns whether there was one."""
    job = result.patch_job
    if job is None:
        return False
    result.patch_job = None
    _patch_step(deps, result.state, job.planned, job.vanilla)
    return True


def deactivate_all(result: BootResult, deps: BootDeps) -> list[str]:
    """Deactivate every active mod in reverse activation order, fail-open; returns the errors."""
    errors: list[str] = []
    for mod_id in reversed(result.state.order):
        activation = result.activations.get(mod_id)
        if activation is None:
            continue
        for handle in reversed(activation.handles):
            error = deactivate_part(handle, activation.ctx)
            if error:
                errors.append(f"{mod_id}: {error}")
        drop_mod_modules(mod_id)
        if deps.on_deactivated is not None:
            try:
                deps.on_deactivated(mod_id)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{mod_id}: unwind failed: {exc}")
        if mod_id in result.state.mods:
            result.state.mods[mod_id].active = False
            for part in result.state.mods[mod_id].parts:
                if part.status == "active":
                    part.status = "inactive"
    result.activations.clear()
    return errors
