"""FloofyCrew's own update check and ``floofy self-update`` (task 10.7; Requirement 7.7, 11.6).

A fake release endpoint on loopback in both shapes the edition adapters use —
the GitHub releases API (``releases/latest`` JSON with the build's ``RELEASE.md``
as body and the assets) and a raw ``RELEASE.md`` on a package's default branch
(artifacts as blobs at the tag) — with the release notes rendered by the real
packaging renderers. Then: the cache and the 24 h rule, ``--offline``,
``updates.check false``, the notice rule (a newer release whose ``supports``
names the running host), a hash mismatch refused, the staged swap on a scratch
prefix, the CLI surfaces (``doctor``, ``status``, ``self-update``, ``config``),
the trigger path installing the staged Loader app, and the Loader's ``/state``
summary. No real network: every URL is loopback (plaintext is allowed there and
nowhere else).
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from floofy_core import __version__ as FRAMEWORK_VERSION
from floofy_core.audit import read_audit
from floofy_core.cli.main import run
from floofy_core.consent import write_consent
from floofy_core.datahome import DataHome
from floofy_core.loaderapp import load_module_from_file
from floofy_core.selfupdate import (
    CACHE_NAME,
    FEED_ENV,
    FeedError,
    Release,
    ReleaseFeed,
    SelfUpdateError,
    StagePlan,
    UpdateCheck,
    check,
    download_release,
    fetch_release,
    is_newer,
    notice_text,
    parse_release_notes,
    parse_sums,
    read_stage,
    supports_host,
    swap,
    up_to_date_reason,
    write_stage,
)
from floofy_core.semver import Version
from floofy_core.settings import Settings, SettingsError

from floofy_testing import REPO_ROOT, FakeGateway, fake_payload
from test_loaderapp import install_fake_loader_app

PUBLIC_BUILD = load_module_from_file("_selfupdate_tests_public_build", REPO_ROOT / "packaging" / "public" / "build.py")
INTERNAL_BUILD = load_module_from_file("_selfupdate_tests_internal_build", REPO_ROOT / "packaging" / "internal" / "build.py")

HOST = "0.7.0.5"
#: The channel the fake internal payload reports (a stamped build without a channel marker).
CHANNEL = "stable"
SUPPORTS = {"internal": {"beta": [HOST], "stable": [HOST]}, "external": {"insider": ["0.7.0rc5"], "stable": ["0.6.0"]}}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def newer(version: str = FRAMEWORK_VERSION) -> str:
    major, minor, patch = (int(p) for p in version.split(".")[:3])
    return f"{major}.{minor + 1}.0"


def older(version: str = FRAMEWORK_VERSION) -> str:
    """The release just below ``version`` — the feed's newest when the running FloofyCrew is ahead of it (task 10.12)."""
    major, minor, patch = (int(p) for p in version.split(".")[:3])
    if patch:
        return f"{major}.{minor}.{patch - 1}"
    return f"{major}.{minor - 1}.99" if minor else f"{major - 1}.99.99"


# --- a fake release in both shapes -------------------------------------------------------------------------------------


class FakeRelease:
    """The artifacts of one FloofyCrew release: a zipapp stand-in, a Loader archive, SHA256SUMS and RELEASE.md (both renderers)."""

    def __init__(self, version: str, *, supports: dict | None = SUPPORTS, pyz: bytes | None = None):
        self.version = version
        self.tag = f"v{version}"
        self.supports = supports
        self.pyz = pyz if pyz is not None else f"#!/usr/bin/env python3\n# fake floofy.pyz {version}\n".encode()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("floofycrew/app.json", json.dumps({"name": "floofycrew", "version": version}))
            archive.writestr("floofycrew/floofy_loader/__init__.py", f'__version__ = "{version}"\n')
        self.loader_name = f"floofycrew-loader-app-{version}.zip"
        self.files: dict[str, bytes] = {"floofy.pyz": self.pyz, self.loader_name: buffer.getvalue()}
        self.sums = {name: sha(data) for name, data in self.files.items()}
        self.files["SHA256SUMS"] = "".join(f"{digest}  {name}\n" for name, digest in sorted(self.sums.items())).encode()
        self.files["supports.json"] = json.dumps({"floofycrew": version, "supports": supports}).encode()
        self.public_notes = PUBLIC_BUILD._release_notes(version, self.tag, "example/FloofyCrew", supports, dict(self.sums))
        internal_sums = {f"dist/{name}": digest for name, digest in self.sums.items()}
        self.internal_notes = INTERNAL_BUILD._release_notes(version, supports, internal_sums)
        self.internal_sums_text = "".join(f"{digest}  {name}\n" for name, digest in sorted(internal_sums.items()))

    def tamper(self, name: str, data: bytes) -> None:
        """Replace a served artifact without touching the sums (a corrupted or substituted download)."""
        self.files[name] = data


