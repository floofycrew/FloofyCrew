"""``validate-submission`` — the CI gate a registry pull request must pass (Requirement 8.7).

Given the mod (a directory or a ``.zip``/``.tar.gz`` archive — the release asset
the record names) and optionally the registry repository the PR targets, check:

1. **schema and validator** — ``floofy validate`` (schema, SemVer ranges, every
   ``files[]`` entry present with a matching ``sha256``, engineering-rule targets,
   network scan) must report zero errors; warnings are listed, not fatal;
2. **hashes of the archive** — when an archive was given: its own ``sha256`` and
   size must equal the ``release.json`` record's ``files[]`` entry for it;
3. **licence** — ``license`` in the manifest **and** a ``LICENSE``/``LICENCE``/
   ``COPYING`` file at the mod root;
4. **no host-name masquerade** — :mod:`registry_tools.masquerade` on ``id`` and
   ``name``, unless the registry's ``masquerade-allow.txt`` lists the id;
5. **release tag** — ``--tag`` (the release the assets were published under) must
   equal the manifest ``version``, or ``v<version>``, or the record's ``tag``;
6. **record consistency** — with ``--registry``: ``mods/<id>/<version>/floofy.json``
   must exist and be byte-for-byte the archive's manifest (canonical comparison),
   ``release.json`` must list at least one HTTPS file with ``sha256``/``size``, and
   the mod's repository must be known.

Every finding is a :class:`Finding` with a stable ``code``; ``ok`` means no
error-level finding. ``--json`` prints the whole report for the workflow log.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from floofy_core.archive import ArchiveError, is_archive, open_archive_safely
from floofy_core.canonical import canonical_bytes
from floofy_core.validator import validate_mod

from .masquerade import DEFAULT_TERMS, check_masquerade
from .repo import RegistryRepo, RepoError

__all__ = ["Finding", "LICENSE_FILE_NAMES", "SubmissionReport", "validate_submission"]

LICENSE_FILE_NAMES = ("license", "licence", "copying", "unlicense")


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    severity: str = "error"  # error | warning

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "severity": self.severity}

    def format(self) -> str:
        return f"{self.severity.upper()} {self.code}: {self.message}"


@dataclass
class SubmissionReport:
    source: str
    mod_id: str | None = None
    version: str | None = None
    findings: list[Finding] = field(default_factory=list)
    validation: dict[str, Any] | None = None

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, code: str, message: str) -> None:
        self.findings.append(Finding(code, message, "error"))

    def warn(self, code: str, message: str) -> None:
        self.findings.append(Finding(code, message, "warning"))

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "id": self.mod_id, "version": self.version, "ok": self.ok, "findings": [f.to_dict() for f in self.findings], "validation": self.validation}

    def format(self) -> str:
        head = f"validate-submission: {self.source}" + (f" ({self.mod_id} {self.version})" if self.mod_id else "")
        return "\n".join([head, *(f"  {f.format()}" for f in self.findings), f"{'OK' if self.ok else 'FAIL'}: {len(self.errors)} error(s), {len(self.findings) - len(self.errors)} warning(s)"])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _has_license_file(root: Path) -> bool:
    for child in root.iterdir():
        if child.is_file() and child.name.lower().split(".", 1)[0] in LICENSE_FILE_NAMES:
            return True
    return False


def validate_submission(
    mod: Path,
    *,
    registry: Path | None = None,
    tag: str | None = None,
    terms: tuple[str, ...] | list[str] = DEFAULT_TERMS,
    allowlist: set[str] | None = None,
) -> SubmissionReport:
    mod = Path(mod)
    report = SubmissionReport(str(mod))
    repo: RegistryRepo | None = None
    if registry is not None:
        try:
            repo = RegistryRepo.load(Path(registry))
        except RepoError as exc:
            report.error("Registry", str(exc))
    cleared = set(allowlist or set()) | (repo.allowlist() if repo is not None else set())

    scratch: Path | None = None
    archive_sha: str | None = None
    archive_size: int | None = None
    try:
        if is_archive(mod):
            archive_sha, archive_size = _sha256(mod), mod.stat().st_size
            scratch = Path(tempfile.mkdtemp(prefix="floofy-submission-"))
            try:
                root = open_archive_safely(mod, scratch)
            except ArchiveError as exc:
                report.error("UnsafeArchive", f"{exc} (Requirement 1.8)")
                return report
        elif mod.is_dir():
            root = mod
        else:
            report.error("NotAMod", f"{mod} is neither a mod directory nor a .zip/.tar.gz archive")
            return report

        # 1. schema + floofy validate (files[] hashes included)
        validation = validate_mod(root)
        report.validation = validation.to_dict()
        manifest = validation.manifest or {}
        report.mod_id = manifest.get("id") if isinstance(manifest.get("id"), str) else None
        report.version = manifest.get("version") if isinstance(manifest.get("version"), str) else None
        for finding in validation.errors:
            report.error(f"Validate:{finding.code}", finding.format())
        for finding in validation.warnings:
            report.warn(f"Validate:{finding.code}", finding.format())
        if report.mod_id is None or report.version is None:
            report.error("Manifest", "floofy.json has no usable id/version; the remaining checks need them")
            return report

        # 3. licence
        if not (isinstance(manifest.get("license"), str) and manifest["license"].strip()):
            report.error("License", "the manifest declares no `license`")
        if not _has_license_file(root):
            report.error("LicenseFile", "no LICENSE / LICENCE / COPYING file at the mod root")

        # 4. masquerade
        for hit in check_masquerade(report.mod_id, manifest.get("name") if isinstance(manifest.get("name"), str) else None, terms=terms, allowlist=cleared):
            report.error("Masquerade", hit.format() + " (list the id in masquerade-allow.txt if this is legitimate)")

        # 5. release tag
        if tag is not None:
            accepted = {report.version, f"v{report.version}"}
            record_tag: str | None = None
            if repo is not None:
                try:
                    record_tag = repo.read_version(report.mod_id, report.version).tag
                except RepoError:
                    record_tag = None
            if record_tag:
                accepted.add(record_tag)
            if tag not in accepted:
                report.error("ReleaseTag", f"release tag {tag!r} does not match the mod version {report.version} (accepted: {', '.join(sorted(accepted))}); Requirement 8.2 tags a release identically to the version")

        # 2 + 6. the record in the registry repository
        if repo is not None:
            try:
                record = repo.read_version(report.mod_id, report.version)
            except RepoError as exc:
                report.error("Record", f"no usable record for {report.mod_id}@{report.version}: {exc}")
                return report
            if canonical_bytes(record.manifest) != canonical_bytes(manifest):
                report.error("RecordManifest", f"mods/{report.mod_id}/{report.version}/floofy.json differs from the submitted mod's floofy.json")
            files = record.files
            if not files:
                report.error("RecordFiles", "release.json lists no files[]")
            for entry in files:
                url = entry.get("url")
                if not (isinstance(url, str) and url.startswith("https://")):
                    report.error("RecordFileUrl", f"release.json file url {url!r} is not https")
                digest, size = entry.get("sha256"), entry.get("size")
                if not (isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)):
                    report.error("RecordFileHash", f"release.json file {url!r} has no 64-hex sha256")
                if not (isinstance(size, int) and not isinstance(size, bool) and size >= 0):
                    report.error("RecordFileSize", f"release.json file {url!r} has no integer size")
            if archive_sha is not None:
                matching = [f for f in files if f.get("sha256") == archive_sha]
                if not matching:
                    report.error("ArchiveHash", f"the submitted archive (sha256 {archive_sha[:12]}…, {archive_size} bytes) matches no files[] entry of release.json")
                elif any(f.get("size") != archive_size for f in matching):
                    report.error("ArchiveSize", f"release.json size {matching[0].get('size')} differs from the archive's {archive_size} bytes")
            try:
                mod_record = repo.read_mod(report.mod_id)
                if mod_record.repo() is None:
                    report.error("RecordRepo", f"mods/{report.mod_id}/mod.json names no `repo` and the manifest has no links.repo")
            except RepoError as exc:
                report.error("Record", str(exc))
            app = record.app_facts()
            if app is not None:
                app_json = root / str(record.parts[0].get("path") or "app.json")
                if app_json.is_file():
                    try:
                        declared_name = json.loads(app_json.read_text(encoding="utf-8")).get("name")
                    except (OSError, ValueError, AttributeError):
                        declared_name = None
                    if isinstance(declared_name, str) and declared_name != app["name"]:
                        report.error("AppName", f"app.json names the app {declared_name!r} but the registry row would list {app['name']!r} (set release.json \"app\": {{\"name\": \"{declared_name}\"}})")
        return report
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
