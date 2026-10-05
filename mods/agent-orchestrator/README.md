# Agent Orchestrator

A FloofyCrew mod for KiroCrew that adds real parallel multi-agent orchestration
with a centralized orchestrator, agent-to-agent communication, and a live
hub-and-spoke graph of the run. FloofyCrew is unofficial and not affiliated
with Kiro or KiroCrew.

## Why

KiroCrew can already fan out — `spawn_run` subagents, and the
conductor/worker pattern (`session_create` + the work ledger) — but every return
trip is either a whole LLM turn or a file the conductor must remember to poll,
workers cannot address each other, nothing wakes a session when a peer has
something for it, and there is no live per-worker view. So a run still reads
like one linear chat. This mod closes those gaps without changing how the host
spawns or controls sessions; the research and design are in
`research/orchestration-mod/` of the FloofyCrew repo.

## What it adds

- **A message bus** per run (`@agent-orchestrator-bus`, six MCP tools):
  `bus_roster`, `bus_post` (say / ask / answer — a worker posts to the
  orchestrator, the orchestrator posts to a worker or `*`), `bus_inbox`
  (optionally blocking — the rendezvous for an answer), `bus_status` (the live
  ≤140-char summary line), `bus_wait` (orchestrator: wait-any over messages,
  reports, idle / needs-input / stall / gone events) and `bus_run` (goal,
  close). The topology is **hub-and-spoke**: every worker reports to the
  orchestrator, which relays what a sibling needs to know. A direct worker →
  worker post is refused (`peer_messaging_off`) unless the operator switches
  the `peer_messaging` setting on.
- **Wake-ups that carry no body**: a post nudges the recipient — a mid-turn steer
  on kiro/KAS, a new turn on an idle session — with a one-line notice; the body
  is read as a *tool result* wrapped in a provenance header. Another agent's
  words are information, never an instruction and never an approval.
- **Two agents**, rendered into `~/.kiro/agents/` on activation (owner-sidecar'd,
  removed on deactivate, never overwriting a spec that is not the mod's):
  `agent-orchestrator` (no file-writing or shell tool — it dispatches, waits and
  decides) and `agent-orchestrator-worker` (does one item, reports via the
  ledger, talks on the bus).
- **The graph page** (FloofyCrew App → Mods → Agent Orchestrator): orchestrator
  in the centre, one card per worker with state glyph, progress bar, liveness
  dot, age and the live summary; messages travel as coloured dots along the
  edges; a `message bus` log and a footer (`N agents <goal> · M messages on the
  bus`). Click a card to open its tab; shift-click to filter the log. It also
  draws the host's own `kirocrew-conductor` fleets (without bus messages).
- **An optional cap** (`enforce_cap`, default on): a create by one of the mod's
  orchestrators above `max_workers` live children is refused with a
  non-retryable error. Every other caller passes through untouched.

The host's `session_create / session_send / session_read_message` and
`work_brief / work_report / work_ledger_*` stay exactly as they are; the mod
*observes* the dashboard's frames and its `session_send` / `work_report` routes
to draw the whole conversation.

## Identity and security

Bus identity is the `KIROCREW_SESSION_KEY` the host sets in every kiro-cli
child it spawns for a dashboard tab — the same value the host's first-party
`kirocrew-work` server trusts. A `spawn_run` subagent has none and is refused.
On the **KAS backend** the host projects a spec's `mcpServers` onto the wire and
withholds `env` for every server it does not own, so the key never reaches the
bus there; the hook bridges that with an after-hook on
`kas_agents._project_mcp_servers` that puts the same session key the host gives
its managed servers onto the mod's own bus server (recognised by its argv, not
its name) and nothing else. Without the seam (`ctx.state.kas_identity_bridge`
false) KAS sessions get `caller_unidentified` and the agents fall back to the
ledger; kiro-cli sessions are unaffected.
A member may address only members of its own run (depth 1, derived from
`slots[].created_by`); a worker addresses only the orchestrator unless
`peer_messaging` is on; only the orchestrator may broadcast or `bus_wait`.
Transport is a 0600 unix socket in the mod's data dir; no port is opened. Caps
refuse rather than truncate (2000 chars per message, 500 per run, 20 per minute
per sender, 140 chars per status).

## Use

1. Install and enable the mod; open a chat with the agent `agent-orchestrator`
   and give it a goal. It plans items, creates one `agent-orchestrator-worker`
   session per item (disjoint file ownership), binds each to a ledger item and
   seeds it, then loops on `bus_wait`.
2. Open FloofyCrew → Mods → *Agent Orchestrator* to watch the run.
3. Settings (the page's config, `.floofy/config.json`): `max_workers` (6),
   `enforce_cap`, `peer_messaging` (off — hub-and-spoke; on lets workers
   address each other directly), `wake_idle`, `steer_running`,
   `nudge_cooldown_secs` (10), `inbox_wait_max_secs` (300),
   `message_max_chars` (2000), `run_retention_days` (7),
   `follow_session_delete` (on), `stall_secs` (600).
4. A run stays on the page as long as its orchestrator chat exists. **Deleting**
   that chat for good (the host's history delete, single or "clear closed
   sessions") removes the run's record with it — bus log, status lines, meta —
   while a worker tab is still open the record waits for that tab. Merely
   **closing** the tabs moves the chats to history and leaves the run on the
   page as `○ archived (chats gone)` with a `✕ remove` button; the record also
   ages out by itself after `run_retention_days` without activity (closed or
   not). Set `follow_session_delete` to `false` to keep records across deletes
   and remove them only by hand.

A scripted end-to-end demo (four workers, two relayed questions, verdicts, a
`session_send` follow-up, close) is in `demo/README.md` with the goal prompt in
`demo/prompt.txt`; `python3 demo/check.py` verifies both what the run produced
and, from the bus log, that it stayed hub-and-spoke. `demo/seam-check.sh` is a
read-only check that a host's KiroCrew payload still exposes the seams the hook
uses (run it before installing on a new host version).

Backends: the mid-turn nudge needs `_session/steer` (kiro-cli, KAS). On Claude
Code / Codex backends a running worker is not nudged; it reads the bus at its
next step (the worker prompt says to), and `bus_inbox(wait_secs)` still blocks.

## Develop

```sh
floofy validate .                        # schema, hashes, parts, targets, network scan
uv run pytest mods/agent-orchestrator    # bus store/service, graph reducer, MCP server, hook
python tests/page_probe.py [--screenshot out.png]  # headless Chromium, END TO END: the real SPA mod API,
                                         # the real Loader route dispatch, the real hook on a seeded run
floofy dev . --follow                    # link into the data home, stream the mod's log
python tests/smoke_test.py --refresh     # rewrite files[] hashes after editing
```

Layout: `hook/` (bus store, graph reducer, RPC service + unix socket, the
Loader entry point), `mcp/server.py` (stdlib stdio MCP server the agent specs
launch), `agents/*.json.tmpl` (rendered per machine), `ui/` (the graph page).
