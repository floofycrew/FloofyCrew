"""``floofy`` — entry point, argument parsing and the in-process ``run()`` the manager UI uses.

Command inventory (Requirement 7.1)::

    init  deinit  doctor  status  search  install  uninstall  enable  disable
    update [--all]  apply  restore  verify  validate  new  dev  yeet  hold
    profile {list,save,use,export,import}  registry {add,remove,list,refresh}
    audit  which  vanilla   (also: floofy --vanilla)

Global flags: ``--home`` (host home; default ``KIROCREW_HOME`` or ``~/.kiro/crew``),
``--json``, ``--yes`` (ordinary confirmations only — never the one-time consent
or a governance-altering per-file confirmation), ``--root`` (extra payload
root, repeatable), ``--no-adapters``, ``--kirocrew`` (the host launcher),
``--port``/``--token`` (a running gateway), ``--version``.

:func:`run` executes one command in-process and returns ``RunResult(exit,
stdout, json)``; the Loader's ``POST /api/apps/floofycrew/cli`` route calls it
with ``non_interactive=True`` and an allowlist, so the UI and the CLI share one
implementation (design "Manager (CLI + UI)").
"""
from __future__ import annotations

import argparse
import io
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import API_VERSION, __version__ as FRAMEWORK_VERSION
from .console import Console
from .context import CliContext, CliError, build_context, dumps

__all__ = ["RunResult", "build_parser", "main", "run"]

PROG = "floofy"
UNOFFICIAL = "FloofyCrew is unofficial and not affiliated with Kiro or KiroCrew."

#: Commands whose implementation lands in a later task of package 6; they print the task and exit 2.
_LANDS_LATER: dict[str, str] = {
    "profile": "6.6",
    **{name: "6.8" for name in ("registry", "audit", "which")},
}


@dataclass
class RunResult:
    exit: int
    stdout: str
    json: dict[str, Any] = field(default_factory=dict)
    stderr: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"exit": self.exit, "stdout": self.stdout, "stderr": self.stderr, "json": self.json}


class _Parser(argparse.ArgumentParser):
    """``error()`` raises instead of exiting so ``run()`` can report usage problems as exit 2."""

    def error(self, message: str) -> None:  # type: ignore[override]
        raise CliError(f"{self.prog}: {message}", exit_code=2)


def _lands_later(task: str) -> Callable[[CliContext, argparse.Namespace], int]:
    def handler(ctx: CliContext, args: argparse.Namespace) -> int:
        ctx.console.error(f"`floofy {args.command}` lands in task {task}")
        return 2

    return handler


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog=PROG, description=f"The FloofyCrew mod manager for KiroCrew. {UNOFFICIAL}", formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="store_true", help="print the FloofyCrew and API versions")
    parser.add_argument("--home", metavar="DIR", default=None, help="host data home (default: $KIROCREW_HOME or ~/.kiro/crew); FloofyCrew lives in <home>/floofy")
    parser.add_argument("--json", action="store_true", help="machine-readable output (implies non-interactive)")
    parser.add_argument("--yes", "-y", action="store_true", help="answer ordinary confirmations with yes (never the consent warning or a governance-altering file confirmation)")
    parser.add_argument("--root", action="append", default=[], metavar="DIR", help="extra payload root to search (repeatable)")
    parser.add_argument("--no-adapters", action="store_true", help="search only --root directories and this interpreter (no edition adapters)")
    parser.add_argument("--kirocrew", metavar="PATH", default=None, help="the host's kirocrew launcher (default: PATH, then the payload's bin/kirocrew)")
    parser.add_argument("--port", type=int, default=None, help="loopback port of a running gateway (default: the dashboard socket in the host home)")
    parser.add_argument("--token", default=None, help="a dashboard session token to use instead of minting one")
    parser.add_argument("--quiet", "-q", action="store_true", help="less prose")
    parser.add_argument("--vanilla", action="store_true", help="same as `floofy vanilla`: the next gateway boot runs with every mod disabled, once")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    from . import cmd_doctor, cmd_init  # noqa: PLC0415

    cmd_init.register(sub)
    cmd_doctor.register(sub)
    for module_name in ("cmd_mods", "cmd_patch", "cmd_scaffold", "cmd_yeet", "cmd_profile", "cmd_registry"):
        try:
            module = __import__(f"{__package__}.{module_name}", fromlist=["register"])
        except ImportError:
            continue
        module.register(sub)
    for name, task in _LANDS_LATER.items():
        if name not in sub.choices:
            later = sub.add_parser(name, help=f"lands in task {task}")
            later.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
            later.set_defaults(handler=_lands_later(task))
    return parser


