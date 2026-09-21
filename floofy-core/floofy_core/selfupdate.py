"""FloofyCrew's own update check and self-update (task 10.7; Requirement 7.7, 11.6).

Once a day at most the manager compares ``floofy_core.__version__`` with the
newest FloofyCrew release for the running edition and channel and, when that
release's ``supports`` list names the running host version, shows a one-line
notice (``doctor``, ``status``, the interactive ``floofy``, the App).
``floofy self-update`` then installs that release through the edition's installer
path with the same hash verification as a fresh install, staging the swap so a
running gateway is never disturbed.

**Where the release comes from** is the edition adapter's business
(:func:`floofy_core.editions.release_feed`): a :class:`ReleaseFeed` of one of two
shapes —

* ``github-releases`` — the GitHub releases API (``…/releases/latest``): the tag
  names the version, the release body is the build's ``RELEASE.md`` (its
  ``## Supports`` lines and its assets table), the assets are the release's
  ``floofy.pyz``, ``SHA256SUMS`` and the Loader app archive;
* ``release-notes`` — a raw ``RELEASE.md`` on the package's default branch (the
  internal package): the heading names the version, the same ``## Supports``
  lines and assets table, and the artifacts are the package's ``dist/`` blobs at
  the release tag.

Both are read over HTTPS (plaintext only to loopback — the test endpoints), with
the adapter's network identity when it has one, a short timeout, and a generic
``User-Agent``; the request carries nothing identifying beyond itself (no query
parameters, no host facts). A failed check is cached as ``unreachable`` and stays
silent until the next day, so the check never blocks a command; it is skipped
with ``--offline`` and when ``updates.check`` is ``false``.

**Cache record** — ``<data home>/cache/self-update.json``::

    {"schema": 1, "checkedAt": "…Z", "edition": "internal", "channel": "beta",
     "running": "1.1.0", "hostVersion": "0.7.0.5",
     "status": "ok" | "unreachable", "detail": "…",
     "latest": {"version": "1.2.0", "tag": "v1.2.0",
                "supports": {"internal": {"beta": ["0.7.0.5"], …}, …},
                "url": "<release notes>", "sha256": "<floofy.pyz digest or null>",
                "sha256s": {"floofy.pyz": "…", "floofycrew-loader-app-1.2.0.zip": "…"},
                "assets": {"floofy.pyz": "<url>", "SHA256SUMS": "<url>", "loaderApp": "<url>"}},
     "notice": "FloofyCrew 1.2.0 is available …" | null}

**Notice rule** — ``latest.version > running`` **and** ``latest.supports[edition]
[channel]`` contains the running host version; anything else (no feed, no
support claim for this host, unreachable) is silent. The quiet line ``doctor``
and ``self-update --check`` print instead explains why there is nothing to
install (:func:`up_to_date_reason`): the newest release is newer but does not
list this host, it is the running version, or the running version is ahead of
it (a checkout, a release cut before its publish).

**Staged swap** — the zipapp is downloaded to ``<target>.new`` beside the
installed ``floofy.pyz`` (the file the ``~/.local/bin/floofy`` wrapper runs; the
same path on both editions), verified against ``SHA256SUMS`` (and the release
notes' table when it names the file), then ``os.replace``d over the target — the
CLI is not the gateway, so this is safe at any time. The Loader app archive is
verified the same way and staged under ``<data home>/pending/self-update/``
with a marker; it is installed through the host App Kit (the same
``kirocrew app install`` path ``floofy init`` uses) by ``floofy apply`` — the
re-apply trigger's entry point — the next time no gateway is running, or right
away by ``floofy self-update --now``. Every step writes an ``op: self-update``
audit row.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .netscan import is_loopback_host
from .semver import InvalidVersion, Version

__all__ = [
    "CACHE_NAME",
    "CHECK_INTERVAL",
    "FEED_ENV",
    "FEED_KINDS",
    "FeedError",
    "Release",
    "ReleaseFeed",
    "SelfUpdateError",
    "StagePlan",
    "UpdateCheck",
    "check",
    "download_release",
    "fetch_release",
    "is_newer",
    "loader_archive_name",
    "notice_text",
    "parse_release_notes",
    "parse_sums",
    "read_stage",
    "supports_host",
    "swap",
    "up_to_date_reason",
    "write_stage",
]

CACHE_NAME = "self-update.json"
STAGE_DIR = "self-update"
STAGE_MARKER = "self-update.json"
CHECK_INTERVAL = timedelta(hours=24)
TIMEOUT = 5.0
MAX_FEED_BYTES = 1 << 20
MAX_ARTIFACT_BYTES = 64 << 20
#: A generic agent: the request identifies the program, never the user or the host.
USER_AGENT = "floofy (unofficial KiroCrew mod manager)"
#: Test switch: a JSON :class:`ReleaseFeed` document overriding the adapters' feed (loopback endpoints).
FEED_ENV = "FLOOFY_RELEASE_FEED"
FEED_KINDS = ("github-releases", "release-notes")
PYZ_NAME = "floofy.pyz"
SUMS_NAME = "SHA256SUMS"
_LOADER_ARCHIVE_RE = re.compile(r"^floofycrew-loader-app-.+\.zip$")
_VERSION_RE = re.compile(r"\bv?(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)\b")
_SUPPORTS_RE = re.compile(r"^\s*[-*]\s+([A-Za-z][\w-]*)\s*/\s*([A-Za-z][\w-]*)\s*:\s*(.*?)\s*$")
_ASSET_ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*`([0-9a-fA-F]{64})`\s*\|")
_SUMS_LINE_RE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(\S.*)$")


class FeedError(RuntimeError):
    """The release endpoint could not be read or made no sense."""


class SelfUpdateError(RuntimeError):
    """A download, verification or swap step refused."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_stamp(text: Any) -> datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


