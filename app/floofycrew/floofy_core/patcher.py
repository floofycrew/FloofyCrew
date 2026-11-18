"""The Patcher: ``apply`` / ``restore`` / ``status`` / ``verify`` / ``gc`` over every payload.

Requirement 5.4 — ``apply`` is idempotent, BSIPA revert-then-patch: for every
payload present it classifies drift (Requirement 5.3), starts from the recorded
original of each target (the sidecar backup, or the file itself when untouched),
re-applies the **full** enabled patch set and writes the result once, so a
half-applied state self-heals and a mod that dropped out of the set is restored.
Requirement 5.8 — ``restore`` is manifest-driven, then sweeps the payload for
``*.floofybak`` backups (moved back over their originals, which also returns
sidelined ``.br``/``.gz`` sidecars) and ``*-floofy.*`` added files (removed), so a
lost manifest still restores. Requirement 5.7 — ``verify`` asks the running
gateway over the dashboard unix socket / loopback for the shell and every patched
or added asset and compares the served bytes with the deployment manifest,
distinguishing a **dormant** payload (the gateway serves another version, or
answers 404 for every asset) from a **failed** one. Requirement 6.4 — every
payload is patched, dormant ones marked; Requirement 6.5 — ``gc`` drops manifests
of payloads that no longer exist.

Targets are classified with :class:`floofy_core.targets.TargetPolicy` before
anything is written: engineering-rule targets (host Python, launcher) are
**refused** with an error (Requirement 5.10); governance files are **allowed**
only when the caller has confirmed that exact target (``confirmed_governance_targets``,
per-file typed confirmation — Requirement 11.4) and are flagged
``governance_altering`` in the report and audit; an unconfirmed one is skipped
with a diagnostic, never silently. Governance *warnings* (task 3.6) never stop an
apply — DR-5.

Descriptor targets are resolved against the directory that holds the ``kiro_crew``
package (``kiro_crew/static/dist/index.html``). Every mutating operation is
appended to ``<data_home>/audit.jsonl`` (Requirement 11.5).
"""
from __future__ import annotations

import http.client
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .aliasgraph import republish, sideline_sidecars
from .asar import AsarArchive, AsarError
from .audit import AuditLog
from .deploy import (
    Backups,
    DeployManifest,
    DriftAction,
    DriftClass,
    DriftReport,
    atomic_write_bytes,
    classify_drift,
    is_floofy_added,
    iter_manifests,
    sha256_bytes,
    sha256_file,
    utc_now,
)
from .electron import ElectronShell, find_electron_shell
from .gateway import GatewayEndpoint, find_endpoints, probe_version, served_version
from .governance import AlwaysConfirm, Confirmer, GovernanceSnapshot, GovernanceWarning, survival_check, survival_prompt
from .patches import PatchDescriptor, PatchResult, Skip, apply_descriptor
from .payloads import Payload
from .boot_script import BOOT_ASSETS_DIR
from .spa_patches import PATCHED_DIR, apply_import_map_patch, default_ui_root, refresh_patched_index, resolve_chunk_target, ui_url_for
from .targets import DEFAULT_TARGET_POLICY, TargetClass, TargetPolicy

__all__ = [
    "ApplyReport",
    "AuditLog",
    "GcReport",
    "Patcher",
    "PayloadApply",
    "PayloadStatus",
    "PlannedPatch",
    "RestoreReport",
    "StatusReport",
    "VerifyReport",
    "VerifyStatus",
    "resolve_target",
    "url_for",
]

Reporter = Callable[[str], None]


def _silent(_message: str) -> None:
    return None


# --- inputs --------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedPatch:
    """One enabled ``patch`` part: who owns it and the loaded descriptor."""

    mod: str
    part: str
    descriptor: PatchDescriptor

    @property
    def label(self) -> str:
        return f"{self.mod}#{self.part}"


def resolve_target(payload: Payload, target: str) -> Path:
    """A descriptor ``target`` is relative to the directory holding ``kiro_crew``."""
    return (payload.package_dir.parent / target).resolve()


def url_for(payload: Payload, path: Path) -> str | None:
    """The dashboard URL that serves a file under ``static/dist`` (``None`` outside it)."""
    try:
        relative = Path(path).resolve().relative_to(payload.dist_dir.resolve())
    except ValueError:
        return None
    if relative.as_posix() == "index.html":
        return "/"
    return "/" + relative.as_posix()


# --- reports -------------------------------------------------------------------------


@dataclass
class PayloadApply:
    payload: str
    host_version: str
    dormant: bool
    drift: dict[str, str] = field(default_factory=dict)
    written: list[str] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)  # "<mod>#<part> -> <target>#ops/<i>"
    skipped: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    governance_altering: list[str] = field(default_factory=list)
    alias_graph: str | None = None
    verify: "VerifyReport | None" = None

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ok"] = self.ok
        return data


