"""The Loader app manifest is a valid KiroCrew App that uses only the App Kit seam (Requirement 2.3, 3.1)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from loader_testing import LOADER_APP_DIR, REPO_ROOT, bundle_interpreter, scratch_payload, site_packages

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import build_loader_app  # noqa: E402

MANIFEST = LOADER_APP_DIR / "app.json"
#: The host's own app-name grammar (``kiro_crew/apps/manifest.py`` L34 ``KEBAB_RE``).
KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
#: The host's hook-path grammar (``manifest.py`` L672 ``HooksConfig._HOOK_PATH_RE``).
HOOK_PATH_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*:[a-zA-Z_][a-zA-Z0-9_]*$")


def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def built_icon_path(out: Path) -> str:
    return json.loads((out / "app.json").read_text(encoding="utf-8"))["iconPath"]


def test_manifest_shape_matches_the_host_grammar():
    doc = manifest()
    assert doc["name"] == "floofycrew" and KEBAB_RE.fullmatch(doc["name"])
    assert re.match(r"^\d+\.\d+\.\d+([+-]|$)", doc["version"])  # manifest.py L35 SEMVER_RE
    assert doc["displayName"] and doc["description"]
    assert doc["displayName"] == "FloofyCrew" and "unofficial" in doc["description"].lower()  # Requirement 16.1, 11.8 (the App's banner says it too)
    hooks = doc["backend"]["hooks"]
    assert set(hooks) == {"routes", "on_startup", "on_shutdown"}
    for value in hooks.values():
        assert HOOK_PATH_RE.match(value), value
        assert value.startswith("floofy_loader.hooks:")
    assert doc["ui"]["entry"] == "index.mjs" and (LOADER_APP_DIR / "ui" / "index.mjs").is_file()
    assert not doc["ui"]["entry"].startswith("ui/")  # UIConfig.entry resolves against ui/ already (manifest.py L616-620)
    assert "crons" not in doc and "mcpServers" not in doc and "entryPoint" not in doc["backend"]
    assert doc["permissions"] == {}


def test_store_icon_is_declared_and_single_sourced():
    """``iconPath`` is the store icon: the host serves ONLY manifest-declared paths from
    ``/apps/<name>/art/`` (``kiro_crew/apps/routes.py`` ``_ART_MANIFEST_FIELDS``), images only,
    relative to the app dir. The bytes are the branding mark, not a second drawing."""
    doc = manifest()
    icon = doc["iconPath"]
    assert not icon.startswith(("/", "./")) and ".." not in icon
    assert Path(icon).suffix == ".svg"
    installed = LOADER_APP_DIR / icon
    assert installed.is_file(), installed
    assert installed.read_bytes() == (REPO_ROOT / "branding" / "logo.svg").read_bytes(), "loader-app/art/icon.svg must be a copy of branding/logo.svg"
    assert installed.read_text(encoding="utf-8").lstrip().startswith("<svg")
    assert "<script" not in installed.read_text(encoding="utf-8")


def test_hook_modules_exist_and_never_register_a_plugins_entry_point():
    """Requirement 3.1: the App Kit seam only; no ``kirocrew.plugins`` anywhere in the app."""
    for value in manifest()["backend"]["hooks"].values():
        module, _, attr = value.partition(":")
        path = LOADER_APP_DIR / (module.replace(".", "/") + ".py")
        assert path.is_file(), path
        assert re.search(rf"^def {attr}\(", path.read_text(encoding="utf-8"), re.M), f"{attr} missing in {path}"
    for path in LOADER_APP_DIR.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".json", ".toml", ".cfg", ".txt"} and "tests" not in path.parts and "build" not in path.parts:
            assert "kirocrew.plugins" not in path.read_text(encoding="utf-8", errors="replace").replace("``kirocrew.plugins``", ""), path


def test_build_assembles_a_self_contained_app_dir(tmp_path: Path):
    editions = list(build_loader_app.EDITIONS)
    out = build_loader_app.build(tmp_path / "floofycrew", editions)
    assert (out / "app.json").is_file() and (out / "floofy_loader" / "hooks.py").is_file()
    assert (out / "floofy_core" / "resolver.py").is_file(), "floofy_core must be vendored beside floofy_loader"
    for package in build_loader_app.EDITIONS.values():
        assert (out / package / "__init__.py").is_file(), package
    assert (out / "ui" / "index.mjs").is_file()
    assert (out / built_icon_path(out)).is_file(), "the declared store icon ships in the built app"
    for name in ("host.mjs", "boundary.mjs", "events.mjs", "surfaces.mjs", "patches.mjs", "reporter.mjs"):
        assert (out / "ui" / name).is_file(), f"the SPA host module {name} is shipped in ui/"
        assert (out / "ui" / name).read_bytes() == (REPO_ROOT / "spa-host" / "src" / name).read_bytes()
    assert not list(out.rglob("__pycache__")) and not (out / "floofy_loader" / "tests").exists()
    built = json.loads((out / "app.json").read_text(encoding="utf-8"))
    assert built["version"] == build_loader_app.core_version()
    assert not build_loader_app.differs(out, build_loader_app.build(tmp_path / "again", editions))


def test_shim_imports_the_runtime_from_the_built_app_dir(tmp_path: Path):
    """Loaded the way the host loads it (by file path, no sys.path help), the shim finds floofy_loader and floofy_core."""
    out = build_loader_app.build(tmp_path / "floofycrew", ["public"])
    code = f"""
