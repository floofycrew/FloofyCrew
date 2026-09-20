"""Run the manager App's ``node --test`` suite (``loader-app/tests/ui/*.test.mjs``) from pytest (task 11.3; Requirement 13.3).

The JavaScript tests stay runnable on their own (``node --test loader-app/tests/ui``);
this wrapper makes ``python -m pytest`` cover them too, and skips without Node 20+.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent / "ui"


def node_binary() -> str | None:
    candidate = shutil.which("node") or str(Path.home() / ".local" / "bin" / "node")
    if not Path(candidate).is_file() or not os.access(candidate, os.X_OK):
        return None
    try:
        version = subprocess.run([candidate, "--version"], capture_output=True, text=True, timeout=20, check=False).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    major = version.lstrip("v").split(".", 1)[0]
    return candidate if major.isdigit() and int(major) >= 20 else None


NODE = node_binary()


@pytest.mark.skipif(NODE is None, reason="node 20+ not available")
def test_manager_app_node_suite_passes():
    result = subprocess.run([NODE, "--test", str(TESTS_DIR)], capture_output=True, text=True, timeout=300, check=False, cwd=str(TESTS_DIR.parent.parent))
    summary = "\n".join(line for line in result.stdout.splitlines() if line.startswith("# "))
    assert result.returncode == 0, f"node --test failed:\n{result.stdout[-4000:]}\n{result.stderr[-2000:]}"
    assert "# fail 0" in summary, summary
