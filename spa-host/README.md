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
| `surfaces.mjs` + `surfaces.json` | `window.floofy.surfaces` — stable names for host UI surfaces located by **content fingerprints** (below) |
| `patches.mjs` | `window.floofy.patches` — inspection of the import-map remaps the Patcher applied (below) |
| `reporter.mjs` | `window.floofy.report()` — every surface and patch fingerprint evaluated against the live document and bundle (below) |
| `boot.mjs` | the pre-hydration boot script TEMPLATE the Patcher renders into `index.html` for `activation: boot` parts (below) |

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
into `index.html` for first-frame effects (below).

## Boot activation (Requirement 4.3)

`boot.mjs` is a TEMPLATE: `floofy_core.boot_script` copies the `floofyBoot`
function between its marker comments into ONE inline classic script
`<script id="floofy-boot">…floofyBoot({defaults, themeBootstrap, mods:[…]})</script>`
inserted before the **first** `<script` of every payload's `index.html`
(`occurrence: first`) so it runs before anything can paint. Generalised from
the O3R theme patcher's boot block:

1. theme bootstrap — the host's own formula for `data-theme` / `data-mode` /
   `data-mode-pref` from `localStorage` (`mc-color-theme`, `mc-theme`), with the
   host config's `dashboard.theme_color` / `theme_mode` captured at patch time
   as the fallback, so a cold client (empty storage, pre-auth page) still paints
   the configured theme;
2. per boot mod, in load order, gated by `when.theme` when declared: `attributes`
   on `<html>`, a `<link rel=icon id="floofy-boot-favicon-<id>">`, the host's
   `--theme-logo` custom property, `document.title`, the mod's own hook (its
   `spa` part file, inlined as `function (ctx)`, ES5 only);
3. drift guard — `MutationObserver`s re-assert the attributes until the host
   settles (its `<style id="mc-custom-theme-…">` or `<link id="mc-theme-favicon">`
   lands, or 5 s pass), then remove the boot favicon links so the host's
   authoritative icon is the only one. `window.__floofyBoot` records
   `{color, mode, pref, applied[], skipped[], settled, errors[]}`.

The baked CSS is separate: one `<style id="floofy-boot-css-<id>">` per mod
appended just before `</head>`, after the host's stylesheets (source order wins
on equal specificity). Favicon/logo files are copied under
`/apps/floofycrew/ui/boot/<id>/` (unauthenticated app-UI route). The same
generated descriptor carries the spike-1.3 loader tag, so both land in one
`index.html` op set; the Loader adds it whenever any active mod has a `spa`
part (`floofy_loader.pending.plan_boot`), the CLI with
`floofy-patch apply --boot-mod <dir> [--loader-tag]`. Everything is reverted by
`restore` (backups, manifest, `ui/boot/` sweep).

## Surfaces (Requirement 4.4)

`surfaces.json` is the registry: one row per stable name, written against a
host build (`fromBuild`), with up to two fingerprints — never a chunk hash,
never a line number:

* `dom` — a CSS selector over attributes the host renders literally and that
  survive minification (`data-testid`, ids, ARIA roles/labels, literal class
  names such as `topbar`, `rel`/`type`), optionally `text` (required substring),
  `count` (`some` | `once`) and `route` (a path prefix: off that route the DOM
  check is *not applicable*, not a miss). Resolved to live nodes, re-resolved
  on `route.changed` and on DOM mutation (debounced `MutationObserver` on
  `document.body`); `surfaces.changed` fires when a node identity or a match
  status changed.
* `bundle` — `{chunk, contains | regex, count}`: a literal or regex expected in
  the module text of the hashed chunk named by its **stem** (`App`, `main`,
  `useTheme`). The file name is discovered from the never-cached shell
  (`<script type=module src>`, `<link rel=modulepreload>`) or, for a lazy
  chunk, from a `./<stem>-<hash>.js` reference inside an already-known chunk;
  the text is fetched same-origin from `/assets/*` and cached per document.

