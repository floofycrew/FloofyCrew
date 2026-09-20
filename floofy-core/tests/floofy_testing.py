"""Shared test helpers for floofy-core and the edition adapters (plain module, on the pytest pythonpath).

Fake payloads, a stand-in gateway for the verify protocol, and the live-install guard that every
conftest registers so no test can touch a real host install.
"""
from __future__ import annotations

import http.server
import json
import os
import shutil
import socket
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

import hash_example_files  # from scripts/, on the pytest pythonpath

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "floofy-core" / "examples"
KINDS = ("theme", "agent", "skill", "appearance", "config", "app", "python-hook", "spa", "patch")

# SAFETY: the test session must never reach a live host install. The CLI honours this
# environment guard by searching only the roots a test names explicitly.
os.environ["FLOOFY_NO_ADAPTERS"] = "1"
# SAFETY: the drop-in seams (agent/skill/appearance) land in ``KIRO_HOME`` (default ``~/.kiro``),
# which ``--home`` does not move. Point the whole session at a scratch Kiro home so a fixture
# that forgets to set ``KIRO_HOME`` still cannot write into the live ``~/.kiro/agents``.
SESSION_KIRO_HOME = Path(tempfile.mkdtemp(prefix="floofy-tests-kiro-"))
os.environ["KIRO_HOME"] = str(SESSION_KIRO_HOME)

def live_shells() -> list[Path]:
    """Every dashboard shell of a real host install on this machine, found the way the CLI would.

    The roots come from the installed edition adapters (plus the host's default data
    home for venv-style installs), so this module names no edition itself.
    """
    from floofy_core.editions import edition_providers
    from floofy_core.payloads import find_package_dirs

    roots: list[Path] = [Path.home() / ".kiro"]
    for provider in edition_providers():
        try:
            roots.extend(provider.roots())
        except OSError:
            continue
    shells: list[Path] = []
    for root in roots:
        for package_dir in find_package_dirs(root, max_depth=6):
            index = package_dir / "static" / "dist" / "index.html"
            if index.is_file():
                shells.append(index)
    return shells


def live_fingerprint() -> dict[str, tuple[int, float]]:
    """(size, mtime) of every live dashboard shell — cheap and sufficient to detect a write."""
    found: dict[str, tuple[int, float]] = {}
    for index in live_shells():
        try:
            stat = index.stat()
        except OSError:
            continue
        found[str(index)] = (stat.st_size, stat.st_mtime)
    return found


def live_dropin_sidecars() -> set[str]:
    """Every FloofyCrew ``.floofy-owner`` sidecar in the live drop-in seams (``~/.kiro/agents``, the
    default crew home's ``skills/`` and ``appearance-library/``).

    The seam handlers write one next to everything they install, so a new sidecar here
    during a test session means a fixture reached the live Kiro home (it happened once:
    a fixture without ``KIRO_HOME`` installed an ``agent`` part into ``~/.kiro/agents``).
    The live directories are only ever listed, never written.
    """
    from floofy_core.kinds.dropins import OWNER_SUFFIX

    kiro_home = Path.home() / ".kiro"
    crew_home = Path(os.environ.get("KIROCREW_HOME") or kiro_home / "crew")
    found: set[str] = set()
    for directory in (kiro_home / "agents", crew_home / "skills", crew_home / "appearance-library" / "appearances"):
        try:
            found.update(str(p) for p in directory.iterdir() if p.name.endswith(OWNER_SUFFIX))
        except OSError:
            continue
    return found


def read_manifest(root: Path) -> dict[str, Any]:
    return json.loads((root / "floofy.json").read_text(encoding="utf-8"))


