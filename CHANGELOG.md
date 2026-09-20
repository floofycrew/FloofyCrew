# Changelog

FloofyCrew follows SemVer: `floofy.api_version` is what mods depend on;
deprecated API names keep working for two minor releases with a warning that
names the caller. Releases are tagged `vA.B.C` (label `floofycrew vA.B.C`); each
carries a `supports` list — the host versions whose compatibility-matrix row
reports `loader: ok` per edition and channel — repeated here, in
`pyproject.toml` (`[tool.floofycrew] supports`) and in the release's
`supports.json`. FloofyCrew is unofficial and not affiliated with Kiro or
KiroCrew; it never ships a rebuilt host.

## Unreleased

### Added

- Terminal style layer (Requirement 15.1, task 10.1): the CLI paints its human
  output with a small semantic palette (`heading`, `ok`, `warn`, `danger`,
  `muted`, `accent`) and the weights bold/dim/italic, choosing the colour depth
  from `COLORTERM` (`truecolor`/`24bit` → 24-bit) and `TERM` (`256color` → 256,
  else 16). Styling is off when stdout is not a terminal, under `NO_COLOR` (any
  value), with the new global `--no-color`, on `TERM=dumb` and always with
  `--json`, whose output stays byte-identical; `FORCE_COLOR` turns it on for a
  pipe. What a line says never depends on colour: the transcript the manager page
  reads is plain text.
- The terminal banner (Requirement 15.5, task 10.2): the FloofyCrew mark from
  `branding/ascii/floofy.json` (built by `branding/build_ascii.py`) ships as the
  CLI's package data and leads `floofy --version` and `floofy doctor` on a
  terminal — 24-bit where the terminal advertises it, quantised to the 256-colour
  cube, a single bright-magenta rendering on 16 colours, and omitted when colours
  are off or the terminal is narrower than the art plus its margins (42 columns).
  Never in `--json` output or the in-process transcript.
- The install scripts of both editions restyled (Requirement 15.2, task 10.3):
  POSIX `sh`, five numbered steps with a status glyph each (`✓`/`✗`/`·`, ASCII
  fallback), the CLI's palette and banner under the same switches (`NO_COLOR`,
  `--no-color`, not a terminal; `--no-banner`), numbered menus with a default for
  the re-apply trigger and for running `floofy init` now, and a final summary with
  the next command. Without a terminal the defaults are taken and printed — a
  piped or scripted run never blocks — and every previous flag and environment
  variable still works; `FLOOFYCREW_TRIGGER` / `--trigger` pre-answer the menu.
  `floofy init --trigger KIND` (repeatable: `user-timer`, `path-wrapper`) installs
  only the named trigger kinds. The internal package ships `banner/`; the public
  one-liner embeds the art.
- Full-screen consent (Requirement 15.3, task 10.4): on a terminal `floofy init`
  shows the one-time warning on the alternate screen buffer — banner, the text
  wrapped and scrollable, a single `[ I AGREE ]` control; Enter agrees, Esc or `q`
  declines — and restores the previous screen either way. The acknowledgement is
  recorded exactly as before with `how: "screen"`; `--i-accept-the-risk` (`flag`)
  and the typed `I ACCEPT` prompt without a terminal (`typed`) are unchanged, and
  `--yes` still never answers. Typed governance confirmations stay typed.
- `floofy install <git reference>` — `ssh://<host>/<path>@<tag>` or
  `https://<host>/<owner>/<repo>[.git]@<tag>` (optional `#<subdirectory>`): a
  shallow clone at the tag with your own git credentials, the commit recorded,
  every manifest `files[]` entry verified, `floofy validate` on the checkout, and
  one extra consent line — *unlisted source: no curator review, no compatibility
  data* — confirmed by typing `I ACCEPT` or with `--accept-unlisted-source`
  (recorded); `--yes` never accepts it. An unpinned reference needs an explicit
  `--ref <branch|commit>`. Only `ssh://` and `https://` are accepted
  (Requirement 8.8).
