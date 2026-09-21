"""The build gate of {{PACKAGE_NAME}}: every record must hold before it reaches the default branch.

``index.json`` at the root of this repository is read by every FloofyCrew client
of the {{EDITION}} edition; there is no publish step between a merge and those
clients, so review plus this gate are all that stands between a bad record and
every user's manager. The gate therefore runs in the package build
(`{{GATE_COMMAND}}`) and fails it, not just a developer's terminal; the same
checks run without the build system as ``PYTHONPATH=src python -m {{GATE_MODULE}}``.

What it checks, in order (Requirement 8.7, 8.9, 8.10):

1. ``registry.json`` names this registry, its signing key and the repository
   pattern link records must match;
2. every ``mods/<id>/mod.json`` names a ``contact`` ({{CONTACT_KIND}}) and the
   mod's ``id`` and ``name`` claim neither the host, its vendor nor a reserved
   product name (``schema/reserved-names.json``: ``leadingToken`` names match
   exactly or as the leading dash-token, ``exactOnly`` names match only
   themselves);
3. every version record is well-formed: a link record's ``repo`` matches the
   pattern, its tag is the version (``<version>``, ``v<version>`` or
   ``<id>-<version>``), its ``manifestSha256`` is the hash of the record's own
   ``floofy.json``; the manifest declares a ``license`` and lists a licence file,
   and every ``network.hosts[]`` entry is a plain host (encrypted-only is the
   manager's rule, enforced on the checkout in step 6);
4. ``index.json`` and ``app-registry.json`` are exactly what the records
   rebuild to (``registry_tools build --check``), and the README's mod table is
   what ``index.json`` renders to (``registry_tools readme --check``);
5. ``index.json.sig`` and ``compat.json.sig`` verify against the pinned public
   key (``schema/signing-key.pub.json``, the key clients trust);
6. every link record is **cloned at its tag** and run through the submission
   validator (``registry_tools validate-submission --record``): commit,
   canonical-manifest hash and every file must equal the record; ``floofy
   validate`` must report zero errors; licence, masquerade and tag rules apply.
   This step reaches the forge with the builder's own credentials; setting
   ``{{GATE_ENV_PREFIX}}_SKIP_CLONE=1`` skips it for an offline local run and says
   so — the merge gate never sets it.

The checks are implemented by the FloofyCrew tooling vendored under
``vendor/`` (``floofy_core`` and ``registry_tools``, byte copies of the FloofyCrew
commit named in ``RENDERED.md``; standard library only), so this package builds
with no dependency beyond Python and pytest. Rendered by
``registry-tools/scripts/init_registry_repo.sh`` from the FloofyCrew repository
(``registry-tools/templates/registry-gate/``); regenerate it there rather than
editing here.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

__all__ = [
    "GateReport",
    "ReservedNames",
    "SKIP_CLONE_ENV",
    "find_repository_root",
    "load_reserved_names",
    "main",
    "reserved_name_hit",
    "validate_registry",
]

SKIP_CLONE_ENV = "{{GATE_ENV_PREFIX}}_SKIP_CLONE"
CONTACT_PATTERN = re.compile(r"{{CONTACT_PATTERN}}")
_LICENSE_NAMES = ("license", "licence", "copying", "unlicense")
_HOST_RE = re.compile(r"^(\*\.)?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*(:[0-9]{1,5})?$")


def find_repository_root(start: Path | None = None) -> Path:
    """The directory holding ``registry.json`` and ``vendor/``: searched upwards from ``start`` (default: the working directory, then this file)."""
    candidates = [Path(start) if start is not None else Path.cwd(), Path(__file__).resolve().parent]
    for origin in candidates:
        for directory in [origin, *origin.parents]:
            if (directory / "registry.json").is_file() and (directory / "vendor").is_dir():
                return directory
    raise FileNotFoundError("cannot find the registry repository root (a directory holding registry.json and vendor/); run from inside the package")


def _use_vendored(root: Path) -> None:
    vendor = str(root / "vendor")
    if vendor not in sys.path:
        sys.path.insert(0, vendor)


@dataclass(frozen=True)
class ReservedNames:
    """``schema/reserved-names.json``: two sorted, de-duplicated arrays of lower-case slugs."""

    exact_only: tuple[str, ...]
    leading_token: tuple[str, ...]


def load_reserved_names(root: Path) -> ReservedNames:
    """Load the reserved list; a missing file is a hard error (absence would silently retire the rule)."""
    path = root / "schema" / "reserved-names.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path}: expected an object with exactOnly and leadingToken arrays")
    out: dict[str, tuple[str, ...]] = {}
    for key in ("exactOnly", "leadingToken"):
        values = document.get(key)
        if not isinstance(values, list) or not all(isinstance(v, str) and re.match(r"^[a-z0-9][a-z0-9-]*$", v) for v in values):
            raise ValueError(f"{path}: {key} must be an array of lower-case dashed slugs")
        if values != sorted(set(values)):
            raise ValueError(f"{path}: {key} must be sorted and de-duplicated")
        out[key] = tuple(values)
    return ReservedNames(exact_only=out["exactOnly"], leading_token=out["leadingToken"])


def reserved_name_hit(name: str, reserved: ReservedNames) -> str | None:
    """The reserved name ``name`` claims, or ``None``: ``leadingToken`` names match exactly or as the leading dash-token, ``exactOnly`` names only exactly."""
    slug = name.strip().lower().replace("_", "-").replace(" ", "-")
    if slug in reserved.exact_only:
        return slug
    for token in reserved.leading_token:
        if slug == token or slug.startswith(token + "-"):
            return token
    return None


@dataclass
class GateReport:
    root: Path
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    records: int = 0
    cloned: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems

    def problem(self, text: str) -> None:
        self.problems.append(text)

    def format(self) -> str:
        lines = [f"{{PACKAGE_NAME}} gate: {self.root} — {self.records} record(s), {self.cloned} cloned"]
        lines += [f"  note: {n}" for n in self.notes]
        lines += [f"  PROBLEM: {p}" for p in self.problems]
        lines.append("OK" if self.ok else f"FAIL: {len(self.problems)} problem(s)")
        return "\n".join(lines)


def _has_license_file(files: Iterable[dict[str, Any]]) -> bool:
    for entry in files:
        path = entry.get("path") if isinstance(entry, dict) else None
        if isinstance(path, str) and "/" not in path and path.lower().split(".", 1)[0] in _LICENSE_NAMES:
            return True
    return False


def validate_registry(root: Path | None = None, *, clone: bool | None = None) -> GateReport:
    """Run every check over the repository at ``root``; ``clone`` (default: unless :data:`SKIP_CLONE_ENV` is ``1``) controls step 6."""
    root = Path(root) if root is not None else find_repository_root()
    _use_vendored(root)
    from floofy_core.canonical import canonical_bytes  # noqa: PLC0415
    from floofy_core.schema import load_schema  # noqa: PLC0415
    from floofy_core.schema.check import validate_instance  # noqa: PLC0415
    from floofy_core.signing import load_public_key_record  # noqa: PLC0415
    from floofy_core.sigverify import verify_detached  # noqa: PLC0415
    from registry_tools.app_registry import app_registry_matches  # noqa: PLC0415
    from registry_tools.build import build_index, index_matches  # noqa: PLC0415
    from registry_tools.links import manifest_hash_matches, repo_matches_pattern  # noqa: PLC0415
    from registry_tools.masquerade import check_masquerade  # noqa: PLC0415
    from registry_tools.readme import readme_matches  # noqa: PLC0415
    from registry_tools.repo import RegistryRepo, RepoError  # noqa: PLC0415
    from registry_tools.submission import accepted_tags, validate_submission  # noqa: PLC0415

    import hashlib  # noqa: PLC0415

    report = GateReport(root)
    clone = (os.environ.get(SKIP_CLONE_ENV, "").strip() != "1") if clone is None else clone
    try:
        repo = RegistryRepo.load(root)
    except RepoError as exc:
        report.problem(str(exc))
        return report
    # 1. registry.json
    if repo.record_kind == "link" and not repo.repo_url_pattern:
        report.problem("registry.json: a link registry must pin its forge with repoUrlPattern (Requirement 8.10)")
    if not repo.key_id:
        report.problem("registry.json: keyId is missing (the index must be signed with the pinned key)")
    try:
        reserved = load_reserved_names(root)
    except (OSError, ValueError) as exc:
        report.problem(f"reserved names: {exc}")
        reserved = ReservedNames((), ())
    allow = repo.allowlist()
    # 2 + 3. the records
    try:
        mods = list(repo.mods())
    except RepoError as exc:
        report.problem(str(exc))
        return report
    for mod in mods:
        contact = mod.curated.get("contact")
        if not (isinstance(contact, str) and CONTACT_PATTERN.match(contact)):
            report.problem(f"mods/{mod.mod_id}/mod.json: `contact` ({{CONTACT_KIND}}) is missing or malformed — who answers for this mod")
        name = str(mod.field("name", mod.mod_id))
        for label, value in (("id", mod.mod_id), ("name", name)):
            hit = reserved_name_hit(value, reserved)
            if hit and mod.mod_id not in allow:
                report.problem(f"mods/{mod.mod_id}: {label} {value!r} claims the reserved product name {hit!r} (schema/reserved-names.json); only that product's owners may list it")
        for hit in check_masquerade(mod.mod_id, name, allowlist=allow):
            report.problem(f"mods/{mod.mod_id}: {hit.format()}")
        for record in mod.versions:
            report.records += 1
            manifest = record.manifest
            if not (isinstance(manifest.get("license"), str) and manifest["license"].strip()):
                report.problem(f"{record.mod_id}@{record.version}: floofy.json declares no license")
            if not _has_license_file(manifest.get("files") or []):
                report.problem(f"{record.mod_id}@{record.version}: files[] lists no LICENSE / LICENCE / COPYING file at the mod root")
            network = manifest.get("network") if isinstance(manifest.get("network"), dict) else {}
            for host in network.get("hosts") or []:
                if not (isinstance(host, str) and _HOST_RE.match(host)):
                    report.problem(f"{record.mod_id}@{record.version}: network.hosts[] entry {host!r} is not a plain host[:port]")
            if record.is_link:
                link = record.link or {}
                if not repo_matches_pattern(str(link.get("repo", "")), repo.repo_url_pattern):
                    report.problem(f"{record.mod_id}@{record.version}: link.repo {link.get('repo')!r} is outside this registry's forge pattern {repo.repo_url_pattern!r}")
                if not record.link_commit and record.link_tag and record.link_tag not in accepted_tags(record.mod_id, record.version):
                    # a record made before commits were pinned is still located by its tag: the tag rule applies to it alone
                    report.problem(f"{record.mod_id}@{record.version}: tag {record.link_tag!r} is not the version ({record.version}, v{record.version} or {record.mod_id}-{record.version})")
                ok, why = manifest_hash_matches(record)
                if not ok:
                    report.problem(why)
                if not record.link_resolved:
                    report.problem(f"{record.mod_id}@{record.version}: the link record is unresolved (commit / manifestSha256 / files[]); run `registry_tools record` or `build --resolve-links`")
            else:
                if repo.record_kind == "link":
                    report.notes.append(f"{record.mod_id}@{record.version} is an archive record in a link registry (transitional)")
                if record.tag not in accepted_tags(record.mod_id, record.version):
                    report.problem(f"{record.mod_id}@{record.version}: tag {record.tag!r} is not the version")
    # 4. derived files
    result = build_index(repo)
    for problem in result.problems:
        report.problem(f"index build: {problem}")
    if result.ok:
        ok, message = index_matches(repo.index_path, result)
        if not ok:
            report.problem(message)
        ok, message = app_registry_matches(repo, result.index)
        if not ok:
            report.problem(message)
        ok, message = readme_matches(repo, index=result.index)
        if not ok:
            report.problem(message)
    try:
        index = json.loads(repo.index_path.read_text(encoding="utf-8"))
        for error in validate_instance(index, load_schema("index")):
            report.problem(f"index.json{error.path or '/'}: {error.message}")
    except (OSError, ValueError) as exc:
        report.problem(f"index.json: {exc}")
    # 5. signatures
    key_path = root / "schema" / "signing-key.pub.json"
    try:
        kid, public = load_public_key_record(key_path)
        if repo.key_id and kid != repo.key_id:
            report.problem(f"{key_path}: key {kid} is not registry.json's keyId {repo.key_id}")
        for name in ("index.json", "compat.json"):
            payload = root / name
            if not payload.is_file():
                continue
            signature = root / f"{name}.sig"
            verdict = verify_detached(payload.read_bytes(), signature.read_bytes() if signature.is_file() else None, {kid: public}, expected_key_id=repo.key_id)
            if not verdict.verified:
                report.problem(f"{name}: signature {verdict.status} — {verdict.detail} (sign with the pinned key before merging)")
    except Exception as exc:  # noqa: BLE001 - an unreadable key record is one problem, not a crash
        report.problem(f"{key_path}: {exc}")
    # 6. clone every link record at its tag and compare
    for mod in mods:
        for record in mod.versions:
            if not record.is_link:
                continue
            if not clone:
                report.notes.append(f"{record.mod_id}@{record.version}: clone step skipped ({SKIP_CLONE_ENV}=1) — the merge gate never skips it")
                continue
            submission = validate_submission(None, registry=root, record=f"{record.mod_id}@{record.version}")
            report.cloned += 1
            for finding in submission.errors:
                report.problem(f"{record.mod_id}@{record.version}: {finding.format()}")
            for finding in submission.findings:
                if finding.severity != "error":
                    report.notes.append(f"{record.mod_id}@{record.version}: {finding.format()}")
    _ = canonical_bytes, hashlib  # the vendored helpers stay importable for the tests
    return report


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    clone: bool | None = None
    if "--no-clone" in args:
        clone = False
        args.remove("--no-clone")
    root = Path(args[0]) if args else None
    try:
        report = validate_registry(root, clone=clone)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(report.format())
    return 0 if report.ok else 1
