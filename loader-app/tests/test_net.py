"""``ctx.http`` / ``floofy.fetch`` (Requirement 11.6): encrypted-only with the loopback exemption, declared hosts, audited denials."""
from __future__ import annotations

import http.server
import json
import sys
import threading
import types
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from floofy_core.netscan import is_loopback_host
from floofy_loader import api
from floofy_loader.consent import write_consent
from floofy_loader.net import DENIAL_CODES, FloofyHttp, FloofyNetworkDenied, FloofyNetworkError, NetworkPolicy, redact
from floofy_loader.paths import FloofyPaths
from floofy_loader.runtime import LoaderRuntime

from test_boot import make_mod


class LocalServer:
    """A loopback HTTP server: ``/ok``, ``/json``, ``/missing`` (404), ``/redirect-local`` → ``/ok``, ``/redirect-plain`` → ``http://example.com/``."""

    def __enter__(self) -> "LocalServer":
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_a):  # noqa: D401
                return None

            def do_GET(self):  # noqa: N802
                server.requests.append((self.command, self.path, self.headers.get("User-Agent", "")))
                if self.path == "/ok":
                    self._send(200, b"fine", "text/plain; charset=utf-8")
                elif self.path == "/json":
                    self._send(200, json.dumps({"hello": "world"}).encode(), "application/json")
                elif self.path == "/redirect-local":
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{server.port}/ok")
                    self.end_headers()
                elif self.path == "/redirect-plain":
                    self.send_response(302)
                    self.send_header("Location", "http://example.com/plain")
                    self.end_headers()
                elif self.path == "/redirect-undeclared":
                    self.send_response(302)
                    self.send_header("Location", "https://undeclared.example.net/x")
                    self.end_headers()
                else:
                    self._send(404, b"nope", "text/plain")

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                server.requests.append((self.command, self.path, body.decode()))
                self._send(201, body, self.headers.get("Content-Type", "application/octet-stream"))

            def _send(self, status, body, content_type):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.requests: list[tuple] = []
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()

    def url(self, path: str, host: str = "127.0.0.1") -> str:
        return f"http://{host}:{self.port}{path}"


@pytest.fixture
def server():
    with LocalServer() as local:
        yield local


def make_client(mod_id="testmod", hosts=("api.example.com", "*.example.org")):
    rows: list[tuple] = []
    client = FloofyHttp(mod_id, hosts, audit=lambda op, **fields: rows.append((op, fields)))
    return client, rows


# --- the policy --------------------------------------------------------------------------------


def test_policy_decisions():
    policy = NetworkPolicy("m", ("api.example.com", "*.example.org", "cdn.example.com:8443"))
    for allowed in ("http://localhost:8080/x", "http://127.0.0.1/", "http://[::1]:9/", "ws://localhost:1234/", "https://api.example.com/v1", "wss://api.example.com/ws", "https://a.b.example.org/", "https://cdn.example.com:8443/", "unix:///tmp/gateway.sock", "http://127.8.9.10/"):
        policy.check(allowed)
    with pytest.raises(FloofyNetworkDenied) as denied:
        policy.check("http://api.example.com/plain")
    assert denied.value.code == "PlaintextDenied"
    with pytest.raises(FloofyNetworkDenied) as denied:
        policy.check("https://elsewhere.example.net/")
    assert denied.value.code == "UndeclaredHost" and "elsewhere.example.net" in str(denied.value)
    with pytest.raises(FloofyNetworkDenied) as denied:
        policy.check("https://example.org/")
    assert denied.value.code == "UndeclaredHost", "a wildcard label needs at least one label"
    with pytest.raises(FloofyNetworkDenied) as denied:
        policy.check("https://cdn.example.com:9443/")
    assert denied.value.code == "UndeclaredHost", "a declared port is part of the host"
    for scheme_case in ("ftp://api.example.com/", "file:///etc/passwd", "data:text/plain,hi", "https:///nohost"):
        with pytest.raises(FloofyNetworkDenied) as denied:
            policy.check(scheme_case)
        assert denied.value.code == "UnsupportedScheme"
    assert policy.allows("https://api.example.com/") and not policy.allows("http://api.example.com/")


