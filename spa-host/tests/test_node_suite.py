"""Run the SPA host's ``node --test`` suite from pytest (Requirement 13.3).

The JavaScript tests stay runnable on their own (``node --test 'spa-host/tests/*.test.mjs'``);
this wrapper only makes ``python -m pytest`` cover them too. Skips when no Node
20+ is available (``node`` on PATH or ``~/.local/bin/node``).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SRC_DIR = TESTS_DIR.parent / "src"


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
def test_spa_host_node_suite_passes():
    # Node 22+ treats a `node --test` positional as a file pattern, not a directory to
    # walk, so name the test files: that runs the same suite on Node 20 and on Node 24.
    tests = sorted(str(path) for path in TESTS_DIR.glob("*.test.mjs"))
    assert tests, f"no *.test.mjs files under {TESTS_DIR}"
    result = subprocess.run([NODE, "--test", *tests], capture_output=True, text=True, timeout=300, check=False, cwd=str(TESTS_DIR.parent))
    summary = "\n".join(line for line in result.stdout.splitlines() if line.startswith("# "))
    assert result.returncode == 0, f"node --test failed:\n{result.stdout[-4000:]}\n{result.stderr[-2000:]}"
    assert "# fail 0" in summary, summary


def test_spa_host_modules_are_plain_same_origin_es_modules():
    """Requirement 4.6 / DR-3: hand-written ES modules, no third-party or cross-origin imports, no build step."""
    for path in sorted(SRC_DIR.glob("*.mjs")):
        text = path.read_text(encoding="utf-8")
        assert '"use strict";' in text, path.name
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") and " from " in stripped:
                spec = stripped.rsplit(" from ", 1)[1].strip().strip(";").strip("\"'")
                assert spec.startswith("./"), f"{path.name}: only relative same-origin imports are allowed, got {spec!r}"
        assert "http://" not in text.replace("http://127.0.0.1", "").replace("http://localhost", ""), f"{path.name} mentions a remote plaintext URL"
        assert "https://" not in text, f"{path.name} would load or reference a third-party origin"
