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
    for command in ("floofy init", "floofy deinit", "floofy apply", "floofy restore --all", "floofy verify", "floofy hold", "floofy dev", "floofy new", "floofy validate", "kirocrew restart"):
        assert command in settings, command
    assert "kirocrew gateway restart" not in settings, "the host's verb is `kirocrew restart`; `gateway` has no restart action"
    for page, command in (("mods.mjs", "floofy install"), ("registry.mjs", "floofy registry add"), ("profiles.mjs", "floofy profile"), ("doctor.mjs", "floofy doctor"), ("audit.mjs", "floofy audit")):
        assert command in (UI / "app" / "pages" / page).read_text(encoding="utf-8"), (page, command)


def test_tab_styles_use_per_side_border_colors_only():
    """1.1.6 kept a tab's frame after re-selection; the follow-up: the active tab grew a BOTTOM border.

    React diffs inline styles key by key and writes only what changed: a 4-side
    `borderColor` write on inactive→active repaints all four sides while
    `borderBottomColor` compares equal and is not re-written, so the shorthand's
    bottom value wins and the active tab shows a bottom border after any tab
    switch (the first render was fine, which is why it looked intermittent).
    Border colours are therefore spelled per side, in BOTH states, and no
    4-side `border`/`borderColor` key is allowed on either.
    """
    text = (UI / "app" / "palette.mjs").read_text(encoding="utf-8")
    item = re.search(r"navItem: \{(.*?)\},\n", text)
    active = re.search(r"navItemActive: \{(.*?)\},\n", text)
    assert item and active
    item_keys = set(re.findall(r"(\w+): ", item.group(1)))
    active_keys = set(re.findall(r"(\w+): ", active.group(1)))
    sides = {"borderTopColor", "borderRightColor", "borderLeftColor", "borderBottomColor"}
    for keys in (item_keys, active_keys):
        assert "border" not in keys and "borderColor" not in keys, "no shorthand and no 4-side colour key: per-side longhands only"
        assert sides <= keys, sides - keys
    assert {"borderWidth", "borderStyle", "fontWeight", "color"} <= item_keys
    assert active_keys <= item_keys, active_keys - item_keys
    # the active tab never paints a bottom border: it must stay transparent in both states
    assert 'borderBottomColor: "transparent"' in item.group(1) and 'borderBottomColor: "transparent"' in active.group(1)


def test_install_form_defaults_to_enabled_and_the_manual_yeet_button_is_gone():
    """A confirmed install lands enabled (Requirement 11.7): the form's only switch is the plain-language
    "install switched off"; the jargon "enable code parts right away" and the manual Yeet button are gone
    (Disable/Uninstall cover the user-facing need; the automatic quarantine and its Restore stay)."""
    mods = (UI / "app" / "pages" / "mods.mjs").read_text(encoding="utf-8")
    assert "floofycrew-install-disabled" in mods and "install switched off" in mods
    assert "enable code parts right away" not in mods
    assert '"Yeet"' not in mods, "no manual yeet button; the terminal equivalent is shown instead"
    assert "floofy yeet <id>" in mods, "Requirement 16.2: a capability the App lacks is shown with the command"
    assert '"Restore set"' in mods, "the quarantine's restore stays"


def test_update_check_shows_progress_and_a_result_line():
    """Registry › Check for updates: a busy label while the check runs and a one-line verdict after it."""
    registry = (UI / "app" / "pages" / "registry.mjs").read_text(encoding="utf-8")
    assert "floofycrew-update-check-busy" in registry and "floofycrew-update-check-result" in registry
    assert '"Checking…"' in registry and "check failed" in registry


def test_the_landing_page_reminds_about_mod_updates():
    """Requirement 7.7: the Mods page shows a mod-updates notice (the trigger keeps the data fresh unattended)."""
    mods = (UI / "app" / "pages" / "mods.mjs").read_text(encoding="utf-8")
    assert "floofycrew-mod-updates-notice" in mods and "floofycrew-mod-updates-open" in mods
    assert 'navigate("registry")' in mods, "the notice hands the user to the Registry page for the actions"