# --- the feed -------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReleaseFeed:
    """Where an edition publishes FloofyCrew releases (adapter-provided; see the module docstring).

    ``url`` — the endpoint to read (the GitHub ``releases/latest`` API, or a raw
    ``RELEASE.md``); ``download`` — a template for the artifacts with ``{tag}``,
    ``{version}`` and ``{file}``; ``notes`` — a template for the human-readable
    release page with ``{tag}`` and ``{version}``; ``label`` — for messages.
    """

    kind: str
    url: str
    download: str
    notes: str = ""
    label: str = ""

    @classmethod
    def from_dict(cls, document: Any) -> "ReleaseFeed":
        if not isinstance(document, dict):
            raise FeedError("release feed: not an object")
        kind = str(document.get("kind") or "")
        if kind not in FEED_KINDS:
            raise FeedError(f"release feed: unknown kind {kind!r} (known: {', '.join(FEED_KINDS)})")
        url, download = document.get("url"), document.get("download")
        if not isinstance(url, str) or not url or not isinstance(download, str) or not download:
            raise FeedError("release feed: `url` and `download` are required")
        return cls(kind, url, download, str(document.get("notes") or ""), str(document.get("label") or ""))

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "url": self.url, "download": self.download, "notes": self.notes, "label": self.label}

    def artifact_url(self, file: str, *, tag: str, version: str) -> str:
        return self.download.replace("{tag}", tag).replace("{version}", version).replace("{file}", file)

    def notes_url(self, *, tag: str, version: str) -> str:
        return self.notes.replace("{tag}", tag).replace("{version}", version) if self.notes else ""


def feed_from_env(environ: dict[str, str] | None = None) -> ReleaseFeed | None:
    """The :data:`FEED_ENV` override (tests), or ``None``."""
    raw = (os.environ if environ is None else environ).get(FEED_ENV, "").strip()
    if not raw:
        return None
    try:
        return ReleaseFeed.from_dict(json.loads(raw))
    except ValueError as exc:
        raise FeedError(f"{FEED_ENV}: {exc}") from exc


# --- fetching (HTTPS, loopback plaintext for tests; a short timeout; nothing identifying) -------------------------