class FakeReleaseServer:
    """Loopback HTTP serving a :class:`FakeRelease` as GitHub releases (``/gh/…``) and as a package's raw blobs (``/pkg/…``)."""

    def __init__(self, release: FakeRelease, *, fail_with: int | None = None, tag_pushed: bool = True):
        self.release = release
        self.fail_with = fail_with
        self.tag_pushed = tag_pushed  # package shape: the assets live at the release tag; False = the tag is not on the remote yet
        self.requests: list[dict[str, Any]] = []
        self._server: ThreadingHTTPServer | None = None

    def __enter__(self) -> "FakeReleaseServer":
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:  # noqa: D401
                return None

            def do_GET(self) -> None:  # noqa: N802
                server.requests.append({"path": self.path, "headers": {k: v for k, v in self.headers.items()}})
                if server.fail_with:
                    return self._send(server.fail_with, b"nope", "text/plain")
                path, _, query = self.path.partition("?")
                release = server.release
                if path == "/gh/api/releases/latest":
                    document = {
                        "tag_name": release.tag,
                        "html_url": f"{server.base}/gh/releases/tag/{release.tag}",
                        "body": release.public_notes,
                        "assets": [{"name": name, "browser_download_url": f"{server.base}/gh/download/{release.tag}/{name}"} for name in release.files],
                    }
                    return self._send(200, json.dumps(document).encode(), "application/json")
                if path.startswith(f"/gh/download/{release.tag}/"):
                    name = path.rsplit("/", 1)[1]
                    if name in release.files:
                        return self._send(200, release.files[name], "application/octet-stream")
                    return self._send(404, b"no such asset", "text/plain")
                if path == "/pkg/mainline/RELEASE.md" and query == "raw=1":
                    return self._send(200, release.internal_notes.encode(), "text/plain")
                if path.startswith(f"/pkg/{release.tag}/dist/") and query == "raw=1":
                    if not server.tag_pushed:
                        return self._send(404, b"not found", "text/plain")  # the package commit is on mainline, the tag is not pushed yet
                    name = path.rsplit("/", 1)[1]
                    if name == "SHA256SUMS":
                        return self._send(200, release.internal_sums_text.encode(), "text/plain")
                    if name in release.files:
                        return self._send(200, release.files[name], "application/octet-stream")
                    return self._send(404, b"no such blob", "text/plain")
                return self._send(404, b"not found", "text/plain")

            def _send(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    @property
    def base(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def github_feed(self) -> ReleaseFeed:
        return ReleaseFeed("github-releases", f"{self.base}/gh/api/releases/latest", f"{self.base}/gh/download/{{tag}}/{{file}}", f"{self.base}/gh/releases/tag/{{tag}}", "fake GitHub releases")

    def notes_feed(self) -> ReleaseFeed:
        return ReleaseFeed("release-notes", f"{self.base}/pkg/mainline/RELEASE.md?raw=1", f"{self.base}/pkg/{{tag}}/dist/{{file}}?raw=1", f"{self.base}/pkg/{{tag}}/RELEASE.md", "fake package RELEASE.md")

    def hits(self, prefix: str = "") -> int:
        return sum(1 for r in self.requests if r["path"].startswith(prefix))

    def __exit__(self, *exc) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


def facts(**overrides: Any) -> dict[str, Any]:
    base = {"running": FRAMEWORK_VERSION, "edition": "internal", "channel": "beta", "host_version": HOST}
    base.update(overrides)
    return base


# --- parsing --------------------------------------------------------------------------------------------------------------


def test_parse_release_notes_of_both_renderers_and_sums_of_both_layouts() -> None:
    release = FakeRelease("9.9.9")
    public = parse_release_notes(release.public_notes)
    assert public["version"] == "9.9.9" and public["supports"] == SUPPORTS
    assert public["sha256"] == release.sums, "the assets table names every dist file with its digest"
    internal = parse_release_notes(release.internal_notes)
    assert internal["version"] == "9.9.9" and internal["supports"] == SUPPORTS and internal["sha256"] == release.sums, "the internal table's dist/ prefix is stripped"
    empty = FakeRelease("9.9.9", supports=None)
    assert parse_release_notes(empty.public_notes)["supports"] == {}, "'not recorded in this build' is no support claim"
    assert parse_sums(release.files["SHA256SUMS"].decode()) == release.sums
    assert parse_sums(release.internal_sums_text) == release.sums
    assert parse_release_notes("# floofycrew v1.2.3-rc.1 (public edition)\n")["version"] == "1.2.3-rc.1"


# --- the check against both shapes -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ["github", "notes"])
def test_check_reads_both_shapes_and_caches_the_record(tmp_path: Path, shape: str) -> None:
    release = FakeRelease(newer())
    cache = tmp_path / "cache" / CACHE_NAME
    with FakeReleaseServer(release) as server:
        feed = server.github_feed() if shape == "github" else server.notes_feed()
        outcome = check(feed, cache, **facts())
        assert outcome.status == "ok" and outcome.fresh and outcome.latest is not None
        latest = outcome.latest
        assert latest.version == release.version and latest.tag == release.tag and latest.supports == SUPPORTS
        assert latest.sha256 == release.sums["floofy.pyz"] and latest.sha256s[release.loader_name] == release.sums[release.loader_name]
        if shape == "github":
            assert latest.assets["floofy.pyz"] == f"{server.base}/gh/download/{release.tag}/floofy.pyz" and latest.assets["loaderApp"].endswith(release.loader_name)
            assert latest.url == f"{server.base}/gh/releases/tag/{release.tag}"
        else:
            assert latest.assets["floofy.pyz"] == f"{server.base}/pkg/{release.tag}/dist/floofy.pyz?raw=1" and latest.assets["SHA256SUMS"].endswith("dist/SHA256SUMS?raw=1")
            assert latest.url == f"{server.base}/pkg/{release.tag}/RELEASE.md"
        assert outcome.available and outcome.notice and release.version in outcome.notice and HOST in outcome.notice and "floofy self-update" in outcome.notice
        # the request carries nothing identifying: the bare endpoint path, a generic agent, no query, no custom header
        [request] = server.requests
        assert request["path"] == ("/gh/api/releases/latest" if shape == "github" else "/pkg/mainline/RELEASE.md?raw=1")
        assert request["headers"]["User-Agent"] == "floofy (unofficial KiroCrew mod manager)"
        assert not any(k.lower().startswith(("x-", "cookie", "authorization")) for k in request["headers"])
        # the record on disk is the charter's shape
        document = json.loads(cache.read_text(encoding="utf-8"))
        assert document["schema"] == 1 and document["edition"] == "internal" and document["channel"] == "beta" and document["running"] == FRAMEWORK_VERSION
        assert document["checkedAt"].endswith("Z") and document["status"] == "ok"
        assert set(document["latest"]) >= {"version", "supports", "url", "sha256", "tag", "assets"} and document["latest"]["sha256"] == release.sums["floofy.pyz"]
        assert document["notice"] == outcome.notice
        # the same record reloads and re-grades
        reloaded = UpdateCheck.load(cache)
        assert reloaded is not None and reloaded.latest is not None and reloaded.latest.to_dict() == latest.to_dict()


def test_the_cache_holds_for_a_day_and_is_refreshed_when_stale_forced_or_for_other_facts(tmp_path: Path) -> None:
    release = FakeRelease(newer())
    cache = tmp_path / "cache" / CACHE_NAME
    now = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    with FakeReleaseServer(release) as server:
        feed = server.github_feed()
        first = check(feed, cache, now=now, **facts())
        assert first.fresh and server.hits() == 1
        again = check(feed, cache, now=now + timedelta(hours=23, minutes=59), **facts())
        assert not again.fresh and again.status == "ok" and again.notice == first.notice and server.hits() == 1, "within 24 h the cache answers"
        forced = check(feed, cache, now=now + timedelta(hours=1), force=True, **facts())
        assert forced.fresh and server.hits() == 2
        stale = check(feed, cache, now=now + timedelta(hours=25, minutes=1), **facts())
        assert stale.fresh and server.hits() == 3, "after 24 h the endpoint is asked again"
        other_channel = check(feed, cache, now=now + timedelta(hours=25, minutes=2), **facts(channel="stable"))
        assert other_channel.fresh and server.hits() == 4, "a record made for another channel does not count"
        other_running = check(feed, cache, now=now + timedelta(hours=25, minutes=3), **facts(running="0.0.1"))
        assert other_running.fresh and server.hits() == 5, "a record made by another FloofyCrew version does not count"
        regraded = check(feed, cache, now=now + timedelta(hours=25, minutes=4), **facts(running="0.0.1", host_version="0.6.0"))
        assert not regraded.fresh and server.hits() == 5 and regraded.notice is None, "the same record, judged for a host the release does not support: no notice"


def test_offline_and_disabled_never_touch_the_network(tmp_path: Path) -> None:
    release = FakeRelease(newer())
    cache = tmp_path / "cache" / CACHE_NAME
    with FakeReleaseServer(release) as server:
        feed = server.github_feed()
        disabled = check(feed, cache, enabled=False, **facts())
        assert disabled.status == "disabled" and disabled.notice is None and server.hits() == 0 and not cache.exists(), "updates.check false: no request, nothing cached"
        offline = check(feed, cache, offline=True, **facts())
        assert offline.status == "offline" and offline.notice is None and server.hits() == 0 and not cache.exists()
        online = check(feed, cache, **facts())
        assert online.fresh and server.hits() == 1
        cached_offline = check(feed, cache, offline=True, force=True, **facts())
        assert cached_offline.status == "ok" and not cached_offline.fresh and cached_offline.notice == online.notice and "offline" in cached_offline.detail and server.hits() == 1
        assert check(None, cache, force=True, **facts(running="0.0.1")).status == "no-feed" and server.hits() == 1


def test_an_unreachable_endpoint_is_cached_as_unreachable_and_stays_silent(tmp_path: Path) -> None:
    release = FakeRelease(newer())
    cache = tmp_path / "cache" / CACHE_NAME
    now = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    with FakeReleaseServer(release, fail_with=500) as server:
        outcome = check(server.github_feed(), cache, now=now, **facts())
        assert outcome.status == "unreachable" and outcome.notice is None and "HTTP 500" in outcome.detail and outcome.latest is None
        assert json.loads(cache.read_text())["status"] == "unreachable"
        again = check(server.github_feed(), cache, now=now + timedelta(hours=1), **facts())
        assert not again.fresh and server.hits() == 1, "a failure is cached like a success: no retry storm, one request a day"
    dead = ReleaseFeed("github-releases", "http://127.0.0.1:9/api", "http://127.0.0.1:9/{tag}/{file}")
    outcome = check(dead, cache, now=now + timedelta(days=2), timeout=1.0, **facts())
    assert outcome.status == "unreachable" and outcome.notice is None
    # a previously known release survives an outage in the record (the App keeps its facts) but is re-graded normally
    with FakeReleaseServer(release) as server:
        good = check(server.github_feed(), cache, now=now + timedelta(days=3), **facts())
        assert good.status == "ok" and good.latest is not None
    outage = check(dead, cache, now=now + timedelta(days=4), timeout=1.0, **facts())
    assert outage.status == "unreachable" and outage.latest is not None and outage.latest.version == release.version
    assert outage.notice is not None, "the last known release still supports this host: the notice stands"


def test_plaintext_to_a_non_loopback_host_is_refused_before_any_request(tmp_path: Path) -> None:
    feed = ReleaseFeed("github-releases", "http://example.invalid/api", "http://example.invalid/{tag}/{file}")
    with pytest.raises(FeedError, match="must use https"):
        fetch_release(feed)
    outcome = check(feed, tmp_path / CACHE_NAME, **facts())
    assert outcome.status == "unreachable" and "must use https" in outcome.detail


# --- the notice rule ------------------------------------------------------------------------------------------------------


def test_notice_only_for_a_newer_release_that_supports_the_running_host() -> None:
    release = Release(newer(), f"v{newer()}", SUPPORTS, "https://example.test/notes")
    assert supports_host(release, "internal", "beta", HOST) and not supports_host(release, "internal", "beta", "0.7.0.4")
    assert not supports_host(release, "external", "beta", HOST) and not supports_host(release, None, "beta", HOST) and not supports_host(None, "internal", "beta", HOST)
    text = notice_text(release, running=FRAMEWORK_VERSION, edition="internal", channel="beta", host_version=HOST)
    assert text and release.version in text and "https://example.test/notes" in text
    assert notice_text(release, running=FRAMEWORK_VERSION, edition="internal", channel="beta", host_version="0.7.0.4") is None, "not listed as supported: silent"
    assert notice_text(release, running=FRAMEWORK_VERSION, edition="external", channel="stable", host_version=HOST) is None, "another edition's list does not count"
    assert notice_text(Release(FRAMEWORK_VERSION, "v", SUPPORTS), running=FRAMEWORK_VERSION, edition="internal", channel="beta", host_version=HOST) is None, "the same version is no update"
    assert notice_text(Release("0.0.1", "v0.0.1", SUPPORTS), running=FRAMEWORK_VERSION, edition="internal", channel="beta", host_version=HOST) is None, "an older release is no update"
    assert notice_text(Release("not-a-version", "x", SUPPORTS), running=FRAMEWORK_VERSION, edition="internal", channel="beta", host_version=HOST) is None


def test_the_up_to_date_reason_is_judged_by_version_order_not_equality() -> None:
    """Task 10.12: the quiet line's explanation blames the supports list only for a release that is newer yet unsupported."""
    judged = dict(running=FRAMEWORK_VERSION, edition="internal", channel="beta", host_version=HOST)
    assert is_newer(newer(), FRAMEWORK_VERSION) and not is_newer(older(), FRAMEWORK_VERSION) and not is_newer(FRAMEWORK_VERSION, FRAMEWORK_VERSION)
    assert not is_newer("not-a-version", FRAMEWORK_VERSION) and not is_newer(None, FRAMEWORK_VERSION) and not is_newer(newer(), "")
    assert up_to_date_reason(Release(newer(), "v", SUPPORTS), **judged) is None, "newer and supported: the notice speaks, not the reason"
    assert up_to_date_reason(Release(newer(), "v", SUPPORTS), **{**judged, "host_version": "0.7.0.4"}) == f"{newer()} does not list host 0.7.0.4 (internal/beta) as supported"
    assert up_to_date_reason(Release(FRAMEWORK_VERSION, "v", SUPPORTS), **judged) == "this is the newest release"
    ahead = f"this is newer than the newest published release ({older()})"
    assert up_to_date_reason(Release(older(), "v", SUPPORTS), **judged) == ahead, "the running version is ahead of the feed: that is the reason"
    assert up_to_date_reason(Release(older(), "v", {}), **judged) == ahead, "…whether or not the older release lists this host"
    assert up_to_date_reason(None, **judged) is None
    outcome = UpdateCheck("ok", "2026-09-21T12:00:00Z", "internal", "beta", FRAMEWORK_VERSION, HOST, Release(older(), f"v{older()}", SUPPORTS))
    assert outcome.notice is None and outcome.available is False and outcome.reason == ahead
    assert UpdateCheck("ok", None, "internal", "beta", FRAMEWORK_VERSION, HOST, Release(newer(), "v", SUPPORTS)).reason is None


# --- the staged swap on a scratch prefix ------------------------------------------------------------------------------------


def scratch_prefix(tmp_path: Path) -> tuple[Path, Path]:
    """A pretend install: ``<prefix>/lib/floofycrew/floofy.pyz`` (mode 0644) and the data home's ``pending/``."""
    prefix = tmp_path / "prefix"
    lib = prefix / "lib" / "floofycrew"
    lib.mkdir(parents=True)
    target = lib / "floofy.pyz"
    target.write_bytes(b"#!/usr/bin/env python3\n# old floofy.pyz\n")
    target.chmod(0o644)
    pending = tmp_path / "home" / "floofy" / "pending"
    return target, pending


@pytest.mark.parametrize("shape", ["github", "notes"])
def test_download_verifies_every_artifact_then_swaps_atomically_and_stages_the_loader_app(tmp_path: Path, shape: str) -> None:
    release = FakeRelease(newer())
    target, pending = scratch_prefix(tmp_path)
    plan = StagePlan(target, pending)
    with FakeReleaseServer(release) as server:
        feed = server.github_feed() if shape == "github" else server.notes_feed()
        latest = fetch_release(feed)
        downloaded = download_release(latest, plan)
        assert Path(downloaded["pyz"]["path"]) == plan.staging and plan.staging.read_bytes() == release.pyz and downloaded["pyz"]["sha256"] == release.sums["floofy.pyz"]
        assert target.read_bytes().startswith(b"#!/usr/bin/env python3\n# old"), "nothing replaced before the swap"
        archive = Path(downloaded["loaderApp"]["path"])
        assert archive == plan.stage_dir / release.loader_name and archive.read_bytes() == release.files[release.loader_name]
        assert downloaded["sums"] == release.sums
        swapped = swap(plan)
        assert swapped == target and target.read_bytes() == release.pyz and not plan.staging.exists()
        assert target.stat().st_mode & 0o777 == 0o644, "the target's mode is kept"
        record = write_stage(plan, version=release.version, archive=archive, sha256=downloaded["loaderApp"]["sha256"], by="tests")
        staged = read_stage(pending)
        assert staged is not None and staged["version"] == release.version and staged["archive"] == str(archive) and staged["present"] is True and staged["applied"] is False
        assert record["sha256"] == release.sums[release.loader_name] and record["stagedAt"].endswith("Z")
        # the downloads went where the release said, over loopback
        wanted = ("/gh/download/", "/pkg/") if shape == "github" else ("/pkg/",)
        assert all(r["path"].startswith(wanted) or "RELEASE.md" in r["path"] or "api/releases" in r["path"] for r in server.requests)


def test_a_hash_mismatch_is_refused_and_leaves_the_installed_zipapp_alone(tmp_path: Path) -> None:
    release = FakeRelease(newer())
    target, pending = scratch_prefix(tmp_path)
    before = target.read_bytes()
    plan = StagePlan(target, pending)
    release.tamper("floofy.pyz", b"#!/usr/bin/env python3\n# substituted\n")
    with FakeReleaseServer(release) as server:
        latest = fetch_release(server.github_feed())
        with pytest.raises(SelfUpdateError, match="sha256 mismatch"):
            download_release(latest, plan)
        assert target.read_bytes() == before and not plan.staging.exists() and not plan.stage_dir.exists()
        with pytest.raises(SelfUpdateError, match="nothing staged"):
            swap(plan)
    # a tampered Loader archive is refused too, and the already verified zipapp download is discarded with it
    release = FakeRelease(newer())
    release.tamper(release.loader_name, b"PK\x05\x06 not the archive")
    with FakeReleaseServer(release) as server:
        latest = fetch_release(server.github_feed())
        with pytest.raises(SelfUpdateError, match="sha256 mismatch"):
            download_release(latest, plan)
        assert target.read_bytes() == before and not plan.staging.exists()
    # the release notes and SHA256SUMS disagreeing on a digest is refused before any download
    release = FakeRelease(newer())
    lying = Release(release.version, release.tag, SUPPORTS, "", {"floofy.pyz": "0" * 64}, {})
    with FakeReleaseServer(release) as server:
        lying.assets = fetch_release(server.github_feed()).assets
        with pytest.raises(SelfUpdateError, match="release notes say"):
            download_release(lying, plan)
        assert server.hits("/gh/download/") == 1, "only SHA256SUMS was fetched"
        assert target.read_bytes() == before


# --- settings -----------------------------------------------------------------------------------------------------------------


def test_settings_defaults_types_and_unknown_keys(tmp_path: Path) -> None:
    settings = Settings.for_data_home(tmp_path)
    assert settings.get("updates.check") is True and settings.effective() == {"updates.check": True}
    assert settings.set("updates.check", "false") is False and settings.set("updates.check", "on") is True and settings.set("updates.check", False) is False
    settings.save()
    assert json.loads((tmp_path / "config.json").read_text()) == {"updates": {"check": False}}
    assert Settings.for_data_home(tmp_path).get("updates.check") is False
    with pytest.raises(SettingsError):
        settings.set("updates.check", "maybe")
    with pytest.raises(SettingsError):
        settings.set("updates.chek", "true")
    with pytest.raises(SettingsError):
        settings.get("nope")
    (tmp_path / "config.json").write_text('{"updates": {"check": "yes please"}}', encoding="utf-8")
    assert Settings.for_data_home(tmp_path).get("updates.check") is True, "a value of the wrong type falls back to the default"
    (tmp_path / "config.json").write_text("not json", encoding="utf-8")
    assert Settings.for_data_home(tmp_path).get("updates.check") is True


# --- the CLI surfaces ------------------------------------------------------------------------------------------------------


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A fake internal payload (0.7.0.5), a temp host home with consent, no adapters; ``FLOOFY_RELEASE_FEED`` names the fake endpoint."""
    monkeypatch.setenv("FLOOFY_NO_ADAPTERS", "1")
    monkeypatch.delenv("KIROCREW_HOME", raising=False)
    monkeypatch.setenv("KIRO_HOME", str(tmp_path / "kiro"))
    payload_root = tmp_path / "payload"
    fake_payload(payload_root, "0.7.0", build=HOST)
    home = tmp_path / "home"
    data = DataHome.for_host_home(home).ensure()
    write_consent(data.consent, by="tests", how="test")

    class Env:
        root = payload_root
        host_home = home
        paths = data

        def argv(self, *args: str) -> list[str]:
            return ["--home", str(home), "--root", str(payload_root), *args]

        def run(self, *args: str, **kw):
            return run(self.argv(*args), non_interactive=True, actor="test", **kw)

        def feed(self, server: FakeReleaseServer, shape: str = "github") -> None:
            monkeypatch.setenv(FEED_ENV, json.dumps((server.github_feed() if shape == "github" else server.notes_feed()).to_dict()))

    return Env()


def test_doctor_and_status_show_the_one_line_notice_from_one_cached_check(cli) -> None:
    release = FakeRelease(newer())
    with FakeReleaseServer(release) as server:
        cli.feed(server)
        doctor = cli.run("--json", "doctor")
        assert doctor.exit == 0, doctor.stderr
        summary = doctor.json["selfUpdate"]
        assert summary["available"] is True and summary["version"] == release.version and summary["status"] == "ok" and summary["staged"] is None
        assert summary["notice"] and release.version in summary["notice"] and summary["supportsHost"] is True
        assert cli.paths.self_update_cache.is_file() and server.hits() == 1
        text = cli.run("doctor")
        assert doctor.json["host"]["channel"] == CHANNEL
        assert f"update: FloofyCrew {release.version} is available and supports your host {HOST} (internal/{CHANNEL})" in text.stdout and "floofy self-update" in text.stdout
        status = cli.run("status")
        assert status.exit == 0 and f"update: FloofyCrew {release.version} is available" in status.stdout
        status_json = cli.run("--json", "status")
        assert status_json.json["selfUpdate"]["notice"] == summary["notice"]
        assert server.hits() == 1, "doctor, doctor, status, status: one request — the cache answered the rest"
        # --offline: the cached notice may still show, but nothing is fetched (also with a stale cache)
        stale = json.loads(cli.paths.self_update_cache.read_text())
        stale["checkedAt"] = "2020-01-01T00:00:00Z"
        cli.paths.self_update_cache.write_text(json.dumps(stale), encoding="utf-8")
        offline = cli.run("--offline", "--json", "doctor")
        assert offline.json["selfUpdate"]["available"] is True and "offline" in offline.json["selfUpdate"]["detail"] and server.hits() == 1
        online = cli.run("--json", "status")
        assert server.hits() == 2, "the stale record is refreshed once --offline is dropped"
        assert online.json["selfUpdate"]["available"] is True


def test_doctor_stays_quiet_when_up_to_date_unsupported_disabled_or_unreachable(cli) -> None:
    with FakeReleaseServer(FakeRelease(FRAMEWORK_VERSION)) as server:
        cli.feed(server)
        same = cli.run("doctor")
        assert same.exit == 0 and f"update: FloofyCrew {FRAMEWORK_VERSION} is up to date for this host (this is the newest release" in same.stdout
        assert "is available" not in same.stdout
        assert "update:" not in cli.run("status").stdout, "status says nothing when there is nothing to say"
    cli.paths.self_update_cache.unlink()
    with FakeReleaseServer(FakeRelease(newer(), supports={"internal": {CHANNEL: ["0.7.0.4"]}})) as server:
        cli.feed(server)
        unsupported = cli.run("--json", "doctor")
        assert unsupported.json["selfUpdate"]["available"] is False and unsupported.json["selfUpdate"]["supportsHost"] is False
        assert f"does not list host {HOST} (internal/{CHANNEL}) as supported" in cli.run("doctor").stdout
    cli.paths.self_update_cache.unlink()
    with FakeReleaseServer(FakeRelease(newer())) as server:
        cli.feed(server)
        assert cli.run("config", "set", "updates.check", "false").exit == 0
        assert cli.run("--json", "config", "get", "updates.check").json["value"] is False
        disabled = cli.run("--json", "doctor")
        assert disabled.json["selfUpdate"]["status"] == "disabled" and disabled.json["selfUpdate"]["available"] is False and server.hits() == 0
        assert "update: check disabled" in cli.run("doctor").stdout and not cli.paths.self_update_cache.exists()
        rows = read_audit(cli.paths.audit)
        assert rows[-1]["op"] == "config-set" and rows[-1]["key"] == "updates.check" and rows[-1]["value"] is False
        # the explicit command is the user asking now: it checks regardless of the setting
        explicit = cli.run("--json", "self-update", "--check")
        assert explicit.exit == 0 and explicit.json["selfUpdate"]["available"] is True and server.hits() == 1
        assert cli.run("config", "set", "updates.check", "true").exit == 0
    with FakeReleaseServer(FakeRelease(newer()), fail_with=503) as server:
        cli.paths.self_update_cache.unlink()
        cli.feed(server)
        unreachable = cli.run("--json", "doctor")
        assert unreachable.exit == 0 and unreachable.json["selfUpdate"]["status"] == "unreachable" and "update: check unreachable" in cli.run("doctor").stdout
        failed = cli.run("self-update")
        assert failed.exit == 1 and "cannot check" in failed.stderr
    assert cli.run("--offline", "self-update").exit == 1


@pytest.mark.parametrize("shape", ["notes", "github"])
def test_a_floofy_ahead_of_the_feed_is_up_to_date_without_blaming_the_supports_list(cli, tmp_path: Path, shape: str) -> None:
    """Task 10.12 (Requirement 7.7, 7.4): found on the 1.1.2 checkout against the internal package's `RELEASE.md` (1.1.1, which lists
    the host) — `doctor` said "1.1.1 does not list host 0.7.0.5 (internal/stable) as supported". The reason is the version order."""
    behind = older()
    ahead = f"this is newer than the newest published release ({behind})"
    target, _pending = scratch_prefix(tmp_path)
    with FakeReleaseServer(FakeRelease(behind)) as server:  # SUPPORTS lists HOST for internal/stable: the older release supports this host
        cli.feed(server, shape)
        doctor = cli.run("--json", "doctor")
        assert doctor.exit == 0, doctor.stderr
        summary = doctor.json["selfUpdate"]
        assert summary["status"] == "ok" and summary["running"] == FRAMEWORK_VERSION and summary["version"] == behind
        assert summary["available"] is False and summary["notice"] is None and summary["supportsHost"] is True
        text = cli.run("doctor").stdout
        assert f"update: FloofyCrew {FRAMEWORK_VERSION} is up to date for this host ({ahead}; checked " in text
        assert "does not list host" not in text and "is available" not in text
        status = cli.run("status")
        assert status.exit == 0 and "update:" not in status.stdout, "status stays quiet: nothing to say"
        assert cli.run("--json", "status").json["selfUpdate"]["available"] is False
        checked = cli.run("self-update", "--check")
        assert checked.exit == 0, checked.stderr
        assert f"FloofyCrew {FRAMEWORK_VERSION}: up to date for this host ({ahead}; checked " in checked.stdout and "does not list" not in checked.stdout
        # the plain command decides the same way: nothing to install, no false refusal, the target untouched
        plain = cli.run("--yes", "self-update", "--target", str(target))
        assert plain.exit == 0, plain.stderr
        assert ahead in plain.stdout and "does not list your host" not in plain.stderr and target.read_bytes().startswith(b"#!/usr/bin/env python3\n# old")
    cli.paths.self_update_cache.unlink()
    with FakeReleaseServer(FakeRelease(behind, supports={"internal": {CHANNEL: ["0.7.0.4"]}})) as server:
        cli.feed(server, shape)
        text = cli.run("doctor").stdout
        assert ahead in text and "does not list host" not in text, "an older release's supports list is beside the point"
    cli.paths.self_update_cache.unlink()
    # the counter-case: a newer release that does not list this host is still explained by its supports list, on every surface
    with FakeReleaseServer(FakeRelease(newer(), supports={"internal": {CHANNEL: ["0.7.0.4"]}})) as server:
        cli.feed(server, shape)
        blamed = f"{newer()} does not list host {HOST} (internal/{CHANNEL}) as supported"
        doctor = cli.run("--json", "doctor")
        assert doctor.json["selfUpdate"]["available"] is False and doctor.json["selfUpdate"]["supportsHost"] is False
        text = cli.run("doctor").stdout
        assert f"update: FloofyCrew {FRAMEWORK_VERSION} is up to date for this host ({blamed}; checked " in text and "newest published" not in text
        assert "update:" not in cli.run("status").stdout
        checked = cli.run("self-update", "--check")
        assert checked.exit == 0 and f"up to date for this host ({blamed}; checked " in checked.stdout
        refused = cli.run("--yes", "self-update", "--target", str(target))
        assert refused.exit == 1 and "does not list your host" in refused.stderr


def test_self_update_swaps_a_scratch_target_stages_and_installs_the_loader_app_and_audits(cli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    release = FakeRelease(newer())
    target, _pending = scratch_prefix(tmp_path)
    before = target.read_bytes()
    install_fake_loader_app(cli.host_home)  # the Loader app is "installed": the update goes through the host App Kit
    launcher = tmp_path / "bin" / "kirocrew"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/sh\necho \"$@\" >> \"$FAKE_KIROCREW_LOG\"\ncase \"$1 $2\" in\n  \"app install\") mkdir -p \"$KIROCREW_HOME/apps/floofycrew\"; cp -r \"$3\"/. \"$KIROCREW_HOME/apps/floofycrew/\"; printf '{\"name\": \"floofycrew\", \"version\": \"%s\", \"enabled\": true}\\n' \"$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]+\"/app.json\"))[\"version\"])' \"$3\")\" > \"$KIROCREW_HOME/apps/floofycrew/installed.json\" ;;\n  \"app uninstall\") rm -rf \"$KIROCREW_HOME/apps/floofycrew\" ;;\n  *) : ;;\nesac\n", encoding="utf-8")
    launcher.chmod(0o755)
    monkeypatch.setenv("FAKE_KIROCREW_LOG", str(tmp_path / "kirocrew.log"))
    with FakeReleaseServer(release) as server:
        cli.feed(server)
        declined = cli.run("self-update", "--target", str(target))
        assert declined.exit == 1 and "declined" in declined.stdout and target.read_bytes() == before
        assert read_audit(cli.paths.audit)[-1]["op"] == "self-update" and read_audit(cli.paths.audit)[-1]["result"] == "declined"
        result = cli.run("--yes", "--kirocrew", str(launcher), "self-update", "--target", str(target))
        assert result.exit == 0, result.stderr + result.stdout
        assert target.read_bytes() == release.pyz and not target.with_name("floofy.pyz.new").exists(), "the zipapp was swapped atomically"
        installed = result.json["selfUpdate"]["installed"]
        assert installed["version"] == release.version and installed["target"] == str(target) and installed["pyzSha256"] == release.sums["floofy.pyz"]
        assert installed["loaderApp"]["install"]["ok"] is True, "no gateway runs: the Loader app was installed right away through the host App Kit"
        calls = (tmp_path / "kirocrew.log").read_text().splitlines()
        assert any(c.startswith("app uninstall floofycrew") for c in calls) and any(c.startswith("app install ") for c in calls)
        meta = json.loads((cli.host_home / "apps" / "floofycrew" / "installed.json").read_text())
        assert meta["version"] == release.version
        stage = read_stage(cli.paths.pending)
        assert stage is not None and stage["applied"] is True and stage["present"] is False, "the marker records the install and the archive is gone"
        rows = [r for r in read_audit(cli.paths.audit) if r["op"] == "self-update" and r["result"] == "ok"]
        assert [r["phase"] for r in rows] == ["swap", "loader-app"] and all(r["version"] == release.version for r in rows)
        assert str(target) in rows[0]["files"] and rows[0]["sha256"] == release.sums["floofy.pyz"]
        assert cli.paths.staged_mods() == [], "the self-update stage is not a staged mod"
        # up to date now (the fake target is not this process, so the running version is still the old one: the check says newer, but the target already holds it)
        again = cli.run("--json", "self-update", "--check")
        assert again.exit == 0 and again.json["selfUpdate"]["available"] is True


def test_the_swapped_in_release_runs_the_loader_app_step_itself(cli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Everything after the swap runs on the OLD release's modules, so a bug in the Loader step of the release being
    replaced ran once more on every update (1.1.3 → 1.1.4 and 1.1.4 → 1.1.5 on the test box). When the new zipapp is
    runnable it is invoked as `<new pyz> self-update --install-staged --json` and its result relayed; the fake stand-in
    zipapps of the other tests are not zip files, so they keep exercising the in-process fallback."""
    import subprocess
    import sys

    built = tmp_path / "built" / "floofy.pyz"
    built.parent.mkdir()
    build = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "build_zipapp.py"), "--out", str(built)], capture_output=True, text=True, check=False)
    assert build.returncode == 0, build.stderr
    release = FakeRelease(newer(), pyz=built.read_bytes())
    target, _pending = scratch_prefix(tmp_path)
    install_fake_loader_app(cli.host_home)
    launcher = tmp_path / "bin" / "kirocrew"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/sh\necho \"$@\" >> \"$FAKE_KIROCREW_LOG\"\ncase \"$1 $2\" in\n  \"app install\") mkdir -p \"$KIROCREW_HOME/apps/floofycrew\"; cp -r \"$3\"/. \"$KIROCREW_HOME/apps/floofycrew/\"; printf '{\"name\": \"floofycrew\", \"version\": \"%s\", \"enabled\": true}\\n' \"$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]+\"/app.json\"))[\"version\"])' \"$3\")\" > \"$KIROCREW_HOME/apps/floofycrew/installed.json\" ;;\n  \"app uninstall\") rm -rf \"$KIROCREW_HOME/apps/floofycrew\" ;;\n  *) : ;;\nesac\n", encoding="utf-8")
    launcher.chmod(0o755)
    monkeypatch.setenv("FAKE_KIROCREW_LOG", str(tmp_path / "kirocrew.log"))
    with FakeReleaseServer(release) as server:
        cli.feed(server)
        result = cli.run("--yes", "--kirocrew", str(launcher), "self-update", "--target", str(target))
        assert result.exit == 0, result.stderr + result.stdout
        assert target.read_bytes() == built.read_bytes()
        install = result.json["selfUpdate"]["installed"]["loaderApp"]["install"]
        assert install["via"] == "new-release" and install["ok"] is True and install["enabled"] is True, install
        assert "by the new release itself" in result.stdout
        assert json.loads((cli.host_home / "apps" / "floofycrew" / "installed.json").read_text())["version"] == release.version
        stage = read_stage(cli.paths.pending)
        assert stage["applied"] is True and stage["present"] is False
        rows = [r for r in read_audit(cli.paths.audit) if r["op"] == "self-update" and r["phase"] == "loader-app"]
        assert rows[-1]["result"] == "ok", "the subprocess wrote the loader-app audit row into the same data home"


