"""The boot sequence (Requirement 2.4, 3.5, 3.6, 11.1, 11.2) against a fake host and scratch data home."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from floofy_core.governance import GovernanceSnapshot
from floofy_core.resolver import Reason
from floofy_loader.activation import MOD_NAMESPACE, ModContext
from floofy_loader.boot import BootDeps, boot, deactivate_all
from floofy_loader.consent import REASON_CONSENT_REQUIRED, WARNING_VERSION, read_consent, write_consent
from floofy_loader.host import read_host_facts
from floofy_loader.paths import FloofyPaths
from floofy_loader.state import REASONS, ModState

from loader_testing import LOADER_APP_DIR

HOOK_OK = '''
def activate(ctx):
    ctx.state["marker"] = f"{ctx.mod_id}@{ctx.version} on {ctx.host.version}"
    ctx.state["order"] = list(ctx.state.get("order", [])) + ["activate"]
    ctx.log.info("hello from %s", ctx.mod_id)

def deactivate(ctx):
    ctx.state["order"] = list(ctx.state.get("order", [])) + ["deactivate"]
'''

HOOK_RAISES = '''
def activate(ctx):
    raise RuntimeError("boom at activate")
'''

HOOK_IMPORT_ERROR = '''
import module_that_does_not_exist_anywhere
'''


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_mod(paths: FloofyPaths, mod_id: str, *, code: str | None = HOOK_OK, version: str = "1.0.0", extra: dict[str, Any] | None = None, parts: list[dict] | None = None, files: dict[str, str] | None = None, staged: bool = False) -> Path:
    """Write a schema-valid mod under ``mods/<id>/`` (or ``pending/<id>/``) with correct hashes."""
    root = (paths.pending if staged else paths.mods) / mod_id
    root.mkdir(parents=True, exist_ok=True)
    shipped: dict[str, str] = dict(files or {})
    part_list = parts if parts is not None else []
    if code is not None:
        (root / "hook").mkdir(exist_ok=True)
        (root / "hook" / "__init__.py").write_text(code, encoding="utf-8")
        shipped["hook/__init__.py"] = code
        part_list = [*part_list, {"kind": "python-hook", "side": "gateway", "path": "hook/", "module": "hook"}]
    for rel, text in shipped.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    manifest = {
        "schema": 1,
        "id": mod_id,
        "name": mod_id.title(),
        "version": version,
        "description": "test mod",
        "authors": ["tests"],
        "license": "MIT",
        "kirocrew": {"version": ">=0.7.0 <0.9.0"},
        "dependsOn": {"floofycrew": ">=0.0.0"},
        "parts": part_list,
        "files": [{"path": rel, "sha256": sha(root / rel)} for rel in sorted(shipped)],
    }
    manifest.update(extra or {})
    (root / "floofy.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return root


@pytest.fixture
def env(tmp_path: Path):
    """A scratch host home + data home, host facts for a fake 0.7.0.5 build, and a context factory."""
    home = tmp_path / "home"
    module = types.ModuleType("kiro_crew")
    module.__version__ = "0.7.0.5"
    package_dir = tmp_path / "payload" / "lib" / "python3.12" / "site-packages" / "kiro_crew"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text('__version__ = "0.7.0"\n', encoding="utf-8")
    module.__file__ = str(package_dir / "__init__.py")
    facts = read_host_facts(host=module, env={"KIROCREW_HOME": str(home)}, adapters=[])
    paths = FloofyPaths(facts.data_home).ensure()
    contexts: dict[str, ModContext] = {}

    def make_context(mod_id: str, version: str, mod_dir: Path, manifest: dict[str, Any]) -> ModContext:
        ctx = ModContext(mod_id, version, mod_dir, facts, logging.getLogger(f"floofy.mods.{mod_id}"), data_dir=paths.mod_dir(mod_id))
        contexts[mod_id] = ctx
        return ctx

    unwound: list[str] = []
    deps = BootDeps(paths=paths, facts=facts, make_context=make_context, governance_reader=lambda _f: GovernanceSnapshot(), patch_runner=lambda planned, f, p: {"ran": True, "planned": [pp.label for pp in planned]}, on_deactivated=unwound.append)
    return types.SimpleNamespace(home=home, paths=paths, facts=facts, deps=deps, contexts=contexts, unwound=unwound)


def consent(env) -> None:
    write_consent(env.paths.consent, by="tests")


def enable(env, **flags: bool) -> None:
    env.paths.enabled.write_text(json.dumps(flags), encoding="utf-8")


# --- consent ---------------------------------------------------------------------------------


def test_inert_without_consent_reports_everything_read_only(env):
    make_mod(env.paths, "alpha")
    make_mod(env.paths, "beta", staged=True)
    enable(env, alpha=True)
    result = boot(env.deps)
    state = result.state
    assert state.loader == "inert" and state.consent["required"] is True and state.consent["detail"] == "missing"
    assert state.mods["alpha"].active is False and state.mods["alpha"].reason == REASON_CONSENT_REQUIRED
    assert state.active == [] and result.activations == {}
    assert not any(k.startswith(f"{MOD_NAMESPACE}.alpha") for k in sys.modules), "nothing was imported"
    assert (env.paths.pending / "beta").is_dir() and state.pending["skipped"] == "consent required", "the stage is left alone"
    assert state.patches == {} and not env.paths.quarantine_requests.exists()


def test_outdated_consent_is_required_again(env):
    env.paths.consent.write_text(json.dumps({"warningVersion": WARNING_VERSION - 1, "acknowledgedAt": "x", "by": "me"}), encoding="utf-8")
    status = read_consent(env.paths.consent)
    assert not status.ok and status.status == "outdated"
    make_mod(env.paths, "alpha")
    enable(env, alpha=True)
    assert boot(env.deps).state.mods["alpha"].reason == REASON_CONSENT_REQUIRED


# --- activation ------------------------------------------------------------------------------


def test_consented_boot_activates_hooks_in_order_and_publishes_exports(env):
    consent(env)
    make_mod(env.paths, "alpha", extra={"loadAfter": ["beta"]})
    make_mod(env.paths, "beta")
    enable(env, alpha=True, beta=True)
    result = boot(env.deps)
    state = result.state
    assert state.loader == "ok" and state.order == ["beta", "alpha"] and state.active == ["beta", "alpha"]
    assert state.mods["alpha"].exports["marker"] == "alpha@1.0.0 on 0.7.0.5"
    assert f"{MOD_NAMESPACE}.alpha.hook" in sys.modules and f"{MOD_NAMESPACE}.beta.hook" in sys.modules
    assert state.mods["alpha"].parts[0].status == "active" and "floofy_mods.alpha.hook" in state.mods["alpha"].parts[0].detail
    assert state.mods["alpha"].reason is None and state.consent["required"] is False
    assert env.paths.loader_state.parent == env.paths.data_home
    errors = deactivate_all(result, env.deps)
    assert errors == [] and env.unwound == ["alpha", "beta"], "reverse activation order"
    assert env.contexts["alpha"].state["order"] == ["activate", "deactivate"]
    assert not any(k.startswith(f"{MOD_NAMESPACE}.alpha") for k in sys.modules)


def test_a_raising_mod_is_the_only_casualty(env):
    consent(env)
    make_mod(env.paths, "good")
    make_mod(env.paths, "bad", code=HOOK_RAISES)
    make_mod(env.paths, "worse", code=HOOK_IMPORT_ERROR)
    enable(env, good=True, bad=True, worse=True)
    state = boot(env.deps).state
    assert state.mods["good"].active and state.mods["good"].reason is None
    assert state.mods["bad"].reason == Reason.Error.value and "boom at activate" in state.mods["bad"].detail
    assert any("Traceback" in e for e in state.mods["bad"].errors)
    assert state.mods["worse"].reason == Reason.Error.value and "module_that_does_not_exist_anywhere" in state.mods["worse"].detail
    assert not any(k.startswith(f"{MOD_NAMESPACE}.bad") or k.startswith(f"{MOD_NAMESPACE}.worse") for k in sys.modules)
    assert env.unwound == ["bad", "worse"], "the runtime is told to unwind the failed mods' hooks"
    assert state.mods["bad"].parts[0].status == "error"


def test_missing_or_tampered_file_is_missing_files(env):
    consent(env)
    root = make_mod(env.paths, "alpha")
    (root / "hook" / "__init__.py").write_text("# tampered\n", encoding="utf-8")
    make_mod(env.paths, "beta")
    (env.paths.mods / "beta" / "hook" / "__init__.py").unlink()
    enable(env, alpha=True, beta=True)
    state = boot(env.deps).state
    assert state.mods["alpha"].reason == Reason.MissingFiles.value and state.mods["alpha"].files_ok is False
    assert state.mods["beta"].reason == Reason.MissingFiles.value


def test_unlisted_code_mod_lands_disabled_until_enabled(env):
    consent(env)
    make_mod(env.paths, "alpha")
    state = boot(env.deps).state
    assert state.mods["alpha"].reason == Reason.UserDisabled.value and "land disabled" in state.mods["alpha"].detail
    enable(env, alpha=True)
    assert boot(env.deps).state.mods["alpha"].active


def test_strict_out_of_range_host_is_unsupported_and_non_strict_warns(env):
    consent(env)
    make_mod(env.paths, "strict", extra={"kirocrew": {"version": ">=9.0.0", "strict": True}})
    make_mod(env.paths, "lenient", extra={"kirocrew": {"version": ">=9.0.0"}})
    enable(env, strict=True, lenient=True)
    state = boot(env.deps).state
    assert state.mods["strict"].reason == Reason.Unsupported.value
    assert state.mods["lenient"].active and any(w["code"] == "HostVersionOutOfRange" for w in state.mods["lenient"].warnings)


# --- compat and quarantine -------------------------------------------------------------------


def test_broken_matrix_cell_quarantines_and_requests_the_move(env):
    consent(env)
    make_mod(env.paths, "alpha")
    make_mod(env.paths, "beta")
    enable(env, alpha=True, beta=True)
    env.paths.compat_cache.write_text(json.dumps({"schema": 1, "rows": [{"edition": "internal", "channel": "beta", "hostVersion": "0.7.0.5", "framework": {"loader": "ok"}, "mods": {"alpha@1.0.0": {"verdict": "broken", "run": "https://forge.example/run/1"}, "beta@1.0.0": {"verdict": "tested"}}}]}), encoding="utf-8")
    state = boot(env.deps).state
    assert state.mods["alpha"].reason == Reason.Quarantined.value and state.mods["alpha"].quarantined
    assert state.mods["beta"].active
    request = json.loads((env.paths.quarantine_requests / "alpha.json").read_text(encoding="utf-8"))
    assert request["mod"] == "alpha" and request["hostVersion"] == "0.7.0.5" and request["run"] == "https://forge.example/run/1"
    assert not (env.paths.quarantine_requests / "beta.json").exists()
    assert state.compat["row"]["framework"] == {"loader": "ok"}


# --- governance is a warning, never a reason -------------------------------------------------


def test_governance_closes_nothing_but_warns(env):
    consent(env)
    make_mod(env.paths, "themed", code=None, parts=[{"kind": "theme", "side": "gateway", "path": "theme/theme.json"}], files={"theme/theme.json": json.dumps({"id": "themed", "name": "Themed", "version": "1.0.0"})})
    make_mod(env.paths, "hooked")
    enable(env, themed=True, hooked=True)
    env.deps.governance_reader = lambda _f: GovernanceSnapshot(capabilities={"theme_install": False}, apps_allow_third_party=False, admission_mode="enforce", admission_banned=["floofycrew", "themed"])
    state = boot(env.deps).state
    assert state.mods["themed"].active and state.mods["themed"].reason is None
    assert state.mods["hooked"].active and state.mods["hooked"].reason is None
    codes = {g["code"] for g in state.mods["themed"].governance}
    assert "ThemeInstallClosed" in codes and not state.mods["hooked"].governance
    assert {g["code"] for g in state.governance} >= {"ThirdPartyAppsDisabled", "AppAdmissionEnforced", "AppAdmissionBanned"}
    assert state.governance_snapshot["appsAllowThirdParty"] is False
    for mod in state.mods.values():
        assert mod.reason in (None, *REASONS) and mod.reason != "Governance"


def test_no_code_path_can_set_a_governance_reason():
    """Static guard: every reason comes from the resolver's Reason or the Loader-level ConsentRequired."""
    assert "Governance" not in REASONS and "Governance" not in {r.value for r in Reason}
    with pytest.raises(ValueError):
        ModState("x", "1.0.0").set_reason("Governance")
    source = "\n".join(p.read_text(encoding="utf-8") for p in (LOADER_APP_DIR / "floofy_loader").glob("*.py"))
    for match in re.finditer(r"(?<!def )set_reason\(\s*([^,)]+)", source):
        argument = match.group(1).strip()
        assert argument.startswith("Reason.") or argument in {"REASON_CONSENT_REQUIRED", "reason"} or argument.startswith("decision.reason"), argument
    assert not re.search(r"reason\s*=\s*[\"']Governance", source)


