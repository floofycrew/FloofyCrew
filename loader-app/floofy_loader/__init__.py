"""The FloofyCrew Loader: the Python runtime that lives inside the KiroCrew gateway.

The Loader is a KiroCrew App (``loader-app/app.json``) installed through the
host's own App Kit seam — DR-2 rung 2, Requirement 3.1 — never a
``kirocrew.plugins`` entry point. The host imports ``floofy_loader.hooks`` from
the installed app directory (``kiro_crew/apps/module_loader.py``
``load_app_module``) and calls ``on_startup(ctx)`` / ``on_shutdown(ctx)`` /
``register_routes(ctx)``; that thin shim puts the app directory on ``sys.path``
and hands over to :mod:`floofy_loader.runtime`, which owns the boot sequence
(:mod:`floofy_loader.boot`), the hook registry, the mod-facing API
(:mod:`floofy_loader.api`, importable by mods as ``floofy``) and the routes.

Everything here is standard-library Python 3.12 and edition-neutral: edition
facts come from the ``floofy_edition_*`` adapters through
:mod:`floofy_core.editions`. Nothing in this package writes under a host payload
root (Requirement 3.7); every file it owns lives under ``<host home>/floofy/``.
"""

__all__ = ["__version__"]

#: The Loader ships with the FloofyCrew release it belongs to; the build script
#: (``scripts/build_loader_app.py``) keeps ``app.json`` ``version`` in step with
#: ``floofy_core.__version__``.
__version__ = "1.3.0"
