"""Link records: resolving and checking them against the repository they point at (Requirement 8.9, 8.10).

A link record (``mods/<id>/<version>/release.json`` with ``link: {repo, path?,
ref?}``) is complete for the index only once it carries the **facts a client
verifies**: the ``commit`` the version is pinned at, the SHA-256 of the canonical
``floofy.json`` and the checkout's ``files[]`` as ``{path, sha256, size}``. The
commit is the pin — a version is located by it, never by a tag: the mod's
repository keeps the mod under its directory on its default branch (or ``ref``),
and the registry records the commit it curated. Two ways to get the facts:

* :func:`resolve_record` — clone ``repo`` (at ``link.ref``, else the default
  branch; at ``link.commit`` when the record already pins one) with the same
  :func:`floofy_core.gitsource.clone_into` a client uses and the operator's own
  credentials, into a scratch directory, and read them off the checkout
  (``registry_tools record``, ``build --resolve-links``, ``bootstrap --record link``);
* :func:`facts_from_local_commit` — read them off a **local** checkout at a
  commitish (``HEAD``, a branch, a commit) exported with ``git archive``, so they
  describe the committed tree, not the working copy (the bootstrap's
  ``--link-source``; a commit the owner has not pushed yet).

:func:`check_record` is the verification side (``validate-submission --record``,
the internal package's build gate): fetch the pinned commit and compare the
manifest hash and every file with the record; any difference is a finding. The
manifest hash is also checked **without** a clone by :func:`manifest_hash_matches`
— the record's own ``floofy.json`` must hash to ``manifestSha256`` — which is
what ``build`` does for every link record on every run. A record made before
commits were pinned (``tag`` but no ``commit``) is still cloned at its tag.

``repo`` must use ``ssh://`` or ``https://`` (:class:`floofy_core.gitsource.GitRef`
refuses anything else) and, when the registry pins its forge
(``registry.json`` ``repoUrlPattern``, Requirement 8.10), must match that pattern.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from floofy_core.canonical import canonical_bytes
from floofy_core.gitsource import GitRef, GitRefError, clone_into, manifest_sha256

from .repo import RegistryRepo, VersionRecord

__all__ = ["LinkError", "LinkFacts", "check_record", "commit_of", "commit_of_tag", "facts_from_checkout", "facts_from_local_commit", "facts_from_local_tag", "link_reference", "manifest_hash_matches", "repo_matches_pattern", "resolve_record", "write_resolved"]

_SKIP_DIRS = frozenset({".git", "__pycache__", ".pytest_cache", ".hypothesis"})


class LinkError(ValueError):
    """A link record that cannot be resolved or does not hold."""


@dataclass
class LinkFacts:
    """What a resolved link record carries beyond ``repo``/``path``/``ref``."""

    commit: str
    manifest_sha256: str
    files: list[dict[str, Any]] = field(default_factory=list)

    def to_link(self, link: dict[str, Any]) -> dict[str, Any]:
        """The ``link`` block: ``repo`` (+ ``path``, ``ref`` when given) with the pinned ``commit`` and ``manifestSha256``; no tag."""
        out = {k: v for k, v in link.items() if k in ("repo", "path", "ref") and v}
        out.update({"commit": self.commit, "manifestSha256": self.manifest_sha256})
        return out


def link_reference(record: VersionRecord, *, pinned: bool = True) -> GitRef:
    """The :class:`GitRef` a link record is fetched as.

    ``pinned`` (the client's and the gate's view): the repository at ``link.commit``
    — the pin; a legacy record without a commit is cloned at its tag. Not pinned
    (making or refreshing a record): the repository at ``link.ref``, else its
    default branch. Both carry the mod's ``path`` inside the checkout.
    """
    link = record.link
    if link is None:
        raise LinkError(f"{record.mod_id}@{record.version}: not a link record")
    path = link["path"] if isinstance(link.get("path"), str) and link["path"] else ""
    try:
        if pinned and record.link_commit:
            return GitRef.at_commit(str(link["repo"]), record.link_commit, path)
        if pinned and record.link_tag:
            return GitRef.parse(f"{link['repo']}@{record.link_tag}" + (f"#{path}" if path else ""))
        if pinned:
            raise LinkError(f"{record.mod_id}@{record.version}: the link record pins no commit (run `registry_tools record` or `build --resolve-links`)")
        if record.link_ref:
            return GitRef.parse(str(link["repo"]) + (f"#{path}" if path else ""), ref=record.link_ref)
        return GitRef.default_branch(str(link["repo"]), path)
    except GitRefError as exc:
        raise LinkError(f"{record.mod_id}@{record.version}: link.repo is unusable: {exc}") from exc


def repo_matches_pattern(repo: str, pattern: str | None) -> bool:
    """Whether ``repo`` matches the registry's ``repoUrlPattern`` (``None`` pins nothing)."""
    if not pattern:
        return True
    try:
        return re.search(pattern, repo) is not None
    except re.error:
        return False