def test_redact_strips_secrets():
    assert redact("https://user:pw@api.example.com:8443/path?token=abc#frag") == "https://api.example.com:8443/path"


# --- requests ------------------------------------------------------------------------------------


def test_loopback_plaintext_is_allowed_and_works(server):
    client, rows = make_client()
    response = client.fetch(server.url("/ok"))
    assert response.status == 200 and response.ok and response.text() == "fine" and response.url.endswith("/ok")
    assert client.fetch(server.url("/json", host="localhost")).json() == {"hello": "world"}
    missing = client.get(server.url("/missing"))
    assert missing.status == 404 and not missing.ok and missing.body == b"nope"
    posted = client.post(server.url("/ok"), json_body={"a": 1})
    assert posted.status == 201 and posted.json() == {"a": 1} and server.requests[-1][2] == '{"a": 1}'
    assert all(ua.startswith("FloofyCrew-mod") for cmd, path, ua in server.requests if cmd == "GET")
    assert rows == [] and client.denials == []


def test_denials_raise_and_are_audited(server):
    client, rows = make_client()
    with pytest.raises(FloofyNetworkDenied) as denied:
        client.fetch("http://example.com/")
    assert denied.value.code == "PlaintextDenied" and denied.value.mod == "testmod"
    with pytest.raises(FloofyNetworkDenied) as denied:
        client.fetch("https://undeclared.example.net/secret?token=1")
    assert denied.value.code == "UndeclaredHost"
    assert [op for op, _ in rows] == ["net-denied", "net-denied"]
    assert rows[1][1] == {"mod": "testmod", "code": "UndeclaredHost", "url": "https://undeclared.example.net/secret", "detail": rows[1][1]["detail"]}
    assert len(client.denials) == 2 and server.requests == [], "denied before any socket was opened"


def test_redirects_are_revalidated(server):
    client, rows = make_client()
    followed = client.fetch(server.url("/redirect-local"))
    assert followed.status == 200 and followed.text() == "fine" and followed.url.endswith("/ok")
    with pytest.raises(FloofyNetworkDenied) as denied:
        client.fetch(server.url("/redirect-plain"))
    assert denied.value.code == "PlaintextDenied" and denied.value.url == "http://example.com/plain"
    with pytest.raises(FloofyNetworkDenied) as denied:
        client.fetch(server.url("/redirect-undeclared"))
    assert denied.value.code == "UndeclaredHost"
    assert [(op, f["code"]) for op, f in rows] == [("net-denied", "PlaintextDenied"), ("net-denied", "UndeclaredHost")]
    assert [path for _, path, _ in server.requests] == ["/redirect-local", "/ok", "/redirect-plain", "/redirect-undeclared"], "the plaintext/undeclared targets were never fetched"


def test_websocket_and_unix_pass_the_rule_but_are_not_served():
    client, rows = make_client()
    with pytest.raises(NotImplementedError):
        client.fetch("wss://api.example.com/ws")
    with pytest.raises(NotImplementedError):
        client.fetch("unix:///tmp/x.sock")
    with pytest.raises(FloofyNetworkDenied):
        client.fetch("ws://api.example.com/ws")
    assert len(rows) == 1


def test_transport_failure_is_a_network_error():
    client, _ = make_client()
    with pytest.raises(FloofyNetworkError):
        client.fetch("http://127.0.0.1:1/unreachable", timeout=2)


def test_from_manifest_and_verification_stays_on():
    client = FloofyHttp.from_manifest("m", {"network": {"hosts": ["API.Example.com", 7, " *.example.org "]}})
    assert client.declared_hosts == ("api.example.com", "*.example.org")
    context = client._context()
    assert context.verify_mode.name == "CERT_REQUIRED" and context.check_hostname is True
    assert FloofyHttp.from_manifest("m", {}).declared_hosts == ()


