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

Signature verification is :func:`verify_index` (the :data:`VERIFIER` hook): the
detached ``index.json.sig`` is an Ed25519 signature over the canonical JSON of the
index (:mod:`floofy_core.sigverify`), checked against the keys the edition
adapters pin (:func:`floofy_core.editions.registry_keys`) plus the key the user
pinned for that source (``floofy registry add --public-key``); ``--key-id``
restricts a source to one key. Verdicts: ``verified`` (usable), ``unsigned`` (no
``.sig``) and ``invalid`` (tampered, wrong or unknown key, unreadable) — the
last two are usable only under the user's explicit ``allowUnsigned``, which is
exactly the default-safe rule (design "Testing Strategy → Registry": tampered
index refused by default, admitted with override + audit). A verified index
whose ``generatedAt`` is older than the one already cached from the same source
is still verified but flagged (a replayed old index cannot hide newer versions
unnoticed).

Source URLs name a directory (``…/registry/``) or carry a ``{file}`` placeholder
(``…/blobs/mainline/--/{file}?raw=1``) for hosts whose raw view needs a query
string; :meth:`Source.file_url` fills in ``index.json`` and friends. An edition
adapter may supply a ``urllib`` opener per URL for its network identity
(:func:`floofy_core.editions.registry_opener`); the core itself is anonymous.
"""
from __future__ import annotations

import base64
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
from .sigverify import STATUS_INVALID, STATUS_UNSIGNED, STATUS_VERIFIED, SignatureError, VerifyResult, key_id, verify_detached

__all__ = [
    "KEY_PROVIDER",
    "MAX_INDEX_BYTES",
    "RefreshOutcome",
    "STATUS_INVALID",
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
    "trusted_keys_for",
    "verify_index",
]

TRUST_LEVELS = ("index", "owner")
MAX_INDEX_BYTES = 20 * 1024 * 1024

#: The verdicts :data:`VERIFIER` may return.
VERIFY_STATUSES = (STATUS_VERIFIED, STATUS_UNSIGNED, STATUS_INVALID)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def source_key(url: str) -> str:
    """A stable directory name for a source URL (host + path, then 8 hex of the URL's sha256)."""
    parsed = urllib.parse.urlsplit(url)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{parsed.hostname or 'source'}{parsed.path}").strip("_")[:60]
    return f"{stem}-{hashlib.sha256(url.encode('utf-8')).hexdigest()[:8]}"


FILE_PLACEHOLDER = "{file}"


def decode_public_key(text: str | None) -> bytes | None:
    """A pinned public key as the user gave it: base64 (44 chars) or 64 hex digits; ``None`` when absent."""
    if not text:
        return None
    candidate = text.strip()
    try:
        if len(candidate) == 64 and all(c in "0123456789abcdefABCDEF" for c in candidate):
            raw = bytes.fromhex(candidate)
        else:
            raw = base64.b64decode(candidate, validate=True)
    except ValueError as exc:
        raise InstallError(f"--public-key is neither base64 nor hex: {exc}") from exc
    if len(raw) != 32:
        raise InstallError(f"an Ed25519 public key is 32 bytes, got {len(raw)}")
    return raw


@dataclass
class Source:
    """One row of ``registries.json``."""

    url: str
    trust: str = "index"
    allow_unsigned: bool = False
    key_id: str | None = None
    name: str | None = None
    added_at: str = field(default_factory=_now)
    #: A public key the user pinned for this source (base64), verified alongside the edition's pinned keys.
    public_key: str | None = None
    #: The KiroCrew federated app-registry row the same source publishes (task 7.4): ``{"repo", "branch", "name"}``.
    host_registry: dict[str, Any] | None = None

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
        if FILE_PLACEHOLDER in self.url:
            return self.url.split(FILE_PLACEHOLDER, 1)[0]
        if self.url.endswith("/index.json"):
            return self.url[: -len("index.json")]
        return self.url if self.url.endswith("/") else self.url + "/"

    def file_url(self, name: str) -> str:
        """The URL of one registry file: ``{file}`` substituted, else ``base + name``."""
        if FILE_PLACEHOLDER in self.url:
            return self.url.replace(FILE_PLACEHOLDER, name)
        return self.base + name

    @property
    def public_key_bytes(self) -> bytes | None:
        try:
            return decode_public_key(self.public_key)
        except InstallError:
            return None

    def to_record(self) -> dict[str, Any]:
        """The ``registries.json`` row (design "Consent, trust and audit")."""
        record: dict[str, Any] = {"url": self.url, "name": self.name, "trust": self.trust, "allowUnsigned": self.allow_unsigned, "keyId": self.key_id, "addedAt": self.added_at}
        if self.public_key:
            record["publicKey"] = self.public_key
        if self.host_registry:
            record["hostRegistry"] = dict(self.host_registry)
        return record

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
            raw.get("publicKey") if isinstance(raw.get("publicKey"), str) else None,
            dict(raw["hostRegistry"]) if isinstance(raw.get("hostRegistry"), dict) else None,
        )

    @classmethod
    def from_default(cls, row: dict[str, Any]) -> "Source":
        """A source from an edition adapter's ``default_sources()`` row (``repo``/``branch`` become the host-registry row)."""
        source = cls.from_dict(row)
        if source.host_registry is None and isinstance(row.get("repo"), str):
            source.host_registry = {"repo": row["repo"], "branch": str(row.get("branch") or "main"), "name": str(row.get("hostRegistryName") or row.get("name") or "floofycrew")}
        return source


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
        if source.public_key:
            pinned = decode_public_key(source.public_key)  # raises on a malformed key
            if source.key_id and pinned is not None and key_id(pinned) != source.key_id:
                raise InstallError(f"--key-id {source.key_id} does not match the given --public-key (its id is {key_id(pinned)})")
            if pinned is not None and not source.key_id:
                source.key_id = key_id(pinned)
        if source.name and any(s.name == source.name and s.url != source.url for s in self.sources):
            raise InstallError(f"a different source is already named {source.name!r}")
        existing = self.find(source.url)
        loosened = source.allow_unsigned and (existing is None or not existing.allow_unsigned)
        if existing is not None:
            existing.trust, existing.allow_unsigned = source.trust, source.allow_unsigned
            existing.key_id, existing.name = source.key_id or existing.key_id, source.name or existing.name
            existing.public_key = source.public_key or existing.public_key
            existing.host_registry = source.host_registry or existing.host_registry
            return existing, loosened
        self.sources.append(source)
        return source, loosened

    def remove(self, ref: str) -> Source | None:
        source = self.find(ref)
        if source is not None:
            self.sources.remove(source)
        return source


