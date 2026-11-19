"""Command-line front end of the validator (``floofy validate``, Requirement 1.9).

Wired into the ``floofy`` CLI by the manager (task 6.1); usable on its own with
``python -m floofy_core.cli_validate <mod-dir-or-archive>``. Exit status 0 when
there are no errors (warnings alone do not fail — governance and network findings
are the user's to accept), 1 when there are errors, 2 on usage problems.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .targets import TargetPolicy
from .validator import validate_mod

__all__ = ["build_parser", "main"]


def build_parser(prog: str = "floofy validate") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="Validate a FloofyCrew mod directory or archive.")
    parser.add_argument("path", type=Path, help="mod root directory, or a .zip / .tar.gz / .tgz archive of one")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--quiet", "-q", action="store_true", help="print only the verdict line")
    parser.add_argument(
        "--engineering-rule-target",
        action="append",
        default=[],
        metavar="GLOB",
        help="extra target pattern refused as host code (normally supplied by the edition adapter)",
    )
    parser.add_argument(
        "--governance-target",
        action="append",
        default=[],
        metavar="GLOB",
        help="extra target pattern flagged governance-altering (normally supplied by the edition adapter)",
    )
    return parser


def main(argv: Sequence[str] | None = None, *, targets: TargetPolicy | None = None, out=None) -> int:
    """Run the validator; ``targets`` lets an edition adapter pass its extended policy."""
    out = out or sys.stdout
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.path.exists():
        parser.exit(2, f"{parser.prog}: error: {args.path} does not exist\n")
    policy = (targets or TargetPolicy()).extended(
        engineering_rule=args.engineering_rule_target, governance=args.governance_target
    )
    report = validate_mod(args.path, targets=policy)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), file=out)
    elif args.quiet:
        print(report.format().splitlines()[-1], file=out)
    else:
        print(report.format(), file=out)
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
