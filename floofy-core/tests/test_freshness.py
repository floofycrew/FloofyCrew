"""The re-apply trigger's unattended freshness pass (Requirement 7.7; :mod:`floofy_core.freshness`).

The trigger's ``floofy apply --if-changed`` keeps the App's update reminders
fresh: the daily FloofyCrew release check (its own 24 h cache) and a registry
refresh at most once a day — best-effort, never failing the trigger, honouring
``updates.check``, and never fetching a registry the user has not fetched once
themselves.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from floofy_core import freshness
from floofy_core.datahome import DataHome
from floofy_core.registry_sources import SourceStore


class FakeAudit:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record(self, op: str, **fields) -> None:
        self.rows.append({"op": op, **fields})


class FakeCtx:
    """Exactly the surface :func:`freshness.run` touches."""

    def __init__(self, home: DataHome, *, offline: bool = False, settings: dict | None = None) -> None:
        self.home = home
        self.offline = offline
        self._settings = {"updates.check": True, **(settings or {})}
        self.audit = FakeAudit()

    def settings(self):
        return dict(self._settings)

    def url_opener(self, url: str):
        return None


@pytest.fixture()
def home(tmp_path: Path) -> DataHome:
    return DataHome.for_host_home(tmp_path / "h").ensure()


def _add_source(home: DataHome, url: str = "https://registry.example/dir/") -> None:
    home.registries.write_text(json.dumps({"schema": 1, "sources": [{"url": url, "trust": "index", "allowUnsigned": False}]}), encoding="utf-8")


def _write_meta(home: DataHome, *, age_seconds: float) -> None:
    store = SourceStore.load(home)
    key = store.sources[0].key
    directory = home.cache_sources / key
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(time.time() - age_seconds, tz=timezone.utc).isoformat()
    (directory / "meta.json").write_text(json.dumps({"fetchedAt": stamp}), encoding="utf-8")


def _spies(monkeypatch: pytest.MonkeyPatch) -> tuple[list, list]:
    checks: list = []
    refreshes: list = []

    def fake_update_check(ctx, **kw):
        checks.append(kw)
        return SimpleNamespace(status="ok")

    def fake_refresh(home, store, **kw):
        refreshes.append(kw)
        return SimpleNamespace(merged_mods=3, usable=["a"], refused=[], decisions=lambda: [])

    import floofy_core.cli.cmd_selfupdate as cs
    import floofy_core.registry_sources as rs

    monkeypatch.setattr(cs, "update_check", fake_update_check)
    monkeypatch.setattr(rs, "refresh", fake_refresh)
    return checks, refreshes


def test_runs_the_check_and_refreshes_a_stale_source(home: DataHome, monkeypatch: pytest.MonkeyPatch):
    checks, refreshes = _spies(monkeypatch)
    _add_source(home)
    _write_meta(home, age_seconds=freshness.REFRESH_TTL + 60)
    ctx = FakeCtx(home)
    report = freshness.run(ctx)
    assert report["selfUpdate"] == "ok" and report["registry"].startswith("refreshed: 3 mod(s)")
    assert len(checks) == 1 and len(refreshes) == 1
    assert ctx.audit.rows and ctx.audit.rows[-1]["op"] == "registry-refresh" and "unattended" in ctx.audit.rows[-1]["detail"]


def test_a_fresh_source_is_not_refetched(home: DataHome, monkeypatch: pytest.MonkeyPatch):
    checks, refreshes = _spies(monkeypatch)
    _add_source(home)
    _write_meta(home, age_seconds=60)
    report = freshness.run(FakeCtx(home))
    assert len(checks) == 1, "the release check always runs (it carries its own 24 h cache)"
    assert not refreshes and report["registry"].startswith("fresh")


def test_a_never_fetched_source_waits_for_the_users_first_refresh(home: DataHome, monkeypatch: pytest.MonkeyPatch):
    _, refreshes = _spies(monkeypatch)
    _add_source(home)
    report = freshness.run(FakeCtx(home))
    assert not refreshes and report["registry"].startswith("never fetched")


def test_no_sources_and_the_switch_and_offline_all_skip(home: DataHome, monkeypatch: pytest.MonkeyPatch):
    checks, refreshes = _spies(monkeypatch)
    assert freshness.run(FakeCtx(home))["registry"] == "no sources"
    assert len(checks) == 1
    _add_source(home)
    _write_meta(home, age_seconds=freshness.REFRESH_TTL + 60)
    disabled = freshness.run(FakeCtx(home, settings={"updates.check": False}))
    assert disabled["selfUpdate"].startswith("disabled") and disabled["registry"].startswith("disabled")
    offline = freshness.run(FakeCtx(home, offline=True))
    assert offline == {"selfUpdate": "offline", "registry": "offline"}
    assert len(checks) == 1 and not refreshes, "neither switch-off nor offline touched the network paths"


def test_failures_never_raise(home: DataHome, monkeypatch: pytest.MonkeyPatch):
    import floofy_core.cli.cmd_selfupdate as cs
    import floofy_core.registry_sources as rs

    def boom(*a, **k):
        raise RuntimeError("kaput")

    monkeypatch.setattr(cs, "update_check", boom)
    monkeypatch.setattr(rs, "refresh", boom)
    _add_source(home)
    _write_meta(home, age_seconds=freshness.REFRESH_TTL + 60)
    report = freshness.run(FakeCtx(home))
    assert report["selfUpdate"].startswith("error: kaput") and report["registry"].startswith("error: kaput")
