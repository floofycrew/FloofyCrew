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

### Fixed

- **One canonical spelling per payload file** (`floofy_core.payloads.make_payload`,
  `floofy_core.deploy`). On a host whose `$HOME` is a symlink (a common
  clouddesk layout, `/home/x -> /local/home/x`) the gateway spelled payload
  paths through the symlink while a shell spelled them canonically. The
  deployment manifest, keyed by raw string, collected one entry per spelling
  for the same file; drift then classified the other process's write as
  `user-edited`, and inside a single gateway apply `index.html` was committed
  as two independent work items — the import-map commit (derived from the
  pre-patch original) silently dropping the boot-script ops written a moment
  earlier. Net effect: the first-frame theme sheet and the SPA-host loader tag
  vanished on every gateway restart, so custom themes flashed the stale
  pre-hydration theme and runtime parts never loaded. Payload roots are now
  canonicalized at discovery, every manifest lookup and record goes through
  `canonical_path`, and loading a manifest heals pre-existing duplicate
  spellings (newest record kept, first-recorded original hash preserved).

### Added

- **`display-comfort` 1.0.0**, a quality-of-life mod answering the two
  most-asked-for display requests upstream: a persistent chat text scale
  (50–250 %, every message bubble, optionally the composer too) and an
  arbitrary page zoom (25–300 % in 1 % steps, not just the browser's fixed
  stops). A `ui` settings page with live-preview sliders persisting through
  `floofy.mod(id).config` (no python-hook — the Loader's own config route),
  a `spa` runtime part applying one custom property on `<html>` that the
  mod's stylesheet turns into `zoom` on `[data-testid="message-bubble"]`
  (explicit rem/px utility classes would ignore an inherited font-size),
  and a `spa` boot part with a baked stylesheet plus a localStorage cache so
  the chosen sizes paint on the first frame — a reload never flashes the
  defaults. Fail-open everywhere: no settings, an out-of-range value, or a
  renamed host marker leaves the dashboard stock.

- **Update reminders without a terminal** (Requirement 7.7 extended). The
  re-apply trigger's `floofy apply --if-changed` — the one run that happens
  unattended, outside the gateway, with the user's own credentials — now also
  performs the daily FloofyCrew release check (its own 24 h cache, so the
  hourly trigger costs at most one request a day) and a registry-source
  refresh at most once a day (`floofy_core/freshness.py`), so the App's
  release banner and per-mod update rows stay fresh for users who never open
  a terminal. Best-effort: an offline desk or a failed feed never fails the
  trigger; a registry the user never fetched themselves is not fetched; and
  `updates.check false` disables both. The Loader still performs no network
  I/O itself. The Mods landing page now carries the mod-updates notice too
  ("N mod update(s) available … Review on the Registry page") — before, only
  the Registry page showed them.

## 1.3.1 — 2026-09-22

A patch on 1.3.0 that removes ceremony from installing mods. A git reference
no longer needs a tag: a bare `ssh://…` / `https://….git` installs the
repository's **default branch**, with the version and host compatibility read
from the checkout's `floofy.json` — never derived from a tag or commit hash —
and the resolved commit recorded (`@<tag>` and `--ref` stay for pinning). A
confirmed install now lands **enabled**: the install confirmation is the
consent, so the separate `floofy enable` step (and the "enable code parts
right away" jargon) is gone; `--disabled` / the App's "install switched off"
checkbox land a mod off on request. In the App, the manual Yeet button is
retired (Disable/Uninstall cover it; the automatic quarantine and Restore
stay), "Check for updates" shows a busy state and a one-line verdict, and the
active tab no longer grows a bottom border after a tab switch. No API change:
`floofy.api_version` stays 1.2.0.

### Supports

| Edition | Channel | Host versions |
|---|---|---|
| internal | beta | 0.7.0.5 |
| internal | stable | 0.7.0.5 |
| external (public) | insider | 0.7.0rc5 |
| external (public) | stable | 0.6.0 |

### Changed

- **A confirmed install lands enabled** (Requirement 11.7 reworded). The
  install confirmation *is* the consent, so the separate `floofy enable` step —
  which users (and the owner) kept forgetting — is gone. `--disabled` (CLI) and
  the App's plain-language "install switched off (enable it later)" checkbox
  land a mod off on request; the jargon "enable code parts right away" flag and
  checkbox are retired (`--enable` is still accepted and ignored so pre-1.3
  automation keeps working). The disclosure's `landsDisabled` field is now
  `codeParts` — what the confirmation covers, not a promise about the flag.
- **A git reference no longer needs a tag** (Requirement 8.8 reworded). A bare
  `ssh://…` / `https://….git` reference installs the repository's **default
  branch** (its HEAD); the mod's version and host compatibility are read from
  the checkout's `floofy.json`, never derived from a tag or commit hash, and
  the install is recorded with the commit the clone resolved to. `@<tag>` and
  `--ref <branch|commit>` stay available to pin a point on purpose
  (`UnpinnedReference` is no longer raised).
- **The manual Yeet button is gone from the App.** Disable and Uninstall cover
  the user-facing need; the automatic per-host-version quarantine, its Restore
  button and the `floofy yeet` command are unchanged, and the quarantine card
  now says in plain language when mods land there (and names the terminal
  command for parking one by hand, Requirement 16.2).
- **Registry › "Check for updates" reports what it did**: the button reads
  "Checking…" with a busy note while the sources are asked, then a one-line
  verdict — how many updates are available, "everything is at the newest
  version known to work here", or the error when the check failed.

### Fixed

- **The active tab grew a bottom border after any tab switch.** React writes
  only the changed inline-style keys, so the 4-side `borderColor` write on
  activation repainted the bottom while `borderBottomColor` (unchanged,
  transparent) was not re-written. Border colours are now spelled per side in
  both tab states; the active tab never paints a bottom border, matching the
  first render.

### Added

- **License and third-party notices.** The repository gains its `LICENSE` (MIT)
  and `NOTICE.md`: the theme-pack validation (`floofy_core/themes.py`) is a port
  of KiroCrew's theme validator, and patch anchors/fingerprints quote short
  verbatim excerpts of KiroCrew source — KiroCrew is Apache License 2.0, so the
  attribution from its NOTICE and the full license text (`LICENSES/Apache-2.0.txt`)
  now ship in **every** artifact: the zipapp, the wheel (`dist-info/licenses/` +
  `License-File` metadata), the Loader app tree and both release trees. The
  de-Amazon check skips LICENSE/NOTICE files (legal notices are reproduced
  verbatim; upstream's public attribution is not an internal identifier).
- **The public export is a mechanism, not a convention**
  (`scripts/export_public.py`; Requirement 10.2). The public (GitHub) repository
  receives a filtered export, and this script is the only sanctioned way to
  produce it: copy an explicit include list (anything unnamed never leaves),
  drop the internal-only mods (`mochi-pet-zoom-fix`, `rimuru-branding`) and
  their `mods/README.md` rows, scan the **whole** exported tree with the
  de-Amazon checker against a separate justified allowlist
  (`scripts/public-export-allow.txt`, every line must name an existing exported
  path), and refuse to write into any git clone with an internal remote (the
  markers come from the scanner's own denylist). It never pushes. CI's
  `de-amazon` job now runs `export_public.py --check` beside the core scan, and
  `floofy-core/tests/test_public_export.py` proves the boundary: the include
  and exclude sets, the row pruning, a planted leak failing the scan, the
  internal-remote and non-empty-target refusals.
- **Pre-push AI review of the public export**
  (`packaging/internal/review_public_export.py`; internal-only, never
  exported). The advisory second net over the deterministic gate: a language
  model reads exactly the delta a `git push` of the public clone would publish
  (uncommitted state; new files in full, tracked changes as diffs, binaries by
  name) and flags what a substring denylist structurally cannot — unknown
  hostnames, people, codenames, ticket-shaped ids, secret-shaped strings. The
  deterministic scan is re-run first and stays the blocking gate; a marker-less
  or malformed model answer is an ERROR, never a pass; findings are printed
  for a human to judge and recorded in the clone's
  `.git/floofycrew-ai-review.json`. Runs `kiro-cli` headless (v3 engine, model
  pinned, prompt on stdin so argv size caps never truncate a delta), chunks
  large deltas on line boundaries, and refuses clones with internal remotes.
  `export_public.py --out` now prints the review command as the next step.

### Changed

- The first-party mods' `LICENSE` and manifest `authors` credit the author by
  name instead of an internal account alias; `packaging/README.md` and the
  changelog no longer name internal systems, so the full public export set
  scans clean.
- The export now strips **internal-audience** content, not just internal
  identifiers: regions of shared files wrapped in the exporter's
  internal-only start/end markers (or tagged with its single-line marker,
  which a markdown table row or code-block line carries inline) are dropped
  from the public tree. The README's internal-edition install block, the
  internal-only layout rows and the internal build command no longer confuse
  public readers; unbalanced markers fail the export. The marker strings are
  assembled at runtime in the exporter (and never spelled in prose — this
  entry included) so the stripper cannot eat its own shipped source.

## 1.3.0 — 2026-09-22

A minor on 1.2.0: `floofy.api_version` rises to 1.2.0 with one additive API,
`floofy.mod(id).dialog` — in-document confirm/prompt/alert/form dialogs for a
mod's page (the KiroCrew desktop shell has no `window.alert`/`confirm`/`prompt`,
so pages that used them did nothing there), and `custom-themes` 1.0.1 asks every
question through it. Registry link records are now pinned by **commit** instead
of a git tag (`tag` stays as legacy), with `registry_tools record` to write and
refresh them. The manager App gets squared corners on one 2 px radius, a footer
with the author, a dismissable unofficial banner (a per-browser preference; the
footer keeps the statement), and both READMEs now lead with what FloofyCrew does
and how to install it. Same `supports` list as 1.2.0.

### Supports

| Edition | Channel | Host versions |
|---|---|---|
| internal | beta | 0.7.0.5 |
| internal | stable | 0.7.0.5 |
| external (public) | insider | 0.7.0rc5 |
| external (public) | stable | 0.6.0 |

### Added

- **`floofy.mod(id).dialog`** — `confirm(text, opts)`, `prompt(text, opts)`,
  `alert(text)` and `form({title, text, fields})`: in-document dialogs for a mod's
  page, rendered as the same overlay the App uses for its install confirmations
  (Enter answers, Escape or the backdrop cancels, fields validate, one dialog at a
  time). The dashboard also runs inside the KiroCrew desktop shell, which has no
  `window.alert` / `confirm` / `prompt`: a page that used them did nothing there.

### Fixed

- **`custom-themes` 1.0.1**: New blank theme, adopting or starting from a preset,
  Import, Delete, Uninstall, the unsaved-changes and copy-palette questions all
  asked through the browser's pop-ups and so did nothing in the KiroCrew app;
  they now use `api.dialog` (name and slug in one dialog, the slug following the
  name until edited). On an older Loader the page says it needs `api_version`
  1.2.0 instead of silently failing. The App's own Yeet button asks through its
  modal too, no longer `window.confirm`.

### Changed

- **Manager App: squared corners, a footer, a dismissable banner.** Every corner
  of the App sits on one small radius (`RADIUS`, 2 px): cards, the banner, tabs,
  buttons, inputs, badges (no more pills), the code chips and the modal. The page
  ends with a footer — a rule, the unofficial statement, the running version and
  `author: Oscar Tseng` — with 6 rem of room below it. The unofficial banner at
  the top carries "Don't show this again": a browser preference
  (`localStorage` `floofycrew.banner.hidden`) that hides the banner on that
  browser; Settings › About shows it again, and the footer keeps the unofficial
  statement on every page, so hiding the banner never hides what FloofyCrew is
  (Requirement 11.8).
- **Link records no longer need a git tag.** A registry link record now pins
  `link{repo, path, commit, manifestSha256[, ref]}`: the mod lives under its
  directory on the repository's default branch, the registry records the commit it
  curated, and the client fetches exactly that commit (`git fetch --depth 1 origin
  <sha>`, then a shallow checkout) before comparing the manifest hash and `files[]`
  with the record. `tag` is optional and legacy: records that carry one and no
  `commit` still resolve at the tag. The index schema, the gated-package
  templates, the registry `README` renderer and `validate-submission` follow the
  new shape.
- **`registry_tools record REPO ID`** writes or refreshes a link record from a
  clone of the mod repository's default branch (`--ref` for another branch or
  commit, `--refresh` to re-pin an existing record); `registry_tools bootstrap`
  seeds its records from `--link-source` at `--link-ref` (default `HEAD`) instead
  of `<id>-<version>` tags, and `floofy_core.gitsource.GitRef` gains
  `at_commit()`, `default_branch()` and `is_default_branch()`.

## 1.2.0 — 2026-09-21

A minor on 1.1.5: a `patch` descriptor may now target `electron:<member>` — one
file inside the desktop shell's `app.asar` — the new descriptor target form that
makes this release a minor, and `mochi-pet-zoom-fix` 1.0.0 is the first mod to use
it. Also new: the `custom-themes` mod (a theme editor inside the FloofyCrew App and
the layer theme mods build on; `rimuru-branding` 2.0.0 is now a pure theme pack on
top of it), Restart KiroCrew and Update & restart from the App, and fixes for the
early-activation list (`early.json` is now written), the early shim on a symlinked
home, the Loader app grant's local-source shape and the post-swap Loader step. No
API change (`floofy.api_version` stays 1.1.0); same `supports` list as 1.1.5.

### Supports

| Edition | Channel | Host versions |
|---|---|---|
| internal | beta | 0.7.0.5 |
| internal | stable | 0.7.0.5 |
| external (public) | insider | 0.7.0rc5 |
| external (public) | stable | 0.6.0 |

### Added

- **`custom-themes`**, a new first-party mod: a theme editor for KiroCrew inside
  the FloofyCrew App, and the layer theme mods build on. A theme is a host theme
  pack (palette, name, emoji, bot name, logo, favicon, fonts, `overrides.css` —
  installed through the ported host validator, listed in Settings → Display,
  working without FloofyCrew) plus a FloofyCrew layer of **extra CSS** with any
  selector, scoped to `html[data-theme="custom-<slug>-dark|light"]` by the mod's
  compiler and applied by the mod itself — the rules the host's runtime allowlist
  would drop. The editor page (Palette with the 56 host tokens per mode, Look,
  Pack, Branding tabs; live preview re-scoped to `html[data-theme]`; Save,
  Install to host, Use this theme, Export, Import, Duplicate, Uninstall, Delete)
  starts a theme from **the running host's own built-in themes**: the hook reads
  every `[data-theme=<slug>-dark|light]` palette in the payload's stylesheets, the
  theme's decorative rules and their `@keyframes`, and the sidebar label/emoji —
  edition-neutral and current on any host build, with no theme data copied into
  the repository. The first frame comes from the mod's boot hook: a warm client
  inlines the sheet the runtime part cached, a cold client gets a render-blocking
  link to the sheet the hook regenerates for every installed custom theme
  (`<Loader app>/ui/boot/custom-themes/themes.css`), measured in a real browser
  (`test_playwright.py::test_1`, cold and warm). The theme-reset import-map patch
  moved here from `rimuru-branding`, since every custom theme needs it. Routes
  under the mod's api root: `themes` CRUD, `install`/`uninstall`, `export`/`import`
  (one JSON file, `format: floofy-theme/1`, assets base64), `assets` uploads within
  the host's caps, `presets`, `css`, `preview`, `refresh`, `status`.
- **Electron shell patches** (Requirement 5.11 — the new target form this minor
  carries): a `patch` descriptor may target `electron:<member>` — one file inside
  the desktop shell's `app.asar`. The Patcher finds the shell by walking up from
  the payload's `kiro_crew` package (`floofy_core.electron`), reads the member,
  applies the ops, re-lays the archive with the member's integrity record
  recomputed (`floofy_core.asar`, standard library only) and commits `app.asar` as
  one file through the usual backup, manifest, drift and restore path (the
  lost-manifest sweep now covers the shell's resources directory). A gateway-only
  payload skips it as `NotApplicable`; a shell whose Electron fuses enforce asar
  integrity is skipped as `IntegrityLocked` rather than left unable to launch.
  Declare such parts with `side: electron`; `floofy validate` warns on a mismatch
  (`SideMismatch`). Docs: `patching.md` → "Electron shell targets".
- **`mochi-pet-zoom-fix` 1.0.0**, the first desktop-shell mod: gives the Mochi pet
  overlay its own session partition and pins its zoom to 100 %, so the pet no longer
  inherits the dashboard's Zoom Level (scaled, off-screen, undraggable at any zoom
  other than 100 %). Two `electron:` patches (`mochi/petOverlays.js` and the older
  `crew-companion/petOverlay.js`), fingerprints verified once each against the
  upstream sources.

### Changed

- **`rimuru-branding` 2.0.0** is a pure theme pack: the `theme` part and
  `dependsOn: {"custom-themes": ">=1.0.0 <2.0.0"}`; its boot part, baked CSS and
  reset-guard patch are gone. The first-frame parity it carried is now proven on
  the `custom-themes` path. `scripts/install_both_editions.py` builds and installs
  all three first-party mods (`custom-themes` first).

- **Restart KiroCrew** from the manager App. A red button in the App's header,
  on the Mods page's "staged" line and in Settings asks once (the App's `yes-no`
  modal through the 409 protocol; nothing is answered for the user) and then
  `POST /host/restart` hands the restart to the host's own `kirocrew restart`,
  started detached with the managed host home in `KIROCREW_HOME` — service-aware:
  a systemd/launchd unit is restarted by its service manager, a foreground
  gateway is stopped and a detached replacement started and verified. The App
  polls the Loader's `/health` and reloads the dashboard when the new gateway
  answers; every restart is an `op: host-restart` audit row (`actor: app`), a
  missing launcher is a 422 naming the terminal command. The CLI keeps its
  stance: no `floofy` command restarts a live gateway by itself.
- **Update & restart** when a newer FloofyCrew supports this host: the notice on
  the Mods page and the Registry's Updates card carries a button that runs the new
  typed route `POST /self-update {now: true}` (`floofy self-update --now`; the
  CLI's own "Install FloofyCrew X over Y?" question in the modal; no `--force`,
  `--check` or `--target` reachable) and then the restart above. A staged Loader
  app update gets an "Install the staged update & restart" button.
- Manager App polish: pages are columns of cards with a 1.25 rem gap and 4 rem of
  room under the last one; a tab clicked once no longer keeps a grey frame after
  another tab is selected (the tab border is spelled as longhands in both states,
  so React's style diff never unsets it); Registry › Sources is a table (Source ·
  Trust · Signature policy · Last refresh · Actions) instead of stacked lines;
  the Profiles page opens with one line saying what a profile is; switching
  sections re-reads the Loader so a page never shows what a terminal changed
  meanwhile. Settings names the host's verb correctly (`kirocrew restart`, not
  `kirocrew gateway restart`, which does not exist).

