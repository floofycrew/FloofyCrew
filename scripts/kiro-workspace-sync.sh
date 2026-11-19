#!/bin/bash
# kiro-workspace-sync.sh -- put this checkout's kiro-cli agents in place from their committed
# sources (editions/public/kiro/agents/), so `.kiro/agents/` can stay untracked.
#
# Why: KiroCrew rewrites `.kiro/agents/*.json` in place whenever a session opens on the
# checkout (an upgrade stamp, injected per-session entries), so tracking the copies meant a
# permanently dirty tree. The sources live where nothing rewrites them; this script copies them
# into the path kiro-cli reads. The skill the agent loads (`.kiro/skills/floofy-mod-dev/SKILL.md`)
# is tracked and needs no sync.
#
#   scripts/kiro-workspace-sync.sh            # copy when missing or when the source changed since the last sync
#   scripts/kiro-workspace-sync.sh --force    # overwrite every agent
#   scripts/kiro-workspace-sync.sh --check    # exit 1 when a sync is needed; changes nothing
#
# Agents are compared through a sidecar hash (.kiro/agents/.floofy-src-<name>) rather than
# against the live file, because the live file is expected to differ from the source once a
# session has rewritten it. Run it once after a fresh clone; it is idempotent. Then:
#
#   kiro-cli chat        # and pick the agent: /agent → floofy-mod-dev
set -u

HERE="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$HERE/editions/public/kiro"
DST="$HERE/.kiro"
MODE="sync"
case "${1:-}" in
  --force) MODE="force" ;;
  --check) MODE="check" ;;
  "") ;;
  -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
  *) echo "unknown argument: $1" >&2; exit 2 ;;
esac

digest() { sha256sum "$1" | cut -c1-64; }

needed=0
copy() {  # src dst reason
  if [ "$MODE" = "check" ]; then
    echo "needs sync: $2 ($3)"; needed=1
  else
    mkdir -p "$(dirname "$2")"
    cp "$1" "$2" && echo "synced: ${2#"$HERE"/} ($3)"
  fi
}

for src in "$SRC"/agents/*.json; do
  [ -f "$src" ] || continue
  name="$(basename "$src")"
  dst="$DST/agents/$name"
  marker="$DST/agents/.floofy-src-${name%.json}"
  want="$(digest "$src")"
  have="$(cat "$marker" 2>/dev/null || true)"
  if [ "$MODE" = "force" ]; then
    copy "$src" "$dst" "forced"
  elif [ ! -f "$dst" ]; then
    copy "$src" "$dst" "missing"
  elif [ "$want" != "$have" ]; then
    copy "$src" "$dst" "source changed"
  else
    [ "$MODE" = "check" ] || echo "current: ${dst#"$HERE"/}"
    continue
  fi
  [ "$MODE" = "check" ] || printf '%s\n' "$want" > "$marker"
done

[ "$MODE" = "check" ] && [ "$needed" -eq 1 ] && exit 1
exit 0
