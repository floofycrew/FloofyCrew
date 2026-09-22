"""The install engine behind ``floofy install`` / ``update`` / ``profile use`` (Requirement 7.6, 11.3, 11.4, 11.7).

Pure building blocks, driven by the CLI (:mod:`floofy_core.cli.cmd_mods`) and
reused in-process by the manager UI:

* :func:`resolve_source` — a mod reference is a **path** (directory or
  ``.zip``/``.tar.gz``), a **URL** (HTTPS only; plaintext ``http://`` is refused
  unless the host is loopback — Requirement 11.6 applies to FloofyCrew's own
  traffic too), a **git reference** (``ssh://…`` / ``https://…[.git]``, ``@tag`` optional:
  shallow-cloned with the user's own credentials, Requirement 8.8 —
  :mod:`floofy_core.gitsource`) or a **registry id** (``id`` / ``id@version`` from
  the merged ``cache/index.json``, with the index's ``sha256`` checked after
  download, or resolved through the clone path when the record is link-based);
* :func:`disclose` — what the manager shows before install (Requirement 11.7):
  every part with kind, side, seam and whether it modifies payload files; the
  declared ``network.hosts[]`` and ``credentials``; the validator's governance
  and network flags; the host's admission verdict for ``app`` parts and the
  theme route's state, both as **warnings** (Requirement 2.3, 11.2); the source
  tier and, for a git reference, the extra *unlisted source* line (Requirement 8.8, 8.11);
* :func:`governance_targets` — the exact paths that need a per-file typed
  confirmation (Requirement 11.4);
* :func:`place` — copy the validated tree into ``pending/<id>/`` (a gateway is
  running and ``--now`` was not given: applied at the next gateway start, the
  ModAssistant pattern) or straight into ``mods/<id>/``, preserving the mod's
  ``.floofy/`` runtime state across updates, and write the source record;
* :func:`run_handlers` — the seam kind handlers (theme, agent, skill,
  appearance, config, app) run at install/uninstall time; code kinds are the
  Loader's and Patcher's business.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .archive import ArchiveError, is_archive, open_archive_safely
from .compat import CompatCache
from .datahome import DataHome
from .gitsource import UNLISTED_SOURCE_LINE, GitRef, GitRefError, fetch as git_fetch, manifest_sha256, verify_files
from .governance import GovernanceSnapshot, warnings_for
from .kinds import SEAMS, KindContext, PartOutcome, handlers
from .modstore import code_kinds_of, parts_of, write_source
from .netscan import is_loopback_host
from .patches import PatchDescriptor, PatchDescriptorError
from .registry import IndexCache, IndexVersion
from .targets import DEFAULT_TARGET_POLICY, TargetClass, TargetPolicy
from .tier import TIER_LISTED, TIER_UNLISTED
from .validator import ValidationReport, validate_mod

__all__ = [
    "CONFIRM_KINDS",
    "Disclosure",
    "InstallError",
    "MAX_DOWNLOAD_BYTES",
    "ResolvedSource",
    "disclose",
    "download",
    "governance_targets",
    "place",
    "resolve_git",
    "resolve_link",
    "resolve_source",
    "run_handlers",
    "sha256_of",
]

#: Kinds whose code runs with gateway privileges or touch payload files: an explicit yes is required (Requirement 11.7).
CONFIRM_KINDS = frozenset({"python-hook", "patch", "app"})
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024


class InstallError(Exception):
    """A refusal or failure the CLI turns into an error line and exit 1."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --- sources -----------------------------------------------------------------------------------