### Fixed

- The Loader app grant is recorded in the host's **local-source shape**. The
  manager wrote the app name into `agent.apps_trusted` alone; the host treats such
  a name-only grant as legacy — honoured only while an app with local provenance is
  installed under that name, and refused for a fresh install as "execution trust
  predates repository binding" (`apps/execution.py`). A Loader reinstall is exactly
  a fresh install between its uninstall and install steps, so the 1.1.4 → 1.1.5
  update on the test box had its restored grant rejected and `kirocrew app install`
  refused, leaving the App page broken. `grant_app_trust` now writes what the
  dashboard's "trust this app" writes for a directory/archive install: the name in
  `agent.apps_trusted` **and** `agent.apps_trusted_local`, with no
  `agent.apps_trusted_repositories` entry (a stale one is dropped). The test double
  now refuses a fresh install on a name-only grant and requires the binding to
  enable, like the host.
- After the zipapp swap, the Loader app step is run **by the release just
  installed**: `<interpreter> <new pyz> self-update --install-staged --json` as a
  subprocess with the same host home, launcher and roots, its result relayed.
  Everything after the swap otherwise runs on the old release's modules, which is
  why two consecutive updates each re-ran the previous release's Loader-step bug.
  A swap target that is not a runnable zipapp falls back to the in-process step.
