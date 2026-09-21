#!/usr/bin/env python3
"""Refresh (or check) the ``files[].sha256`` entries of FloofyCrew mod manifests.

Every shipped file of a mod is listed in ``floofy.json`` ``files[]`` with its
SHA-256 (Requirement 1.6); the validator refuses a mismatch with
``MissingFiles``. This helper keeps the example mods under
``floofy-core/examples/*/`` honest and doubles as a hand tool for mod authors.

Usage:
    python scripts/hash_example_files.py                # rewrite the examples' hashes
    python scripts/hash_example_files.py --check        # exit 1 if any hash is stale
    python scripts/hash_example_files.py path/to/mod …  # operate on given mod roots
    python scripts/hash_example_files.py --add-missing  # also list unlisted files

Standard library only. Formatting of the manifest is preserved when each
``files[]`` entry is written as ``{"path": "...", "sha256": "..."}`` on one
line; otherwise the manifest is rewritten with two-space indentation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = REPO_ROOT / "floofy-core" / "examples"
MANIFEST_NAME = "floofy.json"

#: Directory names that are never part of a shipped mod.
SKIP_DIRS = frozenset({".git", "__pycache__", "node_modules", ".hypothesis", ".pytest_cache"})


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def shipped_files(root: Path) -> list[str]:
    """Every regular file under ``root`` except the manifest, hidden files and caches."""
    found: list[str] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts):
            continue
        if path.is_file() and rel.as_posix() != MANIFEST_NAME:
            found.append(rel.as_posix())
    return found


def refresh_manifest(root: Path, *, check: bool, add_missing: bool) -> tuple[bool, list[str]]:
    """Return ``(changed, messages)``; rewrite the manifest unless ``check``."""
    manifest_path = root / MANIFEST_NAME
    text = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(text)
    files = manifest.get("files")
    if not isinstance(files, list):
        return False, [f"{manifest_path}: no files[] list"]

    messages: list[str] = []
    changed = False
    listed = {entry["path"] for entry in files if isinstance(entry, dict) and "path" in entry}
    unlisted = [rel for rel in shipped_files(root) if rel not in listed]
    if unlisted:
        if add_missing:
            for rel in unlisted:
                files.append({"path": rel, "sha256": "0" * 64})
                messages.append(f"{root.name}: added {rel}")
            changed = True
        else:
            messages.extend(f"{root.name}: unlisted file {rel} (use --add-missing)" for rel in unlisted)

    for entry in files:
        target = root / entry["path"]
        if not target.is_file():
            messages.append(f"{root.name}: listed file missing: {entry['path']}")
            continue
        actual = sha256_of(target)
        if entry.get("sha256") != actual:
            messages.append(f"{root.name}: {entry['path']}: {entry.get('sha256', '?')[:12]}… -> {actual[:12]}…")
            entry["sha256"] = actual
            changed = True

    if changed and not check:
        manifest_path.write_text(_render(text, manifest, files), encoding="utf-8")
    return changed, messages


def _render(original: str, manifest: dict, files: list[dict]) -> str:
    """Substitute hashes in place when every entry is a one-line object; else re-dump."""
    updated = original
    for entry in files:
        pattern = re.compile(
            r'("path"\s*:\s*"' + re.escape(entry["path"]) + r'"\s*,\s*"sha256"\s*:\s*")[0-9a-fA-F]{64}(")'
        )
        updated, count = pattern.subn(r"\g<1>" + entry["sha256"] + r"\g<2>", updated, count=1)
        if count != 1:
            return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    return updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("roots", nargs="*", type=Path, help="mod roots (default: floofy-core/examples/*)")
    parser.add_argument("--check", action="store_true", help="report stale hashes and exit 1 instead of rewriting")
    parser.add_argument("--add-missing", action="store_true", help="append unlisted shipped files to files[]")
    args = parser.parse_args(argv)

    roots = args.roots or sorted(p.parent for p in EXAMPLES_DIR.glob(f"*/{MANIFEST_NAME}"))
    if not roots:
        print("no manifests found", file=sys.stderr)
        return 2

    any_changed = False
    for root in roots:
        if not (root / MANIFEST_NAME).is_file():
            print(f"skip {root}: no {MANIFEST_NAME}", file=sys.stderr)
            continue
        changed, messages = refresh_manifest(root, check=args.check, add_missing=args.add_missing)
        any_changed = any_changed or changed
        for message in messages:
            print(message)

    if args.check:
        print("stale hashes found" if any_changed else "all hashes current")
        return 1 if any_changed else 0
    print("hashes rewritten" if any_changed else "all hashes current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
