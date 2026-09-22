# FloofyCrew — mods for KiroCrew

FloofyCrew is an unofficial mod manager and loader for KiroCrew. Install a
theme, a fix or a new page from a signed registry (or straight from a git
link), switch it on, and it survives every KiroCrew update — nothing in your
KiroCrew install is ever replaced, and `floofy restore --all` takes it all back
out.

- **Easy**: `floofy install <mod>`, or click Install in the FloofyCrew page of
  your dashboard. Restart KiroCrew from the same page.
- **Safe**: every mod runs on your explicit consent, every change is logged and
  reversible, registry indexes are signature-verified, and a mod that does not
  match your KiroCrew version is quarantined, not applied blind.
- **Yours**: make your own — a theme, a patch, a settings page — with
  `floofy new`, and publish it to the registry.

Works with both the internal and the public (open-source) edition of KiroCrew.

## Install

**Public edition** (GitHub releases, verified against `SHA256SUMS`):

```bash
curl -fsSL https://raw.githubusercontent.com/floofycrew/FloofyCrew/main/packaging/public/install.sh | sh
floofy doctor
```

or into the host's own virtualenv: `pip install floofycrew-<version>-py3-none-any.whl`.

Every install path ends in `floofy init`, which shows a one-time warning and installs nothing
until you agree. Then open **FloofyCrew** in the dashboard sidebar, or run
`floofy` for the interactive terminal. Updating FloofyCrew itself: `floofy
self-update`, or the *Update & restart* button when the App tells you a new
release is out.

> FloofyCrew is a community project. It is not affiliated with, endorsed by or
> supported by Kiro or the KiroCrew maintainers. It attaches to the KiroCrew you
> already have and never ships a rebuilt one; the risk of every mod you install
> is yours. Read [docs/consent-and-trust.md](docs/consent-and-trust.md) before
> `floofy init`.

Current release: **1.3.1** — what each release ships is in
[CHANGELOG.md](CHANGELOG.md); the KiroCrew versions it supports are in
`pyproject.toml` (`[tool.floofycrew] supports`) and in the release's
`supports.json`.

## Layout

One repository, one shared core, two thin edition adapters. The core and the
docs are edition-neutral; CI checks it, and measures that ≥ 80 % of the shipped
files are byte-identical across the two editions (the figures are in
[docs/two-track.md](docs/two-track.md)).

| Path | What |
|---|---|
| `floofy-core/` | the shared core (`floofy_core`): manifest schema and validator, resolver, Patcher, registry client and signing, compatibility cache, the `floofy` CLI; example mods under `examples/`, tests under `tests/` |
| `editions/public/` | the public edition adapter: pipx / venv / desktop payloads, the public registry and its pinned key |
| `loader-app/` | the Loader, a KiroCrew App (`app.json`, `floofy_loader/`, the early shim `floofy_early/`, the manager page in `ui/`) |
| `spa-host/` | the dashboard-side runtime: `window.floofy`, surfaces, patch inspection, the reporter, the first-frame boot script |
| `mods/` | first-party mods, one complete mod per directory — the index is [mods/README.md](mods/README.md) |
| `registry-tools/` | build, sign and validate registry indexes; bootstrap a registry repository |
| `packaging/` | the release tracks: `public/` (GitHub release assets, wheel, `install.sh`) and the internal package track |
| `docs/` | documentation ([index](docs/README.md)) |
| `scripts/` | repository tooling (identifier check, zipapp / wheel / Loader builds, identity report) |
| `branding/` | the fox mark, wordmark and ASCII banner, with the scripts that generate them |

## Documentation

Want to contribute a mod without reading any of this? Each edition can ship a
contributor assistant that scaffolds, develops and publishes the mod with you;
the docs below are the reference it works from.

[docs/README.md](docs/README.md) is the index: writing a mod (manifest, seams,
the Python and SPA APIs, patches and fingerprints), publishing to a registry,
the CLI and the interactive terminal, the consent and trust model, surviving
updates, and the Forge runbook.

## Development

```bash
uv sync                                          # dev tools (pytest, hypothesis, playwright)
uv run pytest                                    # the whole suite (~5 min; Playwright tests need Chromium)
python scripts/check_no_internal_identifiers.py  # what CI's de-amazon job runs
python scripts/artifact_identity_report.py       # byte-identity across editions
packaging/public/build.sh --check                # the public release tree, built twice, byte-identical
```

Rules the code keeps: one repository, two thin adapters; no internal identifiers
in the shared core or `docs/`; no rebuilt host is ever shipped; the user's
consent outranks host governance (governance is a warning, never a gate); mod
network traffic is HTTPS-only, loopback excepted; the Loader is a KiroCrew App
through the App Kit seam, never the host's plugin entry point.

Author: Oscar Tseng. Licensed under the [MIT License](LICENSE); the theme-pack
validation is ported from KiroCrew (Apache License 2.0) — attribution and the
license text are in [NOTICE.md](NOTICE.md) and `LICENSES/`, and every built
artifact carries them.
