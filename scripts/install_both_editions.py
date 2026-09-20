#!/usr/bin/env python3
"""Install the same mod archive on both editions from scratch (task 9.3; Requirement 10.3).

Requirement 10.3: *the same mod archive SHALL install on both editions when its
manifest allows both*. This script proves it end to end without a live host:

1. builds ``mods/rimuru-branding`` (the first-party mod: ``theme`` + ``spa`` boot +
   ``patch`` parts, ``kirocrew.editions`` internal **and** external) into one
   deterministic archive — the bytes both editions receive;
2. for each edition lays out a scratch **payload** (a venv-shaped host with a
   ``static/dist`` copied from a fixture or from a payload copy — never a live
   install) and a scratch **host home**; the edition is forced the way the host
   itself declares it — the internal build carries a ``BUILD_VERSION`` stamp
   (``X.Y.Z.N``) and the public build does not — with ``FLOOFY_NO_ADAPTERS=1`` so
   no edition adapter can list the machine's real installs;
3. runs the manager in-process: ``floofy init --i-accept-the-risk`` (the consent
   record; no Loader app, no trigger), ``floofy validate <archive>``,
   ``floofy --yes install <archive> --now``, ``floofy enable rimuru-branding``;
4. runs the Loader's ``boot()`` in-process against the same payload and asserts the
   mod is **active** there and the Patcher ran (the ``spa`` boot part is baked
   into the payload's ``index.html``; the ``patch`` part applies when the fixture
   carries the real chunk and is *skipped with a diagnostic* when it holds the
   trimmed stub — a skip is Requirement 5.5's outcome, not a failure);
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

DEFAULT_MOD = REPO_ROOT / "mods" / "rimuru-branding"
FIXTURE_DIST = REPO_ROOT / "floofy-core" / "tests" / "fixtures" / "dist-0.7.0.5"
PAYLOAD_COPY_DIST = REPO_ROOT / ".scratch" / "payload-0.7.0.5" / "lib" / "python3.12" / "site-packages" / "kiro_crew" / "static" / "dist"
#: How each edition's host declares itself: the version the ``kiro_crew`` module reports and the stamp file.
EDITIONS: dict[str, dict[str, str | None]] = {
    "internal": {"module_version": "0.7.0.5", "stamp": "0.7.0.5"},
    "external": {"module_version": "0.7.0", "stamp": None},
}

__all__ = ["build_mod_archive", "install_on_edition", "lay_out_payload", "run_both"]


def build_mod_archive(mod_dir: Path, out: Path) -> tuple[Path, str, int]:
    """The mod as one deterministic archive: ``(path, sha256, size)``."""
    mod_id = json.loads((mod_dir / "floofy.json").read_text(encoding="utf-8"))["id"]
    version = json.loads((mod_dir / "floofy.json").read_text(encoding="utf-8"))["version"]
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


def _boot_in_process(host_home: Path, package_dir: Path, payload_root: Path, module_version: str) -> dict[str, Any]:
    """Run the Loader's boot sequence against the scratch payload (no gateway process) and return the state document."""
    from floofy_core.governance import AlwaysConfirm, GovernanceSnapshot  # noqa: PLC0415
    from floofy_core.patcher import Patcher  # noqa: PLC0415
    from floofy_core.payloads import discover_payloads  # noqa: PLC0415
    from floofy_loader.activation import ModContext  # noqa: PLC0415
    from floofy_loader.boot import BootDeps, boot, deactivate_all  # noqa: PLC0415
    from floofy_loader.host import read_host_facts  # noqa: PLC0415
    from floofy_loader.paths import FloofyPaths  # noqa: PLC0415

    module = types.ModuleType("kiro_crew")
    module.__version__ = module_version
    module.__file__ = str(package_dir / "__init__.py")
    facts = read_host_facts(host=module, env={"KIROCREW_HOME": str(host_home)}, adapters=[])
    paths = FloofyPaths(facts.data_home).ensure()

    def make_context(mod_id: str, version: str, mod_dir: Path, manifest: dict[str, Any]) -> ModContext:
        return ModContext(mod_id, version, mod_dir, facts, logging.getLogger(f"floofy.mods.{mod_id}"), data_dir=paths.mod_dir(mod_id))

    def patch_runner(planned: list, f: Any, p: FloofyPaths) -> dict[str, Any]:
        discovery = discover_payloads([], extra_roots=[payload_root])
        patcher = Patcher(p.data_home, discovery.payloads, host_home=f.host_home, confirmer=AlwaysConfirm(), triggers_installed=True)
        report = patcher.apply(planned, verify=False)
        return {"ran": True, "ok": report.ok, "report": report.to_dict()}

    deps = BootDeps(paths=paths, facts=facts, make_context=make_context, governance_reader=lambda _f: GovernanceSnapshot(), patch_runner=patch_runner)
    result = boot(deps)
    state = result.state.to_dict()
    deactivate_all(result, deps)
    return state


