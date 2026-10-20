"""The SPA reporter, driven from Python (Requirement 4.5, 9.1; design "SPA host — Reporter").

Two ways to evaluate the surface registry (``spa-host/src/surfaces.json``, the
single source of truth, shipped in the Loader app's ``ui/``):

* :func:`check_bundle_fingerprints` — **offline**, standard library only: the
  ``bundle`` fingerprints against a ``static/dist`` directory on disk. Chunks are
  discovered the way the browser does it (stems from the shell's module tag and
  ``modulepreload`` hints, lazy chunks through ``./<stem>-<hash>.js`` references
  in already-known chunks) — never by hash. This is what the Forge can run on a
  provisioned payload without a browser, and what the fixture tests use.
* :func:`run_browser_report` — the **live** report: headless Chromium (Playwright,
  imported inside the function so the core stays stdlib-only at import time)
  authenticates with the dashboard's ``?token=`` link, waits for
  ``window.floofy.report`` (injecting the SPA host module tag itself when no
  loader tag is patched in), calls it and returns the JSON. Both DOM and bundle
  fingerprints, plus the patch report.

Both return the shape the compatibility matrix records under
``framework.spaFingerprints`` (``{matched, total, missed[]}``) next to the full
``matched`` / ``missed`` lists with reasons.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

__all__ = [
    "CHUNK_RE",
    "HOST_MODULE_URL",
    "MOUNT_SELECTOR",
    "MOUNT_SETTLE_MS",
    "PlaywrightMissing",
    "SpaReport",
    "bundle_stems",
    "check_bundle_fingerprints",
    "default_registry_path",
    "discover_chunks",
    "drive_report_page",
    "load_registry",
    "match_bundle",
    "run_browser_report",
]

#: Same-origin URL of the SPA host module (served by the unauthenticated app-UI route).
HOST_MODULE_URL = "/apps/floofycrew/ui/host.mjs"
#: "The SPA has mounted": the React mount point (the ``shell.root`` surface, ``#root``) has rendered a child.
#: Since 0.7.0.5 the entry chunk renders only after a ``/api/ui-prefs`` round-trip, so ``load`` is not enough.
MOUNT_SELECTOR = "#root > *"
#: Settle time after the mount before the report (the surfaces observer debounces mutations by 150 ms).
MOUNT_SETTLE_MS = 500
#: ``<stem>-<hash>.js`` references, as ``/assets/<name>`` URLs or ``./<name>`` module specifiers (mirrors ``surfaces.mjs``).
CHUNK_RE = re.compile(r"(?:/assets/|\./)([A-Za-z][A-Za-z0-9_.]*?)-([A-Za-z0-9_-]{8})\.js\b")
_SHELL_REF_RE = re.compile(r'(?:<script[^>]*\bsrc=|<link[^>]*\bhref=)(["\'])(/assets/[^"\']+\.js)\1', re.I)

PLAYWRIGHT_HINT = (
    "Playwright is not installed for this interpreter. Install it with\n"
    "  python -m pip install playwright && python -m playwright install chromium\n"
    "(the FloofyCrew core itself needs no third-party package; only `verify --spa` drives a browser)."
)


class PlaywrightMissing(RuntimeError):
    """Raised by :func:`run_browser_report` when Playwright cannot be imported."""


def default_registry_path() -> Path | None:
    """``surfaces.json``: beside the SPA host sources in a checkout, or in ``ui/`` next to a vendored core."""
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "spa-host" / "src" / "surfaces.json",  # <repo>/floofy-core/floofy_core/spa_report.py
        here.parents[1] / "ui" / "surfaces.json",  # <app>/floofy_core/spa_report.py → <app>/ui/surfaces.json
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def load_registry(path: Path | str | None = None) -> dict[str, Any]:
    """The surface registry document (``{"schema": 1, "surfaces": [...]}``)."""
    target = Path(path) if path else default_registry_path()
    if target is None or not Path(target).is_file():
        raise FileNotFoundError("surfaces.json not found; pass --registry <path>")
    document = json.loads(Path(target).read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema") != 1 or not isinstance(document.get("surfaces"), list):
        raise ValueError(f"{target}: not a schema-1 surface registry")
    return document


# --- offline bundle check ------------------------------------------------------------------


def discover_chunks(dist_dir: Path, *, wanted: set[str] | None = None) -> dict[str, str]:
    """``stem → file name`` from ``index.html`` references, expanded through chunk texts for lazy stems."""
    dist_dir = Path(dist_dir)
    found: dict[str, str] = {}
    index = dist_dir / "index.html"
    if not index.is_file():
        return found
    shell = index.read_text(encoding="utf-8", errors="replace")
    for _quote, url in _SHELL_REF_RE.findall(shell):
        _add_chunks(found, url)
    if wanted is None or wanted <= set(found):
        return found
    for name in list(found.values()):
        chunk = dist_dir / "assets" / name
        if chunk.is_file():
            _add_chunks(found, chunk.read_text(encoding="utf-8", errors="replace"))
        if wanted <= set(found):
            break
    return found


def _add_chunks(into: dict[str, str], text: str) -> None:
    for stem, digest in CHUNK_RE.findall(text):
        into.setdefault(stem, f"{stem}-{digest}.js")


def match_bundle(fingerprint: dict[str, Any], text: str) -> tuple[str, str]:
    """``("matched"|"missed", reason)`` for one bundle fingerprint (``contains`` or ``regex``; ``count`` some|once)."""
    if isinstance(fingerprint.get("regex"), str):
        hits = len(re.findall(fingerprint["regex"], text))
    elif isinstance(fingerprint.get("contains"), str) and fingerprint["contains"]:
        hits = text.count(fingerprint["contains"])
    else:
        return "missed", "fingerprint declares neither contains nor regex"
    if hits == 0:
        return "missed", "no occurrence in the chunk text"
    if fingerprint.get("count", "some") == "once" and hits > 1:
        return "missed", f"{hits} occurrences; exactly one required"
    return "matched", f"{hits} occurrence(s)"


@dataclass
class SpaReport:
    """The reporter's answer, in the matrix shape."""

    host_version: str | None
    matched: list[str] = field(default_factory=list)
    missed: list[dict[str, Any]] = field(default_factory=list)
    not_applicable: list[dict[str, Any]] = field(default_factory=list)
    chunks: dict[str, str] = field(default_factory=dict)
    source: str = "offline"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.missed

    @property
    def total(self) -> int:
        return len(self.matched) + len(self.missed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hostVersion": self.host_version,
            "source": self.source,
            "matched": list(self.matched),
            "missed": list(self.missed),
            "notApplicable": list(self.not_applicable),
            "total": self.total,
            "spaFingerprints": {"matched": len(self.matched), "total": self.total, "missed": [m["name"] for m in self.missed]},
            "chunks": dict(self.chunks),
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **self.extra,
        }

    @classmethod
    def from_browser(cls, document: dict[str, Any], *, extra: dict[str, Any] | None = None) -> "SpaReport":
        report = cls(
            host_version=document.get("hostVersion"),
            matched=[str(m) for m in document.get("matched", [])],
            missed=[dict(m) if isinstance(m, dict) else {"name": str(m), "kind": "unknown", "reason": ""} for m in document.get("missed", [])],
            not_applicable=[dict(m) for m in document.get("notApplicable", []) if isinstance(m, dict)],
            chunks={str(k): str(v) for k, v in (document.get("chunks") or {}).items()},
            source="browser",
        )
        report.extra = {k: v for k, v in document.items() if k in ("route", "surfaces", "loaderVersion", "mounted")}
        report.extra.update(extra or {})
        return report


