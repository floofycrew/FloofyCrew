"""Kiro Spec & Plan — the gateway half.

The mod's four agents (`spec`, `quick-spec`, `bug-fix`, `plan`) are plain
drop-ins; nothing here creates them. KiroCrew activates a session's agent on
the kiro-cli v3 engine (KAS) with ``session/set_mode {modeId: <agent name>}``,
and KAS owns those four ids as its bundled modes, so the name alone selects
Kiro's real Spec / Quick Spec / Bug Fix / Plan workflow and the drop-in's own
prompt is never used. On any other backend the same names are ordinary agents
whose prompt merely explains the situation.

Two jobs, then:

1. Tell the truth about whether the modes WORK: read ``agent.acp_backend`` from
   the host's config.json, publish the verdict through ``ctx.state`` (the SPA
   half reads it as ``config.read()``), and log a warning the user can act on.
   Read only: the mod never edits the host config.

2. Keep the activation route open on hosts that close it. KiroCrew 0.8.0.9
   reserves the engine's bundled ids (``KAS_RESERVED_AGENT_IDS``) and refuses,
   before anything reaches the engine, a session whose agent carries one: a
   *crewmate* named ``spec`` would otherwise be silently replaced by the
   engine's own agent. For these four drop-ins the replacement is the point --
   they are handles for the bundled modes -- so the bridge below narrows the
   host's two refusals to exactly these ids and nothing else:

   * ``kas_agents.to_client_custom_agent`` raises on a reserved id. For a mode
     id it instead returns a minimal, tool-less entry so the session start keeps
     going (the host derives "there is an agent to activate" from that list
     being non-empty).
   * ``attach_kas_custom_agents`` is what puts that list on the wire. It is one
     function reached through two module bindings -- ``_dispatch`` (its home,
     where ``build_session_new_params`` calls it for ``session/new``) and
     ``runtime`` (the import used for the MCP-readiness params and
     ``session/load``) -- and both are hooked. The mode entry is dropped there,
     so the engine receives NO client agent under a bundled id --
     ``session/set_mode`` then selects the built-in, which is what the engine
     does for a bundled id regardless of its registry. Hooking only the
     ``runtime`` binding (0.2.0) left the entry on ``session/new``: the engine
     registered it as a ``(client)`` agent beside its own and listed it as a
     delegation target, and the orchestrator's first reply quoted its prompt.
   * ``KasHarness.activation_refusal`` refuses an advertised mode stamped
     ``origin: bundled``. For a mode id that stamp is the expected one.
   * ``ContextBuilder._resolve_agent_prompt`` puts every custom agent's prompt
     in the turn as an ``[AGENT SYSTEM PROMPT]`` block, on any backend. For a
     mode id whose session runs on KAS (the Agent Backend Router's record when
     it is active, the host's configured backend otherwise) the block is empty:
     the engine runs the built-in mode, and the handle's own text -- "if you
     are reading this, KiroCrew is not on KAS" -- would be repeated to the user
     as a false note about the backend.

   Every other agent id passes through untouched, and a host without a given
   seam (an older KiroCrew that never refused, a newer one that moved the
   function) is reported in ``status`` rather than guessed at.
"""
from __future__ import annotations

import json
import os

MODES = ("spec", "quick-spec", "bug-fix", "plan")
KAS_BACKEND = "kas"
ENABLE_HINT = "kirocrew config set agent.acp_backend kas"

#: The host's reserved-id set. Its presence is what makes the bridge necessary;
#: a host without it never refused these ids and gets no hooks.
RESERVED_IDS_TARGET = "kiro_crew.agent_files"
RESERVED_IDS_ATTR = "KAS_RESERVED_AGENT_IDS"

#: The seams, in the order a session start crosses them. The wire seam is ONE
#: function under TWO names: ``attach_kas_custom_agents`` is defined in
#: ``_dispatch`` and called there by ``build_session_new_params`` (the
#: ``session/new`` request), and ``runtime`` imports its own binding for the
#: MCP-readiness params and ``session/load``. A replace hook rebinds one module
#: attribute, so both must be hooked or the placeholder still travels on
#: ``session/new`` (measured 2026-10-24: the engine then lists it as a
#: ``(client)`` delegation target and the model surfaces its prompt).
SEAM_PROJECT = "kiro_crew.acp.kas_agents:to_client_custom_agent"
SEAM_WIRE = "kiro_crew.acp._dispatch:attach_kas_custom_agents"
SEAM_WIRE_RUNTIME = "kiro_crew.acp.runtime:attach_kas_custom_agents"
SEAM_ACTIVATE = "kiro_crew.acp.harness.kas:KasHarness.activation_refusal"
#: The host puts every custom agent's prompt in the turn as an ``[AGENT SYSTEM
#: PROMPT]`` block, whatever backend the session runs on. For a mode id on KAS
#: the engine runs the built-in mode and that block would hand the model the
#: handle's "if you are reading this, KiroCrew is not on KAS" text -- which it
#: then repeats to the user (measured 2026-10-24: "Kiro Spec & Plan runs
#: natively only on the KAS backend ... I'll continue regardless", on a session
#: the router had pinned to KAS). Emptied for a mode id when the session runs
#: on KAS; kept otherwise, where it tells the truth.
SEAM_PROMPT = "kiro_crew.context:ContextBuilder._resolve_agent_prompt"
SEAMS = {"project": SEAM_PROJECT, "wire": SEAM_WIRE, "wire_runtime": SEAM_WIRE_RUNTIME, "activate": SEAM_ACTIVATE,
         "prompt": SEAM_PROMPT}
