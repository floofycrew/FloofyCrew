"""A tiny loopback-only HTTP client for the running gateway (Requirement 5.7, 11.2).

The Patcher talks to the dashboard over the **unix socket** the host binds in its
data home — ``<KIROCREW_HOME>/dashboard-<port>.sock`` (``dashboard/urls.py``
``dashboard_socket_name``; ``5476`` is the default port, ``0`` in ``--test-mode``)
— or over **loopback TCP** when a port is known. Nothing else: FloofyCrew's own
traffic never leaves the machine (Requirement 11.6 applies to mods; this client
enforces the same rule on itself by construction).

``GET /api/health`` answers ``{"ok": true, "app": "kirocrew", "version": …}`` to a
direct-local caller with a served ``Host`` header (``handlers/core.py``
``_liveness_payload``), which is how :func:`served_version` learns which payload
the gateway is actually serving — the basis of the "dormant payload" verdict.
``GET /`` and ``/assets/*`` are served unauthenticated (the SPA cold-start path),
so the shell and chunk bytes can be compared to disk without a token.
"""
from __future__ import annotations

import http.client
import json
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_HOST_HOME",
    "GatewayEndpoint",
    "Response",
    "find_endpoints",
    "served_version",
]

#: The host's default data home; ``KIROCREW_HOME`` overrides it.
DEFAULT_HOST_HOME = Path.home() / ".kiro" / "crew"
_SOCKET_RE = re.compile(r"^dashboard-(\d+)\.sock$")


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
        request_headers = {"Accept-Encoding": "identity", "Host": "localhost"}
        if headers:
            request_headers.update(headers)
        connection = self._connection()
        try:
            connection.request("GET", path, headers=request_headers)
            response = connection.getresponse()
            body = response.read()
            return Response(response.status, {k.lower(): v for k, v in response.getheaders()}, body)
        finally:
            connection.close()

    def reachable(self) -> bool:
        try:
            return self.get("/api/health").status == 200
        except (OSError, http.client.HTTPException):
            return False


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


def served_version(endpoint: GatewayEndpoint) -> str | None:
    """The version the gateway reports on ``/api/health`` to a direct-local caller, or ``None``."""
    try:
        response = endpoint.get("/api/health")
        payload = response.json() if response.status == 200 else None
    except (OSError, http.client.HTTPException, ValueError):
        return None
    if isinstance(payload, dict) and isinstance(payload.get("version"), str):
        return payload["version"]
    return None