@dataclass
class ApplyReport:
    payloads: list[PayloadApply] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    governance_warnings: list[dict[str, Any]] = field(default_factory=list)  # informational, never a gate (DR-5)
    confirmation: str | None = None  # "confirmed" | "declined" | None when nothing had to be asked

    @property
    def ok(self) -> bool:
        return all(p.ok for p in self.payloads)

    @property
    def declined(self) -> bool:
        return self.confirmation == "declined"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "confirmation": self.confirmation,
            "governanceWarnings": list(self.governance_warnings),
            "payloads": [p.to_dict() for p in self.payloads],
            "notes": list(self.notes),
        }


@dataclass
class RestoreReport:
    payloads: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    manifests_removed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"payloads": self.payloads, "manifestsRemoved": self.manifests_removed}


class VerifyStatus:
    VERIFIED = "verified"
    DORMANT = "dormant"
    FAILED = "failed"
    NO_GATEWAY = "no-gateway"
    NOTHING = "nothing-to-verify"


@dataclass
class VerifyReport:
    payload: str
    status: str
    endpoint: str | None = None
    served_version: str | None = None
    checks: list[dict[str, Any]] = field(default_factory=list)
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (VerifyStatus.VERIFIED, VerifyStatus.DORMANT, VerifyStatus.NOTHING)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PayloadStatus:
    payload: str
    host_version: str
    edition: str
    root: str
    current: bool
    dormant: bool
    served_version: str | None
    manifest: str | None
    patched: int
    added: int
    sidelined: int
    backups_present: int
    drift: dict[str, int]
    mods: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StatusReport:
    payloads: list[PayloadStatus] = field(default_factory=list)
    gateway: str | None = None
    served_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"gateway": self.gateway, "servedVersion": self.served_version, "payloads": [p.to_dict() for p in self.payloads]}


@dataclass
class GcReport:
    removed: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- the engine ----------------------------------------------------------------------


@dataclass
class _TargetWork:
    path: Path
    relative: str
    patches: list[PlannedPatch]
    cache_bust: bool = False
    #: The target is the desktop shell's ``app.asar``; each patch names a member inside it (Requirement 5.11).
    asar: bool = False