import importlib.util, importlib.machinery, json, sys
spec = importlib.util.spec_from_file_location("_kirocrew_app_floofycrew.floofy_loader.hooks", {str(out / 'floofy_loader' / 'hooks.py')!r})
pkg = importlib.util.module_from_spec(importlib.machinery.ModuleSpec("_kirocrew_app_floofycrew", None, is_package=True)); pkg.__path__ = [{str(out)!r}]
sys.modules["_kirocrew_app_floofycrew"] = pkg
sub = importlib.util.module_from_spec(importlib.machinery.ModuleSpec("_kirocrew_app_floofycrew.floofy_loader", None, is_package=True)); sub.__path__ = [{str(out / 'floofy_loader')!r}]
sys.modules["_kirocrew_app_floofycrew.floofy_loader"] = sub
module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
class Ctx:
    name = "floofycrew"; data_dir = {str(tmp_path / 'home' / 'apps' / 'floofycrew' / 'data')!r}; logger = None
assert module.register_routes(Ctx()) == [] or isinstance(module.register_routes(Ctx()), list)
module.on_startup(Ctx())
import floofy_loader.runtime, floofy_core
print(json.dumps({{"runtime": floofy_loader.runtime.__file__, "core": floofy_core.__file__, "started": floofy_loader.runtime.RUNTIME.started}}))
"""
    env = dict(os.environ, KIROCREW_HOME=str(tmp_path / "home"), PYTHONPATH="")
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, env=env, check=False)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert Path(report["runtime"]).is_relative_to(out) and Path(report["core"]).is_relative_to(out)
    assert report["started"] is True
    assert not (tmp_path / "home" / "floofy" / "loader-failure.json").exists()


def test_shim_records_a_failure_instead_of_raising(tmp_path: Path):
    """Requirement 3.6: a failing Loader leaves loader-failure.json and returns normally."""
    out = build_loader_app.build(tmp_path / "floofycrew", ["public"])
    code = f"""
import importlib.util, sys
spec = importlib.util.spec_from_file_location("hooks_under_test", {str(out / 'floofy_loader' / 'hooks.py')!r})
module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)  # registered first, as the host's module_loader does
class Ctx: name = "floofycrew"; data_dir = "x"; logger = None
assert module.on_startup(Ctx()) is None
assert module.register_routes(Ctx()) == []
"""
    env = dict(os.environ, KIROCREW_HOME=str(tmp_path / "home"), FLOOFY_LOADER_SELFTEST_FAIL="1", PYTHONPATH="")
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, env=env, check=False)
    assert result.returncode == 0, result.stderr
    record = json.loads((tmp_path / "home" / "floofy" / "loader-failure.json").read_text(encoding="utf-8"))
    assert record["phase"] in {"startup", "routes"} and "SELFTEST" in record["error"] and record["traceback"]


def test_manifest_validates_with_the_host_manifest_loader():
    """The host's own ``AppManifest`` accepts ``app.json`` (skipped without a payload copy)."""
    payload = scratch_payload()
    if payload is None:
        pytest.skip("no payload copy under .scratch (set FLOOFY_SCRATCH_PAYLOAD)")
    interpreter = bundle_interpreter(payload)
    packages = site_packages(payload)
    assert interpreter is not None and packages is not None
    code = f"""
import json
from pathlib import Path
from kiro_crew.apps.manifest import AppManifest
m = AppManifest.from_json_file(Path({str(MANIFEST)!r}))
errors = m.validate(app_root=Path({str(LOADER_APP_DIR)!r}))
print(json.dumps({{"errors": errors, "name": m.name, "hooks": m.backend.hooks.to_dict(), "entry": m.ui.entry, "perm": m.permissions.to_dict()}}))
"""
    env = dict(os.environ, PYTHONPATH=str(packages), KIROCREW_HOME=str(payload.parent / "loader-dev" / "manifest-check-home"))
    result = subprocess.run([str(interpreter), "-c", code], capture_output=True, text=True, env=env, check=False, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["errors"] == [], report
    assert report["name"] == "floofycrew" and report["entry"] == "index.mjs" and report["perm"] == {}
    assert report["hooks"] == manifest()["backend"]["hooks"]
