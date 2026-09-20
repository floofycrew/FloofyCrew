# The Python API (gateway side)

A `python-hook` part is a Python package the Loader imports inside the gateway
process and drives through two functions:

```python
# hook/__init__.py — the part's "module": "hook"
import floofy

def activate(ctx):
    ctx.log.info("hello from %s on KiroCrew %s", ctx.mod_id, ctx.host.version)
    ctx.hooks.after("kiro_crew.some.module:some_function", my_after_hook)
    ctx.state["ready"] = True          # published as this mod's `exports` in the state API

def deactivate(ctx):
    pass                               # registrations are unwound for you
```

The Loader is a KiroCrew App (`backend.hooks.on_startup` / `on_shutdown` /
`routes`); it never registers the host's plugin entry point
([seams.md](seams.md)). Mods are imported under namespaced module keys
(`floofy_mods.<id>.<module>`), so two mods may ship a package with the same name.
Everything a mod calls back into is wrapped fail-open: an exception in
`activate`, in a hook or in an event handler is logged with attribution,
reported through the state API and the event bus, and disables **that** mod
with reason `Error` — the gateway and the other mods keep running. If the
Loader itself fails to initialise, the gateway boots exactly as shipped and
`floofy doctor` says so.

## `import floofy`

The runtime registers a `floofy` module before any mod is activated, so the
plain import works inside the gateway without a package on `sys.path`.

| Name | What |
|---|---|
| `floofy.host` | the host facts (below) |
| `floofy.api_version` | the SemVer string mods depend on — **pin on this, never on host internals** |
| `floofy.framework_version` | the FloofyCrew release |
| `floofy.fetch(url, **kwargs)` | the sanctioned network client, attributed to the calling mod (same rule as `ctx.http`) |
| `floofy.events` | the Loader event bus |
| `floofy.unofficial` | always `True` |

### Host facts (`floofy.host`, `ctx.host`)

| Field | Meaning |
|---|---|
| `version` | the host version as the host prints it (`0.7.0.5`, `0.7.0`, `0.7.0-insider.3`) |
| `base_version` | the `X.Y.Z` base every range is matched against |
| `build_version` | the four-component build stamp on the internal edition, else `None` |
| `channel` | `beta` / `stable` / `insider` / `nightly` when known |
| `edition` | `internal` or `external` |
| `profile` | the host profile the facts imply |
| `payload_root`, `package_dir` | where the running host lives on disk (read-only for mods) |
| `host_home`, `data_home` | the host data home and `<host home>/floofy` |
| `interpreter` | the interpreter running the gateway |

Host-version derivation is an edition-adapter concern (a build stamp on one
edition, the package's `__version__` plus a channel marker on the other); mods
only read the result.

## `ctx` — what `activate(ctx)` / `deactivate(ctx)` receive

| Attribute | What |
|---|---|
| `ctx.mod_id`, `ctx.version`, `ctx.mod_dir` | identity and the installed directory (read-only) |
| `ctx.host`, `ctx.api_version` | as above |
| `ctx.log` | a logger writing to `<data home>/mods/<id>/.floofy/log` (rotating) and to the gateway log with the mod's prefix |
| `ctx.hooks` | the hook registry, scoped to this mod (below) |
| `ctx.http`, `ctx.fetch(url)` | the network client (below) |
| `ctx.config` | per-mod key/value storage: `get`, `set`, `update`, `delete`, `clear`, `items`, `to_dict`, `in`, `[]` — a JSON file under `<data home>/mods/<id>/.floofy/`, never under a payload root |
| `ctx.events` | the event bus scoped to this mod (`subscribe`, `publish`, `history`) |
| `ctx.routes` | the mod's own backend routes (below): `add(method, path, handler)`, `remove()`, `list()`, `base_path` |
| `ctx.data_dir` | the mod's private directory (`<data home>/mods/<id>/.floofy/`) |
| `ctx.state` | a dict of facts the mod publishes; exposed as `exports` in the state API and the manager page |
| `ctx.unofficial` | always `True` |

## Hooks: `ctx.hooks.before / after / replace`

Targets are either an **import path** — `"pkg.module:attr"`, with `attr`
possibly dotted (`Class.method`); the attribute is swapped for one wrapper per
target, async originals get an async wrapper, and the original is restored by
identity when the last registration unwinds — or a **gateway route** —
`"route:GET /api/status"`; the live route handler is swapped so the change
takes effect immediately even though the router is frozen after startup.
Route targets are the supported form for request handlers (patching a handler's
module attribute would not reach dispatch); import paths cover everything the
host calls by name.

| Registration | Signature | Semantics |
|---|---|---|
| `before(target, fn)` | `fn(*args, **kwargs)` | runs first; the original runs regardless of `fn`'s return |
| `replace(target, fn)` | `fn(call_next, *args, **kwargs)` | `call_next` is the rest of the chain ending in the original |
| `after(target, fn)` | `fn(result, *args, **kwargs)` | its return value becomes the result |

Every registration carries the owning mod id (`floofy status` and the state API
list them). A raising callback removes **all** of that mod's registrations and
falls through to the original behaviour; `deactivate` unwinds them in reverse
order. Mods should wrap only what they need and prefer `after` to `replace`.

## Network: `ctx.http` and `floofy.fetch`

The rule, checked before a socket opens and again on every redirect:

1. every request uses an encrypted channel — `https` or `wss`;
2. except to **loopback** (`localhost`, `127.0.0.0/8`, `::1`, `unix:` sockets),
   which may be plaintext so a mod can talk to the local gateway;
3. a non-loopback host must be declared in the manifest's `network.hosts[]`.

A violation raises `FloofyNetworkDenied` (`PlaintextDenied`, `UndeclaredHost`,
`UnsupportedScheme`) **and** writes an `op: net-denied` row to
`<data home>/audit.jsonl`. Certificate verification is never disabled and there
is no switch to disable it. `ctx.http.request(method, url, …)`, `.get`, `.post`
and `.fetch` return a small `Response` (`status`, `headers`, `body`, `.json()`);
HTTP error statuses come back as responses, not exceptions. `wss` URLs pass the
check (`ctx.http.check(url)`) but the client does not implement WebSockets in
v1 — open your own TLS WebSocket once the URL passed. FloofyCrew never handles
host or user credentials: a mod that needs some asks the user itself and says so
with `network.credentials: true`.

## Routes: `ctx.routes.add(method, path, handler)`

A mod may serve its own backend routes to its page (a `ui` part, [spa-api.md](spa-api.md)
`floofy.mod(id).routes`) or to anything else on the dashboard origin. The host's
route registry is static, so the Loader registers catch-alls at startup and
dispatches at request time to what the mod registered in `activate(ctx)`:

```python
def activate(ctx):
    def echo(request):                       # sync or async
        return {"echo": request.json(), "config": ctx.config.to_dict()}

    ctx.routes.add("POST", "echo", echo)      # /api/apps/floofycrew/mods/<id>/api/echo
    ctx.routes.add("GET", "items/{key}", lambda request: (200, {"key": request.params["key"]}))
```

`method` is `GET`, `POST`, `PUT` or `DELETE`; `path` is relative to the mod's
api root, one to four segments of `[A-Za-z0-9._~-]` or `{param}`. The handler
receives a request with `method`, `path`, `params`, `query`, `headers`, `body`
(bytes), `json()`, `text` and `mod_id`, and returns a JSON-able object (a 200
JSON reply), `(status, body)`, `(status, headers, body)` or a `ModResponse`. An
exception is that request's 500 (message logged to the mod's logger) — never a
fault, so a bad request cannot disable a mod. Registrations unwind with the mod
(deactivate, a fault, shutdown); the state API lists them as `mods[id].routes`.
Every route sits behind the dashboard auth and is same-origin; nothing here
opens a port.