# --- verification hook --------------------------------------------------------------------------------


def _edition_keys() -> dict[str, bytes]:
    """The keys pinned by the installed edition adapters (empty under ``FLOOFY_NO_ADAPTERS=1``: tests pin their own)."""
    import os  # noqa: PLC0415

    if os.environ.get("FLOOFY_NO_ADAPTERS") == "1":
        return {}
    from .editions import registry_keys  # noqa: PLC0415

    return registry_keys()


def trusted_keys_for(source: Source) -> dict[str, bytes]:
    """key id → public key a signature of ``source`` may be checked against: the edition pins plus the source's own pinned key."""
    keys = dict(KEY_PROVIDER())
    pinned = source.public_key_bytes
    if pinned is not None:
        keys[key_id(pinned)] = pinned
    return keys


#: Replaceable (tests, embedding): ``() -> {key id: public key}`` for the edition-pinned keys.
KEY_PROVIDER: Callable[[], dict[str, bytes]] = _edition_keys


def verify_index(index_bytes: bytes, signature: bytes | None, source: Source) -> VerifyResult:
    """The detached-signature check (Requirement 8.3, 11.6).

    ``signature`` is the raw ``index.json.sig`` document (``None`` when the source
    publishes none → ``unsigned``). The Ed25519 signature must verify over the
    canonical JSON of ``index_bytes`` with a key from :func:`trusted_keys_for`;
    ``source.key_id`` pins the source to one key (a signature by another key is
    ``invalid`` even when that key is trusted). The caller decides usability from
    the status alone (:func:`is_usable`), so this never looks at ``allowUnsigned``.
    """
    try:
        keys = trusted_keys_for(source)
    except SignatureError as exc:
        return VerifyResult(STATUS_INVALID, f"the pinned keys cannot be read: {exc}")
    return verify_detached(index_bytes, signature, keys, expected_key_id=source.key_id)


