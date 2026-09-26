"""The gateway half: the KAS verdict is read from the host config, never written."""

import importlib.util
import json
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hook", "__init__.py")


def _load():
    sys.modules.setdefault("floofy", types.ModuleType("floofy"))
    spec = importlib.util.spec_from_file_location("kiro_spec_plan_hook", HOOK)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    return hook


class Ctx:
    def __init__(self, home):
        self.host = types.SimpleNamespace(host_home=home, version="0.7.0.5")
        self.state = {}
        self.routes = types.SimpleNamespace(added=[], add=lambda m, p, h: self.routes.added.append((m, p)))
        self.log = types.SimpleNamespace(info=lambda *a: self.logged.append(("info", a[0] % a[1:] if a[1:] else a[0])),
                                         warning=lambda *a: self.logged.append(("warning", a[0] % a[1:] if a[1:] else a[0])))
        self.logged = []


def main():
    hook = _load()
    with tempfile.TemporaryDirectory() as home:
        cfg = os.path.join(home, "config.json")
        # No config at all -> not kas, with the hint.
        v = hook.verdict(home)
        assert v == {"kas": False, "backend": "(default)", "modes": ["spec", "quick-spec", "bug-fix", "plan"], "hint": hook.ENABLE_HINT}, v
        # Default backend ('').
        json.dump({"agent": {"acp_backend": ""}}, open(cfg, "w"))
        assert hook.verdict(home)["kas"] is False
        # KAS on.
        json.dump({"agent": {"acp_backend": "kas", "model": "x"}}, open(cfg, "w"))
        v = hook.verdict(home)
        assert v["kas"] is True and v["hint"] is None and v["backend"] == "kas", v
        # Garbage config -> not kas, no crash.
        open(cfg, "w").write("{not json")
        assert hook.verdict(home)["kas"] is False

        # activate publishes the facts and warns when off; config is never written.
        json.dump({"agent": {"acp_backend": ""}}, open(cfg, "w"))
        before = open(cfg).read()
        ctx = Ctx(home)
        hook.activate(ctx)
        assert ctx.state["kas"] is False and ctx.state["hint"] == hook.ENABLE_HINT
        assert any(level == "warning" and "acp_backend" in msg for level, msg in ctx.logged), ctx.logged
        assert ("GET", "status") in ctx.routes.added
        assert open(cfg).read() == before, "the hook must never write the host config"
        assert hook._status(None)["kas"] is False
        json.dump({"agent": {"acp_backend": "kas"}}, open(cfg, "w"))
        assert hook.refresh()["kas"] is True and ctx.state["kas"] is True
        hook.deactivate(ctx)
    print("kiro-spec-plan hook OK: verdict from config, warning when off, read-only")


if __name__ == "__main__":
    main()