def test_self_update_refuses_an_unsupported_release_unless_forced_and_needs_a_zipapp_target(cli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target, _pending = scratch_prefix(tmp_path)
    with FakeReleaseServer(FakeRelease(newer(), supports={"internal": {CHANNEL: ["0.7.0.4"]}})) as server:
        cli.feed(server)
        refused = cli.run("--yes", "self-update", "--target", str(target))
        assert refused.exit == 1 and "does not list your host" in refused.stderr and target.read_bytes().startswith(b"#!/usr/bin/env python3\n# old")
        forced = cli.run("--yes", "self-update", "--target", str(target), "--force")
        assert forced.exit == 0, forced.stderr
        assert target.read_bytes() == server.release.pyz
    with FakeReleaseServer(FakeRelease(newer())) as server:
        cli.feed(server)
        monkeypatch.setattr("floofy_core.cli.cmd_selfupdate.INSTALLED_PYZ", tmp_path / "nowhere" / "floofy.pyz")
        no_target = cli.run("--yes", "self-update")
        assert no_target.exit == 1 and "does not run from an installed zipapp" in no_target.stderr
    with FakeReleaseServer(FakeRelease(FRAMEWORK_VERSION)) as server:
        cli.feed(server)
        same = cli.run("--yes", "self-update", "--target", str(target))
        assert same.exit == 0 and "up to date" in same.stdout


def test_apply_installs_a_staged_loader_app_only_when_no_gateway_runs(cli, tmp_path: Path) -> None:
    release = FakeRelease(newer())
    install_fake_loader_app(cli.host_home)
    launcher = tmp_path / "bin" / "kirocrew"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/sh\ncase \"$1 $2\" in\n  \"app install\") mkdir -p \"$KIROCREW_HOME/apps/floofycrew\"; cp -r \"$3\"/. \"$KIROCREW_HOME/apps/floofycrew/\"; printf '{\"name\": \"floofycrew\", \"version\": \"staged\", \"enabled\": true}\\n' > \"$KIROCREW_HOME/apps/floofycrew/installed.json\" ;;\n  \"app uninstall\") rm -rf \"$KIROCREW_HOME/apps/floofycrew\" ;;\nesac\n", encoding="utf-8")
    launcher.chmod(0o755)
    plan = StagePlan(tmp_path / "unused.pyz", cli.paths.pending)
    archive = plan.stage_dir / release.loader_name
    archive.parent.mkdir(parents=True)
    archive.write_bytes(release.files[release.loader_name])
    write_stage(plan, version=release.version, archive=archive, sha256=release.sums[release.loader_name], by="tests")
    dist = cli.root / "kiro_crew" / "static" / "dist"
    with FakeGateway(dist, HOST, socket_dir=cli.host_home):
        held = cli.run("--kirocrew", str(launcher), "apply", "--no-verify")
        assert held.exit == 0, held.stderr
        assert "selfUpdateLoaderApp" not in held.json and read_stage(cli.paths.pending)["applied"] is False, "a running gateway is never disturbed"
        status = cli.run("status")
        assert f"Loader app update to FloofyCrew {release.version} staged" in status.stdout
    applied = cli.run("--kirocrew", str(launcher), "apply", "--no-verify")
    assert applied.exit == 0, applied.stderr
    assert applied.json["selfUpdateLoaderApp"]["ok"] is True and read_stage(cli.paths.pending)["applied"] is True
    assert json.loads((cli.host_home / "apps" / "floofycrew" / "installed.json").read_text())["version"] == "staged"
    rows = [r for r in read_audit(cli.paths.audit) if r["op"] == "self-update"]
    assert rows[-1]["phase"] == "loader-app" and rows[-1]["result"] == "ok"
    third = cli.run("--kirocrew", str(launcher), "apply", "--no-verify")
    assert "selfUpdateLoaderApp" not in third.json, "an applied stage is not applied twice"


