#!/usr/bin/env python3
"""Install the same mod archives on both editions from scratch (task 9.3, 11.6; Requirement 10.3).

Requirement 10.3: *the same mod archive SHALL install on both editions when its
manifest allows both*. This script proves it end to end without a live host:

1. builds every first-party mod — ``mods/custom-themes`` (a ``ui`` editor, a
   ``python-hook`` backend, ``spa`` runtime + boot parts and the theme-reset
   ``patch``), ``mods/rimuru-branding`` (a pure ``theme`` part depending on it)
   and ``mods/settings-demo`` (a ``ui`` settings page + a ``python-hook``
   backend route; task 11.6), all with ``kirocrew.editions`` internal **and**
   external — into one deterministic archive each: the bytes
   both editions receive;
2. for each edition lays out a scratch **payload** (a venv-shaped host with a
   ``static/dist`` copied from a fixture or from a payload copy — never a live
   install) and a scratch **host home**; the edition is forced the way the host
   itself declares it — the internal build carries a ``BUILD_VERSION`` stamp
   (``X.Y.Z.N``) and the public build does not — with ``FLOOFY_NO_ADAPTERS=1`` so
   no edition adapter can list the machine's real installs;
3. runs the manager in-process: ``floofy init --i-accept-the-risk`` (the consent
   record; no Loader app, no trigger), then per mod ``floofy validate <archive>``,
   ``floofy --yes install <archive> --now``, ``floofy enable <id>``;
4. runs the Loader's ``boot()`` in-process against the same payload and asserts
   every mod is **active** there: the Patcher ran for ``custom-themes`` (the
   ``spa`` boot part is baked into the payload's ``index.html``; the ``patch``
   part applies when the fixture carries the real chunk and is *skipped with a
   diagnostic* when it holds the trimmed stub — a skip is Requirement 5.5's
   outcome, not a failure), and ``settings-demo``'s hook registered its ``echo``
   route, which is dispatched once through the mod-route registry;
5. prints one report for both editions (``--json`` for machines) and exits 1 on
   any failed step.

Payload sources: the internal edition uses ``--internal-dist`` (default
``floofy-core/tests/fixtures/dist-0.7.0.5``); the external edition uses
``--external-dist`` (default: the ``.scratch/payload-0.7.0.5`` copy's dist when
present, else the same fixture). Both are *copied* into the scratch payloads.
Standard library plus the repository's own packages (``floofy-core``,
``loader-app``, ``registry-tools`` on ``sys.path``). CI runs it
(``.github/workflows/ci.yml`` job ``two-track``); ``floofy-core/tests/test_two_track.py``
drives the same function.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
for entry in ("floofy-core", "loader-app", "registry-tools"):
    sys.path.insert(0, str(REPO_ROOT / entry))

from registry_tools.bootstrap import deterministic_zip  # noqa: E402

#: The first-party mods, in install order (``custom-themes`` first: the one with payload-touching parts, and ``rimuru-branding`` depends on it).
DEFAULT_MODS: tuple[Path, ...] = (REPO_ROOT / "mods" / "custom-themes", REPO_ROOT / "mods" / "rimuru-branding", REPO_ROOT / "mods" / "settings-demo")
DEFAULT_MOD = DEFAULT_MODS[0]
FIXTURE_DIST = REPO_ROOT / "floofy-core" / "tests" / "fixtures" / "dist-0.7.0.5"
PAYLOAD_COPY_DIST = REPO_ROOT / ".scratch" / "payload-0.7.0.5" / "lib" / "python3.12" / "site-packages" / "kiro_crew" / "static" / "dist"
#: How each edition's host declares itself: the version the ``kiro_crew`` module reports and the stamp file.
EDITIONS: dict[str, dict[str, str | None]] = {
    "internal": {"module_version": "0.7.0.5", "stamp": "0.7.0.5"},
    "external": {"module_version": "0.7.0", "stamp": None},
}

__all__ = ["DEFAULT_MODS", "build_mod_archive", "install_on_edition", "lay_out_payload", "run_both"]


def _manifest(mod_dir: Path) -> dict[str, Any]:
    return json.loads((Path(mod_dir) / "floofy.json").read_text(encoding="utf-8"))


def build_mod_archive(mod_dir: Path, out: Path) -> tuple[Path, str, int]:
    """The mod as one deterministic archive: ``(path, sha256, size)``."""
    manifest = _manifest(mod_dir)
    mod_id, version = manifest["id"], manifest["version"]
    archive = out / f"{mod_id}-{version}.zip"
    digest, size = deterministic_zip(mod_dir, archive, top=mod_id)
    return archive, digest, size


def lay_out_payload(root: Path, dist_source: Path, *, module_version: str, stamp: str | None) -> Path:
    """A venv-shaped scratch payload with ``dist_source`` copied in; returns the ``kiro_crew`` package directory."""
    package_dir = root / "lib" / "python3.12" / "site-packages" / "kiro_crew"
    package_dir.mkdir(parents=True)
    (root / "pyvenv.cfg").write_text("home = /usr/bin\nversion_info = 3.12.14\n", encoding="utf-8")
    (root / "bin").mkdir()
    (root / "bin" / "python").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    base = module_version.split("rc")[0]
    base = ".".join(base.split(".")[:3])
    (package_dir / "__init__.py").write_text(f'"""scratch host"""\n__version__ = "{base}"\n', encoding="utf-8")
    if stamp:
        (package_dir / "BUILD_VERSION").write_text(stamp + "\n", encoding="utf-8")
    shutil.copytree(dist_source, package_dir / "static" / "dist")
    return package_dir


def _cli(host_home: Path, payload_root: Path, *args: str) -> Any:
    from floofy_core.cli.main import run  # noqa: PLC0415

    return run(["--home", str(host_home), "--root", str(payload_root), *args], non_interactive=True, actor="install_both_editions")


def _boot_in_process(host_home: Path, package_dir: Path, payload_root: Path, module_version: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the Loader's boot sequence against the scratch payload (no gateway process); returns ``(state document, per-mod route probes)``.

    The context handed to a ``python-hook`` carries what the runtime would give it
    — ``ctx.config`` (the mod's ``.floofy/config.json``) and ``ctx.routes`` (the
    mod-route registry, task 11.5) — so a hook that registers backend routes
    activates the way it does inside the gateway; every registered ``GET`` route
    is dispatched once through the registry and its status recorded.
    """
    from floofy_core.governance import AlwaysConfirm, GovernanceSnapshot  # noqa: PLC0415
    from floofy_core.patcher import Patcher  # noqa: PLC0415
    from floofy_core.payloads import discover_payloads  # noqa: PLC0415
    from floofy_loader.activation import ModContext  # noqa: PLC0415
    from floofy_loader.boot import BootDeps, boot, deactivate_all  # noqa: PLC0415
    from floofy_loader.host import read_host_facts  # noqa: PLC0415
    from floofy_loader.modroutes import ModRouteRegistry  # noqa: PLC0415
    from floofy_loader.paths import FloofyPaths  # noqa: PLC0415
    from floofy_loader.storage import ModConfig  # noqa: PLC0415

    module = types.ModuleType("kiro_crew")
    module.__version__ = module_version
    module.__file__ = str(package_dir / "__init__.py")
    facts = read_host_facts(host=module, env={"KIROCREW_HOME": str(host_home)}, adapters=[])
    paths = FloofyPaths(facts.data_home).ensure()
    registry = ModRouteRegistry()

    def make_context(mod_id: str, version: str, mod_dir: Path, manifest: dict[str, Any]) -> ModContext:
        return ModContext(mod_id, version, mod_dir, facts, logging.getLogger(f"floofy.mods.{mod_id}"), config=ModConfig(paths.data_home, mod_id, payload_root=facts.payload_root), routes=registry.for_mod(mod_id), data_dir=paths.mod_dir(mod_id))

    def patch_runner(planned: list, f: Any, p: FloofyPaths) -> dict[str, Any]:
        discovery = discover_payloads([], extra_roots=[payload_root])
        patcher = Patcher(p.data_home, discovery.payloads, host_home=f.host_home, confirmer=AlwaysConfirm(), triggers_installed=True)
        report = patcher.apply(planned, verify=False)
        return {"ran": True, "ok": report.ok, "report": report.to_dict()}

    deps = BootDeps(paths=paths, facts=facts, make_context=make_context, governance_reader=lambda _f: GovernanceSnapshot(), patch_runner=patch_runner)
    result = boot(deps)
    state = result.state.to_dict()
    probes: dict[str, Any] = {}
    for mod_id in state.get("mods") or {}:
        routes = registry.listing(mod_id)
        if not routes:
            continue
        calls = []
        for route in routes:
            # only the parameter-free GET routes can be probed blind; a JSON reply must be non-empty, any other
            # content type (custom-themes answers text/css) only has to be a 200 with a body
            if route["method"] != "GET" or "{" in route["path"]:
                continue
            status, headers, body = registry.dispatch_sync(mod_id, "GET", route["path"])
            if headers.get("Content-Type", "").startswith("application/json"):
                ok = status == 200 and bool(json.loads(body or b"null"))
            else:
                ok = status == 200 and bool(body)
            calls.append({"route": f"GET {route['path']}", "status": status, "ok": ok})
        probes[mod_id] = {"routes": routes, "calls": calls}
    deactivate_all(result, deps)
    return state, probes


