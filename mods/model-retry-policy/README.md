# Model Retry Policy

A FloofyCrew mod for KiroCrew that decides what happens when the model or its
API fails with a transient error — throttling, "model under high load", a 5xx,
a dropped stream. KiroCrew's own answer is fixed: three same-model retries on a
2 s doubling curve, then the `agent.fallback_model` chain (two attempts per
candidate), then the error. This mod lets you choose instead:

| Mechanism | What it does |
|---|---|
| **KiroCrew default** | nothing is overridden (the state when the mod is installed but you have not picked anything) |
| **Retry N times** | N same-model retries of your choosing; KiroCrew's own fallback chain still runs after them |
| **Retry forever, fixed interval** | retry every N seconds until the turn succeeds or you press Stop |
| **Retry forever, backoff** | exponential backoff between retries (base × factor^n, capped) — never gives up |
| **Fallback profile** | retry the primary N times, then move to the next model in the profile for its own number of attempts, and so on; when the chain is exhausted either show the error or keep retrying the last model forever |

Independent of those, and off by default:

| Mechanism | What it does |
|---|---|
| **Backend failover** | when a dashboard chat's same-model retries are spent, move the chat to **another agent backend that serves the same model** (kiro-cli ↔ KAS ↔ Claude Code ↔ Codex) before any model chain is walked; the model never changes, the conversation is replayed on the new backend |

It applies to every surface that retries: dashboard chats, cron and Slack turns
(`stream_and_collect`), and sub-agents. The page shows the sequence of attempts
your settings produce before you save them, and a live log of the retries,
model switches, backend moves and exhaustions that actually happened.

Designed to run alongside **Agent Backend Router**: that mod picks which agent
backend a *session* runs on; this one governs what a *turn* does when its
model fails — and, for the backend failover, asks the router where a throttled
session may go and files the request for its next start. Their hooks touch
different host seams. Note that each retry is a new turn attempt, so the
router's telemetry counts every failed attempt as an error sample (its own
fallback strategy trips on those). The failover also depends on **Agent Backend
Doctor**, which answers whether a target backend is usable right now.

## How it hooks in

KiroCrew binds the retry primitives by name in three modules
(`llm_helpers`, `dashboard/chat_runner`, the sub-agent runner). The gateway
hook (`hook/__init__.py`) sits on the seams they share:

* `transient_retry_delay` (the wait between retries) and
  `configured_fallback_chain` (the model chain) — `replace` hooks at every
  module that calls them;
* `FallbackState.should_retry_active` — the per-candidate attempt budget on the
  unattended surfaces;
* the dashboard's `_fallback_swap_for_turn` + `fallback_rewound_transient_budget`
  — the interactive ladder encodes the per-candidate budget as a counter rewind;
* `TRANSIENT_RETRIES` — a plain `int` the host compares against, overridden by
  attribute assignment on the modules that bind it and restored on deactivate
  (the Loader deactivates a faulting mod, so a fault restores it too);
* the dashboard's `_ChatSlot.queue_insert`, with `_ChatSlot.append` as the
  witness — the **mid-turn** recovery. The ladder above only runs when zero
  output has streamed. An error that lands after the turn already produced
  text or ran a tool is handled by a separate host path: the live session is
  re-prompted with a "continue" instruction, exactly **once** per user turn
  (`_posttoken_retry_used`), whatever the retry budget says — and in a long
  tool-using turn that is where nearly every throttle lands. The hook counts
  those recoveries per slot and, while your mid-turn budget is not spent,
  clears the one-shot flag again right after the host has enqueued the
  recovery, so the next mid-turn error recovers too. The budget follows the
  mechanism you chose: N for "retry N times", unlimited for the two "forever"
  mechanisms, and for a profile every attempt it allows in total (unlimited
  with "keep retrying the last model") — KiroCrew cannot switch models in the
  middle of a turn, so those all run on the current model. The configured
  delay curve applies between mid-turn recoveries as well.

Every hook calls straight through when you have not opted in, so the default
is KiroCrew's behaviour byte-for-byte. Stop ends any retry loop — "forever"
means until the turn succeeds, you stop it, the session is reset or the
gateway restarts; a Stop pressed during a recovery's backoff withholds the
recovery, and the mod never re-arms what was not enqueued. The host's
start-of-turn restore probe still moves a session back to its primary model
once that model recovers.

Not governed: the Slack cron's outer two-retry wrapper around a whole job
(it recurses per attempt and wraps the governed turn), Discord's own
counter, and the sub-agent runner's own one-shot post-output recovery (a
hand-maintained copy of the dashboard rule in `subagent.py`; sub-agents are
covered for pre-output errors only).

