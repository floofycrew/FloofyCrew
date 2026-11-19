---
name: floofy-mod-dev
description: Build, test and publish FloofyCrew mods for KiroCrew without reading the full docs first. Covers scaffolding a mod (floofy new), the develop loop (floofy dev, validate, verify --spa), choosing the right part kind for a goal (theme, ui, spa, python-hook, patch, agent, skill, config, app, appearance), and both publish paths — your own git repository that anyone installs from a git reference, or a record in the FloofyCrew registry (archive release plus a pull request). Use when someone wants to create a KiroCrew mod, theme, or App page, asks how to publish or list a mod, or asks which FloofyCrew seam fits an idea. Edition-neutral; an edition may ship an INTERNAL.md addendum beside this file.
version: 2.0.0
tags: [skill, floofycrew, kirocrew, mods, modding, registry]
---

# FloofyCrew mod development

## Overview

FloofyCrew is the unofficial modding ecosystem for KiroCrew (not affiliated with
Kiro or KiroCrew). A **mod** is a directory with a `floofy.json` manifest and
the files it ships; the manager (`floofy`) installs it, the Loader app runs it
inside the gateway, and the Patcher applies file-level patches. This skill turns
a developer's idea into a working, publishable mod with the minimum of doc
reading.

**Edition addendum.** This page is edition-neutral: it names the public
registry and public hosting. An edition of FloofyCrew may ship an `INTERNAL.md`
beside this file. When one is there, read it right after this page: it replaces
the *Prerequisites* and *Publish* sections with that edition's own (its
registry, its hosting, its review flow) and adds its guardrails. Everything else
here applies on every edition.

The authoritative references, when a detail is needed (all in the FloofyCrew
repository — clone <https://github.com/floofycrew/FloofyCrew> if not already in
one):

| Question | Read |
|---|---|
| manifest fields (`floofy.json`) | `docs/manifest.md` |
| which part kind lands where | `docs/seams.md` |
| gateway-side Python (`ctx.*`, hooks, routes, `ctx.http`) | `docs/python-api.md` |
| dashboard-side JS (`window.floofy`, surfaces, boot parts) | `docs/spa-api.md` |
| patch descriptors and fingerprints | `docs/patching.md` |
| publishing and the registry's CI gate | `docs/publishing.md` |
| how mods survive host updates (quarantine, rollback) | `docs/updating.md` |
| the registry's own layout and tooling | `registry-tools/README.md` |
| the human-facing version of this skill | `CONTRIBUTING.md`, section *Mods* |

Working examples: `floofy-core/examples/<kind>/` (one minimal mod per kind) and
the first-party mods under `mods/` (`settings-demo` for an App settings page
over a `python-hook`, `custom-themes` for themes + hook + routes + a `patch`,
`display-comfort` and `os-notify-bridge` for a runtime `spa` part with its own
App page, `agent-orchestrator` for a `python-hook` that drives the host's
sessions).

## Prerequisites (check before anything else)

```bash
floofy --version         # the manager is installed (else: the README's Install section)
floofy doctor            # consent recorded, Loader app enabled, gateway visible, "no problems found"
floofy registry list     # the edition's default registry is recorded and fetched
```

If `floofy` is missing, install the current release first (the README's
`install.sh` one-liner, which also runs `floofy init`).

## Step 1 — pick the part kind (interview, don't lecture)

Ask what the mod should DO, then map it. A mod may combine several parts.

| The developer wants… | kind | example |
|---|---|---|
| colours/branding/a look for the dashboard | `theme` (+ depend on `custom-themes` for first frame) | `floofy-core/examples/theme` |
| their own page inside the FloofyCrew App (settings, dashboards) | `ui` | `mods/settings-demo` |
| run JS inside the dashboard SPA at runtime | `spa` (activation `runtime`) | `floofy-core/examples/spa` |
| JS before first paint (no-flash effects) | `spa` (activation `boot`) | `mods/custom-themes` boot part |
| gateway-side Python: routes, timers, host events | `python-hook` | `floofy-core/examples/python-hook` |
| edit a host file (dashboard chunk, index.html, app.asar member) | `patch` (`spa` / `gateway` / `electron` side) | `floofy-core/examples/patch` |
| ship a KiroCrew agent / skill / config preset / App | `agent` / `skill` / `config` / `app` | `floofy-core/examples/<kind>` |
| fonts, icons, small look tweaks without a full theme | `appearance` | `floofy-core/examples/appearance` |

