# Seams: where each kind of change lands, and why

A *seam* is an extension point the host exposes on purpose. FloofyCrew's rule is
the one every durable modding scene arrived at: **hooks over patches, patches
over rebuilds**. Each part kind lands through the cleanest seam that reaches its
effect, so most mods never touch a host file, and FloofyCrew never ships a
rebuilt host (design decision DR-1: mod the distributed build).

## The attachment ladder (design decision DR-2)

Cleanest first. Prefer the lowest rung that does the job.

| Rung | Mechanism | Survives host updates? | Used by |
|---|---|---|---|
| 1 | **Data drop-ins** in the host data home: `themes/`, `skills/`, `appearance-library/`, the agents directory | yes — official, untouched by updates | `theme`, `skill`, `appearance`, `agent`, `config` |
| 2 | **App Kit**: an installed app under `<host home>/apps/<name>/` runs third-party Python in the gateway process (`backend.hooks`) and third-party ES modules in the dashboard document (`ui.entry`), audited by the host | yes | the FloofyCrew Loader itself, `app` parts, `python-hook` and runtime `spa` parts (through the Loader), `ui` parts (a mod's own page, mounted by the FloofyCrew App with `floofy.mod(id)`) |
| 3 | **Early shim**: a `.pth` line in the host interpreter's site directory (user site on the bundled interpreter, venv site on venv installs) | yes — outside the host package | `python-hook` parts with `"early": true` |
| 4 | **`index.html` injection**: the boot script and baked CSS for first-frame effects, the SPA host's loader tag, import-map keys | re-applied by the Patcher after every update | `spa` parts with `activation: "boot"`, module patches |
| 5 | **Hashed-chunk edits** with alias-graph cache busting | re-applied, fingerprint-gated, reporter-tested | `patch` parts no other rung reaches |
| — | **Never, by FloofyCrew itself**: the host's plugin entry point, in-place edits of host Python, the launcher, the install bookkeeping files, the host's governance files | — | — |

The last row is engineering discipline, not governance: the Loader's hooks give
the same power reversibly. A *mod* that targets governance files is allowed but
flagged governance-altering and needs a per-file typed confirmation
([consent-and-trust.md](consent-and-trust.md)).

## When to use which kind

- **Colours, logo, favicon, bot name** → `theme` (rung 1). The pack goes through
  the host's own theme validator; the host renders branding natively since 0.7.0.
  Add a `spa` part with `activation: "boot"` (rung 4) only when the first frame
  matters — a theme pack applies after hydration and flashes once on a cold
  client; the boot part paints before React mounts.
- **A new agent, skill or appearance pack** → `agent`, `skill`, `appearance`
  (rung 1): drop the artifact in, nothing else.
- **A host setting** → `config`: declared key/value pairs applied with the
  host's `config set` semantics; the previous values are recorded so uninstall
  restores them.
- **A dashboard page, panel or backend route of your own** → `app` (rung 2):
  the host's App Kit is the sanctioned channel for third-party code, and the
  manager offers — with your confirmation — the per-app `agent.apps_trusted`
  grant the host needs to run it.
- **Changing what the gateway does** → `python-hook` (rung 2): `activate(ctx)` /
  `deactivate(ctx)` inside the gateway, with `ctx.hooks` wrapping host functions
  by import path or gateway routes by method and path, fail-open per mod
  ([python-api.md](python-api.md)). Add `"early": true` (rung 3) only for code
  that must run before the host's platform bootstrap.
- **Changing what the dashboard does at runtime** → `spa` (rung 2): an ES module
  loaded by the SPA host after the host bundle, inside an error boundary, with
  `window.floofy` for host facts, surfaces and events ([spa-api.md](spa-api.md)).
- **Changing minified host code that no hook reaches** → `patch` (rungs 4–5):
  a descriptor with content fingerprints and host-version gates, applied by the
  Patcher with backups and a deployment manifest, reported by the reporter on
  every new host version ([patching.md](patching.md)). Use it last, and say why
  in the descriptor's `description`.

The manager shows, for every part, which seam it used, whether it modified any
payload file, and any governance warning attached to it (`floofy status`, the
manager page).

## Why FloofyCrew never uses the host's plugin entry point

The host has a Python entry-point seam (`kirocrew.plugins`) that looks like the
obvious place for a mod loader. FloofyCrew does not register one, ever:

- it admits **exactly one occupant** — a second entry point aborts the gateway
  boot, so a loader living there would fight any other extension using it;
- its presence **flips the host's profile** (the installed edition reports
  itself as the enterprise profile), which changes host behaviour far beyond
  what a mod loader should touch;
- the App Kit reaches the same process (`backend.hooks.on_startup` runs inside
  the gateway) through a seam designed for third parties, audited by the host,
  with the admission verdict shown to the user.

So the Loader is a KiroCrew App installed under `<host home>/apps/floofycrew/`
(`kirocrew app install`), and mods reach the gateway through the Loader's own
hook registry. Should the entry-point seam ever admit several occupants, the
ladder is revisited; until then it stays untouched.

## Reversibility on every rung

Rungs 1–3 are removed by deleting what was added (`floofy uninstall`, `floofy
deinit`). Rungs 4–5 keep a deployment manifest per payload and a `.floofybak`
sidecar beside every touched file; `floofy restore --all` returns every payload
to vanilla by manifest and by sweep, and works even when the manifest is lost
([updating.md](updating.md)).

---
Covers Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 3.1, 3.2.