def test_the_restart_and_update_buttons_reach_their_routes_and_the_pages_place_them():
    """1.1.6: the red restart button and 'Update & restart' — POST /host/restart and POST /self-update, always behind a question."""
    actions = (UI / "app" / "actions.mjs").read_text(encoding="utf-8")
    assert "manager.restartHost()" in actions and "manager.selfUpdateAndRestart()" in actions
    assert "styles.restart" in actions, "the restart button is the filled red one"
    palette = (UI / "app" / "palette.mjs").read_text(encoding="utf-8")
    assert re.search(r"restart: \{ background: PALETTE\.danger", palette)
    shell = (UI / "index.mjs").read_text(encoding="utf-8")
    assert 'callRoute("/host/restart", {}, { ask })' in shell, "the restart asks through the protocol; no answer is invented"
    assert 'act("/self-update", { now: true })' in shell
    assert "confirmations" not in shell.split("const restartHost")[1].split("return { state")[0], "the shell never pre-answers the restart question"
    for page, testids in (("mods.mjs", ("floofycrew-restart-pending", "UpdateAndRestartButton")), ("settings.mjs", ("floofycrew-restart-settings", "floofycrew-restart-card")), ("registry.mjs", ("floofycrew-self-update-restart-registry",))):
        text = (UI / "app" / "pages" / page).read_text(encoding="utf-8")
        for needle in testids:
            assert needle in text, (page, needle)
    assert "floofycrew-restart-header" in shell


def test_pages_are_columns_of_cards_and_the_sources_are_a_table():
    """1.1.6: the shell's page section is a flex column with the card gap, the page ends with room below, Sources is a table, Profiles explains itself."""
    palette = (UI / "app" / "palette.mjs").read_text(encoding="utf-8")
    assert re.search(r'pageBody: \{ display: "flex", flexDirection: "column", gap: "1\.25rem" \}', palette)
    assert re.search(r'page: \{ padding: "1\.25rem 1\.25rem 6rem"', palette), "room under the footer"
    shell = (UI / "index.mjs").read_text(encoding="utf-8")
    assert 'React.createElement("section", { style: styles.pageBody' in shell
    registry = (UI / "app" / "pages" / "registry.mjs").read_text(encoding="utf-8")
    assert '"table"' in registry and "floofycrew-sources-table" in registry and '["Source", "Trust", "Signature policy", "Last refresh", "Actions"]' in registry
    profiles = (UI / "app" / "pages" / "profiles.mjs").read_text(encoding="utf-8")
    assert "export const PROFILES_INTRO" in profiles and "floofycrew-profiles-intro" in profiles


def test_corners_are_squared_footer_and_banner_preference():
    """Squared corners everywhere (one RADIUS ≤ 4px, no pill badges, no rounded modal), the footer with the author, the banner's
    "don't show this again" that leaves the unofficial statement in the footer (Requirement 11.8)."""
    palette = (UI / "app" / "palette.mjs").read_text(encoding="utf-8")
    radius = re.search(r'export const RADIUS = "(\d+)px";', palette)
    assert radius and int(radius.group(1)) <= 4, "the one radius is squared"
    for path in modules():
        text = path.read_text(encoding="utf-8")
        for value in re.findall(r"borderRadius: ([^,}]+)", text):
            assert "RADIUS" in value, f"{path.name}: a corner not on the shared radius: {value}"
    assert "footer:" in palette and "bannerDismiss:" in palette
    shell = (UI / "index.mjs").read_text(encoding="utf-8")
    assert 'export const AUTHOR = "Oscar Tseng";' in shell
    assert '"footer"' in shell and "floofycrew-footer-author" in shell and "`author: ${AUTHOR}`" in shell
    footer = shell.split("function Footer")[1].split("function Nav")[0]
    assert "UNOFFICIAL" in footer, "the footer carries the unofficial statement, so the dismissed banner never hides it"
    assert "floofycrew-banner-dismiss" in shell and 'localStorage.getItem(BANNER_PREFERENCE_KEY) === "1"' in shell
    settings = (UI / "app" / "pages" / "settings.mjs").read_text(encoding="utf-8")
    assert "floofycrew-banner-toggle" in settings and "banner.show" in settings, "Settings brings the banner back"
    assert re.search(r'page: \{ padding: "1\.25rem 1\.25rem 6rem"', palette), "room under the footer"


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
