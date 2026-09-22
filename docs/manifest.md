# The mod manifest: `floofy.json`

Every mod is a directory (or a `.zip` / `.tar.gz` of one) with a `floofy.json` at
its root. The manifest is what the manager reads to install, order, admit and
quarantine a mod without reading its code. It is validated by `floofy validate`
against the published JSON Schema (`floofy_core/schema/floofy.schema.json`,
draft 2020-12, id `https://floofycrew.dev/schema/floofy-1.json`) and then by
the semantic checks a schema cannot express (ranges, hashes, targets, network).
The exhaustive field-by-field reference is `floofy-core/floofy_core/schema/README.md`;
this page is the author's view. Nine minimal valid mods, one per part kind,
live in `floofy-core/examples/<kind>/`, and `floofy new <kind>` scaffolds one.

## Identity

```json
{
  "schema": 1,
  "id": "rimuru-branding",
  "name": "Rimuru branding",
  "version": "1.0.0",
  "description": "…",
  "authors": ["you"],
  "license": "MIT",
  "links": {"docs": "https://…"},
  "icon": "icon.png",
  "tags": ["theme", "branding"]
}
```

`schema` is the integer `1`. `id` matches `^[a-z][a-z0-9_-]{1,63}$` and is the
key everywhere: dependency maps, the registry, the data home
(`<host home>/floofy/mods/<id>/`). `version` is SemVer 2.0. `name`,
`description`, `authors[]` (at least one) and `license` are required; `links`,
`icon` (a relative path also listed in `files[]`) and `tags` are optional.

## Host compatibility: `kirocrew`

```json
"kirocrew": {"version": ">=0.7.0 <0.9.0", "editions": ["internal", "external"], "channels": ["stable"], "strict": false}
```

`version` is an npm-style range (`>= > < <= =`, hyphen ranges, `^`, `~`, `x`
wildcards, `||`) matched against the host's `X.Y.Z` **base** version: an internal
`0.7.0.5` and a public `0.7.0-insider.3` both compare as `0.7.0`. `editions` and
`channels` narrow further. `strict` decides what an out-of-range host means:
`false` (default) loads the mod with the warning `HostVersionOutOfRange`; `true`
refuses it with the typed reason `Unsupported`.

## Graded dependencies

Five maps of `id → range` (or `id → {"range", "reason"}`):

| Map | Effect |
|---|---|
| `dependsOn` | hard: missing or out of range → reason `Dependency`, propagated to dependents. Must contain `floofycrew` — the **framework** version is the hard dependency, never the host version |
| `recommends`, `suggests` | warnings only |
| `conflicts` | mutual: when both are active the declaring mod is disabled with reason `Conflict` (ties broken by id) |
| `breaks` | one-directional: the declaring mod is disabled, the other stays |

`loadBefore[]` / `loadAfter[]` add ordering edges to the topological sort. A cycle
disables its participants with reason `Conflict`; the rest of the boot proceeds.

## Network

```json
"network": {"hosts": ["api.example.com", "*.cdn.example.com"], "credentials": false}
```

`hosts[]` names every remote host the mod contacts (lower-case, optional
`:port`; a `*` label matches one or more labels at that position). Mod traffic
must be encrypted — `https`, `wss` — with loopback as the only exception; the
Loader enforces this for requests made through `ctx.http` / `floofy.fetch`
([python-api.md](python-api.md)). The validator **warns**, never refuses, on a
plaintext non-loopback URL in mod code (`PlaintextNetwork`) and on an encrypted
URL to an undeclared host (`UndeclaredHost`); the user sees the flags at install
and may accept them. `credentials: true` says the mod asks the user for
credentials itself — FloofyCrew never handles credentials.

## Parts

`parts[]` (at least one) is discriminated on `kind`; every part carries `side`
(`gateway`, `spa`, `electron`, `cli`) and, except `config`, a `path` relative to
the mod root (no leading `/`, no `..`, no backslash). Which seam each kind lands
through is the subject of [seams.md](seams.md).

| Kind | `path` points at | Kind-specific fields |
|---|---|---|
| `theme` | `theme.json` of a theme pack | — |
| `agent` | an agent spec (`.json` / `.md`) | — |
| `skill` | `SKILL.md` | — |
| `appearance` | the appearance pack's manifest | — |
| `app` | `app.json` of a KiroCrew App | — |
| `config` | optional JSON file of key/value pairs | `values` (wins over the file) |
| `python-hook` | a Python package directory | `module` (dotted name, imported under `floofy_mods.<id>.<module>`), `early` (also run before the host's platform bootstrap through the early shim) |
| `spa` | an ES module | `activation`: `runtime` (default; loaded by the SPA host after the host bundle) or `boot` (first-frame effects baked into `index.html` by the Patcher) plus the `boot` object |
| `patch` | a patch descriptor | — ([patching.md](patching.md)); the only kind that modifies payload files. A descriptor whose `target` is `electron:<member>` rewrites one file inside the desktop shell's `app.asar` (`side: electron`) |
| `ui` | the directory holding the mod's own page (or the entry file) | `entry` (the ES module the FloofyCrew App imports, `.mjs`/`.js`, listed in `files[]`), `title` (the entry in the App's Mods section, 1–80 characters), optional `icon` (`.svg`/`.png`); `side` is `spa` |

