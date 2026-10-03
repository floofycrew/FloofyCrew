# mods/

First-party FloofyCrew **mods**. Each directory is a complete mod (a `floofy.json`
manifest at its root plus the files it ships) that installs through the manager
like any third-party mod; nothing here is part of the framework core, the Loader
app or the SPA host, and the core never depends on anything in this directory.
They are the reference mods the tests, the docs and the Forge smoke stage use.

| Mod | What it is |
|---|---|
| [`custom-themes/`](custom-themes/) | a theme editor for KiroCrew inside the FloofyCrew App, and the layer theme mods build on: a `ui` editor (palette / look / pack / branding, live preview, install, activate, export, import), a `python-hook` backend (the library, the CSS compiler, presets harvested from the running host's own stylesheet, the first-frame sheet), a `spa` runtime part (applies the active theme's extra CSS the host's allowlist would drop), a `spa` boot part (the first frame, warm and cold clients) and the theme-reset `patch` every custom theme needs |
| [`rimuru-branding/`](rimuru-branding/) | the Rimuru slime theme: a Level-1 theme pack (the first real mod, ported from the standalone O3R theme patcher — task 5.5). Since 2.0.0 a pure `theme` part depending on `custom-themes` |
| [`settings-demo/`](settings-demo/) | the example of a mod with its own settings page inside the FloofyCrew App: a `ui` part persisting a text field and a toggle through `floofy.mod(id).config`, plus a `python-hook` part registering the `echo` route the page calls (Requirement 16.4 — task 11.6; the second mod of the internal registry) |
| [`mochi-pet-zoom-fix/`](mochi-pet-zoom-fix/) | the first desktop-shell mod: two `electron:` patches (`side: electron`) inside `app.asar` that give the Mochi pet overlay its own session partition and pin its zoom to 100 %, so the pet no longer inherits the dashboard's Zoom Level (Requirement 5.11; the third mod of the internal registry) |
| [`display-comfort/`](display-comfort/) | persistent chat text size and an arbitrary page zoom: a `ui` settings page (two sliders with live preview, a composer toggle, reset) persisting through `floofy.mod(id).config`, a `spa` runtime part applying the scale (one custom property on `<html>`, `zoom` on the message bubbles) and the page zoom, and a `spa` boot part with a baked stylesheet so the cached sizes paint on the first frame |
| [`os-notify-bridge/`](os-notify-bridge/) | mirrors the dashboard's in-app notifications to native OS banners: a `spa` runtime part listening to the host's own `mc-notification` fan-out (channel mutes keep their meaning), enriched from the feed API, and a `ui` settings page with the permission surface, a background-only policy, per-kind switches and the host's hidden turn-complete flag surfaced as a switch |
| [`agent-backend-router/`](agent-backend-router/) | routes each new chat session across KiroCrew's agent backends (kiro-cli v2, KAS, Claude Code, Codex, …) instead of one static `agent.acp_backend`, to soften throttling and spread credit use: a `python-hook` that `replace`s the one construction call where a session's backend is fixed and measures every turn (TTFT, TPOT, latency, throttle/error class); four strategies — round robin, adaptive (window medians as ratios to the best, weighted, × a throttle penalty, with an explore share), fallback (one active backend, trips on transient errors or error rate, wraps) and random; the session's model is never changed — a backend is eligible only when its own served list carries the same model (Claude models → kiro/KAS/Claude Code, GPT models → kiro/KAS/Codex) and receives that list's spelling of it; Kiro's Spec / Quick Spec / Bug Fix / Plan agents pinned to KAS; a `ui` page with strategy cards, pool, per-backend namespace/served lists, an eligibility checker, pins, tuning and inline-SVG dashboards of the recorded metrics |
| [`model-retry-policy/`](model-retry-policy/) | chooses what KiroCrew does when the model or its API fails transiently (throttling, overload, 5xx, dropped stream) instead of the fixed three same-model retries: a `python-hook` that `replace`s the shared retry seams (`transient_retry_delay`, `configured_fallback_chain`, the per-candidate budget and the dashboard's swap/rewind) at every surface — dashboard chats, cron/Slack turns, sub-agents — and overrides the `TRANSIENT_RETRIES` budget with save/restore; four mechanisms — a retry count, retry forever at a fixed interval, retry forever with exponential backoff, and fallback profiles (primary N times, then each model in the profile for its own count, then stop or stay on the last forever); the default leaves the host byte-for-byte; a `ui` page with mechanism cards, parameters, a profiles editor, a "what will happen" attempt timeline and a live retry/switch log. Designed to run alongside `agent-backend-router` (different seams) |
| [`kiro-spec-plan/`](kiro-spec-plan/) | Kiro's own Spec, Quick Spec, Bug Fix and Plan modes as KiroCrew agents: four `agent` drop-ins named after the kiro-cli v3 (KAS) bundled mode ids, so picking one selects Kiro's real workflow with nothing copied out of Kiro; a read-only `python-hook` publishing the `agent.acp_backend` verdict as exports (doctor warning when KAS is off) and a `spa` part badging the four rows in the agent selector |

Keep a mod honest with `python scripts/hash_example_files.py mods/<id>` (the
`files[]` hashes) and `python -m floofy_core.cli_validate mods/<id>`
(schema, hashes, targets, network scan). A theme mod that still bakes its own
first-frame CSS into a boot part regenerates it with
`python scripts/bake_theme_css.py mods/<id>`; the first-party theme relies on
`custom-themes` instead.


Want to build one of your own? You do not need to read the API docs first:
if your edition ships a contributor assistant (the internal edition does —
see its contributing guide under `editions/`), start there; otherwise
[docs/publishing.md](../docs/publishing.md) and the author pages in
[docs/README.md](../docs/README.md) are the by-hand path, with these mods as
the working examples.
