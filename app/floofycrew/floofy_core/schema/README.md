# FloofyCrew schemas

Five JSON Schemas (draft 2020-12) ship inside `floofy_core.schema` as package data
and are loaded with `floofy_core.schema.load_schema("floofy" | "patch" | "index" |
"compat" | "signature")` (Requirement 1.9, 8.1, 9.1). `floofy validate` checks a
mod against the first two with the stdlib-only checker in
`floofy_core.schema.check`, then runs the semantic checks (SemVer ranges, file
hashes, targets, network) that a schema cannot express; `registry-tools` checks
registry documents against the last three.

Schema ids are `https://floofycrew.dev/schema/<name>-1.json`. The `floofycrew.dev`
name is the project's public documentation domain; the ids are identifiers,
nothing fetches them, and the trailing `-1` is the document's `schema` number so
a future `floofy-2.json` can coexist. Keys beginning with `x-` are reserved for
extensions at the top level of every document (and inside index/compat rows) and
are ignored by the framework.

Ten minimal, valid example mods — one per part kind — live in
`floofy-core/examples/<kind>/`; `scripts/hash_example_files.py` keeps their
`files[].sha256` entries current (`--check` in CI).

## `floofy.json` (schema 1)

**Identity** (Requirement 1.1, 1.2). `schema` is the integer `1`. `id` matches
`^[a-z][a-z0-9_-]{1,63}$` and is the key used in dependency maps, the registry and
the data home (`~/.kiro/crew/floofy/mods/<id>/`). `name`, `version` (SemVer 2.0,
the official grammar), `description`, `authors[]` (at least one) and `license`
are required; `links{}` (name → URL), `icon` (a relative path listed in
`files[]`) and `tags[]` are optional.

**Host compatibility** (Requirement 1.3). `kirocrew.version` is a range in the
npm style — comparators `>= > < <= =`, hyphen ranges `1.2.3 - 2.0.0`, caret `^1.2`,
tilde `~1.2.3`, wildcards `1.x` / `*`, unions with `||`, whitespace as AND —
matched against the host's `X.Y.Z` base version (an internal `0.7.0.5` or a
public `0.7.0-insider.3` both compare as `0.7.0`). `editions` (`internal`,
`external`) and `channels` narrow further. `strict` (default `false`) decides
what an out-of-range host means: a warning `HostVersionOutOfRange` and the mod
still loads, or the non-load reason `Unsupported`.

**Graded dependencies** (Requirement 1.4, 1.5). Five maps of `id → range` or
`id → {range, reason}`: `dependsOn` (hard; missing or out of range gives reason
`Dependency`, propagated to dependents), `recommends` and `suggests` (warnings
only), `conflicts` (mutual: when both mods are active the declaring one is
disabled with reason `Conflict`, ties broken by id) and `breaks` (one-directional:
the declaring mod is disabled, the other stays active). `dependsOn` must contain
`floofycrew` — the framework version, not the host version, is the hard
dependency; the range is matched against the FloofyCrew release version.
`loadBefore[]` / `loadAfter[]` add ordering edges; a cycle disables its
participants with reason `Conflict` and the rest of the boot proceeds.

**Network** (Requirement 1.10, 11.6). `network.hosts[]` lists every remote host
the mod contacts, lower-case, optional `:port`, where a `*` label matches one or
more labels at that position (`*.example.com` matches `api.example.com` and
`a.b.example.com`, not `example.com`). `network.credentials` (default `false`)
says the mod asks the user for credentials itself; FloofyCrew never handles
credentials. Mod traffic must be encrypted (`https`, `wss`); loopback is the one
exception. The validator warns — never refuses — on plaintext non-loopback URLs
(`PlaintextNetwork`) and on encrypted URLs to undeclared hosts (`UndeclaredHost`).