def install_on_edition(edition: str, work: Path, archive: Path, dist_source: Path, mod_id: str) -> dict[str, Any]:
    """Init → validate → install --now → enable → boot() on one edition; returns the per-edition record."""
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
    record: dict[str, Any] = {"edition": edition, "payload": str(payload_root), "hostHome": str(host_home), "distSource": str(dist_source), "steps": {}}
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
        validated = _cli(host_home, payload_root, "--json", "validate", str(archive))
        record["steps"]["validate"] = {"exit": validated.exit, "errors": validated.json.get("errors"), "warnings": len(validated.json.get("warnings") or [])}
        ok &= validated.exit == 0
        installed = _cli(host_home, payload_root, "--json", "--yes", "install", str(archive), "--now")
        record["steps"]["install"] = {"exit": installed.exit, "stderr": installed.stderr[-400:], "seams": [(p.get("kind"), p.get("seam")) for p in (installed.json.get("disclosure") or {}).get("parts", [])], "landedEnabled": installed.json.get("enabled")}
        ok &= installed.exit == 0
        enabled = _cli(host_home, payload_root, "--json", "enable", mod_id)
        record["steps"]["enable"] = {"exit": enabled.exit, "stderr": enabled.stderr[-400:]}
        ok &= enabled.exit == 0
        state = _boot_in_process(host_home, package_dir, payload_root, str(spec["module_version"]))
        mod_state = (state.get("mods") or {}).get(mod_id) or {}
        patches = state.get("patches") or {}
        payload_reports = ((patches.get("report") or {}).get("payloads") or []) if isinstance(patches, dict) else []
        applied = [a for r in payload_reports for a in r.get("applied", [])]
        skipped = [s for r in payload_reports for s in r.get("skipped", [])]
        record["boot"] = {
            "loader": state.get("loader"),
            "hostEdition": (state.get("host") or {}).get("edition"),
            "hostVersion": (state.get("host") or {}).get("version"),
            "active": bool(mod_state.get("active")),
            "reason": mod_state.get("reason"),
            "parts": [(p.get("kind"), p.get("status")) for p in mod_state.get("parts", [])],
            "patcherRan": bool(patches.get("ran")) if isinstance(patches, dict) else False,
            "patcherOk": bool(patches.get("ok")) if isinstance(patches, dict) else False,
            "applied": applied,
            "skipped": [{"target": s.get("target"), "code": s.get("code"), "message": s.get("message")} for s in skipped],
            "errors": state.get("errors"),
        }
        index_html = (package_dir / "static" / "dist" / "index.html").read_text(encoding="utf-8", errors="replace")
        record["boot"]["bootScriptInjected"] = "floofy" in index_html and index_html != (dist_source / "index.html").read_text(encoding="utf-8", errors="replace")
        ok &= record["boot"]["loader"] == "ok" and record["boot"]["active"] and record["boot"]["hostEdition"] == edition and record["boot"]["patcherRan"] and record["boot"]["patcherOk"] and record["boot"]["bootScriptInjected"]
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


