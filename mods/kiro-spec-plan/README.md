# Kiro Spec & Plan

Adds Kiro's built-in modes to KiroCrew's agent list — **Spec**, **Quick Spec**,
**Bug Fix** and **Plan** — the same structured workflows Kiro IDE offers, run by
the kiro-cli v3 engine (KAS). Pick one as the agent of a chat and the session
runs that workflow: Spec walks requirements → design → tasks with approval at
each step, Quick Spec writes the three documents in one pass, Bug Fix goes
reproduce → diagnose → fix → verify, Plan is read-only and produces an
implementation plan.

## Requires the KAS backend

```
kirocrew config set agent.acp_backend kas
```

KiroCrew activates a session's agent on KAS with `session/set_mode {modeId:
<agent name>}`, and KAS owns `spec`, `quick-spec`, `bug-fix` and `plan` as its
bundled modes — so an agent with one of those names selects Kiro's real
workflow, prompts and all, without this mod copying anything out of Kiro. On
the default (v2) backend the same four agents exist but answer as plain agents
that explain the missing setting; `floofy doctor` and the agent picker's badge
say the same.

## On KiroCrew 0.8.0.9 and later: the activation bridge

From 0.8.0.9 KiroCrew reserves the engine's bundled ids
(`KAS_RESERVED_AGENT_IDS`: `default`, `vibe`, `spec`, `quick-spec`, `bug-fix`,
`plan`, `autonomous`) and refuses, before anything reaches the engine, a
session whose agent carries one — "Rename this crewmate's template: 'spec' is
reserved for a built-in agent …". The reason is sound for a *crewmate* of that
name (the engine would silently run its own agent instead of the crewmate's
prompt and tools); for this mod's four drop-ins that substitution is the whole
point. So the gateway hook narrows the host's refusals to exactly these four
ids, at the places a session start crosses them:

- `kas_agents.to_client_custom_agent` raises on a reserved id; for a mode id it
  returns a tool-less placeholder so the start continues;
- `attach_kas_custom_agents` puts the agent list on the wire; the placeholder is
  removed there, so the engine receives **no** client agent under a bundled id
  and `session/set_mode` selects the built-in (which is what the engine does for
  a bundled id regardless of its registry — verified in the 2.33 and 2.34
  bundles: `switchMode` reads nothing from the registry for one). It is one
  function under two module bindings and both are hooked: `_dispatch`, its
  home, where `build_session_new_params` calls it for `session/new`, and
  `runtime`, which imports its own copy for the MCP-readiness params and
  `session/load`. 0.2.0 hooked only the `runtime` binding, so the placeholder
  still went out on `session/new`: the engine registered it as a `(client)`
  agent beside its own, listed it as a delegation target for the orchestrator,
  and a Spec session's first reply quoted the handle's prompt ("Kiro Spec & Plan
  runs natively only on the KAS backend …") while running on KAS all along;
- `KasHarness.activation_refusal` refuses an advertised mode stamped
  `origin: bundled`; for a mode id that stamp is the expected one;
- `ContextBuilder._resolve_agent_prompt` is where the host puts every custom
  agent's prompt into the turn as an `[AGENT SYSTEM PROMPT]` block, on any
  backend. For a mode id whose session runs on KAS the block is left empty: the
  engine runs the built-in mode with its own instructions, and the handle's
  text ("if you are reading this, KiroCrew is not running on the KAS backend
  …") would otherwise be repeated to you as a false note about the backend,
  which is what a Spec session's first reply did on 0.2.0. Where the session
  runs is asked of the **Agent Backend Router** when it is active
  (`session_backend(key)`, its construction-time record, so a pinned or
  failed-over session reads right from before the first prompt), and of the
  host's configured `agent.acp_backend` otherwise. On any other backend the
  prompt stays, where it tells the truth.

Every other agent id passes through untouched, so the host's protection for
real crewmates stays whole. On a host that never refused these ids (KiroCrew
before 0.8.0.9) nothing is hooked. `GET status` (and the mod's exports) carry
`bridge: {needed, seams: {project, wire, wire_runtime, activate, prompt}}`,
each seam `ok` or `missing: <why>`, so a host that moves one of those functions
shows up as a named gap rather than a mystery refusal.

Why not copy the modes out instead: the Spec / Quick Spec / Bug Fix / Plan
workflows are not prompts you can paste into a crew agent. In the engine bundle
they are mode-level machinery — the orchestrator, the per-phase workflow
sub-agents (`fast-task-workflow`, `bugfix-workflow`, the task-execution
sub-agent that writes `[~]` / `[-]` / `[x]` into `tasks.md`), `user_input`
prompts and the delegation between them. Only the engine runs that, so the
only faithful route is activating its built-in modes.

## What it installs

- four agent specs into `~/.kiro/agents/` (`spec.json`, `quick-spec.json`,
  `bug-fix.json`, `plan.json`), each with a FloofyCrew owner sidecar so
  uninstall removes exactly these files — an agent of the same name that you
  created yourself is never overwritten (the install reports the collision);
- a gateway hook that reads `agent.acp_backend` (read-only) and publishes the
  verdict (`kas`, `backend`, `hint`, `bridge`) as the mod's exports, plus `GET
  status`; on KiroCrew 0.8.0.9+ it also installs the activation bridge above
  (three `replace` hooks, scoped to the four mode ids);
- a dashboard part that badges the four rows in the agent selector
  ("Kiro mode", or "needs KAS" with the fix in its tooltip).

Nothing in the host is modified; `floofy restore kiro-spec-plan` takes it all
back out.

## Not affiliated

FloofyCrew and this mod are unofficial. The workflows themselves are Kiro's,
shipped inside kiro-cli and executed there; this mod only makes them
selectable from KiroCrew.
