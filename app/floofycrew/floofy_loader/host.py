"""``floofy.host`` — the host facts mods may depend on (Requirement 3.3, 10.1).

Mods never read ``kiro_crew`` internals for these; they read :data:`floofy.host`
(a :class:`HostFacts`) and pin their compatibility on ``floofy.api_version``.

Where each fact comes from (0.7.0.5 host code read for this):

* ``version`` — ``kiro_crew.__version__`` as the running interpreter sees it:
  the release literal with the distribution's ``BUILD_VERSION`` stamp already
  applied (``kiro_crew/__init__.py`` L10–L109, ``_apply_build_version_file``),
  so an internal build reports ``0.7.0.5`` and a public one ``0.7.0`` /
  ``0.7.0rc4`` / ``0.7.0.dev20260918``.
* ``build_version`` — that stamp (``X.Y.Z.N``) when present, else ``None``;
  ``base_version`` — the ``X.Y.Z`` :class:`floofy_core.semver.Version` that
  ``kirocrew.version`` ranges are matched against.
* ``channel`` — the edition adapter's answer when its payload provider
  recognises the running payload (the internal distribution's channel lives in
  its bookkeeping, not in the bytes: beta and stable are the same wheel);
  otherwise the host's own rule ``kiro_crew.release_channel.channel()``
  (``release_channel.py`` L83–L96: ``.dev``/``-nightly.`` → nightly, any other
  prerelease → insider, else stable), ported as
  :func:`floofy_core.payloads.channel_for_version`. ``None`` when an internal
  build could not be matched to its bookkeeping (the channel is then unknown,
  not "stable").
* ``edition`` — ``floofy_core.editions.combined_edition_probe()`` over the
  package directory: each ``floofy_edition_*`` adapter recognises its own
  companion package; the neutral fallback treats an unstamped build as
  ``external``.
* ``profile`` — ``KIROCREW_PROFILE`` (the operator/launcher override that the
  host's ``platform/profile.py`` L77–L96 ``resolve_profile`` honours first;
  the internal launcher exports ``amazon``, an alias of ``enterprise``, L53),
  else ``enterprise`` for the internal edition and ``standalone`` otherwise.
* ``payload_root`` / ``package_dir`` — from ``kiro_crew.__file__``: the
  ``kiro_crew`` directory and the install root above it (the venv or version
  directory holding ``pyvenv.cfg`` / ``lib/pythonX.Y/site-packages``, else the
  directory holding the package).
* ``host_home`` — the host's data home, ``kiro_crew.config.paths.config_dir()``
  (``KIROCREW_HOME`` when set and valid, else ``~/.kiro/crew``; ``paths.py``
  L293 onwards) — computed without the host when it is not importable;
  ``data_home`` is ``<host_home>/floofy``; ``interpreter`` is ``sys.executable``.

Everything here is fail-soft: a missing host name never raises out of
:func:`read_host_facts`; the corresponding field is ``None`` or its fallback.
"""
from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from floofy_core import API_VERSION, __version__ as FRAMEWORK_VERSION
from floofy_core.semver import HostVersion, InvalidVersion, Version

__all__ = ["HostFacts", "default_host_home", "read_host_facts"]

#: The host package whose facts we read.
HOST_PACKAGE = "kiro_crew"

#: The edition vocabulary of ``floofy.json`` ``kirocrew.editions`` (schema enum).
EDITION_INTERNAL = "internal"
EDITION_EXTERNAL = "external"


@dataclass(frozen=True)
class HostFacts:
    """What ``floofy.host`` exposes (Requirement 3.3)."""

    version: str
    base_version: Version
    build_version: str | None
    channel: str | None
    edition: str
    profile: str
    payload_root: Path | None
    package_dir: Path | None
    host_home: Path
    data_home: Path
    interpreter: Path
    #: How the facts were derived (``"adapter"``, ``"package"``, ``"fallback"``) — for ``doctor``.
    source: str = "fallback"
    payload_id: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def host_version(self) -> HostVersion:
        try:
            return HostVersion.parse(self.version)
        except InvalidVersion:
            return HostVersion(self.base_version.major, self.base_version.minor, self.base_version.patch, text=self.version)

    @property
    def api_version(self) -> str:
        return API_VERSION

    @property
    def framework_version(self) -> str:
        return FRAMEWORK_VERSION

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        for key, value in list(raw.items()):
            if isinstance(value, Path):
                raw[key] = str(value)
        raw["base_version"] = str(self.base_version)
        raw["notes"] = list(self.notes)
        raw["api_version"] = API_VERSION
        raw["framework_version"] = FRAMEWORK_VERSION
        return raw


