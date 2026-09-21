# The SPA API (dashboard side)

A `spa` part is an ES module. With the default `activation: "runtime"` the
FloofyCrew SPA host imports it into the dashboard document **after** the host
bundle, inside an error boundary, and calls its default export (or `activate`):

```js
// spa/main.js
export default function activate(ctx) {
  ctx.log.info("hello from", ctx.modId, "on", ctx.host.version);
  const nav = ctx.surfaces.get("sidebar.nav");          // a live node or null
  ctx.events.subscribe("surfaces.changed", () => { /* re-render */ });
  return () => { /* optional cleanup */ };
}
export function deactivate(ctx) {}
```

The SPA host is served from the Loader app's own `ui/`
(`/apps/floofycrew/ui/host.mjs`), started by the one-line loader tag the Patcher
injects into `index.html` or by the manager page, and runs once per document. It
reads the Loader state from `/api/apps/floofycrew/state`, builds `window.floofy`,
then `import()`s every **active** mod's runtime `spa` files from
`/api/apps/floofycrew/spa/<id>/`. A failing module is reported to
`POST /mods/<id>/fault`, disabled with reason `Error`, and never affects other
mods or the host UI. Everything is same-origin: nothing is fetched or imported
from another origin, ever.

## `window.floofy`

Frozen after the first state read.

| Member | What |
|---|---|
| `version` | the SPA host's own version |
| `unofficial` | `true` |
| `host` | `{version, edition, channel, …}` — the Loader's host facts |
| `api_version` | the FloofyCrew API version (the same string as `floofy.api_version` on the Python side) |
| `events` | the document-wide event bus (`subscribe`, `publish`) |
| `surfaces` | stable names for host UI surfaces (below) |
| `patches` | inspection of the module patches in force (below) |
| `report(options)` | the reporter (below) |
| `mods` | a summary of every mod: state, typed reason, parts |
| `mod(id)` | the per-mod API a `ui` part's page receives (below) |
| `state()` | the latest Loader state document |

Each mod receives its own frozen `ctx`: `modId`, `version`, `host`,
`api_version`, `baseUrl` (its served `spa/` directory), `events` (scoped: names
published by the mod are prefixed for attribution), `log`, `surfaces`, and
`config.read()` — the `exports` its Python side published through `ctx.state`,
so a mod's two halves share facts without a side channel.

## `floofy.mod(id)` — the API of a mod's own page

A mod's `ui` part (`kind: ui`, [manifest.md](manifest.md)) is an ES module the
FloofyCrew App imports same-origin and mounts with `mount(container, api)`;
`api` is `window.floofy.mod(id)`, frozen, and every call is same-origin behind
the dashboard auth (Requirement 16.4, 4.6):

| Member | What | Backed by |
|---|---|---|
| `id`, `baseUrl`, `unofficial` | the mod id; `/api/apps/floofycrew/ui/mods/<id>/` for the page's own assets | — |
| `config.get()` → object | the mod's settings — schema-free JSON | `GET /api/apps/floofycrew/mods/<id>/config` (the mod's `.floofy/config.json`) |
| `config.set(patch)` → object | merge key by key (`null` deletes a key); the whole document must stay under 64 KiB | `PUT …/mods/<id>/config {patch}` — the same store the Python side reads as `ctx.config` |
| `routes.list()` → `[{method, path}]` | what the mod's `python-hook` registered with `ctx.routes.add(method, path, handler)` | the Loader state |
| `routes.fetch(path, init)` → `Response` | `fetch()` scoped to the mod's own routes; `path` is relative (`echo`, `items/3`), never absolute | `…/mods/<id>/api/<path>` |
| `routes.url(path)` | the absolute URL of one of those routes | — |
| `theme.tokens()` → `{"--bg": …}` | the host's CSS custom properties as computed on `:root` (declared names plus the known set) | the document |
| `theme.current()` → `{theme, mode}` | the active colour theme (`localStorage['mc-color-theme']`, else `data-theme`) and `data-mode` | the document |
| `state()` → the mod's Loader verdict | `active`, `reason`, `parts`, `exports`, `tier`, `routes` | `GET /state` |
| `log` | `[floofy:<id>]`-prefixed console logger | — |
| `dialog.confirm(text, opts)` → `Promise<boolean>` | a yes/no question in the App's own overlay (`title`, `okLabel`, `cancelLabel`, `danger`); Enter answers, Escape cancels | the document (api 1.2.0) |
| `dialog.prompt(text, opts)` → `Promise<string\|null>` | one text field (`value`, `placeholder`, `label`, `validate(value)` → an error string disables OK) | the document |
| `dialog.alert(text, opts)` → `Promise<void>` | a notice with one button | the document |
| `dialog.form({title, text, fields, okLabel, cancelLabel, danger})` → `Promise<object\|null>` | several text fields — `fields[{name, label, value, placeholder, validate(value, values), transform(value, values)}]`, `transform` may return `{otherField: value}` to fill another field; the answer is keyed by `name` | the document |

```js
export default async function mount(container, api) {
  const config = await api.config.get();
  const input = document.createElement("input");
  input.value = config.greeting || "";
  input.addEventListener("change", () => api.config.set({ greeting: input.value }));
  container.appendChild(input);
  const reply = await api.routes.fetch("echo", { method: "POST", body: JSON.stringify({ hi: 1 }) });
  console.log(await reply.json());
  return () => { container.textContent = ""; };   // cleanup when the user leaves the page
}
```

A page never calls `window.alert`, `window.confirm` or `window.prompt`: the
dashboard also runs inside the KiroCrew desktop shell, which has none of them (the
call returns at once and the page appears to do nothing). `api.dialog` renders the
same overlay the App uses for its install confirmations
(`data-testid="floofycrew-modal"`, `data-kind="mod-confirm|mod-prompt|mod-alert"`,
`data-mod="<id>"`), one dialog at a time, and works in both.

