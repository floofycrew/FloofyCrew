#!/bin/bash
# build.sh — assemble the public release tree of FloofyCrew from this checkout
# (task 9.2; Requirement 10.5). A thin wrapper around build.py so the public
# release recipe is one command:
#
#   packaging/public/build.sh [--out DIR] [--supports supports.json] [--tag vX.Y.Z] [--check]
#
# Default output: build/public/ (gitignored): dist/floofy.pyz, the wheel, the Loader
# app archive, SHA256SUMS, app/floofycrew/, app-registry.json, install.sh,
# README.md, RELEASE.md. Prints the artifact hashes; --check builds twice and fails
# unless both trees are byte-identical. Needs python3.12 (or $PYTHON) and nothing
# else — standard library only.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python3.12}"
command -v "$PY" >/dev/null 2>&1 || PY=python3
exec "$PY" "$HERE/build.py" "$@"
