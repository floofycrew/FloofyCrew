# spa-host

The dashboard-side runtime: hand-written ES modules (ES2022, strict mode, no
build step, no npm dependencies) served from the Loader app's `ui/` as
`/apps/floofycrew/ui/<file>`. `scripts/build_loader_app.py` copies `src/*` into
the installable app's `ui/` next to the manager entry `loader-app/ui/index.mjs`;
the app archive **is** the source. Same-origin only and no CSP changes
(Requirement 4.6, spike 1.4): nothing is fetched or imported from another
origin, and this directory never references the internal edition — CI scans it
for internal identifiers.

## How the host gets into the document (spike 1.3)

An App's `ui.entry` loads only when its page is opened, so the always-on
vehicle is a one-line loader tag the Patcher injects into each payload's
`index.html` before the host's `main-*.js` module tag (task 5.4):

```html
<script type="module" src="/apps/floofycrew/ui/host.mjs" id="floofy-host"></script>
```

The manager page (`ui/index.mjs`) imports `./host.mjs` too. Both resolve to the
same module URL, so the host runs once per document (`ensureStarted()` is
idempotent; `window.floofy` is installed once, non-writable).

## Modules (`src/`)

| File | Purpose |
|---|---|
| `host.mjs` | the always-on runtime: reads `/api/apps/floofycrew/state` (same origin, dashboard cookie; on the cold `?token=` path it polls `/health` with backoff for ~10 s, then gives up quietly), builds the frozen `window.floofy`, `import()`s every **active** mod's runtime `spa` parts from `/api/apps/floofycrew/spa/<id>/<file>` in Loader order inside an error boundary, wraps `history.pushState/replaceState` + `popstate` into `route.changed` |
| `boundary.mjs` | the error boundary: `run()` (try/catch around import and `activate`), scoped `error`/`unhandledrejection` listeners attributing later errors by the `/api/apps/floofycrew/spa/<id>/` URL prefix in the stack, one fault report per mod |
| `events.mjs` | the fail-open bus (`on/once/off/emit`, `*` wildcard, per-owner drop, bounded history); `scoped(bus, modId)` for a mod's `ctx.events` |
| `surfaces.mjs` | `window.floofy.surfaces` — content-fingerprinted stable names for host UI surfaces (task 5.2 fills the registry and the resolution) |
| `patches.mjs` | `window.floofy.patches` — inspection of the import-map remaps the Patcher applied (task 5.3) |
| `reporter.mjs` | `window.floofy.report()` — which fingerprints still match (task 5.2) |

## `window.floofy`

Frozen. `version` (SPA host), `unofficial: true`, `host` (`version`, `edition`,
`channel`, `build_version`, `base_version`, `profile`), `api_version`,
`framework_version` (the Loader release), `events` (the bus), `surfaces`,
`patches`, `report()`, `mods` (read-only summaries: id, version, name, active,
enabled, reason, parts with `activation`, governance warnings), `loaded()`,
`faulted()`, `state()` (last Loader state), `refresh()`.

Events the host emits: `host.ready`, `mod.loaded`, `mod.faulted`,
`route.changed`, `surfaces.changed`.

## What a `spa` part gets

A runtime `spa` part is an ES module. The host calls `export default (ctx)` or
`export function activate(ctx)` (sync or async) and, when the mod faults later,
`export function deactivate(ctx)`. `ctx` is frozen: `modId`, `version`, `host`,
`api_version`, `baseUrl` (`/api/apps/floofycrew/spa/<id>/`, for the mod's own
files), `events` (scoped: subscriptions are dropped when the mod faults),
`log` (`console` prefixed `[floofy:<id>]`), `surfaces`, `config.read()` (the
mod's published `exports` from the Loader state).

Failure model (Requirement 4.1): an exception during import or `activate`, or a
later uncaught error / unhandled rejection whose stack points into the mod's
served files, disables that mod — `deactivate()` is attempted, its bus
subscriptions are dropped, `mod.faulted` is emitted and
`POST /api/apps/floofycrew/mods/<id>/fault` `{message, stack, source: "spa"}`
tells the Loader (which records reason `Error` and stops serving the mod's
files). Other mods and the host UI are unaffected; nothing rethrows into the host.

Parts with `activation: "boot"` are not loaded here: the Patcher inlines them
into `index.html` for first-frame effects (task 5.4).

## Tests

`node --test spa-host/tests` (Node 20+, fakes for `fetch`, `import()`, `window`)
— also run by `python -m pytest` through `tests/test_node_suite.py`, which
additionally asserts every module is a plain same-origin ES module.
