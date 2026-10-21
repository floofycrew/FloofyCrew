"""Installers and status for the optional early shim (Requirement 3.2, 10.1).

The shim is the ``floofy_early`` package plus a ``zz_floofycrew.pth`` line
(``import floofy_early``) in the site directory the host interpreter processes:

* **user site** on payloads whose interpreter honours it — the bundled host
  interpreter is started plainly (no ``-s``/``-I``, no ``PYTHONNOUSERSITE``;
  design "Spike outcomes" 1.5), so ``site.getusersitepackages()`` of that
  interpreter (``~/.local/lib/python3.X/site-packages``) is the place;
* **venv site** on venv payloads — ``site.ENABLE_USER_SITE`` is ``False`` inside
  a venv, so only ``sysconfig.get_paths()["purelib"]`` of the venv interpreter
  works, and it must be re-applied for every venv (``crew-venv-<ver>``).

The edition adapter chooses which installer applies; this module only knows the
two mechanisms. Reach limit worth documenting (spike 1.5): children the host
starts with ``-s`` (``kiro_crew/apps/bridges.py`` L560, app-provided MCP servers
that wrap the CLI) skip the *user* site, and the ``-I -S`` sandbox shims skip
both; the venv site still reaches ``-s`` children. ``floofy doctor`` reports
this through :func:`status`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

__all__ = ["PTH_NAME", "ShimStatus", "install_user_site", "install_venv_site", "site_dirs", "status", "uninstall_user_site", "uninstall_venv_site"]

PTH_NAME = "zz_floofycrew.pth"
PACKAGE_NAME = "floofy_early"
PTH_LINE = "import floofy_early\n"

#: The shim package as shipped inside the Loader app directory (beside ``floofy_loader``).
SOURCE_PACKAGE = Path(__file__).resolve().parent.parent / PACKAGE_NAME

REACH_NOTE_USER = "user site: reaches the gateway and every plain `sys.executable -m …` child; skipped by `-s` children (app-provided MCP servers wrapping the CLI) and by the `-I -S` sandbox shims"
REACH_NOTE_VENV = "venv site: reaches plain and `-s` children; skipped only by the `-I -S` sandbox shims"


@dataclass(frozen=True)
class SiteDirs:
    """What the target interpreter says about its site directories."""

    interpreter: str
    user_site: str | None
    user_site_enabled: bool | None
    purelib: str | None
    in_venv: bool
    version: str


def site_dirs(interpreter: Path | str, *, env: dict[str, str] | None = None, timeout: float = 30.0) -> SiteDirs:
    """Ask ``interpreter`` for its user site, ``ENABLE_USER_SITE``, purelib and venv-ness (one subprocess)."""
    code = (
        "import json, site, sys, sysconfig;"
        "print(json.dumps({'user_site': site.getusersitepackages(), 'enabled': site.ENABLE_USER_SITE,"
        " 'purelib': sysconfig.get_paths()['purelib'], 'in_venv': sys.prefix != sys.base_prefix,"
        " 'version': '%d.%d' % sys.version_info[:2]}))"
    )
    result = subprocess.run([str(interpreter), "-c", code], capture_output=True, text=True, timeout=timeout, check=False, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"{interpreter} could not report its site directories: {result.stderr.strip()[-500:]}")
    document = json.loads(result.stdout.strip().splitlines()[-1])
    return SiteDirs(str(interpreter), document.get("user_site"), document.get("enabled"), document.get("purelib"), bool(document.get("in_venv")), str(document.get("version")))


@dataclass(frozen=True)
class ShimStatus:
    """What ``floofy doctor`` shows about the early shim for one interpreter."""

    kind: str  # "user-site" | "venv-site"
    interpreter: str
    site_dir: str | None
    pth_present: bool
    package_present: bool
    package_version: str | None
    user_site_enabled: bool | None
    reach: str
    notes: tuple[str, ...] = ()

    @property
    def installed(self) -> bool:
        return self.pth_present and self.package_present

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["installed"] = self.installed
        data["notes"] = list(self.notes)
        return data


def _package_version(package_dir: Path) -> str | None:
    init = package_dir / "__init__.py"
    try:
        for line in init.read_text(encoding="utf-8").splitlines():
            if line.startswith("__version__"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def _install_into(site_dir: Path, source: Path = SOURCE_PACKAGE) -> tuple[Path, Path]:
    """Copy the shim package into ``site_dir`` and write the ``.pth`` (idempotent; returns (pth, package))."""
    if not (source / "__init__.py").is_file():
        raise FileNotFoundError(f"shim package not found at {source}")
    site_dir = Path(site_dir)
    site_dir.mkdir(parents=True, exist_ok=True)
    package_dir = site_dir / PACKAGE_NAME
    if package_dir.exists():
        shutil.rmtree(package_dir)
    shutil.copytree(source, package_dir, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", PTH_NAME))
    pth = site_dir / PTH_NAME
    pth.write_text(PTH_LINE, encoding="utf-8")
    return pth, package_dir


def _uninstall_from(site_dir: Path) -> list[str]:
    removed: list[str] = []
    site_dir = Path(site_dir)
    pth = site_dir / PTH_NAME
    if pth.exists():
        pth.unlink()
        removed.append(str(pth))
    package_dir = site_dir / PACKAGE_NAME
    if package_dir.exists():
        shutil.rmtree(package_dir)
        removed.append(str(package_dir))
    return removed


def install_user_site(interpreter: Path | str, *, env: dict[str, str] | None = None, source: Path = SOURCE_PACKAGE) -> ShimStatus:
    """Install into the interpreter's **user** site (the bundle-interpreter case)."""
    dirs = site_dirs(interpreter, env=env)
    if not dirs.user_site:
        raise RuntimeError(f"{interpreter} reports no user site directory")
    _install_into(Path(dirs.user_site), source)
    return status(interpreter, "user-site", env=env)


