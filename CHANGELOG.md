# Changelog

FloofyCrew follows SemVer: `floofy.api_version` is what mods depend on;
deprecated API names keep working for two minor releases with a warning that
names the caller. Releases are tagged `vA.B.C` (label `floofycrew vA.B.C`); each
carries a `supports` list — the host versions whose compatibility-matrix row
reports `loader: ok` per edition and channel — repeated here, in
`pyproject.toml` (`[tool.floofycrew] supports`) and in the release's
`supports.json`. FloofyCrew is unofficial and not affiliated with Kiro or
KiroCrew; it never ships a rebuilt host.

## 1.0.0 — 2026-09-20

The first release: the whole modding ecosystem, on both KiroCrew editions,
from one codebase.

### Supports

| Edition | Channel | Host versions |
|---|---|---|
| internal | beta | 0.7.0.5 |
| internal | stable | 0.7.0.5 |
| external (public) | insider | 0.7.0rc5 |
| external (public) | stable | 0.6.0 |

Every row was produced by the Forge on the build host (provision → check →
matrix): Loader `ok`, all SPA fingerprints and Python anchors matched. Public
stable 0.6.0 needed one repair round (the bundler folds the theme hook into a
differently named shared chunk there; `bundle.chunk` fingerprints now accept
fallback stems), merged into this release. Public nightly is watched but not
supported.

### What ships

- **The manifest** (`floofy.json`, schema 1) with the published JSON Schema:
  identity, host compatibility with `strict`, five graded dependency levels,
  `loadBefore`/`loadAfter`, `network` declarations, nine part kinds, `files[]`
  hashes; `floofy validate` (schema, hashes, engineering-rule targets refused,
  governance-file targets flagged, network scan).
- **The resolver**: graded dependencies, mutual conflicts, topological order,
  cycles → `Conflict`, `strict` → `Unsupported`, typed non-load reasons.
- **The Patcher**: payload discovery through the edition adapters, deployment
  manifests and `.floofybak` sidecars, drift classification, exactly-once
  fingerprints with `appliesTo`/`fromBuild`/`toBuild`, per-op atomicity, the
  import-map path for hashed chunks and the alias-graph fallback, `.br`/`.gz`
  sidelining, revert-then-patch, verify against the live gateway, restore by
  manifest and sweep, every payload present, garbage collection.
- **The Loader** as a KiroCrew App: consent check, pending installs, hash
  validation, resolver, compat cache, governance **warnings**, fail-open
  activation of `python-hook` parts under namespaced modules, the hook registry
  (`before`/`after`/`replace` by import path and by route, unwind on
  deactivate), `ctx.http`/`floofy.fetch` (encrypted-only, loopback exempt,
  declared hosts, audited denials), per-mod config and logger, the event bus,
  the state API, vanilla fallback when the Loader itself fails, the optional
  early shim.
- **The SPA host**: `window.floofy`, runtime `spa` parts in error boundaries,
  content-fingerprinted surfaces (13 shipped), patch inspection, the reporter,
  the first-frame boot script with baked CSS and the `MutationObserver` drift
  guard.
- **The manager**: the `floofy` CLI (standard-library Python 3.12, zipapp and
  wheel), `init` with the one-time warning and the re-apply triggers, `doctor`,
  `status`, the full install/uninstall/enable/disable/update flow with
  disclosure and confirmations, `pending/` staging and `--now`, seam kind
  handlers with byte-equivalent direct writes when a host route is closed,
  host-version-change handling (reporter, matrix, re-apply, yeet, rollback),
  `hold`, profiles and lockfiles, the manager page with the unofficial banner,
  the audit log, registry trust controls.
- **The registries**: signed `index.json`/`compat.json` (Ed25519 over canonical
  JSON, keys pinned per edition), federated sources, hash lookup, the
  KiroCrew-compatible `app-registry.json`, `registry-tools` (build, sign,
  validate-submission, compat-merge, bootstrap).
- **The Forge**: five lanes, the JSON ledger with self-clearing blockers,
  provisioning in isolation, the check stage, repair agents through the
  retry/fallback runner with a review pass, matrix rows, the release stage
  (byte-identical double build, signed snapshots per edition), notifications.
- **The first mod**: `rimuru-branding` (theme + first-frame boot + import-map
  patch), installable on both editions from one archive.
- **Two tracks**: the internal data package and the public release assets
  (zipapp, wheel, Loader app archive, `SHA256SUMS`, `install.sh`), both built
  reproducibly; 86.7 % of shipped files (95.8 % of bytes) are byte-identical
  across editions — only the two edition adapters differ.
- **Documentation** under `docs/`: manifest, seams, Python and SPA APIs with the
  deprecation policy, patching and fingerprints, publishing, consent and trust,
  the CLI, update survival, the Forge runbook, the two-track measurements.

### Not in this release

- The public registry repository and the public GitHub organisation are the
  maintainer's to create; the adapter carries a placeholder organisation until
  then (`editions/public/floofy_edition_public/registry.json`).
- Electron main-process modding, Docker-image payloads and a moderation queue
  beyond pull-request review are out of scope for v1.
- `ctx.http` validates `wss` URLs but does not implement a WebSocket client.