API: `names()`, `definitions()`, `get(name)` → live node or `null`,
`resolve(name)` → `{status, node, reason}`, `refresh()`, `check()` (async, the
reporter's rows), `chunks()` (stem → file), `start()` / `stop()`.

The shipped set for 0.7.0.5 (13 surfaces, 12 with a bundle fingerprint):
`shell.root`, `dashboard.shell`, `topbar`, `sidebar.nav`, `logo`, `favicon`,
`theme.tokens`, `theme.custom-style`, `theme.reset-site`, `theme.decor-slot`,
`page.main`, `apps.page` (route `/apps`), `settings.page` (route `/settings`).
Measured on the scratch 0.7.0.5 gateway: 21/21 fingerprints matched on
`/chat/new-session`, the two route-scoped DOM checks reported not applicable.

## Reporter (Requirement 4.5)

`window.floofy.report({extraSurfaces?})` → `{hostVersion, route, matched[],
missed[{name, kind, reason, chunk?}], notApplicable[], total, spaFingerprints:
{matched, total, missed[]}, chunks, surfaces[], loaderVersion, generatedAt}`.
Names are `<surface>#dom`, `<surface>#bundle` and `patch:<name>`;
`spaFingerprints` is exactly the block the compatibility matrix records
(Requirement 9.1). `extraSurfaces` adds ad-hoc definitions for one run.

From Python: `floofy_core.spa_report.run_browser_report(url, token)` opens the
dashboard headlessly (Playwright), authenticates with the `?token=` link, adds
the host module tag itself when `index.html` carries none (`hostTag:
"injected"`), calls `report()` and returns the same shape;
`check_bundle_fingerprints(dist_dir)` evaluates the `bundle` rows against a
`static/dist` on disk with no browser. CLI: `python -m floofy_core.cli_spa
verify --spa --port P --token T [--json]` (exit 1 when anything is missed) and
`… bundle-check --dist DIR`; the `floofy verify --spa` front end (task 6.x) wires
the same `main()`. The Forge runs both on every new host version (8.4).

## Patches (Requirement 4.4, spike 1.4)

`window.floofy.patches` is an **inspection** API: `list()` (the remaps the
Patcher recorded in `ui/patched/index.json`, joined with the document's live
import map), `isApplied(chunk | mod | "mod#part")` (the import map maps the
chunk to the patched copy and the served copy carries the Patcher's marker
comment) and `report()` (every recorded op's fingerprint re-evaluated against
the live ORIGINAL chunk at `/assets/<chunk>`, plus the import-map key and the
served copy; folded into `window.floofy.report()` as `patch:<mod#part>#…`).

Nothing is rewritten in the browser: native ES modules have no runtime module
registry to hook and the CSP blocks Blob/data module imports, so a `patch`
descriptor with `mode: "import-map"` is applied by the Patcher — patched copy
with rebased imports under `/apps/floofycrew/ui/patched/<chunk>`, one
import-map key in `index.html`, `modulepreload` hint dropped
(`floofy_core.spa_patches`). Regex fingerprints are authored for the Python
Patcher; one the browser cannot compile is reported as a miss with that reason.

## Tests

`node --test spa-host/tests` (Node 20+, fakes for `fetch`, `import()`, `window`;
33 tests) — also run by `python -m pytest` through `tests/test_node_suite.py`,
which additionally asserts every module is a plain same-origin ES module.

`tests/test_playwright.py` (Requirement 13.3, task 5.6) drives a real dashboard:
one scratch gateway on the payload copy under `.scratch/` plus one headless
Chromium shared by four tests — no first-frame flash with the Rimuru boot part
(`data-theme` and the computed `--bg` already the mod's at `DOMContentLoaded`,
React not mounted; a screenshot pair under `.scratch/loader-tests/playwright/`),
boundary isolation (a mod failing on import, one throwing in `activate`, one
healthy: the healthy one runs, the shell renders, two fault reports reach the
Loader and its files stop being served), the reporter on impossible DOM and
bundle fingerprints (`missed` with reasons; `floofy verify --spa` exits 1 with
JSON against the same gateway, 0 without the extras), and the spike-1.4 remap (a
`mode: import-map` patch's marker is evaluated by the host's own importers).
About a minute in total; the copy's `index.html` is restored byte-identically at
teardown. Skipped without a payload copy.