@dataclass
class ResolvedSource:
    kind: str  # path | archive | url | registry | git
    ref: str
    root: Path  # the mod directory to validate and copy
    sha256: str | None = None
    version: str | None = None
    registry_key: str | None = None
    #: The commit a git reference or a link record resolved to (Requirement 8.8, 8.9).
    commit: str | None = None
    #: ``unlisted`` / ``listed`` at install time (Requirement 8.11; ``tested`` is derived against the running host when shown).
    tier: str = TIER_UNLISTED
    #: The parsed git reference (``url``, ``tag``, ``ref``, ``subdirectory``) when a clone was involved.
    git: dict[str, Any] | None = None
    #: The registry link record the clone was checked against (``repo``, ``commit``, ``manifestSha256``[, ``path``]), when any.
    link: dict[str, Any] | None = None
    _cleanup: list[Path] = field(default_factory=list, repr=False)

    def cleanup(self) -> None:
        for path in self._cleanup:
            shutil.rmtree(path, ignore_errors=True)
        self._cleanup.clear()

    @property
    def unlisted_git(self) -> bool:
        """A git reference the user named directly — the install that needs the extra consent line (Requirement 8.8)."""
        return self.kind == "git"

    def to_dict(self) -> dict[str, Any]:
        record: dict[str, Any] = {"kind": self.kind, "ref": self.ref, "sha256": self.sha256, "version": self.version, "registryKey": self.registry_key, "tier": self.tier}
        if self.commit:
            record["commit"] = self.commit
        if self.git:
            record["git"] = dict(self.git)
        if self.link:
            record["link"] = dict(self.link)
        return record

    def record_extra(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """The fields :func:`floofy_core.modstore.write_source` adds beyond ``source``/``ref``/``sha256``."""
        extra: dict[str, Any] = {"registryKey": self.registry_key, "version": str(manifest.get("version") or ""), "tier": self.tier}
        if self.commit:
            extra["commit"] = self.commit
        if self.git:
            extra["git"] = {**self.git, "commit": self.commit}
        if self.link:
            extra["link"] = dict(self.link)
        return extra


def _refuse_plaintext(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and is_loopback_host(parsed.hostname or ""):
        return
    raise InstallError(f"refusing {url}: FloofyCrew's own traffic must use https (plaintext is allowed to loopback only — Requirement 11.6)")


def download(url: str, destination: Path, *, expected_sha256: str | None = None, max_bytes: int = MAX_DOWNLOAD_BYTES, opener: Callable[..., Any] | None = None, expected_size: int | None = None) -> str:
    """Fetch ``url`` (HTTPS, or plaintext loopback) into ``destination``; returns the SHA-256, checked when expected.

    ``opener`` is a ``urllib`` ``OpenerDirector`` (an edition adapter's network
    identity) or a callable with ``urlopen``'s signature. ``expected_size`` (the
    index's ``size``) is checked too: a mismatch is refused like a hash mismatch,
    and the download is cut off as soon as it exceeds the expected size.
    """
    _refuse_plaintext(url)
    open_url = opener.open if opener is not None and hasattr(opener, "open") else (opener or urllib.request.urlopen)
    digest = hashlib.sha256()
    total = 0
    limit = min(max_bytes, expected_size) if expected_size is not None else max_bytes
    try:
        with open_url(urllib.request.Request(url, headers={"User-Agent": "floofy (unofficial KiroCrew mod manager)"}), timeout=60) as response, Path(destination).open("wb") as out:
            while True:
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise InstallError(f"{url}: larger than {limit} bytes ({'the index says ' + str(expected_size) if expected_size is not None and limit == expected_size else 'the download cap'}); refusing")
                digest.update(chunk)
                out.write(chunk)
    except urllib.error.URLError as exc:
        Path(destination).unlink(missing_ok=True)
        raise InstallError(f"download failed: {url}: {exc}") from exc
    except InstallError:
        Path(destination).unlink(missing_ok=True)
        raise
    actual = digest.hexdigest()
    if expected_size is not None and total != expected_size:
        Path(destination).unlink(missing_ok=True)
        raise InstallError(f"{url}: size mismatch (the index says {expected_size} bytes, got {total}); the download was discarded (Requirement 8.3)")
    if expected_sha256 and actual != expected_sha256.lower():
        Path(destination).unlink(missing_ok=True)
        raise InstallError(f"{url}: sha256 mismatch (expected {expected_sha256[:12]}…, got {actual[:12]}…); the download was discarded (Requirement 8.3)")
    return actual


def _extract(archive: Path, source: ResolvedSource) -> Path:
    scratch = Path(tempfile.mkdtemp(prefix="floofy-install-"))
    source._cleanup.append(scratch)
    try:
        return open_archive_safely(archive, scratch)
    except ArchiveError as exc:
        source.cleanup()
        raise InstallError(f"{archive}: unsafe archive: {exc} (Requirement 1.8)") from exc


def resolve_git(reference: GitRef, *, home: DataHome, tier: str = TIER_UNLISTED) -> ResolvedSource:
    """Shallow-clone a parsed git reference into the cache and return it as a ``git`` source (Requirement 8.8).

    The checkout must hold a ``floofy.json`` at its root (or under the reference's
    ``#subdirectory``); the manifest's own ``files[]`` is verified right here so a
    tampered checkout is refused before anything is disclosed — ``floofy validate``
    repeats the check in the install flow. ``sha256`` is the canonical-manifest
    hash (what a link record pins, Requirement 8.9), ``commit`` the clone's HEAD.
    """
    try:
        checkout, commit = git_fetch(reference, home)
    except GitRefError as exc:
        raise InstallError(str(exc)) from exc
    root = checkout / reference.subdirectory if reference.subdirectory else checkout
    if not (root / "floofy.json").is_file():
        hint = f" (the mod is not at the repository root: name its directory as {reference.text}#<subdirectory>)" if not reference.subdirectory else ""
        raise InstallError(f"{reference.text}: no floofy.json in the checkout at {root}{hint}")
    try:
        manifest = manifest_of(root)
        verify_files(root, [f for f in (manifest.get("files") or []) if isinstance(f, dict)])
        digest = manifest_sha256(root)
    except (OSError, ValueError) as exc:
        raise InstallError(f"{reference.text}: floofy.json is unreadable: {exc}") from exc
    except GitRefError as exc:
        raise InstallError(f"{reference.text} at {commit[:12]}: {exc} — refusing the checkout (Requirement 8.8)") from exc
    source = ResolvedSource("git", reference.text, root, sha256=digest, version=str(manifest.get("version") or "") or None, commit=commit, tier=tier, git=reference.to_dict())
    return source


def resolve_link(key: str, picked: IndexVersion, *, home: DataHome) -> ResolvedSource:
    """Resolve a **link record** through the git clone path and hold the checkout to the record (Requirement 8.9).

    A link record pins a **commit**: the record's ``repo`` is fetched at exactly
    ``link.commit`` (``git fetch --depth 1 origin <sha>``, the user's own
    credentials, :func:`resolve_git`), so where the repository's branches or tags
    have moved since does not matter and no tag is needed to locate a version. The
    checkout is refused when its canonical-manifest hash is not ``manifestSha256``
    (the manifest changed) or when any of the record's ``files[]`` differs in hash
    or size. A record made before commits were pinned (``tag`` but no ``commit``)
    is still cloned at its tag. What passes is a ``registry`` source of tier
    ``listed`` carrying both the link record and the clone facts.
    """
    link = picked.link or {}
    expected_commit = str(link.get("commit") or "").lower()
    subdirectory = str(link.get("path") or "")
    try:
        if expected_commit:
            reference = GitRef.at_commit(str(link["repo"]), expected_commit, subdirectory)
        elif link.get("tag"):
            reference = GitRef.parse(f"{link['repo']}@{link['tag']}" + (f"#{subdirectory}" if subdirectory else ""))
        else:
            raise InstallError(f"{key}@{picked.version}: the link record pins neither a commit nor a tag")
    except (KeyError, GitRefError) as exc:
        raise InstallError(f"{key}@{picked.version}: the link record is unusable: {exc}") from exc
    source = resolve_git(reference, home=home, tier=TIER_LISTED)
    if expected_commit and source.commit != expected_commit:
        raise InstallError(f"{key}@{picked.version}: the fetch resolved to commit {source.commit[:12]}, the registry record says {expected_commit[:12]} — refusing (Requirement 8.9)")
    expected_manifest = str(link.get("manifestSha256") or "").lower()
    if expected_manifest and source.sha256 != expected_manifest:
        raise InstallError(f"{key}@{picked.version}: the checkout's floofy.json (canonical sha256 {source.sha256[:12]}…) differs from the registry record ({expected_manifest[:12]}…) — refusing (Requirement 8.9)")
    try:
        verify_files(source.root, picked.path_files, what="the registry record")
    except GitRefError as exc:
        raise InstallError(f"{key}@{picked.version}: {exc} — refusing (Requirement 8.9)") from exc
    source.kind = "registry"
    source.ref = f"{key}@{picked.version}"
    source.version = picked.version
    source.registry_key = key
    source.tier = TIER_LISTED
    source.link = {"repo": reference.url, "commit": source.commit, "manifestSha256": source.sha256, **({"tag": reference.tag} if reference.tag else {}), **({"path": reference.subdirectory} if reference.subdirectory else {})}
    return source


def resolve_source(
    ref: str,
    *,
    home: DataHome,
    base_version: Any,
    edition: str | None,
    host_version: str | None,
    requested_version: str | None = None,
    expected_sha256: str | None = None,
    opener: Callable[..., Any] | None = None,
    opener_for: Callable[[str], Any] | None = None,
    channel: str | None = None,
    expected_size: int | None = None,
    compat: CompatCache | None = None,
    git_ref: str | None = None,
) -> ResolvedSource:
    """Turn ``<id[@version]|path|url|git reference>`` into a local mod directory to validate.

    ``opener_for(url)`` supplies an edition adapter's network identity per URL
    (:func:`floofy_core.editions.registry_opener`); ``opener`` is the fallback for
    every URL. A registry reference picks the newest version known to work on this
    host (:meth:`floofy_core.registry.IndexCache.pick`, Requirement 9.2) and checks
    the downloaded archive against the index's ``sha256`` **and** ``size``. A git
    reference (``ssh://…`` / ``https://…[.git]``; :class:`floofy_core.gitsource.GitRef`)
    is shallow-cloned with the user's own credentials — the default branch when
    bare, ``@tag`` or the explicit ``git_ref`` (``--ref <branch|commit>``) when the
    caller pins a point (Requirement 8.8).
    """
    candidate = Path(ref).expanduser()
    if candidate.exists():
        if candidate.is_dir():
            return ResolvedSource("path", str(candidate.resolve()), candidate.resolve())
        if is_archive(candidate):
            source = ResolvedSource("archive", str(candidate.resolve()), candidate, sha256=sha256_of(candidate))
            source.root = _extract(candidate, source)
            return source
        raise InstallError(f"{ref}: not a mod directory or a .zip/.tar.gz archive")
    if GitRef.looks_like(ref, explicit_ref=git_ref):
        try:
            reference = GitRef.parse(ref, ref=git_ref)
        except GitRefError as exc:
            raise InstallError(str(exc)) from exc
        return resolve_git(reference, home=home)
    if git_ref is not None:
        raise InstallError(f"--ref only applies to a git reference (ssh://… or https://…), not to {ref!r}")
    if ref.startswith(("http://", "https://")):
        source = ResolvedSource("url", ref, Path("."))
        scratch = Path(tempfile.mkdtemp(prefix="floofy-download-"))
        source._cleanup.append(scratch)
        name = Path(urllib.parse.urlsplit(ref).path).name or "mod.zip"
        target = scratch / name
        chosen = (opener_for(ref) if opener_for is not None else None) or opener
        try:
            source.sha256 = download(ref, target, expected_sha256=expected_sha256, opener=chosen, expected_size=expected_size)
        except InstallError:
            source.cleanup()
            raise
        if not is_archive(target):
            source.cleanup()
            raise InstallError(f"{ref}: the download is not a .zip/.tar.gz archive")
        source.root = _extract(target, source)
        return source
    # a registry reference: id or id@version
    mod_ref, _, version = ref.partition("@")
    cache = IndexCache.load(home)
    entry, picked, why = cache.pick(mod_ref, base_version=base_version, edition=edition, host_version=host_version, requested=requested_version or version or None, channel=channel, compat=compat if compat is not None else CompatCache.load(home.compat_cache))
    if entry is None or picked is None:
        hint = "; ".join(cache.notes) if cache.notes else "run `floofy search` to see what the cache knows"
        raise InstallError(f"{why} ({hint})")
    if picked.link is not None:
        return resolve_link(entry.key, picked, home=home)
    archive_files = picked.asset_files
    if not archive_files:
        raise InstallError(f"{entry.key}@{picked.version} lists no downloadable file in the index")
    file = archive_files[0]
    size = file.get("size")
    source = resolve_source(
        str(file["url"]),
        home=home,
        base_version=base_version,
        edition=edition,
        host_version=host_version,
        expected_sha256=str(file.get("sha256") or "") or expected_sha256,
        opener=opener,
        opener_for=opener_for,
        expected_size=int(size) if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else None,
        compat=compat,
    )
    source.kind = "registry"
    source.ref = f"{entry.key}@{picked.version}"
    source.version = picked.version
    source.registry_key = entry.key
    source.tier = TIER_LISTED
    return source


# --- disclosure -----------------------------------------------------------------------------------


def governance_targets(root: Path, manifest: dict[str, Any], policy: TargetPolicy | None = None) -> list[str]:
    """Shipped files and patch targets that land on the host's governance files (Requirement 11.4)."""
    policy = policy or DEFAULT_TARGET_POLICY
    found: list[str] = []
    for entry in manifest.get("files") or []:
        if isinstance(entry, dict) and isinstance(entry.get("path"), str) and policy.classify(entry["path"]) is TargetClass.GOVERNANCE:
            found.append(entry["path"])
    for part in parts_of(manifest):
        if part.get("kind") != "patch" or not isinstance(part.get("path"), str):
            continue
        try:
            descriptor = PatchDescriptor.load(Path(root) / part["path"])
        except (PatchDescriptorError, OSError, ValueError):
            continue
        if policy.classify(descriptor.target) is TargetClass.GOVERNANCE:
            found.append(descriptor.target)
    return sorted(dict.fromkeys(found))


@dataclass
class Disclosure:
    """Everything the user sees before saying yes (Requirement 11.7)."""

    mod_id: str
    version: str
    name: str
    parts: list[dict[str, Any]]
    modifies_payload: bool
    network_hosts: list[str]
    credentials: bool
    flags: list[dict[str, Any]]  # validator warnings the user may accept (governance, network, unlisted files)
    governance_targets: list[str]
    host_warnings: list[dict[str, Any]]  # the host's verdicts, informational
    confirm_kinds: list[str]
    #: Whether the mod carries code kinds (``python-hook``/``spa``) — what the confirmation covers (Requirement 11.7).
    code_parts: bool
    errors: list[dict[str, Any]]
    #: The source tier this install will have (Requirement 8.11) and, for a git reference, the facts behind the extra consent line.
    tier: str = TIER_UNLISTED
    unlisted_source: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.mod_id,
            "version": self.version,
            "name": self.name,
            "parts": list(self.parts),
            "modifiesPayload": self.modifies_payload,
            "network": {"hosts": list(self.network_hosts), "credentials": self.credentials},
            "flags": list(self.flags),
            "governanceTargets": list(self.governance_targets),
            "hostWarnings": list(self.host_warnings),
            "confirmKinds": list(self.confirm_kinds),
            "codeParts": self.code_parts,
            "errors": list(self.errors),
            "tier": self.tier,
            "unlistedSource": dict(self.unlisted_source) if self.unlisted_source else None,
        }

    def lines(self) -> list[str]:
        out = [f"{self.name} ({self.mod_id} {self.version})"]
        if self.unlisted_source:
            where = self.unlisted_source.get("ref") or self.unlisted_source.get("url")
            commit = str(self.unlisted_source.get("commit") or "")[:12]
            out.append(f"  UNLISTED SOURCE: {UNLISTED_SOURCE_LINE} — {where}" + (f" (commit {commit})" if commit else ""))
            out.append("    nothing about this source is trusted beyond your consent; the mod's own files[] hashes were verified (Requirement 8.8)")
        else:
            out.append(f"  source tier: {self.tier}" + (" (no curator review, no compatibility data)" if self.tier == TIER_UNLISTED else ""))
        out.append("  parts:")
        for part in self.parts:
            payload = " [MODIFIES PAYLOAD FILES]" if part["modifiesPayload"] else ""
            out.append(f"    {part['index']}. {part['kind']}/{part['side']} {part['path']} -> {part['seam']}{payload}")
        if self.network_hosts or self.credentials:
            out.append(f"  network: hosts {', '.join(self.network_hosts) or '-'}; asks for credentials itself: {'yes' if self.credentials else 'no'}")
        else:
            out.append("  network: no remote hosts declared (only loopback is allowed without a declaration)")
        for flag in self.flags:
            out.append(f"  flag {flag['code']}{' [' + flag['path'] + ']' if flag.get('path') else ''}: {flag['message']}")
        for target in self.governance_targets:
            out.append(f"  GOVERNANCE-ALTERING target: {target} (needs its path typed to confirm)")
        for warning in self.host_warnings:
            out.append(f"  host says: {warning['code']}: {warning['message']}")
        if self.code_parts:
            out.append("  code parts (python-hook/spa) run once installed — a confirmed install lands ENABLED; `--disabled` lands it switched off (Requirement 11.7)")
        for error in self.errors:
            out.append(f"  ERROR {error['code']}: {error['message']}")
        return out


def disclose(root: Path, report: ValidationReport, snapshot: GovernanceSnapshot | None, *, policy: TargetPolicy | None = None, source: ResolvedSource | None = None) -> Disclosure:
    """Everything the user sees before saying yes; ``source`` adds the tier and, for a git reference, the unlisted-source line (Requirement 8.8, 8.11)."""
    manifest = report.manifest or {}
    parts = []
    for index, part in enumerate(parts_of(manifest)):
        kind = str(part.get("kind", ""))
        parts.append({"index": index, "kind": kind, "side": str(part.get("side", "")), "path": str(part.get("path", "")), "seam": SEAMS.get(kind, "?"), "modifiesPayload": kind == "patch", "description": part.get("description")})
    network = manifest.get("network") if isinstance(manifest.get("network"), dict) else {}
    kinds = {p["kind"] for p in parts}
    host_warnings: list[dict[str, Any]] = []
    if snapshot is not None:
        mod_id = str(manifest.get("id") or "")
        host_warnings = [w.to_dict() for w in warnings_for(snapshot, theme_targets=[mod_id] if "theme" in kinds else (), app_targets=[mod_id] if "app" in kinds else ())]
        if "app" in kinds and snapshot.admission_mode:
            host_warnings.append({"code": "AppAdmissionMode", "message": f"the host's app admission is in {snapshot.admission_mode} mode; its verdict is shown, not enforced (Requirement 2.3)", "affectedTargets": [mod_id]})
    return Disclosure(
        mod_id=str(manifest.get("id") or root.name),
        version=str(manifest.get("version") or "0.0.0"),
        name=str(manifest.get("name") or manifest.get("id") or root.name),
        parts=parts,
        modifies_payload="patch" in kinds,
        network_hosts=[str(h) for h in (network.get("hosts") or [])],
        credentials=bool(network.get("credentials")),
        flags=[f.to_dict() for f in report.warnings if f.accept],
        governance_targets=governance_targets(root, manifest, policy),
        host_warnings=host_warnings,
        confirm_kinds=sorted(kinds & CONFIRM_KINDS),
        code_parts=bool(code_kinds_of(manifest)),
        errors=[f.to_dict() for f in report.errors],
        tier=source.tier if source is not None else TIER_UNLISTED,
        unlisted_source=({"url": (source.git or {}).get("url"), "ref": source.ref, "commit": source.commit} if source is not None and source.unlisted_git else None),
    )


def validate(root: Path, *, policy: TargetPolicy | None = None) -> ValidationReport:
    return validate_mod(root, targets=policy)


# --- placing the tree ---------------------------------------------------------------------------


def _copy_tree(source: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        if target.is_symlink():
            target.unlink()
        else:
            shutil.rmtree(target)
    shutil.copytree(source, target, symlinks=False, ignore=shutil.ignore_patterns("__pycache__", ".git", ".pytest_cache", ".hypothesis"))


def place(home: DataHome, source: ResolvedSource, manifest: dict[str, Any], *, stage: bool) -> tuple[Path, str]:
    """Copy the mod into ``pending/<id>/`` (``stage``) or ``mods/<id>/``; keep ``.floofy/`` runtime state; write the source record."""
    mod_id = str(manifest["id"])
    home.ensure()
    installed = home.mod_dir(mod_id)
    runtime_backup: Path | None = None
    if (installed / ".floofy").is_dir() and not installed.is_symlink():
        runtime_backup = Path(tempfile.mkdtemp(prefix="floofy-runtime-")) / ".floofy"
        shutil.copytree(installed / ".floofy", runtime_backup)
    target = home.pending / mod_id if stage else installed
    remove_marker = home.pending / f"{mod_id}.remove"
    if remove_marker.exists():
        remove_marker.unlink()
    _copy_tree(source.root, target)
    if runtime_backup is not None:
        shutil.rmtree(target / ".floofy", ignore_errors=True)
        shutil.copytree(runtime_backup, target / ".floofy")
        shutil.rmtree(runtime_backup.parent, ignore_errors=True)
    write_source(target, source=source.kind, ref=source.ref, sha256=source.sha256, extra=source.record_extra(manifest))
    return target, ("staged" if stage else "installed")


def run_handlers(kctx: KindContext, mod_id: str, mod_dir: Path, manifest: dict[str, Any], op: str) -> list[PartOutcome]:
    """``install`` / ``uninstall`` / ``status`` every part through its kind handler (fail-soft per part)."""
    table = handlers()
    outcomes: list[PartOutcome] = []
    for index, part in enumerate(parts_of(manifest)):
        kind = str(part.get("kind", ""))
        handler = table.get(kind)
        if handler is None:
            outcomes.append(PartOutcome(kind, index, SEAMS.get(kind, "?"), kind == "patch", ok=False, status="error", detail=f"no handler for kind {kind!r}"))
            continue
        try:
            outcome = getattr(handler, op)(kctx, mod_id, Path(mod_dir), part, index)
        except Exception as exc:  # noqa: BLE001 - one seam failing must not abort the others
            outcome = PartOutcome(kind, index, handler.seam, handler.modifies_payload, ok=False, status="error", detail=f"{type(exc).__name__}: {exc}")
        outcomes.append(outcome)
    return outcomes


def manifest_of(root: Path) -> dict[str, Any]:
    document = json.loads((Path(root) / "floofy.json").read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise InstallError(f"{root}/floofy.json is not a JSON object")
    return document
