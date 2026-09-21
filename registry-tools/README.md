# registry-tools

Tooling for the mod registries: build `index.json` from mod records and
release assets, produce and verify the detached Ed25519 signature over
canonical JSON, validate submissions in CI (schema, `sha256` of every file,
`floofy validate`, licence present, no host-name masquerade) and emit the
KiroCrew-compatible `app-registry.json` for app-kind mods. The same tooling
drives both registries — the public GitHub repository and the internal one — so
it takes the source name, endpoint and signing key as parameters (the registry
repository's own `registry.json`) and must not hard-code either registry's
identity. Edition-neutral: CI scans this directory for internal identifiers.

Standard library plus `floofy_core` (itself stdlib-only): run it as
`PYTHONPATH=floofy-core:registry-tools python -m registry_tools …` from a
checkout, which is also how the CI templates use it.

## Commands

| Command | What it does |
|---|---|
| `keygen --out KEY [--comment TEXT]` | an Ed25519 key pair: `KEY` (private, mode 0600, never committed) and `KEY.pub.json` (the public record an edition adapter pins) |
| `build REPO [--out index.json] [--compat compat.json] [--sign KEY] [--app-registry] [--check] [--floofycrew VERSION] [--resolve-links]` | `index.json` from `mods/**` with the matrix cells folded in (schema-checked); `--check` only compares with the committed index (the CI gate); `--sign` writes `index.json.sig` (refused when `registry.json`'s `keyId` names another key); `--app-registry` also emits `app-registry.json`; `--resolve-links` reads every unresolved **link record** at its branch (default branch unless `link.ref`) and pins `commit`, `manifestSha256` and the checkout's `files[]` into its `release.json` (every build re-checks `manifestSha256` against the record's `floofy.json` and `link.repo` against `registry.json` `repoUrlPattern`) |
| `sign PAYLOAD --key KEY [--out SIG]` | the detached signature of any JSON document (`index.json`, `compat.json`) |
| `verify PAYLOAD [--sig SIG] [--public-key RECORD …] [--key-id ID] [--edition-keys]` | verify against the given public records or the keys pinned by the installed edition adapters; exit 1 on `invalid`/`unsigned` |
| `validate-submission MOD [--registry REPO] [--tag TAG] [--allowlist FILE] [--terms t,t] [--json]` | the CI gate for a pull request: `floofy validate` with zero errors (schema, `files[]` hashes, targets, network scan), licence in the manifest **and** a `LICENSE`/`LICENCE`/`COPYING` file, no host-name masquerade after homoglyph normalisation (`k1r0crew` reads as `kirocrew`; ids cleared in `REPO/masquerade-allow.txt`), the release tag equal to the version (`1.2.0`, `v1.2.0`, `<id>-1.2.0` or the record's `tag`), the record's `floofy.json` identical to the archive's, `release.json` `files[]` HTTPS + `sha256` + `size` matching the archive, a known repository, and for app-kind mods the `app.json` name matching the row |
| `validate-submission --record ID@VERSION --registry REPO [--json]` | the same gate for a **link record**: fetch `link.repo` at the pinned commit (the operator's own credentials), compare the checkout's commit, canonical-manifest hash and every file with the record, refuse a `repo` outside `repoUrlPattern`, then every check above on the checkout |
| `compat-merge COMPAT ROW [--source LABEL] [--sign KEY]` | add or replace the `compat.json` row with the run file's `(edition, channel, hostVersion)`, keeping the row's human `overrides[]`; rows sorted; schema-checked. `--override CELL=VERDICT --edition … --channel … --host-version … --reason … --by …` records a human override instead (Requirement 9.3) |
| `app-registry REPO [--out FILE]` | the KiroCrew-compatible `app-registry.json` from the committed `index.json`: a JSON array of `{name, gitUrl, repo, branch, subdirectory}` rows — one per app-kind mod (a single `app` part), the newest non-yanked version pinned to its release tag — exactly the shape the host's `_fetch_external_registry_index` reads at a registry clone's root (`kiro_crew/apps/registry.py` L3043–L3199 in 0.7.0.5; cited in `registry_tools/app_registry.py`). Mods whose `repo` is not a cloneable https/ssh git URL are skipped with a note |
| `record REPO ID [--repo URL] [--path DIR] [--ref BRANCH] [--source CHECKOUT [--source-ref COMMITISH]] [--contact ALIAS] [--refresh] [--json]` | make (or `--refresh`) the **link record** of a mod from its repository: read the mod under `DIR` (default `mods/ID`) on `BRANCH` (default the repository's default branch), or from a local checkout, take the version from its `floofy.json`, and write `mods/ID/<version>/floofy.json` + `release.json` pinned at the **commit** read (plus `mod.json` for a new mod). No tag is made or read (Requirement 8.9); follow with `build --sign` and `readme` |

## Registry repository layout

```
registry.json                     {"schema": 1, "source": "github:org/repo", "keyId": "<16 hex>",
                                   "archiveUrlTemplate": "https://…/{id}-{tag}/{file}", "hostRegistry": {"repo", "branch"}}
mods/<id>/mod.json                optional curated fields: repo, tags, links, icon (else taken from the newest manifest)
mods/<id>/<version>/floofy.json   the manifest exactly as released
mods/<id>/<version>/release.json  {"tag", "channel", "publishedAt", "files": [{url, sha256, size, name}],
                                   "app": {"name", "subdirectory"}?, "yanked": "<reason>"?}
compat.json (+ .sig)              the matrix (Requirement 9); the Forge merges rows with compat-merge
index.json (+ .sig)               generated by build; never edited by hand (CI: build --check)
app-registry.json                 generated for app-kind mods (Requirement 8.6)
masquerade-allow.txt              ids the curators cleared of the host-name check
```

Mod archives are never committed to the public registry: they are release
assets of the mod's own repository, tagged identically to the version
(Requirement 8.2, the Obsidian model). A pull request adds or changes one
`mods/<id>/<version>/` directory; `templates/ci-validate-submission.yml`
downloads the named assets and runs `validate-submission` against them, then
`build --check`. `templates/ci-publish-index.yml` (optional) rebuilds and signs
on merge with the private key held as a repository secret.

## Bootstrapping a registry (task 7.5)

`scripts/init_registry_repo.sh <target> --edition public|internal [--host-version V
--channel C --payload <payload copy> --verdict tested|expected --run URL --sign KEY
--seed MOD_ID | --no-seed]` instantiates `templates/registry-repo/` for one
edition: every value (source label, signing key id, archive URL template, host
App Store row, FloofyCrew repository) comes from the edition adapter's
`registry.json` — found by its `edition` field, so nothing here names an edition.
It seeds the first records from the monorepo's `mods/<id>/` (manifest copy, a
deterministic archive — byte-identical across editions — and `release.json`),
writes the first `compat.json` row (the framework block measured offline against
the payload copy: bundle fingerprints + Python anchors), builds `index.json` and
`app-registry.json` and signs them. A GitHub-hosted registry also gets the two CI
workflows. Archives land under `archives/<id>/` when the edition's URL template
points into the repository, else under `.dist/` (gitignored) for upload as a
release asset. `python -m registry_tools.bootstrap --help` lists the options.

## Signing

`index.json.sig` / `compat.json.sig` are `{"schema": 1, "alg": "ed25519",
"keyId", "signature", "signedAt", "canonical": "json-c14n-1"}` over the canonical
bytes of the parsed document (`floofy_core.canonical`); the manager verifies with
the keys its edition adapter pins plus any key the user pinned per source. Key
ids are `sha256(public key)[:16]`. Private keys stay outside every repository.


## The registry as a KiroCrew app registry (Requirement 8.6)

The host's federated app registries are git repositories it shallow-clones and
reads `app-registry.json` from. A FloofyCrew registry repository therefore
doubles as one: `build --app-registry` emits the rows for its app-kind mods, and
the manager adds the repository itself as an **operator** row of the host —
`floofy registry add <url> --host-registry <git repo>[@branch]` (or `floofy
registry defaults`, which uses the row the edition adapter ships) — through
`PUT /api/apps/registries` when a gateway runs, else the same edit of the host
`config.json` `registries[]` list. The row is always `trust: index` under an id
of FloofyCrew's own (`floofycrew`, or the id the edition adapter's default source
names); the ids an edition pins for its own registries are never reused or
overwritten — the manager refuses the collision before the host would
(`floofy_core/hostregistry.py`; the adapter lists the ids through
`pinned_registry_ids()`). What the host then thinks of each app (admission,
execution gate) is shown by the manager as a warning and never enforced by
FloofyCrew.
