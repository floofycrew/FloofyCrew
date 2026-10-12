"""The re-apply trigger's unattended freshness pass (Requirement 7.7, 8.12; :mod:`floofy_core.freshness`).

The trigger's ``floofy apply --if-changed`` keeps the App's update reminders
fresh: the daily FloofyCrew release check (its own 24 h cache) and a registry
refresh at most once a day — best-effort, never failing the trigger, honouring
``updates.check``, and never fetching a registry the user added but has not
fetched once themselves. The edition's default source is the exception: it is
recorded when missing and fetched while its cache is unusable.
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

    def __init__(self, home: DataHome, *, offline: bool = False, settings: dict | None = None, defaults: list[dict] | None = None) -> None:
        self.home = home
        self.offline = offline
        self._settings = {"updates.check": True, **(settings or {})}
        self._defaults = list(defaults or [])
        self.audit = FakeAudit()

    def settings(self):
        return dict(self._settings)

    def url_opener(self, url: str):
        return None

    def default_registry_sources(self):
        return [dict(row) for row in self._defaults]


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
    assert offline == {"selfUpdate": "offline", "registry": "offline", "defaultSource": "none"}
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


# --- the edition's default source (Requirement 8.12) ------------------------------------------------------

DEFAULT = {"url": "https://registry.example/ours/", "name": "ours", "trust": "index", "keyId": "k1"}


def _default_spy(monkeypatch: pytest.MonkeyPatch, *, usable: bool = True) -> list:
    """Stand in for ``refresh`` on the default source: writes the ``meta.json`` verdict the real one would."""
    refreshes: list = []

    def fake_refresh(home, store, **kw):
        refreshes.append(kw)
        source = store.find(kw["only"])
        directory = home.cache_sources / source.key
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "meta.json").write_text(json.dumps({"fetchedAt": datetime.now(tz=timezone.utc).isoformat(), "usable": usable, "label": source.label, "url": source.url}), encoding="utf-8")
        return SimpleNamespace(sources=[{"label": source.label, "url": source.url, "usable": usable, "signature": {"status": "verified" if usable else "invalid"}}], merged_mods=2 if usable else 0, merged_rows=0, usable=[source.key] if usable else [], refused=[] if usable else [source.key])

    import floofy_core.cli.cmd_selfupdate as cs
    import floofy_core.registry_sources as rs

    monkeypatch.setattr(cs, "update_check", lambda ctx, **kw: SimpleNamespace(status="ok"))
    monkeypatch.setattr(rs, "refresh", fake_refresh)
    return refreshes


def test_a_missing_default_source_is_recorded_and_fetched_on_the_tick(home: DataHome, monkeypatch: pytest.MonkeyPatch):
    refreshes = _default_spy(monkeypatch)
    ctx = FakeCtx(home, defaults=[DEFAULT])
    report = freshness.run(ctx)
    assert report["defaultSource"] == "recorded, fetched: usable 1, refused 0", report
    stored = SourceStore.load(home).sources
    assert len(stored) == 1 and stored[0].url == DEFAULT["url"] and stored[0].name == "ours" and stored[0].key_id == "k1"
    assert len(refreshes) == 1 and refreshes[0]["only"] == stored[0].key, "only the default source is fetched — never the user's other sources"
    assert [r["op"] for r in ctx.audit.rows[:2]] == ["registry-add", "registry-refresh"] and "re-apply trigger" in ctx.audit.rows[0]["detail"]
    assert report["registry"].startswith("fresh"), "once usable it is under the daily rule like every other source"
    again = freshness.run(FakeCtx(home, defaults=[DEFAULT]))
    assert again["defaultSource"] == "present, usable" and len(refreshes) == 1


def test_an_unusable_default_is_retried_every_tick_but_a_users_source_is_not(home: DataHome, monkeypatch: pytest.MonkeyPatch):
    refreshes = _default_spy(monkeypatch, usable=False)
    _add_source(home, url="https://registry.example/theirs/")
    for _ in range(2):
        report = freshness.run(FakeCtx(home, defaults=[DEFAULT]))
        assert report["defaultSource"].endswith("fetched: usable 0, refused 1"), report
    assert len(refreshes) == 2 and all(kw["only"] == SourceStore.load(home).find("ours").key for kw in refreshes), "only the default was fetched, never the user's own source"
    assert report["registry"].startswith("fresh"), "the default's fresh fetch keeps the daily refresh-all quiet"
    assert [s.name for s in SourceStore.load(home).sources] == [None, "ours"], "the user's row is untouched; the default was appended"


def test_the_default_is_recorded_but_not_fetched_offline_or_when_updates_are_off(home: DataHome, monkeypatch: pytest.MonkeyPatch):
    refreshes = _default_spy(monkeypatch)
    offline = freshness.run(FakeCtx(home, offline=True, defaults=[DEFAULT]))
    assert offline["defaultSource"] == "recorded, not fetched (offline)" and offline["registry"] == "offline"
    assert len(SourceStore.load(home).sources) == 1 and not refreshes
    disabled = freshness.run(FakeCtx(home, settings={"updates.check": False}, defaults=[DEFAULT]))
    assert disabled["defaultSource"] == "present, not fetched (updates.check false)" and not refreshes


def test_the_users_trust_settings_on_the_default_are_never_reset_by_the_tick(home: DataHome, monkeypatch: pytest.MonkeyPatch):
    refreshes = _default_spy(monkeypatch)
    home.registries.write_text(json.dumps({"schema": 1, "sources": [{**DEFAULT, "trust": "owner", "allowUnsigned": True}]}), encoding="utf-8")
    report = freshness.run(FakeCtx(home, defaults=[DEFAULT]))
    assert report["defaultSource"] == "present, fetched: usable 1, refused 0" and len(refreshes) == 1
    kept = SourceStore.load(home).sources[0]
    assert kept.trust == "owner" and kept.allow_unsigned is True
