"""The public edition's trigger set (task 6.4; Requirement 6.1): the hourly user timer plus the PATH wrapper for venv installs."""
from __future__ import annotations

from pathlib import Path

import floofy_edition_public
from floofy_core.payloads import make_payload
from floofy_core.triggers import trigger_managers
from floofy_edition_public.triggers import venv_launcher

from floofy_testing import fake_payload

COMMAND = ["/usr/bin/env", "python3.12", "/opt/floofy.pyz", "--home", "/h"]


def test_public_edition_adds_the_path_wrapper_for_venv_payloads(tmp_path: Path):
    venv_root = tmp_path / "crew-venv"
    package_dir = fake_payload(venv_root, "0.7.0", layout="venv")
    (venv_root / "bin" / "kirocrew").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    payload = make_payload(kind="venv", root=venv_root, package_dir=package_dir, source="test", edition="external")
    assert venv_launcher(payload) == venv_root / "bin" / "kirocrew"
    managers = trigger_managers(tmp_path / "home", [floofy_edition_public], command=COMMAND, edition="external", payload=payload, unit_dir=tmp_path / "units", bin_dir=tmp_path / "bin", activate=False, platform="darwin")
    assert [m.kind for m in managers] == ["user-timer", "path-wrapper"]
    timer, wrapper = managers
    assert timer.platform == "darwin" and timer.environment == {"FLOOFY_ACTOR": "trigger"}
    report = timer.install()
    assert report.ok and report.paths[0].endswith("dev.floofycrew.reapply.plist")
    installed = wrapper.install()
    assert installed.ok and installed.changed and (tmp_path / "bin" / "kirocrew").is_file()
    assert floofy_edition_public.early_shim_kind(payload) == "venv-site"
    # a desktop bundle (no pyvenv.cfg): the wrapper is not applicable
    flat_dir = fake_payload(tmp_path / "flat", "0.7.0")
    flat = make_payload(kind="app", root=tmp_path / "flat", package_dir=flat_dir, source="test", edition="external")
    assert venv_launcher(flat) is None
    only_timer = trigger_managers(tmp_path / "home", [floofy_edition_public], command=COMMAND, edition="external", payload=flat, unit_dir=tmp_path / "u2", bin_dir=tmp_path / "b2", activate=False)
    assert only_timer[1].install().changed is False
    assert floofy_edition_public.early_shim_kind(flat) == "user-site"
