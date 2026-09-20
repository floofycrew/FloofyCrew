#!/usr/bin/env python3
"""Re-measure the two editions' byte-identity and rewrite the numbers the docs quote.

``floofy-core/tests/test_two_track.py::test_docs_quote_the_measured_numbers``
pins ``docs/two-track.md`` (the ``files`` / ``bytes`` rows) and ``README.md`` (the
"x % of files, y % of bytes" sentence) to what
``scripts/artifact_identity_report.py`` measures on the current tree. Every
commit that adds or changes a shipped file moves those numbers; this script
rewrites them in place so the change lands in the same commit. Standard library only.

Usage: python scripts/refresh_identity_numbers.py [--check]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "two-track.md"
README = REPO_ROOT / "README.md"


def grouped(number: int) -> str:
    return f"{number:,}".replace(",", " ")


def main(argv: list[str] | None = None) -> int:
    check = "--check" in (argv or sys.argv[1:])
    raw = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "artifact_identity_report.py"), "--json"], capture_output=True, text=True, check=True, cwd=str(REPO_ROOT)).stdout
    report = json.loads(raw)
    # the JSON report rounds its percentages to two decimals; the test rounds the raw ratio to one — recompute from the counts
    file_percent = round(report["identicalFiles"] * 100.0 / report["totalFiles"], 1)
    byte_percent = round(report["identicalBytes"] * 100.0 / report["totalBytes"], 1)
    files_row = f"| files | {report['identicalFiles']} | {report['totalFiles']} | {file_percent} % |"
    bytes_row = f"| bytes | {grouped(report['identicalBytes'])} | {grouped(report['totalBytes'])} | {byte_percent} % |"
    doc = DOC.read_text(encoding="utf-8")
    new_doc = re.sub(r"^\| files \| \d+ \| \d+ \| [\d.]+ % \|$", files_row, doc, count=1, flags=re.M)
    new_doc = re.sub(r"^\| bytes \| [\d ]+ \| [\d ]+ \| [\d.]+ % \|$", bytes_row, new_doc, count=1, flags=re.M)
    differing = report["totalFiles"] - report["identicalFiles"]
    new_doc = re.sub(r"^The \d+ differing paths are", f"The {differing} differing paths are", new_doc, count=1, flags=re.M)
    readme = README.read_text(encoding="utf-8")
    new_readme = re.sub(r"\([\d.]+ % of files, [\d.]+ % of bytes on the current tree;", f"({file_percent} % of files, {byte_percent} % of bytes on the current tree;", readme, count=1)
    changed = [p for p, old, new in ((DOC, doc, new_doc), (README, readme, new_readme)) if old != new]
    if check:
        print("stale: " + ", ".join(str(p.relative_to(REPO_ROOT)) for p in changed) if changed else "up to date")
        return 1 if changed else 0
    DOC.write_text(new_doc, encoding="utf-8")
    README.write_text(new_readme, encoding="utf-8")
    print(f"{files_row}\n{bytes_row}\nupdated: {', '.join(str(p.relative_to(REPO_ROOT)) for p in changed) or 'nothing'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