@settings(max_examples=80, deadline=None)
@given(
    scheme=st.sampled_from(["http", "https", "ws", "wss"]),
    labels=st.lists(st.from_regex(r"[a-z][a-z0-9-]{0,6}", fullmatch=True), min_size=1, max_size=4),
    declared=st.lists(st.from_regex(r"(\*|[a-z][a-z0-9-]{0,6})(\.[a-z][a-z0-9-]{0,6}){0,3}", fullmatch=True), max_size=3),
    loopback=st.sampled_from([None, "localhost", "127.0.0.1", "127.255.0.9", "[::1]"]),
)
def test_policy_property(scheme, labels, declared, loopback):
    host = loopback or ".".join(labels)
    url = f"{scheme}://{host}/p"
    policy = NetworkPolicy("m", tuple(declared))
    try:
        policy.check(url)
        allowed = True
    except FloofyNetworkDenied as denied:
        allowed = False
        assert denied.code in DENIAL_CODES
    plain = scheme in ("http", "ws")
    if loopback is not None:
        assert allowed, "loopback is always allowed"
    elif plain:
        assert not allowed, "plaintext to a non-loopback host is always denied"
    elif allowed:
        assert any(True for _ in declared), "an encrypted request needs a declared host to be allowed"
    assert is_loopback_host(host.strip("[]")) == (loopback is not None)


# --- runtime wiring: ctx.http and floofy.fetch inside a booted Loader ----------------------------------


NET_MOD = '''
import floofy

def activate(ctx):
    ctx.state["declared"] = list(ctx.http.declared_hosts)
    try:
        ctx.http.fetch("http://example.com/")
    except Exception as exc:
        ctx.state["denied"] = type(exc).__name__
    try:
        floofy.fetch("https://undeclared.example.net/")
    except Exception as exc:
        ctx.state["facade_denied"] = type(exc).__name__ + ":" + getattr(exc, "mod", "?")
    ctx.state["allows_local"] = ctx.http.policy.allows("http://127.0.0.1:1234/")
'''


def test_runtime_gives_each_mod_its_declared_hosts_and_audits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path / "home"))
    monkeypatch.setitem(sys.modules, "kiro_crew", None)
    paths = FloofyPaths(tmp_path / "home" / "floofy").ensure()
    write_consent(paths.consent, by="tests")
    make_mod(paths, "netmod", code=NET_MOD, extra={"kirocrew": {"version": "*"}, "network": {"hosts": ["api.example.com"], "credentials": False}})
    paths.enabled.write_text(json.dumps({"netmod": True}), encoding="utf-8")
    runtime = LoaderRuntime()
    previous_fetch = api.fetch
    try:
        runtime.startup(types.SimpleNamespace(name="floofycrew", data_dir=tmp_path, logger=None))
        exports = runtime.state_dict()["mods"]["netmod"]["exports"]
        assert exports == {"declared": ["api.example.com"], "denied": "FloofyNetworkDenied", "facade_denied": "FloofyNetworkDenied:netmod", "allows_local": True}
        rows = [json.loads(line) for line in paths.audit.read_text(encoding="utf-8").splitlines()]
        assert [(r["op"], r["mod"], r["code"]) for r in rows] == [("net-denied", "netmod", "PlaintextDenied"), ("net-denied", "netmod", "UndeclaredHost")]
        assert runtime.state_dict()["network"]["netmod"] == {"mod": "netmod", "declaredHosts": ["api.example.com"], "loopbackPlaintext": True, "encryptedOnly": True, "denials": 2}
        with pytest.raises(FloofyNetworkDenied) as denied:
            api.fetch("https://api.example.com/")  # not a mod: the strictest policy, attributed to this module
        assert denied.value.code == "UndeclaredHost" and denied.value.mod.startswith("unknown:")
    finally:
        runtime.shutdown(None)
        api.bind(fetch_impl=previous_fetch)
