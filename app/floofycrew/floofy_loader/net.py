"""``ctx.http`` / ``floofy.fetch`` — the sanctioned network client for mods (Requirement 11.6).

The rule, enforced before a socket is opened and again on every redirect:

a. every request uses an encrypted channel — ``https`` or ``wss`` (TLS) —
b. **except** to loopback: ``localhost``, ``127.0.0.0/8``, ``::1`` and ``unix:``
   sockets may be plaintext (``http``, ``ws``), so a mod can talk to the local
   gateway and other local services (:func:`floofy_core.netscan.is_loopback_host`);
c. a non-loopback host must be one the mod declared in ``floofy.json``
   ``network.hosts[]`` (wildcard labels per :func:`floofy_core.netscan.host_matches`);
d. a violation raises :class:`FloofyNetworkDenied` (``code`` ``PlaintextDenied``
   / ``UndeclaredHost`` / ``UnsupportedScheme``) **and** appends an
   ``op: net-denied`` row to ``<data home>/audit.jsonl`` (Requirement 11.5).

Allowed requests go through :mod:`urllib.request` with
``ssl.create_default_context()`` — certificate verification is never disabled
and there is no option to disable it. Redirects are re-validated by the same
policy (:class:`_PolicyRedirectHandler`), so an ``https`` request cannot be
bounced to plaintext or to an undeclared host. ``ws``/``wss`` URLs are validated
by the policy but a WebSocket client is not implemented in v1
(:meth:`FloofyHttp.request` raises :class:`NotImplementedError` after the check;
mods may open their own TLS WebSocket once the URL passed :meth:`FloofyHttp.check`).

FloofyCrew handles no host or user credentials (Requirement 11.6): a mod that
needs some asks the user itself and says so in its manifest.
"""
from __future__ import annotations

import http.client
import json
import logging
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from floofy_core.netscan import host_matches, is_loopback_host, split_authority

__all__ = ["DENIAL_CODES", "FloofyHttp", "FloofyNetworkDenied", "FloofyNetworkError", "NetworkPolicy", "Response"]

logger = logging.getLogger("floofy.loader.net")

ENCRYPTED_SCHEMES = frozenset({"https", "wss"})
PLAINTEXT_SCHEMES = frozenset({"http", "ws"})
LOOPBACK_SCHEMES = frozenset({"unix"})
DENIAL_CODES = ("PlaintextDenied", "UndeclaredHost", "UnsupportedScheme")

AuditFn = Callable[..., None]


class FloofyNetworkError(OSError):
    """The request was allowed but failed at the transport level (DNS, connect, TLS, timeout)."""

    def __init__(self, url: str, reason: str):
        super().__init__(f"{url}: {reason}")
        self.url = url
        self.reason = reason


class FloofyNetworkDenied(PermissionError):
    """The request violates the network rule; already audited when raised by :class:`FloofyHttp`."""

    def __init__(self, code: str, url: str, mod: str, detail: str):
        if code not in DENIAL_CODES:
            raise ValueError(code)
        super().__init__(f"{code}: {detail} (mod {mod}, url {redact(url)})")
        self.code = code
        self.url = url
        self.mod = mod
        self.detail = detail

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "url": redact(self.url), "mod": self.mod, "detail": self.detail}