def default_host_home(env: dict[str, str] | None = None) -> Path:
    """``KIROCREW_HOME`` or ``~/.kiro/crew`` — the host's own default without importing it."""
    environment = os.environ if env is None else env
    override = (environment.get("KIROCREW_HOME") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".kiro" / "crew"


def _host_home(host: ModuleType | None, env: dict[str, str] | None) -> Path:
    """Prefer the host's validated answer (``config_dir()``), fall back to the plain rule."""
    if host is not None and env is None:
        try:
            from kiro_crew.config.paths import config_dir  # type: ignore[import-not-found]  # noqa: PLC0415

            return Path(config_dir())
        except Exception:  # noqa: BLE001 - the host may not expose this on every version
            pass
    return default_host_home(env)


def _import_host() -> ModuleType | None:
    try:
        return sys.modules.get(HOST_PACKAGE) or __import__(HOST_PACKAGE)
    except Exception:  # noqa: BLE001 - not inside a host, or a broken one
        return None


def _package_dir(host: ModuleType | None) -> Path | None:
    origin = getattr(host, "__file__", None) if host is not None else None
    if not origin:
        return None
    try:
        return Path(origin).resolve().parent
    except OSError:
        return None


def _payload_root(package_dir: Path | None) -> Path | None:
    """The install root above ``kiro_crew``: a venv / version dir (``pyvenv.cfg`` or ``lib/pythonX.Y/site-packages``), else the site dir."""
    if package_dir is None:
        return None
    for parent in package_dir.parents:
        if (parent / "pyvenv.cfg").is_file():
            return parent
    site = package_dir.parent
    if site.name == "site-packages" and site.parent.name.startswith("python") and site.parent.parent.name == "lib":
        return site.parent.parent.parent
    return site


def _host_channel_rule(version: str, host: ModuleType | None) -> str | None:
    """The host's own ``release_channel.channel()`` when importable, else the ported rule."""
    if host is not None:
        try:
            from kiro_crew.release_channel import channel  # type: ignore[import-not-found]  # noqa: PLC0415

            return str(channel(version))
        except Exception:  # noqa: BLE001
            pass
    try:
        from floofy_core.payloads import channel_for_version  # noqa: PLC0415

        return channel_for_version(HostVersion.parse(version))
    except (InvalidVersion, Exception):  # noqa: BLE001
        return None


def _adapter_view(package_dir: Path | None, adapters: list[ModuleType] | None) -> tuple[str | None, str | None, str | None, list[str]]:
    """``(edition, channel, payload_id, notes)`` from the edition adapters (Requirement 10.1).

    The adapters' payload providers recognise the running payload by its
    ``package_dir`` (the internal one reads its channel from the distribution's
    bookkeeping); ``FindSpecProvider`` covers a plain install. Edition comes from
    the combined probe either way.
    """
    notes: list[str] = []
    edition: str | None = None
    channel: str | None = None
    payload_id: str | None = None
    if package_dir is None:
        return edition, channel, payload_id, notes
    try:
        from floofy_core.editions import combined_edition_probe, edition_providers, load_edition_adapters  # noqa: PLC0415
        from floofy_core.payloads import discover_payloads  # noqa: PLC0415

        loaded = load_edition_adapters() if adapters is None else adapters
        probe = combined_edition_probe(loaded)
        edition = probe(package_dir)
        if os.environ.get("FLOOFY_NO_ADAPTERS") == "1":
            notes.append("FLOOFY_NO_ADAPTERS=1: adapter payload providers not consulted")
            return edition, None, None, notes
        result = discover_payloads(edition_providers(loaded), edition_probe=probe, include_find_spec=False)
        for payload in result.payloads:
            if payload.package_dir.resolve() == package_dir:
                channel = payload.channel
                payload_id = payload.id
                if payload.edition and payload.edition != "unknown":
                    edition = payload.edition
                break
        else:
            notes.append("running payload not recognised by any edition adapter provider")
    except Exception as exc:  # noqa: BLE001 - adapters are optional at runtime
        notes.append(f"edition adapters unavailable: {type(exc).__name__}: {exc}")
    return edition, channel, payload_id, notes


def read_host_facts(
    *,
    host: ModuleType | None = None,
    env: dict[str, str] | None = None,
    adapters: list[ModuleType] | None = None,
) -> HostFacts:
    """Assemble :class:`HostFacts` for the running process.

    ``host`` defaults to the importable ``kiro_crew`` (``None`` outside a host:
    the facts then describe an unknown host, version ``0.0.0``). ``env`` and
    ``adapters`` exist for tests; ``adapters=[]`` uses the neutral fallbacks only.
    """
    environment = os.environ if env is None else env
    host_module = _import_host() if host is None else host
    notes: list[str] = []

    raw_version = getattr(host_module, "__version__", None) if host_module is not None else None
    version = str(raw_version) if raw_version else "0.0.0"
    if not raw_version:
        notes.append("kiro_crew is not importable: host facts are placeholders")
    try:
        parsed = HostVersion.parse(version)
    except InvalidVersion as exc:
        notes.append(f"unrecognised host version {version!r}: {exc}")
        parsed = HostVersion(0, 0, 0, text=version)
    build_version = version if parsed.build is not None else None

    package_dir = _package_dir(host_module)
    edition, channel, payload_id, adapter_notes = _adapter_view(package_dir, adapters)
    notes.extend(adapter_notes)
    source = "adapter" if payload_id else ("package" if package_dir else "fallback")
    if build_version and edition in (None, "unknown", EDITION_EXTERNAL):
        # Only a stamped distribution reports ``X.Y.Z.N`` (``kiro_crew/__init__.py``
        # L10–L46): the neutral probe cannot see the stamp when it reads a fake or
        # a payload it does not recognise, but the version already says "internal".
        edition = EDITION_INTERNAL
    if edition is None:
        edition = EDITION_EXTERNAL
    if channel is None and payload_id is None:
        # No bookkeeping matched. The bytes only tell the channel of an unstamped
        # (public) build; a stamped internal build's beta/stable is unknown here.
        channel = None if build_version else _host_channel_rule(version, host_module)

    profile = (environment.get("KIROCREW_PROFILE") or "").strip() or (
        "enterprise" if edition == EDITION_INTERNAL else "standalone"
    )
    host_home = _host_home(host_module, env)
    return HostFacts(
        version=version,
        base_version=parsed.base,
        build_version=build_version,
        channel=channel,
        edition=edition,
        profile=profile,
        payload_root=_payload_root(package_dir),
        package_dir=package_dir,
        host_home=host_home,
        data_home=host_home / "floofy",
        interpreter=Path(sys.executable),
        source=source,
        payload_id=payload_id,
        notes=tuple(notes),
    )