## Events

The bus is synchronous and fail-open per subscriber. The Loader publishes:

| Event | Payload |
|---|---|
| `mod.activated`, `mod.deactivated` | `{"mod", "version"}` |
| `mod.quarantined` | `{"mod", "version", "hostVersion"}` |
| `mod.faulted` | `{"mod", "source", "message"}` |
| `host.version_changed` | `{"previous", "current"}` |
| `loader.state` | `{"loader", "active", "bootedAt"}` after every boot and reload |

`ctx.events.subscribe(name, fn)` (or `"*"`), `ctx.events.publish(name, payload)`
— publish your own under `mod.<id>.<topic>` by convention. A short history is
exposed by the state API for the manager page.

## The early shim (`"early": true`)

Some hooks must run **before** the host's platform bootstrap composes the
gateway. For those the Loader offers an optional shim: a `.pth` line
(`import floofy_early`) in the site directory the host interpreter processes —
the user site on the bundled interpreter, the venv site on venv installs (chosen
by the edition adapter; `floofy init --early` installs it, `floofy deinit`
removes it). The shim is a no-op unless the host package is importable from that
interpreter **and** `<data home>/early.json` lists a mod, and it swallows every
error (a raising `.pth` would print into every host process). It installs a
one-shot import finder that lets the host's bootstrap module import, then runs
`early(ctx)` of every listed mod, fail-open, logged to `<data home>/early.log`.

Reach limit worth knowing: the shim runs in every process of the host
interpreter that processes that site directory — the gateway, the MCP daemon,
the CLI shims — but **not** in children the host starts with `-s` (the user site
is skipped; a venv site is still processed) and not in the `-I -S` sandbox
shims. `floofy doctor` reports the shim's state.

## Stability and deprecation policy

Mods depend on `floofy.api_version` (SemVer). Within a major version, names are
only added. When a name must change, the old name keeps working for **two
FloofyCrew minor releases** and every use emits a `DeprecationWarning` plus a
log line naming the calling mod's module and the release that drops it
(`floofy.old_name is deprecated, use floofy.new_name (removed in FloofyCrew
X.Y); called from floofy_mods.<id>.hook`). Nothing deprecated is removed
earlier. Host internals reached through `ctx.hooks` are the **host's** API, not
FloofyCrew's: they may change with any host release — that is what the
compatibility matrix and the Forge exist to catch ([updating.md](updating.md)).

## The state API

The Loader mounts its routes under `/api/apps/floofycrew/` behind the dashboard
session: `GET /state` (the whole Loader state: consent, host facts, every mod's
state, parts, typed reason, warnings, exports, the last patch report, events),
`GET /health`, `POST /reload` (re-run the boot sequence, apply `pending/`),
`POST /mods/{id}/fault` (the SPA host's fault report), `GET /spa/{id}/{file}`
(an enabled mod's `spa` files), `POST /cli` (one read-only `floofy` command
in-process for the manager page), `GET /registry` (cache summary and update
availability) and the manager App's typed action routes (`POST /mods/install`,
`/mods/{id}/enable`, … — each the same handler as the CLI with `actor: app` and
the 409 confirmation protocol; `loader-app/README.md` lists them). `floofy status`
reads the same state.

---
Covers Requirements 2.4, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 11.6, 14.3.
