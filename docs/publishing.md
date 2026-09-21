# Publishing a mod

FloofyCrew has one registry per edition, both with the same schema and the same
tooling: the **public registry** (a GitHub repository, `floofycrew-registry`)
for the public KiroCrew edition, and an **internal registry** (a git repository
on the maintainer's internal forge) for the internal edition. A registry holds
**records**, not files — the Obsidian community-plugins model: your archive is a
release asset of your own repository, and a pull request adds the record that
names it. Users then find your mod with `floofy search`, and the compatibility
matrix tells them which version works on their exact host version before
anything is downloaded.

> **Shortcut:** if your edition ships a contributor assistant (the internal
> edition does — see its contributing guide under `editions/`), it walks this
> whole page with you, and you only run the final push and open the review
> yourself. What follows is the by-hand reference.

## 1. Make the mod releasable

```bash
floofy new theme my-theme      # manifest, README, LICENSE stub, smoke test, CI workflow
floofy validate my-theme       # schema, files[] hashes, parts, targets, network scan — zero errors
```

- `floofy.json` needs a `license` **and** the mod root needs a `LICENSE`
  (`LICENCE`/`COPYING`) file.
- `id` / `name` must not masquerade as the host or its vendor (the CI check
  normalises look-alikes; curators can clear a legitimate exception).
- Every shipped file is listed in `files[]` with its `sha256`; the scaffolded
  smoke test and the registry's CI recompute them.
- Redistribute only artwork and code you have the right to redistribute — the
  registry record carries your `license`, and curators read it.

`floofy dev <path>` symlinks a checkout into place, enables the host's dev mode
where applicable and streams the mod's Loader and SPA-host logs while you
iterate.

## 2. Release the archive

Tag your repository with the mod **version** (`1.2.0` or `v1.2.0`) and attach
the archive — a `.zip` or `.tar.gz` of the mod directory (no symlinks, no `..`
segments) — to that release, reachable over HTTPS. A deterministic archive
(sorted entries, fixed timestamps) lets anyone reproduce your `sha256`; the
scaffold's workflow builds one. The scaffolded
`.github/workflows/floofy-validate.yml` runs `floofy validate` on every push, so
a broken manifest never reaches the release step.

## 3. Open the pull request against the registry

One directory per version, three small files:

```
mods/<id>/mod.json                   {"repo": "<your repository URL>", "tags": [...], "links": {...}}   (first submission only)
mods/<id>/<version>/floofy.json      the manifest exactly as shipped in the archive
mods/<id>/<version>/release.json     {"tag": "<version or v<version>>", "channel": "stable", "publishedAt": "…Z",
                                      "files": [{"url": "https://…/<id>-<version>.zip", "sha256": "<64 hex>", "size": 12345, "name": "…"}]}
```

A registry whose forge has no release assets lists versions by **link** instead
(`registry.json` `recordKind: link`): `release.json` then names your repository,
the mod's directory in it and the **commit** the version is pinned at —
`{"channel": "stable", "publishedAt": "…Z", "link": {"repo": "<your repository URL>",
"path": "<mod directory, or omit>", "commit": "<40 hex>", "manifestSha256": "<64 hex>"}}` —
written by `registry_tools record <registry> <id> --repo <url>`, which reads the
mod on your default branch (or `--ref <branch>`) and records the commit, the
SHA-256 of the canonical `floofy.json` and every file with hash and size. No tag
locates a version: keep the mod under its directory on the branch, bump `version`
in `floofy.json` per release, and re-run `record`. A user's `floofy install <id>`
then fetches exactly that commit from your repository
at that tag with their own git credentials and refuses the checkout if the
commit, the manifest hash or any file differs from the record. The repository
URL must be `ssh://` or `https://`, and a registry may pin the forge it accepts
by pattern. (Asset records still name the GitHub release tag they were published
under: `<version>`, `v<version>` or `<id>-<version>` in a repository holding
several mods.)

For a pure App Kit app (a single `app` part) add `"app": {"name": "<the name in
app.json>"}` when it differs from the mod id: the registry also emits a
KiroCrew-compatible `app-registry.json`, so your app appears in the host's own
App Store when a user has the registry added as a federated app registry. Never
edit `index.json`, `app-registry.json` or `compat.json` by hand — they are
regenerated after merge.

The registry's CI (`validate-submission.yml`, standard-library Python straight
from a FloofyCrew checkout) downloads the assets named in `release.json` and runs
`registry_tools validate-submission <archive> --registry . --tag <tag>` — or,
for a link record, `registry_tools validate-submission --record <id>@<version>
--registry .`, which fetches the pinned commit and compares manifest hash and
files with the record: the schema and `floofy validate`, the hashes and sizes,
the licence, the masquerade check, the release tag of an archive record, and the record's `floofy.json`
against the mod's; then `build --check` proves `index.json` was regenerated from
the records. A curator reviews what the manager will disclose to users — the parts,
the seams they use, the declared network hosts, any governance flag — and
merges.

## 4. Signing and what users verify

On merge the registry rebuilds `index.json` (and `app-registry.json`) and signs
it: a detached Ed25519 signature over the canonical JSON in `index.json.sig`,
with the key id `sha256(public key)[:16]`. The same applies to `compat.json`.
The manager ships each edition's public key pinned; it refuses an unsigned or
mismatched index **by default**, verifies every download's `sha256` and size,
and lets a user loosen those controls only per source and only with an audit
row ([consent-and-trust.md](consent-and-trust.md)). Private keys never live in a
repository; the public registry's CI signs with a repository secret or the
curators sign locally after every merge.

## 5. Compatibility verdicts

`compat.json` is written by the Forge for every KiroCrew release on every channel
of both editions: `tested` / `expected` / `broken` per `mod@version`, with a link
to the run ([forge-runbook.md](forge-runbook.md)). Clients pick the newest
version known to work on their exact host version and never pick a `broken`
one. A curator may override a cell with a reason (`registry_tools compat-merge
… --override <id>@<version>=broken --reason … --by …`). To withdraw a version,
set `"yanked": "<reason>"` in its `release.json`: it stays listed for
transparency and is never picked automatically. To point users at the release
notes of a version, set `"changelog": "https://…"` in its `release.json` (or
`links.changelog` in the manifest): `floofy update --check` and the FloofyCrew
App show that link beside the available update, falling back to the mod's `repo`.

## Federated sources

Anyone can run a registry with the same layout (`registry-tools/README.md`,
`init_registry_repo.sh`), and users add it with `floofy registry add <url>
[--public-key …] [--trust index|owner]`; a mod id that collides across sources
is namespaced by the source. `floofy registry defaults` records the edition's
default source, verified against the pinned key, and — on confirmation — adds
it as an operator row of the host's App Store so app-kind mods appear there too.

## The internal registry

The internal edition's registry uses the same records, the same tooling and the
same signature format; what differs is where it lives, how it is reached, and
that archives may be committed beside the records. Those specifics are
maintained with the internal edition adapter, in its `docs/publishing-internal.md`
(under `editions/`), because they name internal systems this public page must not.

---
Covers Requirements 8.1, 8.2, 8.3, 8.4, 8.6, 8.7, 8.9, 9.2, 14.1, 14.2.
