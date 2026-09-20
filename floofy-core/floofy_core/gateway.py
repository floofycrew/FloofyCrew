"""A tiny loopback-only HTTP client for the running gateway (Requirement 5.7, 11.2).

The Patcher talks to the dashboard over the **unix socket** the host binds in its
data home — ``<KIROCREW_HOME>/dashboard-<port>.sock`` (``dashboard/urls.py``
``dashboard_socket_name``; ``5476`` is the default port, ``0`` in ``--test-mode``)
— or over **loopback TCP** when a port is known. Nothing else: FloofyCrew's own
traffic never leaves the machine (Requirement 11.6 applies to mods; this client
enforces the same rule on itself by construction).

``GET /api/health`` answers ``{"ok": true}`` to everyone and adds ``"app":
"kirocrew", "version": …`` only for a **direct-local** caller with a served
``Host`` header (``handlers/core.py`` ``_liveness_payload``: ``origin.py``
``is_direct_local_request`` = a loopback TCP peer and none of ``Forwarded`` /
``X-Forwarded-*`` / ``X-Real-IP``, plus ``check_host`` = the ``Host`` hostname is
one the dashboard serves). A **unix-socket peer has no remote address**, so over
the socket the answer is always the bare ``{"ok": true}`` — owner-verified on a
live 0.7.0.5 install (task 10.8). :func:`probe_version` therefore asks over the
socket first and, when the answer carries no ``version``, retries once over
``127.0.0.1:<port>`` with ``Host: 127.0.0.1:<port>`` — the port read from the
socket's own name ``dashboard-<port>.sock`` (``dashboard/urls.py``
``dashboard_socket_name``). A socket whose name yields no usable port
(``dashboard-0.sock`` in ``--test-mode``, an unrecognised name) stays a bare
answer: reachable, version unknown. Every other call (the shell, the chunks, the
Loader routes) keeps using the socket. The served version is the basis of the
"dormant payload" verdict (Requirement 5.7, 6.4). ``GET /`` and ``/assets/*``
are served unauthenticated (the SPA cold-start path), so the shell and chunk
bytes can be compared to disk without a token.
"""
from __future__ import annotations

import http.client
import json
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_HOST_HOME",
    "GatewayAuthError",
    "GatewayEndpoint",
    "GatewaySession",
    "Response",
    "VersionProbe",
    "find_endpoints",
    "port_from_socket_name",
    "probe_version",
    "read_local_secret",
    "served_version",
    "session_for",
]

#: The host's default data home; ``KIROCREW_HOME`` overrides it.
DEFAULT_HOST_HOME = Path.home() / ".kiro" / "crew"
_SOCKET_RE = re.compile(r"^dashboard-(\d+)\.sock$")
#: How :func:`probe_version` learned the version.
VIA_SOCKET = "socket"
VIA_LOOPBACK_TCP = "loopback-tcp"


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8", "replace"))

    @property
    def encoding(self) -> str:
        return self.headers.get("content-encoding", "identity")


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:  # noqa: D401 - http.client hook
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._socket_path)
        self.sock = sock


@dataclass(frozen=True)
class GatewayEndpoint:
    """Where a gateway listens: a unix socket path, or a loopback port."""

    socket_path: Path | None = None
    port: int | None = None
    host: str = "127.0.0.1"
    timeout: float = 10.0

    @property
    def label(self) -> str:
        return str(self.socket_path) if self.socket_path else f"http://{self.host}:{self.port}"

    def _connection(self) -> http.client.HTTPConnection:
        if self.socket_path is not None:
            return _UnixHTTPConnection(str(self.socket_path), self.timeout)
        if self.port is None:
            raise ValueError("endpoint needs a socket path or a port")
        return http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)

    def get(self, path: str, headers: dict[str, str] | None = None) -> Response:
        """One GET; ``Accept-Encoding: identity`` by default so bytes compare to disk."""
        return self.request("GET", path, headers=headers)

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None) -> Response:
        """One request of any method (the CLI's ``POST`` calls go through here too)."""
        request_headers = {"Accept-Encoding": "identity", "Host": "localhost"}
        if headers:
            request_headers.update(headers)
        if body is not None and "Content-Type" not in request_headers:
            request_headers["Content-Type"] = "application/json"
        connection = self._connection()
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            payload = response.read()
            headers: dict[str, str] = {}
            for key, value in response.getheaders():
                name = key.lower()
                # repeated headers (two Set-Cookie lines: the session and the refresh cookie) are joined, never dropped
                headers[name] = f"{headers[name]}, {value}" if name in headers else value
            return Response(response.status, headers, payload)
        finally:
            connection.close()

    def reachable(self) -> bool:
        try:
            return self.get("/api/health").status == 200
        except (OSError, http.client.HTTPException):
            return False


def port_from_socket_name(socket_path: Path | str | None) -> int | None:
    """The loopback port a dashboard socket's name carries (``dashboard-<port>.sock``), or ``None``.

    ``0`` (a ``--test-mode`` gateway bound to an ephemeral port, whose socket is
    still ``dashboard-0.sock``) and an unrecognised name both yield ``None``:
    nothing to retry against.
    """
    if socket_path is None:
        return None
    match = _SOCKET_RE.match(Path(socket_path).name)
    if not match:
        return None
    port = int(match.group(1))
    return port or None


