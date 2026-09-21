"""The Loader runs the manager's host-version-change handling in-process before a boot on a new host version (Requirement 6.1, 6.2)."""
from __future__ import annotations

import json
import types
from pathlib import Path

from floofy_core.audit import read_audit
from floofy_core.consent import write_consent
from floofy_core.datahome import DataHome
from floofy_loader.host import read_host_facts
from floofy_loader.runtime import LoaderRuntime

from floofy_testing import fake_payload


def test_handle_host_change_runs_the_cli_in_process_and_records_the_outcome(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FLOOFY_NO_ADAPTERS", "1")
    payload_root = tmp_path / "payload"
    package_dir = fake_payload(payload_root, "0.7.0", build="0.7.0.6")
    home = tmp_path / "home"
    data = DataHome.for_host_home(home).ensure()
    write_consent(data.consent, by="tests", how="test")
    module = types.ModuleType("kiro_crew")
    module.__version__ = "0.7.0.6"
    module.__file__ = str(package_dir / "__init__.py")
    runtime = LoaderRuntime()
    runtime.facts = read_host_facts(host=module, env={"KIROCREW_HOME": str(home)}, adapters=[])
    runtime.paths = data
    runtime._handle_host_change("0.7.0.5")
    assert runtime.host_change is not None and runtime.host_change["previous"] == "0.7.0.5" and runtime.host_change["current"] == "0.7.0.6"
    outcome = runtime.host_change["outcome"]
    assert runtime.host_change["exit"] == 0, runtime.host_change
    assert outcome["change"]["firstRun"] is True and outcome["hostVersion"] == "0.7.0.6"
    assert outcome["anchors"]["pythonAnchors"]["total"] >= 12 and outcome["yeeted"] == []
    assert data.host_state.is_file() and data.hostchange("0.7.0.6").is_file()
    rows = read_audit(data.audit)
    assert rows[-1]["op"] == "apply-if-changed" and rows[-1]["actor"] == "loader"
    assert json.loads(data.host_state.read_text(encoding="utf-8"))["current"] == "0.7.0.6"
    # a second call with nothing changed is a no-op
    runtime._handle_host_change("0.7.0.5")
    assert runtime.host_change["outcome"]["change"]["changed"] is False
    assert runtime.state_dict()["loader"] == "inert", "not booted yet: state_dict is the not-booted stub"