def _shipped_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in sorted(Path(root).rglob("*")):
        rel = path.relative_to(root)
        if any(part in _SKIP_DIRS or part.startswith(".") for part in rel.parts):
            continue
        if path.is_file() and not path.is_symlink():
            found.append(path)
    return found


def facts_from_checkout(root: Path, *, commit: str) -> LinkFacts:
    """The facts of a mod directory at ``commit``: canonical-manifest hash and every shipped file with hash and size (``floofy.json`` excluded)."""
    root = Path(root)
    if not (root / "floofy.json").is_file():
        raise LinkError(f"{root}: no floofy.json in the checkout")
    files: list[dict[str, Any]] = []
    for path in _shipped_files(root):
        rel = path.relative_to(root).as_posix()
        if rel == "floofy.json":
            continue
        data = path.read_bytes()
        files.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    return LinkFacts(commit=commit, manifest_sha256=manifest_sha256(root), files=files)


def commit_of(checkout: Path, commitish: str = "HEAD") -> str:
    """``git rev-list -n 1 <commitish>`` in a local checkout: the commit a branch, ``HEAD`` or a tag names there."""
    done = subprocess.run(["git", "-C", str(checkout), "rev-list", "-n", "1", commitish], capture_output=True, text=True, check=False, timeout=60)
    commit = done.stdout.strip()
    if done.returncode != 0 or not re.match(r"^[0-9a-f]{40}$", commit):
        raise LinkError(f"{checkout}: no commit named {commitish!r} ({(done.stderr or '').strip() or 'git rev-list failed'})")
    return commit


def commit_of_tag(checkout: Path, tag: str) -> str:
    """:func:`commit_of` for a tag (kept for callers that still name one)."""
    return commit_of(checkout, tag)


def facts_from_local_tag(checkout: Path, tag: str, path: str = "") -> LinkFacts:
    """:func:`facts_from_local_commit` for a tag (kept for callers that still name one)."""
    return facts_from_local_commit(checkout, tag, path)


