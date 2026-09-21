"""``app-registry.json`` for app-kind mods (task 7.4, 7.6; Requirement 8.6): emission, staleness check, and acceptance by the host's own reader."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from floofy_core.scaffold import refresh_files

from registry_tools.app_registry import app_registry_matches, app_registry_rows, is_git_url, write_app_registry
from registry_tools.build import build_index
from registry_tools.cli import main
from registry_tools.repo import RegistryRepo

from registry_testing import REPO_ROOT, ScratchRegistry

pytestmark = pytest.mark.registry


def _licensed(registry: ScratchRegistry, kind: str, target: Path, **overrides) -> Path:
    root = registry.copy_example(kind, target)
    (root / "LICENSE").write_text("MIT\n", encoding="utf-8")
    manifest = json.loads((root / "floofy.json").read_text(encoding="utf-8"))
    manifest.update(overrides)
    (root / "floofy.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    refresh_files(root)
    return root


@pytest.fixture
def registry(tmp_path: Path) -> ScratchRegistry:
    scratch = ScratchRegistry(tmp_path / "registry")
    scratch.add_version(_licensed(scratch, "app", tmp_path / "m" / "example-app"), repo="https://git.example/mods/example-app")
    scratch.add_version(_licensed(scratch, "app", tmp_path / "m" / "example-app-2", version="1.1.0"), repo="https://git.example/mods/example-app", release_extra={"tag": "v1.1.0", "app": {"name": "example-app"}})
    scratch.add_version(_licensed(scratch, "app", tmp_path / "m" / "example-app-3", version="2.0.0"), repo="https://git.example/mods/example-app", release_extra={"yanked": "pulled"})
    scratch.add_version(_licensed(scratch, "theme", tmp_path / "m" / "example-theme"))
    scratch.add_version(_licensed(scratch, "app", tmp_path / "m" / "ssh-app", id="ssh-app", name="SSH app"), repo="ssh://git.example.internal/pkg/SshApp")
    scratch.add_version(_licensed(scratch, "app", tmp_path / "m" / "bare-app", id="bare-app", name="Bare app"), repo="not a git url")
    return scratch


def test_rows_follow_the_host_shape_and_skip_what_the_host_cannot_clone(registry: ScratchRegistry):
    repo = RegistryRepo.load(registry.root)
    result = build_index(repo)
    assert result.ok, result.problems
    rows, notes = app_registry_rows(result.index)
    assert rows == [
        {"name": "example-app", "gitUrl": "https://git.example/mods/example-app", "repo": "https://git.example/mods/example-app", "branch": "v1.1.0", "subdirectory": "app"},
        {"name": "ssh-app", "gitUrl": "ssh://git.example.internal/pkg/SshApp", "repo": "ssh://git.example.internal/pkg/SshApp", "branch": "1.0.0", "subdirectory": "app"},
    ], "the newest non-yanked app version, pinned to its release tag; the theme has no row"
    assert notes == ["bare-app: repo 'not a git url' is not a cloneable https/ssh git URL; no app row"]
    assert is_git_url("git@git.example:org/app.git") and not is_git_url("http://git.example/x/y") and not is_git_url("https://git.example/x/../y")
    path, written = write_app_registry(repo, result.index)
    assert path == registry.root / "app-registry.json" and json.loads(path.read_text(encoding="utf-8")) == written == rows
    assert app_registry_matches(repo, result.index)[0]
    path.write_text("[]\n", encoding="utf-8")
    ok, message = app_registry_matches(repo, result.index)
    assert not ok and "stale" in message
    # through the CLI: build --app-registry writes both; --check --app-registry catches a stale file
    assert main(["build", str(registry.root), "--app-registry"]) == 0
    assert main(["build", str(registry.root), "--check", "--app-registry"]) == 0
    path.write_text("[]\n", encoding="utf-8")
    assert main(["build", str(registry.root), "--check", "--app-registry"]) == 1
    assert main(["app-registry", str(registry.root), "--out", str(registry.root / "elsewhere.json")]) == 0
    assert json.loads((registry.root / "elsewhere.json").read_text(encoding="utf-8")) == rows
    # a registry without app-kind mods needs no file
    empty = ScratchRegistry(registry.root.parent / "empty")
    assert app_registry_matches(RegistryRepo.load(empty.root), {"mods": []}) == (True, f"{empty.root / 'app-registry.json'} not needed: no app-kind mods")


def _payload_interpreter() -> tuple[Path, Path] | None:
    """The scratch payload copy's interpreter and site-packages (the host's reader), or ``None`` when absent."""
    payload = Path(os.environ.get("FLOOFY_SCRATCH_PAYLOAD") or REPO_ROOT / ".scratch" / "payload-0.7.0.5")
    interpreters = sorted(payload.glob("bin/python3.*"))
    interpreters = [p for p in interpreters if p.is_file() and not p.name.endswith("-config")]
    if not interpreters:
        return None
    interpreter = interpreters[-1]
    for site in payload.glob("lib/python3.*/site-packages"):
        if (site / "kiro_crew" / "apps" / "registry.py").is_file():
            return interpreter, site
    return None


HOST_READER_SCRIPT = r"""
import json, re, sys
import kiro_crew.apps.registry as registry  # the host's own reader (0.7.0.5)
rows = json.loads(open(sys.argv[1], encoding="utf-8").read())
assert isinstance(rows, list), "the host requires a JSON array (registry.py L3154)"
entries = registry._credential_free_external_registry_entries([item for item in rows if isinstance(item, dict)])
out = []
for entry in entries:
    git_url = registry._entry_git_url(entry)
    assert git_url and registry._is_supported_registry_transport(git_url), git_url
    branch = entry.get("branch", "main")
    assert re.match(r"^[A-Za-z0-9][A-Za-z0-9_\-./]*$", branch) and ".." not in branch, branch
    origin, ref, subdirectory, name = registry._manifest_source_coordinates(entry)
    out.append({"name": name, "gitUrl": git_url, "ref": ref, "subdirectory": subdirectory, "origin": origin})
