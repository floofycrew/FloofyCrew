# loader-app

The FloofyCrew Loader packaged as a KiroCrew App (`app.json` with
`backend.hooks.on_startup` / `on_shutdown` / `routes`, `ui.entry`, permissions),
installed into `~/.kiro/crew/apps/floofycrew/` through the host's own App Kit
seam — never a `kirocrew.plugins` entry point. It runs the Python Loader from
`floofy-core` inside the gateway (consent check, pending apply, resolve, activate
`python-hook` parts fail-open, publish state) and serves the manager page and the
SPA host from `ui/`. Only packaging and the app manifest belong here; the logic
stays in `floofy-core` so both editions ship the same archive. Task 4.1 adds the
manifest. Edition-neutral: CI scans this directory for internal identifiers.