- Link-based registry records (Requirement 8.9): a version record may carry
  `link{repo, tag, commit, manifestSha256[, path]}` and the checkout's
  `files[]{path, sha256, size}` instead of archive URLs; the client resolves it
  through the same clone and refuses a moved tag, a changed manifest or any
  differing file. `registry_tools build --resolve-links`, `validate-submission
  --record <id>@<version>`, `bootstrap --record link --link-source DIR --replace`;
  the internal registry lists mods by link to the tag `<id>-<version>` of the
  `FloofyCrew` package, which now ships `mods/<id>/`.
- The internal registry as a gated package (Requirement 8.10): rendered by
  `init_registry_repo.sh --edition internal` — a standard-library gate module and
  its tests (the package's own build is the merge gate: records, reserved names, a
  `contact` per mod, tag rule, manifest hash, signatures, `index.json` /
  `app-registry.json` / README mod table in sync, every link record cloned at its
  tag and compared), the adapter's build files and CR template, the vendored
  tooling under `vendor/`, `RENDERED.md`; `registry_tools readme [--check]`
  generates the README mod table.
- The source tier (Requirement 8.11): `unlisted` (a git reference, a path or an
  archive), `listed` (a registry record), `tested` (listed + a `tested` matrix
  cell for the running host) — in `floofy search` (the tier an install would
  have), the new `floofy info <id>` and `floofy list`, `floofy status`, the
  Loader's `/state` and `/registry` payloads and the manager page's tier badge.
  The install record (`.floofy/source.json`) and the audit row of an install
  carry `tier`, `commit` and the clone facts. Docs: `installing-from-a-link.md`.
- Branding: the FloofyCrew mark (fox-eared fluffy ghost with a brush tail on the
  sunset card), icon PNGs, `favicon.ico`, a `currentColor` mono mark and the
  horizontal wordmark, all generated from `branding/gen_logos.py`. The Loader app
  declares the mark as its store icon (`iconPath: art/icon.svg`), served by the
  host from `/apps/floofycrew/art/`; `build_loader_app.py` ships `art/`.

## 1.0.1 — 2026-09-20

A patch release for the internal edition's first real consumer: the shipped
1.0.0 client could not fetch its own registry from a fresh home, for two
independent reasons fixed here. Same `supports` list as 1.0.0;
`floofy.api_version` unchanged at 1.0.0.

### Fixed

- zipapp: the bundled data files are now read through `importlib.resources`
  (`floofy_core.resources.read_package_text`) instead of
  `Path(__file__).with_name(...)`, which cannot read a member of
  `dist/floofy.pyz`. In 1.0.0 the adapters' `default_sources()` and
  `registry_keys()` swallowed that error, so `floofy registry defaults` from the
  zipapp reported "no edition adapter supplies default registry sources" and no
  key was pinned; `anchors.json` and the JSON Schemas were equally unreadable
  (`floofy check`, `floofy validate`). Directory installs (the Loader app, the
  wheel, a checkout) were unaffected, which is why the release verification
  passed. `test_internal_package.py` now runs the built pyz on a bare
  interpreter and reads every bundled file through the CLI's code paths.
- internal edition: the ***REMOVED*** adapter's ***REMOVED*** opener now presents a
  `curl/8 FloofyCrew (unofficial KiroCrew mod manager)` User-Agent on every hop
  of the redirect chain (configurable as `identity.userAgent`). Sentry selects
  the login flow by agent: a `curl/…` agent receives the cookie-based SSO
  redirect the `***REMOVED***` jar satisfies, while any other non-browser agent is
  given a Kerberos Negotiate challenge the standard library cannot answer, so
  `floofy registry defaults` failed with HTTP 401 at `sentry.***REMOVED***`
  although `curl -b ~/.***REMOVED***/cookie` fetched the same URL. Verified against
  the pushed `FloofyCrewRegistry` with the pinned key `bf253dc3e34a0a1b`.
- `registry-tools`: `index.schema.json` now accepts the same host-version
  shapes as `compat.schema.json` (PEP 440 pre-releases such as `0.7.0rc5`
  were refused as compat cell keys, so a public insider row could not be
  folded into `index.json`), and `build --check` — the CI gate — no longer
  reports an index carrying the publisher's `floofycrew` release stamp as
  stale. Publisher-side only: the 1.0.0 client does not validate a fetched
  index against the schema and reads the corrected public index as is.

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