def run_both(work: Path, *, mod_dir: Path = DEFAULT_MOD, internal_dist: Path = FIXTURE_DIST, external_dist: Path | None = None) -> dict[str, Any]:
    """Build the archive once and install it on both editions; returns the whole report."""
    if external_dist is None:
        external_dist = PAYLOAD_COPY_DIST if PAYLOAD_COPY_DIST.is_dir() else FIXTURE_DIST
    work.mkdir(parents=True, exist_ok=True)
    archive, digest, size = build_mod_archive(mod_dir, work)
    mod_id = json.loads((mod_dir / "floofy.json").read_text(encoding="utf-8"))["id"]
    editions = {
        "internal": install_on_edition("internal", work, archive, internal_dist, mod_id),
        "external": install_on_edition("external", work, archive, external_dist, mod_id),
    }
    return {
        "mod": mod_id,
        "archive": {"path": str(archive), "sha256": digest, "size": size, "sameBytesOnBothEditions": True},
        "editions": editions,
        "ok": all(e["ok"] for e in editions.values()),
    }


def render(report: dict[str, Any]) -> str:
    lines = [f"Same mod archive on both editions (Requirement 10.3): {report['mod']}", "", f"  archive: {Path(report['archive']['path']).name} sha256 {report['archive']['sha256']} ({report['archive']['size']} bytes)"]
    for edition, record in report["editions"].items():
        boot = record.get("boot") or {}
        steps = ", ".join(f"{name} exit {info.get('exit')}" for name, info in record["steps"].items())
        lines += [
            "",
            f"  [{edition}] payload dist from {record['distSource']}",
            f"    detected edition: {record.get('detectedEdition')}; steps: {steps}",
            f"    boot(): loader={boot.get('loader')} host={boot.get('hostEdition')}/{boot.get('hostVersion')} active={boot.get('active')} reason={boot.get('reason')} parts={boot.get('parts')}",
            f"    patcher: ran={boot.get('patcherRan')} ok={boot.get('patcherOk')} applied={len(boot.get('applied') or [])} skipped={len(boot.get('skipped') or [])} boot-script-injected={boot.get('bootScriptInjected')}",
        ]
        for skip in boot.get("skipped") or []:
            lines.append(f"      skipped ({skip.get('code')}): {skip.get('message')}")
        for error in boot.get("errors") or []:
            lines.append(f"      error: {error}")
        lines.append(f"    {'OK' if record['ok'] else 'FAIL'}")
    lines += ["", "OK: the same archive installed and activated on both editions" if report["ok"] else "FAIL: see the steps above"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mod", default=str(DEFAULT_MOD), help=f"the mod directory to archive (default {DEFAULT_MOD.relative_to(REPO_ROOT)})")
    parser.add_argument("--internal-dist", default=str(FIXTURE_DIST), help="static/dist copied into the internal scratch payload")
    parser.add_argument("--external-dist", default=None, help="static/dist copied into the external scratch payload (default: the payload copy under .scratch when present, else the fixture)")
    parser.add_argument("--work", default=None, metavar="DIR", help="keep the scratch payloads and homes under DIR (default: a temporary directory)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    external = Path(args.external_dist) if args.external_dist else None
    if args.work:
        report = run_both(Path(args.work), mod_dir=Path(args.mod), internal_dist=Path(args.internal_dist), external_dist=external)
    else:
        with tempfile.TemporaryDirectory(prefix="floofy-two-track-") as scratch:
            report = run_both(Path(scratch), mod_dir=Path(args.mod), internal_dist=Path(args.internal_dist), external_dist=external)
    print(json.dumps(report, indent=2, default=str) if args.json else render(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
