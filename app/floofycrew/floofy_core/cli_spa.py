"""``floofy verify --spa`` — drive the SPA reporter (Requirement 4.5), and the offline bundle check.

Task 5.2 ships this as ``python -m floofy_core.cli_spa``; the ``floofy`` CLI
(task 6.1/6.2) wires :func:`main` under ``floofy verify --spa``. Two commands:

* ``verify --url http://127.0.0.1:PORT --token TOKEN [--ready-file F] [--path /]
  [--extra-surfaces FILE] [--no-inject] [--screenshot PNG] [--json]`` — opens the
  dashboard in headless Chromium (Playwright, imported only here; a missing
  Playwright prints how to install it), authenticates with the ``?token=`` link,
  calls ``window.floofy.report()`` and prints the result. Exit 0 when nothing is
  missed, 1 otherwise, 2 on usage errors. ``--ready-file`` reads ``port`` and
  ``token`` from a file holding the gateway's ``KIROCREW_READY:{...}`` line
  (its stdout) or the bare JSON. The dashboard's unix socket cannot be driven by
  a browser; use the loopback port.
* ``bundle-check --dist DIR [--host-version V] [--json]`` — the offline check of
  the ``bundle`` fingerprints against a ``static/dist`` on disk, no browser
  (what the Forge runs first on a provisioned payload).

Both print the matrix shape ``spaFingerprints {matched, total, missed[]}``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .spa_report import PlaywrightMissing, SpaReport, check_bundle_fingerprints, load_registry, run_browser_report

__all__ = ["build_parser", "main", "read_ready_file"]


def read_ready_file(path: Path) -> dict[str, Any]:
    """``{"port", "token", ...}`` from a gateway stdout capture or a bare JSON file."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    for line in reversed(text.splitlines()):
        if line.startswith("KIROCREW_READY:"):
            return json.loads(line[len("KIROCREW_READY:") :])
    stripped = text.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)
    raise ValueError(f"{path}: no KIROCREW_READY line and not a JSON object")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="floofy-spa", description=__doc__.splitlines()[0])
    parser.add_argument("--registry", type=Path, default=None, help="surfaces.json to use (default: the shipped registry)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify", help="live report through headless Chromium (floofy verify --spa)")
    verify.add_argument("--spa", action="store_true", help="accepted for symmetry with `floofy verify --spa`")
    verify.add_argument("--url", default=None, help="dashboard base URL, e.g. http://127.0.0.1:38000")
    verify.add_argument("--port", type=int, default=None, help="loopback port (shorthand for --url http://127.0.0.1:PORT)")
    verify.add_argument("--token", default=None, help="the dashboard link token (KIROCREW_READY prints it)")
    verify.add_argument("--ready-file", type=Path, default=None, help="file with the gateway's KIROCREW_READY line (fills --port/--token)")
    verify.add_argument("--path", default="/", help="route to open (default /)")
    verify.add_argument("--extra-surfaces", type=Path, default=None, help="JSON file with extra surface definitions for this run")
    verify.add_argument("--no-inject", action="store_true", help="fail instead of adding the host module tag when index.html carries none")
    verify.add_argument("--screenshot", type=Path, default=None, help="save a PNG of the page after the report")
    verify.add_argument("--timeout", type=float, default=45.0, help="seconds to wait for the dashboard and the report")
    verify.add_argument("--headed", action="store_true", help="show the browser (debugging)")

    offline = sub.add_parser("bundle-check", help="offline check of the bundle fingerprints against a static/dist directory")
    offline.add_argument("--dist", type=Path, required=True, help="a kiro_crew/static/dist directory")
    offline.add_argument("--host-version", default=None, help="recorded in the report")
    return parser


def _print(report: SpaReport, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report.to_dict(), indent=2))
        return
    data = report.to_dict()
    print(f"spa report ({report.source}) host={report.host_version or '?'}: {len(report.matched)}/{report.total} fingerprints matched")
    for name in report.matched:
        print(f"  ok    {name}")
    for miss in report.missed:
        chunk = f" [{miss['chunk']}]" if miss.get("chunk") else ""
        print(f"  MISS  {miss['name']}{chunk}: {miss.get('reason', '')}")
    for row in report.not_applicable:
        print(f"  n/a   {row['name']}: {row.get('reason', '')}")
    extra = {k: v for k, v in data.items() if k in ("route", "hostTag", "mounted", "url")}
    if extra:
        print(f"  {extra}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        registry = load_registry(args.registry)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))

    if args.command == "bundle-check":
        if not (args.dist / "index.html").is_file():
            parser.error(f"{args.dist} has no index.html")
        report = check_bundle_fingerprints(args.dist, registry, host_version=args.host_version)
        _print(report, args.json)
        return 0 if report.ok else 1

    port, token = args.port, args.token
    if args.ready_file is not None:
        try:
            ready = read_ready_file(args.ready_file)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        port = port or int(ready.get("port", 0)) or None
        token = token or ready.get("token")
    url = args.url or (f"http://127.0.0.1:{port}" if port else None)
    if not url:
        parser.error("verify needs --url or --port (or --ready-file)")
    extra_surfaces = None
    if args.extra_surfaces is not None:
        loaded = json.loads(args.extra_surfaces.read_text(encoding="utf-8"))
        extra_surfaces = loaded.get("surfaces", loaded) if isinstance(loaded, dict) else loaded
    try:
        report = run_browser_report(
            url,
            token,
            extra_surfaces=extra_surfaces,
            inject_host=not args.no_inject,
            timeout_s=args.timeout,
            path=args.path,
            headless=not args.headed,
            screenshot=args.screenshot,
        )
    except PlaywrightMissing as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"verify --spa failed: {exc}", file=sys.stderr)
        return 1
    _print(report, args.json)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
