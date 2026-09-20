# loader-app

The FloofyCrew Loader packaged as a KiroCrew App: `app.json` declares
`backend.hooks.on_startup` / `on_shutdown` / `routes` and a `ui.entry`, and the
host installs it into `<host home>/apps/floofycrew/` through its own App Kit
seam (design DR-2 rung 2, Requirement 3.1). It never registers a
`kirocrew.plugins` entry point (that seam admits one occupant and flips the
profile) and never edits host `.py` files. It runs the Python Loader inside the
gateway (consent check, pending apply, resolve, activate `python-hook` parts
fail-open, publish state), serves the manager page from `ui/` and, from 5.x,
the SPA host. Edition-neutral: CI scans this directory for internal identifiers;
edition facts come from the `floofy_edition_*` adapters via `floofy_core.editions`.

FloofyCrew is **unofficial** and says so in `displayName` and `description`
(Requirement 11.8).

## Layout

| Path | Purpose |
|---|---|
| `app.json` | the host manifest (`kiro_crew/apps/manifest.py` `AppManifest`): name `floofycrew`, hooks `floofy_loader.hooks:{register_routes,on_startup,on_shutdown}`, `ui.entry` `index.mjs`, `permissions` `{}`, `iconPath` `art/icon.svg` |
| `art/icon.svg` | the store / sidebar icon — a copy of `branding/logo.svg` (the test pins them byte-equal; rebuild with `python branding/build_icons.py && cp branding/logo.svg loader-app/art/icon.svg`). The host serves only the paths the manifest declares, from `/apps/floofycrew/art/<path>` for an installed app and through its blob proxy for a registry row (`routes.py` `handle_app_art_file`, `_ART_MANIFEST_FIELDS`), so `iconPath` is the one field to touch |
| `floofy_loader/hooks.py` | the shim the host imports: puts the app directory on `sys.path`, delegates to `floofy_loader.runtime`, and turns any failure into `<data home>/loader-failure.json` so the gateway boots vanilla (Requirement 3.6) |
| `floofy_loader/runtime.py` | the process-wide `LoaderRuntime` (`startup` / `shutdown` / `register_routes`): reads the host facts, runs the manager's host-version-change handling in-process (`floofy apply --if-changed`, actor `loader`) when the previous `loader-state.json` ran another host version — reporter, anchors, compat, re-apply, yeet BEFORE the mods boot — then `boot()`, keeps the live activations, writes `loader-state.json` (`hostChange` carries that outcome) |
| `floofy_loader/boot.py` | the boot sequence (`boot(BootDeps) -> BootResult`, `deactivate_all`) with injectable collaborators (context factory, governance reader, patch runner) |
| `floofy_loader/state.py` | `LoaderState` / `ModState` / `PartState`, the closed `REASONS` vocabulary, `SEAM_BY_KIND` |
| `floofy_loader/consent.py` | re-export of `floofy_core.consent` (`consent.json` reader/writer, `WARNING_TEXT`, `WARNING_VERSION`, `ACCEPT_PHRASE`, `ConsentRequired`) — the text lives in the core so the `floofy` CLI never imports this package |
| `floofy_loader/activation.py` | `ModContext` (the `ctx` mods get), namespaced import under `floofy_mods.<id>.<module>`, fail-open `activate`/`deactivate` |
| `floofy_loader/pending.py` | staged installs/removals, `enabled.json`, `patch` part planning for the Patcher |
| `floofy_loader/compat.py` | re-export of `floofy_core.compat` (`CompatCache.row_for`, verdicts) |
| `floofy_loader/paths.py` | `FloofyPaths` = `floofy_core.datahome.DataHome`, the shared data-home layout |
| `floofy_early/` | the optional early shim: `zz_floofycrew.pth` (`import floofy_early`) + a stdlib-only package that runs mods' `early(ctx)` right after `kiro_crew.platform.bootstrap` is imported |
| `floofy_loader/early_install.py` | installers/status for the shim: `install_user_site` / `install_venv_site` / `uninstall_*` / `status(interpreter, kind)` (`doctor` reads it) |
| `floofy_loader/storage.py` | `ModConfig` (`ctx.config`, atomic JSON under `mods/<id>/.floofy/config.json`), `mod_logger` (`floofy.mods.<id>`, rotating `mods/<id>/.floofy/mod.log`, `[floofy:<id>]` prefix), `mod_data_dir` containment |
| `floofy_loader/events.py` | `EventBus` / `ModEvents` (`ctx.events`, `floofy.events`): fail-open pub/sub with attribution and history |
| `floofy_loader/routes.py` | the gateway routes (pure responders + the aiohttp `AppRoute` adapter) |
| `floofy_loader/net.py` | `FloofyHttp` (`ctx.http`, `floofy.fetch`), `NetworkPolicy`, `FloofyNetworkDenied`: the encrypted-only rule with the loopback exemption, declared hosts, audited denials, policy-checked redirects |
| `floofy_loader/hooks_registry.py` | `HookRegistry` / `ModHooks` (`ctx.hooks`): `before`/`after`/`replace` on `pkg.module:attr` and `route:METHOD /path` targets, per-mod attribution, identity-restoring unwind, fail-open faults |
| `floofy_loader/host.py`, `api.py` | `floofy.host` facts and the `floofy` facade (below) |
| `ui/index.mjs` | the manager **App** shell (task 6.7 → 11.2; Requirement 16.1, 16.2, 2.7, 7.3, 11.8): a hand-written React component (`react` through the host's import map, no bundler, no `@kirocrew/app-sdk/ui` components) — the header (the branding mark, `FloofyCrew`, Loader/host facts), the always-visible unofficial banner, the navigation Mods · Registry · Profiles · Doctor · Audit · Settings (the page in the URL hash, `#/registry`), the shared controller `useManager()` (Loader state, `status` rows, `/registry`, the output pane, `act(path, body)` = one typed route through the confirmation protocol) and the output/error panes. Pages under `ui/app/pages/`: `mods.mjs` (the landing page: the mod table with state + typed reason, enabled flag, host-compat and source-tier badges — `floofycrew-tier-<id>` — parts with seam / payload impact, warnings and governance warnings, update-available; Enable/Disable/Update/Yeet/Uninstall/Restore; the staged set with Apply-now = `POST /reload`; the install form, staged by default), `registry.mjs` (task 11.4: the Updates card — every installed mod with a newer version the registry cache knows to work here, the release-notes link from the record's `changelog` (else its `repo`), one-click update staged or now through `POST /mods/{id}/update`, check-for-updates and update-all; the sources with trust, add, refresh, defaults; search over the merged cache with the source tier an install would have and every version's compat cell for this host; install from a record or a git reference), `profiles.mjs`, `doctor.mjs`, `audit.mjs`, `settings.mjs` (consent, vanilla boot, the terminal-only capabilities with their commands, About with the banner from `GET /banner`), `modpage.mjs` (task 11.5: the "Mod pages" list of every mod with a `ui` part and the page host — `import()` of the entry from `/ui/mods/<id>/`, `mount(container, window.floofy.mod(id))`, an error boundary that shows a throwing page's error with a *Disable <mod>* button and never takes the manager page down). `ui/app/api.mjs` is the same-origin client and the 409 protocol (`callRoute`: a question goes to the asker, the answer is merged into `confirmations`, a `$body` patch carries the staged-vs-apply-now choice, `null` stops), `ui/app/confirm.mjs` the modals that answer every question kind (task 11.3: the consent modal with the CLI's `WARNING_TEXT` and an `[ I AGREE ]` control — Esc declines; the disclosure with Yes/No and the staging choice; a text field per governance-altering target, unlocked only by the exact path and re-compared by the Loader; the unlisted-source disclosure with an explicit `I ACCEPT` item; the loosening warning), `ui/app/palette.mjs` the CLI palette mirrored (pinned by `test_ui_shell.py`; `tests/ui/*.test.mjs` cover the protocol client under `node --test`). Imports `./host.mjs` so the SPA host runs on the manager page even without the `index.html` loader tag |
| `ui/host.mjs`, `boundary.mjs`, `events.mjs`, `surfaces.mjs`, `patches.mjs`, `reporter.mjs` | the SPA host (task 5.x) — copied from `spa-host/src/` by the build script; see `spa-host/README.md` |
| `tests/` | `test_manifest.py` (shape, host-loader validation on a payload copy, shim behaviour) |

`scripts/build_loader_app.py` assembles the **installable** directory (default
`loader-app/build/floofycrew/`, gitignored): `app.json` with `version` stamped
from `floofy_core.__version__`, `ui/` (the manager entry plus the SPA host
modules from `spa-host/src/`), `floofy_loader/`, `floofy_early/`, a
vendored copy of `floofy-core/floofy_core` and the edition adapter(s) chosen
with `--edition <editions/ dir name>|both` (default both — an adapter is inert
on a host it does not recognise, so one archive installs on both editions).
`--check` fails CI when the build directory is stale.

## How the Loader finds `floofy_core` at runtime

The host imports hook modules **by file path** and does not touch `sys.path`
(`kiro_crew/apps/module_loader.py` L266–L340 `load_app_module`: the module is
registered as `_kirocrew_app_floofycrew.floofy_loader.hooks`; the synthetic
parent packages come from `_ensure_namespace_packages`, L140–L172). An absolute
`import floofy_core` would therefore fail inside the gateway. Decision: **the
installed app directory is self-contained and puts itself on `sys.path`.**

1. `scripts/build_loader_app.py` vendors `floofy_core` (and the adapters) beside
   `floofy_loader/` — the Loader and the core it was released with are one
   artifact, version-locked; the `floofy` CLI may be a different install.
2. `floofy_loader/hooks.py` inserts the directory holding `app.json` at the
   front of `sys.path` (the vendored core wins over any other copy on the
   interpreter) and imports the canonical `floofy_loader.runtime` from there.
   The shim itself is the only module living in the host's namespace.
3. Developer fallback: a `floofy-home.json` beside `app.json`
   (`{"pythonpath": ["/checkout/floofy-core", "/checkout/editions/public"]}`) adds
   further entries for a checkout that installs `loader-app/` directly without
   vendoring. `floofy dev` (task 6.x) may write it; the build script never does.

Mods import the mod-facing API as `floofy` (`floofy_loader.api`, registered
under `sys.modules["floofy"]` before any mod is activated).

## Mod-facing API (`import floofy`)

| Name | Meaning |
|---|---|
| `floofy.api_version` | the SemVer API version mods pin on (`floofy_core.API_VERSION`, `1.0.0`); `floofy.framework_version` is the FloofyCrew release |
| `floofy.host` | `floofy_loader.host.HostFacts`: `version` (the full string the host reports, stamp applied), `build_version` (`X.Y.Z.N` or `None`), `base_version` (`X.Y.Z` `Version`), `channel` (`stable`/`insider`/`nightly`, the adapter's channel for a recognised internal payload, `None` when an internal build's channel is unknown), `edition` (`internal`/`external`), `profile` (`KIROCREW_PROFILE`, else `enterprise`/`standalone`), `payload_root`, `package_dir`, `host_home`, `data_home` (`<host_home>/floofy`), `interpreter`, plus `source`/`payload_id`/`notes` for `doctor` |
| `floofy.fetch(url, **kw)` | the sanctioned network client (task 4.5) |
| `floofy.events` | the Loader event bus (task 4.6) |
| `floofy.deprecated_name(old, new, removed_in)` | the deprecation helper: a `DeprecationWarning` + log line naming the caller's module; renamed API names stay for two FloofyCrew minors via `api.DEPRECATED_NAMES` |
| `floofy.unofficial` | always `True` (Requirement 11.8) |

`floofy.host` sources (host file:line in `floofy_loader/host.py`): `kiro_crew.__version__`
with the `BUILD_VERSION` stamp applied by the host itself (`kiro_crew/__init__.py`
L10–L109); channel from the edition adapter's payload provider when it recognises
the running payload, else the host's `release_channel.channel()` rule
(`release_channel.py` L83–L96); edition from `floofy_core.editions.combined_edition_probe`;
`host_home` from `kiro_crew.config.paths.config_dir()` (`KIROCREW_HOME` or `~/.kiro/crew`).

## `ctx.hooks` — the hook registry (Requirement 3.4)

`ctx.hooks.before(target, fn)`, `.after(target, fn)`, `.replace(target, fn)`;
every registration is attributed to the calling mod and listed under `hooks[]`
in the state API.

* `target = "pkg.module:attr"` — an import path (`attr` may be dotted:
  `Class.method`). One wrapper per target is swapped onto the owning module or
  class; async originals get an async wrapper; the original object is restored
  **by identity** when the last registration on it is unwound.
* `target = "route:GET /api/status"` — a live gateway route. aiohttp keeps the
  handler on the `ResourceRoute` (`aiohttp/web_urldispatcher.py` `AbstractRoute`
  L224 `self._handler`, read per request via the `handler` property L233), so
  swapping `route._handler` is live even though the router is frozen after
  startup. The application comes from the host's App SDK wiring:
  `kiro_crew.apps.hooks_integration.get_route_registry()` (L295) → `._app.router`.
  Use this form for request handlers — patching a handler's module attribute
  would not reach dispatch, because the router already holds the function.
* Callback shapes: `before(*args, **kwargs)` (return ignored),
  `replace(call_next, *args, **kwargs)` (`call_next` is the rest of the chain
  ending in the original; the last registered replacer is outermost),
  `after(result, *args, **kwargs) -> result`. Sync or `async def` callbacks are
  both fine on async targets.
* Fail-open: a raising callback is logged with attribution, **all** of that mod's
  registrations are removed, the call falls through to the original, and the
  runtime disables the mod with reason `Error` (`LoaderRuntime.fault_mod`;
  the fault is listed under `faults[]`). Other mods' hooks keep running.
* Unwind: `deactivate(mod_id)` on mod deactivation/fault, `shutdown()` at
  gateway shutdown — most recent registration first; a property test asserts
  any interleaving leaves every target `is` its original.

## `ctx.http` / `floofy.fetch` — the network rule (Requirement 11.6)

`floofy_loader/net.py`. Before any socket opens, and again on every redirect:

* the scheme must be `https` or `wss` (TLS) — **except** to loopback
  (`localhost`, `127.0.0.0/8`, `::1`, `unix:` sockets), where `http`/`ws` are
  fine, so mods can talk to the local gateway in plaintext;
* a non-loopback host must match one of the mod's `network.hosts[]` (wildcard
  labels per `floofy_core.netscan.host_matches`; a declared `host:port` pins the
  port);
