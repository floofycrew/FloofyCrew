"""Shared helpers for the Loader tests (plain module on the pytest pythonpath).

Importing this module arms the same live-install guard as the core tests
(``floofy_testing`` sets ``FLOOFY_NO_ADAPTERS=1``): no Loader test may reach a
real host install. The gateway-backed tests run against a **copy** of a host
payload under ``.scratch/`` with scratch ``KIROCREW_HOME`` / ``KIRO_HOME`` and
skip cleanly when no copy exists (design "Spike outcomes" 1.3/1.5 recipe).
"""
from __future__ import annotations

import os
from pathlib import Path

from floofy_testing import REPO_ROOT, live_fingerprint  # noqa: F401 (arms FLOOFY_NO_ADAPTERS)

LOADER_APP_DIR = REPO_ROOT / "loader-app"
EXAMPLES_DIR = REPO_ROOT / "floofy-core" / "examples"

#: Where the gateway-backed tests expect a payload copy; ``FLOOFY_SCRATCH_PAYLOAD`` overrides.
SCRATCH_PAYLOAD_ENV = "FLOOFY_SCRATCH_PAYLOAD"
DEFAULT_SCRATCH_PAYLOAD = REPO_ROOT / ".scratch" / "payload-0.7.0.5"


def scratch_payload() -> Path | None:
    """The payload copy to test against, or ``None`` when there is none (tests skip)."""
    candidate = Path(os.environ.get(SCRATCH_PAYLOAD_ENV) or DEFAULT_SCRATCH_PAYLOAD).resolve()
    if (candidate / "bin" / "kirocrew").is_file() and site_packages(candidate) is not None:
        return candidate
    return None


def site_packages(payload: Path) -> Path | None:
    """The ``site-packages`` holding ``kiro_crew`` for the interpreter the launcher picks."""
    interpreter = bundle_interpreter(payload)
    if interpreter is None:
        return None
    minor = interpreter.name.removeprefix("python")
    for candidate in sorted(payload.glob(f"**/python{minor}/site-packages")):
        if (candidate / "kiro_crew" / "__init__.py").is_file():
            return candidate
    return None


def bundle_interpreter(payload: Path) -> Path | None:
    """The highest-minor real (non-symlink) ``python3.X`` in the payload, as ``bin/kirocrew`` selects it."""
    best: tuple[int, Path] | None = None
    for candidate in [*payload.glob("bin/python3.*"), *payload.glob("*/bin/python3.*")]:
        if candidate.is_symlink() or not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue
        suffix = candidate.name.removeprefix("python3.")
        if not suffix.isdigit():
            continue
        if best is None or int(suffix) > best[0]:
            best = (int(suffix), candidate)
    return best[1] if best else None



# --- a scratch gateway (payload copy + scratch homes) ------------------------------------------------

import http.cookiejar  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Any  # noqa: E402

SCRATCH_ROOT = REPO_ROOT / ".scratch" / "loader-tests"
#: Environment for the scratch gateway; an edition's identity-check skip variable can be added via
#: ``FLOOFY_SCRATCH_GATEWAY_ENV="K=V,K=V"`` (the internal adapter's README names it).
GATEWAY_ENV_EXTRA = "FLOOFY_SCRATCH_GATEWAY_ENV"


