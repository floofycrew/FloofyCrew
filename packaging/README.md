# packaging

The two release tracks of FloofyCrew (design DR-3; Requirement 10.5). Both build
the **same** shared core, Loader app, SPA host, registry client and docs; only
the edition adapter differs, and `scripts/artifact_identity_report.py` measures
how much of the two trees is byte-identical (Requirement 10.4, target ≥ 80 %).
Everything here is standard-library Python 3.12 plus shell, reproducible
(fixed timestamps, sorted entries) and verified by `--check` (build twice,
compare every byte).

| Track | Directory | Output | Distribution |
|---|---|---|---|
| Internal edition | `internal/` (lives in the overlay, see [The overlay build](#the-overlay-build)) | `build/FloofyCrew/`: `dist/floofy.pyz`, the Loader app (`app/floofycrew/` + archive), `SHA256SUMS`, `mods/<id>/`, `install.sh` (+ `banner/`), `app-registry.json`, `RELEASE.md` naming the vendored public tag | the internal data package `FloofyCrew`, rendered from a pinned public tag plus the overlay (`build.sh --out <clone>`; the package itself is an operator app-registry row); the community App Store entry is prepared in `internal/community-registry-submission.md` |
| Public (GitHub) | `public/` | `build/public/`: `dist/floofy.pyz`, the wheel, the Loader app archive, `SHA256SUMS`, plus the `loader-app` tree (`app/`, `app-registry.json`, `install.sh`, `README.md`, `RELEASE.md`) | GitHub release assets for tag `vX.Y.Z` (`.github/workflows/release.yml`), the generated `loader-app` branch, `curl -fsSL …/packaging/public/install.sh \| sh`; `public/registry-row.json` carries the app-registry row and the `registries.json` source with the pinned public key |

The wheel is written by `scripts/build_wheel.py` (a stdlib wheel writer: the
repository is a multi-root workspace with no build backend, so `uv build` has
nothing to build; the writer is deterministic and needs nothing installed).
Public artifacts are scanned for internal identifiers before they ship
(`scripts/check_no_internal_identifiers.py`); the internal package may name
internal systems and is not scanned. Neither track ever contains KiroCrew code
(design DR-1).

## The install scripts

Both editions' `install.sh` are POSIX `sh` with one structure (Requirement 15.2):
five numbered steps with a status glyph each (`✓`/`✗`/`·`, ASCII when the locale
is not UTF-8), the CLI's palette and banner — off when stdout is not a terminal,
under `NO_COLOR`, on `TERM=dumb` or with `--no-color`; `--no-banner` /
`FLOOFYCREW_NO_BANNER=1` skip the art alone — and numbered menus with a default
for the two enumerated answers: the re-apply trigger (`floofy init --trigger` /
`--no-trigger`) and whether to run `floofy init` now. A run without a terminal
takes the defaults, prints them and never blocks; the previous flags and
environment variables still work (`FLOOFYCREW_*`, `--no-init`, `--prefix`, flags
after `--` for the internal script), and `FLOOFYCREW_TRIGGER` / `--trigger`
pre-answer the menu. The banner is `branding/ascii/floofy.ans` (24-bit, when
`COLORTERM` says so) or `floofy.txt` in one colour: the internal package ships
them under `banner/`, the public one-liner embeds them (a piped script has no
files beside it). `packaging/tests/test_install_scripts.py` runs both scripts
headless (`setsid`, `</dev/null`) and on a pseudo-terminal.

## The overlay build

This repository is the source of record for both editions: what lands on `main`
is what ships, and nothing is exported, filtered or reviewed on the way to GitHub
(the conventions are in [CONTRIBUTING.md](../CONTRIBUTING.md)). The internal
edition is built from an **overlay**: a separate internal repository that holds
only what that edition adds &mdash; its edition adapter (a sibling of
`editions/public/`), its internal-only mods, the `internal/` packaging track
described here, the Forge and the desk tooling &mdash; plus one file,
`PUBLIC_PIN`, naming the public tag it is built on. The root README's
[Internal edition](../README.md#internal-edition) section says the same from the
other side. An internal release is exactly "a public release plus these
additions": the overlay carries no copy of the shared core, and the build refuses
a file that exists in both trees. The overlay's own README is the runbook for
that edition (installing it, its test target and CI, contributing a mod to it);
this section is the build's contract.

### The pin

`PUBLIC_PIN` is one JSON file at the overlay root, so `git log -- PUBLIC_PIN` is
the internal release history:

```json
{
  "schema": 1,
  "repo": "https://github.com/floofycrew/FloofyCrew",
  "tag": "v1.3.12",
  "commit": "<40 hex>",
  "treeSha256": "<sha256 over the tag's tree>",
  "pinnedAt": "2026-10-25T09:12:00Z",
  "pinnedBy": "FloofyCrew Forge <forge@floofycrew.invalid>"
}
```

`tag` is what a human reads; `commit` is what the build verifies, so a moved tag
fails the build; `treeSha256` is the cheaper second check that the tree a mirror
served is the pinned one (a digest over every file's mode, content and path, not
of the archive bytes, so it does not depend on the git version). The schema is
`internal/schema/pin.schema.json`; the writer and the verifier are
`internal/pin.py`:

```bash
python packaging/internal/pin.py set vX.Y.Z [--repo URL] [--mirror DIR] [--local N] [--pinned-by IDENT]
python packaging/internal/pin.py verify [--mirror DIR]
python packaging/internal/pin.py show          # the pin and the internal version it names
```

`set` resolves the tag on the public repository (`git ls-remote --tags`, the
peeled commit), fetches the tag's tree &mdash; through a bare mirror of this
repository when there is one (`--mirror DIR`; by default
`$FORGE_HOME/mirrors/FloofyCrew.git`, `~/forge` being the Forge's home, when it
exists), else by a shallow fetch of the tag alone &mdash; computes the digest and
writes the file. It refuses a tree whose `floofy_core.__version__` differs from
the tag's `X.Y.Z`: an internal release of FloofyCrew always carries the public
version. An internal-only fix that cannot wait for a public patch is pinned as
the same public tag with `--local N` (`"localVersion": "+internal.N"`), and the
rendered package calls itself `X.Y.Z+internal.N` (a PEP 440 local version); the
base version never differs from the tag. `verify` re-resolves the tag,
re-fetches the tree and compares both; either difference is a `PinMismatch`.

### Composing

`internal/compose.py` materialises the tree the internal package is rendered
from: the pinned public tag plus the overlay's **render inputs**, its
`OVERLAY_SET` &mdash; the internal edition adapter, the internal-only mods, the
skeleton of the rendered package (`internal/FloofyCrew/`) and the identifier
scanner's plain list (see [The identifier list](#the-identifier-list)). The
Forge, the research notes and the desk tooling are overlay content but never
render inputs. In order:

1. fetch the tag: through the mirror when one is given and holds the tag (it is
   refreshed with `git fetch --tags origin` first; an offline mirror that
   already holds the tag still serves it), else a shallow fetch of the tag alone
   into `work/public.git`;
2. `refs/tags/vX.Y.Z^{commit}` on the repository the archive is taken from must
   equal the pin's `commit`, else `PinMismatch: vX.Y.Z moved …`;
3. extract `git archive <commit>` into `work/tree`;
4. the extracted tree's digest must equal `treeSha256`, else
   `PinMismatch: … tree differs`;
5. **collisions**: every overlay file whose path already exists in the public
   tree (as a file, a symlink or a directory), and every public file standing
   where the overlay needs a directory, is one; the build stops with
   `collision: <path> exists in both the public tag and the overlay`, one line
   per path, exit 1. A shared *directory* such as `mods/` or `scripts/` merges,
   a shared *file* never does, so the overlay cannot silently fork the core;
6. copy the overlay files on top, in sorted order, modes and symlinks kept;
7. the **README sidecar**: `mods/README.md` is the one shared file the overlay
   may touch, and only by appending rows. The overlay never ships its own copy;
   it ships `mods/README.internal.md`, whose table rows (a header-and-separator
   pair, when the sidecar carries one for readability, is dropped) are inserted
   after the last row of the first table of the public `mods/README.md`. A row
   whose first cell already heads a public row is skipped, so composing over a
   README that carries the rows never duplicates them; the sidecar itself is
   consumed, not copied.

Everything is staged under the work directory and renamed into `work/tree` only
when every step passed; on any failure nothing is left behind. `git archive` is
deterministic, the copy is sorted and the append is a pure function of two
texts, so two composes of one pin and one overlay are identical trees, which is
what `--check` below relies on.

### Rendering

```bash
packaging/internal/build.sh [--pin FILE] [--mirror DIR|none] [--out DIR] [--supports supports.json]
packaging/internal/build.sh [--pin FILE] [--mirror DIR|none] --check
packaging/internal/build.sh [--pin FILE] [--mirror DIR|none] --compose-only [--tree DIR]
```

`build.sh` is a one-line wrapper around `internal/build.py` (`python3.12`, or
`$PYTHON`); both take the same flags:

| Flag | What |
|---|---|
| `--pin FILE` | the pin to vendor. Default: `PUBLIC_PIN` at the overlay root; until the cut-over, the staged `internal/overlay/PUBLIC_PIN` |
| `--mirror DIR` | a bare mirror of this repository to fetch the tag through. Default: `$FORGE_HOME/mirrors/FloofyCrew.git` when it exists, else none; `--mirror none` (or `--no-mirror`) forces the shallow fetch |
| `--out DIR` | where the package is rendered (default `build/FloofyCrew/`, gitignored; a fresh clone of the package is a valid target &mdash; everything but `.git/` is replaced) |
| `--supports JSON` | the release stage's `supports.json`, recorded in `RELEASE.md` |
| `--check` | compose and render twice into fresh directories and exit 1 unless both composed trees and both packages are byte-identical, naming every path that differs |
| `--compose-only [--tree DIR]` | write the composed tree (default `build/public-tree/`, replacing an existing one) and render nothing: the overlay's test target imports the public packages from there |
| `--root DIR` | render an already composed tree without composing: no pin is read or verified and `RELEASE.md` carries no `Vendors` line &mdash; for tests and offline renders, never a release |

Rendering is the same `build_package` that built the internal package before the
overlay existed, run with the composed tree as its root: `app/floofycrew/` (the
Loader app), `dist/floofy.pyz`, `dist/floofycrew-loader-app-<version>.zip`,
`dist/SHA256SUMS`, `mods/<id>/` (every first-party mod of the composed tree,
public and overlay alike, tagged `<id>-<version>` for the internal registry's
link records), the skeleton's `install.sh`, `app-registry.json` and `README.md`,
and `RELEASE.md`, which gains one line, `Vendors public tag vX.Y.Z (<commit>)`
(followed by `; internal version X.Y.Z+internal.N` for a local-only fix). The
render tooling (`scripts/build_zipapp.py`, `scripts/build_loader_app.py`, the
deterministic zip of `registry_tools`) is loaded from the composed tree, never
from the overlay checkout: the overlay holds none of it, and the tooling of the
pinned tag is what rendered that tag's public release. The composed tree's
`floofy_core.__version__` must be the pin's `X.Y.Z`, or nothing is rendered.

### The identifier list

The scanner, `scripts/check_no_internal_identifiers.py`, is public; its plain
denylist, `scripts/internal-identifiers.txt`, lives only in the overlay (a list
of the names kept out of the public tree is itself such a list). This repository
carries the list's hashed form, `scripts/internal-identifiers.sha256` &mdash; one
`<sha256 of the lowercase entry>  <length>` line per entry &mdash; and CI's
`de-amazon` job scans every file against it with `--hashed` on every push and
pull request (the required checks are in
[CONTRIBUTING.md](../CONTRIBUTING.md#the-required-checks)). The overlay's
`tools/hash-identifiers.py` writes the hashed file, and its `--check` refuses a
public tree whose copy is stale; the overlay's CI and the internal release
script run it, so the hashed list is current before anything public is pushed
next. The plain list is a render input: the rendered internal package may name
internal systems and is never scanned.

## The public mod records

Public users install mods from the public registry, whose records point at
archive assets on this repository's GitHub releases: one release per mod
version, tagged `<id>-<version>`, whether the mod lives in-tree under `mods/<id>`
or in a repository of its own (one archive home for every first-party mod; the
registry's `archiveUrlTemplate` is unchanged). The Forge writes the records
unattended as part of a release &mdash; the archive records of the mod releases
that now exist, every public matrix row, `registry_tools build --sign
--app-registry --floofycrew X.Y.Z`, one signed commit landed on the registry's
`main` &mdash; and the record logic is public, `seed_release` in
`registry-tools/registry_tools/seed.py`, so a hand-cut release writes the same
records through the overlay's thin CLI over it:

```bash
python packaging/internal/seed_public_records.py --export <public-clone> --registry <registry-clone> \
    [--mods forge/mods.json] [--clone <id>=<dir>]... [--no-standalone]
# one JSON line per mod: id, version, tag, archive path, sha256 — a version already recorded is skipped
```

It covers the first-party mods under `mods/` of the public clone and the mods
that ship from their own public repository, listed in the overlay's
`forge/mods.json`: the one source of truth, read by the Forge's check, widen and
release stages too, with each mod's `id`, its public `repo` (the mod's source of
record), the `branch` the Forge reads and, where one exists, the internal package
that vendors a tag of it and the branch of that package to read. The KiroCrew
IDE, `kirocrew-ide`, is the first entry; in-tree is the default, and a mod gets
an entry only when it is large and the owner asks for it. A listed mod is read
from the **committed** tree of a clone (`git archive`, so nothing untracked
reaches the archive): the directory named after the id next to `--export`, or
`--clone <id>=<dir>`; `--no-standalone` seeds the in-tree mods alone. A
curator-edited `mods/<id>/mod.json` in the registry is never overwritten; a new
standalone mod's `mod.json` links to its own repository.

Then, per line: `registry_tools validate-submission <archive> --registry
<registry-clone> --tag <tag>`; `gh release create <tag> <archive> --target
<release commit> --latest=false` (a commit, never a tag name &mdash; GitHub
answers a tag name with `HTTP 422`); and finally one stamp of the release into
the registry, which rebuilds and signs the index, writes `app-registry.json` and
rewrites the registry README's mod table in one step:

```bash
PYTHONPATH=floofy-core:registry-tools python -m registry_tools stamp <registry-clone> \
    [--floofycrew X.Y.Z] [--sign <key>] [--no-app-registry] [--commit]
```

Run from the public clone, `--floofycrew` defaults to that clone's
`floofy_core.__version__`; `--commit` makes the `chore(index): Stamp floofycrew
X.Y.Z` commit in the registry clone and never pushes. The compatibility rows are
not touched: they come from the Forge (`compat-merge`).

## Cutting a release

A FloofyCrew release has two halves, and the internal half never leads: it
refuses to run unless the public tag it pins exists on the public remote.

**The public half** runs in this repository. `scripts/cut-release.sh <X.Y.Z |
next> [--notes FILE] [--remote NAME] [--repo OWNER/NAME] [--check-only]` folds
the Forge's releases into `CHANGELOG.md`, bumps the version files, runs the gates
(the hashed identifier scan, `packaging/public/build.sh --check`, the test suite)
and makes one commit plus the annotated tag `vX.Y.Z`; the push is yours, and the
pushed tag fires `.github/workflows/release.yml`, which builds the assets and
creates the GitHub release.
[CONTRIBUTING.md](../CONTRIBUTING.md#cutting-a-release-by-hand) walks through
it. The Forge's public lanes do the same unattended when a public KiroCrew
version reaches `matrix-written`.

**The internal half** runs in the overlay once the public tag is pushed:

```bash
tools/cut-release-internal.sh <vX.Y.Z | X.Y.Z> [--mirror DIR|none] [--public-repo URL] \
    [--package-clone DIR] [--registry-clone DIR] [--sign-key FILE] [--no-desk] [--check-only]
```

In order, stopping at the first failure and putting `PUBLIC_PIN` back when it
was changed:

0. preconditions: the overlay clone on `mainline` with a clean tracked tree and
   a git identity that is not the Forge's; the overlay, the clone of the
   rendered package and the clone of the internal registry fetched and not
   behind their origins; `vX.Y.Z` not yet a tag of the overlay or of the
   package, locally or on their origins, and not already the pin's tag; the
   registry's signing key present (`--sign-key`); and the refusal &mdash;
   `refs/tags/vX.Y.Z` must
   resolve on the public repository (`--public-repo`, default the current pin's
   `repo`);
1. the pin: `pin.py set vX.Y.Z --repo <public repo>` writes `PUBLIC_PIN`;
2. the gates: `build.sh --compose-only` (a file in both trees fails here with its
   path named), `tools/hash-identifiers.py --check` against the composed tree's
   `scripts/internal-identifiers.sha256`, `build.sh --check`, the overlay's test
   suite (`uv run pytest`), and the registry pre-flight (the key is the
   registry's; which shipped mod versions it does not list yet);
3. the render: `build.sh --out <package clone> --supports <supports.json>`, the
   supports taken from the composed tree's `pyproject.toml`; `RELEASE.md` must
   name `X.Y.Z` and the vendored tag;
4. the desk check: the render installed on the testing desk, the first-party
   mods installed through the desk's own `floofy`, headless Chromium on the live
   dashboard (`python -m forge deskcheck`); nothing is committed anywhere before
   it passes, so a broken mod leaves only the uncommitted render behind;
   `--no-desk` skips it for a render that was already exercised there;
5. the rendered package `FloofyCrew`: one commit, the annotated tag `vX.Y.Z` and
   one `<mod id>-<version>` tag per `mods/<id>/` of the render not tagged yet;
6. the internal registry: a link record for every shipped mod version it does
   not list yet, pinned at the package commit; the index rebuilt and signed,
   `app-registry.json` and the README table regenerated; one commit, or none when
   everything was listed already;
7. the overlay: `chore(pin): Vendor public vX.Y.Z (<commit>)` with the gate
   results in its body, and the annotated tag `vX.Y.Z` &mdash; the provenance
   record, so every internal release is one `git log -- PUBLIC_PIN` away from
   the public tag it vendors;
8. it prints the three pushes, in that order (overlay, package, registry). The
   pushes are yours; the script never pushes.

`--check-only` runs the preconditions and the gates with the pin written to a
scratch file: nothing tracked in any clone is written, committed or tagged
(only the gitignored `build/public-tree/` is refreshed), and exit 0 means a real
run would get through. The Forge's daily internal fire does this
half unattended once the public twin `X.Y.Z` is `matrix-written`, landing the
same three pushes under its release lock; the overlay's README has the lanes.
