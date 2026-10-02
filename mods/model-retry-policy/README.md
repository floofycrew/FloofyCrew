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

It applies to every surface that retries: dashboard chats, cron and Slack turns
(`stream_and_collect`), and sub-agents. The page shows the sequence of attempts
your settings produce before you save them, and a live log of the retries,
model switches and exhaustions that actually happened.

Designed to run alongside **Agent Backend Router**: that mod picks which agent
backend a *session* runs on; this one governs what a *turn* does when its
model fails. Their hooks touch different host seams. Note that each retry is a
new turn attempt, so the router's telemetry counts every failed attempt as an
error sample (its own fallback strategy trips on those).

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
  (the Loader deactivates a faulting mod, so a fault restores it too).

Every hook calls straight through when you have not opted in, so the default
is KiroCrew's behaviour byte-for-byte. Retries only ever happen when zero
output has streamed (the host's own guard), and Stop ends any retry loop —
"forever" means until the turn succeeds, you stop it, the session is reset or
the gateway restarts. The host's start-of-turn restore probe still moves a
session back to its primary model once that model recovers.

Not governed: the Slack cron's outer two-retry wrapper around a whole job
(it recurses per attempt and wraps the governed turn), and Discord's own
counter.

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
                                     "exhausted": "stop | retry-last"}}}
}
```

Model ids are the ones the dashboard's model picker shows; `auto` lets the
backend choose. A candidate the session does not advertise is skipped, as in
KiroCrew's own chain walk.

## Development

```bash
python -m pytest tests/            # the policy brain + the hooks against a fake host through FloofyCrew's real registry
floofy validate .                  # schema, hashes, parts, targets, network scan
python tests/smoke_test.py --refresh   # rewrite files[] after editing
```

Unofficial; nothing here is part of KiroCrew. MIT.
