"""Shared helpers for the SPA host's browser tests and the Rimuru parity run (plain module on the pytest pythonpath).

Everything runs against the payload COPY under ``.scratch/`` with scratch homes
(``loader_testing.ScratchGateway``); importing this module arms the live-install
guard. The manager's kind handlers land in 6.x, so the pieces the manager will do
are done here by hand, the same way: the theme pack is direct-written into the
scratch home's ``themes/`` (the byte-equivalent path of Requirement 2.1), the mod
directory is copied into ``mods/<id>/``, ``enabled.json`` and ``consent.json`` are
written, and the Loader app is installed and granted through the host CLI.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from floofy_core.themes import direct_write_theme
from floofy_loader.consent import write_consent
from floofy_loader.paths import FloofyPaths

from loader_testing import REPO_ROOT, ScratchGateway, build_loader_app  # noqa: F401 (arms FLOOFY_NO_ADAPTERS)

MODS_DIR = REPO_ROOT / "mods"
RIMURU_DIR = MODS_DIR / "rimuru-branding"
CUSTOM_THEMES_DIR = MODS_DIR / "custom-themes"
API = "/api/apps/floofycrew"

#: The scratch home's display config: the pack selected, dark mode, onboarding done (no wizard over the shell).
DASHBOARD_CONFIG: dict[str, Any] = {"theme_color": "custom-rimuru", "theme_mode": "dark", "onboarded": True, "import_onboarded": True, "privacy_acked": True, "language": "en"}


def write_host_config(gw: ScratchGateway, *, dashboard: dict[str, Any] | None = None, trusted: bool = True) -> Path:
    """``config.json`` of the scratch home: display preferences plus the per-app trust grant (Requirement 2.3)."""
    gw.home.mkdir(parents=True, exist_ok=True)
    config_path = gw.home / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    config["dashboard"] = {**config.get("dashboard", {}), **(dashboard if dashboard is not None else DASHBOARD_CONFIG)}
    if trusted:
        config.setdefault("agent", {})["apps_trusted"] = ["floofycrew"]
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config_path


def install_mod(gw: ScratchGateway, mod_dir: Path, *, enabled: bool = True) -> Path:
    """Copy a mod into the scratch data home's ``mods/<id>/`` and flag it in ``enabled.json``; returns the installed dir."""
    paths = FloofyPaths(gw.data_home).ensure()
    manifest = json.loads((mod_dir / "floofy.json").read_text(encoding="utf-8"))
    target = paths.mod_dir(manifest["id"])
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(mod_dir, target)
    flags = json.loads(paths.enabled.read_text(encoding="utf-8")) if paths.enabled.is_file() else {}
    flags[manifest["id"]] = enabled
    paths.enabled.write_text(json.dumps(flags, indent=2) + "\n", encoding="utf-8")
    for part in manifest.get("parts", []):
        if part.get("kind") == "theme":
            direct_write_theme(mod_dir / Path(part["path"]).parent, gw.home / "themes")
    return target


def write_test_mod(gw: ScratchGateway, mod_id: str, files: dict[str, str], parts: list[dict[str, Any]], *, enabled: bool = True, extra: dict[str, Any] | None = None) -> Path:
    """A throwaway mod with the given files and parts, hashed and flagged."""
    import hashlib

    paths = FloofyPaths(gw.data_home).ensure()
    root = paths.mod_dir(mod_id)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    manifest = {
        "schema": 1,
        "id": mod_id,
        "name": mod_id,
        "version": "1.0.0",
        "description": "SPA host browser test mod",
        "authors": ["tests"],
        "license": "MIT",
        "kirocrew": {"version": ">=0.7.0 <0.9.0"},
        "dependsOn": {"floofycrew": ">=0.0.0"},
        "parts": parts,
        "files": [{"path": rel, "sha256": hashlib.sha256((root / rel).read_bytes()).hexdigest()} for rel in sorted(files)],
    }
    manifest.update(extra or {})
    (root / "floofy.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    flags = json.loads(paths.enabled.read_text(encoding="utf-8")) if paths.enabled.is_file() else {}
    flags[mod_id] = enabled
    paths.enabled.write_text(json.dumps(flags, indent=2) + "\n", encoding="utf-8")
    return root


def consent(gw: ScratchGateway) -> None:
    write_consent(FloofyPaths(gw.data_home).ensure().consent, by="spa-tests")


def install_loader_app(gw: ScratchGateway, app_dir: Path) -> None:
    """``kirocrew app install`` → the trust grant → ``kirocrew app enable`` on the scratch home.

    The grant is written AFTER the install: the host binds a name grant to the
    installed record's provenance, and one written before it exists is refused
    as "predating repository binding" (``kiro_crew/apps/execution.py``).
    """
    write_host_config(gw, trusted=False)
    installed = gw.cli("app", "install", str(app_dir))
    assert installed.returncode == 0 and "installed floofycrew" in installed.stdout, installed.stdout + installed.stderr
    write_host_config(gw)
    enabled = gw.cli("app", "enable", "floofycrew")
    assert enabled.returncode == 0 and "enabled floofycrew" in enabled.stdout, enabled.stdout + enabled.stderr


def dashboard_url(gw: ScratchGateway, path: str = "/") -> str:
    return f"http://127.0.0.1:{gw.ready['port']}{path}?token={gw.ready['token']}"
