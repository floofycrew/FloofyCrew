"""A tag build on GitHub has no Forge beside it: ``packaging/public/build.py`` without ``--supports``
must still ship the ``[tool.floofycrew.supports]`` table, or every public host reads
"does not list your host" and ``floofy self-update`` stops (seen on public 0.9.0 with 1.3.7)."""
from __future__ import annotations

import importlib.util
import json
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD = REPO_ROOT / "packaging" / "public" / "build.py"


def _load():
    spec = importlib.util.spec_from_file_location("supports_default_public_build", BUILD)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_pyproject_supports_is_the_table_in_pyproject():
    build = _load()
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        table = tomllib.load(handle)["tool"]["floofycrew"]["supports"]
    supports = build.pyproject_supports()
    assert supports == table
    assert "external" in supports and "internal" in supports, "both editions are listed"
    for edition, channels in supports.items():
        for channel, versions in channels.items():
            assert versions, f"{edition}/{channel} names at least one host version"


def test_a_build_without_supports_ships_the_pyproject_table(tmp_path: Path):
    build = _load()
    out = tmp_path / "public"
    rc = build.main(["--out", str(out), "--tag", "v-test", "--json"])
    assert rc == 0
    document = json.loads((out / "dist" / "supports.json").read_text(encoding="utf-8"))
    assert document["supports"] == build.pyproject_supports()
    notes = (out / "RELEASE.md").read_text(encoding="utf-8")
    assert "not recorded in this build" not in notes
    assert "- external/stable: " in notes and "- internal/stable: " in notes


def test_the_release_names_the_current_public_stable_host():
    """The feed's current public stable (0.9.0 since 2026-10-16) is in the list the GitHub release ships."""
    build = _load()
    assert "0.9.0" in build.pyproject_supports()["external"]["stable"]
