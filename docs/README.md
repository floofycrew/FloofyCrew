# FloofyCrew documentation

FloofyCrew is an **unofficial** modding ecosystem for KiroCrew — not affiliated
with Kiro or KiroCrew. It attaches to the KiroCrew you already have (it never
ships a rebuilt host), runs the mods you chose on your explicit consent, and
tells you plainly what it did, so that you can always get back to vanilla.

Everything here is edition-neutral and ships with the public repository, so it
names no internal systems; the internal edition's specifics (its registry
location, its identity, its build host) live with the internal edition adapter
under `editions/` and in `forge/docs/`, and CI scans this directory to keep it
that way.

## For users

| Page | What it answers |
|---|---|
| [cli.md](cli.md) | every `floofy` command and option; where FloofyCrew keeps its files |
| [terminal.md](terminal.md) | using `floofy` at the terminal: colours and the switches that turn them off, the banner, the install scripts' steps and menus, the full-screen consent, the interactive mode's keys, and what automation can rely on |
| [consent-and-trust.md](consent-and-trust.md) | the one-time warning, why governance is a warning and not a gate, the audit log, the network rule, the registry trust controls, what FloofyCrew never does |
| [installing-from-a-link.md](installing-from-a-link.md) | installing from a git reference vs. from the registry: the three source tiers (`unlisted` / `listed` / `tested`), what each verifies, the extra consent line, how a mod moves up a tier |
| [updating.md](updating.md) | how mods survive host updates: the re-apply triggers, the per-version quarantine ("yeet"), rollback, `hold`, getting back to vanilla |
| [migrating-from-the-standalone-patcher.md](migrating-from-the-standalone-patcher.md) | for users of the standalone branding patcher FloofyCrew grew out of |

## For mod authors

| Page | What it answers |
|---|---|
| [manifest.md](manifest.md) | the `floofy.json` reference: identity, host compatibility, graded dependencies, network, parts, files |
| [seams.md](seams.md) | the attachment ladder — which kind lands where, when to use each, and why the host's plugin entry point is never used |
| [python-api.md](python-api.md) | `import floofy`, `ctx.*`, hooks by import path and route, `ctx.http`, events, the early shim, the deprecation policy |
| [spa-api.md](spa-api.md) | `window.floofy`, surfaces, patch inspection, boot activation for first-frame effects, what the served CSP allows |
| [patching.md](patching.md) | patch descriptors, content fingerprints, host-version gates, the import-map path and the alias graph, the reporter and `floofy verify --spa` |
| [publishing.md](publishing.md) | releasing a mod and submitting the record to a registry (pull request + release asset), the CI gate, signing, compatibility verdicts |

## For maintainers

| Page | What it answers |
|---|---|
| [forge-runbook.md](forge-runbook.md) | the release-tracking system: lanes, the ledger and its stages, repair agents, the matrix, releases |
| [two-track.md](two-track.md) | one codebase on two editions: the measured byte-identity of the shipped artifacts and the same-archive proof |

Every page ends with the requirement numbers of the FloofyCrew specification it
covers (`.kiro/specs/floofycrew-modding-ecosystem/requirements.md` in the
repository).

---
Covers Requirement 14.3.
