"""Build ``index.json`` from the registry records (Requirement 8.1, 8.9, 9.2).

The index is derived — never edited by hand: every field comes from the mod
records (:mod:`registry_tools.repo`) and the compatibility matrix. Per version:

* the Requirement 8.1 fields from the manifest (``version``, ``kirocrew.version``
  → ``kirocrew``, ``kirocrew.editions`` → ``editions``, ``dependsOn`` →
  ``dependencies``) and the release record (``files[]``, ``channel``,
  ``publishedAt``, ``tag``, ``yanked``); a **link record** contributes
  ``link{repo, commit, manifestSha256[, path][, ref]}`` and its ``files[]`` as
  ``{path, sha256, size}`` of the checkout instead of archive URLs (Requirement 8.9);
* ``compat{hostVersion: verdict}`` folded in from ``compat.json``: every matrix
  row that has a cell for this ``id@version`` contributes ``hostVersion →
  verdict`` (overrides applied; when two rows disagree for one host version the
  most cautious verdict wins: ``broken`` > ``expected`` > ``tested``), so a
  client can grade versions from the index alone before downloading anything;
* ``kinds`` (the part kinds) and, for a pure App Kit app, ``app{name, subdirectory}``.

The result is checked against ``index.schema.json`` before it is written;
``--check`` compares it with the committed ``index.json`` instead (the CI gate
that the index was regenerated after a record change). Signing is a separate
step (``sign``) or ``--sign KEY`` here.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from floofy_core.canonical import canonical_bytes
from floofy_core.compat import CompatCache
from floofy_core.schema import load_schema
from floofy_core.schema.check import validate_instance

from .repo import RegistryRepo, RepoError, VersionRecord

__all__ = ["BuildResult", "build_index", "fold_compat", "write_index"]

CAUTION = {"broken": 3, "expected": 2, "tested": 1}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class BuildResult:
    index: dict[str, Any]
    problems: list[str] = field(default_factory=list)  # schema violations and record defects (the build fails on any)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def fold_compat(compat: CompatCache | None, mod_id: str, version: str) -> dict[str, str]:
    """``hostVersion → verdict`` for one ``mod@version`` across every matrix row (most cautious verdict wins on a clash)."""
    cells: dict[str, str] = {}
    if compat is None:
        return cells
    for row in compat.rows:
        verdict = row.verdict(mod_id, version)
        if verdict is None:
            continue
        current = cells.get(row.host_version)
        if current is None or CAUTION.get(verdict, 0) > CAUTION.get(current, 0):
            cells[row.host_version] = verdict
    return dict(sorted(cells.items()))


def _dependencies(manifest: dict[str, Any]) -> dict[str, Any]:
    depends = manifest.get("dependsOn")
    return {str(k): v for k, v in depends.items()} if isinstance(depends, dict) else {}


def _version_entry(record: VersionRecord, compat: CompatCache | None) -> dict[str, Any]:
    manifest, release = record.manifest, record.release
    host = manifest.get("kirocrew") if isinstance(manifest.get("kirocrew"), dict) else {}
    entry: dict[str, Any] = {
        "version": record.version,
        "kirocrew": str(host.get("version") or "*"),
        "editions": [str(e) for e in (host.get("editions") or ["internal", "external"])],
        "compat": fold_compat(compat, record.mod_id, record.version),
        "files": (
            [{k: f[k] for k in ("path", "sha256", "size") if k in f} for f in record.files]
            if record.is_link
            else [{k: f[k] for k in ("url", "sha256", "size", "name") if k in f} for f in record.files]
        ),
        "dependencies": _dependencies(manifest),
        "channel": str(release.get("channel") or "stable"),
        "publishedAt": str(release.get("publishedAt") or ""),
    }
    if record.is_link:
        link = record.link or {}
        entry["link"] = {"repo": str(link.get("repo") or ""), "commit": str(link.get("commit") or ""), "manifestSha256": str(link.get("manifestSha256") or "")}
        if isinstance(link.get("path"), str) and link["path"]:
            entry["link"]["path"] = link["path"]
        if record.link_ref:
            entry["link"]["ref"] = record.link_ref
        if record.link_tag and not record.link_commit:
            entry["link"]["tag"] = record.link_tag  # a record made before commits were pinned
    if isinstance(host.get("channels"), list) and host["channels"]:
        entry["channels"] = [str(c) for c in host["channels"]]
    if isinstance(host.get("strict"), bool) and host["strict"]:
        entry["strict"] = True
    if record.tag != record.version:
        entry["tag"] = record.tag
    if record.kinds:
        entry["kinds"] = record.kinds
    app = record.app_facts()
    if app is not None:
        entry["app"] = app
    if isinstance(release.get("yanked"), str) and release["yanked"]:
        entry["yanked"] = release["yanked"]
    changelog = _changelog(release, manifest)
    if changelog:
        entry["changelog"] = changelog
    return entry


def _changelog(release: dict[str, Any], manifest: dict[str, Any]) -> str | None:
    """The version's release-notes URL (Requirement 16.6): ``release.json`` ``changelog``, else the manifest's ``links.changelog``; https only."""
    candidates = [release.get("changelog")]
    links = manifest.get("links") if isinstance(manifest.get("links"), dict) else {}
    candidates.append(links.get("changelog"))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.startswith("https://") and not any(c.isspace() for c in candidate):
            return candidate
    return None


def build_index(repo: RegistryRepo, *, compat: CompatCache | None = None, generated_at: str | None = None, floofycrew_version: str | None = None, resolve_links: bool = False) -> BuildResult:
    """Assemble the index document from every record; schema and record defects land in ``problems``.

    A link record must be **resolved** (``commit``, ``manifestSha256``, path-shaped
    ``files[]``) before it can be indexed: ``resolve_links`` clones each unresolved
    one at its branch and pins the commit in its ``release.json`` (Requirement 8.9); without it an
    unresolved record is a problem. Every link record's ``manifestSha256`` is
    checked against the record's own ``floofy.json`` on every build, and its
    ``repo`` against the registry's ``repoUrlPattern`` when one is pinned.
    """
    from .links import LinkError, manifest_hash_matches, repo_matches_pattern, resolve_record, write_resolved  # noqa: PLC0415

    if compat is None and repo.compat_path.is_file():
        compat = CompatCache.load(repo.compat_path)
    result = BuildResult({"schema": 1, "source": repo.source, "generatedAt": generated_at or _now(), "mods": []})
    if floofycrew_version:
        result.index["floofycrew"] = floofycrew_version
    try:
        mods = list(repo.mods())
    except RepoError as exc:
        result.problems.append(str(exc))
        return result
    for mod in mods:
        if not mod.versions:
            result.notes.append(f"{mod.mod_id}: no version directories; listed with an empty versions[]")
        repo_url = mod.repo()
        if repo_url is None:
            result.problems.append(f"{mod.mod_id}: no repository (mods/{mod.mod_id}/mod.json \"repo\" or a manifest links.repo)")
            repo_url = ""
        for record in mod.versions:
            if not record.is_link:
                continue
            link = record.link or {}
            if not repo_matches_pattern(str(link.get("repo", "")), repo.repo_url_pattern):
                result.problems.append(f"{record.mod_id}@{record.version}: link.repo {link.get('repo')!r} does not match this registry's repository pattern {repo.repo_url_pattern!r} (Requirement 8.10)")
            if not record.link_resolved:
                if not resolve_links:
                    result.problems.append(f"{record.mod_id}@{record.version}: the link record is unresolved (no commit / manifestSha256 / files[]); run `registry_tools record` or `build --resolve-links` to read {link.get('repo')} at {record.link_ref or 'its default branch'} and pin the commit")
                    continue
                try:
                    facts = resolve_record(record)
                except LinkError as exc:
                    result.problems.append(str(exc))
                    continue
                write_resolved(record, facts)
                result.notes.append(f"{record.mod_id}@{record.version}: link record resolved from {link.get('repo')} at {record.link_ref or 'the default branch'} (pinned commit {facts.commit[:12]}, {len(facts.files)} file(s))")
            ok, why = manifest_hash_matches(record)
            if not ok:
                result.problems.append(why)
        entry: dict[str, Any] = {
            "id": mod.mod_id,
            "name": str(mod.field("name", mod.mod_id)),
            "description": str(mod.field("description", "")),
            "authors": [str(a) for a in (mod.field("authors", []) or [])],
            "tags": sorted({str(t) for t in (mod.field("tags", []) or [])}),
            "repo": repo_url,
            "versions": [_version_entry(v, compat) for v in mod.versions],
        }
        for optional in ("license", "links", "icon"):
            value = mod.field(optional)
            if value:
                entry[optional] = value
        result.index["mods"].append(entry)
    for error in validate_instance(result.index, load_schema("index")):
        result.problems.append(f"index.json{error.path or '/'}: {error.message}")
    return result


def write_index(repo: RegistryRepo, result: BuildResult, *, out: Path | None = None) -> Path:
    target = Path(out) if out else repo.index_path
    target.write_text(json.dumps(result.index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def index_matches(existing: Path, result: BuildResult) -> tuple[bool, str]:
    """Whether the committed index equals the rebuilt one (compared in canonical form).

    ``generatedAt`` is ignored, and so is the ``floofycrew`` release stamp when the
    rebuild was not given one: the stamp is set by whoever publishes (the Forge's
    release stage, ``build --floofycrew``), while the CI gate (``build --check``,
    Requirement 8.7) only knows the records under ``mods/`` — a gate that failed on
    the publisher's own stamp would reject every release. An explicit
    ``--floofycrew`` on the check still has to match.
    """
    try:
        committed = json.loads(Path(existing).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, f"{existing} does not exist; run `registry_tools build`"
    except (OSError, ValueError) as exc:
        return False, f"{existing}: {exc}"
    if not isinstance(committed, dict):
        return False, f"{existing}: not a JSON object"
    ignored = {"generatedAt"} | ({"floofycrew"} if "floofycrew" not in result.index else set())
    left = {k: v for k, v in committed.items() if k not in ignored}
    right = {k: v for k, v in result.index.items() if k not in ignored}
    if canonical_bytes(left) != canonical_bytes(right):
        return False, f"{existing} is stale: it differs from the index rebuilt from mods/ (run `registry_tools build` and commit)"
    return True, f"{existing} is up to date with mods/"
