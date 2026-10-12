"""Smoke test: the mod validates cleanly (run by CI and by hand).

``--refresh`` rewrites the ``files[]`` hashes after you edited files.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: Never shipped (git ignores them, installs copy around them): the validator
#: rejects a files[] entry under any of these, so the refresh must not list one.
UNSHIPPED_DIRS = frozenset({".git", "__pycache__", "node_modules", ".hypothesis", ".pytest_cache"})


def refresh() -> None:
    manifest_path = ROOT / "floofy.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = []
    for path in sorted(ROOT.rglob("*")):
        parts = path.relative_to(ROOT).parts
        rel = path.relative_to(ROOT).as_posix()
        if not path.is_file() or rel == "floofy.json" or any(part in UNSHIPPED_DIRS or part.startswith(".") for part in parts):
            continue
        files.append({"path": rel, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest["files"] = files
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    if "--refresh" in sys.argv:
        refresh()
        print("files[] refreshed")
        return 0
    result = subprocess.run([sys.executable, "-m", "floofy_core.cli", "validate", str(ROOT)], text=True)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
