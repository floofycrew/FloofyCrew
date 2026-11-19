# Contributing to FloofyCrew

This repository is the source of record for FloofyCrew on both editions of
KiroCrew: what lands on `main` here is what ships, and the internal edition
follows a tag of it through an overlay (the README's
[Internal edition](README.md#internal-edition) section; anything that belongs to
that edition alone goes to the overlay, never here). Nothing is exported,
filtered or reviewed between a commit here and GitHub, so the conventions below
are the whole process.

## Set up

```bash
git clone https://github.com/floofycrew/FloofyCrew && cd FloofyCrew
uv sync                     # the project's interpreter and dev tools (pytest, hypothesis, playwright)
uv run pytest               # the whole suite (~5 min; Playwright tests need Chromium)
```

The README's [Development](README.md#development) section lists the other local
commands (the identity report, the public build check).

## Your git identity in this clone

Commits are attributed by the clone's own `.git/config`, not by a global
setting, so set the identity once per clone, without `--global`:

```bash
git config user.name  "Oscar Tseng"
git config user.email "oscar217b@gmail.com"
```

Those two values are the maintainer's public identity: the one every commit of
the maintainer on this repository carries and the one GitHub links to the
maintainer's account. A tool used to write them into the public clone; now the
clone's config is the only place they live, which is why they are spelled here.
A contributor sets their own name and address the same way.

One identity is reserved: `FloofyCrew Forge <forge@floofycrew.invalid>` is the
release train's (`git log --author=forge@` separates its commits from everybody
else's), and `scripts/cut-release.sh` refuses to cut a release under it, since a
hand-cut release is a person's. That script also reads `user.name` and
`user.email` from the clone's `.git/config` only and stops when either is
missing.

## Branches, and what `main` means

- **Feature work lives on branches.** A change goes to `main` when it is
  complete: tests, docs and its `CHANGELOG.md` entry under `## Unreleased`
  included. Half of a change stays on its branch.
- **`main` is always releasable.** The Forge, the unattended release train
  described in [docs/forge-runbook.md](docs/forge-runbook.md), tests every new
  KiroCrew release against a fresh clone of `main` and, when everything passes,
  cuts a FloofyCrew release from another fresh clone of `main` without asking.
  Whatever is on `main` at a nightly tick may ship as a patch.
- **Holding a release.** When something has landed on `main` that must not ship
  yet, the hold is one file on the Forge's host: remove `~/forge/publish-enabled`.
  Every Forge push to a shared remote (`main`, tags, GitHub releases, the
  registry) then waits behind a self-clearing blocker that lets go the moment the
  file is put back. Nothing is lost in between, the prepared release stays in the
  Forge's scratch directory, and nothing else has to be stopped.
- **The Forge yields to you.** It never touches a human working tree, never
  force-pushes, and lands its commits by fast-forward only: when `main` moved
  since it cloned, it rebases its own commit and re-runs its gates. It also
  waits out a quiet period after the last human commit on `main` (two hours by
  default) before cutting from it, so a release never lands between two pushes
  of one piece of work.

## The required check

One status check runs on every pull request and on `main`
(`.github/workflows/ci.yml`); it is required for a merge:

| Check | What it runs | Locally |
|---|---|---|
| `pytest` | the test suite in the locked environment | `uv run pytest` |

## What a Forge release looks like

When you see one of these in `git log`, it is the Forge's, and there is nothing
to do:

- one commit `chore(release): Release floofycrew X.Y.Z` on `main` that touches
  only the version files (`pyproject.toml`, `uv.lock`,
  `floofy-core/floofy_core/__init__.py`, `loader-app/floofy_loader/__init__.py`,
  `loader-app/app.json`), the `Current release` line of `README.md` and the
  `mods/<id>/floofy.json` manifests whose disproved host bound it removed (their
  patch versions bumped), plus the annotated tag `vX.Y.Z`;
- the tag fires `.github/workflows/release.yml`, which builds the assets and
  creates the GitHub release against that tag with `RELEASE.md` as its notes;
- one GitHub release `<mod id>-<version>` per bumped first-party mod, with the
  mod's archive as its one asset; the public registry's records and matrix rows
  follow;
- **it never edits `CHANGELOG.md`.** A Forge release's notes live in the
  `RELEASE.md` of its GitHub release. The next hand-cut release folds them in:
  `scripts/cut-release.sh` first runs `scripts/fold_forge_releases.py`, which
  inserts one `## A.B.C — <tag date>` section per Forge tag newer than the
  changelog's newest heading, with the body of that release's `RELEASE.md`,
  under `## Unreleased`; the human release's own heading then goes on top.

So write your changelog entries under `## Unreleased` and leave the version
files alone on a feature branch. The one file every branch edits never conflicts
with a Forge commit.

## Cutting a release by hand

```bash
scripts/cut-release.sh next --check-only   # fold, bump, gate, then put every file back
scripts/cut-release.sh next                # the same, then one commit and the annotated tag vX.Y.Z
git push origin main vX.Y.Z                # the script prints this command; it never pushes
```

`next` is the highest `vX.Y.Z` on the remote plus one patch number: the script
reads the remote's tags itself (`git ls-remote --tags`), because the Forge may
have released since your last pull. It needs a clean tracked tree, the identity
above, a branch that is not behind the remote, no `vX.Y.Z` on the remote yet,
and something to release: `## Unreleased` entries, or Forge releases waiting to
be folded (the fold needs `gh`). `--notes FILE` adds an intro paragraph above
the Supports table. The gates are the required check (`pytest`) plus
`packaging/public/build.sh --check`. The public release tree and its assets are
described in [packaging/README.md](packaging/README.md).

## Mods

First-party mods live in-tree under `mods/<id>` (the index is
[mods/README.md](mods/README.md)); a mod's archive is a GitHub release
`<id>-<version>` of this repository, and the Forge publishes it. Writing one is
covered by the "For mod authors" pages in [docs/README.md](docs/README.md);
getting it into a registry by [docs/publishing.md](docs/publishing.md). You do
not have to read either first:

### The mod-dev agent

The checkout ships a kiro-cli agent and skill that carry the whole mod-author
path — the interview about what the mod should do, the part kind, `floofy new`,
the `floofy dev` loop, `floofy validate`, and the publish step (your own
repository installed by git reference, or a record in
[floofycrew-registry](https://github.com/floofycrew/floofycrew-registry)):

```bash
git clone https://github.com/floofycrew/FloofyCrew && cd FloofyCrew
scripts/kiro-workspace-sync.sh   # once: puts the agent under .kiro/agents/ (untracked, see below)
kiro-cli chat                    # then pick the agent: /agent → floofy-mod-dev
```

Tell it what the mod should do. It scaffolds, iterates on your own gateway,
keeps the manifest valid, and walks the publish path with you; the push, the
release and the pull request stay yours — the agent never pushes, releases or
opens one. It also works without a checkout, against the installed `floofy`
alone.

- The **skill** is `.kiro/skills/floofy-mod-dev/SKILL.md` (tracked; the method,
  edition-neutral: it names the public registry and public hosting and nothing
  else). It is the one copy: an edition that needs more ships an `INTERNAL.md`
  *beside* it — the internal edition's overlay does — which replaces the
  skill's Prerequisites and Publish sections and adds that edition's guardrails,
  and the agent reads both. So a change to how mods are built goes here, once;
  a change to how one edition publishes goes to that edition's addendum.
  Keep this file free of edition specifics.
- The **agent**'s source is `editions/public/kiro/agents/floofy-mod-dev.json`;
  `.kiro/agents/` is deliberately untracked (KiroCrew rewrites those files in
  place whenever a session opens on the checkout), so
  `scripts/kiro-workspace-sync.sh` materialises it from the source; re-run it
  after pulling a change to the source (`--check` tells you). Edit the source,
  never the copy. The internal edition keeps its own agent beside its addendum.

### Pins and records

What a user can install is decided by the registry record and the compatibility
matrix together, never by the manifest alone. The rules, each with its reason:

- **`kirocrew.version` is a floor, not a forecast.** Declare `>=<the oldest
  host you built against>` and no upper bound: `>=0.7.0` for a mod that only
  uses the seams, since those are the hosts FloofyCrew supports. A mod does not
  need a new version per host release; its contract is `dependsOn.floofycrew`
  plus the seams, and the matrix row for each host is the measurement (a
  `broken` verdict or a missed fingerprint quarantines the mod on that host,
  whatever the range says). With `strict: false` (the default) an out-of-range
  host loads the mod with a warning, and the client admits a version there once
  the row for that exact host grades that exact `id@version` `tested` or
  `expected`. Add a `<` bound only for a host change you know of: a later host
  that breaks the mod, or that fixed the thing the mod works around
  (`mods/custom-themes/patches/theme-reset-guard.json` is bounded with
  `toBuild` for exactly that reason). Set `strict: true` only when an
  out-of-range host MUST refuse the mod.
- **A version users should install needs a record** in the registry index and,
  publicly, the archive release `<id>-<version>`. A tag or a commit alone is
  invisible to `floofy install <id>`. Rows are keyed by exact `id@version`, so a
  tested version without a record helps nobody, and an older record outside its
  range with no verdict is excluded: installs on a new host then answer "no
  version works".
- **A recorded version is immutable.** Any change, the range included, is a
  patch bump plus a new record. A manifest edited in place at a recorded version
  is never seen by users.
- **A mod version changes when its code or its declared dependencies change,
  and the Forge publishes it.** The `mod-release` step tags `<id>-<version>`,
  archives it and writes the records on both editions, with no FloofyCrew
  release (`python -m forge mod-release --dry-run` shows the plan). What moves
  per new host is FloofyCrew's own `supports` list and the matrix rows, not
  the mods. The Forge never moves a bound minor by minor: a non-strict `<`
  bound that a `tested` verdict disproves is removed once (one patch bump, one
  new record), after which the mod is measured, not forecast. A `strict` range
  is yours alone to edit.
- **Hard dependencies go in `dependsOn`, not `recommends`.** The Loader refuses
  a mod whose declared dependency is not installed, and the Forge installs the
  declared dependencies before it grades a mod; a dependency left in
  `recommends` (or undeclared) is outside that install set, and a mod that
  needs it is graded `broken` on a host where it would have worked.
- **A mod in its own repository with an internal package too:** the Forge lands
  the bound removal on the public repository; the internal branch is the
  maintainer's to move, and each edition's records follow what its branch
  carries.
- **After publishing by hand,** confirm the record is in the registry index of
  every edition you ship to and that `floofy install <id>` on a current host
  picks the version you expect. A hand-cut FloofyCrew release writes no public
  mod records.