The page is served only while the mod is active, from manifest-listed files,
and runs inside the App's error boundary: a throw during import or mount, or a
later error whose stack names the mod's URL, is shown with the message and a
*Disable* button; the manager page and the other mods are unaffected.

## Surfaces

Host UI surfaces are located by **content fingerprints**, never by chunk hash
or line number. The shipped registry (`ui/surfaces.json`) has one row per
surface with a `fromBuild` (the host build it was written against) and two
fingerprint kinds:

- `dom` — a CSS selector over attributes the host renders literally and that
  survive minification (`data-testid`, ids, ARIA roles and labels, utility class
  names), optionally with a required text substring and a `route` prefix (off
  that route the surface is "not applicable", not a miss). Resolved to live nodes
  and re-resolved on route change and DOM mutation; `surfaces.changed` fires
  when a node identity or a match status changed.
- `bundle` — a literal or regex expected in the module text of a hashed chunk
  named by its **stem** (`App`, `main`, `useTheme`); the real file name is
  discovered from the shell's `<script src>` and `modulepreload` hints or from a
  `./<stem>-<hash>.js` reference inside an already-fetched chunk. Chunk text is
  fetched same-origin from `/assets/*`.

Shipped names: `shell.root`, `dashboard.shell`, `topbar`, `sidebar.nav`, `logo`,
`favicon`, `theme.tokens`, `theme.custom-style`, `theme.reset-site`,
`theme.decor-slot`, `page.main`, `apps.page`, `settings.page`. API:
`surfaces.names()`, `definitions()`, `get(name)` (a live node or `null`),
`resolve(name)` (`{status, node, reason}`), `chunks()`, `start()` / `stop()`
(the observer). Mods may inject extra definitions through the reporter
(`floofy verify --spa --extra-surfaces`) while authoring their own.

## Patches: inspection only

Module patches are applied at **Patcher time**, not in the browser
([patching.md](patching.md)). The reasons are measured, not assumed (design
spike 1.4, headless Chromium against the served dashboard):

- the served Content-Security-Policy blocks `blob:` and `data:` module imports
  (`script-src-elem` violations), so Vencord-style Blob-module patching is not
  viable;
- native ES modules have no runtime module registry to hook, so a chunk inside
  the host's import graph cannot be swapped after the fact;
- but the host's `index.html` already carries an inline **import map**, and
  adding one key — `"/assets/<chunk>": "/apps/floofycrew/ui/patched/<chunk>"` —
  makes every importer, including dynamic `import()`, evaluate the patched copy
  the Patcher wrote under the Loader app's `ui/patched/`. The chunk's
  `modulepreload` hint is dropped in the same operation.

`window.floofy.patches` therefore reports rather than rewrites: `list()` — the
remaps the Patcher recorded joined with the document's live import map;
`isApplied(name)` — the map points at the patched copy **and** the served copy
carries the Patcher's marker; `report()` — every recorded patch's fingerprints
re-evaluated against the live original chunk text, so the reporter can say which
patch would stop applying on a new host build.

## Boot activation (first-frame effects)

Runtime `spa` parts load after the host bundle — fine for panels and behaviour,
too late for a favicon, a logo or theme tokens on a cold client (one flash). For
those a `spa` part declares `activation: "boot"` with a `boot` object
([manifest.md](manifest.md)); the Patcher bakes one boot script and the parts'
scoped CSS into every payload's `index.html`: the palette, favicon, logo, title
and `data-*` attributes are asserted before React hydrates and re-asserted by a
`MutationObserver` drift guard until the host settles. The boot hook runs with
`{id, color, mode, pref, element}`, must be ES5, and is inlined (at most 32 KB,
no `</script`). The `when.theme` gate keeps a branding mod inert while another
colour theme is selected.

## What the served CSP allows (and what FloofyCrew never asks for)

The host sets its CSP as an HTTP header on every dashboard response. As served,
`script-src 'self' 'unsafe-inline' …` admits same-origin module imports
(`/apps/<name>/ui/…`, `/assets/*`) and runtime-injected inline `<script>` /
`<script type="module">`; `connect-src 'self' …` admits same-origin fetches of
chunk text; `blob:` / `data:` module imports are blocked. Everything the SPA host
does fits inside that policy: FloofyCrew requires **no CSP change** and loads
**nothing from a third-party origin**. The policy's own third-party allowances
(the host's widget CDNs and fonts) are the host's; a `spa` part that reached for
them would be flagged by the manifest network scan like any other remote host.
Files under `/apps/<name>/ui/` are served with `Cache-Control: no-cache`, so a
patched copy or a boot asset is live without a restart.

## Reporter

`window.floofy.report()` evaluates every surface fingerprint and every recorded
patch against the current document and bundle and returns `{matched, missed,
total, …}` per kind. `floofy verify --spa` runs the same reporter in headless
Chromium against the served dashboard (`--bundle` runs the offline fingerprint
check against the payload's files) and prints the matrix shape
`spaFingerprints {matched, total, missed[]}` — the Forge runs it on every new
host version and writes the result into the compatibility matrix
([updating.md](updating.md), [forge-runbook.md](forge-runbook.md)).

## Stability

`window.floofy` follows the same policy as the Python API: mods depend on
`api_version`; renamed members keep working for two FloofyCrew minors with a
console warning naming the mod; surface **names** are stable across host
builds while their fingerprints are maintained per build (`fromBuild`), which
is exactly what the reporter measures.

---
Covers Requirements 2.5, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 14.3.
