"""The Loader app as the manager sees it: where it is installed, how to obtain a build, its early shim.

The Loader is a KiroCrew App installed through the host App Kit into
``<host home>/apps/floofycrew/`` (Requirement 3.1; ``kiro_crew/apps/manager.py``
L67 ``apps_dir`` = ``config_dir()/apps``, L72 ``app_dir``, L59
``installed.json``). The manager never writes into that directory itself — the
host's ``kirocrew app install|enable|disable|uninstall`` do — but it needs to
know three things:

* :func:`installed_meta` — is it installed and enabled (``installed.json``
  ``enabled``, ``version``)?
* :func:`obtain_build` — a built app directory to hand to ``kirocrew app install``:
  ``--loader-app PATH`` (a directory or a ``.zip``/``.tar.gz`` archive of one),
  else a fresh build from a source checkout (``scripts/build_loader_app.py``
  beside this package), else nothing (the manual steps are printed).
* :func:`early_install_module` — the installed app's ``floofy_loader.early_install``
  (the shim package ``floofy_early`` lives beside it), imported from the app
  directory so the CLI never needs the Loader on its own import path.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

from .archive import ArchiveError, is_archive, open_archive_safely
from .governance import LOADER_APP_NAME

__all__ = ["APP_NAME", "EARLY_INSTALL_MODULE_NAME", "app_dir", "checkout_root", "early_install_module", "installed_meta", "load_module_from_file", "obtain_build"]

APP_NAME = LOADER_APP_NAME


def app_dir(host_home: Path) -> Path:
    return Path(host_home) / "apps" / APP_NAME


def installed_meta(host_home: Path) -> dict[str, Any]:
    """``{installed, enabled, version, appDir, dev}`` from the host's ``installed.json`` (absent → not installed)."""
    directory = app_dir(host_home)
    meta: dict[str, Any] = {"installed": (directory / "app.json").is_file(), "enabled": None, "version": None, "appDir": str(directory), "dev": None}
    try:
        record = json.loads((directory / "installed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return meta
    if isinstance(record, dict):
        meta["enabled"] = bool(record.get("enabled")) if "enabled" in record else None
        meta["version"] = record.get("version")
        meta["dev"] = bool(record.get("dev")) if "dev" in record else None
    return meta


def checkout_root() -> Path | None:
    """The FloofyCrew source checkout this ``floofy_core`` belongs to, or ``None`` (installed / zipapp)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "scripts" / "build_loader_app.py").is_file() and (parent / "loader-app" / "app.json").is_file():
            return parent
    return None


def obtain_build(explicit: Path | str | None = None, *, editions: str = "both") -> tuple[Path | None, str, Path | None]:
    """A built Loader app directory ready for ``kirocrew app install``, how it was obtained, and a temp dir to remove afterwards."""
    if explicit:
        source = Path(explicit).expanduser()
        if source.is_dir() and (source / "app.json").is_file():
            return source, f"prebuilt directory {source}", None
        if source.is_file() and is_archive(source):
            scratch = Path(tempfile.mkdtemp(prefix="floofy-loader-app-"))
            try:
                extracted = open_archive_safely(source, scratch)
            except ArchiveError as exc:
                return None, f"{source}: {exc}", scratch
            root = extracted if (extracted / "app.json").is_file() else next((p for p in extracted.rglob("app.json")), None)
            if root is None:
                return None, f"{source}: no app.json inside the archive", scratch
            root = root if root.is_dir() else root.parent
            return root, f"extracted {source}", scratch
        return None, f"{source} is neither a built app directory nor an archive", None
    root = checkout_root()
    if root is None:
        return None, "no --loader-app given and this floofy is not running from a source checkout", None
    sys.path.insert(0, str(root / "scripts"))
    try:
        import build_loader_app as builder  # noqa: PLC0415
    finally:
        sys.path.pop(0)
    scratch = Path(tempfile.mkdtemp(prefix="floofy-loader-build-"))
    wanted = list(builder.EDITIONS) if editions == "both" else [editions]
    built = builder.build(scratch / APP_NAME, wanted)
    return built, f"built from the checkout at {root} (editions: {', '.join(wanted)})", scratch


#: The ``sys.modules`` key the installed app's ``early_install.py`` is executed under (task 10.9).
EARLY_INSTALL_MODULE_NAME = "_floofy_installed_early_install"


def load_module_from_file(name: str, path: Path) -> ModuleType:
    """Execute the Python file at ``path`` as module ``name``, registered in ``sys.modules`` first.

    ``importlib.util.module_from_spec`` + ``exec_module`` alone leaves the module
    out of ``sys.modules``, and ``dataclasses`` resolves string annotations
    (``from __future__ import annotations``) through ``sys.modules[cls.__module__]``
    — so a ``@dataclass`` in such a file raises ``AttributeError: 'NoneType'
    object has no attribute '__dict__'`` inside ``dataclasses._is_type`` (task
    10.9). The module is registered before it runs and popped again when it
    fails; an existing module of the same name is replaced only on success.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot build an import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if previous is not None:
            sys.modules[name] = previous
        else:
            sys.modules.pop(name, None)
        raise
    return module


def early_install_module(host_home: Path) -> ModuleType | None:
    """``floofy_loader.early_install`` from the installed Loader app (fallback: the import path), or ``None``.

    The installed copy is preferred so the CLI never needs the Loader on its own
    import path and always talks about the shim the app actually ships; it is
    loaded through :func:`load_module_from_file` — registered in ``sys.modules``,
    which its ``@dataclass`` declarations under ``from __future__ import
    annotations`` require. Before task 10.9 the unregistered ``exec_module``
    raised, the exception was swallowed, the fallback import failed as well
    (the Loader is not on the CLI's path) and ``doctor`` printed "early shim:
    Loader app not installed" on a machine where the app was present and enabled.
    """
    directory = app_dir(host_home)
    candidate = directory / "floofy_loader" / "early_install.py"
    if candidate.is_file() and (directory / "floofy_early" / "__init__.py").is_file():
        try:
            return load_module_from_file(EARLY_INSTALL_MODULE_NAME, candidate)
        except Exception:  # noqa: BLE001 - fall through to the import path
            pass
    try:
        import floofy_loader.early_install as module  # type: ignore[import-not-found]  # noqa: PLC0415

        return module
    except ImportError:
        return None