# --- pending, patches, spa publish -----------------------------------------------------------


def test_pending_stage_is_applied_before_the_scan(env):
    consent(env)
    make_mod(env.paths, "alpha")  # installed, to be removed
    make_mod(env.paths, "beta", staged=True)  # staged install
    make_mod(env.paths, "gamma", version="1.0.0")  # installed, replaced by a staged 2.0.0
    make_mod(env.paths, "gamma", version="2.0.0", staged=True)
    (env.paths.pending / "alpha.remove").write_text("", encoding="utf-8")
    enable(env, alpha=True, beta=True, gamma=True)
    state = boot(env.deps).state
    assert state.pending["installed"] == ["beta", "gamma"] and state.pending["removed"] == ["alpha"]
    assert "alpha" not in state.mods and state.mods["beta"].active and state.mods["gamma"].version == "2.0.0"
    assert not (env.paths.pending / "beta").exists() and not (env.paths.pending / "alpha.remove").exists()
    assert json.loads(env.paths.enabled.read_text()) == {"beta": True, "gamma": True}


def test_enabled_patch_parts_go_to_the_patch_runner(env, tmp_path: Path):
    consent(env)
    descriptor = json.dumps({"schema": 1, "target": "kiro_crew/static/dist/index.html", "ops": [{"op": "append-head", "content": "<meta name=\"x\">", "marker": 'name="x"'}]})
    make_mod(env.paths, "patchy", code=None, parts=[{"kind": "patch", "side": "spa", "path": "patches/boot.json"}], files={"patches/boot.json": descriptor})
    enable(env, patchy=True)
    state = boot(env.deps).state
    assert state.mods["patchy"].active and state.patches == {"ran": True, "planned": ["patchy#0"]}
    assert state.mods["patchy"].parts[0].to_dict()["modifiesPayload"] is True


