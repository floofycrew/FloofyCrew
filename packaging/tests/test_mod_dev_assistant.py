"""The contributor assistant for mods: the ``floofy-mod-dev`` skill, its agent source, the sync script that
materialises the agent, and the first-landing pages that lead to it.

The skill is the one copy on both editions (an edition adds an ``INTERNAL.md`` beside it, never a second
skill), so these tests keep it edition-neutral: every repository path it names exists, no edition-specific
hosting or registry sneaks in, the agent loads it and reads an addendum when one is there, the sync script
puts the agent where kiro-cli reads it, and a would-be mod author lands on the agent from the README, the
docs index, the mods index, the publishing page and CONTRIBUTING.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL = REPO_ROOT / ".kiro" / "skills" / "floofy-mod-dev" / "SKILL.md"
AGENT = REPO_ROOT / "editions" / "public" / "kiro" / "agents" / "floofy-mod-dev.json"
SYNC = REPO_ROOT / "scripts" / "kiro-workspace-sync.sh"
LANDING = {
    "README.md": REPO_ROOT / "README.md",
    "docs/README.md": REPO_ROOT / "docs" / "README.md",
    "mods/README.md": REPO_ROOT / "mods" / "README.md",
    "docs/publishing.md": REPO_ROOT / "docs" / "publishing.md",
    "CONTRIBUTING.md": REPO_ROOT / "CONTRIBUTING.md",
}

_PATH_TOKEN = re.compile(r"`([A-Za-z0-9_.][A-Za-z0-9_./-]*/[A-Za-z0-9_./-]*)`")
#: Documented generated locations, placeholders, a scaffolded mod's own files, and the edition addendum that is
#: absent from this tree by design: not files of the tree.
ADDENDUM = ".kiro/skills/floofy-mod-dev/INTERNAL.md"
_NOT_FILES = (".kiro/agents", "mods/<id>", "pending/", "~/", "<", ".github/workflows/floofy-validate.yml", ADDENDUM)


def _paths(text: str) -> set[str]:
    found = set()
    for token in _PATH_TOKEN.findall(text):
        if token.startswith(("http", "../")) or any(ch in token for ch in "<>{}*$…") or token.startswith(_NOT_FILES):
            continue
        found.add(token.rstrip("/"))
    return found


def test_the_skill_is_edition_neutral_and_names_only_paths_that_exist() -> None:
    text = SKILL.read_text(encoding="utf-8")
    front = text.split("---", 2)[1]
    assert re.search(r"^name:\s*floofy-mod-dev\s*$", front, re.M)
    assert "INTERNAL.md" in text, "the skill says how an edition extends it: an addendum beside it"
    assert not (REPO_ROOT / ADDENDUM).exists(), "the public tree carries no edition addendum; an edition's overlay does"
    assert "floofycrew-registry" in text and "floofy install https://github.com/" in text, "both publish paths, the public way"
    missing = sorted(p for p in _paths(text) if not (REPO_ROOT / p).exists())
    assert missing == [], f"the skill names paths that do not exist: {missing}"
    # the examples it points at exist as the kinds it claims
    for mod, kinds in {"settings-demo": {"python-hook", "ui"}, "custom-themes": {"patch", "python-hook", "spa", "ui"}, "display-comfort": {"spa", "ui"}, "agent-orchestrator": {"python-hook", "ui"}}.items():
        manifest = json.loads((REPO_ROOT / "mods" / mod / "floofy.json").read_text(encoding="utf-8"))
        assert {part["kind"] for part in manifest["parts"]} == kinds, mod


def test_the_agent_loads_the_skill_and_the_addendum_and_keeps_the_irreversible_steps_for_the_developer() -> None:
    agent = json.loads(AGENT.read_text(encoding="utf-8"))
    assert agent["name"] == "floofy-mod-dev"
    assert "`floofy-mod-dev` skill" in agent["prompt"] and "INTERNAL.md" in agent["prompt"]
    assert "skill://.kiro/skills/*/SKILL.md" in agent["resources"]
    assert any(r.startswith("file://.kiro/skills/floofy-mod-dev/INTERNAL") for r in agent["resources"])
    assert agent["mcpServers"] == {}, "the public agent needs no MCP server"
    rule = next(r for r in agent["permissions"]["rules"] if r["capability"] == "shell")
    denied = [c for c in ("git push origin main", "gh pr create --fill", "gh release create x", "git tag v1", "gh repo create x") if any(re.fullmatch(p, c) for p in rule["deniedCommands"])]
    assert len(denied) == 5, "pushes, pull requests, releases, tags and repository creation are the developer's"
    for admitted in ("floofy validate my-mod", "floofy dev my-mod --follow", "git status", "gh pr view 12"):
        assert not any(re.fullmatch(p, admitted) for p in rule["deniedCommands"]), admitted
    missing = sorted(p for p in _paths(agent["prompt"] + " " + agent["description"]) if not (REPO_ROOT / p).exists())
    assert missing == [], f"the agent prompt names paths that do not exist: {missing}"


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("sha256sum") is None, reason="needs bash and sha256sum")
def test_the_sync_script_materialises_the_agent_once_and_reports_drift(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    (clone / "editions" / "public" / "kiro" / "agents").mkdir(parents=True)
    (clone / "scripts").mkdir()
    shutil.copy(AGENT, clone / "editions" / "public" / "kiro" / "agents" / AGENT.name)
    shutil.copy(SYNC, clone / "scripts" / SYNC.name)
    env = {**os.environ, "LC_ALL": "C"}

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(clone / "scripts" / SYNC.name), *args], cwd=clone, capture_output=True, text=True, env=env, check=False, timeout=60)

    assert run("--check").returncode == 1, "a fresh clone needs a sync"
    first = run()
    assert first.returncode == 0 and "synced:" in first.stdout, first.stdout + first.stderr
    copy = clone / ".kiro" / "agents" / AGENT.name
    assert json.loads(copy.read_text(encoding="utf-8"))["name"] == "floofy-mod-dev"
    assert run("--check").returncode == 0
    # a session rewrites the copy: still current (compared through the sidecar, not the live file)
    copy.write_text(copy.read_text(encoding="utf-8").replace("}\n", "}\n", 1) + "\n", encoding="utf-8")
    assert run("--check").returncode == 0 and "current:" in run().stdout
    # the source changes: a sync is needed, and done
    source = clone / "editions" / "public" / "kiro" / "agents" / AGENT.name
    source.write_text(source.read_text(encoding="utf-8").replace("mod-development assistant", "mod-development assistant (v2)", 1), encoding="utf-8")
    assert run("--check").returncode == 1
    assert "source changed" in run().stdout
    assert "(v2)" in copy.read_text(encoding="utf-8")


def test_every_first_landing_page_leads_a_mod_author_to_the_agent() -> None:
    """A helper nobody is sent to is a helper nobody uses: the README, the docs index, the mods index, the
    publishing page and CONTRIBUTING all name the agent and the sync script."""
    for name, path in LANDING.items():
        text = path.read_text(encoding="utf-8")
        assert "floofy-mod-dev" in text and "kiro-workspace-sync.sh" in text, f"{name} does not lead to the agent"
    contributing = LANDING["CONTRIBUTING.md"].read_text(encoding="utf-8")
    assert "### The mod-dev agent" in contributing
    assert ".kiro/skills/floofy-mod-dev/SKILL.md" in contributing and "editions/public/kiro/agents/floofy-mod-dev.json" in contributing
    assert "INTERNAL.md" in contributing, "the guide says how the internal edition extends the one skill"
    assert ".kiro/agents/" in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8"), "the copies stay untracked"