def bundle_stems(fingerprint: dict[str, Any]) -> list[str]:
    """The chunk stems a bundle fingerprint may live in, in fallback order (``chunk`` is a string or a list)."""
    chunk = fingerprint.get("chunk")
    stems = chunk if isinstance(chunk, list) else [chunk]
    return [str(stem) for stem in stems if isinstance(stem, str) and stem]


def check_bundle_fingerprints(dist_dir: Path, registry: dict[str, Any] | None = None, *, host_version: str | None = None) -> SpaReport:
    """Evaluate every ``bundle`` fingerprint of the registry against ``dist_dir`` on disk (no browser).

    A fingerprint's ``chunk`` may list several stems (a hook the bundler folds into a
    differently named shared chunk on another build): the stems are tried in order and
    the first referenced chunk whose text matches wins; a miss reports the last stem found.
    """
    document = registry or load_registry()
    surfaces = [s for s in document["surfaces"] if isinstance(s, dict) and isinstance(s.get("bundle"), dict)]
    wanted = {stem for s in surfaces for stem in bundle_stems(s["bundle"])}
    chunks = discover_chunks(Path(dist_dir), wanted=wanted)
    report = SpaReport(host_version=host_version, chunks=chunks)
    texts: dict[str, str | None] = {}
    for surface in surfaces:
        name = f"{surface['name']}#bundle"
        stems = bundle_stems(surface["bundle"])
        outcome: dict[str, Any] | None = None
        for stem in stems:
            file_name = chunks.get(stem)
            if file_name is None:
                continue
            if file_name not in texts:
                path = Path(dist_dir) / "assets" / file_name
                texts[file_name] = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
            text = texts[file_name]
            if not text:
                outcome = {"name": name, "kind": "bundle", "status": "missed", "reason": f"chunk {file_name} is missing or empty", "chunk": file_name}
                continue
            status, reason = match_bundle(surface["bundle"], text)
            outcome = {"name": name, "kind": "bundle", "status": status, "reason": reason, "chunk": file_name}
            if status == "matched":
                break
        if outcome is None:
            report.missed.append({"name": name, "kind": "bundle", "reason": f"no chunk with stem {'|'.join(stems) or '?'} is referenced by the shell", "chunk": None})
        elif outcome["status"] == "matched":
            report.matched.append(name)
        else:
            report.missed.append({k: v for k, v in outcome.items() if k != "status"})
    return report


