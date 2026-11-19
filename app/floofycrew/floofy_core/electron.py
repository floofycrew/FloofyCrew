"""Locate a payload's Electron shell — its ``app.asar`` and the binary carrying the fuse wire (Requirement 5.11).

A payload is anchored on the host's Python package (:mod:`floofy_core.payloads`);
on a desktop install that package sits *inside* the shell bundle::

    KiroCrew.app/Contents/Resources/backend-dist/…/site-packages/kiro_crew     (macOS)
    KiroCrew.app/Contents/Resources/app.asar
    KiroCrew.app/Contents/Frameworks/Electron Framework.framework/Versions/A/Electron Framework

    <install>/resources/app.asar                                               (Linux, Windows)
    <install>/kirocrew | <install>/KiroCrew.exe

so the shell is found by walking **up** from the package directory to the nearest
``Resources``/``resources`` directory holding an ``app.asar`` — never sideways,
never by searching the disk. A gateway-only install (a bundle version directory
on Linux, a pipx venv) has no shell and :func:`find_electron_shell` answers
``None``; an ``electron:`` patch target is then ``NotApplicable`` on that payload.

Edition-neutral: the layouts above are Electron's and electron-builder's, not any
edition's.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .asar import Fuses, read_fuses

__all__ = ["ASAR_NAME", "ElectronShell", "find_electron_shell"]

ASAR_NAME = "app.asar"
_RESOURCE_DIR_NAMES = frozenset({"Resources", "resources"})
_MAX_ANCESTORS = 14
_MIN_BINARY_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class ElectronShell:
    """One desktop shell: where its archive is, which platform layout, and its fuses when readable."""

    asar: Path
    resources_dir: Path
    platform: str  # "macos" | "linux" | "windows"
    binary: Path | None = None
    fuses: Fuses | None = None

    @property
    def integrity_locked(self) -> bool:
        """The shell validates asar integrity: a rewritten archive would be refused at launch."""
        return bool(self.fuses is not None and self.fuses.asar_integrity_enforced)

    def describe(self) -> str:
        wire = self.fuses.wire if self.fuses else "unknown"
        return f"{self.platform} shell {self.asar} (fuses {wire})"


def _macos_binaries(contents: Path) -> list[Path]:
    frameworks = contents / "Frameworks" / "Electron Framework.framework"
    return [frameworks / "Versions" / "A" / "Electron Framework", frameworks / "Electron Framework"]


def _flat_binaries(install: Path) -> list[Path]:
    candidates: list[Path] = []
    try:
        children = sorted(install.iterdir())
    except OSError:
        return candidates
    for child in children:
        try:
            if not child.is_file() or child.stat().st_size < _MIN_BINARY_BYTES:
                continue
        except OSError:
            continue
        name = child.name.lower()
        if name.endswith(".exe") or "kirocrew" in name or "electron" in name:
            candidates.insert(0, child)
        elif child.stat().st_mode & 0o111:
            candidates.append(child)
    return candidates


def _shell_at(resources: Path) -> ElectronShell | None:
    asar = resources / ASAR_NAME
    if not asar.is_file():
        return None
    if resources.name == "Resources" and resources.parent.name == "Contents":
        platform, binaries = "macos", _macos_binaries(resources.parent)
    else:
        platform = "windows" if any(p.suffix.lower() == ".exe" for p in _flat_binaries(resources.parent)[:3]) else "linux"
        binaries = _flat_binaries(resources.parent)
    binary = fuses = None
    for candidate in binaries:
        if not candidate.is_file():
            continue
        fuses = read_fuses(candidate)
        if fuses is not None:
            binary = candidate
            break
    return ElectronShell(asar=asar, resources_dir=resources, platform=platform, binary=binary, fuses=fuses)


def find_electron_shell(package_dir: Path | str, *, max_ancestors: int = _MAX_ANCESTORS) -> ElectronShell | None:
    """The shell whose bundle contains ``package_dir``, or ``None`` for a gateway-only payload."""
    node = Path(package_dir).resolve()
    for _ in range(max_ancestors):
        if node.parent == node:
            return None
        node = node.parent
        if node.name in _RESOURCE_DIR_NAMES:
            shell = _shell_at(node)
            if shell is not None:
                return shell
    return None
