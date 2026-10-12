"""Kiro Spec & Plan — the gateway half.

The mod's four agents (`spec`, `quick-spec`, `bug-fix`, `plan`) are plain
drop-ins; nothing here creates them. What this hook does is tell the truth
about whether they WORK: KiroCrew activates a session's agent on the kiro-cli
v3 engine (KAS) with ``session/set_mode {modeId: <agent name>}``, and KAS owns
those four ids as its bundled modes, so the name alone selects Kiro's real Spec
/ Quick Spec / Bug Fix / Plan workflow and the drop-in's own prompt is never
used. On any other backend the same names are ordinary agents whose prompt
merely explains the situation.

So: read ``agent.acp_backend`` from the host's config.json, publish the verdict
through ``ctx.state`` (the SPA half reads it as ``config.read()`` and the
manager page shows it as exports), and log a warning the user can act on. Read
only: the mod never edits the host config — switching backends is the user's
call (``kirocrew config set agent.acp_backend kas``).
"""
from __future__ import annotations

import json
import os

MODES = ("spec", "quick-spec", "bug-fix", "plan")
KAS_BACKEND = "kas"
ENABLE_HINT = "kirocrew config set agent.acp_backend kas"

_ctx = None


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
    return verdict(_ctx.host.host_home) if _ctx is not None else {"kas": False, "backend": "unknown", "modes": list(MODES), "hint": ENABLE_HINT}


def activate(ctx):
    global _ctx
    _ctx = ctx
    ctx.routes.add("GET", "status", _status)
    refresh()


def deactivate(ctx):
    global _ctx
    _ctx = None