def test_spa_parts_are_published_only_while_active(env):
    consent(env)
    make_mod(env.paths, "ui", code=None, parts=[{"kind": "spa", "side": "spa", "path": "spa/main.js"}, {"kind": "spa", "side": "spa", "path": "boot/first.js", "activation": "boot"}], files={"spa/main.js": "export default 1;\n", "spa/lib/util.js": "export const u = 1;\n", "boot/first.js": "// boot\n"})
    enable(env, ui=True)
    state = boot(env.deps).state
    assert state.mods["ui"].active and (env.paths.spa_dir("ui") / "main.js").is_file() and (env.paths.spa_dir("ui") / "lib" / "util.js").is_file()
    parts = state.mods["ui"].to_dict()["parts"]
    assert parts[0]["activation"] == "runtime" and parts[0]["detail"] == "served from spa/ui/main.js"
    assert parts[1]["activation"] == "boot" and parts[1]["detail"].startswith("boot activation: inlined into index.html")
    enable(env, ui=False)
    state = boot(env.deps).state
    assert state.mods["ui"].reason == Reason.UserDisabled.value and not env.paths.spa_dir("ui").exists()


def test_state_dict_is_json_and_carries_the_vocabulary(env):
    consent(env)
    make_mod(env.paths, "alpha")
    enable(env, alpha=True)
    payload = json.loads(json.dumps(boot(env.deps).state.to_dict(), default=str))
    assert payload["loader"] == "ok" and payload["reasons"] == list(REASONS) and payload["host"]["version"] == "0.7.0.5"
    assert payload["mods"]["alpha"]["parts"][0]["seam"].startswith("Loader") and payload["mods"]["alpha"]["parts"][0]["modifiesPayload"] is False


