#!/usr/bin/env python3
"""Build the ``floofycrew`` wheel with the standard library alone (task 9.2; Requirement 7.2, 10.5).

The repository is a multi-root workspace (``floofy-core/``, ``editions/*/``,
``loader-app/`` …) with ``[tool.uv] package = false`` and no build backend, so
``uv build`` / ``pip wheel`` have nothing to build. This script writes the wheel
directly: the same runtime files ``scripts/build_zipapp.py`` bundles — the shared
core ``floofy_core`` and the chosen edition adapter(s) — plus the ``dist-info``
(``METADATA``, ``WHEEL``, ``entry_points.txt`` with ``floofy = floofy_core.cli.main:main``,
``top_level.txt``, ``RECORD``). No third-party runtime dependency, so the wheel
installs with ``pip install --no-deps`` into the host's own venv or pipx::

    python scripts/build_wheel.py [--out DIR] [--edition public|toolbox|both] [--check]
    pip install dist/floofycrew-<version>-py3-none-any.whl && floofy --version

Deterministic: sorted entries, fixed timestamps, fixed permissions, ``RECORD``
sorted — two builds of the same tree are byte-identical (``--check`` proves it).
The public release ships ``--edition public``; the wheel then carries no internal
identifier (``scripts/check_no_internal_identifiers.py`` scans the unpacked tree in
the release workflow, the long description is ``packaging/public/wheel-description.md``
rather than the repository README, which describes both editions). Version, summary
and licence come from ``pyproject.toml`` and ``floofy_core.__version__`` (they must agree).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import re
import stat
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import build_zipapp  # noqa: E402  (shares EDITIONS, CORE, FIXED_TIME and the member walk)

DIST_NAME = "floofycrew"
DEFAULT_OUT = REPO_ROOT / "dist"
#: The long description: a public-clean text of its own (the repository README describes both editions).
DESCRIPTION_FILE = REPO_ROOT / "packaging" / "public" / "wheel-description.md"
TAG = "py3-none-any"
GENERATOR = "floofycrew-build-wheel (scripts/build_wheel.py)"

__all__ = ["build", "build_bytes", "project_metadata", "wheel_filename"]


def project_metadata() -> dict[str, str]:
    """``name``, ``version``, ``summary``, ``license``, ``requires_python`` from ``pyproject.toml`` (no TOML dependency beyond the stdlib)."""
    import tomllib

    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    core_version = build_zipapp_core_version()
    if project["version"] != core_version:
        raise SystemExit(f"pyproject.toml version {project['version']} != floofy_core.__version__ {core_version}")
    return {
        "name": project["name"],
        "version": project["version"],
        "summary": project.get("description", ""),
        "license": project.get("license", "") if isinstance(project.get("license"), str) else "",
        "requires_python": project.get("requires-python", ">=3.12"),
    }


def build_zipapp_core_version() -> str:
    text = (build_zipapp.CORE / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    if not match:
        raise SystemExit("floofy_core/__init__.py has no __version__ literal")
    return match.group(1)


def wheel_filename(version: str) -> str:
    return f"{DIST_NAME}-{version}-{TAG}.whl"


def _record_hash(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


def _metadata(meta: dict[str, str]) -> str:
    readme = DESCRIPTION_FILE.read_text(encoding="utf-8").strip()
    lines = [
        "Metadata-Version: 2.1",
        f"Name: {meta['name']}",
        f"Version: {meta['version']}",
        f"Summary: {meta['summary']}",
        f"License: {meta['license']}",
        *(f"License-File: {name}" for name in build_zipapp.NOTICE_FILES),
        f"Requires-Python: {meta['requires_python']}",
        "Classifier: Programming Language :: Python :: 3 :: Only",
        "Classifier: Programming Language :: Python :: 3.12",
        "Description-Content-Type: text/markdown",
        "",
        readme,
        "",
    ]
    return "\n".join(lines)


def build_bytes(editions: list[str]) -> bytes:
    """The wheel as bytes (deterministic zip)."""
    meta = project_metadata()
    version = meta["version"]
    dist_info = f"{DIST_NAME}-{version}.dist-info"
    members: list[tuple[str, bytes]] = []
    for name, path in build_zipapp._members(build_zipapp.CORE):
        members.append((name, path.read_bytes()))
    top_level = ["floofy_core"]
    for edition in editions:
        package_dir = build_zipapp.EDITIONS[edition]
        top_level.append(package_dir.name)
        for name, path in build_zipapp._members(package_dir):
            members.append((name, path.read_bytes()))
    # the license and notice files ride in dist-info/licenses/ (Metadata 2.4 License-File)
    for name, data in build_zipapp.notice_members():
        members.append((f"{dist_info}/licenses/{name}", data))
    members.append((f"{dist_info}/METADATA", _metadata(meta).encode("utf-8")))
    members.append((f"{dist_info}/WHEEL", f"Wheel-Version: 1.0\nGenerator: {GENERATOR}\nRoot-Is-Purelib: true\nTag: {TAG}\n".encode("utf-8")))
    members.append((f"{dist_info}/entry_points.txt", b"[console_scripts]\nfloofy = floofy_core.cli.main:main\n"))
    members.append((f"{dist_info}/top_level.txt", ("\n".join(sorted(top_level)) + "\n").encode("utf-8")))
    members.sort(key=lambda item: item[0])
    record = "".join(f"{name},{_record_hash(data)},{len(data)}\n" for name, data in members) + f"{dist_info}/RECORD,,\n"
    members.append((f"{dist_info}/RECORD", record.encode("utf-8")))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=build_zipapp.FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def build(out_dir: Path, editions: list[str]) -> Path:
    """Write the wheel into ``out_dir`` and return its path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / wheel_filename(project_metadata()["version"])
    target.write_bytes(build_bytes(editions))
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(DEFAULT_OUT), help=f"output directory (default {DEFAULT_OUT.relative_to(REPO_ROOT)})")
    parser.add_argument("--edition", choices=("toolbox", "public", "both"), default="public")
    parser.add_argument("--check", action="store_true", help="build twice and exit 1 unless both wheels are byte-identical (also checks an existing wheel in --out)")
    args = parser.parse_args(argv)
    editions = list(build_zipapp.EDITIONS) if args.edition == "both" else [args.edition]
    if args.check:
        first, second = build_bytes(editions), build_bytes(editions)
        if first != second:
            print("NOT reproducible: two consecutive builds differ", file=sys.stderr)
            return 1
        existing = Path(args.out) / wheel_filename(project_metadata()["version"])
        if existing.is_file() and existing.read_bytes() != first:
            print(f"{existing} is stale; rebuild with scripts/build_wheel.py --out {args.out}", file=sys.stderr)
            return 1
        print(f"OK: {wheel_filename(project_metadata()['version'])} builds reproducibly ({len(first)} bytes, editions: {', '.join(editions)})")
        return 0
    built = build(Path(args.out), editions)
    print(f"built {built} ({built.stat().st_size} bytes, editions: {', '.join(editions)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