def test_a_published_release_whose_tag_is_not_pushed_yet_fails_cleanly_before_any_change(cli, tmp_path: Path) -> None:
    """Seen on the test box for 1.1.4: the package's RELEASE.md on mainline named the release, but the assets are raw
    blobs at the tag ``v<version>`` and the tag had not been pushed, so SHA256SUMS 404'd — as an uncaught traceback.
    Nothing had changed (SHA256SUMS is fetched first); the CLI now says exactly that and what is missing."""
    release = FakeRelease(newer())
    target, pending = scratch_prefix(tmp_path)
    before = target.read_bytes()
    with FakeReleaseServer(release, tag_pushed=False) as server:
        cli.feed(server, shape="notes")
        result = cli.run("--yes", "self-update", "--target", str(target))
        assert result.exit != 0
        assert "Traceback" not in result.stderr
        assert "could not fetch the release assets" in result.stderr and "HTTP 404" in result.stderr
        assert f"tag {release.tag}, which does not exist yet" in result.stderr and "nothing was changed" in result.stderr
        assert target.read_bytes() == before and read_stage(cli.paths.pending) is None, "no swap, no stage"
        rows = [r for r in read_audit(cli.paths.audit) if r["op"] == "self-update"]
        assert rows[-1]["result"] == "unavailable" and rows[-1]["version"] == release.version