def _mod_boot_record(mod_id: str, state: dict[str, Any], probes: dict[str, Any], package_dir: Path, dist_source: Path) -> tuple[dict[str, Any], bool]:
    """What ``boot()`` said about one mod, and whether that is what the mod's parts require."""
    mod_state = (state.get("mods") or {}).get(mod_id) or {}
    patches = state.get("patches") or {}
    payload_reports = ((patches.get("report") or {}).get("payloads") or []) if isinstance(patches, dict) else []
    applied = [a for r in payload_reports for a in r.get("applied", [])]
    skipped = [s for r in payload_reports for s in r.get("skipped", [])]
    kinds = {p.get("kind") for p in mod_state.get("parts", [])}
    record: dict[str, Any] = {
        "active": bool(mod_state.get("active")),
        "reason": mod_state.get("reason"),
        "parts": [(p.get("kind"), p.get("status")) for p in mod_state.get("parts", [])],
        "routes": (probes.get(mod_id) or {}).get("routes", []),
        "routeCalls": (probes.get(mod_id) or {}).get("calls", []),
    }
    ok = record["active"] and all(status == "active" for kind, status in record["parts"] if kind in ("spa", "python-hook", "ui"))
    if kinds & {"spa", "patch"}:
        index_html = (package_dir / "static" / "dist" / "index.html").read_text(encoding="utf-8", errors="replace")
        record["patcherRan"] = bool(patches.get("ran")) if isinstance(patches, dict) else False
        record["patcherOk"] = bool(patches.get("ok")) if isinstance(patches, dict) else False
        record["applied"] = applied
        record["skipped"] = [{"target": s.get("target"), "code": s.get("code"), "message": s.get("message")} for s in skipped]
        record["bootScriptInjected"] = "floofy" in index_html and index_html != (dist_source / "index.html").read_text(encoding="utf-8", errors="replace")
        ok = ok and record["patcherRan"] and record["patcherOk"] and record["bootScriptInjected"]
    if "python-hook" in kinds:
        ok = ok and bool(record["routes"]) and all(call["ok"] for call in record["routeCalls"])
    return record, bool(ok)