#: The seams a working bridge cannot do without: ``project`` lets the session
#: start, ``activate`` lets the engine's bundled stamp through. Either wire
#: binding missing degrades to "the handle travels", which the log names; the
#: prompt seam missing degrades to "the handle text is in the turn".
_REQUIRED_SEAMS = ("project", "activate")

#: Where a session actually runs, when the Agent Backend Router is active: its
#: ``session_backend(key)`` reads the construction-time record, which exists
#: before the first prompt is built and follows a failover. Without it the
#: host's configured backend is the answer (the host picks it once per session).
ROUTER_MODULE = "floofy_mods.agent-backend-router.hook"

#: Prompt of the placeholder entry when the spec carries none. Never reaches the
#: engine on a host with both wire seams; on one without them, the entry goes
#: out as a tool-less client agent, and the engine lists it beside its own
#: agents as a ``(client)`` delegation target the orchestrator may talk to.
_HANDLE_PROMPT = "Handle for a Kiro built-in mode (Kiro Spec & Plan)."

_ctx = None
_bridge: dict = {"needed": None, "seams": {}}


# --- verdict --------------------------------------------------------------------------------------


def _read_backend(host_home):
    """``agent.acp_backend`` from ``<host home>/config.json`` ('' when unset or unreadable)."""
    try:
        with open(os.path.join(str(host_home), "config.json"), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        value = (doc.get("agent") or {}).get("acp_backend", "")
        return value if isinstance(value, str) else ""
    except (OSError, ValueError, AttributeError):
        return ""


def verdict(host_home):
    """The published facts: whether the four modes are live and why."""
    backend = _read_backend(host_home)
    kas = backend == KAS_BACKEND
    return {
        "kas": kas,
        "backend": backend or "(default)",
        "modes": list(MODES),
        "hint": None if kas else ENABLE_HINT,
        "bridge": dict(_bridge, seams=dict(_bridge["seams"])),
    }


def refresh():
    if _ctx is None:
        return None
    facts = verdict(_ctx.host.host_home)
    _ctx.state.update(facts)
    if facts["kas"]:
        _ctx.log.info("Kiro Spec & Plan: KAS backend active — %s are Kiro's built-in modes", ", ".join(MODES))
    else:
        _ctx.log.warning(
            "Kiro Spec & Plan: agent.acp_backend is %s, not %r — the spec/quick-spec/bug-fix/plan agents will "
            "answer as plain agents until you run `%s`", facts["backend"], KAS_BACKEND, ENABLE_HINT)
    return facts


def _status(_request):
    """GET status — the same verdict, for the manager page and anyone curious."""
    if _ctx is not None:
        return verdict(_ctx.host.host_home)
    return {"kas": False, "backend": "unknown", "modes": list(MODES), "hint": ENABLE_HINT,
            "bridge": dict(_bridge, seams=dict(_bridge["seams"]))}


# --- the activation bridge -----------------------------------------------------------------------


def is_mode(agent_id) -> bool:
    return isinstance(agent_id, str) and agent_id in MODES


def project(call_next, agent_id, spec, prompt, *args, **kwargs):
    """``to_client_custom_agent``: a mode id gets a placeholder instead of the host's refusal.

    The placeholder is deliberately NOT the host's own projection under another
    name: that projection derives permissions and writes audit rows keyed on the
    id it is given, and none of it would describe anything that runs. The entry
    exists so the session start sees a non-empty agent list; ``wire`` removes it
    before the engine does.
    """
    if not is_mode(agent_id):
        return call_next(agent_id, spec, prompt, *args, **kwargs)
    description = spec.get("description") if isinstance(spec, dict) else None
    entry = {
        "id": agent_id,
        "prompt": prompt if isinstance(prompt, str) and prompt.strip() else _HANDLE_PROMPT,
        "tools": [],
    }
    if isinstance(description, str) and description:
        entry["description"] = description
    return entry


def wire(call_next, params, agents, *args, **kwargs):
    """``attach_kas_custom_agents``: the mode placeholder never reaches the engine.

    The engine selects a bundled mode by id alone and reads nothing from its
    registry for it, so the only effect a same-named client entry could have is
    to appear as one more delegation target in the engine's agent list. An
    empty list is the host's own "nothing to attach" (the function returns
    before touching ``_meta``).
    """
    if isinstance(agents, list) and any(is_mode(a.get("id")) for a in agents if isinstance(a, dict)):
        agents = [a for a in agents if not (isinstance(a, dict) and is_mode(a.get("id")))]
    return call_next(params, agents, *args, **kwargs)


def activation(call_next, harness, agent, resp, *args, **kwargs):
    """``KasHarness.activation_refusal``: ``origin: bundled`` is the expected stamp for a mode id."""
    if is_mode(agent):
        return None
    return call_next(harness, agent, resp, *args, **kwargs)


def session_runs_on_kas(session_key) -> bool:
    """Whether *session_key*'s provider runs on KAS: the router's record when it has one, else the host config.

    The router's answer is per session (a pin, a failover re-construction); the
    config is what the host uses for every session the router did not build.
    """
    import sys  # noqa: PLC0415

    router = sys.modules.get(ROUTER_MODULE)
    if router is not None and hasattr(router, "session_backend"):
        try:
            backend = router.session_backend(session_key or "")
        except Exception:  # noqa: BLE001 - another mod's bug must not decide this
            backend = None
        if isinstance(backend, str) and backend:
            return backend == KAS_BACKEND
    if _ctx is not None:
        return _read_backend(_ctx.host.host_home) == KAS_BACKEND
    return False


def prompt(call_next, builder, agent, *args, **kwargs):
    """``ContextBuilder._resolve_agent_prompt``: no in-band handle text for a mode id running on KAS.

    The engine runs the built-in mode there and carries its own instructions; the
    handle's prompt exists only to explain the situation on another backend,
    where ``call_next`` still delivers it. Both callers -- the session start and
    the post-compaction re-injection -- pass ``session_key`` by keyword.
    """
    if is_mode(agent) and session_runs_on_kas(kwargs.get("session_key")):
        return ""
    return call_next(builder, agent, *args, **kwargs)


_HOOKS = {"project": project, "wire": wire, "wire_runtime": wire, "activate": activation, "prompt": prompt}


def _host_reserves_modes() -> bool:
    """Whether this host refuses the bundled ids (KiroCrew >= 0.8.0.9)."""
    try:
        module = __import__(RESERVED_IDS_TARGET, fromlist=[RESERVED_IDS_ATTR])
        reserved = getattr(module, RESERVED_IDS_ATTR)
    except Exception:  # noqa: BLE001 - an older host, or not KiroCrew at all
        return False
    try:
        return any(m in reserved for m in MODES)
    except TypeError:
        return False


def install_bridge(ctx) -> dict:
    """Register the three seams; record per seam whether the host had it."""
    _bridge["seams"] = {}
    needed = _host_reserves_modes()
    _bridge["needed"] = needed
    if not needed:
        ctx.log.info("Kiro Spec & Plan: this host does not reserve the bundled mode ids; no bridge needed")
        return dict(_bridge, seams={})
    hooks = getattr(ctx, "hooks", None)
    if hooks is None or not hasattr(hooks, "replace"):
        for name in SEAMS:
            _bridge["seams"][name] = "no-hooks-api"
        ctx.log.warning("Kiro Spec & Plan: this host reserves the mode ids and the loader offers no hook API; "
                        "Spec/Quick Spec/Bug Fix/Plan will be refused at session start")
        return dict(_bridge, seams=dict(_bridge["seams"]))
    for name, target in SEAMS.items():
        try:
            hooks.replace(target, _HOOKS[name])
            _bridge["seams"][name] = "ok"
        except Exception as exc:  # noqa: BLE001 - a host without the seam: report, do not guess
            _bridge["seams"][name] = "missing: %s" % (exc,)
            ctx.log.warning("Kiro Spec & Plan: seam %s not found on this host (%s)", target, exc)
    taken = [n for n, s in _bridge["seams"].items() if s == "ok"]
    if len(taken) == len(SEAMS):
        ctx.log.info("Kiro Spec & Plan: activation bridge installed — %s select Kiro's built-in modes on KAS",
                     ", ".join(MODES))
    elif all(n in taken for n in _REQUIRED_SEAMS):
        missing_wire = [n for n in ("wire", "wire_runtime") if n not in taken]
        if missing_wire:
            ctx.log.warning("Kiro Spec & Plan: bridge installed without the wire seam (%s); the mode handle travels as a "
                            "tool-less client agent the engine lists as a (client) delegation target",
                            ", ".join(SEAMS[n] for n in missing_wire))
        if "prompt" not in taken:
            ctx.log.warning("Kiro Spec & Plan: bridge installed without the prompt seam (%s); the mode handle's prompt "
                            "is injected in-band and the model may repeat its 'not on KAS' note", SEAM_PROMPT)
    else:
        ctx.log.warning("Kiro Spec & Plan: bridge incomplete (%s); the host will refuse the mode ids",
                        ", ".join("%s=%s" % kv for kv in _bridge["seams"].items()))
    return dict(_bridge, seams=dict(_bridge["seams"]))


# --- lifecycle -----------------------------------------------------------------------------------


def activate(ctx):
    global _ctx
    _ctx = ctx
    ctx.routes.add("GET", "status", _status)
    install_bridge(ctx)
    refresh()


def deactivate(ctx):
    global _ctx
    _ctx = None
    _bridge["needed"] = None
    _bridge["seams"] = {}
