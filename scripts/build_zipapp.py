#!/usr/bin/env python3
"""Build the single-file ``floofy`` zipapp (Requirement 7.2).

``dist/floofy.pyz`` bundles ``floofy_core`` (the CLI included) and the two edition
adapters, with a ``__main__`` that calls ``floofy_core.cli.main.main()``. It runs on
any Python 3.12 — the host's own interpreter, the bundle interpreter — with no
third-party dependency; an adapter is inert on a host it does not recognise, so
one file serves both editions::

    python scripts/build_zipapp.py [--out dist/floofy.pyz] [--edition toolbox|public|both]
                                   [--python /usr/bin/env python3.12] [--check]
    python3.12 dist/floofy.pyz --version

``--check`` rebuilds into a temporary file and exits 1 when ``--out`` differs
(CI freshness). The archive is written with fixed timestamps so two builds of
the same tree are byte-identical. Standard library only.
"""
from __future__ import annotations

import argparse
import io
import os
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "dist" / "floofy.pyz"
EDITIONS = {"toolbox": REPO_ROOT / "editions" / "toolbox" / "floofy_edition_toolbox", "public": REPO_ROOT / "editions" / "public" / "floofy_edition_public"}
CORE = REPO_ROOT / "floofy-core" / "floofy_core"
SKIP_NAMES = frozenset({"__pycache__", "tests", ".pytest_cache", ".hypothesis", "build", "node_modules", ".git"})
#: A fixed, valid zip timestamp (zip cannot store dates before 1980) for reproducible archives.
FIXED_TIME = (2020, 1, 1, 0, 0, 0)

MAIN_PY = '''"""floofy zipapp entry: the FloofyCrew mod manager for KiroCrew (unofficial)."""
import sys

from floofy_core.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
'''


#: The legal files every distribution carries (Apache-2.0 §4: the themes.py port
#: and the quoted anchors derive from KiroCrew — see NOTICE.md).
NOTICE_FILES: tuple[str, ...] = ("LICENSE", "NOTICE.md", "LICENSES/Apache-2.0.txt")


def notice_members(prefix: str = "") -> list[tuple[str, bytes]]:
    """``(archive name, bytes)`` for the license and notice files, sorted by name."""
    root = Path(__file__).resolve().parent.parent
    return sorted((f"{prefix}{name}", (root / name).read_bytes()) for name in NOTICE_FILES)


def _members(package_dir: Path) -> list[tuple[str, Path]]:
    """``(archive name, source path)`` for every runtime file of a package, sorted."""
    out: list[tuple[str, Path]] = []
    for path in sorted(package_dir.rglob("*")):
        rel = path.relative_to(package_dir.parent)
        if any(part in SKIP_NAMES for part in rel.parts) or path.is_dir() or path.suffix == ".pyc":
            continue
        out.append((rel.as_posix(), path))
    return out


def build_bytes(editions: list[str], interpreter: str) -> bytes:
    """The zipapp as bytes (shebang + deterministic zip)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        def add(name: str, data: bytes) -> None:
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, data)

        add("__main__.py", MAIN_PY.encode("utf-8"))
        for name, data in notice_members():
            add(name, data)
        for name, path in _members(CORE):
            add(name, path.read_bytes())
        for edition in editions:
            for name, path in _members(EDITIONS[edition]):
                add(name, path.read_bytes())
    shebang = f"#!{interpreter}\n".encode("utf-8")
    return shebang + buffer.getvalue()


def build(out: Path, editions: list[str], interpreter: str) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(build_bytes(editions, interpreter))
    out.chmod(out.stat().st_mode | 0o755)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(DEFAULT_OUT), help=f"output file (default {DEFAULT_OUT.relative_to(REPO_ROOT)})")
    parser.add_argument("--edition", choices=("toolbox", "public", "both"), default="both")
    parser.add_argument("--python", default="/usr/bin/env python3.12", help="shebang interpreter")
    parser.add_argument("--check", action="store_true", help="exit 1 when --out is not a fresh build")
    args = parser.parse_args(argv)
    editions = list(EDITIONS) if args.edition == "both" else [args.edition]
    out = Path(args.out).resolve()
    if args.check:
        if not out.is_file():
            print(f"{out} does not exist", file=sys.stderr)
            return 1
        if out.read_bytes() != build_bytes(editions, args.python):
            print(f"{out} is stale; rebuild with scripts/build_zipapp.py --out {out}", file=sys.stderr)
            return 1
        print(f"OK: {out} is a fresh build")
        return 0
    built = build(out, editions, args.python)
    print(f"built {built} ({built.stat().st_size} bytes, editions: {', '.join(editions)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
