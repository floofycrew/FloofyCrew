# FloofyCrew

An **unofficial** modding ecosystem for KiroCrew — a mod manifest, an in-host
Loader, an overlay Patcher, a mod manager (CLI and dashboard page), signed mod
registries with a per-host-version compatibility matrix, and a KiroCrew-driven
release-tracking "Forge" — covering both the internal edition of KiroCrew and
the public open-source edition from one codebase.

## Install

Public edition (GitHub releases; HTTPS only, every asset verified against
`SHA256SUMS`):

```bash
curl -fsSL https://raw.githubusercontent.com/<org>/FloofyCrew/main/packaging/public/install.sh | sh
floofy doctor
```

or, into the host's own virtualenv: `pip install floofycrew-<version>-py3-none-any.whl`.

Internal edition (the `FloofyCrew` ***REMOVED*** data package; the clone URL and the
details are in [packaging/internal/FloofyCrew/README.md](packaging/internal/FloofyCrew/README.md)):

```bash
git clone <the FloofyCrew package> && cd FloofyCrew
bash install.sh            # add `-- --i-accept-the-risk` only for automation you own
floofy doctor
```

Both paths end in `floofy init`, which shows the one-time warning and installs
nothing before you acknowledge it; `floofy doctor` afterwards tells you where
you stand, and `floofy restore --all` takes everything back out.

> FloofyCrew is an unofficial, community project. It is not affiliated with,
> endorsed by or supported by Kiro or the KiroCrew maintainers, and it does not
> use their branding as its own. It attaches to the KiroCrew you already have and
> never ships a rebuilt host; mods run on your explicit consent, everything it
> changes is logged and reversible (`floofy restore --all`), and the risk of
> every mod you install is yours. Read [docs/consent-and-trust.md](docs/consent-and-trust.md)
> before `floofy init`.

Status: **1.1.1** (patch on 1.1.0: FloofyCrew self-update and two `doctor` probe
fixes; 1.1.0 brought the terminal experience, the FloofyCrew App with mods' own
settings pages, git-reference installs, link records and the source tier). Every
package of the plan is implemented; the internal package and the public release tree are built
reproducibly by `packaging/`; the compatibility matrix rows come from the Forge.
What the release supports is in [CHANGELOG.md](CHANGELOG.md), in
`pyproject.toml` (`[tool.floofycrew] supports`) and in the release's
`supports.json`.

## Documentation

[docs/README.md](docs/README.md) is the index: the manifest reference, the seam
guide, the Python and SPA APIs with the deprecation policy, patch and
fingerprint authoring with the reporter, publishing to both registries, the
consent and trust model, the CLI, update survival, the Forge runbook and the
two-track measurements.

## Layout

One repository, one shared core, two thin edition adapters (design decision
DR-3). The shared core and the docs are edition-neutral by construction; CI
enforces it, and CI measures that ≥ 80 % of the shipped files are byte-identical
across the two editions (88.8 % of files, 96.3 % of bytes on the current tree; the per-release figures are in `docs/two-track.md`).

| Path | What |
|---|---|
| `floofy-core/` | the shared core (`floofy_core`): manifest schema and validator, resolver, Patcher engine, registry client and signing, compatibility cache, the `floofy` CLI; JSON Schemas under `floofy_core/schema/`; example mods under `examples/`; fixture payloads and the test suite under `tests/` |
| `editions/public/` | the public edition adapter (`floofy_edition_public`): pipx / venv / desktop payloads, host version from the package version and channel marker, venv-site shim, PATH wrapper, the public registry endpoint and pinned key |
| `editions/toolbox/` | the internal edition adapter (`floofy_edition_toolbox`): the internal bundle's payloads and build stamp, user-site shim, the internal registry endpoint, pinned key and identity; `docs/publishing-internal.md` |
| `loader-app/` | the Loader packaged as a KiroCrew App (`app.json`, `floofy_loader/`, the optional early shim `floofy_early/`, the manager page in `ui/`) |
| `spa-host/` | the dashboard-side runtime: `window.floofy`, surfaces, patch inspection, the reporter, the first-frame boot script |
| `mods/` | first-party mods (`rimuru-branding`: theme + first-frame boot + import-map patch) |
| `registry-tools/` | build, sign and validate registry indexes; emit the KiroCrew-compatible `app-registry.json`; bootstrap a registry repository |
| `packaging/` | the two release tracks: `internal/` (the `FloofyCrew` data package) and `public/` (release assets, wheel, `install.sh`, the `loader-app` tree) |
| `forge/` | the release-tracking system: lanes, ledger, provision/check/repair/matrix/release/notify stages, the agent runner; `docs/` holds the build-desk notes (internal-only) |
| `docs/` | public documentation ([index](docs/README.md)) |
| `scripts/` | repository tooling: `check_no_internal_identifiers.py`, `build_zipapp.py`, `build_loader_app.py`, `build_wheel.py`, `artifact_identity_report.py`, `install_both_editions.py`, fixture and hash helpers |
| `research/` | the kickoff research reports and decision records, spike artefacts, build notes |
| `.github/workflows/` | `ci.yml` (de-Amazon check, two-track measurements, pytest) and `release.yml` (the public release on a `v*` tag) |
| `.kiro/specs/floofycrew-modding-ecosystem/` | `requirements.md`, `design.md` (decision records + spike outcomes), `tasks.md`, `BLOCKERS.md` |
| `pyproject.toml`, `CHANGELOG.md` | project metadata (Python ≥ 3.12, **no runtime dependencies**), `[tool.floofycrew] supports`, the dev group; what each release ships |

## Development

```bash
uv sync                                          # dev tools (pytest, hypothesis, playwright)
uv run pytest                                    # the whole suite (~5 min; Playwright tests need Chromium)
python scripts/check_no_internal_identifiers.py  # what CI's de-amazon job runs
python scripts/artifact_identity_report.py       # byte-identity across editions
python scripts/install_both_editions.py          # the same mod archives (both first-party mods) on both editions
packaging/public/build.sh --check                # the public release tree, twice, byte-identical
packaging/internal/build.sh --check              # the internal package, twice, byte-identical
python -m forge --help                           # the Forge (runs on the maintainer's build host)
```

Owner decisions the code must never erode: one repository, two thin adapters;
no internal identifiers in the shared core or `docs/`; no custom host is ever
shipped; user consent outranks host governance (governance is a warning, never
a gate); mod network traffic is encrypted-only with loopback as the sole
exception; the Loader is a KiroCrew App through the App Kit seam; the host's
plugin entry point is never used.

FloofyCrew's Patcher descends from a standalone dashboard-branding patcher for
KiroCrew; that patcher's branding is the first mod (`mods/rimuru-branding`), and
[docs/migrating-from-the-standalone-patcher.md](docs/migrating-from-the-standalone-patcher.md) covers
the hand-over. Licensed under the MIT License.
