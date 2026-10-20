"""Payload discovery for the public open-source distribution (Requirement 5.1, 10.1).

Install shapes the public edition produces (``research/local-install-anatomy.md``,
``external-source.md``):

* **pipx** — ``~/.local/pipx/venvs/kirocrew`` (older pipx) or
  ``~/.local/share/pipx/venvs/kirocrew`` (current default), a venv marked by
  ``pyvenv.cfg``; ``PIPX_HOME`` overrides the base.
* **managed venv** — the installer's ``~/.kiro/crew-venv``; shadow-venv promotion
  stages the next version as a sibling ``~/.kiro/crew-venv-<ver>`` and swaps it
  in, so siblings are payloads too (dormant until promoted).
* **desktop bundle** — macOS ``KiroCrew.app/Contents/Resources/backend-dist``
  under ``/Applications`` or ``~/Applications``; Linux desktop trees under
  ``/opt``.

Every venv resolves through :func:`floofy_core.payloads.venv_site_packages` to
``lib/python3.*/site-packages/kiro_crew``; bundles are walked layout-agnostically.
``current`` is the payload a ``kirocrew`` launcher on this machine resolves into
(``~/.local/bin/kirocrew`` or the ``PATH`` entry); the promoted ``crew-venv`` is
current over its ``crew-venv-<ver>`` siblings when no launcher decides.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from floofy_core.payloads import (
    Payload,
    find_package_dirs,
    launcher_points_into,
    make_payload,
    path_token,
    venv_site_packages,
)
from floofy_core.semver import InvalidVersion

from . import EDITION

__all__ = [
    "DesktopBundleProvider",
    "ManagedVenvProvider",
    "PipxProvider",
    "default_providers",
    "probe_edition",
]

_SHADOW_VENV_RE = re.compile(r"^crew-venv(?:-(?P<ver>[0-9][0-9A-Za-z.-]*))?$")


def probe_edition(package_dir: Path) -> str | None:
    """The public edition has no companion package; a payload with no stamp file is ours."""
    return EDITION if not (Path(package_dir) / "BUILD_VERSION").is_file() else None


def _venv_payload(venv: Path, *, kind: str, source: str, current: bool | None = None) -> Payload | None:
    site = venv_site_packages(venv)
    if site is None:
        return None
    package_dir = site / "kiro_crew"
    if not (package_dir / "__init__.py").is_file():
        return None
    try:
        return make_payload(
            kind=kind,
            root=venv,
            package_dir=package_dir,
            source=source,
            discriminator=venv.name,
            edition=probe_edition(package_dir) or EDITION,
            current=launcher_points_into(venv) if current is None else current,
        )
    except InvalidVersion:
        return None


@dataclass
class PipxProvider:
    """``pipx install kirocrew`` venvs (both pipx home layouts, ``PIPX_HOME`` honoured)."""

    home: Path = field(default_factory=Path.home)
    name: str = "pipx"

    def roots(self) -> list[Path]:
        bases = []
        pipx_home = os.environ.get("PIPX_HOME")
        if pipx_home:
            bases.append(Path(pipx_home))
        bases += [self.home / ".local" / "pipx", self.home / ".local" / "share" / "pipx"]
        roots = [base / "venvs" / "kirocrew" for base in bases]
        return [r for r in dict.fromkeys(roots) if r.is_dir()]

    def discover(self) -> list[Payload]:
        found = (_venv_payload(root, kind="venv", source=self.name) for root in self.roots())
        return [p for p in found if p is not None]


@dataclass
class ManagedVenvProvider:
    """The installer's ``~/.kiro/crew-venv`` and its ``crew-venv-<ver>`` shadow siblings."""

    kiro_home: Path = field(default_factory=lambda: Path.home() / ".kiro")
    name: str = "managed-venv"

    def roots(self) -> list[Path]:
        try:
            children = sorted(Path(self.kiro_home).iterdir())
        except OSError:
            return []
        return [c for c in children if c.is_dir() and _SHADOW_VENV_RE.match(c.name)]

    def discover(self) -> list[Payload]:
        payloads: list[Payload] = []
        roots = self.roots()
        launcher_decides = any(launcher_points_into(r) for r in roots)
        for root in roots:
            promoted = _SHADOW_VENV_RE.match(root.name).group("ver") is None  # type: ignore[union-attr]
            current = launcher_points_into(root) if launcher_decides else promoted
            payload = _venv_payload(root, kind="venv", source=self.name, current=current)
            if payload is not None:
                payloads.append(payload)
        return payloads


@dataclass
class DesktopBundleProvider:
    """macOS ``.app`` ``backend-dist`` payloads and Linux ``/opt`` desktop trees."""

    home: Path = field(default_factory=Path.home)
    app_dirs: tuple[Path, ...] = (Path("/Applications"),)
    opt_dirs: tuple[Path, ...] = (Path("/opt"),)
    name: str = "desktop"

    def roots(self) -> list[Path]:
        roots: list[Path] = []
        for base in (*self.app_dirs, self.home / "Applications"):
            for app in sorted(base.glob("KiroCrew*.app")) if base.is_dir() else []:
                backend = app / "Contents" / "Resources" / "backend-dist"
                if backend.is_dir():
                    roots.append(backend)
        for base in self.opt_dirs:
            if base.is_dir():
                roots.extend(sorted(p for p in base.glob("kirocrew*") if p.is_dir()))
                roots.extend(sorted(p for p in base.glob("KiroCrew*") if p.is_dir()))
        return roots

    def discover(self) -> list[Payload]:
        payloads: list[Payload] = []
        for root in self.roots():
            for package_dir in find_package_dirs(root):
                try:
                    payloads.append(
                        make_payload(
                            kind="app",
                            root=root,
                            package_dir=package_dir,
                            source=self.name,
                            discriminator=path_token(root),
                            edition=probe_edition(package_dir) or EDITION,
                            current=launcher_points_into(root),
                        )
                    )
                except InvalidVersion:
                    continue
        return payloads


def default_providers(home: Path | None = None) -> list[PipxProvider | ManagedVenvProvider | DesktopBundleProvider]:
    """The public edition's provider set, rooted at ``home`` (default: the real home)."""
    base = home or Path.home()
    return [PipxProvider(home=base), ManagedVenvProvider(kiro_home=base / ".kiro"), DesktopBundleProvider(home=base)]