- **The early-activation list was never written.** `early.json`, the file the
  early shim reads, was documented as "written by the manager" but no code path
  wrote it, so `"early": true` python-hook parts never ran before platform
  bootstrap. `floofy_core.modstore.sync_early_list` now regenerates it (or
  removes it when nothing early is enabled) after every install, enable, disable,
  uninstall and dev link, on host-change parking/unparking, after the Loader's
  pending apply, and at every Loader boot. Found installing the first mod with an
  early part on the test box.
- **`early()` and `activate()` did not share one module on a symlinked home.** The
  Loader compared the shim's recorded `__file__` with its own resolved path as
  strings; where `/home/me` is a symlink to `/local/home/me` they differ, the
  module was imported a second time and `activate()` never saw what `early()`
  recorded. Both sides now compare real paths.

## 1.1.5 — 2026-09-21

A patch on 1.1.4 closing the two `self-update` reporting gaps found while updating
the test box through it: a release whose package tag is not pushed yet fails
cleanly before any change instead of with a traceback, and a Loader whose enable
the host refused is reported as installed-but-disabled (with what to run) rather
than as updated. No API change (`floofy.api_version` stays 1.1.0); same `supports`
list as 1.1.4.

### Supports

| Edition | Channel | Host versions |
|---|---|---|
| internal | beta | 0.7.0.5 |
| internal | stable | 0.7.0.5 |
| external (public) | insider | 0.7.0rc5 |
| external (public) | stable | 0.6.0 |