def test_self_update_now_installs_a_stage_left_by_an_earlier_run_when_the_zipapp_is_already_current(cli, tmp_path: Path) -> None:
    """What happened on the test box: the TUI swapped the zipapp to the new release while a gateway ran, so the
    Loader app stayed staged; a later ``floofy self-update --now`` found the zipapp current and only *described*
    the stage again. The up-to-date branch now installs it with ``--now`` (or when no gateway runs)."""
    release = FakeRelease(FRAMEWORK_VERSION)  # the feed's newest release is the running one: nothing to swap
    install_fake_loader_app(cli.host_home)
    launcher = tmp_path / "bin" / "kirocrew"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/sh\ncase \"$1 $2\" in\n  \"app install\") mkdir -p \"$KIROCREW_HOME/apps/floofycrew\"; cp -r \"$3\"/. \"$KIROCREW_HOME/apps/floofycrew/\"; printf '{\"name\": \"floofycrew\", \"version\": \"staged\", \"enabled\": true}\\n' > \"$KIROCREW_HOME/apps/floofycrew/installed.json\" ;;\n  \"app uninstall\") rm -rf \"$KIROCREW_HOME/apps/floofycrew\" ;;\nesac\n", encoding="utf-8")
    launcher.chmod(0o755)
    plan = StagePlan(tmp_path / "unused.pyz", cli.paths.pending)
    archive = plan.stage_dir / release.loader_name
    archive.parent.mkdir(parents=True)
    archive.write_bytes(release.files[release.loader_name])
    write_stage(plan, version=release.version, archive=archive, sha256=release.sums[release.loader_name], by="tui")
    dist = cli.root / "kiro_crew" / "static" / "dist"
    with FakeReleaseServer(release) as server:
        cli.feed(server)
        with FakeGateway(dist, HOST, socket_dir=cli.host_home):
            held = cli.run("--kirocrew", str(launcher), "self-update")
            assert held.exit == 0, held.stderr + held.stdout
            assert "up to date" in held.stdout and f"Loader app update to FloofyCrew {release.version} staged" in held.stdout
            assert read_stage(cli.paths.pending)["applied"] is False, "without --now a running gateway is never disturbed"
            now = cli.run("--kirocrew", str(launcher), "self-update", "--now")
            assert now.exit == 0, now.stderr + now.stdout
            assert "up to date" in now.stdout and "Loader app updated to FloofyCrew" in now.stdout and "next gateway start" in now.stdout
            assert now.json["selfUpdate"]["installed"]["loaderApp"]["install"]["ok"] is True
        stage = read_stage(cli.paths.pending)
        assert stage["applied"] is True and stage["present"] is False
        assert json.loads((cli.host_home / "apps" / "floofycrew" / "installed.json").read_text())["version"] == "staged"
        rows = [r for r in read_audit(cli.paths.audit) if r["op"] == "self-update"]
        assert rows[-1]["phase"] == "loader-app" and rows[-1]["result"] == "ok"
        third = cli.run("--kirocrew", str(launcher), "self-update", "--now")
        assert third.exit == 0 and "Loader app updated" not in third.stdout, "an applied stage is not installed twice"
        # the same stage with no gateway running installs without --now
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(release.files[release.loader_name])
        write_stage(plan, version=release.version, archive=archive, sha256=release.sums[release.loader_name], by="tui")
        quiet = cli.run("--kirocrew", str(launcher), "self-update")
        assert quiet.exit == 0 and "Loader app updated to FloofyCrew" in quiet.stdout and read_stage(cli.paths.pending)["applied"] is True


