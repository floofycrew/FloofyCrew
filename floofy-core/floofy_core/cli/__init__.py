"""The ``floofy`` command-line manager (Requirement 7.1, 7.2, design "Manager (CLI + UI)").

Standard-library Python 3.12 only, so it runs on the host's own interpreter and
on the bundle interpreter, as a package (``floofy`` console script,
``python -m floofy_core.cli``) or as the single-file zipapp
``scripts/build_zipapp.py`` produces. One sub-command module per concern; the
edition adapter is chosen by probing (:mod:`floofy_core.editions`), the manager UI
calls :func:`floofy_core.cli.main.run` in-process so CLI and UI never diverge.

Owner decisions this package must never erode (design DR-5, Requirement 11):
consent outranks host governance (governance is shown as warnings, never a
gate), every governance-crossing step is explicit, named, confirmed and
audited, the one-time consent and per-file governance-target confirmations are
never satisfied by ``--yes``, and FloofyCrew never rewrites the host's
governance files.
"""
from __future__ import annotations

__all__ = ["main", "run"]


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin re-export
    from .main import main as _main

    return _main(argv)


def run(argv: list[str], **kwargs):  # pragma: no cover - thin re-export
    from .main import run as _run

    return _run(argv, **kwargs)
