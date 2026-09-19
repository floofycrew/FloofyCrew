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
