# FloofyCrew schemas

Two JSON Schemas (draft 2020-12) ship inside `floofy_core.schema` as package data
and are loaded with `floofy_core.schema.load_schema("floofy" | "patch")`
(Requirement 1.9). `floofy validate` checks a mod against them with the
stdlib-only checker in `floofy_core.schema.check`, then runs the semantic checks
(SemVer ranges, file hashes, targets, network) that a schema cannot express.

Schema ids are `https://floofycrew.dev/schema/floofy-1.json` and
`https://floofycrew.dev/schema/patch-1.json`. The `floofycrew.dev` name is the
project's public documentation domain; the ids are identifiers, nothing fetches
them, and the trailing `-1` is the manifest `schema` number so a future
`floofy-2.json` can coexist. Keys beginning with `x-` are reserved for
extensions at the top level of both documents and are ignored by the framework.

Nine minimal, valid example mods — one per part kind — live in
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
that modifies payload files.

**Files** (Requirement 1.6, 1.8). `files[]` (at least one) lists every shipped
file other than `floofy.json` itself as `{path, sha256}` (64 lower-case hex). A
missing or mismatched file is the non-load reason `MissingFiles`; a shipped file
that is not listed is a warning. Packages are a plain directory or a `.zip` /
`.tar.gz` of it; archives with symlinks, absolute paths or `..` segments are
rejected before extraction.

## Patch descriptor (`patch-1.json`)

Referenced by `kind: patch` parts and applied by the Patcher (Requirement 5).
`target` is the payload-relative file to patch (host Python sources and launcher
files are refused as engineering-rule targets per Requirement 5.10; governance
files are allowed and flagged governance-altering per Requirement 11.4).
`appliesTo` is a host base-version range (Requirement 5.5); `fromBuild` /
`toBuild` gate on the full host version (inclusive lower, exclusive upper).
`ops[]` run in order: `insert-before`, `insert-after` and `replace` need a
`fingerprint` (literal, or a regular expression with `regex: true`) that matches
exactly once, plus `content`; `append-head` needs only `content`. `marker` names
a literal whose presence means the op is already applied. `cacheBust` marks a
content-hashed immutable target (Requirement 5.6); `importMap` carries import-map
keys for the chunk-remap path recorded in design spike 1.4 and is deliberately
permissive.