# --- property: the verdict vocabulary is closed and consent gates activation ------------------


@settings(max_examples=25, deadline=None)
@given(
    flags=st.lists(st.sampled_from([None, True, False]), min_size=3, max_size=3),
    consented=st.booleans(),
    raising=st.lists(st.booleans(), min_size=3, max_size=3),
)
def test_boot_invariants(tmp_path_factory, flags, consented, raising):
    tmp_path = tmp_path_factory.mktemp("boot")
    module = types.ModuleType("kiro_crew")
    module.__version__ = "0.7.0"
    module.__file__ = None
    facts = read_host_facts(host=module, env={"KIROCREW_HOME": str(tmp_path / "home")}, adapters=[])
    paths = FloofyPaths(facts.data_home).ensure()
    ids = ["m1", "m2", "m3"]
    for mod_id, bad in zip(ids, raising):
        make_mod(paths, mod_id, code=HOOK_RAISES if bad else HOOK_OK)
    paths.enabled.write_text(json.dumps({mod_id: flag for mod_id, flag in zip(ids, flags) if flag is not None}), encoding="utf-8")
    if consented:
        write_consent(paths.consent, by="prop")
    deps = BootDeps(paths=paths, facts=facts, make_context=lambda i, v, d, m: ModContext(i, v, d, facts, logging.getLogger("t")), governance_reader=lambda _f: GovernanceSnapshot(), patch_runner=lambda *a: {})
    result = boot(deps)
    state = result.state
    for mod_id, flag, bad in zip(ids, flags, raising):
        mod = state.mods[mod_id]
        assert mod.active == (mod.reason is None)
        assert mod.reason is None or mod.reason in REASONS
        if not consented:
            assert not mod.active
        elif flag is True:
            assert mod.active != bad and (mod.reason == Reason.Error.value if bad else True)
        else:
            assert mod.reason == Reason.UserDisabled.value
    assert set(state.active) == {m for m in ids if state.mods[m].active}
    deactivate_all(result, deps)
    assert not any(k.startswith(MOD_NAMESPACE + ".") for k in sys.modules if k.split(".")[1:2] and k.split(".")[1] in ids)



