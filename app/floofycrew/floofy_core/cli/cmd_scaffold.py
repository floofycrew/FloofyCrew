"""``floofy new <kind>`` — scaffold a valid mod with README, licence stub, smoke test and CI workflow (Requirement 14.1)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from .. import __version__ as FRAMEWORK_VERSION
from ..scaffold import KINDS, scaffold
from ..validator import validate_mod
from .context import CliContext, CliError

__all__ = ["new", "register"]


def register(sub: argparse._SubParsersAction) -> None:
    n = sub.add_parser("new", help="scaffold a mod of the given kind (manifest, README, LICENSE, smoke test, CI workflow)")
    n.add_argument("kind", choices=KINDS)
    n.add_argument("--id", dest="mod_id", default=None, help="mod id (default my-<kind>)")
    n.add_argument("--name", default=None, help="display name")
    n.add_argument("--dir", default=".", help="parent directory (default: the current one)")
    n.add_argument("--author", default=None, help="author (default: the OS user)")
    n.set_defaults(handler=new)


def new(ctx: CliContext, args: argparse.Namespace) -> int:
    author = args.author or os.environ.get("USER") or os.environ.get("USERNAME") or "you"
    major = FRAMEWORK_VERSION.split(".")[0]
    framework_range = f"^{major}.0" if major != "0" else ">=0.0.0 <2.0.0"
    try:
        root = scaffold(args.kind, Path(args.dir).expanduser(), mod_id=args.mod_id, name=args.name, author=author, framework_range=framework_range)
    except (ValueError, FileExistsError) as exc:
        raise CliError(str(exc)) from exc
    report = validate_mod(root)
    ctx.set_result(root=str(root), kind=args.kind, id=root.name, validation=report.to_dict())
    ctx.say(f"scaffolded {args.kind} mod at {root} ({'valid' if report.ok else str(len(report.errors)) + ' validation error(s)'})")
    for finding in report.errors:
        ctx.say("  " + finding.format())
    ctx.say("next: edit the part files, `floofy validate .`, `floofy dev . --follow`; python tests/smoke_test.py --refresh rehashes files[]")
    return 0 if report.ok else 1