def write_manifest(root: Path, manifest: dict[str, Any]) -> None:
    (root / "floofy.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def rehash(root: Path) -> None:
    """Bring ``files[].sha256`` in line with the files on disk (never adds unlisted files)."""
    hash_example_files.refresh_manifest(root, check=False, add_missing=False)



# --- fake host payloads --------------------------------------------------------------

#: A minimal dashboard shell with the shapes the Patcher anchors on: an inline import
#: map, a modulepreload hint and the module entry tag.
FAKE_INDEX_HTML = """<!doctype html>
<html lang="en" data-theme="dark">
  <head>
    <meta charset="UTF-8" />
    <title>KiroCrew</title>
    <script type="importmap">{"imports":{"react":"/vendor/react.mjs"}}</script>
    <link rel="modulepreload" crossorigin href="/assets/chunk-a-AAAA1111.js">
    <script type="module" crossorigin src="/assets/main-MMMM0000.js"></script>
  </head>
  <body><div id="root"></div></body>
</html>
"""


def fake_payload(
    root: Path,
    version: str,
    *,
    layout: str = "flat",
    build: str | None = None,
    index_html: str = FAKE_INDEX_HTML,
) -> Path:
    """Write a fake host payload under ``root`` and return its ``kiro_crew`` directory.

    ``layout``: ``flat`` (``root/kiro_crew``), ``venv`` (``pyvenv.cfg`` +
    ``lib/python3.12/site-packages/kiro_crew`` + ``bin/python``) or ``nested``
    (a desktop-bundle-like ``Contents/Resources/backend-dist/x86_64/lib/python3.12/
    site-packages/kiro_crew``). ``build`` writes the ``BUILD_VERSION`` stamp verbatim.
    """
    root = Path(root)
    if layout == "flat":
        package_dir = root / "kiro_crew"
    elif layout == "venv":
        package_dir = root / "lib" / "python3.12" / "site-packages" / "kiro_crew"
        root.mkdir(parents=True, exist_ok=True)
        (root / "pyvenv.cfg").write_text("home = /usr/bin\nversion_info = 3.12.14\n", encoding="utf-8")
        (root / "bin").mkdir(exist_ok=True)
        (root / "bin" / "python").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    elif layout == "nested":
        package_dir = root / "Contents" / "Resources" / "backend-dist" / "x86_64" / "lib" / "python3.12" / "site-packages" / "kiro_crew"
    else:
        raise ValueError(layout)
    dist = package_dir / "static" / "dist" / "assets"
    dist.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text(f'"""fake host"""\n__version__ = "{version}"\n', encoding="utf-8")
    if build is not None:
        (package_dir / "BUILD_VERSION").write_text(build, encoding="utf-8")
    (dist.parent / "index.html").write_text(index_html, encoding="utf-8")
    (dist / "main-MMMM0000.js").write_text('import "./chunk-a-AAAA1111.js";console.log("main");\n', encoding="utf-8")
    (dist / "chunk-a-AAAA1111.js").write_text('export const a = 1;\n', encoding="utf-8")
    return package_dir


# --- a stand-in gateway for the verify protocol ---------------------------------------


class FakeGateway:
    """Serves a payload's ``static/dist`` the way the dashboard does, over loopback TCP or a unix socket.

    ``GET /api/health`` answers ``{"ok": true, "app": "kirocrew", "version": <served>}``;
    ``GET /`` serves ``index.html``; ``GET /assets/<name>`` (and any other dist
    path) serves the file with ``Cache-Control: immutable`` under ``/assets``.
    ``status`` (Requirement 11.2 tests) is an optional dict served at ``/api/status``.
    """

    def __init__(self, dist_dir: Path, served_version: str, *, socket_path: Path | None = None, status: dict | None = None):
        self.dist_dir = Path(dist_dir)
        self.served_version = served_version
        self.socket_path = socket_path
        self.status = status
        self.requests: list[str] = []
        self._server: http.server.HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "FakeGateway":
        gateway = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:  # noqa: D401
                return None

            def address_string(self) -> str:
                return "local"

            def do_GET(self) -> None:  # noqa: N802
                path = unquote(urlsplit(self.path).path)
                gateway.requests.append(path)
                if path == "/api/health":
                    body = json.dumps({"ok": True, "app": "kirocrew", "version": gateway.served_version}).encode()
                    return self._send(200, body, "application/json")
                if path == "/api/status":
                    if gateway.status is None:
                        return self._send(401, b"{}", "application/json")
                    return self._send(200, json.dumps(gateway.status).encode(), "application/json")
                if path in ("/", "/index.html"):
                    target = gateway.dist_dir / "index.html"
                else:
                    target = gateway.dist_dir / path.lstrip("/")
                try:
                    target.resolve().relative_to(gateway.dist_dir.resolve())
                except ValueError:
                    return self._send(404, b"not found", "text/plain")
                if not target.is_file():
                    return self._send(404, b"not found", "text/plain")
                headers = {"Cache-Control": "public, max-age=31536000, immutable"} if path.startswith("/assets/") else {}
                return self._send(200, target.read_bytes(), "application/octet-stream", headers)

            def _send(self, status: int, body: bytes, content_type: str, headers: dict | None = None) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

        if self.socket_path is not None:

            class UnixServer(http.server.HTTPServer):
                address_family = socket.AF_UNIX
                allow_reuse_address = False

                def server_bind(self) -> None:
                    self.socket.bind(str(gateway.socket_path))
                    self.server_name, self.server_port = "localhost", 0

            self.socket_path.parent.mkdir(parents=True, exist_ok=True)
            self._server = UnixServer(str(self.socket_path), Handler, bind_and_activate=True)  # type: ignore[arg-type]
        else:
            self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    @property
    def port(self) -> int:
        assert self._server is not None
        return self._server.server_address[1] if self.socket_path is None else 0

    def __exit__(self, *exc) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self.socket_path is not None and self.socket_path.exists():
            self.socket_path.unlink()
