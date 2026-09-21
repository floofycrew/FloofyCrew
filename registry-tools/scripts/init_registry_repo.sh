#!/usr/bin/env bash
# Instantiate a FloofyCrew registry repository from registry-tools/templates/registry-repo (task 7.5).
#
#   registry-tools/scripts/init_registry_repo.sh <target-dir> --edition public|internal [options]
#
# Options (passed through to `python -m registry_tools.bootstrap`):
#   --host-version V      write the first compat.json row for host version V
#   --channel C           the row's channel (default: beta for internal, stable for public)
#   --payload DIR         a payload COPY to measure the framework block against (offline; never the live install)
#   --verdict tested|expected|broken   the seeded mods' verdict on that host (default expected)
#   --run URL             a link recorded with the verdict
#   --sign KEY            sign index.json and compat.json with this private key (must be the adapter's pinned key)
#   --seed MOD_ID         seed mods/<id> (repeatable; default rimuru-branding); --no-seed for none
#
# The edition's VALUES (source label, signing key id, archive URL template, host-registry
# row, FloofyCrew repository) come from the edition adapter's registry.json, found by its
# "edition" field under editions/*/floofy_edition_*/registry.json — nothing here names an
# edition, so this directory stays edition-neutral.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../.." && pwd)
python_bin=${PYTHON:-python}

if [ $# -lt 1 ]; then
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
fi
target=$1; shift
edition=""
passthrough=()
while [ $# -gt 0 ]; do
  case "$1" in
    --edition) edition=$2; shift 2 ;;
    --edition=*) edition=${1#--edition=}; shift ;;
    *) passthrough+=("$1"); shift ;;
  esac
done
case "$edition" in
  public) wanted="external" ;;
  internal) wanted="internal" ;;
  *) echo "error: --edition public|internal is required" >&2; exit 2 ;;
esac

config=$("$python_bin" - "$root" "$wanted" <<'PY'
import glob, json, sys
root, wanted = sys.argv[1], sys.argv[2]
for path in sorted(glob.glob(f"{root}/editions/*/floofy_edition_*/registry.json")):
    with open(path, encoding="utf-8") as handle:
        if json.load(handle).get("edition") == wanted:
            print(path)
            break
else:
    sys.exit(f"no edition adapter registry.json with edition={wanted!r} under {root}/editions")
PY
)

export PYTHONPATH="$root/floofy-core:$root/registry-tools${PYTHONPATH:+:$PYTHONPATH}"
exec "$python_bin" -m registry_tools.bootstrap "$target" --edition-config "$config" --floofycrew-root "$root" "${passthrough[@]}"