bundled = json.loads(open(registry._REGISTRY_FILE, encoding="utf-8").read())
print(json.dumps({"entries": out, "bundledKeys": sorted({k for e in bundled for k in e}), "ourKeys": sorted({k for e in rows for k in e})}))
"""


def test_emitted_rows_are_accepted_by_the_hosts_reader(registry: ScratchRegistry, tmp_path: Path):
    found = _payload_interpreter()
    if found is None:
        pytest.skip("no scratch payload copy (.scratch/payload-0.7.0.5) to borrow the host's reader from")
    interpreter, site = found
    repo = RegistryRepo.load(registry.root)
    path, rows = write_app_registry(repo, build_index(repo).index)
    scratch_home = tmp_path / "home"
    env = {**os.environ, "PYTHONPATH": str(site), "KIROCREW_HOME": str(scratch_home), "KIRO_HOME": str(tmp_path / "kiro"), "KIROCREW_SKIP_MODEL_DOWNLOAD": "1"}
    completed = subprocess.run([str(interpreter), "-c", HOST_READER_SCRIPT, str(path)], env=env, capture_output=True, text=True, timeout=120, cwd=str(tmp_path))
    assert completed.returncode == 0, completed.stderr[-2000:]
    parsed = json.loads(completed.stdout.strip().splitlines()[-1])
    assert [e["name"] for e in parsed["entries"]] == ["example-app", "ssh-app"]
    assert parsed["entries"][0] == {"name": "example-app", "gitUrl": "https://git.example/mods/example-app", "ref": "branch:v1.1.0", "subdirectory": "app", "origin": "https://git.example/mods/example-app"}
    assert set(parsed["ourKeys"]) >= set(parsed["bundledKeys"]), "our rows carry every key the host's bundled seed uses"
    assert set(parsed["ourKeys"]) - set(parsed["bundledKeys"]) <= {"subdirectory", "commit"}
