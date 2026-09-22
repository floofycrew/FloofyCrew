"""os-notify-bridge: the bridge's node suite passes and the mod keeps its house rules.

The behaviour tests live with the mod (``mods/os-notify-bridge/tests``,
plain ``node --test`` like the SPA host's suite); this wrapper makes
``python -m pytest`` cover them too and pins the rules that made the mod
acceptable in the first place: no browser pop-ups (the desktop shell has
none), no third-party origins, no network hosts, and the parts stay the
two declared ones (a runtime ``spa`` bridge and a ``ui`` settings page).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from floofy_testing import REPO_ROOT

MOD_DIR = REPO_ROOT / "mods" / "os-notify-bridge"


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
def test_bridge_node_suite_passes():
    result = subprocess.run(
        [NODE, "--test", str(MOD_DIR / "tests")],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        cwd=str(MOD_DIR),
    )
    summary = "\n".join(line for line in result.stdout.splitlines() if line.startswith("# "))
    assert result.returncode == 0, f"node --test failed:\n{result.stdout[-4000:]}\n{result.stderr[-2000:]}"
    assert "# fail 0" in summary, summary


def test_no_browser_popups_and_no_third_party_origins():
    """The desktop shell has no window.alert/confirm/prompt; everything stays same-origin."""
    for rel in ("spa/main.js", "ui/page.mjs"):
        text = (MOD_DIR / rel).read_text(encoding="utf-8")
        for banned in ("window.alert", "window.confirm", "window.prompt"):
            assert banned not in text, f"{rel} uses {banned}"
        assert "https://" not in text, f"{rel} references a remote origin"
        assert "http://" not in text.replace("http://127.0.0.1", "").replace("http://localhost", ""), f"{rel} references a remote plaintext URL"


def test_manifest_shape():
    manifest = json.loads((MOD_DIR / "floofy.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "os-notify-bridge"
    assert manifest["network"] == {"hosts": [], "credentials": False}
    assert [part["kind"] for part in manifest["parts"]] == ["spa", "ui"]
    assert manifest["parts"][0]["activation"] == "runtime"
    # the suite ships hashed like every other file (the mochi convention)
    assert any(entry["path"] == "tests/bridge.test.mjs" for entry in manifest["files"])