### Fixed

- After installing a staged Loader app, `self-update` reported "Loader app updated
  … runs at the next gateway start" even when the host had just refused
  `kirocrew app enable` (the 1.1.3 → 1.1.4 update on the test box: the Loader
  install step runs on the code of the release being replaced, which predates the
  grant-restore fix, so the grant was lost and the enable refused, while the last
  line still said "updated"). The outcome is now judged on *enabled*, not
  *installed*: the stage is consumed either way, but the line says the Loader is
  installed but disabled and to run `floofy init`, the marker records
  `outcome: disabled`, and the audit row says `disabled` with the reason.
- `floofy self-update` died with a traceback when the feed named a release whose
  assets could not be fetched (seen for 1.1.4 on the internal edition: `RELEASE.md`
  on `mainline` already said 1.1.4, but the assets are raw blobs at the tag
  `v1.1.4`, which had not been pushed, so `SHA256SUMS` was a 404). Nothing had
  changed — `SHA256SUMS` is fetched before any download — and the command now says
  so, names the missing tag when the 404 is at the tag URL, and records an
  `unavailable` audit row instead of crashing.

### Changed

- The internal release recipe (task 10.11, `tools/install-test-box.sh` readiness
  gate, BLOCKERS 11.7) treats the package tag `v<version>` as part of the release:
  a package commit on `mainline` without it is not released yet.