def test_a_refused_enable_after_the_stage_install_is_reported_as_disabled_not_updated(cli, tmp_path: Path) -> None:
    """The first 1.1.3 → 1.1.4 update on the test box: the Loader files were installed, `kirocrew app enable` was
    refused, and the last line still said "Loader app updated … runs at the next gateway start". The stage is
    consumed (the files are in place) but the outcome is `disabled`, the line says what to run, and the audit says so."""
    release = FakeRelease(FRAMEWORK_VERSION)
    install_fake_loader_app(cli.host_home)
    launcher = tmp_path / "bin" / "kirocrew"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/sh\ncase \"$1 $2\" in\n  \"app install\") mkdir -p \"$KIROCREW_HOME/apps/floofycrew\"; cp -r \"$3\"/. \"$KIROCREW_HOME/apps/floofycrew/\"; printf '{\"name\": \"floofycrew\", \"version\": \"staged\", \"enabled\": false}\\n' > \"$KIROCREW_HOME/apps/floofycrew/installed.json\" ;;\n  \"app enable\") echo 'blocked by execution policy: third-party app execution is disabled' >&2; exit 1 ;;\n  \"app uninstall\") rm -rf \"$KIROCREW_HOME/apps/floofycrew\" ;;\nesac\n", encoding="utf-8")
    launcher.chmod(0o755)
    plan = StagePlan(tmp_path / "unused.pyz", cli.paths.pending)
    archive = plan.stage_dir / release.loader_name
    archive.parent.mkdir(parents=True)
    archive.write_bytes(release.files[release.loader_name])
    write_stage(plan, version=release.version, archive=archive, sha256=release.sums[release.loader_name], by="tui")
    with FakeReleaseServer(release) as server:
        cli.feed(server)
        result = cli.run("--kirocrew", str(launcher), "self-update", "--now")
        assert result.exit == 0, result.stderr + result.stdout
        assert "Loader app updated" not in result.stdout
        assert "installed but DISABLED" in result.stdout and "floofy init" in result.stdout
        install = result.json["selfUpdate"]["installed"]["loaderApp"]["install"]
        assert install["ok"] is True and install["enabled"] is False
    stage = read_stage(cli.paths.pending)
    assert stage["applied"] is True and stage["present"] is False and stage.get("outcome") == "disabled" and stage.get("enabled") is False
    rows = [r for r in read_audit(cli.paths.audit) if r["op"] == "self-update"]
    assert rows[-1]["phase"] == "loader-app" and rows[-1]["result"] == "disabled" and "enable" in rows[-1]["detail"]


