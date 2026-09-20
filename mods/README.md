# mods/

First-party FloofyCrew **mods**. Each directory is a complete mod (a `floofy.json`
manifest at its root plus the files it ships) that installs through the manager
like any third-party mod; nothing here is part of the framework core, the Loader
app or the SPA host, and the core never depends on anything in this directory.
They are the reference mods the tests, the docs and the Forge smoke stage use.

| Mod | What it is |
|---|---|
| [`rimuru-branding/`](rimuru-branding/) | the Rimuru slime theme: a Level-1 theme pack + a first-frame boot part + an import-map patch (the first real mod, ported from the standalone O3R theme patcher — task 5.5) |

Keep a mod honest with `python scripts/hash_example_files.py mods/<id>` (the
`files[]` hashes) and `python -m floofy_core.cli_validate mods/<id>`
(schema, hashes, targets, network scan). Theme mods with a boot part regenerate
their baked CSS with `python scripts/bake_theme_css.py mods/<id>`.