def install_on_edition(edition: str, work: Path, archives: dict[str, Path], dist_source: Path) -> dict[str, Any]:
    """Init → (validate → install --now → enable) per mod → one boot() on one edition; returns the per-edition record."""
    spec = EDITIONS[edition]
    root = work / edition
    payload_root = root / "payload"
    host_home = root / "home"
    kiro_home = root / "kiro"
    for directory in (payload_root, host_home, kiro_home):
        directory.mkdir(parents=True)
    package_dir = lay_out_payload(payload_root, dist_source, module_version=str(spec["module_version"]), stamp=spec["stamp"])
    previous = {k: os.environ.get(k) for k in ("FLOOFY_NO_ADAPTERS", "KIRO_HOME", "KIROCREW_HOME")}
    os.environ["FLOOFY_NO_ADAPTERS"] = "1"
    os.environ["KIRO_HOME"] = str(kiro_home)
    os.environ.pop("KIROCREW_HOME", None)
    record: dict[str, Any] = {"edition": edition, "payload": str(payload_root), "hostHome": str(host_home), "distSource": str(dist_source), "steps": {}, "mods": {}}
    ok = True
    try:
        init = _cli(host_home, payload_root, "--json", "init", "--i-accept-the-risk", "--no-loader-app", "--no-trigger", "--no-grant")
        record["steps"]["init"] = {"exit": init.exit, "stderr": init.stderr[-400:]}
        ok &= init.exit == 0
        doctor = _cli(host_home, payload_root, "--json", "doctor")
        detected = (doctor.json.get("host") or {}).get("edition") or doctor.json.get("edition")
        record["steps"]["doctor"] = {"exit": doctor.exit, "edition": detected}
        record["detectedEdition"] = detected
        ok &= doctor.exit == 0 and detected == edition
        for mod_id, archive in archives.items():
            steps: dict[str, Any] = {}
            validated = _cli(host_home, payload_root, "--json", "validate", str(archive))
            steps["validate"] = {"exit": validated.exit, "errors": validated.json.get("errors"), "warnings": len(validated.json.get("warnings") or [])}
            ok &= validated.exit == 0
            installed = _cli(host_home, payload_root, "--json", "--yes", "install", str(archive), "--now")
            steps["install"] = {"exit": installed.exit, "stderr": installed.stderr[-400:], "seams": [(p.get("kind"), p.get("seam")) for p in (installed.json.get("disclosure") or {}).get("parts", [])], "landedEnabled": installed.json.get("enabled")}
            ok &= installed.exit == 0
            enabled = _cli(host_home, payload_root, "--json", "enable", mod_id)
            steps["enable"] = {"exit": enabled.exit, "stderr": enabled.stderr[-400:]}
            ok &= enabled.exit == 0
            record["steps"].update({f"{mod_id}:{name}": info for name, info in steps.items()})
            record["mods"][mod_id] = {"steps": steps}
        state, probes = _boot_in_process(host_home, package_dir, payload_root, str(spec["module_version"]))
        record["boot"] = {"loader": state.get("loader"), "hostEdition": (state.get("host") or {}).get("edition"), "hostVersion": (state.get("host") or {}).get("version"), "errors": state.get("errors")}
        ok &= record["boot"]["loader"] == "ok" and record["boot"]["hostEdition"] == edition
        for mod_id in archives:
            mod_record, mod_ok = _mod_boot_record(mod_id, state, probes, package_dir, dist_source)
            record["mods"][mod_id]["boot"] = mod_record
            record["mods"][mod_id]["ok"] = mod_ok
            ok &= mod_ok
        status = _cli(host_home, payload_root, "--json", "status")
        record["steps"]["status"] = {"exit": status.exit}
        ok &= status.exit == 0
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    record["ok"] = bool(ok)
    return record


