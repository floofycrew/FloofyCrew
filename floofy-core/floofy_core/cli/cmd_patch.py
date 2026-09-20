"""``floofy apply / restore / verify / validate`` — the Patcher, the reporter and the validator from the manager.

* ``apply [--if-changed] [--payload ID] [--no-verify] [--confirm-governance-target PATH]``
  — revert-then-patch every payload with the enabled mods' ``patch`` parts and
  the generated shell descriptor (Requirement 5.4, 6.4). ``--if-changed`` is the
  re-apply trigger's entry (Requirement 6.1/6.2): it compares the payload set
  and host version with ``host-state.json`` and runs the host-version-change
  handling (:mod:`floofy_core.hostchange`) only when something changed, so an
  hourly timer costs nothing on a quiet hour.
* ``restore [--all] [--payload ID] [--no-sweep]`` — every payload back to vanilla,
  manifest-driven then glob sweep (Requirement 5.8).
* ``verify [--spa]`` — served bytes vs the deployment manifest over the dashboard
  socket/loopback (Requirement 5.7); ``--spa`` adds the browser reporter through
  :mod:`floofy_core.cli_spa` (needs Playwright and a loopback port/token);
  ``verify --bundle`` runs the offline bundle fingerprint check on the current payload.
* ``validate <path>`` — :mod:`floofy_core.cli_validate` (Requirement 1.9).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..cli_spa import main as spa_main
from ..cli_validate import main as validate_main
from ..patcher import Patcher, VerifyStatus
from .actions import reapply
from .context import CliContext, CliError

__all__ = ["apply", "register", "restore", "validate_cmd", "verify"]


def register(sub: argparse._SubParsersAction) -> None:
    a = sub.add_parser("apply", help="revert-then-patch every payload with the enabled mods' patches (the re-apply trigger runs `apply --if-changed`)")
    a.add_argument("--if-changed", action="store_true", help="only when the payload set or host version changed since the last run (host-version-change handling)")
    a.add_argument("--payload", action="append", default=[], metavar="ID", help="restrict to this payload id (repeatable)")
    a.add_argument("--no-verify", action="store_true", help="skip the live verification against the running gateway")
    a.add_argument("--confirm-governance-target", action="append", default=[], metavar="PATH", help="confirm one governance-file target for this run (repeatable)")
    a.set_defaults(handler=apply)

    r = sub.add_parser("restore", help="return every payload to vanilla (manifest + sweep; works without a manifest)")
    r.add_argument("--all", action="store_true", help="every payload (the default when no --payload is given)")
    r.add_argument("--payload", action="append", default=[], metavar="ID")
    r.add_argument("--no-sweep", action="store_true", help="manifest-driven only")
    r.set_defaults(handler=restore)

    v = sub.add_parser("verify", help="compare served bytes with the deployment manifests; --spa runs the browser reporter")
    v.add_argument("--spa", action="store_true", help="also run the SPA reporter in headless Chromium (Playwright)")
    v.add_argument("--bundle", action="store_true", help="offline bundle fingerprint check of the current payload's static/dist (no browser)")
    v.add_argument("--ready-file", default=None, help="a file with the gateway's KIROCREW_READY line (fills --port/--token for --spa)")
    v.add_argument("--extra-surfaces", default=None, help="extra surface definitions JSON for --spa")
    v.add_argument("--timeout", type=float, default=45.0)
    v.set_defaults(handler=verify)

    val = sub.add_parser("validate", help="validate a mod directory or archive (schema, hashes, parts, targets, network)")
    val.add_argument("path")
    val.set_defaults(handler=validate_cmd)


def apply(ctx: CliContext, args: argparse.Namespace) -> int:
    ctx.require_consent("apply patches")
    # task 10.7: a Loader app update staged by `floofy self-update` is installed here — the re-apply trigger's entry
    # point runs before a gateway starts (the PATH wrapper) and hourly — whenever no gateway is running
    from .cmd_selfupdate import apply_staged  # noqa: PLC0415

    staged = apply_staged(ctx)
    if staged is not None:
        ctx.set_result(selfUpdateLoaderApp=staged)
    if args.if_changed:
        from ..hostchange import apply_if_changed  # noqa: PLC0415

        outcome = apply_if_changed(ctx, confirmed_governance_targets=list(args.confirm_governance_target), verify=not args.no_verify)
        ctx.set_result(**outcome)
        return 0 if outcome.get("ok", True) else 1
    report = reapply(ctx, verify=not args.no_verify, confirmed_governance_targets=list(args.confirm_governance_target), payload_ids=args.payload or None)
    if report is None:
        raise CliError("no payload found")
    ctx.set_result(**report.to_dict())
    for item in report.payloads:
        flag = "dormant" if item.dormant else "current"
        verdict = "OK" if item.ok else "ERRORS"
        ctx.say(f"{item.payload} ({flag}): {verdict}; written {len(item.written)}, restored {len(item.restored)}, removed {len(item.removed)}, applied {len(item.applied)}, skipped {len(item.skipped)}")
        for error in item.errors:
            ctx.say(f"  ERROR {error}")
        for skipped in item.skipped:
            ctx.say(f"  SKIP {skipped.get('code')}: {skipped.get('message')}")
        if item.verify is not None:
            ctx.say(f"  verify: {item.verify.status}{' — ' + item.verify.detail if item.verify.detail else ''}")
    if report.declined:
        ctx.say("declined: nothing was written")
    failed_verify = any(p.verify is not None and p.verify.status == VerifyStatus.FAILED for p in report.payloads)
    return 0 if report.ok and not failed_verify else 1


def restore(ctx: CliContext, args: argparse.Namespace) -> int:
    payloads = ctx.payloads()
    if not payloads:
        raise CliError("no payload found: " + ctx.discovery().format_miss().splitlines()[0])
    patcher = Patcher(ctx.data_home, payloads, host_home=ctx.host_home, reporter=None if ctx.json_mode else ctx.say)
    report = patcher.restore(args.payload or None, sweep=not args.no_sweep)
    ctx.set_result(**report.to_dict())
    for payload_id, counts in report.payloads.items():
        ctx.say(f"{payload_id}: restored {len(counts['restored'])}, removed {len(counts['removed'])}")
    ctx.audit.record("restore-all" if not args.payload else "restore", payload=";".join(args.payload) or "all", result="ok", files=[f for c in report.payloads.values() for f in c["restored"]])
    return 0


def verify(ctx: CliContext, args: argparse.Namespace) -> int:
    payloads = ctx.payloads()
    if not payloads:
        raise CliError("no payload found")
    outcome: dict[str, Any] = {}
    if args.bundle:
        from ..spa_report import check_bundle_fingerprints  # noqa: PLC0415

        current = ctx.current_payload()
        assert current is not None
        report = check_bundle_fingerprints(current.dist_dir, host_version=current.host_version.text)
        outcome["bundle"] = report.to_dict()
        ctx.say(f"bundle fingerprints on {current.id}: {len(report.matched)}/{report.total} matched" + (f"; missed {[m['name'] for m in report.missed]}" if report.missed else ""))
    patcher = Patcher(ctx.data_home, payloads, host_home=ctx.host_home, endpoints=None, reporter=None if ctx.json_mode else ctx.say)
    reports = [patcher.verify(p) for p in payloads]
    outcome["payloads"] = [r.to_dict() for r in reports]
    for item in reports:
        ctx.say(f"{item.payload}: {item.status}" + (f" — {item.detail}" if item.detail else ""))
    code = 0 if all(r.status != VerifyStatus.FAILED for r in reports) else 1
    if args.bundle and outcome["bundle"]["spaFingerprints"]["missed"]:
        code = 1
    if args.spa:
        spa_argv = ["verify", "--spa", "--timeout", str(args.timeout)]
        if ctx.json_mode:
            spa_argv.insert(0, "--json")
        if args.ready_file:
            spa_argv += ["--ready-file", str(args.ready_file)]
        else:
            session = ctx.session()
            if session is None:
                raise CliError("verify --spa needs a running gateway (loopback port); pass --port/--token or --ready-file")
            spa_argv += ["--port", str(session.port), "--token", session.mint()]
        if args.extra_surfaces:
            spa_argv += ["--extra-surfaces", str(args.extra_surfaces)]
        import contextlib  # noqa: PLC0415
        import io  # noqa: PLC0415

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            spa_code = spa_main(spa_argv)
        text = buffer.getvalue()
        if ctx.json_mode:
            try:
                outcome["spa"] = json.loads(text)
            except ValueError:
                outcome["spa"] = {"raw": text}
        else:
            for line in text.splitlines():
                ctx.say(line)
        code = code or spa_code
    ctx.set_result(**outcome)
    return code


def validate_cmd(ctx: CliContext, args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser()
    if not path.exists():
        raise CliError(f"{path} does not exist", exit_code=2)
    import contextlib  # noqa: PLC0415
    import io  # noqa: PLC0415

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = validate_main([str(path), "--json"] if ctx.json_mode else [str(path)])
    text = buffer.getvalue()
    if ctx.json_mode:
        try:
            ctx.set_result(**json.loads(text))
        except ValueError:
            ctx.set_result(raw=text)
    else:
        for line in text.splitlines():
            ctx.say(line)
    return code
