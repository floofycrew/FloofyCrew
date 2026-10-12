"""The discovery module against a fake home: npm prefixes the host does not search."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hook import discovery as D  # noqa: E402


def _exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def _tree(prefix: Path, package: str, *, with_sdk: bool = True, with_node: bool = True, launcher: str | None = None, nested: bool = False) -> Path:
    root = prefix / "lib" / "node_modules"
    entry = root.joinpath(*package.split("/"), "dist", "index.js")
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("export {};\n")
    if with_sdk and nested:  # the real `npm i -g` layout: the dependency inside the package's own node_modules
        root.joinpath(*package.split("/"), "node_modules", "@agentclientprotocol", "sdk").mkdir(parents=True, exist_ok=True)
    elif with_sdk:           # the hoisted / vendored layout the host itself checks
        (root / "@agentclientprotocol" / "sdk").mkdir(parents=True, exist_ok=True)
    if with_node:
        _exe(prefix / "bin" / "node")
    if launcher:
        _exe(prefix / "bin" / launcher)
    return entry


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


def test_finds_codex_acp_under_local_node_current_with_its_own_node(home: Path):
    prefix = home / ".local" / "node" / "node-v24"
    entry = _tree(prefix, "@agentclientprotocol/codex-acp", launcher="codex-acp")
    (home / ".local" / "node" / "current").symlink_to(prefix, target_is_directory=True)
    found = D.find_adapter("codex-acp", home=str(home), search_path="/usr/bin", environ={"PATH": "/usr/bin"})
    assert found is not None
    assert found.argv == [str(prefix / "bin" / "node"), str(entry.resolve())]
    assert found.rung in ("the tree node lives in", "~/.local/node/current") and os.path.realpath(found.prefix) == str(prefix)


def test_incomplete_tree_without_the_sdk_is_skipped(home: Path):
    _tree(home / ".npm-global", "@agentclientprotocol/claude-agent-acp", with_sdk=False)
    assert D.find_adapter("claude-agent-acp", home=str(home), environ={"PATH": ""}) is None


def test_npmrc_prefix_and_env_prefix_rungs(home: Path, tmp_path: Path):
    custom = tmp_path / "npm-here"
    _tree(custom, "pi-acp")
    (home / ".npmrc").write_text("# comment\nalways-auth=false\nprefix = %s\n" % custom)
    found = D.find_adapter("pi-acp", home=str(home), environ={"PATH": ""})
    assert found is not None and found.rung == "~/.npmrc prefix" and found.prefix == str(custom)
    other = tmp_path / "env-prefix"
    _tree(other, "pi-acp")
    found2 = D.find_adapter("pi-acp", home=str(home), environ={"PATH": "", "NPM_CONFIG_PREFIX": str(other)})
    assert found2 is not None and found2.rung == "NPM_CONFIG_PREFIX"


def test_node_from_the_search_path_is_used_when_the_prefix_ships_none(home: Path, tmp_path: Path):
    node_dir = tmp_path / "nodebin"
    node = _exe(node_dir / "node")
    entry = _tree(home / ".npm-global", "@agentclientprotocol/claude-agent-acp", with_node=False)
    found = D.find_adapter("claude-agent-acp", home=str(home), search_path=str(node_dir), environ={"PATH": ""})
    assert found is not None and found.argv == [str(node), str(entry.resolve())]


def test_no_node_anywhere_means_no_answer(home: Path):
    _tree(home / ".npm-global", "@agentclientprotocol/claude-agent-acp", with_node=False)
    assert D.find_adapter("claude-agent-acp", home=str(home), search_path="", environ={"PATH": ""}) is None


def test_the_tree_node_lives_in_is_a_rung_even_when_unlisted(home: Path, tmp_path: Path):
    prefix = tmp_path / "somewhere" / "node-24"
    entry = _tree(prefix, "@agentclientprotocol/codex-acp")
    found = D.find_adapter("codex-acp", home=str(home), search_path=str(prefix / "bin"), environ={"PATH": ""})
    assert found is not None and found.rung == "the tree node lives in" and found.entry == str(entry.resolve())


def test_launcher_candidates_and_on_search_path(home: Path):
    prefix = home / ".local" / "node"
    _tree(prefix, "@agentclientprotocol/codex-acp", launcher="codex-acp")
    assert D.launcher_candidates("codex-acp", home=str(home), environ={"PATH": ""}) == [str(prefix / "bin" / "codex-acp")]
    assert D.on_search_path("codex-acp", str(prefix / "bin")) == str(prefix / "bin" / "codex-acp")
    assert D.on_search_path("codex-acp", "/nonexistent") is None
    assert D.launcher_candidates("opencode", home=str(home), environ={"PATH": ""}) == []


def test_candidate_prefixes_dedupe_through_symlinks(home: Path):
    real = home / ".local" / "node" / "v24"
    _exe(real / "bin" / "node")
    (home / ".local" / "node" / "current").symlink_to(real, target_is_directory=True)
    pairs = D.candidate_prefixes(str(home), {"PATH": ""}, node_on_path=str(home / ".local" / "node" / "current" / "bin" / "node"))
    prefixes = [p for _, p in pairs]
    assert len(prefixes) == len({os.path.realpath(p) for p in prefixes})
    assert any(r == "the tree node lives in" for r, _ in pairs)


def test_global_npm_layout_with_the_sdk_nested_inside_the_package_is_found(home: Path):
    """The sandbox's real tree: lib/node_modules/@agentclientprotocol/codex-acp/node_modules/@agentclientprotocol/sdk, nothing hoisted."""
    prefix = home / ".local" / "node" / "node-v24.21.0-linux-x64"
    entry = _tree(prefix, "@agentclientprotocol/codex-acp", nested=True, launcher="codex-acp")
    assert not (prefix / "lib" / "node_modules" / "@agentclientprotocol" / "sdk").exists()
    found = D.find_adapter("codex-acp", home=str(home), search_path="", environ={"PATH": ""})
    assert found is not None and found.entry == str(entry.resolve()) and found.node == str(prefix / "bin" / "node")
    assert D.dependency_satisfied(str(prefix / "lib" / "node_modules"), "@agentclientprotocol/codex-acp") == str(prefix / "lib" / "node_modules" / "@agentclientprotocol" / "codex-acp" / "node_modules" / "@agentclientprotocol" / "sdk")