Prefer the highest seam that works (theme > ui/spa > python-hook > patch):
patches are the most host-version-sensitive. `docs/seams.md` is the ladder.

## Step 2 — scaffold

```bash
floofy new <kind> --id <mod-id> --name "<Display name>" --dir <parent> [--author <name>]
```

Gives a valid manifest, README, Apache-2.0 LICENSE, a smoke test and a CI workflow.
`<mod-id>`: lowercase, `[a-z][a-z0-9_-]{1,63}`, never claiming the host's or
another product's name (the registry gate enforces a reserved-names list).
Then fill in the parts; keep `files[]` hashes honest with
`python scripts/hash_example_files.py <dir>` (repo checkout) or by re-running
`floofy validate` until clean.

Manifest musts for later publishing: `license` (plus the LICENSE file, no
third-party artwork), `network.hosts[]` listing every host the mod talks to
(HTTPS only; empty list if none), `kirocrew.version` set to the floor the mod
was built against — `>=<oldest host run>`, `>=0.7.0` for a mod that only uses
the seams — with NO upper bound (the matrix row per host is the measurement; a
`<` bound only for a host change the developer knows about, and `strict: false`
unless an out-of-range host MUST refuse the mod), every hard dependency in
`dependsOn` (not `recommends`), honest `description`.

## Step 3 — the develop loop

```bash
floofy dev <mod-dir>            # symlink into mods/<id>, dev mode, reload the Loader
floofy dev <mod-dir> --follow   # …and stream the mod's log + Loader faults
# edit files; reload the dashboard page (spa/ui) or `floofy apply` (patches)
floofy validate <mod-dir>       # schema, hashes, parts, targets, network scan
floofy verify --spa             # for spa/patch parts: what the live gateway actually serves
floofy dev <mod-dir> --unlink   # done
```