def find_endpoints(host_home: Path | None = None) -> list[GatewayEndpoint]:
    """Every ``dashboard-<port>.sock`` in the host's data home (default ``~/.kiro/crew``)."""
    home = Path(host_home) if host_home else DEFAULT_HOST_HOME
    endpoints: list[GatewayEndpoint] = []
    try:
        entries = sorted(home.iterdir())
    except OSError:
        return endpoints
    for entry in entries:
        match = _SOCKET_RE.match(entry.name)
        if match and entry.is_socket():
            endpoints.append(GatewayEndpoint(socket_path=entry, port=int(match.group(1))))
    return endpoints


@dataclass(frozen=True)
class VersionProbe:
    """What ``/api/health`` said about the gateway behind an endpoint, and how it was learned.

    ``reachable`` — the endpoint answered ``200`` (the gateway runs);
    ``version`` — the served version, or ``None`` when the host withheld it;
    ``via`` — :data:`VIA_SOCKET` / :data:`VIA_LOOPBACK_TCP` when a version was
    learned, else ``None``; ``detail`` — why there is no version, for ``doctor``.
    """

    version: str | None
    via: str | None
    reachable: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "via": self.via, "reachable": self.reachable, "detail": self.detail}


def _health(endpoint: GatewayEndpoint, headers: dict[str, str] | None = None) -> tuple[bool, dict[str, Any] | None, str]:
    """``(answered 200, JSON object or None, detail)`` for one ``GET /api/health``."""
    try:
        response = endpoint.get("/api/health", headers=headers)
    except (OSError, http.client.HTTPException) as exc:
        return False, None, f"{endpoint.label}: {exc}"
    if response.status != 200:
        return False, None, f"{endpoint.label}: /api/health answered HTTP {response.status}"
    try:
        payload = response.json()
    except ValueError:
        return True, None, f"{endpoint.label}: /api/health is not JSON"
    return True, payload if isinstance(payload, dict) else None, ""


def _version_in(payload: dict[str, Any] | None) -> str | None:
    version = payload.get("version") if isinstance(payload, dict) else None
    return version if isinstance(version, str) and version else None


def probe_version(endpoint: GatewayEndpoint) -> VersionProbe:
    """Ask the gateway behind ``endpoint`` which version it serves (see the module docstring for the tree).

    1. ``GET /api/health`` over the endpoint as given. Unreachable → not running.
    2. The answer names a ``version`` → done (``via: socket`` or ``loopback-tcp``).
    3. A bare answer over a **unix socket** whose name carries a port → retry once
       over ``127.0.0.1:<port>`` with ``Host: 127.0.0.1:<port>`` (the direct-local
       shape the host discloses its identity to). A version there → ``via:
       loopback-tcp``; the socket stays the endpoint for every other call.
    4. Anything else (a bare TCP answer — a forwarding proxy or a foreign ``Host``
       in between; a socket with no usable port; a failed retry) → reachable,
       version unknown, with the reason in ``detail``.
    """
    reachable, payload, detail = _health(endpoint, headers=None if endpoint.socket_path is not None else {"Host": f"{endpoint.host}:{endpoint.port}"})
    if not reachable:
        return VersionProbe(None, None, False, detail)
    version = _version_in(payload)
    if version is not None:
        return VersionProbe(version, VIA_SOCKET if endpoint.socket_path is not None else VIA_LOOPBACK_TCP, True)
    if endpoint.socket_path is None:
        return VersionProbe(None, None, True, f"{endpoint.label}: the host withheld its identity on /api/health (not a direct-local request: a forwarding header or an unserved Host in between?)")
    port = endpoint.port or port_from_socket_name(endpoint.socket_path)
    if not port:
        return VersionProbe(None, None, True, f"{endpoint.label}: a unix-socket peer has no remote address, so the host withholds its version there, and the socket name carries no loopback port to retry on")
    retry = GatewayEndpoint(port=port, host="127.0.0.1", timeout=endpoint.timeout)
    tcp_reachable, tcp_payload, tcp_detail = _health(retry, headers={"Host": f"127.0.0.1:{port}"})
    version = _version_in(tcp_payload)
    if version is not None:
        return VersionProbe(version, VIA_LOOPBACK_TCP, True)
    if not tcp_reachable:
        return VersionProbe(None, None, True, f"{endpoint.label}: bare answer over the socket and the loopback retry failed ({tcp_detail})")
    return VersionProbe(None, None, True, f"{endpoint.label}: bare answer over the socket and over {retry.label} (the host withheld its identity)")


def served_version(endpoint: GatewayEndpoint) -> str | None:
    """The version the gateway behind ``endpoint`` serves, or ``None`` (:func:`probe_version` for the reason)."""
    return probe_version(endpoint).version


# --- an authenticated session for the manager -----------------------------------------------


