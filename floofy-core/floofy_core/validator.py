"""``floofy validate``: everything that can be checked about a mod before it is installed.

Requirement 1.9 (schema), 1.6 (``files[]`` hashes → ``MissingFiles``), 1.8 (archive
path-escape rejection), 1.10 / 11.6 (network scan), 5.10 (engineering-rule
targets rejected) and 11.4 (governance-file targets flagged, never refused).

The result is a :class:`ValidationReport` with ``errors`` (the mod must not be
installed as is) and ``warnings`` (the user decides; findings with
``accept=True`` are the ones the manager asks the user to accept explicitly at
install time). Governance and network findings are always warnings — DR-5: user
consent outranks host governance, governance is a warning, not a gate.

Edition-neutral: adapters pass an extended :class:`TargetPolicy`; the core knows
no internal identifier.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .archive import ArchiveError, extracted_archive, is_archive, open_archive_safely
from .netscan import host_matches, is_loopback_host, scan_credentials, scan_urls
from .patches import ELECTRON_TARGET_PREFIX
from .schema import load_schema
from .schema.check import SchemaError, validate_instance
from .semver import HostVersion, InvalidRange, InvalidVersion, Range, Version
from .targets import DEFAULT_TARGET_POLICY, TargetClass, TargetPolicy

__all__ = [
    "Finding",
    "MANIFEST_NAME",
    "SchemaError",
    "TargetPolicy",
    "ValidationReport",
    "open_archive_safely",
    "sha256_file",
    "validate_instance",
    "validate_mod",
]

MANIFEST_NAME = "floofy.json"

#: Files a mod directory may contain without being listed in ``files[]``.
_UNLISTED_OK: frozenset[str] = frozenset({MANIFEST_NAME})
_SKIP_DIRS: frozenset[str] = frozenset({".git", "__pycache__", "node_modules", ".hypothesis", ".pytest_cache"})
_DEPENDENCY_MAPS = ("dependsOn", "recommends", "suggests", "conflicts", "breaks")
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


@dataclass(frozen=True)
class Finding:
    """One validation result.

    ``code`` is stable and machine-readable; ``path`` is a JSON pointer into
    ``floofy.json`` (``/files/2``), a mod-relative file path, or ``file:line``.
    ``accept`` marks warnings the user may accept explicitly at install
    (governance-altering targets, plaintext or undeclared network use).
    """

    code: str
    message: str
    path: str = ""
    severity: str = "error"  # "error" | "warning" | "info"
    accept: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def format(self) -> str:
        flag = " [accept]" if self.accept else ""
        where = f" {self.path}:" if self.path else ""
        return f"{self.severity:<7} {self.code}{flag}{where} {self.message}"


@dataclass
class ValidationReport:
    """Outcome of :func:`validate_mod`."""

    source: str
    manifest: dict[str, Any] | None = None
    errors: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def mod_id(self) -> str | None:
        return self.manifest.get("id") if isinstance(self.manifest, dict) else None

    def error(self, code: str, message: str, path: str = "") -> None:
        self.errors.append(Finding(code, message, path, "error"))

    def warn(self, code: str, message: str, path: str = "", *, accept: bool = False, severity: str = "warning") -> None:
        self.warnings.append(Finding(code, message, path, severity, accept))

    def codes(self) -> set[str]:
        return {f.code for f in (*self.errors, *self.warnings)}

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "id": self.mod_id,
            "version": self.manifest.get("version") if isinstance(self.manifest, dict) else None,
            "ok": self.ok,
            "errors": [f.to_dict() for f in self.errors],
            "warnings": [f.to_dict() for f in self.warnings],
        }

    def format(self) -> str:
        head = f"floofy validate: {self.source}"
        if self.mod_id:
            head += f" ({self.mod_id} {self.manifest.get('version', '?')})"
        lines = [head, *(f"  {f.format()}" for f in (*self.errors, *self.warnings))]
        verdict = "FAIL" if self.errors else "OK"
        lines.append(f"{verdict}: {len(self.errors)} error(s), {len(self.warnings)} warning(s)")
        return "\n".join(lines)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --- entry point -------------------------------------------------------------------


def validate_mod(
    root: Path | str | None = None,
    *,
    archive: Path | str | None = None,
    targets: TargetPolicy | None = None,
) -> ValidationReport:
    """Validate the mod at ``root`` (a directory) or inside ``archive``.

    Exactly one of ``root`` / ``archive`` is given; a ``root`` that is an archive
    file is treated as ``archive``. Archives are inspected and rejected before any
    extraction (Requirement 1.8) and extracted to a temporary directory that is
    removed afterwards.
    """
    policy = targets or DEFAULT_TARGET_POLICY
    if (root is None) == (archive is None):
        raise TypeError("validate_mod() needs exactly one of root= or archive=")
    if root is not None and is_archive(Path(root)):
        archive, root = root, None
    if archive is not None:
        source = str(archive)
        try:
            with extracted_archive(Path(archive)) as extracted:
                return _validate_directory(extracted, policy, source)
        except ArchiveError as exc:
            report = ValidationReport(source)
            report.error("UnsafeArchive", str(exc), exc.entry or "")
            return report
    return _validate_directory(Path(root), policy, str(root))  # type: ignore[arg-type]


def _validate_directory(root: Path, policy: TargetPolicy, source: str) -> ValidationReport:
    report = ValidationReport(source)
    if not root.is_dir():
        report.error("InvalidManifest", f"{root} is not a directory")
        return report
    root = root.resolve()
    manifest = _load_manifest(root, report)
    if manifest is None:
        return report
    report.manifest = manifest
    _check_schema(manifest, report)
    _check_versions(manifest, report)
    _check_files(root, manifest, report)
    _check_parts(root, manifest, report, policy)
    _check_governance_files(manifest, report, policy)
    _check_network(root, manifest, report)
    return report


# --- manifest and schema -----------------------------------------------------------


def _load_manifest(root: Path, report: ValidationReport) -> dict[str, Any] | None:
    path = root / MANIFEST_NAME
    if not path.is_file():
        report.error("InvalidManifest", f"no {MANIFEST_NAME} at the mod root", MANIFEST_NAME)
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        report.error("InvalidManifest", f"{MANIFEST_NAME} is not valid JSON: {exc}", MANIFEST_NAME)
        return None
    if not isinstance(document, dict):
        report.error("InvalidManifest", f"{MANIFEST_NAME} must be a JSON object", MANIFEST_NAME)
        return None
    return document


def _check_schema(manifest: dict[str, Any], report: ValidationReport) -> None:
    """Requirement 1.9: JSON Schema conformance; the framework dependency gets its own code."""
    for error in validate_instance(manifest, load_schema("floofy")):
        if error.path == "/dependsOn" and error.keyword == "required" and "floofycrew" in error.details:
            report.error(
                "MissingFrameworkDependency",
                "dependsOn must include 'floofycrew' (the framework version is the hard dependency, Requirement 1.4)",
                error.path,
            )
        else:
            report.error("SchemaViolation", error.message, error.path)


def _check_versions(manifest: dict[str, Any], report: ValidationReport) -> None:
    """SemVer and range parsing beyond what the schema patterns bound; self references."""
    version = manifest.get("version")
    if isinstance(version, str):
        try:
            Version.parse(version)
        except InvalidVersion as exc:
            report.error("InvalidVersion", str(exc), "/version")
    kirocrew = manifest.get("kirocrew")
    if isinstance(kirocrew, dict) and isinstance(kirocrew.get("version"), str):
        _check_range(kirocrew["version"], "/kirocrew/version", report)
    own_id = manifest.get("id")
    for name in _DEPENDENCY_MAPS:
        mapping = manifest.get(name)
        if not isinstance(mapping, dict):
            continue
        for dep_id, spec in mapping.items():
            pointer = f"/{name}/{dep_id}"
            range_text = spec.get("range") if isinstance(spec, dict) else spec
            if isinstance(range_text, str):
                _check_range(range_text, pointer if not isinstance(spec, dict) else pointer + "/range", report)
            if dep_id == own_id:
                report.error("SelfReference", f"{name} must not name the mod itself", pointer)
    for name in ("loadBefore", "loadAfter"):
        entries = manifest.get(name)
        if isinstance(entries, list) and own_id in entries:
            report.error("SelfReference", f"{name} must not name the mod itself", f"/{name}")


def _check_range(text: str, pointer: str, report: ValidationReport) -> None:
    try:
        Range.parse(text)
    except InvalidRange as exc:
        report.error("InvalidRange", str(exc), pointer)


# --- files -------------------------------------------------------------------------


def _relative_inside(root: Path, rel: str) -> Path | None:
    """Resolve ``rel`` under ``root``; ``None`` when it escapes (symlinks included)."""
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel:
        return None
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _shipped_files(root: Path) -> list[str]:
    found: list[str] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in _SKIP_DIRS or part.startswith(".") for part in rel.parts):
            continue
        if path.is_file() and not path.is_symlink():
            found.append(rel.as_posix())
    return found


def _check_files(root: Path, manifest: dict[str, Any], report: ValidationReport) -> None:
    """Requirement 1.6: every listed file exists with the listed SHA-256 → else ``MissingFiles``."""
    files = manifest.get("files")
    if not isinstance(files, list):
        return
    listed: set[str] = set()
    for index, entry in enumerate(files):
        pointer = f"/files/{index}"
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue  # the schema already reported the shape
        rel = entry["path"]
        if rel in listed:
            report.error("DuplicateFile", f"{rel} is listed twice", pointer)
        listed.add(rel)
        if rel == MANIFEST_NAME:
            report.error("InvalidManifest", f"files[] must not list {MANIFEST_NAME} itself", pointer)
            continue
        target = _relative_inside(root, rel)
        if target is None:
            report.error("PathEscape", f"{rel} escapes the mod root", pointer)
            continue
        if not target.is_file():
            report.error("MissingFiles", f"listed file is missing: {rel}", pointer)
            continue
        expected = entry.get("sha256")
        actual = sha256_file(target)
        if isinstance(expected, str) and expected.lower() != actual:
            report.error("MissingFiles", f"sha256 mismatch for {rel}: manifest {expected[:12]}…, file {actual[:12]}…", pointer)
    for rel in _shipped_files(root):
        if rel not in listed and rel not in _UNLISTED_OK:
            report.warn("UnlistedFile", f"shipped file not listed in files[] (its hash cannot be verified): {rel}", rel, accept=True)


# --- parts -------------------------------------------------------------------------


def _check_parts(root: Path, manifest: dict[str, Any], report: ValidationReport, policy: TargetPolicy) -> None:
    parts = manifest.get("parts")
    if not isinstance(parts, list):
        return
    listed = {e["path"] for e in manifest.get("files", []) if isinstance(e, dict) and isinstance(e.get("path"), str)}
    for index, part in enumerate(parts):
        pointer = f"/parts/{index}"
        if not isinstance(part, dict):
            continue
        kind = part.get("kind")
        rel = part.get("path")
        target: Path | None = None
        if isinstance(rel, str):
            target = _relative_inside(root, rel)
            if target is None:
                report.error("PathEscape", f"part path {rel} escapes the mod root", pointer + "/path")
                continue
            if not target.exists():
                report.error("MissingFiles", f"part path does not exist: {rel}", pointer + "/path")
                continue
            if target.is_file() and rel not in listed:
                report.error("UnverifiedPart", f"part file {rel} is not listed in files[], so its hash cannot be verified", pointer + "/path")
            elif target.is_dir():
                prefix = target.relative_to(root).as_posix().rstrip("/") + "/"
                for shipped in _shipped_files(root):
                    if shipped.startswith(prefix) and shipped not in listed:
                        report.error("UnverifiedPart", f"file {shipped} inside part {rel} is not listed in files[]", pointer + "/path")
        elif kind != "config":
            continue  # the schema reports the missing path
        if kind == "patch" and target is not None and target.is_file():
            _check_patch_descriptor(root, rel, target, pointer, report, policy, side=str(part.get("side") or ""))
        elif kind == "python-hook" and target is not None:
            _check_python_hook(part, rel, target, pointer, report)
        elif kind == "ui" and target is not None:
            _check_ui_part(root, part, rel, target, pointer, report, listed)
        elif target is not None and target.is_file() and target.suffix.lower() == ".json":
            _check_json_file(rel, target, pointer, report)


def _check_json_file(rel: str, target: Path, pointer: str, report: ValidationReport) -> Any:
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        report.error("InvalidPart", f"{rel} is not valid JSON: {exc}", pointer + "/path")
        return None


def _check_python_hook(part: dict[str, Any], rel: str, target: Path, pointer: str, report: ValidationReport) -> None:
    """Requirement 2.4: a module file or a package directory with ``__init__.py``."""
    module = part.get("module")
    if isinstance(module, str) and not _MODULE_RE.match(module):
        report.error("InvalidPart", f"module {module!r} is not a dotted Python name", pointer + "/module")
    if target.is_dir():
        if not (target / "__init__.py").is_file():
            report.error("InvalidPart", f"python-hook directory {rel} has no __init__.py", pointer + "/path")
    elif target.suffix != ".py":
        report.error("InvalidPart", f"python-hook path {rel} must be a .py file or a package directory", pointer + "/path")


def _check_ui_part(root: Path, part: dict[str, Any], rel: str, target: Path, pointer: str, report: ValidationReport, listed: set[str]) -> None:
    """Requirement 16.4: the page's ``entry`` (and ``icon``) exist, sit under ``path``, and are listed in ``files[]``.

    The App imports ``entry`` same-origin and mounts its default export, so the
    module must be a shipped, hash-listed file the validator's network scan
    (Requirement 1.10) already covers like any other code; ``side`` is ``spa`` by
    schema — the page runs in the dashboard document.
    """
    for key, what in (("entry", "ui entry"), ("icon", "ui icon")):
        value = part.get(key)
        if not isinstance(value, str):
            continue  # the schema reports a missing or malformed entry
        file = _relative_inside(root, value)
        if file is None:
            report.error("PathEscape", f"{what} {value} escapes the mod root", pointer + "/" + key)
            continue
        if not file.is_file():
            report.error("MissingFiles", f"{what} does not exist: {value}", pointer + "/" + key)
            continue
        if value not in listed:
            report.error("UnverifiedPart", f"{what} {value} is not listed in files[], so its hash cannot be verified", pointer + "/" + key)
        if target.is_dir():
            try:
                file.relative_to(target.resolve())
            except ValueError:
                report.error("InvalidPart", f"{what} {value} lies outside the part's directory {rel}", pointer + "/" + key)
        elif key == "entry" and file != target:
            report.error("InvalidPart", f"ui part path {rel} is a file but entry names another file {value}; point path at the page's directory", pointer + "/entry")


def _check_patch_descriptor(root: Path, rel: str, target: Path, pointer: str, report: ValidationReport, policy: TargetPolicy, *, side: str = "") -> None:
    """Requirement 5.5 shape, 5.10 engineering-rule targets, 5.11 electron targets, 11.4 governance flags."""
    descriptor = _check_json_file(rel, target, pointer, report)
    if descriptor is None:
        return
    where = f"{rel}"
    errors = validate_instance(descriptor, load_schema("patch"))
    for error in errors:
        report.error("InvalidPatch", error.message, f"{where}#{error.path}")
    if errors or not isinstance(descriptor, dict):
        return
    if isinstance(descriptor.get("appliesTo"), str):
        try:
            Range.parse(descriptor["appliesTo"])
        except InvalidRange as exc:
            report.error("InvalidRange", str(exc), f"{where}#/appliesTo")
    for key in ("fromBuild", "toBuild"):
        if isinstance(descriptor.get(key), str):
            try:
                HostVersion.parse(descriptor[key])
            except InvalidVersion as exc:
                report.error("InvalidPatch", str(exc), f"{where}#/{key}")
    for index, op in enumerate(descriptor.get("ops", [])):
        if isinstance(op, dict) and op.get("regex") and isinstance(op.get("fingerprint"), str):
            try:
                re.compile(op["fingerprint"])
            except re.error as exc:
                report.error("InvalidPatch", f"fingerprint is not a valid regular expression: {exc}", f"{where}#/ops/{index}/fingerprint")
    _classify_target(descriptor.get("target"), f"{where}#/target", report, policy, what="patch target")
    target_text = descriptor.get("target")
    if isinstance(target_text, str) and target_text.startswith(ELECTRON_TARGET_PREFIX):
        member = target_text[len(ELECTRON_TARGET_PREFIX) :].strip("/")
        if not member:
            report.error("InvalidPatch", "an electron: target must name a member inside app.asar (electron:<path/inside/app.asar>)", f"{where}#/target")
        if side and side != "electron":
            report.warn(
                "SideMismatch",
                f"the descriptor targets the desktop shell's app.asar ({target_text!r}) but the part declares side {side!r}; declare side \"electron\" so the manager describes it as a desktop-shell change (Requirement 5.11)",
                pointer,
                accept=True,
            )
        if isinstance(descriptor.get("mode"), str) and descriptor["mode"] != "in-place":
            report.error("InvalidPatch", f"an electron: target is always rewritten in place; mode {descriptor['mode']!r} does not apply to app.asar members", f"{where}#/mode")
    elif side == "electron" and isinstance(target_text, str):
        report.warn("SideMismatch", f"the part declares side \"electron\" but its target {target_text!r} is a payload file, not an electron:<member> of app.asar", pointer, accept=True)


def _classify_target(path: Any, pointer: str, report: ValidationReport, policy: TargetPolicy, *, what: str) -> None:
    if not isinstance(path, str):
        return
    verdict = policy.classify(path)
    pattern = policy.matching_pattern(path)
    if verdict is TargetClass.ENGINEERING_RULE:
        report.error(
            "EngineeringRuleTarget",
            f"{what} {path!r} is host code ({pattern}); the host's Python sources and launcher are never patched in place "
            "(Requirement 5.10) — use a python-hook part instead",
            pointer,
        )
    elif verdict is TargetClass.GOVERNANCE:
        report.warn(
            "GovernanceAltering",
            f"{what} {path!r} is a host governance file ({pattern}); installing requires an explicit per-file "
            "confirmation and is recorded in the audit log (Requirement 11.4)",
            pointer,
            accept=True,
        )


def _check_governance_files(manifest: dict[str, Any], report: ValidationReport, policy: TargetPolicy) -> None:
    """Requirement 11.4: shipped files that would land on governance paths are flagged, never refused."""
    files = manifest.get("files")
    if not isinstance(files, list):
        return
    for index, entry in enumerate(files):
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            if policy.classify(entry["path"]) is TargetClass.GOVERNANCE:
                _classify_target(entry["path"], f"/files/{index}/path", report, policy, what="shipped file")


# --- network -----------------------------------------------------------------------


def _check_network(root: Path, manifest: dict[str, Any], report: ValidationReport) -> None:
    """Requirement 1.10, 11.6 d: plaintext non-loopback and undeclared hosts are warnings the user may accept."""
    network = manifest.get("network") if isinstance(manifest.get("network"), dict) else {}
    declared = [h for h in network.get("hosts", []) if isinstance(h, str)]
    for hit in scan_urls(root):
        where = f"{hit.path}:{hit.line}"
        if is_loopback_host(hit.host):
            continue
        if hit.plaintext:
            report.warn(
                "PlaintextNetwork",
                f"plaintext {hit.scheme}:// URL to {hit.host}; mod traffic must be encrypted except to loopback (Requirement 11.6)",
                where,
                accept=True,
            )
            continue
        if not any(host_matches(hit.host, pattern, hit.effective_port) for pattern in declared):
            report.warn(
                "UndeclaredHost",
                f"{hit.scheme}:// URL to {hit.host}, which is not declared in network.hosts[] (Requirement 1.10)",
                where,
                accept=True,
            )
    credentials = bool(network.get("credentials"))
    if not credentials:
        hint = scan_credentials(root)
        if hint is not None:
            report.warn(
                "CredentialsHint",
                "code mentions credentials but network.credentials is false; set it to true if the mod asks the user for any",
                f"{hint[0]}:{hint[1]}",
                severity="info",
            )