def install_venv_site(interpreter: Path | str, *, env: dict[str, str] | None = None, source: Path = SOURCE_PACKAGE) -> ShimStatus:
    """Install into the interpreter's ``purelib`` (the venv case; re-run per venv)."""
    dirs = site_dirs(interpreter, env=env)
    if not dirs.purelib:
        raise RuntimeError(f"{interpreter} reports no purelib directory")
    _install_into(Path(dirs.purelib), source)
    return status(interpreter, "venv-site", env=env)


def uninstall_user_site(interpreter: Path | str, *, env: dict[str, str] | None = None) -> list[str]:
    dirs = site_dirs(interpreter, env=env)
    return _uninstall_from(Path(dirs.user_site)) if dirs.user_site else []


def uninstall_venv_site(interpreter: Path | str, *, env: dict[str, str] | None = None) -> list[str]:
    dirs = site_dirs(interpreter, env=env)
    return _uninstall_from(Path(dirs.purelib)) if dirs.purelib else []


def status(interpreter: Path | str | None = None, kind: str = "user-site", *, env: dict[str, str] | None = None) -> ShimStatus:
    """Report the shim's presence for ``interpreter`` (default: this one) in the given site kind."""
    target = str(interpreter or sys.executable)
    notes: list[str] = []
    try:
        dirs = site_dirs(target, env=env)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        return ShimStatus(kind, target, None, False, False, None, None, "", (f"interpreter unreachable: {exc}",))
    site_dir = Path(dirs.user_site) if kind == "user-site" else Path(dirs.purelib) if dirs.purelib else None
    if kind == "user-site":
        reach = REACH_NOTE_USER
        if dirs.user_site_enabled is False:
            notes.append("this interpreter does not process the user site (ENABLE_USER_SITE is False: a venv, or -s/-I); use the venv-site shim")
    else:
        reach = REACH_NOTE_VENV
        if not dirs.in_venv:
            notes.append("this interpreter is not a venv: purelib is the system site-packages")
    if site_dir is None:
        return ShimStatus(kind, target, None, False, False, None, dirs.user_site_enabled, reach, tuple(notes))
    package_dir = site_dir / PACKAGE_NAME
    return ShimStatus(
        kind,
        target,
        str(site_dir),
        (site_dir / PTH_NAME).is_file(),
        (package_dir / "__init__.py").is_file(),
        _package_version(package_dir),
        dirs.user_site_enabled,
        reach,
        tuple(notes),
    )
