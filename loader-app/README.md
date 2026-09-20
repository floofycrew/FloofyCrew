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
| `app.json` | the host manifest (`kiro_crew/apps/manifest.py` `AppManifest`): name `floofycrew`, hooks `floofy_loader.hooks:{register_routes,on_startup,on_shutdown}`, `ui.entry` `index.mjs`, `permissions` `{}` |
| `floofy_loader/hooks.py` | the shim the host imports: puts the app directory on `sys.path`, delegates to `floofy_loader.runtime`, and turns any failure into `<data home>/loader-failure.json` so the gateway boots vanilla (Requirement 3.6) |
| `floofy_loader/runtime.py` | the process-wide `LoaderRuntime` (`startup` / `shutdown` / `register_routes`): reads the host facts, runs `boot()`, keeps the live activations, writes `loader-state.json` |
| `floofy_loader/boot.py` | the boot sequence (`boot(BootDeps) -> BootResult`, `deactivate_all`) with injectable collaborators (context factory, governance reader, patch runner) |
| `floofy_loader/state.py` | `LoaderState` / `ModState` / `PartState`, the closed `REASONS` vocabulary, `SEAM_BY_KIND` |
| `floofy_loader/consent.py` | `consent.json` reader/writer, `WARNING_TEXT`, `WARNING_VERSION`, `ConsentRequired` |
| `floofy_loader/activation.py` | `ModContext` (the `ctx` mods get), namespaced import under `floofy_mods.<id>.<module>`, fail-open `activate`/`deactivate` |
| `floofy_loader/pending.py` | staged installs/removals, `enabled.json`, `patch` part planning for the Patcher |
| `floofy_loader/compat.py` | the `cache/compat.json` reader (`CompatCache.row_for`, verdicts) |
| `floofy_loader/paths.py` | the data-home layout |
| `floofy_loader/host.py`, `api.py` | `floofy.host` facts and the `floofy` facade (below) |
| `ui/index.mjs` | manager page entry (placeholder until 6.7); default-exports a React component, `react` resolves through the host's import map |
| `tests/` | `test_manifest.py` (shape, host-loader validation on a payload copy, shim behaviour) |

`scripts/build_loader_app.py` assembles the **installable** directory (default
`loader-app/build/floofycrew/`, gitignored): `app.json` with `version` stamped
from `floofy_core.__version__`, `ui/`, `floofy_loader/`, `floofy_early/`, a
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
| `cache/compat.json` | `floofy registry refresh` | the registry's `compat.json`; the Loader reads the row for `edition × channel × hostVersion` |
| `quarantine/requests/<id>.json` | Loader → manager | "this mod is `broken` here" — the manager moves the files into `quarantine/<hostver>/`; the Loader never moves mod dirs |
| `deploy/`, `audit.jsonl` | Patcher | deployment manifests and the audit log (the Loader appends `net-denied` rows from 4.5) |
| `loader-state.json` | Loader | the last `LoaderState` (what `GET /api/apps/floofycrew/state` also serves); `floofy doctor` reads it |
| `loader-failure.json` | shim | written when the Loader itself failed (`phase`, `error`, `traceback`); a later successful boot removes a stale one |

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
(Requirement 6.1).

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
