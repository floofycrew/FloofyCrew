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

## What it installs

- four agent specs into `~/.kiro/agents/` (`spec.json`, `quick-spec.json`,
  `bug-fix.json`, `plan.json`), each with a FloofyCrew owner sidecar so
  uninstall removes exactly these files — an agent of the same name that you
  created yourself is never overwritten (the install reports the collision);
- a gateway hook that reads `agent.acp_backend` (read-only) and publishes the
  verdict (`kas`, `backend`, `hint`) as the mod's exports, plus `GET status`;
- a dashboard part that badges the four rows in the agent selector
  ("Kiro mode", or "needs KAS" with the fix in its tooltip).

Nothing in the host is modified; `floofy restore kiro-spec-plan` takes it all
back out.

## Not affiliated

FloofyCrew and this mod are unofficial. The workflows themselves are Kiro's,
shipped inside kiro-cli and executed there; this mod only makes them
selectable from KiroCrew.
