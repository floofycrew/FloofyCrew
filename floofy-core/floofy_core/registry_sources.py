"""Registry sources, per-source trust and the cache refresh (Requirement 8.3, 8.4, 11.3, 11.6).

``registries.json``::

    {"schema": 1, "sources": [{"url": "https://…/", "name": "…", "trust": "index" | "owner",
                               "allowUnsigned": false, "keyId": "…", "addedAt": "…"}]}

The trust settings are the **user's** controls with safe defaults (Requirement
11.3): an index is used only when its detached signature verifies (``verified``)
or the user loosened that source with ``--allow-unsigned`` — printed as a
warning and written to the audit log as ``registry-trust-loosened``. ``trust``
records what the user trusts the source for: ``index`` (the index vouches for the
mod files' hashes; default) or ``owner`` (the source operator is trusted as the
author of what it lists).

``refresh`` fetches ``index.json`` (+ ``index.json.sig``) and ``compat.json``
(+ ``.sig``) of every source over HTTPS (plaintext only to loopback — FloofyCrew's
own traffic obeys Requirement 11.6 too) into ``cache/sources/<key>/`` with a
``meta.json`` verdict, then rebuilds the merged ``cache/index.json`` /
``cache/compat.json`` from the **usable** sources only. A mod id present in two
usable sources is namespaced ``<source>/<id>`` by :class:`floofy_core.registry.IndexCache`
(Requirement 8.4); the namespace is the source's **configured** label — its
``--name``, else its cache key — never a name the index claims for itself.

Signature verification is the :data:`VERIFIER` hook. Until task 7.3 lands the
canonical-JSON detached-signature format and the per-edition pinned keys, a present
signature is ``unverified-pending-7.x`` and an absent one ``unsigned`` — neither is
usable without ``allowUnsigned``, which is exactly the default-safe rule (an
``invalid`` signature is admitted under the same override, warned about loudly and
recorded — design "Testing Strategy → Registry": tampered index refused by
default, admitted with override + audit).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .datahome import DataHome
from .installer import InstallError, _refuse_plaintext

__all__ = [
    "MAX_INDEX_BYTES",
    "RefreshOutcome",
    "STATUS_INVALID",
    "STATUS_PENDING",
    "STATUS_UNSIGNED",
    "STATUS_VERIFIED",
    "Source",
    "SourceStore",
    "TRUST_LEVELS",
    "VERIFIER",
    "VerifyResult",
    "is_usable",
    "rebuild_merged",
    "refresh",
    "source_key",
    "source_label",
    "verify_index",
]

TRUST_LEVELS = ("index", "owner")
MAX_INDEX_BYTES = 20 * 1024 * 1024

#: The verdicts :data:`VERIFIER` may return.
STATUS_VERIFIED = "verified"
STATUS_UNSIGNED = "unsigned"
STATUS_INVALID = "invalid"
STATUS_PENDING = "unverified-pending-7.x"
VERIFY_STATUSES = (STATUS_VERIFIED, STATUS_UNSIGNED, STATUS_INVALID, STATUS_PENDING)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def source_key(url: str) -> str:
    """A stable directory name for a source URL (host + path, then 8 hex of the URL's sha256)."""
    parsed = urllib.parse.urlsplit(url)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{parsed.hostname or 'source'}{parsed.path}").strip("_")[:60]
    return f"{stem}-{hashlib.sha256(url.encode('utf-8')).hexdigest()[:8]}"


@dataclass
class Source:
    """One row of ``registries.json``."""

    url: str
    trust: str = "index"
    allow_unsigned: bool = False
    key_id: str | None = None
    name: str | None = None
    added_at: str = field(default_factory=_now)

    @property
    def key(self) -> str:
        return source_key(self.url)

    @property
    def label(self) -> str:
        """The user-facing name and the collision namespace: ``--name``, else the cache key."""
        return source_label(self)

    @property
    def base(self) -> str:
        """The directory URL the files hang off (a URL naming ``index.json`` is accepted too)."""
        if self.url.endswith("/index.json"):
            return self.url[: -len("index.json")]
        return self.url if self.url.endswith("/") else self.url + "/"

    def to_record(self) -> dict[str, Any]:
        """The ``registries.json`` row (design "Consent, trust and audit")."""
        return {"url": self.url, "name": self.name, "trust": self.trust, "allowUnsigned": self.allow_unsigned, "keyId": self.key_id, "addedAt": self.added_at}

    def to_dict(self) -> dict[str, Any]:
        return {**self.to_record(), "key": self.key, "label": self.label}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Source":
        return cls(
            str(raw["url"]),
            str(raw.get("trust") or "index"),
            bool(raw.get("allowUnsigned", False)),
            raw.get("keyId") if isinstance(raw.get("keyId"), str) else None,
            raw.get("name") if isinstance(raw.get("name"), str) else None,
            str(raw.get("addedAt") or _now()),
        )


def source_label(source: Source) -> str:
    return source.name or source.key


@dataclass
class SourceStore:
    """``registries.json``: load, add (reporting a loosening), remove, save."""

    path: Path
    sources: list[Source] = field(default_factory=list)

    @classmethod
    def load(cls, home: DataHome) -> "SourceStore":
        store = cls(home.registries)
        try:
            document = json.loads(home.registries.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return store
        except (OSError, ValueError) as exc:
            raise InstallError(f"{home.registries}: {exc}") from exc
        for raw in (document.get("sources") if isinstance(document, dict) else None) or []:
            if isinstance(raw, dict) and isinstance(raw.get("url"), str):
                store.sources.append(Source.from_dict(raw))
        return store

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"schema": 1, "sources": [s.to_record() for s in self.sources]}, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def find(self, ref: str) -> Source | None:
        return next((s for s in self.sources if s.url == ref or s.key == ref or s.name == ref), None)

    def add(self, source: Source) -> tuple[Source, bool]:
        """Add or update by URL; returns ``(source, loosened)`` — ``loosened`` says a signature requirement was dropped.

        Refuses a plaintext non-loopback URL (Requirement 11.6) and an unknown trust
        level. Re-adding an existing URL updates its trust settings in place; adding it
        again *without* ``--allow-unsigned`` tightens it back and is not a loosening.
        """
        if source.trust not in TRUST_LEVELS:
            raise InstallError(f"trust must be one of {', '.join(TRUST_LEVELS)}")
        _refuse_plaintext(source.url)
        if source.name and any(s.name == source.name and s.url != source.url for s in self.sources):
            raise InstallError(f"a different source is already named {source.name!r}")
        existing = self.find(source.url)
        loosened = source.allow_unsigned and (existing is None or not existing.allow_unsigned)
        if existing is not None:
            existing.trust, existing.allow_unsigned = source.trust, source.allow_unsigned
            existing.key_id, existing.name = source.key_id or existing.key_id, source.name or existing.name
            return existing, loosened
        self.sources.append(source)
        return source, loosened

    def remove(self, ref: str) -> Source | None:
        source = self.find(ref)
        if source is not None:
            self.sources.remove(source)
        return source


# --- verification hook --------------------------------------------------------------------------------


@dataclass
class VerifyResult:
    """What :data:`VERIFIER` says about one index (``status`` is one of :data:`VERIFY_STATUSES`)."""

    status: str
    detail: str = ""
    key_id: str | None = None

    @property
    def verified(self) -> bool:
        return self.status == STATUS_VERIFIED

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "detail": self.detail, "keyId": self.key_id}