def read_local_secret(host_home: Path, port: int | None) -> str:
    """The gateway's internal-API credential, the way the host CLI reads it.

    ``kiro_crew/config/loader.py`` L1158 ``read_local_secret``: the per-listener
    ``run/gateway-<port>.secret`` first (``instances/run_marker.py`` L251
    ``read_secret``), then the shared ``.local_secret`` written at gateway
    startup. Both are ``0600`` files in the host's data home, so only processes
    of the same user can read them. Empty string when neither exists.
    """
    home = Path(host_home)
    candidates: list[Path] = []
    if port is not None:
        candidates.append(home / "run" / f"gateway-{port}.secret")
    candidates.append(home / ".local_secret")
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return ""


class GatewayAuthError(RuntimeError):
    """The manager could not obtain a dashboard session for its authenticated calls."""


@dataclass
class GatewaySession:
    """Loopback calls to routes behind the dashboard auth (the Loader's routes, the theme route).

    The host mints a session for local processes on ``GET /api/token/local``
    (``dashboard/handlers/core.py`` L2426 ``api_token_local``): loopback only,
    ``X-Local-Secret`` header carrying :func:`read_local_secret`'s value. The
    token then rides as ``?token=`` on the first request (the host exchanges it
    for the ``mc_token_<port>`` cookie, ``dashboard/token_auth.py``) and the
    cookie is sent afterwards. A ``token`` may also be supplied directly (the
    one-time link token a ``--test-mode`` gateway prints on ``KIROCREW_READY``).
    Nothing here ever leaves the machine.
    """

    host_home: Path
    port: int
    token: str | None = None
    host: str = "127.0.0.1"
    timeout: float = 20.0
    ttl: str = "10m"
    _cookies: dict[str, tuple[str, str]] = field(default_factory=dict)  # name -> (value, path)

    @property
    def endpoint(self) -> GatewayEndpoint:
        return GatewayEndpoint(port=self.port, host=self.host, timeout=self.timeout)

    @property
    def label(self) -> str:
        return f"http://{self.host}:{self.port}"

    def mint(self) -> str:
        """Obtain a session token with the local secret (idempotent once a token is held)."""
        if self.token:
            return self.token
        secret = read_local_secret(self.host_home, self.port)
        if not secret:
            raise GatewayAuthError(f"no local secret under {self.host_home} (run/gateway-{self.port}.secret or .local_secret): is this the gateway's data home?")
        try:
            response = self.endpoint.request("GET", f"/api/token/local?ttl={self.ttl}", headers={"X-Local-Secret": secret})
        except (OSError, http.client.HTTPException) as exc:
            raise GatewayAuthError(f"{self.label}/api/token/local unreachable: {exc}") from exc
        if response.status != 200:
            raise GatewayAuthError(f"{self.label}/api/token/local answered {response.status}: {response.body[:200].decode('utf-8', 'replace')}")
        try:
            token = response.json().get("token")
        except ValueError:
            token = None
        if not isinstance(token, str) or not token:
            raise GatewayAuthError(f"{self.label}/api/token/local returned no token")
        self.token = token
        return token

    def request(self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None) -> Response:
        """An authenticated call; ``body`` (any JSON value) is encoded as ``application/json``."""
        token = self.mint()
        request_headers = dict(headers or {})
        route = path.split("?", 1)[0]
        applicable = {name: value for name, (value, cookie_path) in self._cookies.items() if route.startswith(cookie_path)}
        if applicable:
            request_headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in applicable.items())
        else:
            path += ("&" if "?" in path else "?") + "token=" + token
        data = json.dumps(body).encode("utf-8") if body is not None else None
        response = self.endpoint.request(method, path, body=data, headers=request_headers)
        self._remember_cookies(response)
        return response

    def _remember_cookies(self, response: Response) -> None:
        """Keep the cookies the exchange set (``mc_token_<port>`` on ``/``; a deletion is ``Max-Age=0``)."""
        raw = response.headers.get("set-cookie")
        if not raw:
            return
        for cookie in re.split(r",(?=\s*[A-Za-z0-9_]+=)", raw):
            parts = [p.strip() for p in cookie.split(";")]
            if "=" not in parts[0]:
                continue
            name, value = parts[0].split("=", 1)
            attributes = {k.strip().lower(): v.strip() for k, _, v in (a.partition("=") for a in parts[1:])}
            value = value.strip().strip('"')
            if attributes.get("max-age") == "0" or not value:
                self._cookies.pop(name.strip(), None)
                continue
            self._cookies[name.strip()] = (value, attributes.get("path") or "/")

    def get(self, path: str, **kw: Any) -> Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, body: Any = None, **kw: Any) -> Response:
        return self.request("POST", path, body, **kw)


def session_for(host_home: Path, *, port: int | None = None, token: str | None = None) -> GatewaySession | None:
    """A session for the gateway serving ``host_home`` — the socket names the port — or ``None`` when none runs."""
    if port is None:
        for endpoint in find_endpoints(host_home):
            if endpoint.port and endpoint.reachable():
                port = endpoint.port
                break
    if port is None:
        return None
    return GatewaySession(Path(host_home), port, token=token)
