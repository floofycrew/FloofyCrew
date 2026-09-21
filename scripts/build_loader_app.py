#!/usr/bin/env python3
"""Assemble the installable FloofyCrew Loader app directory.

The host installs an app by copying a directory holding ``app.json``
(``kirocrew app install <dir>``; ``kiro_crew/apps/manager.py`` ``install_app``
→ ``_copy_app_tree``). The Loader's Python must therefore be **self-contained
inside that directory**: the host imports hook modules by file path and never
extends ``sys.path`` (``kiro_crew/apps/module_loader.py``), so
``floofy_loader/hooks.py`` puts the app directory itself on ``sys.path`` and
expects ``floofy_core`` and the edition adapters to sit right beside it. This
script builds that layout::

    <out>/
      app.json                 version stamped from floofy_core.__version__
      art/                     store art declared by app.json (iconPath -> art/icon.svg)
      ui/                      manager page entry (loader-app/ui) + the SPA host
                               modules and surface registry (spa-host/src, task 5.x);
                               the Patcher later adds ui/patched/ and ui/boot/ at
                               apply time on the user's machine, never here
      floofy_loader/           the runtime (loader-app/floofy_loader)
      floofy_early/            the optional early shim package (task 4.7)
      floofy_core/             vendored copy of floofy-core/floofy_core
      floofy_edition_<x>/      the adapters chosen with --edition

Usage:
    python scripts/build_loader_app.py [--out DIR] [--edition toolbox|public|both]
                                       [--check]

``--edition both`` (the default) vendors every adapter under ``editions/``; the
adapter for the other edition is inert on a host it does not recognise, and one
archive then installs on both editions. ``--check`` rebuilds into a temporary
directory and exits 1 when ``<out>`` differs (CI freshness check).

Standard library only.
"""
from __future__ import annotations

import argparse
import filecmp
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import build_zipapp  # noqa: E402  (shares NOTICE_FILES / notice_members with the other builders)

LOADER_APP = REPO_ROOT / "loader-app"
SPA_HOST_SRC = REPO_ROOT / "spa-host" / "src"
DEFAULT_OUT = LOADER_APP / "build" / "floofycrew"

#: Directory names never copied into the app (tests and caches are not runtime).
SKIP_NAMES = frozenset({"__pycache__", "tests", ".pytest_cache", ".hypothesis", "build", "node_modules", ".git"})
#: SPA host files copied into ``ui/`` (hand-written ES modules and JSON — no build step, no npm).
SPA_HOST_SUFFIXES = (".mjs", ".js", ".json", ".css")

EDITIONS = {"toolbox": "floofy_edition_toolbox", "public": "floofy_edition_public"}


def _ignore(_directory: str, names: list[str]) -> set[str]:
    return {n for n in names if n in SKIP_NAMES or n.endswith(".pyc")}


def core_version() -> str:
    """``floofy_core.__version__`` read from the source without importing it."""
    text = (REPO_ROOT / "floofy-core" / "floofy_core" / "__init__.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("floofy_core/__init__.py has no __version__ literal")


def spa_host_files() -> list[Path]:
    """The SPA host modules shipped in ``ui/`` (source of truth: ``spa-host/src``)."""
    if not SPA_HOST_SRC.is_dir():
        return []
    return sorted(p for p in SPA_HOST_SRC.iterdir() if p.is_file() and p.suffix in SPA_HOST_SUFFIXES)


def copy_spa_host(ui_dir: Path) -> list[Path]:
    """Copy the SPA host into the app's ``ui/`` next to the manager entry; a name clash is a build error."""
    copied: list[Path] = []
    for source in spa_host_files():
        destination = ui_dir / source.name
        if destination.exists():
            raise SystemExit(f"ui/{source.name} exists in both loader-app/ui and spa-host/src; the SPA host owns that name")
        shutil.copy2(source, destination)
        copied.append(destination)
    return copied


def build(out: Path, editions: list[str]) -> Path:
    """Write the app directory at ``out`` (replacing it) and return it."""
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    manifest = json.loads((LOADER_APP / "app.json").read_text(encoding="utf-8"))
    manifest["version"] = core_version()
    (out / "app.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    shutil.copytree(LOADER_APP / "ui", out / "ui", ignore=_ignore)
    copy_spa_host(out / "ui")
    # Store art the manifest declares (``iconPath`` etc.); the host serves only the
    # declared paths from ``/apps/floofycrew/art/`` (``kiro_crew/apps/routes.py``
    # ``handle_app_art_file``), so the directory is part of the installable tree.
    if (LOADER_APP / "art").is_dir():
        shutil.copytree(LOADER_APP / "art", out / "art", ignore=_ignore)
    shutil.copytree(LOADER_APP / "floofy_loader", out / "floofy_loader", ignore=_ignore)
    if (LOADER_APP / "floofy_early").is_dir():
        shutil.copytree(LOADER_APP / "floofy_early", out / "floofy_early", ignore=_ignore)
    shutil.copytree(REPO_ROOT / "floofy-core" / "floofy_core", out / "floofy_core", ignore=_ignore)
    for edition in editions:
        package = EDITIONS[edition]
        shutil.copytree(REPO_ROOT / "editions" / edition / package, out / package, ignore=_ignore)
    # the app tree vendors floofy_core (the themes.py port among it): the notices ride along
    for name, data in build_zipapp.notice_members():
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return out


def differs(left: Path, right: Path) -> bool:
    """True when the two trees differ in file names or contents."""
    comparison = filecmp.dircmp(left, right)
    if comparison.left_only or comparison.right_only or comparison.diff_files or comparison.funny_files:
        return True
    return any(differs(left / sub, right / sub) for sub in comparison.common_dirs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(DEFAULT_OUT), help=f"output directory (default {DEFAULT_OUT.relative_to(REPO_ROOT)})")
    parser.add_argument("--edition", choices=("toolbox", "public", "both"), default="both")
    parser.add_argument("--check", action="store_true", help="exit 1 when --out is not a fresh build")
    args = parser.parse_args(argv)
    editions = list(EDITIONS) if args.edition == "both" else [args.edition]
    out = Path(args.out).resolve()
    if args.check:
        if not out.is_dir():
            print(f"{out} does not exist", file=sys.stderr)
            return 1
        with tempfile.TemporaryDirectory() as scratch:
            fresh = build(Path(scratch) / "floofycrew", editions)
            if differs(fresh, out):
                print(f"{out} is stale; rebuild with scripts/build_loader_app.py --out {out}", file=sys.stderr)
                return 1
        print(f"OK: {out} is a fresh build")
        return 0
    built = build(out, editions)
    files = sum(1 for p in built.rglob("*") if p.is_file())
    print(f"built {built} ({files} files, editions: {', '.join(editions)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
