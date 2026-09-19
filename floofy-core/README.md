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

`examples/<kind>/` holds one minimal valid mod per part kind; keep their hashes
current with `python scripts/hash_example_files.py` (`--check` in CI).