* anything else raises `FloofyNetworkDenied(code, url, mod, detail)` with `code`
  ∈ `PlaintextDenied | UndeclaredHost | UnsupportedScheme` **and** appends an
  `op: net-denied` row (`mod`, `code`, `url` without query/userinfo, `detail`) to
  `<data home>/audit.jsonl`.

`ctx.http` is a `FloofyHttp` built from the mod's manifest: `request(method, url,
*, headers=None, body=None, json_body=None, timeout=10) -> Response(status,
headers, body, url; .ok/.text()/.json())`, `fetch(url, **kw)` (GET), `get`, `post`,
`check(url)`, `policy.allows(url)`. HTTP error statuses are returned, transport
failures raise `FloofyNetworkError`. TLS uses `ssl.create_default_context()`; there
is no way to relax verification. `wss`/`ws` and `unix:` URLs are validated but not
served in v1 (`NotImplementedError` after the check). `floofy.fetch(url, **kw)` is
the same client, attributed to the calling mod by its `floofy_mods.<id>` module
name; a caller that is not a mod gets the strictest policy (no declared hosts)
under `unknown:<module>`. The state API lists every mod's client under `network{}`.

## The early shim (Requirement 3.2, optional)

For code that must run **before** the gateway's platform bootstrap, `floofy init
--early` (task 6.x, through the edition adapter) installs `zz_floofycrew.pth`
containing `import floofy_early` plus the `floofy_early` package into the site
directory the host interpreter processes:

* **user site** (`site.getusersitepackages()`, `~/.local/lib/python3.X/site-packages`)
  on payloads whose bundled interpreter is started plainly — design "Spike
  outcomes" 1.5 measured the `.pth` running in the gateway, the MCP gateway daemon,
  the `kirocrew mcp-*` shims and every other `sys.executable -m …` child;
* **venv site** (`sysconfig.get_paths()["purelib"]`) on venv payloads, where
  `site.ENABLE_USER_SITE` is `False`; re-applied per venv (`crew-venv-<ver>`).

The shim is a no-op unless `kiro_crew` is importable (`find_spec`, no import) and
`<host home>/floofy/early.json` exists; `FLOOFY_EARLY_DISABLE=1` switches it off.
It then arms a one-shot `sys.meta_path` finder for `kiro_crew.platform.bootstrap`
and, once that module executed, runs `early(ctx)` of every listed mod
(`{"mods": [{"id", "path", "module", "part"}]}`) under the Loader's
`floofy_mods.<id>.<module>` key — the Loader reuses that module object at
`activate`, so `early()` and `activate()` share state. Everything is inside one
fail-open `try/except` (a raising `.pth` would print a traceback into every host
process) and logs to `<data home>/early.log`. `ctx` carries `mod_id`, `mod_dir`,
`host_home`, `data_home`, `bootstrap` (the module), `log()`, `state`.

**Reach limit** (spike 1.5, reported by `early_install.status()`): the `-I -S`
sandbox shims skip site processing entirely; children the host starts with `-s`
(`kiro_crew/apps/bridges.py` L560, app-provided MCP servers wrapping the CLI) skip
the *user* site but still process a venv site. The tests exercise both site kinds
under a fake `HOME`/`PYTHONUSERBASE` and assert the real user site never appears.

## The execution gate and the `agent.apps_trusted` grant (Requirement 2.3)

Third-party app Python runs in-process only when the operator granted it
(`kiro_crew/apps/execution.py` L623–L686 `app_execution_denied`):

* `agent.apps_allow_third_party: true` in `<host home>/config.json` admits
  every third-party app (`third_party_execution_allowed`, L354–L376; only the
  JSON boolean `true` counts);
* or the app's name in `agent.apps_trusted: ["floofycrew"]` (`trusted_app_names`,
  L378–L425; literal names only). A name grant over a locally installed app
  (no repository) is honoured because the installed record resolves to local
  provenance (`_repository_grant_denied_for_binding`, L490–L593); for a
  repository-backed install the grant is repository-bound and
  `agent.apps_trusted_repositories` / `agent.apps_trusted_local` come into play.

The gate is checked at `enable` (`kiro_crew/apps/manager.py` L1800–L1849
`enable_app`, error code `app_execution_denied`), at every hook module load
(`module_loader.py` L302–L314) and at boot (`hooks_integration.py` L775–L800
`on_gateway_startup`, action `hook_boot_register`). Measured on the dev host
(0.7.0.5 copy, fresh scratch home):

* `kirocrew app install <dir>` succeeds and lands the app `enabled: false`
  (`installed.json`); the SEL row is `event_type api_access, caller_identity
  app_install, operation install, outcome success, resources name='floofycrew'
  version=0.0.0`, followed by `operation app_execution_admission, outcome denied,
  resources action='resource_register' provenance=unverified`.
* `kirocrew app enable floofycrew` is refused (`❌ blocked by execution policy:
  third-party app execution is disabled; trust this app alone in Settings
  (agent.apps_trusted), or set agent.apps_allow_third_party=true …`); SEL row
  `app_execution_admission denied action='enable' provenance=unverified`.
* After writing `"agent": {"apps_trusted": ["floofycrew"]}` into the scratch
  `config.json`, `enable` succeeds and the gateway boot logs
  `app_execution_admission allowed action='module_load' provenance=trusted_grant`,
  `app_module_load ok floofycrew:floofy_loader.hooks:on_startup (third_party)`
  and `lifecycle_hook_invoke ok floofy_loader.hooks:on_startup` (caller
  `app:floofycrew`), plus the one-time host warning `SECURITY: executing
  third-party app 'floofycrew' Python in-process`.

FloofyCrew treats this gate as **governance information** (DR-5): the manager
offers to write the per-app `agent.apps_trusted` grant with the user's
confirmation and shows the host's verdict as a warning; it never edits the
policy files themselves (Requirement 11.4).

The SEL is `<host home>/security_events.jsonl` (`kiro_crew/sel.py` L12, L122),
an HMAC-chained JSONL; FloofyCrew keeps its own `audit.jsonl` in addition
(Requirement 11.5).

## Where the routes are mounted

`backend.hooks.routes` names a callable `register_routes(ctx) -> list[AppRoute]`
(`kiro_crew/apps/route_registry.py` L29–L34 `AppRoute(method, path, handler)`;
handlers are `async def handler(request, ctx)`). The host dispatches them from a
catch-all at **`/api/apps/floofycrew/<path>`** (`route_registry.py` L117–L123
`ensure_catch_all`, `"/api/apps/{app_name}/{path:.*}"`), behind the dashboard
token/cookie auth. The `/apps/<name>/api/*` prefix the design text mentions is
the reverse proxy for apps that run their own backend process
(`kiro_crew/apps/routes.py` L3620 `handle_app_api_proxy`) and does not apply to
in-process hook routes. Path parameters match one segment (`{id}` →
`([^/]+)`, L47–L67). Segments the host reserves under an app's namespace
(`manifest.py` L1347 `CORE_APP_ROUTE_SEGMENTS`: `_jobs config dev disable enable
manifest migrate-cleanup open token uninstall update`) are never used here.

The Loader's route table (`floofy_loader/routes.py` `ROUTE_TABLE`, mirrored in
`app.json` under the `floofycrew` block the host passes through as `extra`):

| Method | Path under `/api/apps/floofycrew` | Purpose |
|---|---|---|
| GET | `/state` | the full `LoaderState` JSON plus `api_version`, `loaderVersion`, `unofficial: true`, `hooks[]`, `faults[]`, `network{}`, `events{}` |
| GET | `/health` | `{ok, loader, active[], bootedAt, unofficial, api_version, loaderVersion}` |
| POST | `/reload` | re-run the boot sequence (deactivate all, apply `pending/`, re-read `enabled.json`, activate); returns the new state — the manager's "apply now" |
| POST | `/mods/{id}/fault` | body `{"message", "stack", "source": "spa"}` from the SPA host's error boundary; the mod is disabled with reason `Error`, its `spa/<id>/` files stop being served, `mod.faulted` is published |
| GET | `/spa/{id}/<up to 4 segments>` | an **active** mod's `spa` part files from `<data home>/spa/<id>/` — `Content-Type` by extension (`text/javascript` for `.js`/`.mjs`), `Cache-Control: no-cache`, `X-Content-Type-Options: nosniff`, no dotfiles, no escapes. This is what `host.mjs` (5.1) imports |
| POST | `/cli` | body `{"argv": [...]}`: runs ONE **read-only** `floofy` command in-process (`floofy_core.cli.main.run(..., in_gateway=True, actor="ui", live_state=runtime.state_dict)`; `CLI_ALLOWLIST` = status, doctor, search, info, list, audit, which) off the event loop; answers `{ok, exit, stdout, stderr, json, command}`. A mutating command is refused with 403 and the typed route to use instead (`CLI_MOVED`) |
| GET | `/registry` | the registry cache summary (`cache/index.json`) and, per installed mod, `{installed, candidate, verdict, update, inRegistry}` — "newest version known to work here" (Requirement 9.2) |
| GET | `/ui/mods/{id}/<up to 4 segments>` | an **active** mod's `ui` part files (task 11.5, Requirement 16.4) from the installed mod directory — only files the manifest's `files[]` lists (the manifest itself, `.floofy/` and anything unlisted are 404), `text/javascript` for `.mjs`/`.js`, `no-cache`, `nosniff`, no escapes; what the App `import()`s same-origin before calling the module's `mount(container, floofy.mod(id))` |
| GET / PUT | `/mods/{id}/config` | the mod's `.floofy/config.json` (`floofy_loader.storage.ModConfig`, the store its Python side reads as `ctx.config`): `PUT {"patch": {…}}` merges key by key, `null` deletes, 413 above 64 KiB; readable and writable while the mod is installed (the page itself needs it active) |
| GET / POST / PUT / DELETE | `/mods/{id}/api/<up to 4 segments>` | the mod's own backend routes registered in `activate(ctx)` with `ctx.routes.add(method, path, handler)` (`floofy_loader/modroutes.py`): one catch-all per method and depth registered at startup (the host's route registry is static), dispatched at request time; 404 while the mod is not active or for an unknown route (the reply lists the mod's routes), a raising handler is that request's 500 — never a fault |
| * | the manager App's typed routes (`floofy_loader/app_routes.py` `ROUTES`, task 11.1) | one route per CLI action — `POST /mods/install`, `/mods/update-check`, `/mods/update-all`, `/mods/{id}/enable|disable|uninstall|update|yeet`, `GET /mods/{id}/info`, `POST /yeet`, `/restore`, `GET /quarantine`, `/status`, `/doctor`, `/search?q=`, `/audit?tail=&op=`, `GET/POST /registries`, `DELETE /registries/{key}`, `POST /registries/{key}/refresh|trust`, `/registries/refresh|defaults`, `GET/POST /profiles`, `POST /profiles/{name}/use|export`, `/profiles/import`, `/vanilla`, `/consent`. Each spells the sub-command from a typed body and runs it through `floofy_core.cli.main.execute(..., actor="app", in_gateway=True)` — the same handler and the same audit row as the CLI (`actor: app`). A confirmation the CLI would ask is a **409** `{confirmation: {kind, text, expects, targets, detail}}` (kinds: `consent`, `yes-no`, `governance-target`, `unlisted-source`, `unsigned-index`) answered by re-posting with `confirmations: {consent, yes, governanceTargets[], unlistedSource, unsignedIndex}`; nothing changes until then, a typed governance path is compared byte for byte, and no request can spell `--yes`, `--i-accept-the-risk`, `--accept-unlisted-source` or `--confirm-governance-target`. `POST /consent {agree: true}` records the one-time consent with `how: "app"` through the CLI's own `init` step. Installs stage into `pending/` unless `now: true` (Requirement 7.6, 16.5); the Loader reloads only when the handler asked (`enable`/`disable`, `--now`, a yeet) or after the consent |

All of them sit behind the dashboard auth (cookie or `?token=`); the manager UI,
served same-origin, calls them with `fetch("/api/apps/floofycrew/state")`.

## `ctx.config`, `ctx.log`, `ctx.events` (Requirement 3.7, 3.8)

* `ctx.config` — `ModConfig`: `get/set/update/delete/clear/items/to_dict`, dict
  syntax, JSON values only, atomic writes to `<data home>/mods/<id>/.floofy/config.json`.
  The `.floofy/` dot-directory is invisible to `floofy validate` (dotted path
  parts are skipped), so runtime files never show up as unlisted mod files, and
  `mod_data_dir` refuses anything outside `mods/<id>/` or under the host payload.
  `ctx.data_dir` is that directory.
* `ctx.log` — `logging.getLogger("floofy.mods.<id>")`: a small rotating
  `mods/<id>/.floofy/mod.log` plus propagation to the gateway log with every
  record prefixed `[floofy:<id>]`.
* `ctx.events` / `floofy.events` — the Loader bus: `subscribe(name, fn(name,
  payload))`, `unsubscribe`, `publish(name, payload)`; `"*"` subscribes to
  everything; delivery is synchronous and fail-open (a raising subscriber is
  logged and counted, the others still get the event); a mod's subscriptions are
  dropped when it deactivates. Loader events: `mod.activated`, `mod.deactivated`,
  `mod.quarantined`, `mod.faulted`, `host.version_changed` (the previous
  `loader-state.json` ran another host version), `loader.state` (after every
  boot/reload). The state API shows subscriptions, the last 20 events and errors
  under `events{}`.

## Boot sequence and data home

`floofy_loader/boot.py` runs design steps 1–9 on every gateway start (and on the
manager's reload): host facts → `consent.json` → governance (information) →
`pending/` → `mods/*/floofy.json` + `enabled.json` → `floofy validate` (hashes →
`MissingFiles`) → resolver → `cache/compat.json` (`broken` → `Quarantined`) →
`python-hook` activation under `sys.modules["floofy_mods.<id>.<module>"]`,
fail-open per mod → `LoaderState`. Without a current consent record the Loader
is **inert**: everything is still scanned, resolved and reported, but nothing is
activated, staged or patched, and every mod that would have activated carries
the Loader-level reason `ConsentRequired`.

Data home `<host home>/floofy/` (`floofy_loader/paths.py`):

| Path | Owner | Meaning |
|---|---|---|
| `consent.json` | `floofy init` / manager | `{"warningVersion": 1, "acknowledgedAt": "...", "by": "<os user>"}`; `floofy_loader.consent.WARNING_VERSION` is the current text version, an older acknowledgement counts as missing |
| `mods/<id>/` | manager | unpacked mods (`floofy.json` at the root; the directory name must equal the manifest `id`) |
| `enabled.json` | manager | `{"<id>": true|false}`; an unlisted mod with `python-hook`/`spa` parts is off until enabled (Requirement 11.7) |
| `pending/<id>/` | manager → Loader | a complete mod directory staged while a gateway runs; the Loader moves it over `mods/<id>/` at boot |
| `pending/<id>.remove` | manager → Loader | marker: remove `mods/<id>/`, `spa/<id>/` and the `enabled.json` entry |
| `spa/<id>/` | Loader | the `spa` part files of **active** mods, mirrored from `mods/<id>/` for the SPA host route |
| `registries.json`, `cache/sources/<key>/`, `cache/index.json`, `cache/compat.json` | `floofy registry add/refresh` | the user's registry sources and per-source trust; each source's fetched `index.json`/`compat.json` (+ `.sig`) with a `meta.json` verdict; the merged caches rebuilt from the **usable** sources only (signature verified, or the source explicitly `allowUnsigned`). The Loader reads the compat row for `edition × channel × hostVersion` and `GET /registry` reads the merged index |
| `quarantine/requests/<id>.json` | Loader → manager | "this mod is `broken` here" — the manager moves the files into `quarantine/<hostver>/`; the Loader never moves mod dirs |
| `deploy/` | Patcher | deployment manifests |
| `audit.jsonl` | everyone, one writer | `floofy_core.audit.AuditLog`: every mutating operation from the CLI, the seam handlers, the Patcher, the Loader (`net-denied` rows) the interactive `floofy` (`actor: tui`) and the manager App's typed routes (`actor: app`), each row with a `consentRef` (`{warningVersion, sha256}` of `consent.json`); `floofy audit` reads it |
| `loader-state.json` | Loader | the last `LoaderState` (what `GET /api/apps/floofycrew/state` also serves); `floofy doctor` reads it |
| `loader-failure.json` | shim | written when the Loader itself failed (`phase`, `error`, `traceback`); a later successful boot removes a stale one |
| `vanilla-once` | `floofy --vanilla` | marker consumed by the next consented boot: every mod is `UserDisabled` for that boot only, the Patcher runs with an empty set (payloads restored for the boot), `LoaderState.vanilla` is `true`; the boot after runs the mods again |

`LoaderState` (`floofy_loader/state.py`) as JSON: `loader` (`ok|inert|failed`),
`consent{status,required,...}`, `host{...}` (the `floofy.host` facts), `mods{<id>:
{version, name, active, reason, detail, enabled, quarantined, filesOk, warnings[],
governance[], parts[{index, kind, side, path, status, detail, seam,
modifiesPayload}], errors[], modDir, exports{}}}`, `order[]`, `active[]`,
`governance[]` (loader-level warnings), `governanceSnapshot{}`, `compat{}`,
`pending{}`, `patches{}`, `errors[]`, `bootedAt`, `durationMs`, `reasons[]`
(the closed vocabulary: the resolver's `Error Duplicate Conflict Dependency
Released Feature Unsupported MissingFiles Quarantined UserDisabled` plus
`ConsentRequired`). Governance never appears as a `reason`; a test asserts it.
`exports` is what a mod published through `ctx.state[...]`.

Enabled `patch` parts are handed to the Patcher at boot (revert-then-patch,
pre-consented, no live verify) — the Loader is one of the re-apply triggers
(Requirement 6.1). Whenever any active mod has a `spa` part, the Loader adds the
generated `index.html` descriptor (`floofy_loader.pending.plan_boot` →
`floofy_core.boot_script`): the spike-1.3 loader tag for the always-on SPA host
plus the boot script and baked CSS of every `activation: boot` part, owned by
mod id `floofycrew` in the deployment manifest. Patched chunk copies and boot
assets the Patcher writes live in the installed app's own `ui/patched/` and
`ui/boot/` (served by the unauthenticated app-UI route) and are removed by
`restore`.

## Dev-host recipe (never the live install)

```
mkdir -p .scratch/loader-dev/home .scratch/loader-dev/kiro
python scripts/build_loader_app.py --out .scratch/loader-dev/app/floofycrew --edition both
export KIROCREW_HOME=$PWD/.scratch/loader-dev/home KIRO_HOME=$PWD/.scratch/loader-dev/kiro
export KIROCREW_SKIP_MODEL_DOWNLOAD=1   # plus the internal edition's identity-check skip variable (its adapter README names it)
.scratch/payload-0.7.0.5/bin/kirocrew app install .scratch/loader-dev/app/floofycrew
# grant: add "agent": {"apps_trusted": ["floofycrew"]} to .scratch/loader-dev/home/config.json
.scratch/payload-0.7.0.5/bin/kirocrew app enable floofycrew
.scratch/payload-0.7.0.5/bin/kirocrew gateway --test-mode --no-crons --no-tunnel
```

`.scratch/payload-0.7.0.5` is a `cp -a` of the installed 0.7.0.5 bundle; its
`bin/kirocrew` resolves the bundle root from its own path, so exec it directly
(design "Spike outcomes" 1.5). `KIROCREW_READY:{"port","token",…}` on stdout
gives the port and the one-time link token; the first `?token=` request is
exchanged for the `mc_token_<port>` cookie (`kiro_crew/dashboard/token_auth.py`
L2651 onwards). The gateway-backed tests (`tests/test_gateway.py`, task 4.8)
automate this and skip when the payload copy is absent.

## Tests

`python -m pytest -q loader-app/tests` — unit tests for every module (pytest +
hypothesis; no host needed) plus `tests/test_gateway.py`, which skips unless a
payload copy exists. With the copy it builds the app, installs it with the host
CLI into a fresh scratch home under `.scratch/loader-tests/gateway/`, proves the
grant behaviour (enable refused → `agent.apps_trusted` → enabled), starts
`kirocrew gateway --test-mode --no-crons --no-tunnel` (with
`FLOOFY_NO_ADAPTERS=1`, so the Loader never lists live install roots) and checks:

1. inert without consent — `GET /api/apps/floofycrew/state` says `loader: inert`,
   `consent.required`, no mod active, every mod `ConsentRequired`;
2. `consent.json` + `POST /reload` — the shipped `example-python-hook` and the
   test mods activate; hooks are attributed in `hooks[]`;
3. a mod whose `activate` raises is `Error` with a traceback; the others are
   active and `/api/health` stays 200;
4. the Loader itself failing (`FLOOFY_LOADER_SELFTEST_FAIL=1`, a second gateway)
   — `/api/health` 200, `loader-failure.json` written, no Loader routes;
5. hook unwind — an `after` hook on `route:GET /api/health` stamps a header; the
   fault route disables the mod and the original handler is back;
6. governance — a home-tier policy with `capabilities.theme_install=false`
   surfaces under the theme mod's `governance[]`, never as a `reason`;
7. `ctx.http` inside the gateway — plaintext to a loopback server works,
   `http://example.com` and an undeclared `https` host are denied and audited;
8. the `spa/` route serves an active mod's files (nested too) and nothing else.

The whole suite runs in about two minutes on the dev desk (gateway boots ≈ 10 s).