## Backend failover

KiroCrew fixes a session's agent backend when the session is created: the
backend *is* the harness process (kiro-cli's v2 loop, the KAS relay, Claude
Code's adapter, codex-acp), and nothing in the gateway can hand a live
process's conversation to another one. What the host does have is a
cross-backend continuity path it uses for its own model and agent switches:
reset the session (process gone, conversation entry kept), cold-start a new
one, and — because the provider label changed — replay the conversation from
KiroCrew's own `conversation_log` as the first prompt
(`build_session_replay`; ~20K tokens at most, scaled to the model window,
tool results omitted).

The failover arranges exactly those three steps when a throttle exhausts the
same-model budget, from two seams:

1. **`_fallback_swap_for_turn`** — the host's model-swap seam, where this mod
   already sits. When the failover is on and the session is not yet on a
   fallback model, the hook asks the router for the backends that could take
   the session over *on the same model* (`failover_candidates`: the configured
   pool in order after the current backend, each one passed through the
   router's served-list identity rule and its agent pins), skips any that
   itself throttled within `cooldown_s` or that the doctor says is not usable
   right now (`backend_ready`: not installed, or a Codex whose AWS preflight
   would fail; a cached "not found" is cleared on the way), files a route
   request with the router for the session's next cold start
   (`request_route`), marks the session key *pending*, and answers the ladder
   with the model itself — so the host re-queues the same message, exactly as
   it does after its own model swap, and the counter is rewound to give the new
   backend the whole same-model budget.
2. **`SessionManager.get_or_create`** — the allocation the dashboard performs
   when the re-queued message is dequeued. For a pending key the hook first
   runs the host's own `reset` (skipped, and the move dropped, if a turn is
   still in flight on the key), then lets the allocation cold-start. The router
   honours the request at construction (inside its usual fence), the host sees
   a new provider label with a stored session id, clears the id and arms the
   replay. Every other key passes straight through.

The throttle notice in the chat says what happened ("… is throttled on kiro —
moving this chat to kas with the same model …"). Limits: at most
`max_per_turn` moves per user turn (the default 2 walks a three-backend pool
before handing over to whatever model chain is configured); a backend named in
`exclude` is never a target; a pinned agent (Spec, Plan, …) is never moved;
only dashboard chats (the unattended ladders have no session to reset); a
session already on a fallback model is left to the model chain. The move costs
one cold start (measured 8–13 s typically, with outliers to 50 s) plus the
replay prompt, re-billed on every move, and the harness's own tool results and
compaction state are not carried. With "retry forever" the same-model budget is
never spent, so the failover never fires — the page says so.

Needs: **Agent Backend Router ≥ 0.3.2** (its public `failover_candidates` /
`request_route`; without it the mechanism stays off and the page reports
"not active") and **Agent Backend Doctor ≥ 0.2.1** (a hard dependency: its
`backend_ready` is what keeps a throttled chat from being moved onto a backend
that will not start). A pending move that is never dequeued (Stop during the
backoff, a nested turn) is cancelled with its route request; the mod's
`failover` route and page section show pending moves, the last outcome, and
a preview of where the host's default model would go.

## Configuration

Edited from the page; stored in the mod's `config.json`:

```json
{
  "enabled": true,
  "retries":  {"mode": "host | count | infinite", "count": 3},
  "delay":    {"mode": "host | fixed | exponential", "interval_s": 30, "base_s": 2, "factor": 2, "max_s": 300, "jitter": 0.25},
  "fallback": {"enabled": false, "profile": "<id>",
               "profiles": {"<id>": {"name": "Night shift", "primary_retries": 3,
                                     "steps": [{"model": "claude-sonnet-5", "retries": 2}, {"model": "auto", "retries": 2}],
                                     "exhausted": "stop | retry-last"}}},
  "backend_failover": {"enabled": false, "max_per_turn": 2, "cooldown_s": 120, "exclude": ["codex"]}
}
```

Model ids are the ones the dashboard's model picker shows; `auto` lets the
backend choose. A candidate the session does not advertise is skipped, as in
KiroCrew's own chain walk.

## Development

```bash
python -m pytest tests/            # the policy brain, the hooks against a fake host through FloofyCrew's real registry, and the page's save merge (under node)
python tests/page_probe.py         # headless Chromium: the page against a stateful fake api — pick, toggle, count, profile, reload all stick
floofy validate .                  # schema, hashes, parts, targets, network scan
python tests/smoke_test.py --refresh   # rewrite files[] after editing
```

Unofficial; nothing here is part of KiroCrew. MIT.
