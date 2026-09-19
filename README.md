# FloofyCrew

An **unofficial** modding ecosystem for KiroCrew — mod manifest, in-host loader,
overlay patcher, mod manager, registry, and a KiroCrew-driven release-tracking
"Forge" — covering both the Amazon-internal Toolbox edition and the public
open-source edition.

Status: **kickoff / spec review.** Nothing here is installed into any KiroCrew yet.

| Path | What |
|---|---|
| `.kiro/specs/floofycrew-modding-ecosystem/requirements.md` | Requirements (EARS acceptance criteria) |
| `.kiro/specs/floofycrew-modding-ecosystem/design.md` | Decision records (DR-1..4) + system design |
| `.kiro/specs/floofycrew-modding-ecosystem/tasks.md` | Phased implementation plan with spikes |
| `research/` | The four investigation reports, prompts, reconciled discrepancies |
| `tools/agent-run.sh` | Research/repair agent runner with retry + model fallback |
| `.kiro/agents/floofy-researcher.json` | Read-only research agent template (per-model variants are derived by the runner) |

Related: ***REMOVED***'s existing `***REMOVED***KiroCrewThemePatcher` is the direct ancestor of
the Patcher component and the first mod to be ported.
