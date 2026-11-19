# mods/

First-party FloofyCrew **mods**. Each directory is a complete mod (a `floofy.json`
manifest at its root plus the files it ships) that installs through the manager
like any third-party mod; nothing here is part of the framework core, the Loader
app or the SPA host, and the core never depends on anything in this directory.
They are the reference mods the tests, the docs and the Forge smoke stage use.

| Mod | What it is |
|---|---|
| [`custom-themes/`](custom-themes/) | a theme editor for KiroCrew inside the FloofyCrew App, and the layer theme mods build on: a `ui` editor (palette / look / pack / branding, live preview, install, activate, export, import), a `python-hook` backend (the library, the CSS compiler, presets harvested from the running host's own stylesheet, the first-frame sheet), a `spa` runtime part (applies the active theme's extra CSS the host's allowlist would drop), a `spa` boot part (the first frame, warm and cold clients) and the theme-reset `patch` every custom theme needs |
| [`settings-demo/`](settings-demo/) | the example of a mod with its own settings page inside the FloofyCrew App: a `ui` part persisting a text field and a toggle through `floofy.mod(id).config`, plus a `python-hook` part registering the `echo` route the page calls (Requirement 16.4 — task 11.6; the second mod of the internal registry) |
| [`display-comfort/`](display-comfort/) | persistent chat text size and an arbitrary page zoom: a `ui` settings page (two sliders with live preview, a composer toggle, reset) persisting through `floofy.mod(id).config`, a `spa` runtime part applying the scale (one custom property on `<html>`, `zoom` on the message bubbles) and the page zoom, and a `spa` boot part with a baked stylesheet so the cached sizes paint on the first frame |
| [`os-notify-bridge/`](os-notify-bridge/) | mirrors the dashboard's in-app notifications to native OS banners: a `spa` runtime part listening to the host's own `mc-notification` fan-out (channel mutes keep their meaning), enriched from the feed API, and a `ui` settings page with the permission surface, a background-only policy, per-kind switches and the host's hidden turn-complete flag surfaced as a switch |
| [`agent-backend-doctor/`](agent-backend-doctor/) | finds, un-caches and repairs KiroCrew's agent-backend harnesses (Claude Code, Codex, OpenCode, pi): a `python-hook` sitting `after` the host's Node-adapter resolver (`acp.client._resolve_node_adapter_argv`) and the plain-binary ladders, filling a miss from the npm global prefixes the host does not search (`~/.local/node`, `~/.npm-global`, a `~/.npmrc` prefix, `$NPM_CONFIG_PREFIX`) with the host's own completeness rule (`dist/index.js` + `@agentclientprotocol/sdk` beside it); resets the gateway's cached 'not found' (`_UNRESOLVED` sentinel, `agent_sdk.forget_for_recheck` on 0.8) on activation and on demand so a backend installed after the gateway started works without a restart; a `ui` page with one row per backend (host verdict, adapter / binary and the rung it was found at, CLI, gateway cache state, advertised models), an opt-in `CODEX_PATH` switch (run the codex you installed through codex-acp) and an opt-in symlink repair into `~/.local/bin`; since 0.2.0 also keeps a Bedrock-backed Codex's AWS credentials usable inside KiroCrew's sandbox — before each Codex launch (`after` `AcpClient.__init__` and `CodexHarness.apply_spawn_env`) it reads `~/.codex/config.toml` (never `auth.json`), copies only the configured profile's non-sensitive keys and its `source_profile` / `sso_session` chain into a `0700`/`0600` pair outside `~/.aws` (`<host home>/codex-aws/`), hands the child `AWS_CONFIG_FILE` / `AWS_SHARED_CREDENTIALS_FILE` / `AWS_PROFILE` explicitly in place of the broker's sanitized pointers, and offers a sandboxed `aws sts get-caller-identity` probe; `~/.aws` is never exposed, the sandbox never weakened, no static key ever copied; `agent-backend-router` ≥ 0.3.0 depends on it; since 0.2.1 `backend_ready(backend)`, a read-only readiness verdict other mods take before moving a session to a backend (the backend failover of `agent-backend-router` ≥ 0.4.0 depends on it) |
| [`agent-orchestrator/`](agent-orchestrator/) | real parallel multi-agent orchestration with a centralized orchestrator: a `python-hook` that adds a typed, bounded **message bus** between an orchestrator session and the worker sessions it created — hub-and-spoke by default (every worker reports to the orchestrator, which relays; a direct worker → worker post is refused unless the `peer_messaging` setting is on) — six MCP tools on a stdlib stdio server (`bus_roster`, `bus_post` say/ask/answer, `bus_inbox` with a blocking rendezvous, `bus_status` live summary line, `bus_wait` wait-any for the hub, `bus_run`) reached over a 0600 unix socket, identity from the host's own `KIROCREW_SESSION_KEY`, membership derived from `slots[].created_by`; wake-ups that carry no body (mid-turn steer on kiro/KAS, a new turn when idle) with the body read as a provenance-wrapped tool result; two agents rendered into `~/.kiro/agents/` (`agent-orchestrator`, no write/shell tools, dispatches via the host's untouched `session_create` + work ledger; `agent-orchestrator-worker`); an optional non-retryable `max_workers` cap on the host's create route for the mod's own orchestrators only; and a `ui` page with the live hub-and-spoke graph — orchestrator in the centre, one card per worker (state glyph, progress, liveness dot, age, live summary), messages as travelling dots along the edges, a message-bus log and footer. A run lives as long as its orchestrator chat: deleting that chat for good deletes the run's record too (hooks on the host's history delete routes), a closed-not-deleted run is shown archived with a `✕ remove` button, and records age out after `run_retention_days`. Also draws the host's own `kirocrew-conductor` fleets. A scripted four-worker demo with a checker lives in `demo/`; research + design in `research/orchestration-mod/` |

**Deprecated: `model-retry-policy`.** Its retry / fallback policy (retry
count, retry forever at a fixed interval or with backoff, fallback profiles,
the mid-turn recovery budget, the backend failover) was merged into
`agent-backend-router` 0.4.0 as that mod's *fallback* half, so routing and
fallback work together like one load balancer: the routing policy applies
first, and when a turn fails the configured fallback rule applies. The router
imports the old mod's settings on its first activation and stays out of the
retry seams while the old mod is still enabled, so `floofy disable
model-retry-policy` (then `floofy uninstall`) hands the work over without a
change in behaviour. The directory is gone from `mods/` and the mod is removed
from both registries; an installed copy keeps working but receives no updates.
`agent-backend-router` ships on the internal edition today (it still spells an
internal name), so on the public edition the retry policy is unavailable until
the router's move to this repository.


Keep a mod honest with `python scripts/hash_example_files.py mods/<id>` (the
`files[]` hashes) and `python -m floofy_core.cli_validate mods/<id>`
(schema, hashes, targets, network scan). A theme mod that still bakes its own
first-frame CSS into a boot part regenerates it with
`python scripts/bake_theme_css.py mods/<id>`; the first-party theme relies on
`custom-themes` instead.


Want to build one of your own? You do not need to read the API docs first:
the `floofy-mod-dev` agent in this checkout (`scripts/kiro-workspace-sync.sh`,
then `kiro-cli chat` and `/agent → floofy-mod-dev`; the *Mods* section of
[CONTRIBUTING.md](../CONTRIBUTING.md)) scaffolds, develops and publishes the mod
with you; [docs/publishing.md](../docs/publishing.md) and the author pages in
[docs/README.md](../docs/README.md) are the by-hand path, with these mods as
the working examples.
