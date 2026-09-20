"""Helpers for the registry-tools tests: a scratch registry repository populated from the shipped example mods."""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

from floofy_testing import EXAMPLES_DIR, REPO_ROOT  # noqa: F401

__all__ = ["EXAMPLES_DIR", "REPO_ROOT", "ScratchRegistry", "zip_mod"]


def zip_mod(mod_dir: Path, top: str | None = None) -> bytes:
    """A ``.zip`` of ``mod_dir`` (wrapped in ``top/`` when given) — the shape of a release asset."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(mod_dir.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                relative = path.relative_to(mod_dir).as_posix()
                archive.write(path, f"{top}/{relative}" if top else relative)
    return buffer.getvalue()


class ScratchRegistry:
    """A registry repository under ``root`` with the layout ``registry_tools.repo`` reads."""

    def __init__(self, root: Path, *, source: str = "test:scratch-registry", key_id: str | None = None, template: str = "https://assets.example/{id}-{tag}/{file}"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.archives: dict[str, bytes] = {}
        config: dict[str, Any] = {"schema": 1, "source": source, "archiveUrlTemplate": template, "hostRegistry": {"repo": "https://git.example/scratch/registry", "branch": "main"}}
        if key_id:
            config["keyId"] = key_id
        (self.root / "registry.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    def add_version(self, mod_dir: Path, *, repo: str = "https://git.example/mods/{id}", channel: str = "stable", published_at: str = "2026-09-19T00:00:00Z", release_extra: dict[str, Any] | None = None, curated: dict[str, Any] | None = None, manifest_override: dict[str, Any] | None = None) -> tuple[str, str, Path]:
        """Copy ``mod_dir``'s manifest into ``mods/<id>/<version>/`` with a release record naming its zip; returns ``(id, version, archive path)``."""
        manifest = json.loads((mod_dir / "floofy.json").read_text(encoding="utf-8"))
        if manifest_override:
            manifest = {**manifest, **manifest_override}
        mod_id, version = manifest["id"], manifest["version"]
        archive = zip_mod(mod_dir, mod_id)
        name = f"{mod_id}-{version}.zip"
        url = f"https://assets.example/{mod_id}-{version}/{name}"
        self.archives[url] = archive
        directory = self.root / "mods" / mod_id / version
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "floofy.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        release = {"tag": version, "channel": channel, "publishedAt": published_at, "files": [{"url": url, "sha256": hashlib.sha256(archive).hexdigest(), "size": len(archive), "name": name}]}
        release.update(release_extra or {})
        (directory / "release.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
        record = {"repo": repo.replace("{id}", mod_id), **(curated or {})}
        (directory.parent / "mod.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        archive_path = self.root.parent / "assets" / name
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_bytes(archive)
        return mod_id, version, archive_path

    def write_compat(self, rows: list[dict[str, Any]]) -> Path:
        path = self.root / "compat.json"
        path.write_text(json.dumps({"schema": 1, "rows": rows}, indent=2) + "\n", encoding="utf-8")
        return path

    def copy_example(self, kind: str, target: Path) -> Path:
        shutil.copytree(EXAMPLES_DIR / kind, target)
        return target
