"""The host's federated app registries: add/remove FloofyCrew's operator row (Requirement 8.6; design DR-3 "Registry interop").

A KiroCrew host lists apps from external registries it is told about: rows
``{name, repo, branch, trust}`` in the host ``config.json`` top-level
``registries`` list (``kiro_crew/config/sections.py`` L3514
``ExternalRegistryConfig``), managed by ``GET``/``PUT /api/apps/registries``
(``kiro_crew/apps/routes.py`` L3826–L4045 ``handle_registries`` in 0.7.0.5).
The PUT **replaces the list verbatim** after validating every entry — ``repo``
must be a bare name or an https/ssh/scp git URL (``_is_safe_repo_identifier``
L2964–L2989, no plaintext ``http://``), ``name`` must match
``^[A-Za-z0-9_\\-. ]+$`` (L3922), ``branch`` ``^[A-Za-z0-9][A-Za-z0-9_\\-./]*$``
without ``..`` (L3925–L3927), ``trust`` may only be ``index`` for an operator
row (L3935–L3948: the ``owner`` tier is the build's to grant) — and refuses a
row whose name or repo an **edition-pinned** registry already owns (L3907–L3915,
L3950–L3954: an edition's companion may pin registries of its own; the edition
adapter names those ids through :func:`floofy_core.editions.pinned_registry_ids`;
the core drops both rows on a contest, ``registry.py`` L610–L697). The host
writes the list atomically to ``config_path()`` (L4029–L4031) and records
``registries.host_trust_granted`` in its SEL audit for every newly trusted host.

This module mirrors those rules client-side so the manager never sends a row the
host would refuse, and applies FloofyCrew's row two ways:

* **gateway up** — ``GET /api/apps/registries`` (the operator rows, without the
  pinned ones the host reports separately), replace/append ours, ``PUT`` the
  whole list back through the authenticated loopback session;
* **gateway down** — the same edit on ``<host home>/config.json`` under the
  host's ``<config>.lock`` (:func:`floofy_core.hostcli._locked_rmw`), which is
  what the PUT does server-side.

The row is an **operator** row: ``trust: index`` (the host clones credential-
free), an id of FloofyCrew's own (default ``floofycrew``; an edition adapter's
default source may name another), never one of the ids the edition pins
(:func:`floofy_core.editions.pinned_registry_ids`) — a collision is refused here
with the same message the host would give. Adding a registry host is a trust
grant the host itself audits, so the CLI asks first and writes a
``host-registry-add`` row to ``audit.jsonl``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .gateway import GatewayAuthError, GatewaySession
from .hostcli import _locked_rmw

__all__ = ["BRANCH_RE", "HostRegistryError", "HostRegistryRow", "NAME_RE", "REGISTRIES_ROUTE", "apply_row", "current_rows", "is_safe_repo", "remove_row"]

REGISTRIES_ROUTE = "/api/apps/registries"
#: ``routes.py`` L3922 / L3925: the host's operator-row charsets.
NAME_RE = re.compile(r"^[A-Za-z0-9_\-. ]+$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-./]*$")
_BARE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_HTTPS_RE = re.compile(r"^https://[A-Za-z0-9.\-]+(?::[0-9]+)?/[A-Za-z0-9._/\-]+$")
_SCP_RE = re.compile(r"^[A-Za-z0-9._\-]+@[A-Za-z0-9.\-]+:[A-Za-z0-9._/\-]+$")
_SSH_RE = re.compile(r"^ssh://(?:[A-Za-z0-9._\-]+@)?[A-Za-z0-9.\-]+(?::[0-9]+)?/[A-Za-z0-9._/\-]+$")
DEFAULT_ROW_NAME = "floofycrew"


class HostRegistryError(Exception):
    """A row the host would refuse, or a host that could not be reached/written."""


def is_safe_repo(repo: str) -> bool:
    """``routes.py`` L2964–L2989 ``_is_safe_repo_identifier``: a bare name or an https/ssh/scp git URL, no shell metacharacters or ``..``."""
    if not repo or ".." in repo or any(c in repo for c in " \t\n\r;|&$`<>()*?!\\\"'"):
        return False
    return bool(_BARE_RE.match(repo) or _HTTPS_RE.match(repo) or _SCP_RE.match(repo) or _SSH_RE.match(repo))


def _identity_key(name: str) -> str:
    """The collision key the host uses (``registry.py`` L414–L432 ``_registry_identity_key``): the cache file stem, case-folded."""
    if re.match(r"^[A-Za-z0-9_\-]+$", name):
        return name.casefold()
    import hashlib  # noqa: PLC0415

    slug = re.sub(r"[^A-Za-z0-9_\-]+", "-", name).strip("-") or "registry"
    return f"{slug}-{hashlib.sha256(name.encode('utf-8')).hexdigest()[:8]}".casefold()


@dataclass(frozen=True)
class HostRegistryRow:
    """One operator row as the host stores it (``trust`` is always ``index`` for an operator row)."""

    name: str
    repo: str
    branch: str = "main"

    def validate(self, pinned_ids: list[str] | tuple[str, ...] = (), pinned_repos: list[str] | tuple[str, ...] = ()) -> None:
        if not NAME_RE.match(self.name):
            raise HostRegistryError(f"invalid registry name {self.name!r} (the host accepts ^[A-Za-z0-9_\\-. ]+$)")
        if not is_safe_repo(self.repo):
            raise HostRegistryError(f"invalid registry repo {self.repo!r}: the host accepts a bare name or an https/ssh/scp git URL (never plaintext http)")
        if not BRANCH_RE.match(self.branch) or ".." in self.branch:
            raise HostRegistryError(f"invalid branch {self.branch!r}")
        if _identity_key(self.name) in {_identity_key(p) for p in pinned_ids}:
            raise HostRegistryError(f"{self.name!r} is the id of a registry this edition pins ({', '.join(pinned_ids)}); FloofyCrew adds operator rows under its own ids only (Requirement 8.6)")
        if self.repo in set(pinned_repos):
            raise HostRegistryError(f"{self.repo!r} is already provided by this build (a pinned registry); nothing to add")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "repo": self.repo, "branch": self.branch, "trust": "index"}


def _config_path(host_home: Path) -> Path:
    return Path(host_home) / "config.json"


def current_rows(host_home: Path, session: GatewaySession | None) -> dict[str, Any]:
    """``{"registries": [operator rows], "pinned": [build rows], "how": "gateway" | "config"}``."""
    if session is not None:
        try:
            response = session.get(REGISTRIES_ROUTE)
        except (GatewayAuthError, OSError) as exc:
            raise HostRegistryError(f"{session.label}{REGISTRIES_ROUTE}: {exc}") from exc
        if response.status != 200:
            raise HostRegistryError(f"{session.label}{REGISTRIES_ROUTE} answered {response.status}: {response.body[:200].decode('utf-8', 'replace')}")
        document = response.json()
        return {"registries": list(document.get("registries") or []), "pinned": list(document.get("pinned") or []), "how": "gateway"}
    path = _config_path(host_home)
    try:
        document = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError) as exc:
        raise HostRegistryError(f"{path}: {exc}") from exc
    rows = document.get("registries") if isinstance(document, dict) else None
    return {"registries": [r for r in (rows or []) if isinstance(r, dict)], "pinned": [], "how": "config"}


def _merge(rows: list[dict[str, Any]], row: HostRegistryRow) -> list[dict[str, Any]]:
    kept = [dict(r) for r in rows if str(r.get("name", "")).casefold() != row.name.casefold() and r.get("repo") != row.repo]
    return [*kept, row.to_dict()]


def _put(session: GatewaySession, rows: list[dict[str, Any]]) -> None:
    payload = [{"name": r.get("name", ""), "repo": r.get("repo", ""), "branch": r.get("branch", "main") or "main", "trust": "index"} for r in rows]
    try:
        response = session.request("PUT", REGISTRIES_ROUTE, {"registries": payload})
    except (GatewayAuthError, OSError) as exc:
        raise HostRegistryError(f"{session.label}{REGISTRIES_ROUTE}: {exc}") from exc
    if response.status != 200:
        raise HostRegistryError(f"the host refused the registries list ({response.status}): {response.body[:300].decode('utf-8', 'replace')}")


def _write_config(host_home: Path, rows: list[dict[str, Any]]) -> bool:
    def mutate(document: dict[str, Any]) -> dict[str, Any]:
        document["registries"] = [{"name": r.get("name", ""), "repo": r.get("repo", ""), "branch": r.get("branch", "main") or "main", "trust": "index"} for r in rows]
        return document

    _document, written = _locked_rmw(_config_path(host_home), mutate)
    return written


def apply_row(host_home: Path, row: HostRegistryRow, *, session: GatewaySession | None, pinned_ids: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    """Add (or update) FloofyCrew's operator row; returns ``{how, row, rows, written}``.

    Pinned ids come from the edition adapter *and* from what a running host
    reports as ``pinned`` — the host refuses either collision (``routes.py``
    L3907–L3915, L3950–L3954), so both are checked here first.
    """
    state = current_rows(host_home, session)
    reported_ids = [str(p.get("name")) for p in state["pinned"] if isinstance(p, dict) and p.get("name")]
    reported_repos = [str(p.get("repo")) for p in state["pinned"] if isinstance(p, dict) and p.get("repo")]
    row.validate([*pinned_ids, *reported_ids], reported_repos)
    rows = _merge(state["registries"], row)
    unchanged = any(r.get("name") == row.name and r.get("repo") == row.repo and (r.get("branch") or "main") == row.branch for r in state["registries"])
    if session is not None:
        if not unchanged:
            _put(session, rows)
        written = not unchanged
    else:
        written = _write_config(host_home, rows) if not unchanged else False
    return {"how": state["how"], "row": row.to_dict(), "rows": rows, "written": written}


def remove_row(host_home: Path, name: str, *, session: GatewaySession | None) -> dict[str, Any]:
    """Drop the operator row named ``name`` (never a pinned one — those are not in the operator list)."""
    state = current_rows(host_home, session)
    rows = [dict(r) for r in state["registries"] if str(r.get("name", "")).casefold() != name.casefold()]
    removed = len(rows) != len(state["registries"])
    if removed:
        if session is not None:
            _put(session, rows)
        else:
            _write_config(host_home, rows)
    return {"how": state["how"], "removed": removed, "rows": rows}