def run_both(work: Path, *, mod_dirs: tuple[Path, ...] | list[Path] = DEFAULT_MODS, internal_dist: Path = FIXTURE_DIST, external_dist: Path | None = None) -> dict[str, Any]:
    """Build each archive once and install them all on both editions; returns the whole report."""
    if external_dist is None:
        external_dist = PAYLOAD_COPY_DIST if PAYLOAD_COPY_DIST.is_dir() else FIXTURE_DIST
    work.mkdir(parents=True, exist_ok=True)
    archives: dict[str, Path] = {}
    mods: list[dict[str, Any]] = []
    for mod_dir in mod_dirs:
        archive, digest, size = build_mod_archive(Path(mod_dir), work)
        manifest = _manifest(mod_dir)
        archives[manifest["id"]] = archive
        mods.append({"id": manifest["id"], "version": manifest["version"], "kinds": [p.get("kind") for p in manifest.get("parts", [])], "archive": {"path": str(archive), "sha256": digest, "size": size, "sameBytesOnBothEditions": True}})
    editions = {
        "internal": install_on_edition("internal", work, archives, internal_dist),
        "external": install_on_edition("external", work, archives, external_dist),
    }
    return {
        "mods": mods,
        # the first mod's identity and archive, for readers of the single-mod report shape (task 9.3)
        "mod": mods[0]["id"] if mods else None,
        "archive": mods[0]["archive"] if mods else None,
        "editions": editions,
        "ok": all(e["ok"] for e in editions.values()),
    }


