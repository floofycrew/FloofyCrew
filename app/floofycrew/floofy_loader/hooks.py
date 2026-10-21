"""Host entry points of the Loader app — the only module the host imports directly.

``app.json`` names ``floofy_loader.hooks:on_startup``, ``:on_shutdown`` and
``:register_routes`` (``backend.hooks``). The host loads this file with
``importlib.util.spec_from_file_location`` under the namespaced key
``_kirocrew_app_floofycrew.floofy_loader.hooks`` and touches ``sys.path`` for
nothing (``kiro_crew/apps/module_loader.py`` L266–L340 ``load_app_module``;
the synthetic parent package is registered by ``_ensure_namespace_packages``
L140–L172). Absolute imports of ``floofy_loader`` and ``floofy_core`` would
therefore fail, so this shim does exactly one thing before delegating: it puts
the installed app directory (the directory holding ``app.json``) at the front
of ``sys.path`` and imports the canonical :mod:`floofy_loader.runtime` from
there. See ``loader-app/README.md`` "How the Loader finds floofy_core".

Requirement 3.6 — the gateway boots vanilla when the Loader fails: every entry
point below catches *everything*, records the failure for ``floofy doctor``
(``<host home>/floofy/loader-failure.json``) and returns normally. The host's
dispatcher (``kiro_crew/apps/lifecycle.py`` L405 ``_invoke``) would itself
only mark the app degraded on an exception, but a raising route registration
or a leaked exception would still cost the user a diagnostic they cannot see;
the shim keeps the contract absolute.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

#: The installed app directory: ``<host home>/apps/floofycrew/`` once installed,
#: ``loader-app/`` in the source tree.
APP_DIR = Path(__file__).resolve().parent.parent

#: Optional developer file beside ``app.json`` naming extra ``sys.path`` entries
#: (``{"pythonpath": ["/path/to/floofy-core", ...]}``) for a checkout that does
#: not vendor ``floofy_core``; ``scripts/build_loader_app.py`` never writes it.
HOME_FILE = APP_DIR / "floofy-home.json"

#: Set to ``1`` by the gateway-backed self-test (task 4.8 case 4) to make the
#: Loader fail on purpose and prove the vanilla-boot guarantee. Never read by
#: any other path.
SELFTEST_FAIL_ENV = "FLOOFY_LOADER_SELFTEST_FAIL"


def _data_home() -> Path:
    """``<host home>/floofy`` without importing the host (``KIROCREW_HOME`` or ``~/.kiro/crew``)."""
    override = os.environ.get("KIROCREW_HOME", "").strip()
    host_home = Path(override).expanduser() if override else Path.home() / ".kiro" / "crew"
    return host_home / "floofy"


def _ensure_import_paths() -> list[str]:
    """Put the app directory (and any ``floofy-home.json`` entries) on ``sys.path``; return what was added."""
    candidates: list[Path] = []
    if HOME_FILE.is_file():
        try:
            document = json.loads(HOME_FILE.read_text(encoding="utf-8"))
            for entry in document.get("pythonpath", []) if isinstance(document, dict) else []:
                candidates.append(Path(str(entry)).expanduser())
        except (OSError, ValueError):
            pass
    candidates.append(APP_DIR)
    added: list[str] = []
    for candidate in reversed(candidates):  # keep the listed order with the app dir first
        text = str(candidate)
        if text not in sys.path:
            sys.path.insert(0, text)
            added.append(text)
    return added


def _record_failure(phase: str, exc: BaseException) -> None:
    """Write ``loader-failure.json`` so ``floofy doctor`` and the manager UI can show why (Requirement 3.6)."""
    try:
        home = _data_home()
        home.mkdir(parents=True, exist_ok=True)
        record = {
            "phase": phase,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exception(exc),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "appDir": str(APP_DIR),
            "sysPathAdded": [p for p in sys.path if p == str(APP_DIR)],
        }
        (home / "loader-failure.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001 - the failure record must never become a second failure
        pass
    try:
        import logging

        logging.getLogger("floofy.loader").exception("FloofyCrew Loader failed during %s; the gateway continues vanilla", phase)
    except Exception:  # noqa: BLE001
        pass


def _runtime():
    """Import the canonical runtime (from the app directory on ``sys.path``)."""
    _ensure_import_paths()
    if os.environ.get(SELFTEST_FAIL_ENV) == "1":
        raise RuntimeError("FLOOFY_LOADER_SELFTEST_FAIL=1: deliberate Loader failure for the vanilla-boot test")
    import floofy_loader.runtime as runtime  # noqa: PLC0415 - deferred on purpose (see module docstring)

    return runtime


def on_startup(ctx):
    """``backend.hooks.on_startup``: run the boot sequence; never raise (Requirement 3.6)."""
    try:
        return _runtime().startup(ctx)
    except BaseException as exc:  # noqa: BLE001 - vanilla boot is the contract
        _record_failure("startup", exc)
        return None


def on_shutdown(ctx):
    """``backend.hooks.on_shutdown``: deactivate mods in reverse order; never raise."""
    try:
        return _runtime().shutdown(ctx)
    except BaseException as exc:  # noqa: BLE001
        _record_failure("shutdown", exc)
        return None


def register_routes(ctx):
    """``backend.hooks.routes``: the Loader's routes under ``/api/apps/floofycrew/``; ``[]`` on failure."""
    try:
        return _runtime().register_routes(ctx)
    except BaseException as exc:  # noqa: BLE001
        _record_failure("routes", exc)
        return []
