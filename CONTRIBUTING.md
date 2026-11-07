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
commands (the identifier scan, the identity report, the public build check).

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

## The required checks

Two status checks run on every pull request and on `main`
(`.github/workflows/ci.yml`); both are required for a merge:

| Check | What it runs | Locally |
|---|---|---|
| `de-amazon` | `scripts/check_no_internal_identifiers.py` over the whole tree, against the hashed list `scripts/internal-identifiers.sha256` and the justified allowlist `scripts/public-export-allow.txt` | `python scripts/check_no_internal_identifiers.py --paths . --allowlist scripts/public-export-allow.txt --hashed scripts/internal-identifiers.sha256` |
| `pytest` | the test suite in the locked environment | `uv run pytest` |

`de-amazon` is the boundary that keeps this repository public: no internal
hostname, system or account name anywhere in the tree. The list of names is not
in this repository, only its hashed form is, so the check works on forks and
needs no secret; a hit names the file, the line and the matched text. Fix the
file. An allowlist line needs a structural reason, and the allowlist's header
says which reasons qualify.

## What a Forge release looks like

When you see one of these in `git log`, it is the Forge's, and there is nothing
to do:

- one commit `chore(release): Release floofycrew X.Y.Z` on `main` that touches
  only the version files (`pyproject.toml`, `uv.lock`,
  `floofy-core/floofy_core/__init__.py`, `loader-app/floofy_loader/__init__.py`,
  `loader-app/app.json`), the `Current release` line of `README.md` and the
  `mods/<id>/floofy.json` manifests whose host range it widened (their patch
  versions bumped), plus the annotated tag `vX.Y.Z`;
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
the Supports table. The gates are the two required checks plus
`packaging/public/build.sh --check`. The public release tree and its assets are
described in [packaging/README.md](packaging/README.md).

## Mods

First-party mods live in-tree under `mods/<id>` (the index is
[mods/README.md](mods/README.md)); a mod's archive is a GitHub release
`<id>-<version>` of this repository, and the Forge publishes it. Writing one is
covered by the "For mod authors" pages in [docs/README.md](docs/README.md);
getting it into a registry by [docs/publishing.md](docs/publishing.md).