def facts_from_local_commit(checkout: Path, commitish: str = "HEAD", path: str = "") -> LinkFacts:
    """The facts of ``path`` inside a **local** checkout at ``commitish`` — exported with ``git archive`` so they describe the committed tree, not the working copy.

    The bootstrap and ``registry_tools record --source`` use this for a commit that
    exists in a clone but may not be pushed yet; the record they write is then
    exactly what ``check_record`` will find once the commit is reachable.
    """
    commit = commit_of(checkout, commitish)
    scratch = Path(tempfile.mkdtemp(prefix="floofy-link-export-"))
    try:
        args = ["git", "-C", str(checkout), "archive", "--format=tar", commit]
        if path:
            args.append(path)
        done = subprocess.run(args, capture_output=True, check=False, timeout=120)
        if done.returncode != 0:
            raise LinkError(f"{checkout}: git archive {commit[:12]} {path or ''} failed: {done.stderr.decode('utf-8', 'replace').strip()}")
        import io  # noqa: PLC0415
        import tarfile  # noqa: PLC0415

        with tarfile.open(fileobj=io.BytesIO(done.stdout), mode="r:") as archive:
            for member in archive.getmembers():
                if not member.isfile() or member.name.startswith("/") or ".." in member.name.split("/"):
                    continue
                archive.extract(member, scratch, filter="data")
        root = scratch / path if path else scratch
        return facts_from_checkout(root, commit=commit)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def resolve_record(record: VersionRecord, *, timeout: int = 300, allow_local: bool | None = None, refresh: bool = False) -> LinkFacts:
    """Clone the record's repository into a scratch directory and read the facts off the checkout.

    An unresolved record (no commit yet), or any record with ``refresh``, is read
    at ``link.ref`` / the default branch — the commit the clone lands on becomes
    the pin; a resolved record is fetched at its pinned commit.
    """
    reference = link_reference(record, pinned=bool(record.link_commit or record.link_tag) and not refresh)
    scratch = Path(tempfile.mkdtemp(prefix="floofy-link-"))
    try:
        try:
            commit = clone_into(reference, scratch / "clone", timeout=timeout, allow_local=allow_local)
        except GitRefError as exc:
            raise LinkError(f"{record.mod_id}@{record.version}: {exc}") from exc
        root = scratch / "clone" / reference.subdirectory if reference.subdirectory else scratch / "clone"
        return facts_from_checkout(root, commit=commit)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def write_resolved(record: VersionRecord, facts: LinkFacts) -> dict[str, Any]:
    """Rewrite the record's ``release.json`` with the resolved ``link`` block and path-shaped ``files[]``; returns the document."""
    release = dict(record.release)
    release["link"] = facts.to_link(record.link or {})
    release["files"] = [dict(f) for f in facts.files]
    (record.directory / "release.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
    record.release = release
    return release


def manifest_hash_matches(record: VersionRecord) -> tuple[bool, str]:
    """Whether the record's own ``floofy.json`` hashes to ``link.manifestSha256`` (no clone needed)."""
    link = record.link or {}
    expected = str(link.get("manifestSha256") or "").lower()
    actual = hashlib.sha256(canonical_bytes(record.manifest)).hexdigest()
    if not expected:
        return False, f"{record.mod_id}@{record.version}: link.manifestSha256 is missing (run `registry_tools build --resolve-links`)"
    if expected != actual:
        return False, f"{record.mod_id}@{record.version}: link.manifestSha256 {expected[:12]}… is not the hash of the record's floofy.json ({actual[:12]}…)"
    return True, ""


def check_record(record: VersionRecord, repo: RegistryRepo | None = None, *, timeout: int = 300, allow_local: bool | None = None) -> tuple[list[str], Path | None, Path | None]:
    """Fetch the link record's pinned commit and compare the checkout with the record; returns ``(problems, scratch dir, mod root)``.

    The caller removes the scratch directory (it holds the checkout so the
    submission validator can run ``floofy validate`` and the other checks on it);
    on a clone failure both paths are ``None``.
    """
    problems: list[str] = []
    link = record.link or {}
    if repo is not None and not repo_matches_pattern(str(link.get("repo", "")), repo.repo_url_pattern):
        problems.append(f"link.repo {link.get('repo')!r} does not match this registry's repository pattern {repo.repo_url_pattern!r} (Requirement 8.10)")
    ok, why = manifest_hash_matches(record)
    if not ok:
        problems.append(why)
    try:
        # an unresolved record (no commit yet) is checked against the branch it is made from
        reference = link_reference(record, pinned=bool(record.link_commit or record.link_tag))
    except LinkError as exc:
        return [*problems, str(exc)], None, None
    scratch = Path(tempfile.mkdtemp(prefix="floofy-link-check-"))
    try:
        commit = clone_into(reference, scratch / "clone", timeout=timeout, allow_local=allow_local)
    except GitRefError as exc:
        shutil.rmtree(scratch, ignore_errors=True)
        return [*problems, f"cannot clone {reference.text}: {exc}"], None, None
    root = scratch / "clone" / reference.subdirectory if reference.subdirectory else scratch / "clone"
    expected_commit = str(link.get("commit") or "").lower()
    if expected_commit and commit != expected_commit:
        problems.append(f"the fetch resolved to {commit[:12]} but link.commit says {expected_commit[:12]}")
    if not (root / "floofy.json").is_file():
        problems.append(f"no floofy.json at {reference.subdirectory or 'the repository root'} of the checkout")
        return problems, scratch, None
    facts = facts_from_checkout(root, commit=commit)
    if str(link.get("manifestSha256") or "").lower() != facts.manifest_sha256:
        problems.append(f"the checkout's canonical floofy.json hashes to {facts.manifest_sha256[:12]}…, the record says {str(link.get('manifestSha256') or '')[:12]}…")
    if canonical_bytes(record.manifest) != canonical_bytes(json.loads((root / "floofy.json").read_text(encoding="utf-8"))):
        problems.append("the record's floofy.json differs from the checkout's")
    recorded = {f["path"]: f for f in record.files if isinstance(f, dict) and isinstance(f.get("path"), str)}
    actual = {f["path"]: f for f in facts.files}
    for path in sorted(set(recorded) | set(actual)):
        if path not in actual:
            problems.append(f"files[] lists {path} but the checkout has no such file")
        elif path not in recorded:
            problems.append(f"the checkout ships {path}, which files[] does not list")
        elif recorded[path].get("sha256") != actual[path]["sha256"] or recorded[path].get("size") != actual[path]["size"]:
            problems.append(f"{path}: files[] says {str(recorded[path].get('sha256'))[:12]}…/{recorded[path].get('size')} bytes, the checkout has {actual[path]['sha256'][:12]}…/{actual[path]['size']}")
    return problems, scratch, root
