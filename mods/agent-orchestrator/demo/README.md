# Demo — four workers, one orchestrator, hub-and-spoke

A scripted run that exercises every feature of the mod in about 5–10 minutes
and leaves evidence you can check: dispatch of four parallel worker sessions,
live `bus_status` lines on the graph, two questions that **must** go through
the orchestrator (workers cannot address each other), relayed answers with
`reply_to`, `work_report` rows, a `session_send` follow-up, verdicts, and a
closed run — all visible on the *Agent Orchestrator* page while it happens.

## What the workers build

A tiny static "crew status" page in `~/floofy-demo/status-page`: `index.html`
(worker-1), `styles.css` (worker-2), `app.js` (worker-3), `facts.json` +
`check.sh` (worker-4). The dependency is deliberate: worker-1 *chooses* the id
of the list element and the brief does not say it, so worker-3 and worker-4
have to ask — and the only member they may ask is the orchestrator, which asks
worker-1 and forwards the answer. That is the hub-and-spoke relay on the graph.

## Before you start

1. The mod is installed and enabled (`floofy list` shows `agent-orchestrator`
   enabled; the sidebar has FloofyCrew → Mods → *Agent Orchestrator*).
2. `~/.kiro/agents/agent-orchestrator.json` and
   `agent-orchestrator-worker.json` exist (the mod renders them on activation).
3. Decide on approvals. The workers write files and run `check.sh`; their
   specs do **not** pre-grant `fs_write`/`execute_bash`, so each write would be
   a permission prompt in the worker's tab. For an unattended demo switch the
   orchestrator tab to **Trust** before sending the goal: a session created by
   `session_create` inherits its creator's trust posture (`_trust`), so the
   four workers auto-approve too. Leave it off if you want to click through
   the prompts yourself.

## Run it

1. Dashboard → new chat → agent **agent-orchestrator** (title it
   `demo: status page`). Turn on Trust (see above).
2. Paste the contents of `demo/prompt.txt` as the first message.
3. Open **FloofyCrew → Mods → Agent Orchestrator** in another tab (or the
   split view) and watch.

## What you should see, in order

| Phase | On the graph page | On the bus log |
|---|---|---|
| dispatch (~1 min) | the orchestrator card gets the goal line `status page · 4 workers · hub-and-spoke`; four worker cards appear on the ring, each with its `worker-N: …` title and a running dot | four `send` rows (the seed prompts) orchestrator → worker-N |
| work (~2–5 min) | every card's summary line and progress bar move as the workers call `bus_status` | `status` lines are not rows; the cards are the live view |
| the relay | a dot travels worker-3 → orchestrator (`ask`), then orchestrator → worker-1 (`ask`), worker-1 → orchestrator (`answer`), orchestrator → worker-3 (`answer`); the same for worker-4. There is never a chord between two workers | `worker-3 → orchestrator ask …`, `orchestrator → worker-1 ask …`, `worker-1 → orchestrator answer …`, `orchestrator → worker-3 answer …` |
| reports | cards flip to `✓ 100` as each `work_report done` lands | one `report` row per worker |
| the follow-up | a `send` dot orchestrator → worker-4, worker-4 runs `check.sh`, reports again | `send` + `report` |
| close | the footer reads `4 agents · status page … · N messages on the bus`; the live badge flips to `✓ closed` | — |

Shift-click a card to filter the log to that worker. Click a card to jump to
its tab and read the full exchange.

If a worker tries `bus_post to=worker-1` anyway, the bus refuses it with the
non-retryable `peer_messaging_off` and the worker re-posts to the orchestrator
— you will see the refusal in that worker's tab, never on the graph (a refused
post leaves no row).

## Verify

On the host that ran the demo:

```sh
python3 demo/check.py                 # site + bus log of the newest run
python3 demo/check.py --bus-only      # just the hub-and-spoke invariants
```

It prints one `PASS`/`FAIL` per check and ends with a summary such as
`4 agents · 23 messages on the bus · 2 asks up, 2 relays down, 2 answers forwarded`.
The checks that prove the topology: *ZERO worker → worker rows*, *orchestrator
relayed a question*, *answer forwarded with reply_to = the original ask*.
`--data <dir>` points it at the mod's `.floofy` dir if it is not under
`~/.kiro/crew/*/mods/agent-orchestrator/`.

## Reset for another run

```sh
rm -rf ~/floofy-demo/status-page
```

Close the five tabs (or leave them; a new goal in a new orchestrator tab is a
new run). Run `check.py` **before** you delete the orchestrator chat: deleting
it for good deletes the run's bus log too (the record the checker reads), so the
page does not pile up dead runs. Closing the tabs keeps the record; the page then
shows the run as `○ archived (chats gone)` with a `✕ remove` button, and any
record ages out after `run_retention_days`.

## Variations

- `"peer_messaging": true` in the mod's `.floofy/config.json` (under the data
  home, `…/mods/agent-orchestrator/.floofy/config.json`; read live, no reload)
  and the same prompt minus the "workers never address each other" rule:
  worker-3 asks worker-1 directly and a purple chord appears between the two
  cards.
- Set `max_workers` to 3 and watch the fourth `session_create` come back
  `orchestrator_cap_reached`; the orchestrator must reuse an idle worker.
- Stop one worker tab mid-run: the card goes `gone`, `bus_wait` returns it,
  the orchestrator re-briefs or recreates.
