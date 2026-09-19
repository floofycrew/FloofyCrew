"""``floofy apply|restore|status|verify|gc`` — the Patcher's command-line front end.

Task 3.5 ships this as ``python -m floofy_core.cli_patch``; the ``floofy`` CLI
(task 6.1/6.2) wires the same :func:`main` under its sub-commands and supplies
the enabled patch set from the installed mods. Here the set comes from
``--patch <descriptor.json>`` (repeatable; ``--mod`` names the owner).

Payloads come from every installed edition adapter (:mod:`floofy_core.editions`)
plus ``--root`` extras and the interpreter's own ``find_spec``; ``--payload``
restricts an operation to one payload id. ``--data-home`` is FloofyCrew's home
(default ``~/.kiro/crew/floofy``), ``--host-home`` the host's data home holding
the dashboard socket (default: the parent of the data home). Governance-file
targets are patched only when named with ``--confirm-governance <target>``
(Requirement 11.4); engineering-rule targets are always refused (Requirement 5.10).
Exit status: 0 on success, 1 when any payload reports an error or a failed verify,
2 on usage errors.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .editions import combined_edition_probe, edition_providers
from .gateway import GatewayEndpoint
from .patcher import Patcher, PlannedPatch, VerifyStatus
from .patches import PatchDescriptor, PatchDescriptorError
from .payloads import DiscoveryResult, discover_payloads

__all__ = ["DEFAULT_DATA_HOME", "ISOLATION_ENV", "build_parser", "main"]

DEFAULT_DATA_HOME = Path.home() / ".kiro" / "crew" / "floofy"
#: When set, discovery is limited to ``--root`` directories (see ``--no-adapters``).
ISOLATION_ENV = "FLOOFY_NO_ADAPTERS"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="floofy-patch", description=__doc__.splitlines()[0])
    parser.add_argument("--data-home", type=Path, default=None, help=f"FloofyCrew home (default {DEFAULT_DATA_HOME})")
    parser.add_argument("--host-home", type=Path, default=None, help="host data home with the dashboard socket (default: parent of --data-home)")
    parser.add_argument("--root", action="append", default=[], type=Path, metavar="DIR", help="extra payload root to search (repeatable)")
    parser.add_argument("--no-adapters", action="store_true", help="search only the --root directories (no edition adapters, no find_spec)")
    parser.add_argument("--payload", action="append", default=[], metavar="ID", help="restrict to this payload id (repeatable)")
    parser.add_argument("--port", type=int, default=None, help="loopback port of a running gateway (instead of the socket)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--quiet", action="store_true", help="no progress lines")
    sub = parser.add_subparsers(dest="command", required=True)

    apply = sub.add_parser("apply", help="revert-then-patch every payload with the given descriptors")
    apply.add_argument("--patch", action="append", default=[], type=Path, metavar="DESCRIPTOR", help="patch descriptor JSON (repeatable)")
    apply.add_argument("--mod", default="cli", help="mod id recorded as the owner of the patches")
    apply.add_argument("--confirm-governance", action="append", default=[], metavar="TARGET", help="confirm one governance-file target (repeatable)")
    apply.add_argument("--no-verify", action="store_true", help="skip the gateway verification")

    restore = sub.add_parser("restore", help="return payloads to vanilla (manifest + sweep)")
    restore.add_argument("--all", action="store_true", help="every payload (the default when no --payload is given)")
    restore.add_argument("--no-sweep", action="store_true", help="manifest-driven only")

    sub.add_parser("status", help="per-payload patch state, drift and dormancy")
    sub.add_parser("verify", help="compare served bytes with the deployment manifest")
    sub.add_parser("gc", help="drop manifests of payloads that vanished")
    return parser


def _discover(args: argparse.Namespace) -> DiscoveryResult:
    if args.no_adapters or os.environ.get(ISOLATION_ENV):
        # Only the roots named on the command line: no edition adapter, no find_spec. The
        # environment guard is set by the test session so no test can ever reach a live install.
        return discover_payloads([], extra_roots=args.root, include_find_spec=False)
    return discover_payloads(edition_providers(), extra_roots=args.root, edition_probe=combined_edition_probe())


def _load_patches(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[PlannedPatch]:
    patches: list[PlannedPatch] = []
    for path in args.patch:
        try:
            descriptor = PatchDescriptor.load(path)
        except PatchDescriptorError as exc:
            parser.error(str(exc))
        patches.append(PlannedPatch(args.mod, Path(path).stem, descriptor))
    return patches


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    data_home = args.data_home or DEFAULT_DATA_HOME
    say = (lambda _m: None) if (args.quiet or args.json) else (lambda m: print(m))

    discovery = _discover(args)
    if not discovery.payloads:
        print(discovery.format_miss(), file=sys.stderr)
        return 1
    payloads = discovery.payloads
    if args.payload:
        payloads = [p for p in payloads if p.id in set(args.payload)]
        if not payloads:
            print(f"no payload matches {args.payload}; known: {[p.id for p in discovery.payloads]}", file=sys.stderr)
            return 2
    endpoints = [GatewayEndpoint(port=args.port)] if args.port else None
    patcher = Patcher(
        data_home,
        payloads,
        confirmed_governance_targets=getattr(args, "confirm_governance", []),
        endpoints=endpoints,
        host_home=args.host_home,
        reporter=say,
    )

    if args.command == "apply":
        report = patcher.apply(_load_patches(args, parser), verify=not args.no_verify)
        if args.json:
            print(json.dumps(report.to_dict(), indent=2))
        else:
            for item in report.payloads:
                flag = "dormant" if item.dormant else "current"
                verdict = "OK" if item.ok else "ERRORS"
                print(f"{item.payload} ({flag}): {verdict}; written {len(item.written)}, restored {len(item.restored)}, removed {len(item.removed)}, applied {len(item.applied)}, skipped {len(item.skipped)}")
                for error in item.errors:
                    print(f"  ERROR {error}")
                if item.verify is not None:
                    print(f"  verify: {item.verify.status}{' — ' + item.verify.detail if item.verify.detail else ''}")
        failed_verify = any(p.verify is not None and p.verify.status == VerifyStatus.FAILED for p in report.payloads)
        return 0 if report.ok and not failed_verify else 1

    if args.command == "restore":
        report = patcher.restore(args.payload or None, sweep=not args.no_sweep)
        if args.json:
            print(json.dumps(report.to_dict(), indent=2))
        else:
            for payload_id, counts in report.payloads.items():
                print(f"{payload_id}: restored {len(counts['restored'])}, removed {len(counts['removed'])}")
        return 0

    if args.command == "status":
        report = patcher.status()
        if args.json:
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(f"gateway: {report.gateway or 'not running'}" + (f" (serving {report.served_version})" if report.served_version else ""))
            for item in report.payloads:
                state = "dormant" if item.dormant else "serving" if report.served_version else "current"
                print(f"{item.payload} [{item.edition}, {state}] {item.root}")
                print(f"  patched {item.patched}, added {item.added}, sidelined {item.sidelined}, backups {item.backups_present}, mods {item.mods or '-'}")
                drift = {k: v for k, v in item.drift.items() if v}
                if drift:
                    print(f"  drift: {drift}")
        return 0

    if args.command == "verify":
        reports = [patcher.verify(p) for p in payloads]
        if args.json:
            print(json.dumps([r.to_dict() for r in reports], indent=2))
        else:
            for item in reports:
                print(f"{item.payload}: {item.status}" + (f" — {item.detail}" if item.detail else ""))
        return 0 if all(r.status != VerifyStatus.FAILED for r in reports) else 1

    if args.command == "gc":
        report = patcher.gc()
        if args.json:
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(f"removed {len(report.removed)} manifest(s), kept {len(report.kept)}")
        return 0

    parser.error(f"unknown command {args.command}")  # pragma: no cover
    return 2


if __name__ == "__main__":
    sys.exit(main())
