# Contributing a mod to {{SOURCE_LABEL}}

This registry holds **records**, not files: a version record names your mod's
repository and the tag of the release (a *link record*), and the FloofyCrew
manager clones that tag with the user's own credentials, verifies the commit,
the manifest hash and every file against the record, and installs. Nothing is
uploaded here; a change to this repository is a reviewed change to what every
client of the **{{EDITION}}** edition is offered.

## The community bar

To be listed here a mod must — and the build gate checks each item it can:

1. **Pass `floofy validate` clean**: zero errors on the tagged tree (schema,
   `files[]` hashes, targets, network scan).
2. **Carry a licence**: `license` in `floofy.json` **and** a `LICENSE` (or
   `LICENCE` / `COPYING`) file at the mod root, listed in `files[]`, with **no
   third-party artwork** you do not have the right to redistribute (say where
   the artwork comes from in the README).
3. **Not masquerade** as the host, its vendor or a reserved product name:
   `id` and `name` are checked after homoglyph normalisation against the host's
   and vendor's names, and against [`schema/reserved-names.json`](schema/reserved-names.json)
   — a `leadingToken` name matches exactly or as the leading dash-token
   (`taskei-sync` reads as that product's own mod; `migrate-to-taskei` does not),
   an `exactOnly` name matches only itself. Only the product's owners may list
   such a name; curators record a legitimate exception in `masquerade-allow.txt`.
4. **Declare its network**: every remote host the mod contacts is in
   `network.hosts[]`, and mod traffic is **encrypted only** (`https`, `wss`;
   loopback is the one exception) — a plaintext or undeclared URL in the code is
   flagged at validation.
5. **Name a `contact`** in `mods/<id>/mod.json` ({{CONTACT_KIND}}): who answers
   for the mod.
6. **Tag the release with the version**: the tag equals `version` — `1.2.0`,
   `v1.2.0`, or `<id>-1.2.0` in a repository that holds several mods — and the
   record names that tag.

That is the whole list; it is deliberately shallow so a useful mod can be shared
the week it works. The repository must be on this registry's forge
(`registry.json` `repoUrlPattern`: `{{REPO_URL_PATTERN}}`) and reachable over
`ssh://` or `https://`.

## The change

One change per mod version, three small files:

1. `mods/<id>/mod.json` (first submission only):
   ```json
   {"repo": "<your repository URL>", "contact": "<{{CONTACT_KIND}}>", "tags": ["theme"], "links": {"docs": "https://…"}}
   ```
2. `mods/<id>/<version>/floofy.json` — the manifest **exactly** as in the tagged
   tree (the gate compares them after canonicalisation).
3. `mods/<id>/<version>/release.json`:
   ```json
   {"tag": "<id>-<version>", "channel": "stable", "publishedAt": "2026-09-19T08:00:00Z",
    "link": {"repo": "<your repository URL>", "tag": "<id>-<version>", "path": "<directory of the mod inside the repository, or omit>"}}
   ```
   then `registry_tools build . --resolve-links` clones your tag once and fills in
   `commit`, `manifestSha256` and the checkout's `files[]`; for a pure App Kit app
   (a single `app` part) add `"app": {"name": "<the name in app.json>"}` when it
   differs from the mod id.
4. Regenerate the derived files and sign — `registry_tools build . --app-registry --sign <key>`
   (the maintainer holds the key `{{KEY_ID}}`; an unsigned index fails the gate),
   `registry_tools readme .` — and never edit `index.json`, `app-registry.json`,
   `compat.json` or the README's mod table by hand.
5. Run the gate — `{{GATE_COMMAND}}`, or `PYTHONPATH=src python -m {{GATE_MODULE}}`
   without the build system — and open the review. The gate clones every link
   record at its tag with **your** credentials (`{{GATE_ENV_PREFIX}}_SKIP_CLONE=1`
   skips that step for an offline run; the merge gate never does).

A reviewer looks at what the manager will disclose to users — the parts, the
seams they use, the declared network hosts, any governance flag — and merges.

## Compatibility verdicts

`compat.json` is written by the FloofyCrew Forge per KiroCrew release
(`tested` / `expected` / `broken` per `mod@version`). A curator may override a
cell with a reason (`registry_tools compat-merge compat.json --override
<id>@<version>=broken --edition … --channel … --host-version … --reason … --by …`).
Clients pick the newest version known to work on their exact host version and
never pick a `broken` one; a mod with a `tested` row for the running host shows
as tier **tested**, any other registry record as **listed**, and a mod installed
straight from a git reference as **unlisted**.

## Withdrawing a version

Set `"yanked": "<reason>"` in the version's `release.json`, rebuild and sign: the
version stays listed for transparency but is never picked automatically.
