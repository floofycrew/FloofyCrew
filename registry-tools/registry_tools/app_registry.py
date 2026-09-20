"""``app-registry.json`` — the KiroCrew-compatible index of the registry's app-kind mods (Requirement 8.6).

The host's federated app registries are git repositories: the gateway shallow-
clones ``repo`` at ``branch`` and reads ``app-registry.json`` at the clone root
(``kiro_crew/apps/registry.py`` L3043–L3199 ``_fetch_external_registry_index`` in
0.7.0.5). That file must be a JSON **array** of objects (L3154–L3163; non-object
items are dropped); each entry names one app with

* ``name`` — the app id the store lists and installs under;
* ``gitUrl`` / ``repo`` — the app's own git URL (``_entry_git_url`` L360–L373
  prefers ``gitUrl``, falls back to ``repo``; a userinfo-bearing URL is stripped
  by ``_credential_free_external_registry_entries`` L2875);
* ``branch`` — the ref to clone (default ``main``; validated as
  ``^[A-Za-z0-9][A-Za-z0-9_\\-./]*$`` without ``..``, so a release tag works);
* optional ``subdirectory`` — where ``app.json`` lives inside the clone
  (``_manifest_source_coordinates`` L1361–L1384), optional ``commit`` pin.

The bundled seed the host ships (``kiro_crew/apps/app-registry.json``) has
exactly this shape: ``[{"name", "gitUrl", "repo", "branch"}]``. Display text
(description, screenshots) is deliberately NOT here — the host reads it from the
app's own ``app.json`` ("single source of truth", module docstring L1–L11).

So a FloofyCrew registry doubles as a KiroCrew app registry for its pure App Kit
mods (a single ``app`` part): for each such mod the newest non-yanked version
with ``app{name, subdirectory}`` facts becomes one row — ``name`` = the app's
name, ``gitUrl``/``repo`` = the mod's ``repo`` (which must be a cloneable git URL:
https, ssh:// or scp-style), ``branch`` = the version's release ``tag`` (the
Obsidian model pins the row to the released code), ``subdirectory`` = the
directory holding the part's ``app.json``. The manager adds the registry
repository itself as an operator row (``floofy registry add … --host-registry``,
:mod:`floofy_core.hostregistry`) so the host clones it and lists these apps in
its own App Store beside FloofyCrew's index. The host's admission verdict on each
app is the host's business — shown, never enforced (Requirement 2.3, 11.2).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from floofy_core.canonical import canonical_bytes
from floofy_core.semver import InvalidVersion, Version

from .repo import RegistryRepo

__all__ = ["APP_NAME_RE", "BRANCH_RE", "app_registry_matches", "app_registry_rows", "is_git_url", "write_app_registry"]

#: ``kiro_crew/apps/routes.py`` L3925–L3927: the branch/tag charset the host accepts.
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-./]*$")
#: An app id as the host's ``app_name_error`` accepts it (lower-case slug).
APP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_HTTPS_RE = re.compile(r"^https://[A-Za-z0-9.\-]+(?::[0-9]+)?/[A-Za-z0-9._/\-]+$")
_SCP_RE = re.compile(r"^[A-Za-z0-9._\-]+@[A-Za-z0-9.\-]+:[A-Za-z0-9._/\-]+$")
_SSH_RE = re.compile(r"^ssh://(?:[A-Za-z0-9._\-]+@)?[A-Za-z0-9.\-]+(?::[0-9]+)?/[A-Za-z0-9._/\-]+$")


def is_git_url(repo: str) -> bool:
    """A clone target the host's ``_is_safe_repo_identifier`` (``routes.py`` L2964–L2989) admits: https, ssh:// or scp-style; never plaintext http."""
    if not repo or ".." in repo or any(c in repo for c in " \t\n\r;|&$`<>()*?!\\\"'"):
        return False
    return bool(_HTTPS_RE.match(repo) or _SCP_RE.match(repo) or _SSH_RE.match(repo))


def app_registry_rows(index: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """``(rows, notes)``: one host row per app-kind mod of ``index``; notes name the mods skipped and why."""
    rows: list[dict[str, Any]] = []
    notes: list[str] = []
    seen: set[str] = set()
    for mod in index.get("mods") or []:
        if not isinstance(mod, dict):
            continue
        candidates = []
        for version in mod.get("versions") or []:
            if not isinstance(version, dict) or not isinstance(version.get("app"), dict) or version.get("yanked"):
                continue
            try:
                candidates.append((Version.parse(str(version.get("version"))), version))
            except InvalidVersion:
                continue
        if not candidates:
            continue
        _parsed, version = max(candidates, key=lambda item: item[0])
        repo = mod.get("repo")
        if not isinstance(repo, str) or not is_git_url(repo):
            notes.append(f"{mod.get('id')}: repo {repo!r} is not a cloneable https/ssh git URL; no app row")
            continue
        app = version["app"]
        name = str(app.get("name") or mod.get("id") or "")
        if not APP_NAME_RE.match(name):
            notes.append(f"{mod.get('id')}: app name {name!r} is not a valid host app id; no app row")
            continue
        branch = str(version.get("tag") or version.get("version") or "main")
        if not BRANCH_RE.match(branch) or ".." in branch:
            notes.append(f"{mod.get('id')}: release tag {branch!r} is not a valid git ref for the host; no app row")
            continue
        if name in seen:
            notes.append(f"{mod.get('id')}: a row named {name!r} already exists; the host keeps the first")
            continue
        seen.add(name)
        row: dict[str, Any] = {"name": name, "gitUrl": repo, "repo": repo, "branch": branch}
        subdirectory = app.get("subdirectory")
        if isinstance(subdirectory, str) and subdirectory:
            row["subdirectory"] = subdirectory
        rows.append(row)
    return rows, notes


def write_app_registry(repo: RegistryRepo, index: dict[str, Any], *, out: Path | None = None) -> tuple[Path, list[dict[str, Any]]]:
    rows, _notes = app_registry_rows(index)
    target = Path(out) if out else repo.app_registry_path
    target.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return target, rows


def app_registry_matches(repo: RegistryRepo, index: dict[str, Any]) -> tuple[bool, str]:
    rows, _notes = app_registry_rows(index)
    path = repo.app_registry_path
    try:
        committed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return (True, f"{path} not needed: no app-kind mods") if not rows else (False, f"{path} is missing; run `registry_tools build --app-registry`")
    except (OSError, ValueError) as exc:
        return False, f"{path}: {exc}"
    if canonical_bytes(committed) != canonical_bytes(rows):
        return False, f"{path} is stale: it differs from the rows derived from index.json (run `registry_tools build --app-registry` and commit)"
    return True, f"{path} is up to date ({len(rows)} app row(s))"
