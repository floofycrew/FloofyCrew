#!/bin/bash
# seam-check.sh -- read-only: does the installed KiroCrew payload still expose the seams the
# agent-orchestrator mod hooks? Run on the host that will run the mod:  bash seam-check.sh [version]
# Prints one line per seam: FOUND / MISSING. Nothing is modified.
set -u
VER="${1:-$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.***REMOVED***/tools/kirocrew/info.json")))["CurrentVersion"])' 2>/dev/null)}"
PKG="$HOME/.***REMOVED***/tools/kirocrew/$VER/lib/python3.12/site-packages/kiro_crew"
[ -d "$PKG" ] || { echo "no payload at $PKG"; exit 2; }
echo "payload: $VER"
check() { # name, file, regex
  if grep -qE "$3" "$PKG/$2" 2>/dev/null; then echo "FOUND    $1"; else echo "MISSING  $1  ($2 !~ $3)"; fi
}
check "DashboardState.broadcast_ws"            dashboard/state.py            'def broadcast_ws\('
check "DashboardState._broadcast"              dashboard/state.py            'def _broadcast\('
check "DashboardState.get_slot"                dashboard/state.py            'def get_slot\('
check "DashboardState.push_slots_update"       dashboard/state.py            'def push_slots_update\('
check "slots note carries _slots_list_ws"      dashboard/state.py            '_slots_list_ws'
check "chat_delivery.steer_into_running_turn"  dashboard/chat_delivery.py    'def steer_into_running_turn\('
check "chat_delivery.STEER_STEERED"            dashboard/chat_delivery.py    '^STEER_STEERED'
check "chat_runner._run_chat"                  dashboard/chat_runner.py      'def _run_chat\('
check "slot.enqueue_or_run_prompt"             dashboard/state.py            'def enqueue_or_run_prompt\('
check "route session-control/create"           dashboard/session_control.py  'session-control/create'
check "route session-control/send"             dashboard/session_control.py  'session-control/send'
check "route work-ledger/report"               mcp_work.py                   'work-ledger/report'
check "KIROCREW_SESSION_KEY in acp client"     acp/client.py                 'KIROCREW_SESSION_KEY'
check "KAS: kas_agents._project_mcp_servers"   acp/kas_agents.py             'def _project_mcp_servers\('
check "KAS: projection takes session_key"      acp/kas_agents.py             'KIROCREW_SESSION_KEY.*= session_key'
check "slot projection: created_by"            dashboard/slot_projection.py  'created_by'
check "slot projection: needs_input"           dashboard/slot_projection.py  'needs_input'
check "create_session inherits _trust posture" dashboard/session_control.py  '_trust'
echo "-- route registrations (any file):"
grep -rn --include='*.py' -E 'session-control/(create|send)|work-ledger/report' "$PKG" | grep -vE '^\s*#|docstring' | sed "s#$PKG/##" | head -8
