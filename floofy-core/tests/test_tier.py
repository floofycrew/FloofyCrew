"""The source tier — ``unlisted`` / ``listed`` / ``tested`` — everywhere a mod appears (task 7.10; Requirement 8.11, 14.3).

``floofy_core.tier`` derives the tier from the install record and the running
host's matrix row; ``floofy search`` shows the tier an install would have,
``floofy info`` / ``list`` / ``status`` the tier each installed mod has.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from floofy_core.cli.main import run
from floofy_core.compat import CompatRow
from floofy_core.consent import write_consent
from floofy_core.datahome import DataHome
from floofy_core.modstore import write_source
from floofy_core.tier import TIERS, describe, tier_for_candidate, tier_for_source_kind, tier_of

from floofy_testing import EXAMPLES_DIR, fake_payload
from test_cli_mods import write_mod


def test_tier_of_from_record_and_matrix_row():
    assert TIERS == ("unlisted", "listed", "tested")
    row = CompatRow("internal", "beta", "0.7.0.5", {"loader": "ok"}, {"ex@1.0.0": "tested", "ex@0.9.0": "expected"}, {})
    assert tier_of({}, mod_id="ex", version="1.0.0", compat_row=row) == "unlisted", "no record (a dev link, a hand copy): unlisted even with a tested cell"
    assert tier_of({"source": "path"}, mod_id="ex", version="1.0.0", compat_row=row) == "unlisted"
    assert tier_of({"source": "git", "tier": "unlisted"}, mod_id="ex", version="1.0.0", compat_row=row) == "unlisted"
    assert tier_of({"source": "registry"}, mod_id="ex", version="1.0.0", compat_row=None) == "listed", "a pre-tier record is graded by its source kind"
    assert tier_of({"source": "registry", "tier": "listed"}, mod_id="ex", version="0.9.0", compat_row=row) == "listed", "expected is not tested"
    assert tier_of({"source": "registry", "tier": "listed"}, mod_id="ex", version="1.0.0", compat_row=row) == "tested"
    assert tier_of({"source": "registry", "tier": "listed"}, mod_id="ex", version="1.0.0", compat_row=None) == "listed"
    assert tier_of({"tier": "bogus", "source": "url"}, mod_id="ex", version="1.0.0") == "unlisted"
    assert tier_for_source_kind("registry") == "listed" and tier_for_source_kind("archive") == "unlisted"
    assert tier_for_candidate("tested") == "tested" and tier_for_candidate("expected") == "listed" and tier_for_candidate(None) == "listed"
    assert all(describe(t) for t in TIERS) and "no curator review" in describe("unlisted")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FLOOFY_NO_ADAPTERS", "1")
    monkeypatch.delenv("KIROCREW_HOME", raising=False)
    monkeypatch.setenv("KIRO_HOME", str(tmp_path / "kiro"))
    payload = tmp_path / "payload"
    fake_payload(payload, "0.7.0", build="0.7.0.5")
    home = tmp_path / "home"
    data = DataHome.for_host_home(home).ensure()
    write_consent(data.consent, by="tests", how="test")

    class Env:
        paths = data
        work = tmp_path / "work"

        def run(self, *args: str, **kw):
            return run(["--home", str(home), "--root", str(payload), *args], non_interactive=True, actor="test", **kw)

        def matrix(self, cells: dict[str, str]) -> None:
            data.cache.mkdir(parents=True, exist_ok=True)
            data.compat_cache.write_text(json.dumps({"schema": 1, "rows": [{"edition": "internal", "channel": "beta", "hostVersion": "0.7.0.5", "framework": {"loader": "ok"}, "mods": cells}]}), encoding="utf-8")

        def cache(self, mods: list[dict]) -> None:
            data.cache.mkdir(parents=True, exist_ok=True)
            data.index_cache.write_text(json.dumps({"schema": 1, "source": "test", "generatedAt": "2026-09-20T00:00:00Z", "mods": mods}), encoding="utf-8")

    return Env()


def _entry(mod_id: str, version: str, *, link: bool = False) -> dict:
    files = [{"path": "theme/theme.json", "sha256": "a" * 64, "size": 10}] if link else [{"url": "https://assets.example/x.zip", "sha256": "a" * 64, "size": 10}]
    entry = {"id": mod_id, "name": mod_id.title(), "description": "d", "authors": [], "tags": [], "repo": "https://x", "versions": [{"version": version, "kirocrew": ">=0.7.0 <0.9.0", "editions": ["internal", "external"], "compat": {}, "files": files, "dependencies": {}, "channel": "stable", "publishedAt": "2026-09-20T00:00:00Z"}]}
    if link:
        entry["versions"][0]["link"] = {"repo": "ssh://forge.example/pkg/X", "tag": f"{mod_id}-{version}", "commit": "b" * 40, "manifestSha256": "c" * 64}
    return entry


def test_search_shows_the_tier_an_install_would_have(env):
    env.cache([_entry("alpha", "1.0.0"), _entry("beta", "2.0.0", link=True)])
    plain = env.run("--json", "search")
    assert plain.exit == 0
    by_id = {m["id"]: m for m in plain.json["mods"]}
    assert by_id["alpha"]["tier"] == "listed" and by_id["beta"]["tier"] == "listed" and by_id["beta"]["record"] == "link" and by_id["alpha"]["record"] == "archive"
    assert "tier listed" in plain.stdout or plain.stdout == ""  # --json suppresses prose
    env.matrix({"alpha@1.0.0": "tested", "beta@2.0.0": "expected"})
    graded = env.run("--json", "search")
    by_id = {m["id"]: m for m in graded.json["mods"]}
    assert by_id["alpha"]["tier"] == "tested" and by_id["alpha"]["verdict"] == "tested"
    assert by_id["beta"]["tier"] == "listed" and by_id["beta"]["verdict"] == "expected"
    prose = env.run("search", "alpha")
    assert "tier tested" in prose.stdout


def test_info_list_and_status_show_each_installed_mods_tier(env):
    mod = write_mod(env.work / "alpha", "alpha", parts=[{"kind": "agent", "side": "gateway", "path": "agents/a.json"}], files={"agents/a.json": "{}"})
    assert env.run("--yes", "install", str(mod), "--now").exit == 0
    # a path install is unlisted; a registry record (written as the installer does) is listed; a tested cell lifts it
    info = env.run("--json", "info", "alpha")
    assert info.exit == 0
    installed = info.json["installed"]
    assert installed["tier"] == "unlisted" and installed["source"]["source"] == "path" and "no curator review" in installed["tierMeaning"]
    assert installed["files"] == [{"path": "agents/a.json", "sha256": installed["files"][0]["sha256"]}] and installed["parts"][0]["kind"] == "agent"
    prose = env.run("info", "alpha")
    assert "tier: unlisted" in prose.stdout and "source: path" in prose.stdout and "files (1):" in prose.stdout
    listed = env.run("--json", "list")
    assert listed.json["mods"][0]["tier"] == "unlisted" and listed.json["mods"][0]["source"] == "path"
    status = env.run("--json", "status", "--no-live")
    assert status.json["mods"][0]["tier"] == "unlisted"
    assert "tier=unlisted" in env.run("status", "--no-live").stdout and "tier=unlisted" in env.run("list").stdout
    write_source(env.paths.mods / "alpha", source="registry", ref="alpha@1.0.0", sha256="0" * 64, extra={"registryKey": "alpha", "version": "1.0.0", "tier": "listed"})
    assert env.run("--json", "info", "alpha").json["installed"]["tier"] == "listed"
    env.matrix({"alpha@1.0.0": "tested"})
    tested = env.run("--json", "info", "alpha").json["installed"]
    assert tested["tier"] == "tested" and tested["verdict"] == "tested"
    assert env.run("--json", "list").json["mods"][0]["tier"] == "tested" and env.run("--json", "status", "--no-live").json["mods"][0]["tier"] == "tested"
    # info also reads the registry cache for a mod that is not installed, and refuses an unknown id
    env.cache([_entry("gamma", "3.0.0", link=True)])
    gamma = env.run("--json", "info", "gamma")
    assert gamma.exit == 0 and gamma.json["installed"] is None and gamma.json["registry"]["tier"] == "listed" and gamma.json["registry"]["versions"][0]["link"]["commit"] == "b" * 40
    assert env.run("info", "nope").exit == 1
    assert env.run("--json", "list").exit == 0


def test_git_install_records_the_unlisted_tier_and_list_shows_the_commit(env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FLOOFY_ALLOW_LOCAL_GIT", "1")
    from test_install_git import make_bare_repo  # noqa: PLC0415

    url, commit = make_bare_repo(tmp_path / "repo")
    assert env.run("install", f"{url}@1.0.0", "--accept-unlisted-source", "--now").exit == 0
    row = env.run("--json", "list").json["mods"][0]
    assert row["tier"] == "unlisted" and row["source"] == "git" and row["commit"] == commit
    env.matrix({"example-theme@1.0.0": "tested"})
    assert env.run("--json", "info", "example-theme").json["installed"]["tier"] == "unlisted", "a tested cell never lifts an unlisted install (nobody vouched for THIS checkout)"
    assert f"@{commit[:12]}" in env.run("list").stdout