## 1.1.4 — 2026-09-21

A patch on 1.1.3 with three fixes found while updating the test box from 1.1.2 to
1.1.3 through `floofy self-update` itself: the Loader app reinstall no longer loses
the host's `apps_trusted` grant, `self-update --now` installs a Loader stage left by
an earlier run, and the first-frame boot template ships inside the zipapp. No API
change (`floofy.api_version` stays 1.1.0); same `supports` list as 1.1.3.

### Supports

| Edition | Channel | Host versions |
|---|---|---|
| internal | beta | 0.7.0.5 |
| internal | stable | 0.7.0.5 |
| external (public) | insider | 0.7.0rc5 |
| external (public) | stable | 0.6.0 |

### Fixed

- Reinstalling the Loader app left it **installed but disabled** on a host that
  had granted it: `kirocrew app uninstall` also removes the app from
  `agent.apps_trusted` (host `apps/manager.py`, "Remove *name* from
  agent.apps_trusted"), so every `floofy init --reinstall-loader` came back
  ungranted and the self-update Loader install — which never offers the grant —
  ended with `kirocrew app enable` refused. A grant that was in place before the
  uninstall is now restored right after it, without asking (restoring the user's
  earlier decision is not a new one), audited as `grant-apps-trusted: restored`;
  a host that never granted the app is left as it was. The test double's
  uninstall now revokes the grant like the real host.
- `floofy self-update --now` did nothing when the zipapp was already current
  (found on the test box: the TUI's self-update swapped the zipapp to 1.1.3 while
  the gateway ran and staged the Loader app, as designed; the later `floofy
  self-update --now` then only *described* the stage again, and the dashboard kept
  showing the old Loader). The up-to-date branch now installs a pending stage with
  `--now`, or when no gateway runs, through the same App Kit reinstall path the
  swap uses (`install_stage_now`), and says the new Loader runs at the next gateway
  start. A running gateway without `--now` is still never disturbed.
- `floofy apply` from the installed zipapp warned "boot.mjs template not found"
  and dropped the first-frame boot part, so themed installs flashed the stock look
  at first paint. The lookup was relative to `__file__` — a checkout's
  `spa-host/src` or the Loader app's `ui/`, neither of which exists beside
  `~/.local/lib/floofycrew/floofy.pyz`. The template now ships as package data
  (`floofy_core/boot.mjs`, a byte-identical copy of `spa-host/src/boot.mjs`
  pinned by a test, read through `read_package_text` like the banner), with the
  filesystem candidates as fallbacks; a test builds the zipapp into a bare
  directory and renders the boot part from it.

## 1.1.3 — 2026-09-21

A patch on 1.1.2: the mark is reframed for the dashboard's circular sidebar mask (the
App Store icon updates with it), the terminal banner gains the "FloofyCrew" wordmark
beside the fox — in the interactive `floofy`, over the `doctor`/`--version` header and
in both installers from 110 columns — and `doctor`/`self-update` explain the quiet
"up to date" line correctly when running ahead of the feed. No API change
(`floofy.api_version` stays 1.1.0); same `supports` list as 1.1.2.

