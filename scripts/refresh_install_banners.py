#!/usr/bin/env python3
"""Refresh the installers' copies of the terminal banner from ``branding/ascii/``.

The installers cannot import the CLI, so they carry the art themselves:

* ``packaging/public/install.sh`` embeds it as shell literals (a piped script has no
  files beside it): ``BANNER_TXT`` / ``BANNER_ANS`` (the fox) and ``BANNER_WIDE_TXT`` /
  ``BANNER_WIDE_ANS`` (the fox with the wordmark beside it, printed from 110 columns).
  The ``.ans`` art is stored with ``ESC`` written as ``\\033`` and printed via ``printf %b``.
* ``packaging/internal/FloofyCrew/banner/`` ships ``floofy.{ans,txt}`` and
  ``floofy-wide.{ans,txt}`` as byte-identical copies.

Run after ``branding/build_ascii.py``; ``packaging/tests/test_install_scripts.py`` holds
both installers to the branding files. Standard library only.

Usage: python scripts/refresh_install_banners.py [--check]
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BRANDING = REPO_ROOT / "branding" / "ascii"
PUBLIC_SCRIPT = REPO_ROOT / "packaging" / "public" / "install.sh"
INTERNAL_BANNER_DIR = REPO_ROOT / "packaging" / "internal" / "FloofyCrew" / "banner"

#: shell variable -> (branding file, ANSI escapes encoded for printf %b)
LITERALS = {
    "BANNER_TXT": ("floofy.txt", False),
    "BANNER_ANS": ("floofy.ans", True),
    "BANNER_WIDE_TXT": ("floofy-wide.txt", False),
    "BANNER_WIDE_ANS": ("floofy-wide.ans", True),
}
FILES = ("floofy.ans", "floofy.txt", "floofy-wide.ans", "floofy-wide.txt")


def literal(name: str) -> str:
    source, escapes = LITERALS[name]
    text = (BRANDING / source).read_text(encoding="utf-8")
    if "'" in text:
        raise SystemExit(f"{source} contains a single quote; the shell literal would break")
    body = text.rstrip("\n")  # the closing quote sits on the last art row; the script adds the newline
    if escapes:
        body = body.replace("\x1b", "\\033")
    return f"{name}='{body}'"


def rendered_public(script: str) -> str:
    """The public script with every banner literal replaced (appended after BANNER_ANS when missing)."""
    for name in LITERALS:
        # the art never contains a single quote (literal() refuses one), so the literal is
        # everything up to the next quote
        pattern = re.compile(rf"^{name}='[^']*'\n", re.M)
        replacement = literal(name) + "\n"
        if pattern.search(script):
            script = pattern.sub(lambda _m, r=replacement: r, script, count=1)
        else:
            anchor = re.search(r"^BANNER_ANS='[^']*'\n", script, re.M)
            if anchor is None:
                raise SystemExit("install.sh: BANNER_ANS literal not found; cannot place " + name)
            script = script[: anchor.end()] + replacement + script[anchor.end() :]
    return script


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when an installer copy is stale")
    args = parser.parse_args(argv)
    missing = [name for name in FILES if not (BRANDING / name).exists()]
    if missing:
        print(f"missing {missing} — run branding/build_ascii.py first", file=sys.stderr)
        return 2
    current = PUBLIC_SCRIPT.read_text(encoding="utf-8")
    wanted = rendered_public(current)
    stale = []
    if wanted != current:
        stale.append(str(PUBLIC_SCRIPT.relative_to(REPO_ROOT)))
        if not args.check:
            PUBLIC_SCRIPT.write_text(wanted, encoding="utf-8")
    INTERNAL_BANNER_DIR.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        target = INTERNAL_BANNER_DIR / name
        if not target.exists() or target.read_bytes() != (BRANDING / name).read_bytes():
            stale.append(str(target.relative_to(REPO_ROOT)))
            if not args.check:
                shutil.copyfile(BRANDING / name, target)
    if args.check:
        if stale:
            print("stale installer banners: " + ", ".join(stale) + " — run scripts/refresh_install_banners.py", file=sys.stderr)
            return 1
        print("OK: installer banners match branding/ascii")
        return 0
    print("refreshed: " + (", ".join(stale) if stale else "nothing to do"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
