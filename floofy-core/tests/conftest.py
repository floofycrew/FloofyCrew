"""Shared fixtures for the floofy-core tests."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable

import pytest

import hash_example_files  # from scripts/, on the pytest pythonpath

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "floofy-core" / "examples"
KINDS = ("theme", "agent", "skill", "appearance", "config", "app", "python-hook", "spa", "patch")


def read_manifest(root: Path) -> dict[str, Any]:
    return json.loads((root / "floofy.json").read_text(encoding="utf-8"))


def write_manifest(root: Path, manifest: dict[str, Any]) -> None:
    (root / "floofy.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def rehash(root: Path) -> None:
    """Bring ``files[].sha256`` in line with the files on disk (never adds unlisted files)."""
    hash_example_files.refresh_manifest(root, check=False, add_missing=False)


@pytest.fixture
def example_mod(tmp_path: Path) -> Callable[[str], Path]:
    """Copy an example mod into a scratch directory and return its root."""

    def factory(kind: str) -> Path:
        destination = tmp_path / kind
        shutil.copytree(EXAMPLES_DIR / kind, destination)
        return destination

    return factory


# --- fake host payloads --------------------------------------------------------------

#: A minimal dashboard shell with the shapes the Patcher anchors on: an inline import
#: map, a modulepreload hint and the module entry tag.
FAKE_INDEX_HTML = """<!doctype html>
<html lang="en" data-theme="dark">
  <head>
    <meta charset="UTF-8" />
    <title>KiroCrew</title>
    <script type="importmap">{"imports":{"react":"/vendor/react.mjs"}}</script>
    <link rel="modulepreload" crossorigin href="/assets/chunk-a-AAAA1111.js">
    <script type="module" crossorigin src="/assets/main-MMMM0000.js"></script>
  </head>
  <body><div id="root"></div></body>
</html>
"""


def fake_payload(
    root: Path,
    version: str,
    *,
    layout: str = "flat",
    build: str | None = None,
    index_html: str = FAKE_INDEX_HTML,
) -> Path:
    """Write a fake host payload under ``root`` and return its ``kiro_crew`` directory.

    ``layout``: ``flat`` (``root/kiro_crew``), ``venv`` (``pyvenv.cfg`` +
    ``lib/python3.12/site-packages/kiro_crew`` + ``bin/python``) or ``nested``
    (a desktop-bundle-like ``Contents/Resources/backend-dist/x86_64/lib/python3.12/
    site-packages/kiro_crew``). ``build`` writes the ``BUILD_VERSION`` stamp verbatim.
    """
    root = Path(root)
    if layout == "flat":
        package_dir = root / "kiro_crew"
    elif layout == "venv":
        package_dir = root / "lib" / "python3.12" / "site-packages" / "kiro_crew"
        root.mkdir(parents=True, exist_ok=True)
        (root / "pyvenv.cfg").write_text("home = /usr/bin\nversion_info = 3.12.14\n", encoding="utf-8")
        (root / "bin").mkdir(exist_ok=True)
        (root / "bin" / "python").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    elif layout == "nested":
        package_dir = root / "Contents" / "Resources" / "backend-dist" / "x86_64" / "lib" / "python3.12" / "site-packages" / "kiro_crew"
    else:
        raise ValueError(layout)
    dist = package_dir / "static" / "dist" / "assets"
    dist.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text(f'"""fake host"""\n__version__ = "{version}"\n', encoding="utf-8")
    if build is not None:
        (package_dir / "BUILD_VERSION").write_text(build, encoding="utf-8")
    (dist.parent / "index.html").write_text(index_html, encoding="utf-8")
    (dist / "main-MMMM0000.js").write_text('import "./chunk-a-AAAA1111.js";console.log("main");\n', encoding="utf-8")
    (dist / "chunk-a-AAAA1111.js").write_text('export const a = 1;\n', encoding="utf-8")
    return package_dir