def _refuse_plaintext(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and is_loopback_host(parsed.hostname or ""):
        return
    raise FeedError(f"refusing {url}: FloofyCrew's own traffic must use https (plaintext is allowed to loopback only — Requirement 11.6)")


def _open(url: str, opener: Callable[..., Any] | None, timeout: float) -> Any:
    _refuse_plaintext(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*"})
    open_url = opener.open if opener is not None and hasattr(opener, "open") else (opener or urllib.request.urlopen)
    return open_url(request, timeout=timeout)


def fetch_bytes(url: str, *, opener: Callable[..., Any] | None = None, timeout: float = TIMEOUT, max_bytes: int = MAX_FEED_BYTES) -> bytes:
    """One small GET; raises :class:`FeedError` on any failure."""
    try:
        with _open(url, opener, timeout) as response:
            data = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        raise FeedError(f"{url}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise FeedError(f"{url}: {exc}") from exc
    if len(data) > max_bytes:
        raise FeedError(f"{url}: larger than {max_bytes} bytes; refusing")
    return data


def download_to(url: str, destination: Path, *, expected_sha256: str, opener: Callable[..., Any] | None = None, timeout: float = 60.0, max_bytes: int = MAX_ARTIFACT_BYTES) -> str:
    """Stream ``url`` into ``destination`` and refuse it unless its SHA-256 is ``expected_sha256`` (the file is removed)."""
    import hashlib  # noqa: PLC0415

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    try:
        with _open(url, opener, timeout) as response, destination.open("wb") as out:
            while True:
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise SelfUpdateError(f"{url}: larger than {max_bytes} bytes; refusing")
                digest.update(chunk)
                out.write(chunk)
    except urllib.error.HTTPError as exc:
        destination.unlink(missing_ok=True)
        raise SelfUpdateError(f"{url}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, FeedError) as exc:
        destination.unlink(missing_ok=True)
        raise SelfUpdateError(f"{url}: {exc}") from exc
    except SelfUpdateError:
        destination.unlink(missing_ok=True)
        raise
    actual = digest.hexdigest()
    if actual != expected_sha256.lower():
        destination.unlink(missing_ok=True)
        raise SelfUpdateError(f"{url}: sha256 mismatch (expected {expected_sha256[:12]}…, got {actual[:12]}…); the download was discarded")
    return actual


# --- release notes and SHA256SUMS --------------------------------------------------------------------------------


def parse_release_notes(text: str) -> dict[str, Any]:
    """``{version, supports, sha256}`` from a build's ``RELEASE.md`` (either edition's wording).

    The version is the first ``X.Y.Z`` in the first heading; ``supports`` are the
    ``- edition/channel: v1, v2`` bullets (``-`` = none); ``sha256`` maps the
    assets table's file names (``dist/`` stripped) to digests.
    """
    version: str | None = None
    supports: dict[str, dict[str, list[str]]] = {}
    sha256: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if version is None and stripped.startswith("#"):
            match = _VERSION_RE.search(stripped)
            if match:
                version = match.group(1)
            continue
        bullet = _SUPPORTS_RE.match(line)
        if bullet:
            edition, channel, rest = bullet.groups()
            versions = [v.strip().strip("`") for v in rest.split(",") if v.strip() and v.strip() != "-"]
            supports.setdefault(edition.lower(), {})[channel.lower()] = versions
            continue
        row = _ASSET_ROW_RE.match(stripped)
        if row:
            sha256[Path(row.group(1).strip()).name] = row.group(2).lower()
    return {"version": version, "supports": supports, "sha256": sha256}


def parse_sums(text: str) -> dict[str, str]:
    """``SHA256SUMS`` lines (``<digest>  <name>``) as ``{basename: digest}`` — the internal package prefixes names with ``dist/``."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        match = _SUMS_LINE_RE.match(line.strip())
        if match:
            sums[Path(match.group(2).strip()).name] = match.group(1).lower()
    return sums


def loader_archive_name(names: Any) -> str | None:
    """The ``floofycrew-loader-app-<version>.zip`` among ``names``, or ``None``."""
    for name in names:
        if _LOADER_ARCHIVE_RE.match(Path(str(name)).name):
            return Path(str(name)).name
    return None


# --- a release ----------------------------------------------------------------------------------------------------


@dataclass
class Release:
    """The newest release the feed describes (the cache's ``latest``)."""

    version: str
    tag: str
    supports: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    url: str = ""
    sha256s: dict[str, str] = field(default_factory=dict)
    assets: dict[str, str] = field(default_factory=dict)

    @property
    def sha256(self) -> str | None:
        return self.sha256s.get(PYZ_NAME)

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "tag": self.tag, "supports": self.supports, "url": self.url, "sha256": self.sha256, "sha256s": dict(self.sha256s), "assets": dict(self.assets)}

    @classmethod
    def from_dict(cls, document: Any) -> "Release | None":
        if not isinstance(document, dict) or not isinstance(document.get("version"), str):
            return None
        supports = document.get("supports") if isinstance(document.get("supports"), dict) else {}
        clean: dict[str, dict[str, list[str]]] = {}
        for edition, channels in supports.items():
            if isinstance(channels, dict):
                clean[str(edition)] = {str(c): [str(v) for v in (vs or [])] for c, vs in channels.items() if isinstance(vs, list)}
        sha256s = {str(k): str(v) for k, v in (document.get("sha256s") or {}).items()} if isinstance(document.get("sha256s"), dict) else {}
        assets = {str(k): str(v) for k, v in (document.get("assets") or {}).items()} if isinstance(document.get("assets"), dict) else {}
        return cls(document["version"], str(document.get("tag") or f"v{document['version']}"), clean, str(document.get("url") or ""), sha256s, assets)


def _assets_for(feed: ReleaseFeed, *, tag: str, version: str, names: dict[str, str] | None = None) -> dict[str, str]:
    """Artifact URLs: the feed's template, or the GitHub release's own ``browser_download_url`` when known."""
    names = names or {}
    loader = loader_archive_name(names) or f"floofycrew-loader-app-{version}.zip"
    return {
        PYZ_NAME: names.get(PYZ_NAME) or feed.artifact_url(PYZ_NAME, tag=tag, version=version),
        SUMS_NAME: names.get(SUMS_NAME) or feed.artifact_url(SUMS_NAME, tag=tag, version=version),
        "loaderApp": names.get(loader) or feed.artifact_url(loader, tag=tag, version=version),
    }


def fetch_release(feed: ReleaseFeed, *, opener: Callable[..., Any] | None = None, timeout: float = TIMEOUT) -> Release:
    """Read the feed and describe its newest release; raises :class:`FeedError`."""
    data = fetch_bytes(feed.url, opener=opener, timeout=timeout)
    if feed.kind == "github-releases":
        try:
            document = json.loads(data.decode("utf-8"))
        except ValueError as exc:
            raise FeedError(f"{feed.url}: not JSON") from exc
        if isinstance(document, list):  # `…/releases` instead of `…/releases/latest`: the first non-draft, non-prerelease entry
            document = next((r for r in document if isinstance(r, dict) and not r.get("draft") and not r.get("prerelease")), None)
        if not isinstance(document, dict) or not isinstance(document.get("tag_name"), str):
            raise FeedError(f"{feed.url}: no tag_name in the release document")
        tag = document["tag_name"]
        match = _VERSION_RE.search(tag)
        if not match:
            raise FeedError(f"{feed.url}: tag {tag!r} names no version")
        version = match.group(1)
        notes = parse_release_notes(str(document.get("body") or ""))
        urls = {a["name"]: a["browser_download_url"] for a in (document.get("assets") or []) if isinstance(a, dict) and isinstance(a.get("name"), str) and isinstance(a.get("browser_download_url"), str)}
        assets = _assets_for(feed, tag=tag, version=version, names=urls)
        url = str(document.get("html_url") or "") or feed.notes_url(tag=tag, version=version)
        return Release(version, tag, notes["supports"], url, notes["sha256"], assets)
    notes = parse_release_notes(data.decode("utf-8", "replace"))
    if not notes["version"]:
        raise FeedError(f"{feed.url}: the release notes name no version in their heading")
    version = notes["version"]
    tag = f"v{version}"
    return Release(version, tag, notes["supports"], feed.notes_url(tag=tag, version=version), notes["sha256"], _assets_for(feed, tag=tag, version=version))


# --- the rule --------------------------------------------------------------------------------------------------------


def is_newer(candidate: str | None, running: str | None) -> bool:
    """Whether ``candidate`` is a strictly newer version than ``running`` (an unparsable side is never newer)."""
    if not candidate or not running:
        return False
    try:
        return Version.parse(candidate) > Version.parse(running)
    except InvalidVersion:
        return False


def supports_host(release: Release | None, edition: str | None, channel: str | None, host_version: str | None) -> bool:
    """Whether the release's ``supports`` list names ``host_version`` for this edition and channel."""
    if release is None or not edition or not channel or not host_version:
        return False
    return host_version in (release.supports.get(edition, {}) or {}).get(channel, [])


def notice_text(release: Release | None, *, running: str, edition: str | None, channel: str | None, host_version: str | None) -> str | None:
    """The one-line notice, or ``None`` (Requirement 7.7: only a newer release that supports the running host)."""
    if release is None or not is_newer(release.version, running) or not supports_host(release, edition, channel, host_version):
        return None
    where = f" — release notes: {release.url}" if release.url else ""
    return f"FloofyCrew {release.version} is available and supports your host {host_version} ({edition}/{channel}); you run {running}: `floofy self-update`{where}"


def up_to_date_reason(release: Release | None, *, running: str, edition: str | None, channel: str | None, host_version: str | None) -> str | None:
    """Why there is nothing to install — the parenthesis of the quiet "up to date for this host" line.

    ``None`` when the notice applies (or there is no release to judge). Otherwise:
    the feed's newest release is newer but its ``supports`` list does not name this
    host; the running version *is* the newest release; or the running version is
    ahead of the feed's newest release — a development checkout, a release cut
    before its publish — where whether that older release lists the host is beside
    the point. ``doctor``, ``status`` and ``self-update`` all take the reason from
    here, so they agree.
    """
    if release is None:
        return None
    if is_newer(release.version, running):
        if supports_host(release, edition, channel, host_version):
            return None
        return f"{release.version} does not list host {host_version} ({edition}/{channel}) as supported"
    if is_newer(running, release.version):
        return f"this is newer than the newest published release ({release.version})"
    return "this is the newest release"


# --- the cache record ------------------------------------------------------------------------------------------------


@dataclass
class UpdateCheck:
    """One check's outcome — what ``cache/self-update.json`` holds and what the surfaces show."""

    status: str  # ok | unreachable | disabled | offline | no-feed | not-checked
    checked_at: str | None = None
    edition: str | None = None
    channel: str | None = None
    running: str = ""
    host_version: str | None = None
    latest: Release | None = None
    detail: str = ""
    fresh: bool = False  # this call reached the endpoint (as opposed to serving the cache)

    @property
    def notice(self) -> str | None:
        return notice_text(self.latest, running=self.running, edition=self.edition, channel=self.channel, host_version=self.host_version)

    @property
    def available(self) -> bool:
        return self.notice is not None

    @property
    def reason(self) -> str | None:
        """Why there is nothing to install (``None`` when :attr:`notice` applies or no release is known); see :func:`up_to_date_reason`."""
        return up_to_date_reason(self.latest, running=self.running, edition=self.edition, channel=self.channel, host_version=self.host_version)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "checkedAt": self.checked_at,
            "edition": self.edition,
            "channel": self.channel,
            "running": self.running,
            "hostVersion": self.host_version,
            "status": self.status,
            "detail": self.detail,
            "latest": self.latest.to_dict() if self.latest else None,
            "notice": self.notice,
        }

    def summary(self) -> dict[str, Any]:
        """What the Loader's ``/state`` and the App show: the record plus the two derived facts."""
        return {**self.to_dict(), "available": self.available, "version": self.latest.version if self.latest else None, "notes": self.latest.url if self.latest else None, "supportsHost": supports_host(self.latest, self.edition, self.channel, self.host_version)}

    @classmethod
    def load(cls, path: Path) -> "UpdateCheck | None":
        """The cached record as written (``None`` when absent or unreadable); :meth:`regrade` applies the current facts."""
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(document, dict) or document.get("schema") != 1:
            return None
        return cls(
            str(document.get("status") or "unreachable"),
            document.get("checkedAt") if isinstance(document.get("checkedAt"), str) else None,
            document.get("edition") if isinstance(document.get("edition"), str) else None,
            document.get("channel") if isinstance(document.get("channel"), str) else None,
            str(document.get("running") or ""),
            document.get("hostVersion") if isinstance(document.get("hostVersion"), str) else None,
            Release.from_dict(document.get("latest")),
            str(document.get("detail") or ""),
        )

    def regrade(self, *, running: str, edition: str | None, channel: str | None, host_version: str | None) -> "UpdateCheck":
        """The same record judged for the current facts (the notice is derived, never stored as truth)."""
        self.running, self.edition, self.channel, self.host_version = running, edition, channel, host_version
        return self

    def save(self, path: Path) -> None:
        path = Path(path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass  # a cache that cannot be written only means the next command checks again

    def is_current(self, *, now: datetime, running: str, edition: str | None, channel: str | None) -> bool:
        """Within the interval and made for the same FloofyCrew version, edition and channel."""
        moment = _parse_stamp(self.checked_at)
        if moment is None or moment > now + timedelta(minutes=5):
            return False
        return now - moment < CHECK_INTERVAL and self.running == running and self.edition == edition and self.channel == channel


def check(
    feed: ReleaseFeed | None,
    cache_path: Path,
    *,
    running: str,
    edition: str | None,
    channel: str | None,
    host_version: str | None,
    opener: Callable[..., Any] | None = None,
    enabled: bool = True,
    offline: bool = False,
    force: bool = False,
    now: datetime | None = None,
    timeout: float = TIMEOUT,
) -> UpdateCheck:
    """The daily check (see the module docstring): never raises, never blocks for long.

    * ``enabled`` false (``updates.check``) → ``disabled``; no network, the cache untouched.
    * ``offline`` (``--offline``) → the cached record when there is one, else ``offline``; no network.
    * a cached record within 24 h for the same running version, edition and channel → served as is (unless ``force``).
    * else one GET: success → ``ok``; failure → ``unreachable`` (the previous ``latest`` kept), cached for a day.
    * no feed (no adapter, an isolated run) → ``no-feed``; nothing cached.
    """
    moment = now or _utc_now()
    cached = UpdateCheck.load(cache_path)
    if not enabled:
        return UpdateCheck("disabled", None, edition, channel, running, host_version, None, "updates.check is false (floofy config set updates.check true re-enables the daily check)")
    if offline:
        if cached is not None:
            cached.detail = (cached.detail + "; " if cached.detail else "") + "offline: served from the cache"
            return cached.regrade(running=running, edition=edition, channel=channel, host_version=host_version)
        return UpdateCheck("offline", None, edition, channel, running, host_version, None, "offline: no check made and nothing cached")
    if feed is None:
        return UpdateCheck("no-feed", None, edition, channel, running, host_version, None, "no release feed for this edition (no edition adapter installed, or an isolated run)")
    if cached is not None and not force and cached.is_current(now=moment, running=running, edition=edition, channel=channel):
        return cached.regrade(running=running, edition=edition, channel=channel, host_version=host_version)
    try:
        release = fetch_release(feed, opener=opener, timeout=timeout)
        outcome = UpdateCheck("ok", _stamp(moment), edition, channel, running, host_version, release, f"checked {feed.label or feed.url}", fresh=True)
    except FeedError as exc:
        outcome = UpdateCheck("unreachable", _stamp(moment), edition, channel, running, host_version, cached.latest if cached else None, str(exc), fresh=True)
    outcome.save(cache_path)
    return outcome


# --- the staged swap --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class StagePlan:
    """Where the artifacts go: the zipapp beside its target, the Loader archive under ``pending/self-update/``."""

    target: Path
    pending: Path

    @property
    def staging(self) -> Path:
        return self.target.with_name(self.target.name + ".new")

    @property
    def stage_dir(self) -> Path:
        return self.pending / STAGE_DIR

    @property
    def marker(self) -> Path:
        return self.pending / STAGE_MARKER


def download_release(release: Release, plan: StagePlan, *, opener: Callable[..., Any] | None = None, timeout: float = 60.0) -> dict[str, Any]:
    """Fetch ``SHA256SUMS``, then the zipapp (to ``<target>.new``) and the Loader archive (to the stage), each verified.

    The expected digest of every artifact comes from ``SHA256SUMS``; when the
    release notes' table also names the file, the two must agree — a
    disagreement is refused before any download, like a hash mismatch.
    """
    sums = parse_sums(fetch_bytes(release.assets[SUMS_NAME], opener=opener, timeout=timeout).decode("utf-8", "replace"))
    loader = loader_archive_name(sums)
    if PYZ_NAME not in sums or loader is None:
        raise SelfUpdateError(f"{release.assets[SUMS_NAME]}: SHA256SUMS names no {PYZ_NAME} or no Loader app archive; refusing")
    for name in (PYZ_NAME, loader):
        stated = release.sha256s.get(name)
        if stated and stated != sums[name]:
            raise SelfUpdateError(f"{name}: the release notes say sha256 {stated[:12]}… but SHA256SUMS says {sums[name][:12]}…; refusing")
    loader_url = _renamed(release.assets.get("loaderApp") or "", loader)
    if not loader_url:
        raise SelfUpdateError("the release names no Loader app archive to download")
    plan.staging.unlink(missing_ok=True)
    pyz_digest = download_to(release.assets[PYZ_NAME], plan.staging, expected_sha256=sums[PYZ_NAME], opener=opener, timeout=timeout)
    archive = plan.stage_dir / loader
    try:
        archive_digest = download_to(loader_url, archive, expected_sha256=sums[loader], opener=opener, timeout=timeout)
    except SelfUpdateError:
        plan.staging.unlink(missing_ok=True)
        raise
    return {"pyz": {"path": str(plan.staging), "sha256": pyz_digest, "url": release.assets[PYZ_NAME]}, "loaderApp": {"path": str(archive), "sha256": archive_digest, "url": loader_url, "name": loader}, "sums": sums}


def _renamed(url: str, name: str) -> str:
    """``url`` with its last path segment replaced by ``name`` (the archive name ``SHA256SUMS`` states is authoritative)."""
    if not url:
        return ""
    parts = urllib.parse.urlsplit(url)
    head, _, tail = parts.path.rpartition("/")
    if tail == name:
        return url
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, f"{head}/{name}", parts.query, parts.fragment))


def swap(plan: StagePlan) -> Path:
    """``os.replace`` the verified ``<target>.new`` over the target (atomic on one filesystem), keeping the target's mode."""
    if not plan.staging.is_file():
        raise SelfUpdateError(f"nothing staged at {plan.staging}")
    mode = None
    try:
        mode = plan.target.stat().st_mode & 0o7777
    except OSError:
        pass
    if mode is not None:
        os.chmod(plan.staging, mode)
    os.replace(plan.staging, plan.target)
    return plan.target


def write_stage(plan: StagePlan, *, version: str, archive: Path, sha256: str, by: str) -> dict[str, Any]:
    """The marker ``pending/self-update.json`` the trigger path reads (``floofy apply``)."""
    record = {"schema": 1, "version": version, "archive": str(archive), "sha256": sha256, "stagedAt": _stamp(_utc_now()), "by": by, "applied": False}
    plan.marker.parent.mkdir(parents=True, exist_ok=True)
    plan.marker.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def read_stage(pending: Path) -> dict[str, Any] | None:
    """The staged Loader app update (``None`` when nothing is staged or the archive vanished)."""
    marker = Path(pending) / STAGE_MARKER
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or not isinstance(record.get("archive"), str):
        return None
    record["present"] = Path(record["archive"]).is_file()
    return record
