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
| `floofy_core.patches` | `PatchDescriptor.load()` (schema-validated), `apply_descriptor()` → `PatchResult` with per-op atomicity and typed `Skip`s (`NotApplicable`, `AlreadyApplied`, `FingerprintMiss`, `FingerprintAmbiguous`), `fingerprint_report()` (the reporter), `merge_import_map()` / `drop_modulepreload()` for the spike-1.4 remap path |
| `floofy_core.aliasgraph` | `build_graph()` → `ChunkGraph` (importer graph of `assets/` by hashed-name reference), `closure()`, `republish()` (aliases `<stem>-<runtag>-floofy.<ext>` with rebased references, stale-alias removal, repointed `index.html` text returned to the caller), `sideline_sidecars()` (`.br`/`.gz` → backup names, reversible); the fallback for targets the import-map remap cannot reach |
| `floofy_core.patcher` | `Patcher(data_home, payloads)`: `apply(patch_set)` (revert-then-patch every payload, drift actions, target policy, alias graph, `audit.jsonl`), `restore()` (manifest + glob sweep), `status()`, `verify()` over the dashboard socket/loopback (`verified` / `dormant` / `failed` / `no-gateway`), `gc()`; `PlannedPatch`, `resolve_target()`, `url_for()` |
| `floofy_core.gateway` | `GatewayEndpoint` (unix socket `dashboard-<port>.sock` or loopback port, `GET` with identity encoding), `find_endpoints(host_home)`, `served_version()` from `/api/health` |
| `floofy_core.editions` | adapter discovery by module prefix `floofy_edition_*`: `edition_providers()`, `combined_edition_probe()` |
| `floofy_core.cli_patch` | `floofy-patch apply\|restore\|status\|verify\|gc` (`--root`, `--payload`, `--no-adapters`, `--port`, `--json`, `--confirm-governance`); `FLOOFY_NO_ADAPTERS=1` limits discovery to `--root` (set by the test session) |

`examples/<kind>/` holds one minimal valid mod per part kind; keep their hashes
current with `python scripts/hash_example_files.py` (`--check` in CI).

Tests never touch a live host install: `tests/floofy_testing.py` sets
`FLOOFY_NO_ADAPTERS=1` for the session and every conftest fingerprints the live
dashboard shells before and after the run. The opt-in real-gateway smoke
(`tests/test_real_gateway.py`, `FLOOFY_REAL_PAYLOAD=1`) runs a copy of a payload
under `.scratch/` with scratch homes; see its docstring.
