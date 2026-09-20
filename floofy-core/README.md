# floofy-core

The shared core of FloofyCrew, published as-is to both editions: the `floofy.json`
manifest schema and validator, the dependency resolver, the Patcher engine, the
registry client and signature verification, the compatibility-matrix format, the
Loader app runtime and the SPA host. Everything here is stdlib-only Python 3.12
(plus ES modules for the dashboard side) and must run unchanged on the host's own
interpreter. It must not contain any edition-specific value — no internal
hostnames, package names, account identifiers or people — those belong in
`editions/*`; CI enforces this with `scripts/check_no_internal_identifiers.py`.
Tests live in `tests/`.

## Layout

| Module | Purpose |
|---|---|
| `floofy_core.schema` | the two JSON Schemas (`floofy.schema.json`, `patch.schema.json`), `load_schema()`, and `schema/check.py`, a small stdlib JSON Schema checker; field reference in `schema/README.md` |
| `floofy_core.semver` | `Version` (SemVer 2.0), `HostVersion` (the shapes KiroCrew prints, with the `X.Y.Z` `base`), `Range` (npm-style ranges) |
| `floofy_core.validator` | `validate_mod()` → `ValidationReport` of `Finding`s: schema, hashes, parts, targets, network |
| `floofy_core.resolver` | `resolve(records, host)` → `Resolution`: graded dependencies, mutual conflicts, load order, cycle → `Conflict`, `strict` → `Unsupported`, typed `Reason` enum; governance is never a reason |
| `floofy_core.archive` | `open_archive_safely()`: listing checked before extraction (no symlinks, absolute paths or `..`) |
| `floofy_core.targets` | `TargetPolicy`: engineering-rule targets (error) vs governance files (warning, accepted per file); adapters extend it |
| `floofy_core.netscan` | plaintext / undeclared-host scan and `host_matches()` shared with the Loader's `ctx.http` |
| `floofy_core.cli_validate` | `floofy validate` front end (`--json`, exit 1 on errors only) |
| `floofy_core.payloads` | `Payload`, `PayloadProvider`, `discover_payloads()` → `DiscoveryResult` (every searched root listed on a miss); the layout-agnostic `find_package_dirs()` walk, `read_host_version()` (`__version__` + the `BUILD_VERSION` stamp, the host's rule), `channel_for_version()`; generic `FindSpecProvider` / `ExtraRootProvider`; edition adapters add their own providers |
| `floofy_core.deploy` | `DeployManifest` (`deploy/<payload>.json`, atomic save, `record_patch/added/sidelined`), `Backups` (first-write-only `.floofybak` sidecars, `restore_file`, `refresh` for host-updated drift), `added_name()` / `is_floofy_added()` (`-floofy` marker), `classify_drift()` → `DriftReport` with the Requirement 5.3 decision table and default actions |
| `floofy_core.patches` | `PatchDescriptor.load()` (schema-validated; `mode` in-place \| import-map, optional `find` literal, glob chunk targets, per-op `occurrence` only \| first \| last, in-memory `UiAsset`s), `apply_descriptor()` → `PatchResult` with per-op atomicity and typed `Skip`s (`NotApplicable`, `AlreadyApplied`, `FingerprintMiss`, `FingerprintAmbiguous`), `fingerprint_report()` (the reporter), `merge_import_map()` / `drop_modulepreload()` for the spike-1.4 remap path |
| `floofy_core.spa_patches` | the spike-1.4 module-patching path (task 5.3): `resolve_chunk_target()` (a glob on the hashed name → exactly one file), `apply_import_map_patch()` (ops on the ORIGINAL chunk text, `rebase_imports()`, marker comment, patched copy + `.floofy.json` sidecar under the Loader app's `ui/patched/`, import-map key and dropped `modulepreload` in `index.html`), `is_entry_chunk()` (the `<script src>` chunk falls back to the alias graph), `refresh_patched_index()` (`ui/patched/index.json` for the runtime `window.floofy.patches`), `default_ui_root()` |
| `floofy_core.boot_script` | the generated `index.html` descriptor (task 5.4, Requirement 4.3): `build_boot_descriptor(boot_mods, host_home=…)` → the spike-1.3 **loader tag** (`insert-before` the `main-*.js` module tag) + ONE inline `<script id="floofy-boot">` rendered from the `spa-host/src/boot.mjs` template with every `activation: boot` part's config in load order (`insert-before` the first `<script`, `occurrence: first`) + one `<style id="floofy-boot-css-<id>">` per mod (`append-head`); `read_theme_defaults()` (the host config's `dashboard.theme_color` / `theme_mode`, the cold-client fallback); favicon/logo files become `UiAsset`s copied under `ui/boot/<id>/`; refuses `</script`, `</style` and oversized parts |
| `floofy_core.aliasgraph` | `build_graph()` → `ChunkGraph` (importer graph of `assets/` by hashed-name reference), `closure()`, `republish()` (aliases `<stem>-<runtag>-floofy.<ext>` with rebased references, stale-alias removal, repointed `index.html` text returned to the caller), `sideline_sidecars()` (`.br`/`.gz` → backup names, reversible); the fallback for targets the import-map remap cannot reach |
| `floofy_core.patcher` | `Patcher(data_home, payloads, ui_root=…)`: `apply(patch_set)` (revert-then-patch every payload, drift actions, target policy, import-map copies under `ui/patched/`, alias graph, `audit.jsonl`), `restore()` (manifest + glob sweep; a restore of every payload also sweeps `ui/patched/`, a partial one keeps copies other payloads still map to), `status()`, `verify()` over the dashboard socket/loopback (`verified` / `dormant` / `failed` / `no-gateway`; patched copies checked through the app-UI route), `gc()` (vanished payloads and orphaned `ui/patched/` copies); `PlannedPatch`, `resolve_target()`, `url_for()` |
| `floofy_core.gateway` | `GatewayEndpoint` (unix socket `dashboard-<port>.sock` or loopback port, `GET` with identity encoding), `find_endpoints(host_home)`, `served_version()` from `/api/health` |
| `floofy_core.editions` | adapter discovery by module prefix `floofy_edition_*`: `edition_providers()`, `combined_edition_probe()`, `governance_locations()` |
| `floofy_core.governance` | `GovernanceSnapshot.read(locations)` (policy tiers, `config.json` app gate, admission files, `/api/status`), `warnings_for()` / `survival_check()` → `GovernanceWarning`s (codes in `WARNING_CODES`; warnings, never gates — DR-5), the `Confirmer` protocol (`AlwaysConfirm`, `NeverConfirm`, `CallableConfirmer`) the Patcher asks for Requirement 5.9 |
| `floofy_core.themes` | the host's theme-pack validator ported (`validate_theme_dir()`, levels 0–2, caps, denylists, colour allowlist) and `direct_write_theme()` — the byte-equivalent stage-validate-rename install used when `theme_install` is closed (Requirement 2.1, 2.8) |
| `floofy_core.cli_patch` | `floofy-patch apply\|restore\|status\|verify\|gc` (`--root`, `--payload`, `--no-adapters`, `--port`, `--json`, `--confirm-governance`); `FLOOFY_NO_ADAPTERS=1` limits discovery to `--root` (set by the test session) |
| `floofy_core.spa_report` | the SPA reporter from Python (Requirement 4.5, 9.1): `load_registry()` (`spa-host/src/surfaces.json`, or `ui/surfaces.json` beside a vendored core), `check_bundle_fingerprints(dist_dir)` — the **offline** check of the `bundle` fingerprints against a `static/dist` on disk (stems discovered from the shell and `./<stem>-<hash>.js` references, never by hash), `run_browser_report(url, token)` — the **live** `window.floofy.report()` through headless Chromium (Playwright imported inside; injects the host module tag when `index.html` carries none); both return `SpaReport` with the matrix block `spaFingerprints {matched, total, missed[]}` |
| `floofy_core.cli_spa` | `floofy-spa verify --spa --url\|--port\|--ready-file [--token] [--extra-surfaces] [--screenshot] [--json]` (exit 1 when anything is missed; the `floofy verify --spa` front end for 6.x) and `floofy-spa bundle-check --dist DIR` (offline, no browser) |

`examples/<kind>/` holds one minimal valid mod per part kind; keep their hashes
current with `python scripts/hash_example_files.py` (`--check` in CI).

`tests/fixtures/dist-<ver>/` are trimmed real dists from three host versions
(see `tests/fixtures/README.md`; regenerate with `scripts/make_fixture_payload.py`).

Tests never touch a live host install: `tests/floofy_testing.py` sets
`FLOOFY_NO_ADAPTERS=1` for the session and every conftest fingerprints the live
dashboard shells before and after the run. The opt-in real-gateway smoke
(`tests/test_real_gateway.py`, `FLOOFY_REAL_PAYLOAD=1`) runs a copy of a payload
under `.scratch/` with scratch homes; see its docstring.
