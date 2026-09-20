# FloofyCrew

An **unofficial** modding ecosystem for KiroCrew — mod manifest, in-host loader,
overlay patcher, mod manager, registry, and a KiroCrew-driven release-tracking
"Forge" — covering both the Amazon-internal Toolbox edition and the public
open-source edition.

Status: **package 1 (spikes and infrastructure).** Nothing here is installed
into any KiroCrew yet.

## Layout

One repository, one shared core, two thin edition adapters (design DR-3).

| Path | What |
|---|---|
| `floofy-core/` | Shared core (`floofy_core`): manifest, resolver, Patcher engine, registry client, compat, Loader runtime, SPA host. Edition-neutral; CI enforces it |
| `editions/toolbox/` | Internal edition adapter (`floofy_edition_toolbox`): Toolbox payloads, `BUILD_VERSION`, user-site shim, systemd timer, ***REMOVED*** registry, ***REMOVED*** |
| `editions/public/` | External edition adapter (`floofy_edition_public`): pipx/venv/desktop payloads, `kiro_crew.__version__` + channel, venv-site shim, PATH wrapper, GitHub registry |
| `loader-app/` | The Loader packaged as a KiroCrew App (`app.json`, `ui/`) — task 4.1 |
| `spa-host/` | Dashboard-side ES modules: always-on host, `window.floofy`, boot script, reporter |
| `registry-tools/` | Build, sign and validate registry indexes; emit `app-registry.json` |
| `forge/` | Release tracking: watchers (`watch-internal.sh`), build-desk scripts, `docs/build-desk.md` (internal-only notes) |
| `docs/` | Public documentation (manifest reference, seams, APIs, trust model) |
| `research/` | Kickoff research reports, prompts, transcripts, `build-notes.md`, spike artefacts (`spikes/`); the agent runner moved to `forge/runner.sh` |
| `scripts/` | Repository tooling: `check_no_internal_identifiers.py` (+ `de-amazon-allow.txt`), run by CI |
| `.github/workflows/ci.yml` | CI: de-Amazon check of the shared core, `pytest` via `uv` |
| `pyproject.toml` | Project metadata (Python ≥ 3.12, no runtime dependencies), dev group, pytest config |
| `.kiro/specs/floofycrew-modding-ecosystem/` | `requirements.md`, `design.md` (decision records + spike outcomes), `tasks.md`, `BLOCKERS.md` |
| `.kiro/agents/floofy-researcher.json` | Read-only research agent template (per-model variants are derived by the runner) |

## Development

```bash
uv sync                                        # dev tools (pytest, hypothesis, playwright)
uv run pytest                                  # tests
python scripts/check_no_internal_identifiers.py  # what CI's de-amazon job runs
```

Related: ***REMOVED***'s existing `***REMOVED***KiroCrewThemePatcher` is the direct ancestor of
the Patcher component and the first mod to be ported.
