# The Forge: how FloofyCrew tracks every KiroCrew release

KiroCrew releases often — several times a week on the internal edition's `beta`
channel alone — and every release can move a fingerprint. The Forge is the
KiroCrew-driven system (it runs as the maintainer's own KiroCrew crons and
agents) that notices every host release on every channel of both editions,
verifies FloofyCrew and the registered mods against it, repairs what broke with
agent help, writes the compatibility matrix, and cuts a FloofyCrew release whose
`supports` list includes the new host version. This page is the operations view
that is safe to publish; the desk-specific notes (hostnames, credentials,
internal lane sources) live in `forge/docs/` and are not part of a release.

FloofyCrew never ships a custom-built host (design decision DR-1). The Forge can
build both hosts from source for **diagnosis only** — to diff bundles between
releases — and nothing it builds reaches a user.

## Lanes

| Lane | Edition | Channel | Watched through |
|---|---|---|---|
| `public-stable` | public | stable | the public update feed (version, wheel URL, sha256) plus the GitHub releases API |
| `public-insider` | public | insider | same |
| `public-nightly` | public | nightly | the feed (daily) |
| `internal-beta` | internal | beta | the internal edition's channel pointers, plus the version-bump commits of the internal source (a merged version bump *is* the release) |
| `internal-stable` | internal | stable | same |

Every lane is independent: the internal `beta` typically leads the public
`stable` and is level with or ahead of the public `insider`. Watchers fire
hourly (the nightly lane daily) and compare what they see with the ledger.

## The ledger and the pipeline

State lives in one JSON ledger, idempotent per `(edition, channel, version)`.
Each observed version walks the same stages:

```
observed → provisioned → checked → (repairing → reviewed →)* → matrix-written → released → notified
```

plus a side state `blocked{reason, since, clears_when}` that is re-evaluated on
every fire. The Forge **never stalls on a question**: a step that cannot proceed
writes a self-clearing blocker (a file that must appear, a time to retry, or an
explicit human decision) and notifies the maintainer once; the next fire behaves
differently the moment the condition holds.

| Stage | What it does |
|---|---|
| provision | installs that exact host version in isolation: the public edition's signed wheel into a scratch venv, or the internal edition's bundle into a scratch home, each with its own `KIROCREW_HOME` — never the maintainer's live install |
| check | `floofy doctor`, the Python anchors, the SPA reporter (headless Chromium against `kirocrew gateway --test-mode` with the Loader app installed), the Loader smoke test (install, trust grant, consent, boot), and every registered mod's smoke test; writes the matrix row |
| repair | on a failing fingerprint, anchor or smoke test: a repair agent receives the evidence bundle (the failing fingerprint, the new chunk excerpt, the previous working patch, the bundle diff between releases) and proposes a change **on a branch with tests**; a second agent reviews it against the rule list; the checks re-run; at most three rounds, then `blocked{needs-human}`. The Forge never merges to the main branch — the maintainer does |
| matrix | merges the row into the edition's `compat.json`; human overrides carry a reason and win |
| release | bumps the version, computes `supports` from every matrix row with `loader: ok`, builds the zipapp and the Loader app archive **twice** and requires them byte-identical, signs the registry snapshots per edition, and publishes (GitHub release + registry pull request; the internal registry commit); a missing credential is an owner blocker, and a dry run publishes nothing |
| notify | a chat message to the maintainer: lane, version, verdict, the models that did the work, links |

## The agent runner

Every agent invocation goes through one runner with retry and model fallback —
`claude-fable-5.1 → claude-fable-5 → claude-opus-5 → claude-sonnet-5`, N tries
per model with backoff on throttling signatures — and every run records which
model completed it (the notification names it). Agents write their output
incrementally; success is a report carrying a `STATUS:` line.

## Reading the matrix

`compat.json` is keyed by `edition × channel × hostVersion`. The `framework`
block says whether the Loader booted (`loader: ok | degraded | broken`), how many
SPA fingerprints and Python anchors matched (`{matched, total, missed[]}`) and
the early-shim version; the `mods` block grades each `mod@version` `tested |
expected | broken` with a link to the run. A release's `supports` list is the
set of host versions whose row says `loader: ok`. The manager consults the same
matrix at boot and when the host version changes ([updating.md](updating.md)).

## Operating it

```bash
python -m forge ledger show [--lane L]            # what every lane knows
python -m forge tick --lane public-stable          # one cron fire: watch, then advance every entry one stage
python -m forge provision|check|matrix|repair LANE VERSION
python -m forge release VERSION [--dry-run]        # cut a FloofyCrew release (dry run: throwaway key, nothing published)
python -m forge notify LANE VERSION [--dry-run]
python -m forge ledger block|clear-block|reevaluate …
```

Owner items are files the Forge waits for (`~/forge/keys/`, `~/forge/secrets/`,
the repair-agent switch); each is a self-clearing blocker and the ledger says
exactly which one. Measured on the maintainer's build host: a provision 20–25 s,
a full check about a minute, a repair round 8–14 minutes of agent time plus a
few minutes of review, a release dry run under a minute.

## What the Forge will not do

Weaken an owner decision: governance never enters a `supports` computation
(only `loader: ok` rows do); the artifacts are the shared core plus the two
adapters exactly as the build scripts emit them; no custom host is ever shipped;
and a repair branch is only ever *proposed* — review and merge stay human.

---
Covers Requirements 9.1, 9.2, 9.3, 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7, 12.8.