class Patcher:
    """Apply, restore, inspect and verify overlay patches on every payload present."""

    def __init__(
        self,
        data_home: Path,
        payloads: Iterable[Payload],
        *,
        policy: TargetPolicy | None = None,
        backups: Backups | None = None,
        confirmed_governance_targets: Iterable[str] = (),
        endpoints: Iterable[GatewayEndpoint] | None = None,
        host_home: Path | None = None,
        reporter: Reporter | None = None,
        governance: GovernanceSnapshot | None = None,
        confirmer: Confirmer | None = None,
        triggers_installed: bool | None = None,
        ui_root: Path | None = None,
    ):
        self.data_home = Path(data_home)
        self.payloads = list(payloads)
        self.policy = policy or DEFAULT_TARGET_POLICY
        self.backups = backups or Backups()
        self.confirmed_governance_targets = {t for t in confirmed_governance_targets}
        self._endpoints = list(endpoints) if endpoints is not None else None
        self.host_home = Path(host_home) if host_home else self.data_home.parent
        #: The installed Loader app's ``ui/`` — patched chunk copies live under ``ui/patched/`` (spike 1.4).
        self.ui_root = Path(ui_root) if ui_root else default_ui_root(self.host_home)
        self.say = reporter or _silent
        self.audit = AuditLog(self.data_home, actor="patcher")
        #: Governance is read for information (task 3.6); ``None`` means "not read".
        self.governance = governance
        #: Who answers the Requirement 5.9 question; non-interactive callers are pre-consented.
        self.confirmer: Confirmer = confirmer or AlwaysConfirm()
        #: Whether a re-apply trigger exists; ``None`` = unknown (no warning either way).
        self.triggers_installed = triggers_installed
        #: Desktop shells by payload id, looked up on first use (Requirement 5.11).
        self._shells: dict[str, ElectronShell | None] = {}

    # -- gateway ------------------------------------------------------------------------

    def endpoints(self) -> list[GatewayEndpoint]:
        if self._endpoints is None:
            self._endpoints = find_endpoints(self.host_home)
        return self._endpoints

    def _served(self) -> tuple[GatewayEndpoint | None, str | None]:
        """The first running gateway and the version it serves (``None`` when the host withheld it; task 10.8)."""
        for endpoint in self.endpoints():
            probe = probe_version(endpoint)
            if probe.reachable:
                return endpoint, probe.version
        return None, None

    def _is_dormant(self, payload: Payload, served: str | None) -> bool:
        """Dormant = not what the launcher runs, or not what the gateway serves (Requirement 6.4)."""
        if served is not None:
            return served != payload.host_version.text
        return not payload.current

    def manifest_path(self, payload: Payload) -> Path:
        return DeployManifest.path_for(self.data_home, payload.id)

    # -- apply --------------------------------------------------------------------------

    def apply(self, patch_set: Iterable[PlannedPatch], *, verify: bool = True, payload_ids: Iterable[str] | None = None) -> ApplyReport:
        """Revert-then-patch every payload with the full enabled set (Requirement 5.4, 6.4)."""
        patches = list(patch_set)
        wanted = set(payload_ids) if payload_ids is not None else None
        report = ApplyReport()
        endpoint, served = self._served()
        if not self.payloads:
            report.notes.append("no payloads to patch")
        if patches and not self._survival_confirmed(report):
            return report
        for payload in self.payloads:
            if wanted is not None and payload.id not in wanted:
                continue
            result = self._apply_one(payload, patches, dormant=self._is_dormant(payload, served))
            if verify and not result.dormant:
                result.verify = self.verify(payload, endpoint=endpoint)
            report.payloads.append(result)
        return report

    def _survival_confirmed(self, report: ApplyReport) -> bool:
        """Requirement 5.9: warn when the patches will not survive the next host update and ask.

        Governance never refuses (DR-5): the warnings are attached to the report,
        the user is asked once, and a *no* is recorded as ``declined`` — the user's
        decision, not a governance verdict. No file is touched after a *no*.
        """
        # unknown trigger state (None) raises no warning; only a known-missing trigger does
        triggers = True if self.triggers_installed is None else self.triggers_installed
        warnings = survival_check(triggers, self.governance)
        report.governance_warnings = [w.to_dict() for w in warnings]
        if not warnings:
            return True
        for warning in warnings:
            self.say("WARNING " + warning.format())
        if self.confirmer.confirm(survival_prompt(warnings), default=False):
            report.confirmation = "confirmed"
            self.audit.record("apply-confirm", warnings=[w.code for w in warnings], result="confirmed")
            return True
        report.confirmation = "declined"
        report.notes.append("UserDeclined: the user chose not to apply after the update-survival warning; nothing was written")
        self.audit.record("apply-confirm", warnings=[w.code for w in warnings], result="declined")
        self.say("SKIP UserDeclined: nothing written")
        return False

    def _apply_one(self, payload: Payload, patches: list[PlannedPatch], *, dormant: bool) -> PayloadApply:
        result = PayloadApply(payload.id, payload.host_version.text, dormant)
        path = self.manifest_path(payload)
        old = DeployManifest.load_if_exists(path) or DeployManifest(payload.id, payload.host_version.text, payload.edition, str(payload.root))
        drift = classify_drift(old, current_host_version=payload.host_version.text, backups=self.backups)
        result.drift = {k: v.value for k, v in drift.classes.items()}
        new = DeployManifest(payload.id, payload.host_version.text, payload.edition, str(payload.root))

        work, import_map_patches = self._plan(payload, patches, result)
        skipped_targets: set[str] = set()
        seeds: list[Path] = []
        index_text: str | None = None
        index_work: _TargetWork | None = None

        for item in work:
            outcome_text = self._apply_in_place(payload, item, drift, old, new, result, seeds, skipped_targets)
            if item.path == payload.index_html and outcome_text is not None:
                index_text, index_work = outcome_text, item

        if import_map_patches:
            shell_skipped = str(payload.index_html) in skipped_targets
            if shell_skipped:
                for planned in import_map_patches:
                    result.skipped.append({"mod": planned.mod, "part": planned.part, "target": planned.descriptor.target, "code": "UserEdited", "message": f"{planned.label}: index.html was edited by hand; its import map cannot be extended (Requirement 5.3)"})
            else:
                if index_text is None:
                    shell = self._original_bytes(payload.index_html, drift.actions.get(str(payload.index_html), DriftAction.NONE), result)
                    if shell is None:
                        result.errors.append("index.html is missing; import-map patches need the shell")
                    else:
                        index_text = shell.decode("utf-8", "surrogateescape")
                        index_work = _TargetWork(payload.index_html, "kiro_crew/static/dist/index.html", [])
                if index_text is not None and index_work is not None:
                    index_text = self._apply_import_maps(payload, import_map_patches, index_text, index_work, drift, old, new, result, seeds, skipped_targets)

        self._copy_ui_assets(patches, new, result)

        if seeds:
            shell_skipped = str(payload.index_html) in skipped_targets
            shell = index_text if index_text is not None else (None if shell_skipped else self._original_bytes(payload.index_html, DriftAction.NONE, result))
            shell_text = shell if isinstance(shell, str) else (shell or b"").decode("utf-8", "surrogateescape")
            alias = republish(payload.assets_dir, seeds, backups=self.backups, manifest=new, mod=";".join(sorted({p.mod for p in patches})), index_html=None if shell_skipped else shell_text)
            result.alias_graph = alias.summary()
            result.written.extend(str(p) for p in alias.written)
            result.removed.extend(str(p) for p in alias.removed)
            self.say("  " + alias.summary())
            if shell_skipped:
                result.skipped.append({"target": "kiro_crew/static/dist/index.html", "code": "UserEdited", "message": "index.html was edited by hand; the alias graph could not repoint it (hard reload delivers the patched chunks)"})
            elif alias.index_html is not None and alias.index_refs:
                index_text = alias.index_html
                if index_work is None:
                    index_work = _TargetWork(payload.index_html, "kiro_crew/static/dist/index.html", [])
        if index_work is not None and index_text is not None:
            original = self._original_bytes(payload.index_html, drift.actions.get(str(payload.index_html), DriftAction.NONE), result)
            if original is not None:
                self._commit_target(payload, index_work, original, index_text.encode("utf-8", "surrogateescape"), old, new, result)

        self._retire_stale(old, new, skipped_targets, result)
        result.governance_altering = sorted(set(result.governance_altering))
        if new.is_empty:
            if path.exists():
                path.unlink()
        else:
            new.save(path)
        self._refresh_patched_index()
        self.audit.record(
            "apply",
            payload=payload.id,
            hostVersion=payload.host_version.text,
            files=result.written,
            restored=result.restored,
            removed=result.removed,
            governanceFlags=result.governance_altering,
            errors=result.errors,
            result="ok" if result.ok else "errors",
        )
        return result

    def _plan(self, payload: Payload, patches: list[PlannedPatch], result: PayloadApply) -> tuple[list[_TargetWork], list[PlannedPatch]]:
        """Group in-place patches by resolved target and apply the target policy (Requirement 5.10, 11.4).

        ``mode: import-map`` descriptors are returned separately: they never touch
        their target file and are applied against the shell text (spike 1.4).
        """
        by_target: dict[Path, _TargetWork] = {}
        import_maps: list[PlannedPatch] = []
        for planned in patches:
            relative = planned.descriptor.target
            verdict = self.policy.classify(relative)
            if verdict is TargetClass.ENGINEERING_RULE:
                message = f"{planned.label}: target {relative!r} is host code ({self.policy.matching_pattern(relative)}); never patched in place (Requirement 5.10)"
                result.errors.append(message)
                self.say("REFUSE " + message)
                continue
            if verdict is TargetClass.GOVERNANCE:
                if relative not in self.confirmed_governance_targets:
                    result.skipped.append({"target": relative, "code": "GovernanceUnconfirmed", "message": f"{planned.label}: {relative} is a host governance file; it needs an explicit per-file confirmation before it is patched (Requirement 11.4)"})
                    self.say(f"SKIP {relative}: governance-altering target not confirmed")
                    continue
                result.governance_altering.append(relative)
            if planned.descriptor.is_import_map:
                import_maps.append(planned)
                continue
            if planned.descriptor.is_electron:
                shell = self._electron_shell(payload)
                if shell is None:
                    result.skipped.append({"mod": planned.mod, "part": planned.part, "target": relative, "op": None, "code": "NotApplicable", "message": f"{planned.label}: this payload has no Electron shell (no app.asar above {payload.package_dir}); electron targets apply to desktop bundles only"})
                    self.say(f"SKIP {relative}: no Electron shell on payload {payload.id}")
                    continue
                if shell.integrity_locked:
                    result.skipped.append({"mod": planned.mod, "part": planned.part, "target": relative, "op": None, "code": "IntegrityLocked", "message": f"{planned.label}: {shell.describe()} enforces asar integrity (fuse EnableEmbeddedAsarIntegrityValidation); a rewritten app.asar would be refused at launch, so it is left untouched"})
                    self.say(f"SKIP {relative}: {shell.asar} is integrity-locked by the shell's fuses")
                    continue
                item = by_target.setdefault(shell.asar, _TargetWork(shell.asar, str(shell.asar), [], asar=True))
                item.patches.append(planned)
                continue
            if planned.descriptor.target_is_glob:
                resolved, skip = resolve_chunk_target(payload, relative)
                if resolved is None:
                    result.skipped.append({"mod": planned.mod, "part": planned.part, "target": relative, "op": None, "code": skip.code if skip else "FingerprintMiss", "message": skip.message if skip else relative})
                    if skip:
                        self.say(skip.format())
                    continue
            else:
                resolved = resolve_target(payload, relative)
            try:
                resolved.relative_to(payload.package_dir.parent.resolve())
            except ValueError:
                result.errors.append(f"{planned.label}: target {relative!r} escapes the payload")
                continue
            item = by_target.setdefault(resolved, _TargetWork(resolved, relative, []))
            item.patches.append(planned)
            item.cache_bust = item.cache_bust or planned.descriptor.cache_bust
        ordered = sorted(by_target.values(), key=lambda w: (w.path == payload.index_html, str(w.path)))
        return ordered, import_maps

    def _apply_in_place(
        self,
        payload: Payload,
        item: _TargetWork,
        drift: DriftReport,
        old: DeployManifest,
        new: DeployManifest,
        result: PayloadApply,
        seeds: list[Path],
        skipped_targets: set[str],
    ) -> str | None:
        """Rewrite one target from its recorded original; returns the shell text instead of committing when the target is index.html."""
        key = str(item.path)
        action = drift.actions.get(key, DriftAction.NONE)
        if action is DriftAction.SKIP:
            skipped_targets.add(key)
            result.skipped.append({"target": item.relative, "code": "UserEdited", "message": f"{item.relative} was edited by hand since the last apply; left untouched (Requirement 5.3)"})
            record = old.find_file(key)
            if record is not None:
                new.files.append(record)
            self.say(f"SKIP {item.relative}: user-edited, left untouched")
            return None
        original = self._original_bytes(item.path, action, result)
        if original is None:
            result.errors.append(f"{item.relative}: target file is missing and no backup exists")
            return None
        if item.asar:
            patched = self._patch_asar(payload, item, original, result)
            if patched is None:
                return None
            self._commit_target(payload, item, original, patched, old, new, result)
            return None
        text = original.decode("utf-8", "surrogateescape")
        for planned in item.patches:
            outcome = apply_descriptor(text, planned.descriptor, payload.host_version)
            text = outcome.text
            self._record_outcome(result, planned, outcome)
        if item.path == payload.index_html:
            return text
        patched = text.encode("utf-8", "surrogateescape")
        self._commit_target(payload, item, original, patched, old, new, result)
        if item.cache_bust and patched != original:
            seeds.append(item.path)
        return None

    def _electron_shell(self, payload: Payload) -> ElectronShell | None:
        """The payload's desktop shell, looked up once per payload (``None`` on a gateway-only install)."""
        if payload.id not in self._shells:
            self._shells[payload.id] = find_electron_shell(payload.package_dir)
        return self._shells[payload.id]

    def _patch_asar(self, payload: Payload, item: _TargetWork, original: bytes, result: PayloadApply) -> bytes | None:
        """Apply every ``electron:`` descriptor of ``item`` to its member inside the archive (Requirement 5.11).

        Returns the archive bytes to commit — ``original`` itself when nothing
        changed, so an unchanged archive is never re-serialised — or ``None`` on an
        unreadable archive (recorded as an error, nothing written).
        """
        try:
            archive = AsarArchive.parse(original)
        except AsarError as exc:
            result.errors.append(f"{item.relative}: not an asar archive ({exc}); left untouched")
            return None
        changed = False
        for planned in item.patches:
            member = planned.descriptor.asar_member
            label = planned.descriptor.target
            try:
                data = archive.read(member)
            except AsarError as exc:
                result.skipped.append({"mod": planned.mod, "part": planned.part, "target": label, "op": None, "code": "NotApplicable", "message": f"{planned.label}: {exc}"})
                self.say(f"SKIP {label}: {exc}")
                continue
            text = data.decode("utf-8", "surrogateescape")
            outcome = apply_descriptor(text, planned.descriptor, payload.host_version)
            self._record_outcome(result, planned, outcome)
            if outcome.text != text:
                archive = archive.with_member(member, outcome.text.encode("utf-8", "surrogateescape"))
                changed = True
        return archive.pack() if changed else original

    def _apply_import_maps(
        self,
        payload: Payload,
        patches: list[PlannedPatch],
        index_text: str,
        index_work: _TargetWork,
        drift: DriftReport,
        old: DeployManifest,
        new: DeployManifest,
        result: PayloadApply,
        seeds: list[Path],
        skipped_targets: set[str],
    ) -> str:
        """Spike 1.4: patched copies under ``ui/patched/`` + import-map keys in the shell; entry chunks fall back to the alias graph."""
        for planned in patches:
            outcome = apply_import_map_patch(payload, planned.descriptor, mod=planned.mod, part=planned.part, ui_root=self.ui_root, index_html=index_text, manifest=new)
            if outcome.fallback:
                resolved, _skip = resolve_chunk_target(payload, planned.descriptor.target)
                if resolved is None:
                    continue
                result.applied.append(f"{planned.label} -> alias-graph fallback: {outcome.chunk_name} is the entry chunk, not reachable through the import map")
                self.say(f"  {planned.label}: {outcome.chunk_name} is loaded by <script src>; using the in-place + alias-graph path")
                item = _TargetWork(resolved, planned.descriptor.target, [planned], cache_bust=True)
                self._apply_in_place(payload, item, drift, old, new, result, seeds, skipped_targets)
                continue
            for skip in outcome.skips:
                result.skipped.append({"mod": planned.mod, "part": planned.part, "target": skip.target, "op": skip.op_index, "code": skip.code, "message": skip.message})
                self.say(skip.format())
            if not outcome.wrote_copy:
                continue
            for applied in outcome.result.applied:
                result.applied.append(f"{planned.label} -> {applied.target}#ops/{applied.op_index} (import-map copy {outcome.chunk_name})")
            if outcome.copy_written:
                result.written.append(str(outcome.copy_path))
                self.say(f"patched copy: {outcome.copy_path} (import map {'/assets/' + outcome.chunk_name} -> ui/{PATCHED_DIR}/{outcome.chunk_name})")
            else:
                result.unchanged.append(str(outcome.copy_path))
            if outcome.index_changed:
                result.applied.append(f"{planned.label} -> kiro_crew/static/dist/index.html#importMap")
            index_text = outcome.index_html
            index_work.patches.append(planned)
        return index_text

    def _copy_ui_assets(self, patches: list[PlannedPatch], new: DeployManifest, result: PayloadApply) -> None:
        """Files a descriptor wants under the Loader app's ``ui/`` (boot favicon/logo): copied once, recorded as added."""
        for planned in patches:
            for asset in planned.descriptor.ui_assets:
                dest = (self.ui_root / asset.dest).resolve()
                try:
                    dest.relative_to(self.ui_root.resolve())
                except ValueError:
                    result.errors.append(f"{planned.label}: ui asset {asset.dest!r} escapes the Loader app's ui/")
                    continue
                if not Path(asset.source).is_file():
                    result.errors.append(f"{planned.label}: ui asset source {asset.source} is missing")
                    continue
                data = Path(asset.source).read_bytes()
                if not dest.is_file() or dest.read_bytes() != data:
                    atomic_write_bytes(dest, data)
                    result.written.append(str(dest))
                    self.say(f"ui asset: {dest}")
                else:
                    result.unchanged.append(str(dest))
                new.record_added(dest, sha256_bytes(data), asset.mod or planned.mod)

    def _refresh_patched_index(self) -> None:
        manifests = [m for _path, m in iter_manifests(self.data_home)]
        try:
            refresh_patched_index(self.ui_root, manifests)
        except OSError as exc:
            self.say(f"WARNING could not refresh {self.ui_root / PATCHED_DIR / 'index.json'}: {exc}")

    def _original_bytes(self, path: Path, action: DriftAction, result: PayloadApply) -> bytes | None:
        """The bytes to patch from: the backup (recorded original) or the file itself."""
        backup = self.backups.backup_path(path)
        if action is DriftAction.RE_DERIVE and path.is_file():
            self.backups.refresh(path)  # the host re-laid this file: its bytes are the new original
            return path.read_bytes()
        if backup.is_file():
            return backup.read_bytes()
        if path.is_file():
            return path.read_bytes()
        return None

    def _record_outcome(self, result: PayloadApply, planned: PlannedPatch, outcome: PatchResult) -> None:
        for applied in outcome.applied:
            result.applied.append(f"{planned.label} -> {applied.target}#ops/{applied.op_index}")
        if outcome.import_map_applied:
            result.applied.append(f"{planned.label} -> {planned.descriptor.target}#importMap")
        for skip in outcome.skipped:
            result.skipped.append({"mod": planned.mod, "part": planned.part, "target": skip.target, "op": skip.op_index, "code": skip.code, "message": skip.message})
            self.say(skip.format())

    def _commit_target(self, payload: Payload, item: _TargetWork, original: bytes, patched: bytes, old: DeployManifest, new: DeployManifest, result: PayloadApply) -> None:
        path = item.path
        current = path.read_bytes() if path.is_file() else None
        if patched == original:
            previous = old.find_file(str(path))
            if previous is not None and current is not None and sha256_bytes(current) == previous.patched_sha256 and not self.backups.has_backup(path):
                # still patched from an earlier run whose backup is gone: keep tracking it rather than lose it
                new.files.append(previous)
                result.unchanged.append(str(path))
                result.skipped.append({"target": item.relative, "code": "BackupMissing", "message": f"{item.relative} is patched but its {self.backups.suffix} backup is gone; restore needs the host to re-lay the file"})
                return
            # nothing applies any more: put the original back if we ever changed it
            if self.backups.has_backup(path):
                if current != original:
                    atomic_write_bytes(path, original)
                    result.restored.append(str(path))
                    self.say(f"restored: {path}")
                self.backups.discard(path)
            elif current is None:
                atomic_write_bytes(path, original)
                result.restored.append(str(path))
            return
        if current is not None:
            self.backups.ensure(path)
        if current != patched:
            atomic_write_bytes(path, patched)
            result.written.append(str(path))
            self.say(f"patched: {path}")
        else:
            result.unchanged.append(str(path))
        if not self.backups.has_backup(path):
            atomic_write_bytes(self.backups.backup_path(path), original)
        mods = ";".join(sorted({p.mod for p in item.patches})) or "floofycrew"
        parts = ";".join(p.label for p in item.patches)
        new.record_patch(path, sha256_bytes(original), sha256_bytes(patched), mods, parts)
        moved = sideline_sidecars(path, self.backups, new, mods)
        for sidecar, target in moved:
            self.say(f"  sidelined sidecar: {sidecar.name} -> {target.name}")

    def _retire_stale(self, old: DeployManifest, new: DeployManifest, skipped: set[str], result: PayloadApply) -> None:
        """Undo what the previous manifest recorded and this run did not re-create."""
        new_files = {f.path for f in new.files}
        for record in old.files:
            if record.path in new_files or record.path in skipped:
                continue
            path = Path(record.path)
            if self.backups.restore_file(path):
                result.restored.append(record.path)
                self.say(f"restored: {path}")
        new_added = {a.path for a in new.added}
        for record in old.added:
            if record.path in new_added:
                continue
            path = Path(record.path)
            if path.is_file():
                path.unlink()
                result.removed.append(record.path)
                self.say(f"removed: {path}")
        new_sidelined = {s.path for s in new.sidelined}
        for record in old.sidelined:
            if record.path in new_sidelined:
                continue
            moved = Path(record.moved_to)
            if moved.is_file() and not Path(record.path).exists():
                os.replace(moved, record.path)
                result.restored.append(record.path)
                self.say(f"sidecar back: {record.path}")

    # -- restore ------------------------------------------------------------------------

    def restore(self, payload_ids: Iterable[str] | None = None, *, sweep: bool = True) -> RestoreReport:
        """Manifest-driven restore, then a glob sweep so a lost manifest still restores (Requirement 5.8).

        Files under the Loader app's ``ui/`` (patched chunk copies, spike 1.4) are
        shared by every payload: a restore limited to some payloads keeps a copy
        another payload's manifest still maps to; a restore of everything sweeps
        ``ui/patched/`` clean even when no manifest survived.
        """
        wanted = set(payload_ids) if payload_ids is not None else None
        report = RestoreReport()
        kept_manifests = [m for _p, m in iter_manifests(self.data_home) if wanted is not None and m.payload not in wanted]
        still_referenced = {Path(a.path) for m in kept_manifests for a in m.added}
        for payload in self.payloads:
            if wanted is not None and payload.id not in wanted:
                continue
            restored: list[str] = []
            removed: list[str] = []
            path = self.manifest_path(payload)
            manifest = DeployManifest.load_if_exists(path)
            if manifest is not None:
                for record in manifest.files:
                    if self.backups.restore_file(Path(record.path)):
                        restored.append(record.path)
                for added in manifest.added:
                    if Path(added.path) in still_referenced:
                        continue  # another payload still maps to this ui/ copy
                    if Path(added.path).is_file():
                        Path(added.path).unlink()
                        removed.append(added.path)
                for side in manifest.sidelined:
                    if Path(side.moved_to).is_file():
                        os.replace(side.moved_to, side.path)
                        restored.append(side.path)
            if sweep:
                more_restored, more_removed = self._sweep(payload.package_dir)
                shell = self._electron_shell(payload)
                if shell is not None:
                    shell_restored, shell_removed = self._sweep(shell.resources_dir, recursive=False)
                    more_restored += shell_restored
                    more_removed += shell_removed
                restored.extend(more_restored)
                removed.extend(more_removed)
            if path.exists():
                path.unlink()
                report.manifests_removed.append(str(path))
            report.payloads[payload.id] = {"restored": restored, "removed": removed}
            for line in restored:
                self.say(f"restored: {line}")
            for line in removed:
                self.say(f"removed: {line}")
            self.audit.record("restore", payload=payload.id, files=restored, removed=removed, result="ok")
        if sweep and wanted is None:
            swept = self._sweep_ui_patched()
            if swept:
                report.payloads.setdefault("ui", {"restored": [], "removed": []})["removed"].extend(swept)
                for line in swept:
                    self.say(f"removed: {line}")
        self._refresh_patched_index()
        return report

    def _ui_dirs(self) -> tuple[Path, ...]:
        """The directories FloofyCrew owns under the Loader app's ``ui/``: patched chunk copies and boot assets."""
        return (self.ui_root / PATCHED_DIR, self.ui_root / BOOT_ASSETS_DIR)

    def _sweep_ui_patched(self) -> list[str]:
        """Remove every patched chunk copy, sidecar and boot asset under ``ui/`` — the restore-all sweep."""
        removed: list[str] = []
        for directory in self._ui_dirs():
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    path.unlink()
                    removed.append(str(path))
            for sub in sorted((p for p in directory.rglob("*") if p.is_dir()), reverse=True):
                try:
                    sub.rmdir()
                except OSError:
                    pass
        return removed

    def _sweep(self, root: Path, *, recursive: bool = True) -> tuple[list[str], list[str]]:
        """Every backup under ``root`` goes back over its original; every added file goes away."""
        restored: list[str] = []
        removed: list[str] = []
        if not root.is_dir():
            return restored, removed
        for path in sorted(root.rglob("*") if recursive else root.glob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            if self.backups.is_backup(path):
                original = self.backups.original_of(path)
                os.replace(path, original)
                restored.append(str(original))
            elif is_floofy_added(path):
                path.unlink()
                removed.append(str(path))
        return restored, removed

    # -- status -------------------------------------------------------------------------

    def status(self) -> StatusReport:
        endpoint, served = self._served()
        report = StatusReport(gateway=endpoint.label if endpoint else None, served_version=served)
        for payload in self.payloads:
            path = self.manifest_path(payload)
            manifest = DeployManifest.load_if_exists(path)
            drift = classify_drift(manifest, current_host_version=payload.host_version.text, backups=self.backups) if manifest else DriftReport()
            backups_present = sum(1 for f in (manifest.files if manifest else []) if f.backup_path(self.backups.suffix).is_file())
            report.payloads.append(
                PayloadStatus(
                    payload=payload.id,
                    host_version=payload.host_version.text,
                    edition=payload.edition,
                    root=str(payload.root),
                    current=payload.current,
                    dormant=self._is_dormant(payload, served),
                    served_version=served,
                    manifest=str(path) if manifest else None,
                    patched=len(manifest.files) if manifest else 0,
                    added=len(manifest.added) if manifest else 0,
                    sidelined=len(manifest.sidelined) if manifest else 0,
                    backups_present=backups_present,
                    drift=drift.summary(),
                    mods=sorted({m for f in (manifest.files if manifest else []) for m in f.mod.split(";") if m}),
                )
            )
        return report

    # -- verify -------------------------------------------------------------------------

    def verify(self, payload: Payload, *, endpoint: GatewayEndpoint | None = None) -> VerifyReport:
        """Compare what the gateway serves with the deployment manifest (Requirement 5.7)."""
        manifest = DeployManifest.load_if_exists(self.manifest_path(payload))
        if manifest is None or manifest.is_empty:
            return VerifyReport(payload.id, VerifyStatus.NOTHING, detail="no deployment manifest for this payload")
        if endpoint is None:
            endpoint, served = self._served()
        else:
            served = served_version(endpoint)
        if endpoint is None:
            return VerifyReport(payload.id, VerifyStatus.NO_GATEWAY, detail="no running gateway found (no dashboard socket / loopback port)")
        report = VerifyReport(payload.id, VerifyStatus.VERIFIED, endpoint=endpoint.label, served_version=served)
        if served is not None and served != payload.host_version.text:
            report.status = VerifyStatus.DORMANT
            report.detail = f"gateway serves {served}, this payload is {payload.host_version.text} (dormant, not serving)"
            return report
        expectations: list[tuple[str, str, str]] = []  # (url, expected sha256, label)
        for record in manifest.files:
            url = url_for(payload, Path(record.path))
            if url:
                expectations.append((url, record.patched_sha256, "patched"))
        for added in manifest.added:
            url = url_for(payload, Path(added.path)) or ui_url_for(self.ui_root, Path(added.path))
            if url:
                expectations.append((url, added.sha256, "added"))
        if not expectations:
            report.status = VerifyStatus.NOTHING
            unserved = [Path(r.path).name for r in manifest.files if url_for(payload, Path(r.path)) is None]
            report.detail = "no patched file is served by the dashboard" + (f" ({', '.join(unserved)} live outside it — the Electron shell — and are checked by hash in `floofy status`)" if unserved else "")
            return report
        statuses: list[int] = []
        failures = 0
        for url, expected, label in expectations:
            try:
                response = endpoint.get(url)
            except (OSError, http.client.HTTPException) as exc:
                report.status = VerifyStatus.FAILED
                report.detail = f"{url}: {exc}"
                return report
            statuses.append(response.status)
            actual = sha256_bytes(response.body)
            ok = response.status == 200 and actual == expected and response.encoding == "identity"
            failures += 0 if ok else 1
            report.checks.append({"url": url, "kind": label, "status": response.status, "encoding": response.encoding, "bytes": len(response.body), "match": actual == expected, "ok": ok})
            self.say(f"{url} -> HTTP {response.status}, {len(response.body)}B, enc={response.encoding}, {'OK' if ok else 'MISMATCH'}")
        if statuses and all(s == 404 for s in statuses):
            report.status = VerifyStatus.DORMANT
            report.detail = "the gateway answers 404 for every patched asset (dormant payload, not serving)"
        elif failures:
            report.status = VerifyStatus.FAILED
            report.detail = f"{failures} of {len(expectations)} served file(s) differ from the deployment manifest"
        return report

    # -- gc -----------------------------------------------------------------------------

    def gc(self) -> GcReport:
        """Remove manifests of payloads that no longer exist on disk (Requirement 6.5), and orphaned ``ui/patched/`` copies."""
        report = GcReport()
        known = {p.id for p in self.payloads}
        for path, manifest in list(iter_manifests(self.data_home)):
            root = Path(manifest.payload_root) if manifest.payload_root else None
            vanished = manifest.payload not in known and (root is None or not root.exists())
            if vanished:
                path.unlink()
                report.removed.append(str(path))
                self.audit.record("gc", payload=manifest.payload, files=[str(path)], result="ok")
            else:
                report.kept.append(str(path))
        referenced = {Path(a.path).resolve() for _p, m in iter_manifests(self.data_home) for a in m.added}
        for directory in self._ui_dirs():
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*")):
                if path.is_file() and path.name != "index.json" and path.resolve() not in referenced:
                    path.unlink()
                    report.removed.append(str(path))
        self._refresh_patched_index()
        return report
