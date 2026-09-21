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
