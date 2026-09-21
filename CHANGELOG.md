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

### Changed

- The terminal banner is now the fox itself (Requirement 15.5): `branding/build_ascii.py`
  rasterises the new `branding/logo-nobg.svg` (the mark without its card) to a
  34×15-cell art in the sunset palette instead of parsing a hand-made HTML export.
  `floofy doctor` and `floofy --version` print it as a **left column**: the
  `floofy doctor — FloofyCrew …` header sits on the art's top row, the host,
  gateway and loader lines follow beside it, and the detail sections continue
  flush-left below (`Console.open_banner_column` / `close_banner_column`;
  `show_banner` still prints it on its own). Transparent cells carry no colour
  sequence; the transcript and `--json` are unchanged.

### Added

- The FloofyCrew App has a **sidebar page** (task 11.8; Requirement 16.1). `app.json`
  now declares `ui.pages[0]` (`/apps/floofycrew`, label `FloofyCrew`, a lucide
  fallback icon; the row's image stays `iconPath`, the branding mark) and
  `ui.sidebar` (Apps, order 10). The host lists an enabled app in the sidebar
  only when `ui.pages[0]` exists, so until now the App was reachable only through
  Library → FloofyCrew → details; one click on the sidebar row now opens the
  manager (the `ui.entry` AppHost, no per-page bundle).

## 1.1.1 — 2026-09-21

A patch on 1.1.0: the last three items of the terminal-experience package, which
landed after the 1.1.0 tag was cut — FloofyCrew's self-update and two `floofy
doctor` fixes found on a live 0.7.0.5 install. No API change (`floofy.api_version`
stays 1.1.0); same `supports` list as 1.1.0.

### Supports

| Edition | Channel | Host versions |
|---|---|---|
| internal | beta | 0.7.0.5 |
| internal | stable | 0.7.0.5 |
| external (public) | insider | 0.7.0rc5 |
| external (public) | stable | 0.6.0 |

### Added

- **FloofyCrew updates itself** (task 10.7; Requirement 7.7, 11.6). At most once a
  day the manager compares `floofy_core.__version__` with the newest release for
  the running edition and channel — the public edition reads the GitHub releases
  of the FloofyCrew repository, the internal edition the `FloofyCrew` package's
  `RELEASE.md` on `mainline` (both through the edition adapter's new
  `release_feed()` hook) — and shows one line in `floofy doctor`, `floofy status`,
  the interactive `floofy` (home screen and a menu item) and the App (host summary
  and Updates card; the Loader's `/state` and `/registry` gain `selfUpdate`) when
  the newer release's `supports` list names the running host version. The check is
  one HTTPS `GET` with nothing identifying in it, cached under
  `cache/self-update.json` (`{checkedAt, edition, channel, latest{version,
  supports, url, sha256}, notice}`), never blocks a command (a short timeout;
  failures cached as `unreachable` and silent for a day), is skipped with the new
  global `--offline` and switched off with `floofy config set updates.check false`
  (`floofy config get|set`, FloofyCrew's own `config.json`). `floofy self-update
  [--check] [--now] [--force] [--target PYZ]` installs the release through the
  edition's installer path with the same hash verification as a fresh install —
  `SHA256SUMS`, `floofy.pyz` and the Loader app archive (release assets, or the
  package's `dist/` blobs at the tag `v<version>`), every digest checked and the
  release notes' table held to `SHA256SUMS` — then swaps the installed
  `floofy.pyz` atomically (`floofy.pyz.new` renamed over it; the wrapper runs the
  new release from the next invocation) and stages the Loader app archive under
  `pending/self-update/`, installed through the host App Kit by `floofy apply`
  the next time no gateway runs (or with `--now`) — a running gateway is never
  disturbed. Every step writes an `op: self-update` audit row. Tests run against a
  fake release endpoint on loopback in both shapes.

### Fixed

- `registry_tools.bootstrap` read a `__pycache__/*.pyc` beside the internal
  adapter's `registry-package/setup.py` (left by whatever imported the file in
  place) as a UTF-8 template and the internal registry render failed with
  `UnicodeDecodeError`. Every tree walk that renders or archives files now goes
  through `source_files()`, which skips byte-code caches (the mod archiver already
  did); a test plants such a cache and renders the gated package.
- `floofy doctor` printed "early shim: Loader app not installed" on an install
  where the app was present and enabled (task 10.9; Requirement 3.2, 7.4).
  `floofy_core.loaderapp.early_install_module` executed the installed
  `floofy_loader/early_install.py` through `module_from_spec` + `exec_module`
  without registering it in `sys.modules`; the file's `@dataclass` classes (under
  `from __future__ import annotations`) then raised `AttributeError` inside
  `dataclasses._is_type`, the exception was swallowed and the fallback import
  failed too. The module is now registered before it runs (`load_module_from_file`,
  popped again on failure, a previous entry restored), so `doctor` grades the
  early shim per payload from the shim the app actually ships. The same
  `module_from_spec` pattern in the test-side module loaders (the packaging,
  edition-package and Forge scheduler tests) registers its module too; the Loader
  runtime and the early shim already did.
- `floofy doctor`, `status`, `verify` and the Patcher's dormant verdict against a
  live gateway (task 10.8; Requirement 5.7, 6.4, 7.4). The host adds `version` to
  `/api/health` only for a *direct-local* caller — a loopback TCP peer without
  forwarding headers and with a served `Host`; a unix-socket peer has no remote
  address, so over the dashboard socket the answer is always the bare
  `{"ok": true}`. The probe (`floofy_core.gateway.probe_version`) now retries once
  over `127.0.0.1:<port>` with `Host: 127.0.0.1:<port>` — the port taken from the
  socket's own name `dashboard-<port>.sock` — and keeps the socket for every other
  call. Before, `doctor` printed "gateway: not running" against a healthy gateway
  and every payload's `dormant` flag was computed from a missing served version.
  `doctor` now distinguishes *running, serving X* (with `via: socket |
  loopback-tcp`) from *running, served version unknown* (a `--test-mode`
  `dashboard-0.sock`, a forwarding proxy) and *not running*; the `--json`
  `host.gateway` object gains `via` and `detail`. The test double `FakeGateway`
  mimics the real host instead of an idealised one: in socket mode it binds both
  the unix socket (named after the real loopback port) and the TCP listener,
  answers the bare payload over the socket, and discloses identity over TCP only
  without forwarding headers and with a served `Host`.

## 1.1.0 — 2026-09-20

The second minor: the terminal experience (styled output, the banner, the
restyled installers, the full-screen consent, the interactive `floofy`), the
**FloofyCrew App** inside KiroCrew with every CLI action and every CLI
confirmation, mods' own settings pages (`ui` parts and `floofy.mod(id)`),
installs from a git reference, link-based registry records and the internal
registry as a gated package, the source tier, and the second first-party mod,
`settings-demo`. `floofy.api_version` is **1.1.0** (additive: `floofy.mod(id)`,
`ctx.routes`, the `ui` part kind; nothing deprecated, nothing removed). Same
`supports` list as 1.0.x (below); the Forge's matrix rows were re-run against
this release on the build host.

### Supports

| Edition | Channel | Host versions |
|---|---|---|
| internal | beta | 0.7.0.5 |
| internal | stable | 0.7.0.5 |
| external (public) | insider | 0.7.0rc5 |
| external (public) | stable | 0.6.0 |

### Added

- The **FloofyCrew App** (Requirement 16; tasks 11.1–11.4): the Loader app is the
  manager App — display name `FloofyCrew`, the branding mark as its store icon
  (`loader-app/art/icon.svg`, kept byte-identical to `branding/logo.svg` by a
  test), the always-visible unofficial banner, the navigation Mods (landing) ·
  Registry · Profiles · Doctor · Audit · Settings. Every CLI action is a typed
  Loader route under `/api/apps/floofycrew/` (`floofy_loader/app_routes.py`:
  install from a registry record, a path, an archive or a git reference, enable,
  disable, uninstall, update check / update / update all, yeet and restore,
  registries add / remove / refresh / trust / defaults, profiles save / use /
  export / import, status, doctor, search, info, the audit tail, vanilla,
  consent) that spells the sub-command from a typed body and runs it through the
  same parser, the same handler and the same audit row as the CLI (`actor:
  app`); no route can spell an answer flag, and `POST /cli` keeps only the
  read-only commands. Confirmations follow a **409 protocol**: a question the
  request does not answer ends the run before anything mutates and the App shows
  it — the one-time consent as a modal with the CLI's own text and a single
  `[ I AGREE ]` control (Enter agrees, Esc declines; recorded `how: app`), the
  install disclosure (parts and seams, payload impact, declared hosts and
  credentials, flags, host verdicts, source tier, the unlisted-source line) with
  the exact yes/no prompt and the **staged-vs-apply-now** choice (staged by
  default, Requirement 16.5), a typed text field per governance-altering target
  compared byte for byte by the Loader, the unlisted-source `I ACCEPT` item and
  the loosening warning for `--allow-unsigned`; a declined question re-posts
  nothing and leaves no audit row. A capability the App lacks (`floofy init`,
  `apply`/`restore`/`verify`, `vanilla` when the App cannot restart the gateway)
  is shown with its command, never hidden (Requirement 16.2). The Registry page
  browses the cache with source tiers and compat badges, installs from a record
  or a pasted git reference, lists every available update with the record's
  release-notes link (`changelog`, new in the index schema) and updates one or
  all mods through the same `floofy update` path (Requirement 16.6).
- Mod **`ui` parts** and **`floofy.mod(id)`** (Requirement 16.4; task 11.5): the
  tenth part kind — `{kind: ui, side: spa, path, entry, title[, icon]}`, an ES
  module the App mounts as the mod's own page under Mods, same-origin
  (`GET /ui/mods/{id}/<path>` serves an active mod's manifest-listed files),
  inside an error boundary that shows a throwing page with a Disable button and
  never takes the manager down; inert until opened, so a `ui` part alone never
  lands a mod disabled. The page's `api = floofy.mod(id)`
  (`spa-host/src/modapi.mjs`): `config.get()/set(patch)` persisted in the mod's
  `.floofy/config.json` (`GET`/`PUT /mods/{id}/config`, key-by-key merge, `null`
  deletes, 64 KiB cap — the same store the Python side reads as `ctx.config`),
  `routes.list()/fetch(path)` for the mod's own backend routes, `theme.tokens()`
  / `theme.current()`, `state()`, `log`. On the Python side `ctx.routes.add(method,
  path, handler)` inside `activate(ctx)` registers a backend route served at
  `/mods/{id}/api/<path>` (`floofy_loader.modroutes`; sync or async handler, a
  raising handler is that request's 500 and never a fault, registrations unwind
  with the mod). Validator rules for the part (`entry`/`icon` listed in `files[]`
  and under the part's directory), `floofy new ui`, the tenth example mod, the
  manifest reference and the SPA API page.
- The second first-party mod, **`settings-demo`** (task 11.6): a `ui` settings
  page (a greeting field and a toggle through `config`, a button calling the
  mod's backend) plus a `python-hook` registering `GET`/`POST echo`, which
  answers with the stored config — the page and the hook demonstrably share one
  store. MIT, no network; lands disabled (the hook); needs FloofyCrew ≥ 1.1.0.
  Shipped in the `FloofyCrew` package (tag `settings-demo-1.0.0`) and listed as
  the internal registry's second link record; `scripts/install_both_editions.py`
  now installs every first-party mod on both editions from one deterministic
  archive each and probes the hook's routes.
- Tests for the App (Requirement 16.7; task 11.7): route contracts against the
  CLI's own audit rows (`loader-app/tests/test_app_routes.py`), the `ui` part's
  isolation (`test_ui_part.py`, Playwright `test_5c_mod_pages_are_isolated`) and
  the headless smoke `test_6_manager_app_smoke` — open the App, add a local
  signed registry through the Registry page, install `settings-demo` from its
  record through the confirmation modal with *apply now*, enable it, open its
  settings page, set a value and see the mod's backend echo it, disable the mod
  through the page's own button.
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
- Interactive `floofy` (Requirement 15.4, 15.6, task 10.5): bare `floofy` on a
  terminal opens a keyboard-driven interface (curses, standard library) — the
  banner, a host summary (edition, version, channel, Loader state, consent) and
  menus for mods (list with state and source tier; info, enable, disable, update,
  uninstall, yeet/restore, install from the registry or from a git reference),
  registries (add, remove, refresh, trust — `--allow-unsigned` behind the same
  warning and an explicit accept item), profiles (save, use/switch, export,
  import), doctor and check-for-updates. Every action runs the same handler as
  its subcommand (`actor: tui` in the audit row) and shows its output in a
  scrollable pane; the one-time consent is the full-screen `[ I AGREE ]` frame,
  a governance-altering target stays a typed text field. Arrows or `j`/`k`,
  Enter, Esc/`q`, `/` to filter; no mouse. Without a terminal bare `floofy`
  prints the usage as before; when curses cannot start (`TERM=dumb`, no terminfo)
  it prints the usage plus a one-line hint.
- Docs: `docs/terminal.md` — using `floofy` at the terminal: the styling
  switches, the banner, the install scripts' steps and menus, the full-screen
  consent, the interactive mode's keys and the guarantees automation can rely on
  (Requirement 14.3, task 10.6); tests for the whole terminal package
  (`test_cli_style.py`, `test_banner.py`, `test_consent_screen.py`, `test_tui.py`,
  `packaging/tests/test_install_scripts.py`; Requirement 13.3).
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

### Changed

- Installs, updates and `profile use` are **staged** into `pending/` while the
  gateway runs on every surface, the App inside the gateway included (the
  in-gateway exemption of `cmd_mods` is gone); `now: true` / `--now` places the
  mod and reloads the Loader in-process, and `POST /reload` is the *apply now*
  for what is already staged (Requirement 7.6, 16.5).
- The Loader's reload from a request handler boots on the event loop and runs
  the Patcher pass in a worker thread (`BootDeps.defer_patches`,
  `LoaderRuntime.reload_async`), so the host's loop watchdog keeps its heartbeat
  through the file work.
- `ModConfig` re-reads `.floofy/config.json` when another writer changed it, so
  `ctx.config` and `floofy.mod(id).config` always agree.
- `index.schema.json`: a version record's `kinds` admits `ui`; `changelog` (an
  https URL of the release notes) is an optional field of a version record,
  indexed from `release.json` or the manifest's `links.changelog`.
- Two-track measurement on this tree: 88.4 % of shipped files (96.4 % of bytes)
  byte-identical across editions; both first-party mods travel as one archive
  each (`docs/two-track.md`).

### Not in this release

- `floofy self-update` and the daily release check (task 10.7), the
  `gateway.served_version` unix-socket probe fallback (10.8) and the
  `early_install_module` registration fix (10.9) are still open; the App's
  self-update notice renders only once the Loader publishes one.
- The publish steps of both editions remain the maintainer's
  (`.kiro/specs/floofycrew-modding-ecosystem/BLOCKERS.md`).

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
