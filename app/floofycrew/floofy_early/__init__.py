"""FloofyCrew early shim — imported by ``zz_floofycrew.pth`` during ``site`` initialisation (Requirement 3.2).

A ``.pth`` line ``import floofy_early`` runs inside *every* interpreter that
processes this site directory (design "Spike outcomes" 1.5): on the bundled host
interpreter that is the gateway, the MCP gateway daemon, the ``kirocrew mcp-*``
shims and every other ``sys.executable -m …`` child — but **not** the sandbox
shims started with ``-I -S`` and not children started with ``-s`` (which skips
the *user* site; a venv site is still processed). It therefore does as little as
possible, all of it inside one ``try/except`` that swallows everything: a
raising ``.pth`` would print a traceback into every host process.

It is a no-op unless

a. ``kiro_crew`` is importable from this interpreter (``importlib.util.find_spec``,
   no import), and
b. ``<KIROCREW_HOME or ~/.kiro/crew>/floofy/early.json`` exists (written by the
   manager for mods whose ``python-hook`` part declares ``"early": true``), and
c. ``FLOOFY_EARLY_DISABLE`` is not ``1``.

Then it installs a one-shot ``sys.meta_path`` finder that lets the real import
of ``kiro_crew.platform.bootstrap`` (the host's platform composition entry,
``kiro_crew/platform/bootstrap.py`` in 0.7.0.5) proceed and, right after that
module executed, runs ``early(ctx)`` of every listed mod — fail-open per mod,
logged to ``<data home>/early.log``. Mods are imported under the same
``floofy_mods.<id>.<module>`` keys the Loader uses, so the Loader reuses the very
module object at ``activate`` time.

``early.json``::

    {"mods": [{"id": "my-mod", "path": "/home/me/.kiro/crew/floofy/mods/my-mod",
               "module": "hook", "part": 0}]}

Standard library only; no dependency on ``floofy_core`` or ``floofy_loader``.
"""
from __future__ import annotations

__version__ = "1.1.0"

#: The host module whose import completes platform bootstrap.
TRIGGER_MODULE = "kiro_crew.platform.bootstrap"
DISABLE_ENV = "FLOOFY_EARLY_DISABLE"


def _install() -> None:  # pragma: no cover - exercised through subprocess tests
    import importlib.abc
    import importlib.machinery
    import importlib.util
    import json
    import os
    import sys
    import time
    import traceback

    if os.environ.get(DISABLE_ENV) == "1":
        return
    if importlib.util.find_spec("kiro_crew") is None:
        return
    override = (os.environ.get("KIROCREW_HOME") or "").strip()
    host_home = os.path.expanduser(override) if override else os.path.join(os.path.expanduser("~"), ".kiro", "crew")
    data_home = os.path.join(host_home, "floofy")
    early_path = os.path.join(data_home, "early.json")
    if not os.path.isfile(early_path):
        return

    def log(message: str) -> None:
        try:
            with open(os.path.join(data_home, "early.log"), "a", encoding="utf-8") as handle:
                stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                handle.write(f"{stamp} pid={os.getpid()} argv={sys.argv[:3]!r} {message}\n")
        except OSError:
            pass

    try:
        with open(early_path, encoding="utf-8") as handle:
            document = json.load(handle)
        entries = [e for e in (document.get("mods") or []) if isinstance(e, dict) and isinstance(e.get("id"), str) and isinstance(e.get("path"), str)]
    except (OSError, ValueError) as exc:
        log(f"early.json unreadable: {exc}")
        return
    if not entries:
        return

    class EarlyContext:
        """What ``early(ctx)`` receives: identity, paths, a logger function and the bootstrap module."""

        api_version = "1.0.0"
        unofficial = True

        def __init__(self, mod_id: str, mod_dir: str, bootstrap_module: object):
            self.mod_id = mod_id
            self.mod_dir = mod_dir
            self.host_home = host_home
            self.data_home = data_home
            self.bootstrap = bootstrap_module
            self.state: dict = {}

        def log(self, message: str) -> None:
            log(f"[{self.mod_id}] {message}")

    def _namespace(mod_id: str, mod_dir: str) -> None:
        for name, search in (("floofy_mods", None), (f"floofy_mods.{mod_id}", mod_dir)):
            if name not in sys.modules:
                package = importlib.util.module_from_spec(importlib.machinery.ModuleSpec(name, None, is_package=True))
                package.__path__ = [search] if search else []
                sys.modules[name] = package

    def _run_early(bootstrap_module: object) -> None:
        for entry in entries:
            mod_id, mod_dir = entry["id"], entry["path"]
            module_name = str(entry.get("module") or "hook")
            try:
                candidate = os.path.join(mod_dir, module_name.replace(".", os.sep))
                if os.path.isdir(candidate):
                    file, is_package = os.path.join(candidate, "__init__.py"), True
                else:
                    file, is_package = candidate + ".py", False
                if not os.path.isfile(file):
                    log(f"[{mod_id}] early module file missing: {file}")
                    continue
                key = f"floofy_mods.{mod_id}.{module_name}"
                _namespace(mod_id, mod_dir)
                module = sys.modules.get(key)
                if module is None or os.path.realpath(getattr(module, "__file__", None) or "") != os.path.realpath(file):
                    spec = importlib.util.spec_from_file_location(key, file, submodule_search_locations=[os.path.dirname(file)] if is_package else None)
                    if spec is None or spec.loader is None:
                        log(f"[{mod_id}] cannot build a spec for {file}")
                        continue
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[key] = module
                    spec.loader.exec_module(module)
                early = getattr(module, "early", None)
                if not callable(early):
                    log(f"[{mod_id}] {module_name} has no early(ctx); skipped")
                    continue
                early(EarlyContext(mod_id, mod_dir, bootstrap_module))
                log(f"[{mod_id}] early() ran")
            except Exception:  # noqa: BLE001 - fail-open per mod
                log(f"[{mod_id}] early() failed:\n{traceback.format_exc()}")

    class _AfterImportLoader(importlib.abc.Loader):
        def __init__(self, inner: importlib.abc.Loader):
            self._inner = inner

        def create_module(self, spec):  # noqa: D401
            create = getattr(self._inner, "create_module", None)
            return create(spec) if create is not None else None

        def exec_module(self, module) -> None:
            self._inner.exec_module(module)
            log(f"{TRIGGER_MODULE} imported; running early() for {len(entries)} mod(s)")
            _run_early(module)

    class _EarlyFinder(importlib.abc.MetaPathFinder):
        """One-shot: hands the real spec of the trigger module back with a wrapped loader."""

        def find_spec(self, fullname, path=None, target=None):
            if fullname != TRIGGER_MODULE:
                return None
            try:
                sys.meta_path.remove(self)
            except ValueError:
                pass
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
            if spec is None or spec.loader is None:
                return None
            spec.loader = _AfterImportLoader(spec.loader)
            return spec

    sys.meta_path.insert(0, _EarlyFinder())
    log(f"armed for {TRIGGER_MODULE} ({len(entries)} mod(s))")


try:  # the whole shim is fail-open: a .pth import must never print a traceback into a host process
    _install()
except BaseException:  # noqa: BLE001
    pass