The zero-code kinds (`theme`, `agent`, `skill`, `appearance`, `app`) let an
existing KiroCrew artifact be a mod without any code: point the part at the
artifact and the manager installs it through the host's own seam.

### `spa` parts with `activation: "boot"`

```json
{"kind": "spa", "side": "spa", "path": "spa/boot.js", "activation": "boot",
 "boot": {"css": "spa/boot.css", "favicon": "theme/branding/favicon.png", "logo": "theme/branding/logo.png",
          "when": {"theme": "custom-rimuru"}}}
```

A theme mod rarely needs one of these any more: `mods/custom-themes` paints every
installed custom theme on the first frame (and carries the theme-reset guard), so a
theme mod is a `theme` part plus `dependsOn: {"custom-themes": ">=1.0.0"}`
(`mods/rimuru-branding` 2.0.0). The `boot` object stays for mods that bake their
own first frame — `custom-themes` itself declares one with an empty `boot: {}`,
its hook being the whole part.

`css` is appended before `</head>` after the host's own stylesheet (scope it,
e.g. to `[data-theme="custom-<slug>-dark"]`); `favicon` and `logo` are served
from the Loader app's `ui/boot/<id>/` and asserted before the host hydrates;
`title` and `attributes` (`data-*` on `<html>`) are re-asserted by the drift
guard until the host settles; `when.theme` acts only when the active colour
theme equals it. The part's file is the boot hook, inlined as `function (ctx)`
with `{id, color, mode, pref, element}` — ES5 only, no `</script`, at most
32 KB.

### `ui` parts: the mod's own page in the FloofyCrew App

```json
{"kind": "ui", "side": "spa", "path": "ui/", "entry": "ui/page.mjs", "title": "Settings demo", "icon": "ui/icon.svg"}
```

The App lists every mod with a `ui` part under Mods and, when the user opens
it, `import()`s `entry` same-origin from
`/api/apps/floofycrew/ui/mods/<id>/<entry>` (served by the Loader while the mod
is active, manifest-listed files only) and calls its default export
`mount(container, api)` with `api = floofy.mod(id)` — `config.get()/set(patch)`
persisted in the mod's `.floofy/config.json`, `routes.list()/fetch(path)` for
the routes the mod's `python-hook` registered with `ctx.routes.add`, `theme.tokens()`
/ `theme.current()`, `state()`, `dialog.confirm()/prompt()/alert()/form()` (in-document
dialogs — never the browser's pop-ups, which the desktop shell lacks); see
[spa-api.md](spa-api.md). `mount` may return
a cleanup function (or an object with `unmount()`). The page runs inside an
error boundary: an import or mount that throws is shown with the message and a
*Disable* button and never takes the manager page down. A `ui` part is inert
until opened, so it is **not** one of the code kinds the install confirmation
covers (only `python-hook` and `spa` are);
`floofy validate` checks that `entry` and `icon` exist,
sit under `path` and are hash-listed, and its network scan covers the module
like any other shipped code. `floofy new ui` scaffolds one.

## Files

`files[]` lists every shipped file other than `floofy.json` itself as
`{"path", "sha256"}`. A missing or mismatched file is the non-load reason
`MissingFiles`; a shipped file that is not listed is a warning. `floofy
validate` recomputes the hashes; the scaffold's smoke test and the registry's
CI gate do the same. Archives with symlinks, absolute paths or `..` segments are
rejected before extraction.

## What the loader does with all of this

At every gateway start the Loader validates each installed mod (schema, hashes),
consults the compatibility matrix for the exact host version, resolves the
graded dependencies and load order, attaches the host's governance findings as
**warnings**, and activates what remains — fail-open per mod. Every mod that
does not activate carries one typed reason: `Error`, `Duplicate`, `Conflict`,
`Dependency`, `Released`, `Feature`, `Unsupported`, `MissingFiles`,
`Quarantined`, `UserDisabled`, or the Loader-level `ConsentRequired`
([consent-and-trust.md](consent-and-trust.md)). Governance is never a reason.

---
Covers Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 3.5.
