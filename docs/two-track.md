# Two tracks, one codebase: what is measured

FloofyCrew ships to two KiroCrew editions — the internal edition and the public
edition — from one repository: the shared core (`floofy_core`: manifest,
resolver, Patcher, registry client, compatibility cache, the manager CLI), the
Loader app, the SPA host and the documentation are the same files on both
tracks. Only the **edition adapter** differs (`floofy_edition_<edition>`: payload
discovery, host-version derivation, the early-shim location, the re-apply
trigger, the registry endpoints and pinned keys, identity, governance file
locations). Two things are measured on every CI run so this stays true.

## Byte-identity of the shipped artifacts

`scripts/artifact_identity_report.py` builds both editions' release trees the
way the packaging does — the contents of `floofy.pyz` and the Loader app
directory, each with exactly one adapter — hashes every shipped file and
compares the two trees over the union of their paths (a file present in only one
tree counts as different). Every differing file must be an adapter module
(`floofy_edition_*/**`, which includes the adapter's `registry.json`) or an
edition stamp; a difference anywhere else fails the report, as does a share of
identical files below 80 %.

Measured after 1.3.11, on the public-first pathway tree (main: 1.1.0's terminal experience, the FloofyCrew App, `ui` parts and `floofy.mod(id)`, the git-reference and link-record work, 1.2.0's `electron:` target support (`floofy_core.asar`, `floofy_core.electron`) and the `custom-themes` editor, 1.3.0's `floofy.mod(id).dialog` and commit-pinned link records, the unattended freshness pass `floofy_core/freshness.py`, the quarantine heal `floofy_core/quarantine_heal.py`, and the compatibility schema's optional `inherited` verdict field that the internal lanes' delta check writes — every new module is shared core, identical in both trees):

| | identical | total | share |
|---|---|---|---|
| files | 236 | 262 | 90.1 % |
| bytes | 2 892 585 | 2 984 859 | 96.9 % |

The 26 differing paths are the two adapters' modules — 13 files, each counted in
the unpacked zipapp and in the Loader app directory. Run it yourself:

```bash
python scripts/artifact_identity_report.py          # the report; exit 1 below 80 %
python scripts/artifact_identity_report.py --json   # the numbers
python scripts/artifact_identity_report.py --keep /tmp/trees   # keep both trees to diff
```

## The same mod archives on both editions

`scripts/install_both_editions.py` builds each first-party mod into one
deterministic archive — `custom-themes-1.0.3.zip` (sha256
`90a8cbd4bb04c693da5f97c684ad4ae7b248fa5a65bba4de53850c83b7d19a21`, 45 085
bytes: a `ui` editor, a `python-hook` backend, `spa` runtime + boot parts and the
theme-reset `patch`), `rimuru-branding-2.0.1.zip` (sha256
`3ceb6f7eb2fa6f6ea5a62e8d3e59eda66797fc1ce7080fc99e46f661ba23fca2`, 60 635
bytes: a pure `theme` part depending on it) and `settings-demo-1.0.1.zip` (sha256
`df067f853fc16eaefe574ecd38f7a9a409d8005e52d4728aeb2f087fa61c31ef`,
10 366 bytes: a `ui` settings page + a `python-hook` backend route, task 11.6) —
and installs those bytes on two scratch hosts — one that declares itself
the internal edition (a `BUILD_VERSION` stamp, `X.Y.Z.N`) and one that does not
(the public edition) — each with its own scratch data home and a payload copied
from a fixture, never a live install. On each: `floofy init` (the consent
record), then per mod `floofy validate`, `floofy --yes install <archive> --now`,
`floofy enable`, then the Loader's `boot()` in-process. Both must report every
mod **active**; for `custom-themes` the Patcher run and the first-frame boot
script baked into the payload's `index.html` (the `patch` part applies where the
fixture carries the real chunk and is skipped with a `FingerprintMiss`
diagnostic where it holds a trimmed stub — a skip, never a partial rewrite); for
`settings-demo` the hook's `echo` route registered and answering `200` through
the mod-route registry.

```bash
python scripts/install_both_editions.py             # the report; exit 1 on any failed step
python scripts/install_both_editions.py --work /tmp/two-track   # keep the scratch hosts
python scripts/install_both_editions.py --mod mods/settings-demo   # one mod only (repeatable)
```

Both scripts run in CI (`.github/workflows/ci.yml`, job `two-track`) and in the
test suite (`floofy-core/tests/test_two_track.py`), which also checks that the
numbers in this page are the measured ones.

## Comparing two trees

The same script also compares two directories that already exist — `--tree A B`,
labelled with `--names X,Y` (default: the directory names). That answers a
different question from the editions one: when the way an artifact is *produced*
changes, do the old way and the new way still produce the same bytes **for the
same source**? That was the acceptance condition for the public-first cut-over,
and it has to be stated that precisely: a later release is not a counter-example,
because it carries the fixes made after the cut-over and the version bump by
design. Every comparison below therefore holds the source fixed and varies only
the path that built it.

Differences are grouped `expected`, `only in <A>`, `only in <B>` and `other`;
only `other` fails the report, together with an identical share below
`--min-percent`. A file that exists in one tree only is reported on its side and
does not fail on its own — it does lower the percentage, since the denominator is
the union of both trees' paths. `--expect PATTERN[=REGEX]` is repeatable and
moves a path into `expected`:

* bare, any difference on a path fnmatching PATTERN is expected, including a file
  only one tree has;
* with `=REGEX`, only if the file exists in **both** trees and the two are
  identical once every line matching REGEX is dropped from both. So a file that
  may differ *on one stated line* — a vendored tag, a timestamp — is admitted
  without admitting the rest of the file: a change anywhere else still lands in
  `other`.

`fnmatch`'s `*` crosses `/`, so `mods/*` covers that whole subtree.

```bash
# two renders of the same source, one differing line admitted by name
python scripts/artifact_identity_report.py --tree build/old build/new \
    --names old,new --expect 'RELEASE.md=^Vendors public tag '
python scripts/artifact_identity_report.py --tree A B --json   # the numbers
```

Measured over the cut-over (task 18.3): the first two rows hold the source at the
cut-over commit, the last two at `v1.3.12`.

| compared, same source | identical files | identical bytes | differences |
|---|---|---|---|
| the public tree: the old export path's output vs this repository | 1093/1095 (99.8 %) | 9 203 025/9 212 329 (99.9 %) | `.github/workflows/ci.yml` and `CONTRIBUTING.md`, both only in this repository — the export path never emitted them |
| the internal package: rendered from the pre-cut-over single repository vs from this repository plus the overlay | 291/293 (99.3 %) | 4 170 980/4 209 515 (99.1 %) | `mods/README.md` — the composed tree carries the three internal-only mod rows that the single repository's own copy never spelled — and `dist/SHA256SUMS`, on that one file's hash line |
| the internal package at `v1.3.12`: the released package vs a render of the composed tree | 292/293 (99.7 %) | 4 211 132/4 212 734 (99.96 %) | `RELEASE.md`, on the `Vendors public tag` provenance line (and the blank line after it), which a `--root` render does not write |
| the public release at `v1.3.12`: the published assets vs a local build of the tag | 5/5 (100 %) | 1 377 918/1 377 918 (100 %) | none |

---
Covers Requirements 10.1, 10.2, 10.3, 10.4.
