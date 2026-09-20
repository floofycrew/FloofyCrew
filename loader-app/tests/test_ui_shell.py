"""The manager App shell, statically (Requirement 16.1, 16.2, 15.1 mirrored; task 11.2).

The browser test (``spa-host/tests/test_playwright.py::test_5_…``) renders the
shell against a real dashboard; this module checks what needs no browser: every
hand-written module parses (``node --check``), the navigation is Mods · Registry
· Profiles · Doctor · Audit · Settings with Mods first, the App's palette is the
CLI's 24-bit palette tone for tone, every page names the terminal command for a
capability the App lacks, and the shell reaches the Loader only same-origin.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from floofy_core.cli.style import PALETTE, WEIGHTS

from loader_testing import LOADER_APP_DIR

UI = LOADER_APP_DIR / "ui"
NODE = shutil.which("node") or (str(Path.home() / ".local" / "bin" / "node") if (Path.home() / ".local" / "bin" / "node").is_file() else None)


def modules() -> list[Path]:
    return sorted(p for p in UI.rglob("*.mjs") if "build" not in p.parts)


def test_every_shell_module_parses():
    if NODE is None:
        pytest.skip("node is not installed")
    files = modules()
    assert {p.name for p in files} >= {"index.mjs", "api.mjs", "palette.mjs", "mods.mjs", "registry.mjs", "profiles.mjs", "doctor.mjs", "audit.mjs", "settings.mjs"}
    for path in files:
        done = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, timeout=60, env={**os.environ, "NODE_OPTIONS": ""})
        assert done.returncode == 0, f"{path.relative_to(UI)}: {done.stderr[-800:]}"


def test_navigation_is_the_six_sections_with_mods_first():
    text = (UI / "index.mjs").read_text(encoding="utf-8")
    keys = re.findall(r'\{ key: "([a-z]+)", label: "([A-Za-z]+)", component: (\w+) \}', text)
    assert [k for k, _, _ in keys] == ["mods", "registry", "profiles", "doctor", "audit", "settings"]
    assert [label for _, label, _ in keys] == ["Mods", "Registry", "Profiles", "Doctor", "Audit", "Settings"]
    assert 'const key = match ? match[1] : "mods"' in text, "the landing page is the manager page (Requirement 16.1)"
    for _, _, component in keys:
        assert re.search(rf"import \{{ {component} \}} from \"./app/pages/\w+\.mjs\";", text), component
    assert "floofycrew-banner" in text and "UNOFFICIAL" in text, "the unofficial banner is always visible (Requirement 11.8)"
    manifest = json.loads((LOADER_APP_DIR / "app.json").read_text(encoding="utf-8"))
    assert f'export const ICON_PATH = "{manifest["iconPath"]}";' in text, "the header shows the declared store icon (the host serves /apps/<name>/art/<iconPath>)"
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))
    assert 'import React from "react";' in code and "@kirocrew/app-sdk" not in code, "hand-written React through the host's import map, no SDK components"


def _hex(sgr: str) -> str:
    """``[1;]38;2;r;g;b`` → ``#rrggbb``."""
    parts = sgr.split(";")
    r, g, b = (int(x) for x in parts[-3:])
    return f"#{r:02x}{g:02x}{b:02x}"


def test_the_app_palette_is_the_cli_palette():
    text = (UI / "app" / "palette.mjs").read_text(encoding="utf-8")
    block = re.search(r"export const PALETTE = Object\.freeze\(\{(.*?)\}\);", text, re.S)
    assert block, "palette.mjs exports PALETTE"
    js = dict(re.findall(r'(\w+): "(#[0-9a-f]{6})"', block.group(1)))
    expected = {tone: _hex(truecolor) for tone, (_basic, _extended, truecolor) in PALETTE.items()}
    assert js == expected, "the App's colours are the CLI's 24-bit palette, tone for tone"
    bold = re.search(r"export const BOLD_TONES = Object\.freeze\(\[(.*?)\]\);", text)
    assert bold and set(re.findall(r'"(\w+)"', bold.group(1))) == {tone for tone, (_b, _e, truecolor) in PALETTE.items() if truecolor.startswith("1;")}
    weights = re.search(r"export const WEIGHTS = Object\.freeze\(\{(.*?)\}\);", text)
    assert weights and set(re.findall(r"(\w+): \{", weights.group(1))) == set(WEIGHTS)


def test_pages_name_the_terminal_command_for_what_the_app_lacks():
    settings = (UI / "app" / "pages" / "settings.mjs").read_text(encoding="utf-8")
    for command in ("floofy init", "floofy deinit", "floofy apply", "floofy restore --all", "floofy verify", "floofy hold", "floofy dev", "floofy new", "floofy validate", "kirocrew gateway restart"):
        assert command in settings, command
    for page, command in (("mods.mjs", "floofy install"), ("registry.mjs", "floofy registry add"), ("profiles.mjs", "floofy profile"), ("doctor.mjs", "floofy doctor"), ("audit.mjs", "floofy audit")):
        assert command in (UI / "app" / "pages" / page).read_text(encoding="utf-8"), (page, command)


def test_the_shell_talks_to_the_loader_same_origin_only():
    for path in modules():
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"""fetch\(\s*["'`]https?://""", text), f"{path.name} fetches a remote origin"
        assert not re.search(r"""import\(\s*["'`]https?://""", text), f"{path.name} imports from a remote origin"
    api = (UI / "app" / "api.mjs").read_text(encoding="utf-8")
    assert 'export const API = "/api/apps/floofycrew";' in api and 'credentials: "same-origin"' in api
    code = "\n".join(line for line in api.splitlines() if not line.lstrip().startswith("//"))
    for flag in ("--yes", "--i-accept-the-risk", "--accept-unlisted-source", "--confirm-governance-target"):
        assert flag not in code, f"the client never spells the CLI's answer flag {flag}"
