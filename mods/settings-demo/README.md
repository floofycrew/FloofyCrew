# settings-demo

The example of a mod with its **own settings page** inside the FloofyCrew App
(Requirement 16.4; task 11.6) — the second first-party mod, listed in the
internal registry beside `rimuru-branding`. It does nothing useful on purpose:
it shows how a mod page and a mod backend share one configuration through the
`floofy.mod(id)` API, and it is what the manager App's headless smoke test
installs, opens and disables. FloofyCrew is unofficial; nothing here is part of
KiroCrew.

## Parts

| Part | Seam | What it does |
|---|---|---|
| `ui` → `ui/page.mjs` (`title` "Settings demo", `icon` `ui/icon.svg`) | the FloofyCrew App mounts it under **Mods → Settings demo**, same-origin, inside an error boundary | a text field (*Greeting*) and a toggle (*Notify*) persisted with `api.config.set(patch)` in the mod's `.floofy/config.json` and read back with `api.config.get()`; a button calling the mod's backend with `api.routes.fetch("echo")`; the host's theme tokens for the accent colour; `api.state()` for the Loader verdict |
| `python-hook` → `hook/__init__.py` | the Loader activates it inside the gateway once the mod is enabled | `activate(ctx)` registers one backend route, `echo` (`GET` and `POST`), with `ctx.routes.add`; the Loader serves it at `/api/apps/floofycrew/mods/settings-demo/api/echo`. It answers with the config the page stored (`ctx.config` is the same store), the effective values over the defaults, the host edition and version, and the JSON body of a `POST` |

Because the mod ships a `python-hook` part it **lands disabled** after install
(Requirement 11.7) and asks for the usual confirmation; `floofy enable
settings-demo` (or the App's Enable button) activates it, and the page becomes
reachable — a `ui` part alone is inert until opened and would have landed
enabled. No `network` hosts: the page talks to the Loader's same-origin routes
only. Needs FloofyCrew ≥ 1.1.0 (the `ui` part kind and `ctx.routes`).

## Trying it

```bash
floofy install mods/settings-demo --now      # or: floofy install settings-demo (from the internal registry)
floofy enable settings-demo
```

Open the FloofyCrew App → Mods → *Open Settings demo*: type a greeting, flip the
toggle, press *Ask the backend what it stored* — the reply is the same document
`cat ~/.kiro/crew/floofy/mods/settings-demo/.floofy/config.json` shows. The
Disable button on the page's error card (or `floofy disable settings-demo`)
unwinds the route and stops serving the page.

## Keeping it honest

```bash
python scripts/hash_example_files.py mods/settings-demo      # files[] hashes
python -m floofy_core.cli_validate mods/settings-demo        # zero errors, zero warnings
```

`LICENSE` is MIT; `ui/icon.svg` is drawn for this mod (no third-party artwork).