# --- live browser report -------------------------------------------------------------------


def run_browser_report(
    base_url: str,
    token: str | None,
    *,
    extra_surfaces: list[dict[str, Any]] | None = None,
    inject_host: bool = True,
    timeout_s: float = 45.0,
    path: str = "/",
    headless: bool = True,
    screenshot: Path | None = None,
) -> SpaReport:
    """Open the dashboard headlessly, call ``window.floofy.report()`` and return it.

    ``token`` is the dashboard's one-time link token (``KIROCREW_READY`` prints
    it; ``--token``). When the page has no ``window.floofy`` after it loaded and
    ``inject_host`` is true, the SPA host module tag is added — same URL the
    Patcher's loader tag uses — so the report also works on a payload whose
    ``index.html`` is not patched (``hostTag: "injected"`` in the result).

    The report is taken only once the SPA has mounted (see
    :func:`drive_report_page`): since 0.7.0.5 the host defers ``createRoot().render``
    behind a ``/api/ui-prefs`` round-trip, so ``load`` no longer implies a rendered shell.
    """
    try:
        from playwright.sync_api import Error as PlaywrightError  # noqa: PLC0415
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise PlaywrightMissing(PLAYWRIGHT_HINT) from exc

    base = base_url.rstrip("/")
    query = f"?{urlencode({'token': token})}" if token else ""
    url = f"{base}{path}{query}"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url, wait_until="load", timeout=int(timeout_s * 1000))
            document, host_tag = drive_report_page(page, extra_surfaces=extra_surfaces, inject_host=inject_host, timeout_s=timeout_s)
            if screenshot is not None:
                Path(screenshot).parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(screenshot))
        except PlaywrightError as exc:
            raise RuntimeError(f"browser step failed: {exc}") from exc
        finally:
            browser.close()
    return SpaReport.from_browser(document, extra={"url": f"{base}{path}", "hostTag": host_tag})


