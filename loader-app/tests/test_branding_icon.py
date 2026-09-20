"""The App's identity in the host's Apps list (Requirement 16.1; task 11.2).

The Loader app appears as ``FloofyCrew`` with the branding mark: ``app.json``
declares ``iconPath: art/icon.svg``, that file is byte-identical to
``branding/logo.svg`` (the branding is the single source; the copy is pinned
here), and ``scripts/build_loader_app.py`` ships it in the installable tree with
the display name intact.
"""
from __future__ import annotations

import json
from pathlib import Path

from loader_testing import LOADER_APP_DIR, REPO_ROOT, build_loader_app

BRANDING_LOGO = REPO_ROOT / "branding" / "logo.svg"
ICON = LOADER_APP_DIR / "art" / "icon.svg"


def test_app_json_names_the_app_floofycrew_with_the_branding_mark():
    manifest = json.loads((LOADER_APP_DIR / "app.json").read_text(encoding="utf-8"))
    assert manifest["displayName"] == "FloofyCrew"
    assert manifest["iconPath"] == "art/icon.svg" and ICON.is_file()
    assert "unofficial" in manifest["description"].lower() and "not affiliated" in manifest["description"].lower(), "Requirement 11.8: the App says it is unofficial"
    assert manifest["ui"]["entry"] == "index.mjs", "the manager page is the App's landing page (Requirement 16.1)"


def test_the_icon_is_the_branding_logo_byte_for_byte():
    assert BRANDING_LOGO.is_file() and ICON.is_file()
    assert ICON.read_bytes() == BRANDING_LOGO.read_bytes(), "loader-app/art/icon.svg must stay identical to branding/logo.svg (regenerate the copy, never edit it)"
    assert ICON.read_bytes().lstrip().startswith(b"<svg") or b"<svg" in ICON.read_bytes()[:400]


def test_build_ships_the_icon_and_the_display_name(tmp_path: Path):
    out = build_loader_app(tmp_path / "floofycrew")
    assert (out / "art" / "icon.svg").read_bytes() == BRANDING_LOGO.read_bytes()
    built = json.loads((out / "app.json").read_text(encoding="utf-8"))
    assert built["displayName"] == "FloofyCrew" and built["iconPath"] == "art/icon.svg"
    assert (out / "ui" / "index.mjs").is_file() and (out / "ui" / "app" / "palette.mjs").is_file(), "the shell and its page modules ship under ui/"
