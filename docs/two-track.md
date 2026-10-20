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

Measured at 1.3.7 (main: 1.1.0's terminal experience, the FloofyCrew App, `ui` parts and `floofy.mod(id)`, the git-reference and link-record work, 1.2.0's `electron:` target support (`floofy_core.asar`, `floofy_core.electron`) and the `custom-themes` editor, 1.3.0's `floofy.mod(id).dialog` and commit-pinned link records, the unattended freshness pass `floofy_core/freshness.py`, the quarantine heal `floofy_core/quarantine_heal.py` — every new module is shared core, identical in both trees):

| | identical | total | share |
|---|---|---|---|
| files | 233 | 259 | 90.0 % |
| bytes | 2 845 209 | 2 937 483 | 96.9 % |

The 26 differing paths are the two adapters' modules — 13 files, each counted in
the unpacked zipapp and in the Loader app directory. Run it yourself:

```bash
python scripts/artifact_identity_report.py          # the report; exit 1 below 80 %
python scripts/artifact_identity_report.py --json   # the numbers
python scripts/artifact_identity_report.py --keep /tmp/trees   # keep both trees to diff
```

## The same mod archives on both editions

`scripts/install_both_editions.py` builds each first-party mod into one
deterministic archive — `custom-themes-1.0.1.zip` (sha256
`f0125720a56e9459e1f0fc677f4a64e232f26decab0e31e859057e1bc058d4c5`, 41 667
bytes: a `ui` editor, a `python-hook` backend, `spa` runtime + boot parts and the
theme-reset `patch`), `rimuru-branding-2.0.0.zip` (sha256
`af46e27a3953a9b4686658651222d3a60813f91a8fa7a373a652851f64a6111e`, 57 596
bytes: a pure `theme` part depending on it) and `settings-demo-1.0.0.zip` (sha256
`7e41c05c099046cf3974dc4baa148ff57b079a7b23fe73484bf29182a77cb016`,
7 037 bytes: a `ui` settings page + a `python-hook` backend route, task 11.6) —
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

---
Covers Requirements 10.1, 10.2, 10.3, 10.4.
