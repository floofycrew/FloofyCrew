# editions/public — external edition adapter

The thin adapter for the public open-source KiroCrew (GitHub `kirodotdev/KiroCrew`,
versions `X.Y.Z`, channels `stable`/`insider`/`nightly`): payload discovery for
pipx and managed venvs (`~/.kiro/crew-venv*`, `crew-venv-<ver>` siblings), macOS
`.app` `backend-dist` and `/opt` desktop installs; host-version derivation from
`kiro_crew.__version__` plus the channel marker; the venv-site early-shim
location; the hourly user timer (systemd/launchd) and the `kirocrew` PATH wrapper
re-apply triggers; the GitHub registry endpoint and pinned key; no network
identity. Nothing here may reference the internal edition. Shipped with the
GitHub releases.


## Modules

| Module | Purpose |
|---|---|
| `floofy_edition_public.payloads` | `PipxProvider` (both pipx layouts, `PIPX_HOME`), `ManagedVenvProvider` (`~/.kiro/crew-venv` + `crew-venv-<ver>` shadow siblings, promoted one current), `DesktopBundleProvider` (`.app` `backend-dist`, `/opt`), `default_providers()`, `probe_edition()`. Tests in `tests/`. |
| `floofy_edition_public.governance` | `locations(host_home)`: the neutral home-tier files only (no bundled ceiling). |