def redact(url: str) -> str:
    """The URL without userinfo, query or fragment — safe for logs and the audit trail."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "<unparseable url>"
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urllib.parse.urlunsplit((parts.scheme, host, parts.path, "", ""))


@dataclass(frozen=True)
class NetworkPolicy:
    """The pure decision: which URLs a mod with ``declared_hosts`` may reach."""

    mod_id: str
    declared_hosts: tuple[str, ...] = ()

    def check(self, url: str) -> None:
        """Raise :class:`FloofyNetworkDenied` unless ``url`` is allowed (no I/O)."""
        try:
            parts = urllib.parse.urlsplit(url)
        except ValueError as exc:
            raise FloofyNetworkDenied("UnsupportedScheme", url, self.mod_id, f"unparseable URL: {exc}") from exc
        scheme = (parts.scheme or "").lower()
        if scheme in LOOPBACK_SCHEMES:
            return
        if scheme not in ENCRYPTED_SCHEMES and scheme not in PLAINTEXT_SCHEMES:
            raise FloofyNetworkDenied("UnsupportedScheme", url, self.mod_id, f"scheme {scheme or '<none>'!r} is not http(s) or ws(s)")
        host, port = split_authority(parts.netloc)
        if not host:
            raise FloofyNetworkDenied("UnsupportedScheme", url, self.mod_id, "URL has no host")
        if is_loopback_host(host):
            return
        if scheme in PLAINTEXT_SCHEMES:
            raise FloofyNetworkDenied("PlaintextDenied", url, self.mod_id, f"plaintext {scheme} to non-loopback host {host!r}; use {scheme}s")
        if not any(host_matches(host, pattern, port) for pattern in self.declared_hosts):
            declared = ", ".join(self.declared_hosts) or "<none>"
            raise FloofyNetworkDenied("UndeclaredHost", url, self.mod_id, f"host {host!r} is not declared in network.hosts[] ({declared})")

    def allows(self, url: str) -> bool:
        try:
            self.check(url)
        except FloofyNetworkDenied:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {"mod": self.mod_id, "declaredHosts": list(self.declared_hosts), "loopbackPlaintext": True, "encryptedOnly": True}


@dataclass
class Response:
    """A completed HTTP exchange (HTTP error statuses are returned, not raised)."""

    status: int
    headers: dict[str, str]
    body: bytes
    url: str
    reason: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def text(self, encoding: str | None = None) -> str:
        return self.body.decode(encoding or self._charset(), errors="replace")

    def json(self) -> Any:
        return json.loads(self.text())

    def _charset(self) -> str:
        content_type = self.headers.get("content-type", "")
        for part in content_type.split(";")[1:]:
            key, _, value = part.strip().partition("=")
            if key.lower() == "charset" and value:
                return value.strip('"')
        return "utf-8"

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "url": self.url, "headers": dict(self.headers), "bytes": len(self.body)}


class _PolicyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validate every redirect target against the mod's policy before following it."""

    def __init__(self, policy: NetworkPolicy, on_denied: Callable[[FloofyNetworkDenied], None]):
        super().__init__()
        self._policy = policy
        self._on_denied = on_denied

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401, PLR0913 - urllib signature
        try:
            self._policy.check(newurl)
        except FloofyNetworkDenied as denied:
            self._on_denied(denied)
            raise
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass
class FloofyHttp:
    """``ctx.http``: the policy plus a small ``urllib`` client; every denial is audited."""

    mod_id: str
    declared_hosts: tuple[str, ...] = ()
    audit: AuditFn | None = None
    default_timeout: float = 10.0
    user_agent: str = "FloofyCrew-mod (unofficial)"
    denials: list[dict[str, str]] = field(default_factory=list)
    _ssl_context: ssl.SSLContext | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.declared_hosts = tuple(str(h).strip().lower() for h in self.declared_hosts if str(h).strip())
        self.policy = NetworkPolicy(self.mod_id, self.declared_hosts)

    @classmethod
    def from_manifest(cls, mod_id: str, manifest: dict[str, Any], audit: AuditFn | None = None) -> "FloofyHttp":
        network = manifest.get("network") if isinstance(manifest.get("network"), dict) else {}
        hosts: Iterable[Any] = network.get("hosts") or []
        return cls(mod_id, tuple(str(h) for h in hosts if isinstance(h, str)), audit)

    # -- the rule -----------------------------------------------------------------------------------

    def check(self, url: str) -> None:
        """Apply the rule (Requirement 11.6 a–c); denials are audited and raised."""
        try:
            self.policy.check(url)
        except FloofyNetworkDenied as denied:
            self._record(denied)
            raise

    def _record(self, denied: FloofyNetworkDenied) -> None:
        row = denied.to_dict()
        self.denials.append(row)
        logger.warning("network denied for mod %s: %s %s", self.mod_id, denied.code, redact(denied.url))
        if self.audit is not None:
            try:
                self.audit("net-denied", mod=self.mod_id, code=denied.code, url=redact(denied.url), detail=denied.detail)
            except Exception:  # noqa: BLE001 - the audit trail must not turn a denial into a crash
                logger.exception("audit write failed for a network denial")

    # -- requests ------------------------------------------------------------------------------------

    def _context(self) -> ssl.SSLContext:
        if self._ssl_context is None:
            self._ssl_context = ssl.create_default_context()  # verification on; never relaxed
        return self._ssl_context

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | str | None = None,
        json_body: Any = None,
        timeout: float | None = None,
    ) -> Response:
        """Perform one HTTP request under the rule; HTTP error statuses come back as a :class:`Response`."""
        self.check(url)
        scheme = urllib.parse.urlsplit(url).scheme.lower()
        if scheme in ("ws", "wss"):
            raise NotImplementedError("WebSocket client is not part of FloofyCrew v1; the URL passed the network rule, open your own TLS socket")
        if scheme == "unix":
            raise NotImplementedError("unix: sockets are exempt from the rule but not served by ctx.http in v1; use http.client over a unix socket")
        data: bytes | None
        request_headers = {"User-Agent": self.user_agent, **(headers or {})}
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        elif isinstance(body, str):
            data = body.encode("utf-8")
        else:
            data = body
        req = urllib.request.Request(url, data=data, headers=request_headers, method=method.upper())
        opener = urllib.request.build_opener(_PolicyRedirectHandler(self.policy, self._record), urllib.request.HTTPSHandler(context=self._context()))
        try:
            with opener.open(req, timeout=self.default_timeout if timeout is None else timeout) as response:
                return Response(response.status, {k.lower(): v for k, v in response.headers.items()}, response.read(), response.geturl(), getattr(response, "reason", ""))
        except urllib.error.HTTPError as error:
            payload = error.read() if hasattr(error, "read") else b""
            return Response(error.code, {k.lower(): v for k, v in (error.headers.items() if error.headers else [])}, payload, error.geturl() or url, str(error.reason))
        except FloofyNetworkDenied:
            raise
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", None) or exc
            raise FloofyNetworkError(redact(url), str(reason)) from exc

    def fetch(self, url: str, **kwargs: Any) -> Response:
        """``GET`` by default (``method=`` to override) — the ``floofy.fetch`` shape."""
        method = kwargs.pop("method", "GET")
        return self.request(method, url, **kwargs)

    def get(self, url: str, **kwargs: Any) -> Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Response:
        return self.request("POST", url, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {**self.policy.to_dict(), "denials": len(self.denials)}
