"""Payload discovery: every installed copy of the host on this machine (Requirement 5.1).

A *payload* is one installed KiroCrew on disk — a bundle version directory, a
pipx or managed venv, a desktop bundle's ``backend-dist``. One machine commonly
holds several (the updater keeps previous version directories and the running
gateway may still serve an older one — Requirement 6.4), so the Patcher works on
the full list, never only on "the current one".

The core owns the edition-neutral machinery, ported from the standalone theme
patcher that preceded FloofyCrew:

* :func:`find_package_dirs` — a pruned, layout-agnostic walk that locates the
  ``kiro_crew`` package at any depth below a root (flat Linux layout, venv
  ``lib/python3.*/site-packages``, the macOS ``.app`` nesting) without guessing
  depths.
* :func:`read_host_version` — the version as the host itself derives it:
  ``__version__`` from ``kiro_crew/__init__.py`` (read with a regex, never
  imported) overridden by the ``BUILD_VERSION`` stamp file the host honours
  beside that module, with the host's own shape rule (the base, or the base plus
  exactly one ``.N`` segment over a bare numeric base).
* :func:`channel_for_version` — the host's release-channel rule for un-stamped
  builds: ``.devN``/``-nightly.`` → ``nightly``, any other prerelease →
  ``insider``, else ``stable``.
* :class:`FindSpecProvider` (the interpreter's own ``find_spec("kiro_crew")``),
  :class:`ExtraRootProvider` (``--root`` extras) and :func:`discover_payloads`,
  which runs every provider, de-duplicates by package directory and returns a
  :class:`DiscoveryResult` whose ``searched_roots`` lists **every** root that was
  looked at, so a miss is reportable rather than a bare "nothing found".

Edition adapters (Requirement 10.1) contribute :class:`PayloadProvider`
implementations for their own install layouts and set ``edition``/``channel``/
``current`` from edition facts the core must not know (bundle bookkeeping files,
companion packages). Nothing here names either edition.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable, Protocol, runtime_checkable

from .semver import HostVersion, InvalidVersion

__all__ = [
    "DiscoveryResult",
    "EditionProbe",
    "ExtraRootProvider",
    "FindSpecProvider",
    "PACKAGE_NAME",
    "Payload",
    "PayloadProvider",
    "channel_for_version",
    "default_edition_probe",
    "discover_payloads",
    "find_package_dirs",
    "make_payload",
    "path_token",
    "read_host_version",
    "venv_site_packages",
]

#: The host's Python package; a payload is wherever one of these lives.
PACKAGE_NAME = "kiro_crew"

#: Directory names never descended into during the walk.
_WALK_PRUNE: frozenset[str] = frozenset({"node_modules", "__pycache__"})
#: The ``.app`` nesting is ~11 parts below a bundle version directory.
_WALK_MAX_DEPTH = 12

_VERSION_LITERAL_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.M)
_BARE_RELEASE_RE = re.compile(r"[0-9]+(?:\.[0-9]+)*")
_BUILD_SUFFIX_RE = re.compile(r"\.[0-9]+")
_BUILD_VERSION_FILENAME = "BUILD_VERSION"
_BUILD_VERSION_MAX_CHARS = 64

#: ``edition_probe(package_dir) -> edition`` — adapters know their companion packages.
EditionProbe = Callable[[Path], str]


@dataclass(frozen=True)
class Payload:
    """One installed copy of the host (Requirement 5.1).

    ``id`` is stable across runs and safe as a file-name stem once passed through
    :func:`floofy_core.deploy.manifest_filename`: ``<kind>:<discriminator>:<version>``
    (``<kind>:<version>`` when the kind admits one payload per version). ``root``
    is the install root the provider searched, ``package_dir`` the ``kiro_crew``
    directory, ``dist_dir`` its ``static/dist`` (may not exist on a payload with
    no frontend). ``current`` is what the launcher would run right now;
    ``dormant`` payloads are still patched (Requirement 6.4). ``source`` names
    the provider that found it.
    """

    id: str
    root: Path
    package_dir: Path
    dist_dir: Path
    host_version: HostVersion
    edition: str = "unknown"
    channel: str | None = None
    interpreter: Path | None = None
    current: bool = False
    source: str = ""

    @property
    def index_html(self) -> Path:
        return self.dist_dir / "index.html"

    @property
    def assets_dir(self) -> Path:
        return self.dist_dir / "assets"

    @property
    def has_frontend(self) -> bool:
        return self.index_html.is_file()

    def describe(self) -> str:
        flags = ["current" if self.current else "dormant"]
        if self.channel:
            flags.append(self.channel)
        return f"{self.id} ({self.edition}, {', '.join(flags)}) at {self.root}"


@runtime_checkable
class PayloadProvider(Protocol):
    """A source of payloads; edition adapters implement this for their layouts."""

    name: str

    def roots(self) -> list[Path]:
        """Every root this provider would search (listed on a miss, Requirement 5.1)."""

    def discover(self) -> list[Payload]:
        """The payloads found under :meth:`roots`."""


@dataclass
class DiscoveryResult:
    """What :func:`discover_payloads` found and where it looked."""

    payloads: list[Payload] = field(default_factory=list)
    searched_roots: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def current(self) -> Payload | None:
        for payload in self.payloads:
            if payload.current:
                return payload
        return None

    def by_id(self, payload_id: str) -> Payload | None:
        for payload in self.payloads:
            if payload.id == payload_id:
                return payload
        return None

    def format_miss(self) -> str:
        """The diagnostic for "no payload found": every searched root, one per line."""
        lines = ["cannot locate any KiroCrew payload."]
        if self.searched_roots:
            lines.extend(f"  - searched root: {root}" for root in self.searched_roots)
        else:
            lines.append("  - no payload roots to search (no provider found an install location)")
        lines.extend(f"  - {note}" for note in self.notes)
        lines.append("Pass --root <install-dir> to search a location explicitly.")
        return "\n".join(lines)


# --- layout-agnostic walk ------------------------------------------------------------


def find_package_dirs(root: Path, *, max_depth: int = _WALK_MAX_DEPTH) -> list[Path]:
    """Locate every ``kiro_crew`` package directory under ``root``.

    Port of the theme patcher's pruned walk: descend only into ``kiro_crew`` once
    inside a ``site-packages``/``dist-packages`` directory, skip ``node_modules``,
    ``__pycache__`` and dot-directories, never follow symlinks, stop at each
    package found (nothing below a payload matters) and at ``max_depth``.
    A ``kiro_crew`` directory without ``__init__.py`` is not a package.
    """
    found: list[Path] = []
    root = Path(root)
    if not root.is_dir():
        return found
    base_depth = len(root.parts)
    for dirpath, dirnames, _filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        if here.name == PACKAGE_NAME:
            if (here / "__init__.py").is_file():
                found.append(here)
            dirnames[:] = []
            continue
        if len(here.parts) - base_depth >= max_depth:
            dirnames[:] = []
            continue
        if here.name in ("site-packages", "dist-packages"):
            dirnames[:] = [d for d in dirnames if d == PACKAGE_NAME]
        else:
            dirnames[:] = sorted(d for d in dirnames if d not in _WALK_PRUNE and not d.startswith("."))
    return sorted(found)


def venv_site_packages(venv: Path) -> Path | None:
    """The ``lib/python3.*/site-packages`` of a venv marked by ``pyvenv.cfg``, or ``None``."""
    venv = Path(venv)
    if not (venv / "pyvenv.cfg").is_file():
        return None
    candidates = sorted((venv / "lib").glob("python3.*/site-packages")) if (venv / "lib").is_dir() else []
    candidates += sorted((venv / "Lib").glob("site-packages"))  # Windows layout
    return candidates[0] if candidates else None


# --- version and channel -------------------------------------------------------------


def _build_version_override(base: str, candidate: str | None) -> str | None:
    """The host's own rule for the ``BUILD_VERSION`` stamp (``kiro_crew/__init__.py``).

    Accepted: the base itself, or the base plus exactly one ``.N`` numeric segment,
    over a bare numeric base. Anything else is ignored and the base stands.
    """
    if not candidate or not base:
        return None
    value = candidate.strip()
    if value == base:
        return value
    if not _BARE_RELEASE_RE.fullmatch(base) or not value.startswith(base):
        return None
    return value if _BUILD_SUFFIX_RE.fullmatch(value[len(base) :]) else None


def read_host_version(package_dir: Path) -> HostVersion:
    """The version the host reports for the package at ``package_dir``, without importing it.

    ``__version__`` is read from ``__init__.py`` with a regex (the literal the
    release lanes rewrite), then the ``BUILD_VERSION`` stamp beside it is applied
    with the host's shape rule. Raises :class:`InvalidVersion` when neither yields
    a recognisable version.
    """
    init = Path(package_dir) / "__init__.py"
    try:
        text = init.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise InvalidVersion(f"cannot read {init}: {exc}") from exc
    match = _VERSION_LITERAL_RE.search(text)
    if not match:
        raise InvalidVersion(f"no __version__ literal in {init}")
    base = match.group(1).strip()
    stamp = Path(package_dir) / _BUILD_VERSION_FILENAME
    try:
        raw = stamp.read_text(encoding="utf-8")[: _BUILD_VERSION_MAX_CHARS + 1]
    except (OSError, ValueError):
        raw = ""
    override = _build_version_override(base, raw) if raw.strip() and len(raw) <= _BUILD_VERSION_MAX_CHARS else None
    return HostVersion.parse(override or base)


def channel_for_version(version: HostVersion) -> str:
    """The host's release-channel rule for a version string (``release_channel.py``).

    ``1.2.3.dev<stamp>`` / ``1.2.3-nightly.<stamp>`` → ``nightly``; ``1.2.3rc4`` /
    ``1.2.3-insider.4`` / any other prerelease → ``insider``; otherwise ``stable``.
    Adapters whose distribution has its own channel bookkeeping override this.
    """
    if version.dev is not None or (version.tag_name or "").lower() == "nightly":
        return "nightly"
    if version.pre is not None or version.tag is not None:
        return "insider"
    return "stable"


def default_edition_probe(package_dir: Path) -> str:
    """Edition-neutral fallback: an un-stamped build is the public edition; a stamped one is unknown here.

    Only an edition adapter can recognise its own companion package, so the
    combined probe the CLI builds consults the adapters first and falls back to
    this rule.
    """
    stamp = Path(package_dir) / _BUILD_VERSION_FILENAME
    return "unknown" if stamp.is_file() else "external"


def path_token(path: Path, length: int = 8) -> str:
    """A short stable token for a path, for payload ids discriminated by location."""
    return hashlib.sha256(Path(path).as_posix().encode("utf-8")).hexdigest()[:length]


def _interpreter_for(package_dir: Path, root: Path) -> Path | None:
    """``<venv>/bin/python`` for a venv root, ``sys.executable``-shaped guess otherwise."""
    for candidate in (root / "bin" / "python", root / "bin" / "python3", root / "Scripts" / "python.exe"):
        if candidate.exists():
            return candidate
    return None


def make_payload(
    *,
    kind: str,
    root: Path,
    package_dir: Path,
    source: str,
    discriminator: str | None = None,
    version: HostVersion | None = None,
    edition: str | None = None,
    channel: str | None = None,
    interpreter: Path | None = None,
    current: bool = False,
    edition_probe: EditionProbe | None = None,
    derive_channel: bool = True,
) -> Payload:
    """Assemble a :class:`Payload` from a located package directory.

    Providers call this with what they know; everything not supplied is derived
    (version from the package, channel from the version unless ``derive_channel``
    is false, edition from the probe, interpreter from a venv-style ``bin/python``).
    """
    package_dir = Path(package_dir)
    host_version = version or read_host_version(package_dir)
    probe = edition_probe or default_edition_probe
    parts = [kind]
    if discriminator:
        parts.append(discriminator)
    parts.append(str(host_version))
    return Payload(
        id=":".join(parts),
        root=Path(root),
        package_dir=package_dir,
        dist_dir=package_dir / "static" / "dist",
        host_version=host_version,
        edition=edition or probe(package_dir),
        channel=channel or (channel_for_version(host_version) if derive_channel else None),
        interpreter=interpreter or _interpreter_for(package_dir, Path(root)),
        current=current,
        source=source,
    )


# --- generic providers ---------------------------------------------------------------


def _launcher_targets() -> list[Path]:
    """Resolved targets of the ``kirocrew`` launchers a shell would run."""
    launchers = {Path.home() / ".local" / "bin" / "kirocrew"}
    on_path = shutil.which("kirocrew")
    if on_path:
        launchers.add(Path(on_path))
    resolved: list[Path] = []
    for launcher in sorted(launchers):
        try:
            resolved.append(launcher.resolve(strict=True))
        except OSError:
            continue
    return resolved


def launcher_points_into(root: Path) -> bool:
    """``True`` when a ``kirocrew`` launcher on this machine resolves inside ``root``."""
    root = Path(root).resolve()
    for target in _launcher_targets():
        try:
            target.relative_to(root)
            return True
        except ValueError:
            continue
    return False


@dataclass
class FindSpecProvider:
    """The ``kiro_crew`` importable from an interpreter (plain ``pip install`` case).

    Defaults to the running interpreter's ``find_spec``; pass ``interpreter`` to
    ask another Python (the host's) with a short subprocess instead.
    """

    interpreter: Path | None = None
    edition_probe: EditionProbe | None = None
    name: str = "find-spec"

    def roots(self) -> list[Path]:
        package_dir = self._locate()
        return [package_dir.parent] if package_dir else []

    def _locate(self) -> Path | None:
        if self.interpreter is None or Path(self.interpreter).resolve() == Path(sys.executable).resolve():
            try:
                spec = importlib.util.find_spec(PACKAGE_NAME)
            except (ImportError, ValueError):
                return None
            if spec and spec.origin:
                return Path(spec.origin).resolve().parent
            return None
        import subprocess  # local import: only the cross-interpreter path pays for it

        code = f"import importlib.util as u; s=u.find_spec({PACKAGE_NAME!r}); print(s.origin if s and s.origin else '')"
        try:
            out = subprocess.run([str(self.interpreter), "-I", "-c", code], capture_output=True, text=True, timeout=20, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        origin = out.stdout.strip()
        return Path(origin).resolve().parent if out.returncode == 0 and origin else None

    def discover(self) -> list[Payload]:
        package_dir = self._locate()
        if package_dir is None or not (package_dir / "__init__.py").is_file():
            return []
        root = package_dir.parent
        interpreter = Path(self.interpreter) if self.interpreter else Path(sys.executable)
        try:
            return [
                make_payload(
                    kind="spec",
                    root=root,
                    package_dir=package_dir,
                    source=self.name,
                    interpreter=interpreter,
                    current=launcher_points_into(_venv_root_of(package_dir) or root),
                    edition_probe=self.edition_probe,
                )
            ]
        except InvalidVersion:
            return []


def _venv_root_of(package_dir: Path) -> Path | None:
    """The venv directory above a ``site-packages`` package, if the layout is a venv."""
    for parent in Path(package_dir).parents:
        if (parent / "pyvenv.cfg").is_file():
            return parent
    return None


@dataclass
class ExtraRootProvider:
    """Roots the user named with ``--root`` (Requirement 5.1), searched layout-agnostically."""

    extra_roots: list[Path]
    edition_probe: EditionProbe | None = None
    name: str = "extra-root"

    def roots(self) -> list[Path]:
        return [Path(r) for r in self.extra_roots]

    def discover(self) -> list[Payload]:
        payloads: list[Payload] = []
        for root in self.roots():
            for package_dir in find_package_dirs(root):
                try:
                    payloads.append(
                        make_payload(
                            kind="root",
                            root=root,
                            package_dir=package_dir,
                            source=self.name,
                            discriminator=path_token(package_dir),  # one root may hold several package trees
                            current=launcher_points_into(root),
                            edition_probe=self.edition_probe,
                        )
                    )
                except InvalidVersion:
                    continue
        return payloads


# --- the entry point -----------------------------------------------------------------


def discover_payloads(
    providers: Iterable[PayloadProvider],
    extra_roots: Iterable[Path | str] = (),
    *,
    edition_probe: EditionProbe | None = None,
    include_find_spec: bool = True,
) -> DiscoveryResult:
    """Run every provider (plus ``--root`` extras and ``find_spec``) and merge the results.

    Payloads are de-duplicated by resolved ``package_dir``; the first provider's
    record wins, ``current`` is OR-ed across duplicates. The result lists every
    searched root so a miss can be diagnosed (Requirement 5.1).
    """
    all_providers: list[PayloadProvider] = list(providers)
    extras = [Path(r).expanduser() for r in extra_roots]
    if extras:
        all_providers.append(ExtraRootProvider(extras, edition_probe=edition_probe))
    if include_find_spec:
        all_providers.append(FindSpecProvider(edition_probe=edition_probe))

    result = DiscoveryResult()
    seen: dict[Path, int] = {}
    for provider in all_providers:
        try:
            roots = provider.roots()
        except OSError as exc:
            result.notes.append(f"{provider.name}: cannot list roots: {exc}")
            roots = []
        for root in roots:
            if root not in result.searched_roots:
                result.searched_roots.append(root)
        try:
            found = provider.discover()
        except OSError as exc:
            result.notes.append(f"{provider.name}: discovery failed: {exc}")
            continue
        for payload in found:
            key = payload.package_dir.resolve()
            if key in seen:
                index = seen[key]
                if payload.current and not result.payloads[index].current:
                    result.payloads[index] = replace(result.payloads[index], current=True)
                continue
            seen[key] = len(result.payloads)
            result.payloads.append(payload)
    result.payloads.sort(key=lambda p: (p.host_version, p.id))
    return result
