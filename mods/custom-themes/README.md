# custom-themes

A theme editor for KiroCrew inside the FloofyCrew App, and the layer other theme
mods build on. Unofficial, like everything in FloofyCrew.

## What a theme is here

KiroCrew has an installable theme-pack format of its own (`theme.json` +
`variables.json`, then branding, fonts, `styles/overrides.css`, overlays…) and
installs a pack through a validator. Two things stop that format short of the
themes the host itself ships (the ones its sidebar lists with an emoji): at
runtime the host keeps only the `overrides.css` rules that target a handful of
surfaces (`body`, `.topbar`, `.sidebar`, `.chat-container`, `.message-bubble`,
`.input-area`, `.code-block`, `button.primary`) and drops everything else; and a
custom pack paints one frame late on a cold client.

A **FloofyCrew theme** is therefore two layers:

| Layer | Where it lives | Who applies it |
|---|---|---|
| the **host pack** — palette, name, emoji, bot name, logo, favicon, fonts, `overrides.css` | `<data dir>/library/<slug>/pack/`, installed into `<host home>/themes/<slug>/` through `floofy_core.themes.direct_write_theme` (the byte-equivalent of the host's `POST /api/themes/install`: stage, validate with the ported host validator, rename) | the host — the theme is a normal entry of Settings → Display and keeps working without FloofyCrew |
| the **extra CSS** — any selector, any rule (`.topbar-glass`, `[data-role=assistant] .msg-content::after`, `body::before` scanlines…) | `<data dir>/library/<slug>/extra.css`, never in the pack | this mod — scoped to `[data-theme="custom-<slug>-dark|light"]` by the compiler (`hook/cssc.py`) and applied by the runtime part; the first frame comes from a render-blocking `<link>` the boot part writes |

The extra CSS is written **unscoped**; `@floofy-mode dark { … }` limits rules to
one mode, `url(pack:branding/logo.png)` reaches a file the pack ships. The
compiler refuses `@import`, `</style`, `expression()` and any `url()` to another
origin (FloofyCrew's encrypted-only network rule with loopback as the sole
exception; the mod declares no hosts).

## Parts

| Part | What it does |
|---|---|
| `ui` → `ui/page.mjs` | the editor the App mounts under Mods: your themes, the host's built-in themes as presets, tabs Palette (dark / light, the 56 host tokens, colour pickers, unset = derived), Look (extra CSS), Pack (name, emoji, bot name, loader icons, `fonts[]`, `overrides.css`, notes), Branding (logo / favicon / wordmark / preview / font files); live preview (the draft re-scoped to `html[data-theme]` so it overrides the active theme without touching `data-theme`), Save, Install to host, Use this theme, Export, Import, Duplicate, Uninstall, Delete |
| `python-hook` → `hook/` | `library.py` (the store and the document the page round-trips), `cssc.py` (scoping and compilation), `hostthemes.py` (presets), the routes below, and the first-frame sheet `<Loader app>/ui/boot/custom-themes/themes.css` regenerated on every change |
| `spa` (runtime) → `spa/runtime.mjs` | holds the active custom theme's compiled sheet in `<style id="floofy-custom-themes">`; re-fetches on `data-theme` changes, on the host's `mc-custom-themes-changed` and on the editor's `floofy-custom-themes-changed`; asks the hook once per document to refresh the first-frame sheet |
| `spa` (boot) → `spa/boot.js` | when the active colour theme is `custom-*`, `document.write`s a `<link rel=stylesheet>` to the first-frame sheet — parser-inserted, so the browser blocks the first paint on it like on the host's own stylesheets; served by the Loader app's unauthenticated ui route (`no-cache`); removed by the runtime part once its `<style>` holds the same rules |
| `patch` → `patches/theme-reset-guard.json` | the import-map patch on the `useTheme-*.js` chunk that turns the host's persisting theme reset into a no-op (a failed per-pack fetch used to write the default theme into the workspace config and downgrade every client). Moved here from `rimuru-branding`, because every custom theme needs it |

### Presets: the host's own themes

`GET presets` reads the running host's `static/dist/assets/*.css` and returns
every `[data-theme=<slug>-dark|light]` palette it finds (the stock ones and an
edition's own, such as Lumon, LCARS, Bikini Bottom, Knight Rider or Miami Vice
2080 on the internal edition), together with the theme's decorative rules
rewritten into the extra-CSS dialect and the `@keyframes` they reference, and
the label + emoji the sidebar shows (from the `{value, label}` registrations in
the JS chunks). Nothing is transcribed or shipped: a preset exists exactly when
the host has that theme, on either edition, on any build. What cannot carry over
is honest about it: decorations the host's scripts mount only while their theme
is active (Miami's animated wall, LCARS's red-alert banner) have no element to
style under a custom theme.

### Routes (under the mod's api root)

| Route | What |
|---|---|
| `GET status` | host version, the active theme (`dashboard.theme_color`), the first-frame sheet's state |
| `GET themes` / `POST themes` | the library and the host's installed custom themes / create `{slug, name, from: "blank" \| "preset:<slug>" \| "installed:<slug>" \| "library:<slug>"}` |
| `GET / PUT / DELETE themes/{slug}` | the document / save (validated like the host would) / delete (`?uninstall=1` also removes the host copy) |
| `POST themes/{slug}/install` · `…/uninstall` | write the pack into `<host home>/themes/` / remove it |
| `GET themes/{slug}/export` · `POST import[?slug=&overwrite=1]` | one JSON file (`format: floofy-theme/1`, assets base64) |
| `POST assets/{slug}/{branding\|fonts}/{name}` | upload (`{data: base64}`) or remove (`{remove: true}`) a branding image or a font file, within the host's per-file caps |
| `GET presets` · `GET presets/{slug}` | the host's built-in themes (summary; `?full` or the detail route for palettes and extra CSS) |
| `GET css` · `GET css/{slug}` · `POST preview` | the first-frame sheet / one compiled theme / a draft re-scoped for the live preview |
| `POST refresh` | regenerate the first-frame sheet |

Switching the active theme is done by the page the way the host's Settings page
does it: `localStorage['mc-color-theme']`, the `mc-theme-sync` event and
`PUT /api/config/theme` (the host default every client falls back to).

## Theme mods on top of it

A theme mod no longer needs a boot part or a patch: a `theme` part pointing at
its pack plus `dependsOn: {"custom-themes": ">=1.0.0"}` is enough
(`rimuru-branding` 2.0.0 is exactly that). The first-frame sheet covers **every**
custom theme installed on the host, and the reset guard is applied once. To ship
an extra-CSS layer with a theme mod, add the theme to the library here and export
it; a mod-side `extra.css` convention is a candidate for a later release.

## Governance

Installing a pack from the editor writes it directly with the host's own rules
applied locally. If the host's policy denies `capabilities.theme_install`, that
is a **warning** the manager shows for theme mods — never a gate (user consent
outranks host governance, DR-5). `floofy restore --all` removes the first-frame
sheet with the other boot assets; the library under the data dir stays until
`floofy deinit --purge`.

## Regenerating

```bash
python scripts/hash_example_files.py mods/custom-themes    # files[] hashes
python -m floofy_core.cli_validate mods/custom-themes      # zero errors, zero warnings
```

The mod's own files are MIT (`LICENSE`). It ships no artwork but its icon.