### Supports

| Edition | Channel | Host versions |
|---|---|---|
| internal | beta | 0.7.0.5 |
| internal | stable | 0.7.0.5 |
| external (public) | insider | 0.7.0rc5 |
| external (public) | stable | 0.6.0 |

### Fixed

- `floofy doctor` and `floofy self-update` explained the quiet "up to date" line
  wrongly when the running FloofyCrew is *newer* than the feed's newest release
  (task 10.12; Requirement 7.7, 7.4). Found verifying 7.5 against the live
  internal registry: on the 1.1.2 checkout `doctor` printed "1.1.1 does not list
  host 0.7.0.5 (internal/stable) as supported" although the `FloofyCrew` package's
  `RELEASE.md` on `mainline` lists exactly that host — the explanation was chosen
  by *equality* with the running version (`doctor`) or by *inequality*
  (`self-update`), so a development checkout, or a release cut locally before
  its publish, always landed in the "does not list host" branch, and the plain
  `floofy self-update` refused with the same false reason. The version order now
  decides, in one place both commands call
  (`floofy_core.selfupdate.up_to_date_reason`, `UpdateCheck.reason`): "X does not
  list host … as supported" only for a release that is newer yet unsupported here;
  "this is the newest release" for the running version; and "this is newer than
  the newest published release (X)" when the running version is ahead — whether or
  not that older release lists the host. `doctor`, `status`, `self-update --check`
  and the plain `self-update` (which now exits 0 with the line instead of
  refusing) agree; the `--json` documents are unchanged. Tests with the fake
  release endpoint, in both feed shapes: running ahead of a release that lists the
  host and of one that does not, and the newer-but-unsupported counter-case on
  every surface.