def render(report: dict[str, Any]) -> str:
    lines = [f"The same mod archives on both editions (Requirement 10.3): {', '.join(m['id'] for m in report['mods'])}", ""]
    for mod in report["mods"]:
        lines.append(f"  archive: {Path(mod['archive']['path']).name} sha256 {mod['archive']['sha256']} ({mod['archive']['size']} bytes) — parts {', '.join(mod['kinds'])}")
    for edition, record in report["editions"].items():
        boot = record.get("boot") or {}
        steps = ", ".join(f"{name} exit {info.get('exit')}" for name, info in record["steps"].items())
        lines += [
            "",
            f"  [{edition}] payload dist from {record['distSource']}",
            f"    detected edition: {record.get('detectedEdition')}; steps: {steps}",
            f"    boot(): loader={boot.get('loader')} host={boot.get('hostEdition')}/{boot.get('hostVersion')}",
        ]
        for mod_id, mod_record in (record.get("mods") or {}).items():
            mod_boot = mod_record.get("boot") or {}
            lines.append(f"    {mod_id}: active={mod_boot.get('active')} reason={mod_boot.get('reason')} parts={mod_boot.get('parts')}")
            if "patcherRan" in mod_boot:
                lines.append(f"      patcher: ran={mod_boot.get('patcherRan')} ok={mod_boot.get('patcherOk')} applied={len(mod_boot.get('applied') or [])} skipped={len(mod_boot.get('skipped') or [])} boot-script-injected={mod_boot.get('bootScriptInjected')}")
            for skip in mod_boot.get("skipped") or []:
                lines.append(f"      skipped ({skip.get('code')}): {skip.get('message')}")
            if mod_boot.get("routes"):
                lines.append(f"      routes: {', '.join(f'{r['method']} {r['path']}' for r in mod_boot['routes'])}; probes: {', '.join(f'{c['route']} -> {c['status']}' for c in mod_boot.get('routeCalls') or [])}")
            lines.append(f"      {'OK' if mod_record.get('ok') else 'FAIL'}")
        for error in boot.get("errors") or []:
            lines.append(f"      error: {error}")
        lines.append(f"    {'OK' if record['ok'] else 'FAIL'}")
    lines += ["", "OK: the same archives installed and activated on both editions" if report["ok"] else "FAIL: see the steps above"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mod", action="append", default=None, metavar="DIR", help=f"a mod directory to archive (repeatable; default {', '.join(str(m.relative_to(REPO_ROOT)) for m in DEFAULT_MODS)})")
    parser.add_argument("--internal-dist", default=str(FIXTURE_DIST), help="static/dist copied into the internal scratch payload")
    parser.add_argument("--external-dist", default=None, help="static/dist copied into the external scratch payload (default: the payload copy under .scratch when present, else the fixture)")
    parser.add_argument("--work", default=None, metavar="DIR", help="keep the scratch payloads and homes under DIR (default: a temporary directory)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    external = Path(args.external_dist) if args.external_dist else None
    mod_dirs = tuple(Path(m) for m in args.mod) if args.mod else DEFAULT_MODS
    if args.work:
        report = run_both(Path(args.work), mod_dirs=mod_dirs, internal_dist=Path(args.internal_dist), external_dist=external)
    else:
        with tempfile.TemporaryDirectory(prefix="floofy-two-track-") as scratch:
            report = run_both(Path(scratch), mod_dirs=mod_dirs, internal_dist=Path(args.internal_dist), external_dist=external)
    print(json.dumps(report, indent=2, default=str) if args.json else render(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