def test_tui_home_summary_and_actions_carry_the_notice(cli, monkeypatch: pytest.MonkeyPatch) -> None:
    from floofy_core.cli.console import Console
    from floofy_core.cli.context import build_context
    from floofy_core.cli.main import build_parser
    from floofy_core.cli.tui import action, host_summary

    release = FakeRelease(newer())
    with FakeReleaseServer(release) as server:
        cli.feed(server)
        args = build_parser().parse_args(cli.argv("doctor"))
        ctx = build_context(args, console=Console(out=io.StringIO(), err=io.StringIO(), non_interactive=True))
        rows = dict(host_summary(ctx))
        assert "update" in rows and release.version in rows["update"]
        assert action("selfupdate.check").argv == ("self-update", "--check") and action("selfupdate.install").argv == ("self-update",)


def test_the_loader_state_carries_the_cached_notice_regraded_for_its_host(cli) -> None:
    """The Loader never contacts the endpoint: `/state`/`/registry` `selfUpdate` re-grade the CLI's cache for the Loader's facts."""
    sys.path.insert(0, str(REPO_ROOT / "loader-app"))
    try:
        from floofy_loader import __version__ as LOADER_VERSION
        from floofy_loader.host import HostFacts
        from floofy_loader.runtime import LoaderRuntime
    finally:
        sys.path.pop(0)
    def facts_for(version: str) -> HostFacts:
        return HostFacts(version=version, base_version=Version.parse("0.7.0"), build_version=version, channel=CHANNEL, edition="internal", profile="enterprise", payload_root=cli.root, package_dir=cli.root / "kiro_crew", host_home=cli.host_home, data_home=cli.paths.data_home, interpreter=Path(sys.executable))

    runtime = LoaderRuntime()
    runtime.paths = cli.paths
    runtime.facts = facts_for(HOST)
    summary = runtime.self_update_summary()
    assert summary["available"] is False and summary["status"] == "not-checked" and summary["running"] == LOADER_VERSION
    release = FakeRelease(newer(LOADER_VERSION))
    with FakeReleaseServer(release) as server:
        cli.feed(server)
        assert cli.run("--json", "doctor").exit == 0
        summary = runtime.self_update_summary()
        assert summary["available"] is True and summary["version"] == release.version and summary["notes"] == f"{server.base}/gh/releases/tag/{release.tag}" and summary["staged"] is None
        assert server.hits() == 1, "the Loader read the cache; no request of its own"
        runtime.facts = facts_for("0.7.0.4")
        assert runtime.self_update_summary()["available"] is False, "re-graded for a host the release does not list"