def test_boot_keeps_the_early_list_in_step_with_the_enabled_set(env):
    """Requirement 3.2: ``early.json`` is regenerated at boot (and after a pending apply) from the enabled ``early: true`` parts."""
    consent(env)
    early_code = "def early(ctx):\n    pass\n\ndef activate(ctx):\n    pass\n"
    make_mod(env.paths, "early", code=None, parts=[{"kind": "python-hook", "side": "gateway", "path": "hook/", "module": "hook", "early": True}], files={"hook/__init__.py": early_code})
    make_mod(env.paths, "plain")
    make_mod(env.paths, "staged", code=None, parts=[{"kind": "python-hook", "side": "gateway", "path": "hook/", "module": "hook", "early": True}], files={"hook/__init__.py": early_code}, staged=True)
    enable(env, early=True, plain=True, staged=True)
    assert not env.paths.early.exists()
    state = boot(env.deps).state
    assert state.mods["early"].active and state.mods["staged"].active
    document = json.loads(env.paths.early.read_text(encoding="utf-8"))
    assert [(m["id"], m["module"], m["part"]) for m in document["mods"]] == [("early", "hook", 0), ("staged", "hook", 0)]
    assert document["mods"][1]["path"] == str(env.paths.mods / "staged"), "the staged mod is listed at its landed path, not pending/"
    enable(env, early=False, plain=True, staged=False)
    boot(env.deps)
    assert not env.paths.early.exists(), "nothing early is enabled: the file goes away"