def build_loader_app(out: Path) -> Path:
    """Assemble the installable Loader app directory (all editions) at ``out``."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import build_loader_app as builder  # noqa: PLC0415

    return builder.build(out, list(builder.EDITIONS))


@dataclass
class HttpReply:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


@dataclass
class GatewayClient:
    """Loopback HTTP client that exchanges the one-time link token for the dashboard cookie once."""

    port: int
    token: str
    jar: http.cookiejar.CookieJar = field(default_factory=http.cookiejar.CookieJar)
    _exchanged: bool = False

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def request(self, method: str, path: str, body: Any = None, *, auth: bool = True, timeout: float = 30.0) -> HttpReply:
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        url = self.base + path
        if auth and not self._exchanged:
            url += ("&" if "?" in url else "?") + "token=" + self.token
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"} if data is not None else {})
        try:
            with opener.open(request, timeout=timeout) as response:
                reply = HttpReply(response.status, {k.lower(): v for k, v in response.headers.items()}, response.read())
        except urllib.error.HTTPError as error:
            reply = HttpReply(error.code, {k.lower(): v for k, v in (error.headers.items() if error.headers else [])}, error.read())
        if auth and not self._exchanged and any(c.name.startswith("mc_token_") for c in self.jar):
            self._exchanged = True
        return reply

    def get(self, path: str, **kw: Any) -> HttpReply:
        return self.request("GET", path, **kw)

    def post(self, path: str, body: Any = None, **kw: Any) -> HttpReply:
        return self.request("POST", path, body, **kw)


@dataclass
class ScratchGateway:
    """One ``kirocrew gateway --test-mode --no-crons --no-tunnel`` on a payload copy with scratch homes."""

    payload: Path
    scratch: Path
    process: subprocess.Popen | None = None
    ready: dict[str, Any] = field(default_factory=dict)
    extra_env: dict[str, str] = field(default_factory=dict)

    @property
    def home(self) -> Path:
        return self.scratch / "home"

    @property
    def data_home(self) -> Path:
        return self.home / "floofy"

    @property
    def launcher(self) -> Path:
        return self.payload / "bin" / "kirocrew"

    def env(self) -> dict[str, str]:
        env = dict(
            os.environ,
            KIROCREW_HOME=str(self.home),
            KIRO_HOME=str(self.scratch / "kiro"),
            KIROCREW_SKIP_MODEL_DOWNLOAD="1",
            PYTHONUNBUFFERED="1",
            FLOOFY_NO_ADAPTERS="1",  # the Loader inside the scratch gateway never lists live install roots
        )
        env.pop("FLOOFY_LOADER_SELFTEST_FAIL", None)
        for pair in filter(None, os.environ.get(GATEWAY_ENV_EXTRA, "").split(",")):
            key, _, value = pair.partition("=")
            env[key.strip()] = value.strip()
        env.update(self.extra_env)
        return env

    def cli(self, *args: str, timeout: float = 120.0) -> subprocess.CompletedProcess:
        """Run ``kirocrew <args>`` against the scratch home (install, enable, ...)."""
        self.home.mkdir(parents=True, exist_ok=True)
        (self.scratch / "kiro").mkdir(parents=True, exist_ok=True)
        return subprocess.run([str(self.launcher), *args], env=self.env(), capture_output=True, text=True, timeout=timeout, check=False, cwd=str(self.payload))

    def start(self, *, timeout: float = 180.0) -> "ScratchGateway":
        self.home.mkdir(parents=True, exist_ok=True)
        (self.scratch / "kiro").mkdir(parents=True, exist_ok=True)
        out_path, err_path = self.scratch / "gateway.out", self.scratch / "gateway.err"
        out = out_path.open("w", encoding="utf-8")
        err = err_path.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [str(self.launcher), "gateway", "--test-mode", "--no-crons", "--no-tunnel"],
            env=self.env(),
            stdout=out,
            stderr=err,
            cwd=str(self.payload),
            start_new_session=True,
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"gateway exited early: {err_path.read_text(encoding='utf-8', errors='replace')[-3000:]}")
            for line in out_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("KIROCREW_READY:"):
                    self.ready = json.loads(line[len("KIROCREW_READY:") :])
                    return self
            time.sleep(0.5)
        raise RuntimeError(f"gateway did not print KIROCREW_READY within {timeout} s")

    def client(self) -> GatewayClient:
        return GatewayClient(int(self.ready["port"]), str(self.ready["token"]))

    def stderr(self) -> str:
        try:
            return (self.scratch / "gateway.err").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def stop(self, *, timeout: float = 30.0) -> None:
        if self.process is None:
            return
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=timeout)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        finally:
            self.process = None


def fresh_scratch(name: str) -> Path:
    """An empty ``.scratch/loader-tests/<name>/`` directory."""
    target = SCRATCH_ROOT / name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    return target



# --- the payload copy's shell: restore + byte-identity guard --------------------------------------

import hashlib  # noqa: E402


def shell_path(payload: Path) -> Path:
    """``kiro_crew/static/dist/index.html`` of the payload copy."""
    site = site_packages(payload)
    assert site is not None
    return site / "kiro_crew" / "static" / "dist" / "index.html"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def restore_payload_copy(payload: Path, data_home: Path) -> str:
    """Return the payload COPY to vanilla through the Patcher (manifest + sweep) and return the shell's sha256.

    The gateway-backed tests patch the copy's ``index.html`` on purpose (the
    Loader is a re-apply trigger; the boot script and loader tag land there);
    this puts every byte back — a lost manifest still restores through the
    ``*.floofybak`` sweep — so the copy stays reusable. Never touches a live install.
    """
    from floofy_core.cli_patch import main as patch_main  # noqa: PLC0415

    home = Path(data_home).parent
    patch_main(["--quiet", "--data-home", str(data_home), "--host-home", str(home), "--root", str(payload), "--no-adapters", "restore", "--all"])
    return sha256_of(shell_path(payload))


class ShellGuard:
    """Restore the copy before and after a test module and assert the shell came back byte-identical."""

    def __init__(self, payload: Path, data_home: Path):
        self.payload = payload
        self.data_home = data_home
        self.vanilla_sha = restore_payload_copy(payload, data_home)

    def check(self) -> None:
        after = restore_payload_copy(self.payload, self.data_home)
        assert after == self.vanilla_sha, f"the payload copy's index.html did not come back byte-identical ({after} != {self.vanilla_sha})"
        assets = shell_path(self.payload).parent / "assets"
        leftovers = [p.name for p in assets.iterdir() if p.name.endswith(".floofybak") or "-floofy." in p.name]
        assert not leftovers, f"leftover Patcher files in the copy: {leftovers}"