def verify_index(index_bytes: bytes, signature: bytes | None, source: Source) -> VerifyResult:
    """The detached-signature check (Requirement 8.3, 11.6) — the hook task 7.3 completes.

    Contract for the 7.3 implementation (replace this body, or install a callable in
    :data:`VERIFIER`): canonicalise ``index_bytes`` (JSON, sorted keys, no whitespace —
    design "Registry index"), verify ``signature`` against the keys pinned per edition
    by the adapters (a ``registry_keys()`` hook on ``floofy_edition_*``), honour
    ``source.key_id`` when set (a signature by another key is ``invalid``), and return
    ``verified`` / ``invalid`` with the key id used. ``signature is None`` stays
    ``unsigned``. The caller decides usability from the status alone
    (:func:`is_usable`), so the hook never needs to know about ``allowUnsigned``.
    """
    if signature is None:
        return VerifyResult(STATUS_UNSIGNED, "no detached signature (index.json.sig) published by the source")
    return VerifyResult(STATUS_PENDING, "a signature is present but the verifier (canonical JSON, pinned per-edition keys) lands in task 7.3; refused until then unless the source allows unsigned", source.key_id)


#: Replaceable by task 7.3 (and by tests): ``(index_bytes, signature_or_None, source) -> VerifyResult``.
VERIFIER: Callable[[bytes, bytes | None, Source], VerifyResult] = verify_index