def _version_lines() -> list[str]:
    return [f"floofy {FRAMEWORK_VERSION} (api {API_VERSION}) — {UNOFFICIAL}"]


def execute(argv: list[str], console: Console, *, actor: str = "cli", in_gateway: bool = False, on_mutation: Callable[[str], None] | None = None, allow: frozenset[str] | None = None) -> tuple[int, dict[str, Any]]:
    """Parse and run one command; returns ``(exit code, result document)``."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except CliError as exc:
        console.error(str(exc))
        return exc.exit_code, {"error": str(exc)}
    console.assume_yes = bool(args.yes)
    console.quiet = console.quiet or bool(args.json) or bool(args.quiet)
    console.non_interactive = console.non_interactive or bool(args.json) or (console.input_fn is None and not _stdin_is_tty())
    if args.version:
        console.say(_version_lines()[0])
        return 0, {"version": FRAMEWORK_VERSION, "apiVersion": API_VERSION, "unofficial": True}
    if args.vanilla and not args.command:
        args.command = "vanilla"
        args.handler = __import__(f"{__package__}.cmd_doctor", fromlist=["vanilla"]).vanilla
    if not args.command:
        parser.print_help(file=console.out if not console.quiet else io.StringIO())
        return 2, {"error": "no command"}
    if allow is not None and args.command not in allow:
        console.error(f"`floofy {args.command}` is not available from this surface (allowed: {', '.join(sorted(allow))})")
        return 2, {"error": f"command {args.command!r} not allowed here"}
    ctx = build_context(args, console=console, actor=actor, in_gateway=in_gateway, on_mutation=on_mutation)
    handler = getattr(args, "handler", None)
    if handler is None:
        console.error(f"`floofy {args.command}` has no handler")
        return 2, {"error": "no handler"}
    try:
        code = int(handler(ctx, args) or 0)
    except CliError as exc:
        console.error(str(exc))
        ctx.set_result(error=str(exc))
        code = exc.exit_code
    except KeyboardInterrupt:
        console.error("interrupted")
        code = 130
    ctx.result.setdefault("command", args.command)
    ctx.result.setdefault("exit", code)
    ctx.result.setdefault("unofficial", True)
    return code, ctx.result


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    """The console-script / ``python -m`` entry: prints, exits."""
    args = list(sys.argv[1:] if argv is None else argv)
    console = Console()
    code, result = execute(args, console)
    if "--json" in args:
        print(dumps(result))
    return code


def run(argv: list[str], *, home: Path | str | None = None, non_interactive: bool = True, actor: str = "ui", in_gateway: bool = False, allow: frozenset[str] | None = None, on_mutation: Callable[[str], None] | None = None, assume_yes: bool = False) -> RunResult:
    """Run one command in-process and capture it (the manager UI's path, tests).

    ``non_interactive`` makes every ordinary confirmation take its default (``no``
    unless ``assume_yes``); typed confirmations (consent, governance-altering
    targets) are refused outright — they stay CLI-only by design.
    """
    out, err = io.StringIO(), io.StringIO()
    console = Console(out=out, err=err, non_interactive=non_interactive, assume_yes=assume_yes)
    full = list(argv)
    if home is not None and "--home" not in full:
        full = ["--home", str(home), *full]
    code, result = execute(full, console, actor=actor, in_gateway=in_gateway, on_mutation=on_mutation, allow=allow)
    return RunResult(code, out.getvalue(), result, err.getvalue())