## 1.1.2 — 2026-09-21

A patch on 1.1.1: bare `floofy` opens its interface on every interpreter FloofyCrew
runs on — the host bundle's `python3.12` that the internal installer selects has no
curses, so the screens are now drawn by a standard-library terminal driver shared
with the consent screen — the FloofyCrew App is listed in the dashboard sidebar, and
the terminal banner is the fox. No API change (`floofy.api_version` stays 1.1.0);
same `supports` list as 1.1.1.

### Supports

| Edition | Channel | Host versions |
|---|---|---|
| internal | beta | 0.7.0.5 |
| internal | stable | 0.7.0.5 |
| external (public) | insider | 0.7.0rc5 |
| external (public) | stable | 0.6.0 |

### Fixed

- **Interactive `floofy` without curses** (task 10.10; Requirement 15.6, 15.4,
  15.3, 14.2). Bare `floofy` on a terminal now opens the interface on every
  interpreter FloofyCrew runs on: the internal KiroCrew bundle's `python3.12`,
  which the internal installer selects, ships without `_curses`, so until now
  bare `floofy` always printed the fallback hint there while `kiro-cli` ran its
  own interface in the same terminal. The curses backend is gone: the screens are
  drawn by a small standard-library terminal driver, `floofy_core.cli.term`,
  shared with the one-time consent screen — `termios` raw mode entered and
  restored in a `finally` that also covers SIGINT, the alternate screen buffer,
  the cursor hidden while drawn, a frame buffer diffed line by line and painted
  with cursor moves so redraws do not flicker, colours through the CLI's own
  style layer at the detected depth (so `NO_COLOR` and `--no-color` now apply to
  the interface too), the size re-read on `SIGWINCH` and every 0.5 s, and key
  decoding for Enter, Esc (told from an arrow sequence by a short timeout), `q`,
  digits, the arrows, Home/End, PgUp/PgDn, Tab, Backspace and Ctrl-C. Screens,
  menus, handler mapping and audit rows are unchanged; the consent frame is the
  same `compose()` drawn by the same driver, so `floofy init` standalone and from
  the interface show one thing. The interface opens whenever stdin and stdout
  are terminals and `TERM` is not `dumb` — an unset `TERM` is an ANSI terminal,
  as it is for `kiro-cli` — and falls back to the usage only otherwise; the hint
  now says "needs a terminal (stdin and stdout TTYs; not TERM=dumb)". Tests
  drive every screen on a pseudo-terminal with `TERM` unset and with `curses`
  blocked out of `sys.modules`, and assert no module of the package imports
  curses. Docs: [docs/interactive.md](docs/interactive.md), "Using `floofy`
  interactively".

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
- The interactive `floofy` shows the **wordmark** beside the fox: "FloofyCrew" from
  `branding/wordmark-text.svg` rasterised to 6 rows (`floofy_core/cli/wordmark.json`),
  "Floofy" in peach and "Crew" in the pink→orange gradient, drawn to the right of the
  art and centred on its height; when the terminal is narrower than fox + gap + block
  letters (110 columns) the plain word is drawn in the same two colours instead.
  `floofy doctor` and `floofy --version` print the block over their header lines
  on a terminal of 110 columns or more. The installers print the same composition
  (`branding/ascii/floofy-wide.*`, the fox with the wordmark beside it) from 110
  columns: embedded as `BANNER_WIDE_TXT`/`BANNER_WIDE_ANS` in the public `install.sh`,
  shipped as `banner/floofy-wide.*` in the internal package; both are written by
  `scripts/refresh_install_banners.py` (`--check` in the tests). The copies the installers
  carry — the `BANNER_TXT`/`BANNER_ANS` literals of the public `install.sh` and the
  internal package's `banner/` files — are refreshed from the same
  `branding/ascii/` art (their tests hold them byte-identical to it).

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
- The publish steps of both editions remain the maintainer's (tracked in the
  maintainer's own notes).

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
- internal edition: the adapter's SSO opener now presents a
  `curl/8 FloofyCrew (unofficial KiroCrew mod manager)` User-Agent on every hop
  of the redirect chain (configurable as `identity.userAgent`). The SSO gateway
  selects the login flow by agent: a `curl/…` agent receives the cookie-based
  redirect the adapter's cookie jar satisfies, while any other non-browser agent
  is given a Kerberos Negotiate challenge the standard library cannot answer, so
  `floofy registry defaults` failed with HTTP 401 at the gateway although `curl`
  with the same jar fetched the same URL. Verified against the pushed internal
  registry with the pinned key `bf253dc3e34a0a1b`.
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