def drive_report_page(
    page: Any,
    *,
    extra_surfaces: list[dict[str, Any]] | None = None,
    inject_host: bool = True,
    timeout_s: float = 45.0,
    clock: Any = time.monotonic,
) -> tuple[dict[str, Any], str]:
    """On an already-loaded dashboard ``page``, attach the host if needed, wait for the SPA to mount, report.

    Returns ``(report document, host_tag)`` with ``host_tag`` ``"present"`` (the
    shell carried a loader tag), ``"injected"`` (added here) or
    ``"reinjected"`` (added again after the host reloaded the page). ``page``
    is a Playwright page or anything with the same ``wait_for_function`` /
    ``wait_for_selector`` / ``add_script_tag`` / ``evaluate`` / ``wait_for_timeout``
    surface (the tests pass a fake).

    Order of events: ``window.floofy.report`` must exist (Loader + SPA host up),
    then the React root must have rendered (``MOUNT_SELECTOR``) — the DOM
    fingerprints of the shell only exist after that — then a short settle so
    the surfaces observer catches up, then the report. A mount that never comes
    within the budget is not an error: the report is taken anyway and names the
    misses, which is exactly what the matrix should record. The 0.7.0.5 entry
    may also ``location.reload()`` after syncing UI prefs; that wipes an injected
    ``window.floofy``, so the host is checked again after the mount and attached
    once more when it went missing.
    """
    deadline = clock() + timeout_s
    remaining = lambda floor=1.0: max(floor, deadline - clock())  # noqa: E731 - local helper

    def attach() -> None:
        if not inject_host:
            raise RuntimeError("window.floofy is not present and host injection is disabled (--no-inject)")
        page.add_script_tag(url=HOST_MODULE_URL, type="module")
        if not _wait_for_floofy(page, remaining()):
            status = page.evaluate("() => window.__floofyHostStatus || null")
            raise RuntimeError(f"window.floofy.report did not appear (host status: {status}); is the Loader app installed, enabled and the token valid?")

    host_tag = "present"
    if not _wait_for_floofy(page, min(4.0, timeout_s / 4)):
        attach()
        host_tag = "injected"
    mounted = False
    for _attempt in range(2):
        mounted = _wait_for_mount(page, remaining())
        # let the host finish the first render and the surfaces settle
        page.wait_for_timeout(MOUNT_SETTLE_MS if mounted else 100)
        if _wait_for_floofy(page, 0.5):
            break
        # the SPA reloaded itself (ui-prefs sync) and took the injected host with it
        attach()
        host_tag = "reinjected"
    document = page.evaluate("(options) => window.floofy.report(options)", {"extraSurfaces": extra_surfaces or []})
    if isinstance(document, dict):
        document.setdefault("mounted", mounted)
    return document, host_tag


def _wait_for_floofy(page: Any, seconds: float) -> bool:
    try:
        page.wait_for_function("() => window.floofy && typeof window.floofy.report === 'function'", timeout=int(max(0.1, seconds) * 1000))
        return True
    except Exception:  # noqa: BLE001 - a timeout is the negative answer
        return False


def _wait_for_mount(page: Any, seconds: float) -> bool:
    """Whether the React root rendered something within ``seconds`` (``MOUNT_SELECTOR``)."""
    try:
        page.wait_for_selector(MOUNT_SELECTOR, state="attached", timeout=int(max(0.1, seconds) * 1000))
        return True
    except Exception:  # noqa: BLE001 - a timeout is the negative answer
        return False