#: Replaceable by tests and embedders: ``(index_bytes, signature_or_None, source) -> VerifyResult``.
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
    open_url = opener.open if opener is not None and hasattr(opener, "open") else (opener or urllib.request.urlopen)
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


def refresh(home: DataHome, store: SourceStore, *, opener: Callable[..., Any] | None = None, only: str | None = None, opener_for: Callable[[str], Any] | None = None) -> RefreshOutcome:
    """Fetch every source (or ``only`` one) into ``cache/sources/<key>/`` and rebuild the merged caches from the usable ones.

    ``opener`` is used for every URL; ``opener_for(url)`` (an edition adapter's
    network identity, see :func:`floofy_core.editions.registry_opener`) takes
    precedence per URL when it returns one.
    """
    outcome = RefreshOutcome()
    home.ensure()
    for source in store.sources:
        if only and source.key != only and source.url != only and source.name != only:
            continue
        directory = home.cache_sources / source.key
        directory.mkdir(parents=True, exist_ok=True)
        previous_meta = _read_meta(directory)
        record: dict[str, Any] = {"url": source.url, "key": source.key, "label": source.label, "fetchedAt": _now(), "trust": source.trust, "allowUnsigned": source.allow_unsigned, "keyId": source.key_id, "pinnedPublicKey": bool(source.public_key)}

        def fetch(name: str, *, optional: bool = False) -> bytes | None:
            url = source.file_url(name)
            chosen = (opener_for(url) if opener_for is not None else None) or opener
            return _fetch(url, opener=chosen, optional=optional)

        try:
            index_bytes = fetch("index.json")
            signature = fetch("index.json.sig", optional=True)
            compat_bytes = fetch("compat.json", optional=True)
            compat_signature = fetch("compat.json.sig", optional=True) if compat_bytes is not None else None
            app_registry = fetch("app-registry.json", optional=True)
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
        generated_at = document.get("generatedAt") if isinstance(document.get("generatedAt"), str) else None
        record.update({"signature": verdict.to_dict(), "usable": usable, "mods": len(document["mods"]), "compat": compat_bytes is not None, "declaredSource": document.get("source") if isinstance(document.get("source"), str) else None, "generatedAt": generated_at, "appRegistry": app_registry is not None, "hostRegistry": dict(source.host_registry) if source.host_registry else None})
        if not usable:
            record["refusal"] = refusal_text(verdict.status, source)
        elif not verdict.verified:
            record["warning"] = (
                f"the signature of {source.url} is INVALID ({verdict.detail}); the index is used only because you set allowUnsigned — treat every file it lists as untrusted"
                if verdict.status == STATUS_INVALID
                else f"{source.url} is used without a verified signature ({verdict.status}) on your explicit allowUnsigned"
            )
        previous_generated = previous_meta.get("generatedAt") if isinstance(previous_meta.get("generatedAt"), str) else None
        if usable and generated_at and previous_generated and generated_at < previous_generated and previous_meta.get("usable"):
            record["rollback"] = f"the fetched index was generated {generated_at}, older than the cached one ({previous_generated}); a stale or replayed index"
            record["warning"] = (record.get("warning") + "; " if record.get("warning") else "") + record["rollback"]
        if compat_bytes is not None:
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


def _read_meta(directory: Path) -> dict[str, Any]:
    try:
        meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def rebuild_merged(home: DataHome, store: SourceStore) -> tuple[int, int]:
    """``cache/index.json`` and ``cache/compat.json`` from the usable per-source caches.

    Usability is recomputed from the recorded signature verdict and the source's
    *current* ``allowUnsigned`` (:func:`is_usable`), so ``registry add`` /
    ``remove`` can rebuild without a refetch. Each usable source contributes one
    document under its configured label; :class:`floofy_core.registry.IndexCache`
    namespaces colliding ids as ``<label>/<id>`` when it reads the merged file.
    A source's ``compat.json`` rows are merged under the same rule applied to
    *its* signature (``compat.json.sig``): a verified index does not vouch for an
    unsigned or tampered matrix beside it.
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
        compat_status = (meta.get("compatSignature") or {}).get("status") if isinstance(meta, dict) else None
        if not is_usable(compat_status, source):
            continue
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