**Parts** (Requirement 1.6, 1.7, Requirement 2). `parts[]` (at least one) is
discriminated on `kind`; every part has `side` (`gateway`, `spa`, `electron`,
`cli`) and, except `config`, a `path` relative to the mod root (no leading `/`,
no `..`, no backslash). Zero-code kinds point at an existing host artifact:
`theme` → `theme.json`, `app` → `app.json`, `skill` → `SKILL.md`, `agent` → a
`.json`/`.md` agent spec, `appearance` → the pack's manifest; the artifact's
directory is the file's parent. `config` declares host `config.json` pairs in
`values` and/or a JSON file at `path` (`values` wins). `python-hook` adds `module`
(dotted name imported under a namespaced `sys.modules` key) and `early`. `spa`
adds `activation` (`runtime` default, `boot` for first-frame effects injected by
the Patcher). `patch` points at a patch descriptor (below) and is the only kind
that modifies payload files. `ui` (Requirement 16.4) is the mod's own page in the
FloofyCrew App: `path` is the page's directory (or the entry file), `entry` the
ES module the App imports same-origin and mounts through its default export
`mount(container, api)` with `api = floofy.mod(id)`, `title` the App's label,
`icon` optional; `side` is `spa`. Inert until opened, so it never lands a mod
disabled on its own.

**Files** (Requirement 1.6, 1.8). `files[]` (at least one) lists every shipped
file other than `floofy.json` itself as `{path, sha256}` (64 lower-case hex). A
missing or mismatched file is the non-load reason `MissingFiles`; a shipped file
that is not listed is a warning. Packages are a plain directory or a `.zip` /
`.tar.gz` of it; archives with symlinks, absolute paths or `..` segments are
rejected before extraction.

### `spa` parts with `activation: "boot"` (Requirement 4.3)