The Loader disables a faulting mod and keeps the host running (`floofy doctor`
and the App's Mods page show the fault). `floofy vanilla` boots once with
every mod off if anything looks wrong. Test on the developer's OWN gateway
first; never develop against a shared/production KiroCrew.

## Step 4 — publish (two paths; ask which)

### Path A — their own git repository ("unlisted", zero review, instant)

1. Push the mod to a repository the developer owns (GitHub or any git host
   reachable over HTTPS or SSH), with `floofy.json` at the repository root or
   in a subdirectory. The scaffold's `.github/workflows/floofy-validate.yml`
   runs `floofy validate` on every push.
2. Anyone installs it from the git reference:
   ```bash
   floofy install https://github.com/<owner>/<repo>[@<tag>][#<subdir>]
   ```
   The consent screen marks it "unlisted source"; `files[]` hashes are
   verified against the manifest. Document the line in the repository README.
3. Releases = tags in their repository; their rules, their pace. Only a
   git-reference install reads them: `floofy install <id>` by name needs a
   registry record (Path B).

### Path B — the FloofyCrew registry ("listed", curated)

The registry is the repository <https://github.com/floofycrew/floofycrew-registry>:
records, not files (the Obsidian community-plugins model). The mod's archive is
a release asset of the developer's own repository; the record names it. Bar
(from `docs/publishing.md`): `floofy validate` clean; `license` + LICENSE, no
third-party artwork; no masquerade; `network.hosts[]` declared, encrypted-only;
a version bump per release. Flow (`docs/publishing.md` §2–3 is the reference):

```bash
# in the mod's repo: bump version in floofy.json, commit, tag <version> (or v<version>) and attach
# the archive <id>-<version>.zip to that release (the scaffold's workflow builds a deterministic one)
git clone https://github.com/floofycrew/floofycrew-registry && cd floofycrew-registry
mkdir -p mods/<id>/<version>
#   mods/<id>/mod.json               {"repo": "<repository URL>", "tags": [...], "links": {...}}  — first submission only
#   mods/<id>/<version>/floofy.json  the manifest exactly as shipped in the archive
#   mods/<id>/<version>/release.json {"tag": "<tag>", "channel": "stable", "publishedAt": "<UTC>Z",
#                                     "files": [{"url": "<asset URL>", "sha256": "<64 hex>", "size": <bytes>, "name": "<id>-<version>.zip"}]}
PYTHONPATH=<FloofyCrew checkout>/registry-tools python -m registry_tools validate-submission <archive> --registry . --tag <tag>   # the CI gate, locally
PYTHONPATH=<FloofyCrew checkout>/registry-tools python -m registry_tools readme .                                               # the README mod table
git checkout -b list-<id>-<version> && git add -A && git commit -m "feat: List <id> <version>" && gh pr create --fill
```

The registry's CI runs the same gate on the pull request; the maintainer
regenerates and re-signs the derived files (`index.json`, `app-registry.json`,
`compat.json`) and merges; the Forge adds `tested` compatibility rows later.
First submission of a new id always gets a human look. Never edit or commit the
derived files or the signatures yourself — the gate fails on a stale or foreign
signature, that step is the maintainer's.

### Before you call it published (both paths, every release)

- [ ] A **record** exists for this exact `id@version` in `index.json` of every
      edition shipped to, and the archive release `<id>-<version>` is
      reachable. Matrix rows are keyed by exact `id@version`: a tested version
      without a record helps nobody, and a tag or commit alone is invisible to
      `floofy install <id>`.
- [ ] `kirocrew.version` is a floor, not a forecast: `>=<oldest host the mod
      was built against>` (`>=0.7.0` for a seams-only mod), no upper bound. A
      `<` bound only where the developer KNOWS a later host breaks the mod or
      fixed what it works around — usually a patch descriptor's
      `appliesTo`/`toBuild` (custom-themes' `theme-reset-guard.json`), not the
      manifest range. If a non-strict bound is there and the matrix later
      grades the version tested above it, the Forge removes that bound once
      (one patch bump, one new record; `python -m forge mod-release --dry-run`
      shows the plan) and the mod is measured by its rows from then on; the
      Forge never moves bounds minor by minor.
- [ ] `strict` is a deliberate choice: `false` unless an out-of-range host MUST
      refuse the mod. A non-strict version is admitted on a newer host once its
      row there grades it `tested`/`expected`; a `broken` verdict or a missed
      fingerprint quarantines it on that host whatever the range says.
- [ ] Every hard dependency is in `dependsOn`, not `recommends` (the Loader
      refuses a mod whose declared dependency is missing; the Forge installs
      declared dependencies before grading).
- [ ] Nothing was edited in place at a recorded version: a change, the pin
      included, is a patch bump plus a new record. A mod version changes when
      its code or its declared dependencies change — not per KiroCrew release;
      what moves per new host is FloofyCrew's `supports` list and the matrix
      rows.
- [ ] `floofy install <id>` on a current host picks the version expected (read
      the "no version works" reasons if it does not: they name the excluded
      versions and why).

## Guardrails for an agent running this skill

- Confirm with the developer before ANY push, pull request, release or
  repository creation; never push to the registry's or FloofyCrew's default
  branch.
- Never edit the host install, `agent.apps_trusted`, or consent records on the
  developer's machine beyond what `floofy init`/`floofy dev` themselves do.
- Never invent manifest fields — check `docs/manifest.md` or an example; run
  `floofy validate` after every manifest edit.
- Pins and records: `kirocrew.version` is the floor the developer built
  against (`>=…`), no upper bound — never a forecast of future hosts and never
  a bound "to be safe"; a `<` bound only for a host change the developer knows
  about, `strict: true` if an out-of-range host must refuse. The Forge removes
  a non-strict bound a tested verdict disproves, once, and otherwise leaves
  ranges alone (strict ranges, `||` sets and `<=` bounds are the author's to
  edit); never edit a manifest in place at a recorded version — patch bump
  plus new record; a version is published only when its record exists on each
  edition it ships to; hard dependencies go in `dependsOn`.
- Secrets: a mod must not read SSH keys, browser profiles, credential stores
  or vault paths; `network.credentials` stays `false` unless the developer can
  justify it in the record.
- If `floofy` itself misbehaves, stop and point the developer at
  `floofy doctor --json` output and the FloofyCrew issue tracker, not at
  workarounds that patch the host by hand.