def is_usable(status: str | None, source: Source) -> bool:
    """The default-safe rule (Requirement 8.3): a verified index, or any fetched index the user explicitly allowed unsigned.

    ``None`` (nothing fetched, or not an index) is never usable. The rule is applied
    at refresh time *and* when the merged cache is rebuilt, so tightening or loosening
    a source takes effect on the cached copy without a refetch.
    """
    if status is None:
        return False
    return status == STATUS_VERIFIED or bool(source.allow_unsigned)


def refusal_text(status: str, source: Source) -> str:
    return f"index not verified ({status}); refused by default — `floofy registry add {source.url} --allow-unsigned` accepts it at your own risk (Requirement 8.3)"


# --- refresh ---------------------------------------------------------------------------------------------


def _fetch(url: str, *, opener: Callable[..., Any] | None = None, optional: bool = False) -> bytes | None:
    _refuse_plaintext(url)
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(urllib.request.Request(url, headers={"User-Agent": "floofy (unofficial KiroCrew mod manager)"}), timeout=30) as response:
            data = response.read(MAX_INDEX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if optional and exc.code == 404:
            return None
        raise InstallError(f"{url}: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise InstallError(f"{url}: {exc}") from exc
    if len(data) > MAX_INDEX_BYTES:
        raise InstallError(f"{url}: larger than {MAX_INDEX_BYTES} bytes; refusing")
    return data


@dataclass
class RefreshOutcome:
    """Per-source records plus the merged totals; ``sources[]`` is what the audit row and ``--json`` carry."""

    sources: list[dict[str, Any]] = field(default_factory=list)
    merged_mods: int = 0
    merged_rows: int = 0
    usable: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)

    def decisions(self) -> list[dict[str, Any]]:
        """The compact per-source decision for the audit row: ``{label, url, status, usable, allowUnsigned}``."""
        return [
            {"label": r.get("label"), "url": r.get("url"), "status": (r.get("signature") or {}).get("status") or ("error" if r.get("error") else None), "usable": bool(r.get("usable")), "allowUnsigned": bool(r.get("allowUnsigned"))}
            for r in self.sources
        ]

    def to_dict(self) -> dict[str, Any]:
        return {"sources": list(self.sources), "mergedMods": self.merged_mods, "mergedCompatRows": self.merged_rows, "usable": list(self.usable), "refused": list(self.refused)}


def _write_meta(directory: Path, record: dict[str, Any]) -> None:
    (directory / "meta.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def refresh(home: DataHome, store: SourceStore, *, opener: Callable[..., Any] | None = None, only: str | None = None) -> RefreshOutcome:
    """Fetch every source (or ``only`` one) into ``cache/sources/<key>/`` and rebuild the merged caches from the usable ones."""
    outcome = RefreshOutcome()
    home.ensure()
    for source in store.sources:
        if only and source.key != only and source.url != only and source.name != only:
            continue
        directory = home.cache_sources / source.key
        directory.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {"url": source.url, "key": source.key, "label": source.label, "fetchedAt": _now(), "trust": source.trust, "allowUnsigned": source.allow_unsigned, "keyId": source.key_id}
        try:
            index_bytes = _fetch(source.base + "index.json", opener=opener)
            signature = _fetch(source.base + "index.json.sig", opener=opener, optional=True)
            compat_bytes = _fetch(source.base + "compat.json", opener=opener, optional=True)
            compat_signature = _fetch(source.base + "compat.json.sig", opener=opener, optional=True) if compat_bytes is not None else None
        except InstallError as exc:
            record.update({"error": str(exc), "usable": False})
            outcome.sources.append(record)
            outcome.refused.append(source.key)
            _write_meta(directory, record)
            continue
        try:
            document = json.loads(index_bytes.decode("utf-8"))
            if not isinstance(document, dict) or not isinstance(document.get("mods"), list):
                raise ValueError("index.json has no mods[]")
        except ValueError as exc:
            record.update({"error": f"index.json invalid: {exc}", "usable": False})
            outcome.sources.append(record)
            outcome.refused.append(source.key)
            _write_meta(directory, record)
            continue
        verdict = VERIFIER(index_bytes, signature, source)
        usable = is_usable(verdict.status, source)
        record.update({"signature": verdict.to_dict(), "usable": usable, "mods": len(document["mods"]), "compat": compat_bytes is not None, "declaredSource": document.get("source") if isinstance(document.get("source"), str) else None})
        if not usable:
            record["refusal"] = refusal_text(verdict.status, source)
        elif not verdict.verified:
            record["warning"] = (
                f"the signature of {source.url} is INVALID; the index is used only because you set allowUnsigned — treat every file it lists as untrusted"
                if verdict.status == STATUS_INVALID
                else f"{source.url} is used without a verified signature ({verdict.status}) on your explicit allowUnsigned"
            )
        if compat_bytes is not None and compat_signature is not None:
            record["compatSignature"] = VERIFIER(compat_bytes, compat_signature, source).to_dict()
        (directory / "index.json").write_bytes(index_bytes)
        if signature is not None:
            (directory / "index.json.sig").write_bytes(signature)
        else:
            (directory / "index.json.sig").unlink(missing_ok=True)
        if compat_bytes is not None:
            (directory / "compat.json").write_bytes(compat_bytes)
            if compat_signature is not None:
                (directory / "compat.json.sig").write_bytes(compat_signature)
        else:
            (directory / "compat.json").unlink(missing_ok=True)
        _write_meta(directory, record)
        outcome.sources.append(record)
        (outcome.usable if usable else outcome.refused).append(source.key)
    outcome.merged_mods, outcome.merged_rows = rebuild_merged(home, store)
    return outcome


def rebuild_merged(home: DataHome, store: SourceStore) -> tuple[int, int]:
    """``cache/index.json`` and ``cache/compat.json`` from the usable per-source caches.

    Usability is recomputed from the recorded signature verdict and the source's
    *current* ``allowUnsigned`` (:func:`is_usable`), so ``registry add`` /
    ``remove`` can rebuild without a refetch. Each usable source contributes one
    document under its configured label; :class:`floofy_core.registry.IndexCache`
    namespaces colliding ids as ``<label>/<id>`` when it reads the merged file.
    """
    documents: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    mods = 0
    for source in store.sources:
        directory = home.cache_sources / source.key
        try:
            meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
            index = json.loads((directory / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        status = (meta.get("signature") or {}).get("status") if isinstance(meta, dict) else None
        if not is_usable(status, source) or not isinstance(index, dict) or not isinstance(index.get("mods"), list):
            continue
        documents.append({"source": source.label, "key": source.key, "url": source.url, "trust": source.trust, "signature": status, "document": index})
        mods += len(index["mods"])
        try:
            compat = json.loads((directory / "compat.json").read_text(encoding="utf-8"))
            for row in (compat.get("rows") if isinstance(compat, dict) else None) or []:
                if isinstance(row, dict):
                    rows.append({**row, "source": source.label})
        except (OSError, ValueError):
            pass
    home.cache.mkdir(parents=True, exist_ok=True)
    home.index_cache.write_text(json.dumps({"schema": 1, "merged": True, "generatedAt": _now(), "documents": documents}, indent=2) + "\n", encoding="utf-8")
    home.compat_cache.write_text(json.dumps({"schema": 1, "generatedAt": _now(), "rows": rows}, indent=2) + "\n", encoding="utf-8")
    return mods, len(rows)