A `boot` object describes the first-frame effects the Patcher bakes into every
payload's `index.html` (task 5.4): `css` (a stylesheet appended before
`</head>`, after the host's own — it must scope itself, e.g. to
`[data-theme="custom-<slug>-dark"]`), `favicon` and `logo` (files served from
the Loader app's `ui/boot/<id>/` and asserted as `<link rel=icon>` /
`--theme-logo` before the host hydrates), `title`, `attributes` (`data-*` on
`<html>`, re-asserted by the drift guard until the host settles) and `when.theme`
(only act when the active colour theme — `localStorage` `mc-color-theme`, else
the host config's `dashboard.theme_color` captured at patch time — equals it).
The part's `path` is the mod's boot hook, inlined as `function (ctx)` with
`{id, color, mode, pref, element}` — ES5 only, no `</script`, at most 32 KB.

## Patch descriptor (`patch-1.json`)

Referenced by `kind: patch` parts and applied by the Patcher (Requirement 5).
`target` is the payload-relative file to patch (host Python sources and launcher
files are refused as engineering-rule targets per Requirement 5.10; governance
files are allowed and flagged governance-altering per Requirement 11.4).
`appliesTo` is a host base-version range (Requirement 5.5); `fromBuild` /
`toBuild` gate on the full host version (inclusive lower, exclusive upper).
`ops[]` run in order: `insert-before`, `insert-after` and `replace` need a
`fingerprint` (literal, or a regular expression with `regex: true`) that matches
exactly once — or, with `occurrence: first|last`, names a class of locations
("the first `<script`") and takes one of them — plus `content`; `append-head`
needs only `content`. `marker` names
a literal whose presence means the op is already applied. `cacheBust` marks a
content-hashed immutable target (Requirement 5.6); `importMap` carries import-map
keys for the chunk-remap path recorded in design spike 1.4 and is deliberately
permissive.

Module patches on hashed chunks (design spike 1.4, task 5.3): `mode:
"import-map"` leaves the target chunk untouched, writes a patched copy with
rebased imports under the Loader app's `ui/patched/<chunk>` and adds the
import-map key `"/assets/<chunk>": "/apps/floofycrew/ui/patched/<chunk>"` to
`index.html` (dropping the chunk's `modulepreload` hint), which swaps the module
for every importer. The `target` of such a descriptor may carry a glob on the
hashed name (`kiro_crew/static/dist/assets/useTheme-*.js`) that must resolve to
exactly one file — a fingerprint on the name, never a hash. `find` is an optional
literal that must occur in the target text before any op applies (Vencord's
module identification). The entry chunk loaded by `<script src>` is not
reachable through the import map; the Patcher falls back to the in-place +
alias-graph path for it.


## Registry index (`index-1.json`, Requirement 8.1, 8.9)

`index.json` is the signed catalogue of one registry. Top level: `schema` (`1`),
`source` (the registry's own label — informational; the manager namespaces
colliding ids by the source name the *user* configured, never by this claim),
`generatedAt` (UTC timestamp; a verified index older than the cached one is
flagged as a rollback), optional `floofycrew` (the release that built it), and
`mods[]`. Each mod carries `id` (the manifest id), `name`, `description`,
`authors[]`, `tags[]`, `repo` (the source repository) and `versions[]`, plus the
optional `license`, `links{}` and `icon`. Each version carries exactly the
Requirement 8.1 list — `version`, `kirocrew` (the manifest's host range),
`editions`, `compat{hostVersion: tested|expected|broken}` (the matrix cells for
this `mod@version`, folded in from `compat.json` by the registry build so a client
can grade versions before downloading anything), `files[]`, `dependencies` (the
manifest's `dependsOn`), `channel` and `publishedAt` — and the optional
`channels`, `strict`, `tag` (the release tag when it is not the bare version),
`kinds` (the part kinds shipped), `app{name, subdirectory}` (present for a pure
App Kit app, the facts the KiroCrew-compatible `app-registry.json` row needs —
Requirement 8.6), `yanked` (a withdrawal reason; never picked automatically) and
`changelog` (an https URL of the version's release notes — `release.json`
`changelog`, else the manifest's `links.changelog`; the manager App shows it
beside an available update and falls back to the mod's `repo`, Requirement 16.6).
The index never embeds a signature: the detached `index.json.sig` beside it does
that.

A version is **one of two record shapes** (`oneOf`; never mixed):

- an **asset record** lists archives in `files[]{url, sha256, size[, name]}`
  (HTTPS only; the manager checks both the hash and the size of every download);
- a **link record** (Requirement 8.9) carries `link{repo, commit,
  manifestSha256[, path][, ref]}` — the mod's repository (`ssh://` or `https://`,
  the transport a client fetches over with its own credentials), the **commit**
  the version is pinned at, the SHA-256 of the canonical `floofy.json`
  (`floofy_core.canonical`), the mod's directory inside the checkout when it is
  not the root, and optionally the branch the record was made from (`ref`; the
  repository's default branch when absent) — and lists the checkout's files in
  `files[]{path, sha256, size}` (no `url`). No tag locates a version: the client
  fetches exactly `commit` (`git fetch --depth 1 origin <sha>`, which both forges
  FloofyCrew targets serve), so the mod's repository simply keeps the mod under its
  directory on its default branch and the registry pins the commit it curated.
  The manifest's own `files[]` already pins those files; the record's list is the
  curator's independent statement, lets `floofy which <sha256>` resolve a file of
  a link-listed mod and gives the client a size to check. The checkout is refused
  when its canonical-manifest hash or any file differs from the record. Records
  made before commits were pinned may still carry `tag`, which the client uses
  only when `commit` is absent. The internal registry lists every mod
  this way (its forge has no release assets); the public one may use either shape.

## Compatibility matrix (`compat-1.json`, Requirement 9.1)

`compat.json` holds `rows[]`, one per `edition × channel × hostVersion`
(`hostVersion` as the host prints it: `0.7.0.5`, `0.7.0`, `0.7.0-insider.3`).
`framework` records `loader` (`ok|degraded|broken`), `spaFingerprints` and
`pythonAnchors` (`{matched, total, missed[]}`), `shimVersion` and optionally the
`floofycrew` release that produced the row. `mods` maps `id@version` cells to a
verdict — a bare `tested|expected|broken`, or `{verdict, run, detail}` with the
link to the Forge run. `overrides[]` are human decisions `{cell, verdict, reason,
by, at}` that win over the automated cell (the local cache reader applies them).
`run` and `checkedAt` describe the row as a whole. A source's matrix is trusted
under the same rule as its index: `compat.json.sig` must verify, or the user must
have allowed the source unsigned.

## Detached signature (`signature-1.json`, Requirement 8.3)

`index.json.sig` and `compat.json.sig` are JSON: `{"schema": 1, "alg":
"ed25519", "keyId": "<16 hex>", "signature": "<base64, 64 bytes>", "signedAt":
"…Z", "canonical": "json-c14n-1"}`. The signature is Ed25519 (RFC 8032) over the
canonical bytes of the *parsed* document (`floofy_core.canonical`: sorted keys,
no whitespace, UTF-8, `ensure_ascii=False`), so the served file may be
pretty-printed. `keyId` is `sha256(public key)[:16]`; the manager accepts the keys
its edition adapter pins plus any key the user pinned for that source
(`floofy registry add --public-key`), and `--key-id` restricts a source to one
key. The design allowed Ed25519 or RSA-SHA256; only Ed25519 is implemented
(stdlib Python has neither, and Ed25519 fits in ~120 lines the test vectors pin
down), so `alg` is a constant.
