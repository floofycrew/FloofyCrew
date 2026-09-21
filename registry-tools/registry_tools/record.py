"""``registry_tools record``: make or refresh a link record from the mod's repository — no tag involved (Requirement 8.9).

A mod that lives under a directory of a repository is listed by pointing the
registry at that repository, that directory and the branch it is developed on
(the default branch unless said otherwise). This command reads the mod there —
a shallow clone of the branch with the operator's own credentials, or a local
checkout at a commitish (``--source``, for a commit that is not pushed yet) —
takes the version from the ``floofy.json`` it finds, and writes the version
record pinned at the **commit** it read: ``mods/<id>/<version>/floofy.json`` (the
manifest verbatim), ``release.json`` with ``link{repo, path, ref?, commit,
manifestSha256}`` and the checkout's ``files[]``, and ``mods/<id>/mod.json``
when the mod is new to the registry. A client fetches exactly that commit;
the repository's branches may move on freely and nothing needs tagging.

Re-running the command for a mod whose branch now holds a newer version adds
the new version's record beside the old one; for the same version it refreshes
the pin (``--refresh``), which is how a record made against a wrong commit is
corrected. The index is not rebuilt here: ``registry_tools build --sign …`` and
``registry_tools readme`` follow, as after any record change.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from floofy_core.gitsource import GitRef, GitRefError, clone_into

from .links import LinkError, LinkFacts, facts_from_checkout, facts_from_local_commit
from .repo import RegistryRepo

__all__ = ["RecordError", "RecordResult", "make_record"]

_MOD_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


class RecordError(ValueError):
    """The record cannot be made from what the repository holds."""


@dataclass
class RecordResult:
    mod_id: str
    version: str
    repo: str
    path: str
    ref: str | None
    commit: str
    manifest_sha256: str
    files: int
    created: bool
    mod_json_written: bool
    written: list[Path] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.mod_id, "version": self.version, "repo": self.repo, "path": self.path, "ref": self.ref, "commit": self.commit, "manifestSha256": self.manifest_sha256, "files": self.files, "created": self.created, "modJsonWritten": self.mod_json_written, "written": [str(p) for p in self.written]}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_from_repository(repo_url: str, path: str, ref: str | None, *, timeout: int, allow_local: bool | None) -> tuple[LinkFacts, dict[str, Any]]:
    """Shallow-clone ``repo_url`` at ``ref`` (default branch when ``None``) and read the mod at ``path``."""
    try:
        reference = GitRef.parse(repo_url + (f"#{path}" if path else ""), ref=ref, allow_local=allow_local) if ref else GitRef.default_branch(repo_url, path, allow_local=allow_local)
    except GitRefError as exc:
        raise RecordError(f"{repo_url}: {exc}") from exc
    scratch = Path(tempfile.mkdtemp(prefix="floofy-record-"))
    try:
        try:
            commit = clone_into(reference, scratch / "clone", timeout=timeout, allow_local=allow_local)
        except GitRefError as exc:
            raise RecordError(str(exc)) from exc
        root = scratch / "clone" / path if path else scratch / "clone"
        if not (root / "floofy.json").is_file():
            raise RecordError(f"{repo_url} at {ref or 'its default branch'}: no floofy.json under {path or 'the repository root'}")
        manifest = json.loads((root / "floofy.json").read_text(encoding="utf-8"))
        try:
            facts = facts_from_checkout(root, commit=commit)
        except LinkError as exc:
            raise RecordError(str(exc)) from exc
        return facts, manifest
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _read_from_local(source: Path, path: str, commitish: str) -> tuple[LinkFacts, dict[str, Any]]:
    """Read the mod at ``path`` of a local checkout at ``commitish`` (the committed tree, never the working copy)."""
    try:
        facts = facts_from_local_commit(Path(source), commitish, path)
    except LinkError as exc:
        raise RecordError(str(exc)) from exc
    # the manifest of the committed tree: export just that file
    import subprocess  # noqa: PLC0415

    done = subprocess.run(["git", "-C", str(source), "show", f"{facts.commit}:{path + '/' if path else ''}floofy.json"], capture_output=True, text=True, check=False, timeout=60)
    if done.returncode != 0:
        raise RecordError(f"{source}: cannot read {path}/floofy.json at {facts.commit[:12]}: {(done.stderr or '').strip()}")
    return facts, json.loads(done.stdout)


def make_record(
    repo: RegistryRepo,
    mod_id: str,
    *,
    repo_url: str | None = None,
    path: str | None = None,
    ref: str | None = None,
    source: Path | None = None,
    source_ref: str = "HEAD",
    channel: str = "stable",
    contact: str | None = None,
    refresh: bool = False,
    timeout: int = 300,
    allow_local: bool | None = None,
) -> RecordResult:
    """Write (or refresh) the link record of ``mod_id`` from its repository; returns what was pinned.

    ``repo_url`` defaults to the mod's existing ``mod.json`` ``repo``, else the
    registry's ``floofycrew`` repository (``registry.json``); ``path`` to
    ``mods/<id>``; ``ref`` to the branch an existing record names, else the
    default branch. With ``source`` the facts come from that local checkout at
    ``source_ref`` instead of a clone. An existing record for the version found is
    left alone unless ``refresh``.
    """
    if not _MOD_ID.match(mod_id):
        raise RecordError(f"{mod_id!r} is not a mod id")
    mod_json_path = repo.root / "mods" / mod_id / "mod.json"
    curated: dict[str, Any] = {}
    if mod_json_path.is_file():
        try:
            curated = json.loads(mod_json_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise RecordError(f"{mod_json_path}: not JSON: {exc}") from exc
    url = repo_url or (curated.get("repo") if isinstance(curated.get("repo"), str) else None) or (repo.config.get("floofycrew") if isinstance(repo.config.get("floofycrew"), str) else None)
    if not url:
        raise RecordError(f"{mod_id}: no repository — pass --repo (the registry's registry.json names no floofycrew repository and mods/{mod_id}/mod.json has no repo)")
    if repo.repo_url_pattern and not re.search(repo.repo_url_pattern, url):
        raise RecordError(f"{url!r} does not match this registry's repository pattern {repo.repo_url_pattern!r} (Requirement 8.10)")
    mod_path = path if path is not None else f"mods/{mod_id}"
    mod_path = mod_path.strip("/")
    if ref is None:
        # keep the branch an earlier record of this mod named, if any
        for version_dir in sorted((repo.root / "mods" / mod_id).glob("*/release.json")) if (repo.root / "mods" / mod_id).is_dir() else []:
            try:
                previous = json.loads(version_dir.read_text(encoding="utf-8"))
            except ValueError:
                continue
            link = previous.get("link") if isinstance(previous.get("link"), dict) else {}
            if isinstance(link.get("ref"), str) and link["ref"]:
                ref = link["ref"]
    if source is not None:
        facts, manifest = _read_from_local(Path(source), mod_path, source_ref)
    else:
        facts, manifest = _read_from_repository(url, mod_path, ref, timeout=timeout, allow_local=allow_local)
    if not isinstance(manifest, dict) or manifest.get("id") != mod_id:
        raise RecordError(f"{url} {mod_path}: the floofy.json there is for {manifest.get('id') if isinstance(manifest, dict) else '?'!r}, not {mod_id!r}")
    version = str(manifest.get("version") or "")
    if not version:
        raise RecordError(f"{url} {mod_path}: floofy.json has no version")
    record_dir = repo.root / "mods" / mod_id / version
    created = not (record_dir / "release.json").is_file()
    if not created and not refresh:
        raise RecordError(f"{mod_id}@{version} is already recorded (mods/{mod_id}/{version}); the branch holds no newer version — pass --refresh to re-pin it at {facts.commit[:12]}")
    record_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    (record_dir / "floofy.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    written.append(record_dir / "floofy.json")
    previous_release: dict[str, Any] = {}
    if not created:
        try:
            previous_release = json.loads((record_dir / "release.json").read_text(encoding="utf-8"))
        except ValueError:
            previous_release = {}
    link: dict[str, Any] = {"repo": url, "path": mod_path}
    if ref:
        link["ref"] = ref
    link.update({"commit": facts.commit, "manifestSha256": facts.manifest_sha256})
    release: dict[str, Any] = {k: v for k, v in previous_release.items() if k not in ("tag", "link", "files")}
    release.setdefault("channel", channel)
    release["publishedAt"] = previous_release.get("publishedAt") or _now()
    if not created:
        release["refreshedAt"] = _now()
    release["link"] = link
    release["files"] = facts.files
    (record_dir / "release.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
    written.append(record_dir / "release.json")
    mod_json_written = False
    if not mod_json_path.is_file():
        curated = {"repo": url, "tags": sorted({str(t) for t in manifest.get("tags") or []}), "links": {}}
        who = contact or next((str(a) for a in (manifest.get("authors") or []) if isinstance(a, str)), None)
        if who:
            curated["contact"] = who
        mod_json_path.write_text(json.dumps(curated, indent=2) + "\n", encoding="utf-8")
        written.append(mod_json_path)
        mod_json_written = True
    elif contact and curated.get("contact") != contact:
        curated["contact"] = contact
        mod_json_path.write_text(json.dumps(curated, indent=2) + "\n", encoding="utf-8")
        written.append(mod_json_path)
        mod_json_written = True
    return RecordResult(mod_id, version, url, mod_path, ref, facts.commit, facts.manifest_sha256, len(facts.files), created, mod_json_written, written)
