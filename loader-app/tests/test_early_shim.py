"""The optional early shim (Requirement 3.2): ``zz_floofycrew.pth`` + ``floofy_early`` in a fake user site / venv site.

Every subprocess runs with a fake ``HOME`` and ``PYTHONUSERBASE`` under the test's
temporary directory, so the real user site (``~/.local/lib/python3.X/site-packages``)
is never touched — the session asserts it did not appear.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from floofy_loader import early_install
from floofy_loader.early_install import PTH_NAME, install_user_site, install_venv_site, site_dirs, status, uninstall_user_site, uninstall_venv_site

from loader_testing import LOADER_APP_DIR, bundle_interpreter, scratch_payload

REAL_USER_SITE = Path.home() / ".local" / "lib" / f"python{sys.version_info[0]}.{sys.version_info[1]}" / "site-packages"
_real_user_site_existed = REAL_USER_SITE.exists()

EARLY_MOD = '''
def early(ctx):
    import pathlib
    ctx.log("early hook running")
    pathlib.Path(ctx.data_home, "early-marker.txt").write_text(f"{ctx.mod_id} bootstrap={type(ctx.bootstrap).__name__} flag={getattr(ctx.bootstrap, 'BOOTSTRAPPED', None)}")

def activate(ctx):
    ctx.state["shared"] = True
'''

EARLY_MOD_RAISES = '''
def early(ctx):
    raise RuntimeError("early boom")
'''


def non_venv_interpreter() -> Path | None:
    """An interpreter that honours the user site: the payload copy's bundled Python, else the venv's base Python."""
    payload = scratch_payload()
    if payload is not None:
        found = bundle_interpreter(payload)
        if found is not None:
            return found
    base = Path(sys.base_prefix) / "bin" / f"python{sys.version_info[0]}.{sys.version_info[1]}"
    return base if base.exists() else None


@pytest.fixture
def fake_home(tmp_path: Path) -> dict[str, str]:
    """Environment with HOME / PYTHONUSERBASE / KIROCREW_HOME all inside tmp; no real user site is reachable."""
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "userbase").mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "FLOOFY"))}
    env.update(HOME=str(home), PYTHONUSERBASE=str(tmp_path / "userbase"), KIROCREW_HOME=str(home / ".kiro" / "crew"))
    return env


def fake_host_package(root: Path) -> Path:
    """A fake ``kiro_crew`` with the ``platform.bootstrap`` trigger module (sets ``BOOTSTRAPPED``)."""
    package = root / "fakehost" / "kiro_crew"
    (package / "platform").mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "0.7.0"\n', encoding="utf-8")
    (package / "platform" / "__init__.py").write_text("", encoding="utf-8")
    (package / "platform" / "bootstrap.py").write_text("BOOTSTRAPPED = True\n", encoding="utf-8")
    return package.parent


def stage_early_mod(env: dict[str, str], code: str = EARLY_MOD, mod_id: str = "earlybird") -> Path:
    data_home = Path(env["KIROCREW_HOME"]) / "floofy"
    mod_dir = data_home / "mods" / mod_id
    (mod_dir / "hook").mkdir(parents=True)
    (mod_dir / "hook" / "__init__.py").write_text(code, encoding="utf-8")
    data_home.mkdir(parents=True, exist_ok=True)
    (data_home / "early.json").write_text(json.dumps({"mods": [{"id": mod_id, "path": str(mod_dir), "module": "hook", "part": 0}]}), encoding="utf-8")
    return data_home


def run(interpreter: Path, env: dict[str, str], *flags: str, code: str = "import kiro_crew.platform.bootstrap as b; print('ok', b.BOOTSTRAPPED)", pythonpath: Path | None = None) -> subprocess.CompletedProcess:
    child_env = dict(env)
    if pythonpath is not None:
        child_env["PYTHONPATH"] = str(pythonpath)
    return subprocess.run([str(interpreter), *flags, "-c", code], env=child_env, capture_output=True, text=True, timeout=120, check=False)


def test_shim_package_ships_the_pth_and_is_stdlib_only():
    package = LOADER_APP_DIR / "floofy_early"
    assert (package / "__init__.py").is_file() and (package / PTH_NAME).read_text(encoding="utf-8").strip() == "import floofy_early"
    text = (package / "__init__.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|from)\s+floofy_(core|loader)", text, re.M), "the shim depends on the standard library only"
    assert text.rstrip().endswith("pass") and "except BaseException" in text, "the whole shim is inside a fail-open try/except"


@pytest.mark.parametrize("kind", ["user-site", "venv-site"])
def test_shim_runs_after_platform_bootstrap_and_is_skipped_by_isolation_flags(kind: str, tmp_path: Path, fake_home: dict[str, str]):
    if kind == "user-site":
        interpreter = non_venv_interpreter()
        if interpreter is None:
            pytest.skip("no interpreter that honours the user site is available")
        report = install_user_site(interpreter, env=fake_home)
        assert report.installed and report.kind == "user-site" and report.site_dir and report.site_dir.startswith(str(tmp_path / "userbase"))
        assert report.user_site_enabled is True
    else:
        venv = tmp_path / "venv"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True, capture_output=True)
        interpreter = venv / "bin" / "python"
        report = install_venv_site(interpreter, env=fake_home)
        assert report.installed and report.kind == "venv-site" and report.site_dir and report.site_dir.startswith(str(venv))
    site_dir = Path(report.site_dir)
    assert (site_dir / PTH_NAME).read_text(encoding="utf-8") == "import floofy_early\n" and (site_dir / "floofy_early" / "__init__.py").is_file()
    assert not (site_dir / "floofy_early" / PTH_NAME).exists(), "the pth is not copied into the package"
    fakehost = fake_host_package(tmp_path)

    # no early.json → a plain no-op (no log, no marker)
    result = run(interpreter, fake_home, pythonpath=fakehost)
    assert result.returncode == 0 and result.stdout.strip() == "ok True", result.stderr
    data_home = Path(fake_home["KIROCREW_HOME"]) / "floofy"
    assert not (data_home / "early.log").exists()

    # early.json present → early() runs right after kiro_crew.platform.bootstrap executed
    stage_early_mod(fake_home)
    result = run(interpreter, fake_home, pythonpath=fakehost)
    assert result.returncode == 0 and result.stdout.strip() == "ok True", result.stderr
    assert (data_home / "early-marker.txt").read_text(encoding="utf-8") == "earlybird bootstrap=module flag=True"
    log = (data_home / "early.log").read_text(encoding="utf-8")
    assert "armed for kiro_crew.platform.bootstrap" in log and "[earlybird] early() ran" in log and "[earlybird] early hook running" in log
    # the module was imported under the Loader's namespace key
    probe = run(interpreter, fake_home, code="import sys, kiro_crew.platform.bootstrap; print(sorted(k for k in sys.modules if k.startswith('floofy_mods')))", pythonpath=fakehost)
    assert "'floofy_mods.earlybird.hook'" in probe.stdout, probe.stdout + probe.stderr

    # isolation flags skip the site processing
    (data_home / "early-marker.txt").unlink()
    (data_home / "early.log").unlink()
    for flags in (("-I", "-S"), ("-I",), ("-S",)):
        result = run(interpreter, fake_home, *flags, pythonpath=fakehost) if "-I" not in flags else subprocess.run([str(interpreter), *flags, "-c", f"import sys; sys.path.insert(0, {str(fakehost)!r}); import kiro_crew.platform.bootstrap as b; print('ok', b.BOOTSTRAPPED)"], env=fake_home, capture_output=True, text=True, timeout=120, check=False)
        assert result.returncode == 0, (flags, result.stderr)
        assert not (data_home / "early-marker.txt").exists() and not (data_home / "early.log").exists(), flags
    # -s skips the USER site but not a venv site (spike 1.5)
    result = run(interpreter, fake_home, "-s", pythonpath=fakehost)
    assert result.returncode == 0, result.stderr
    assert (data_home / "early-marker.txt").exists() == (kind == "venv-site")

    # a raising early() is logged, never propagated
    for leftover in (data_home / "early-marker.txt", data_home / "early.log"):
        if leftover.exists():
            leftover.unlink()
    stage_early_mod(fake_home, code=EARLY_MOD_RAISES, mod_id="boomer")
    result = run(interpreter, fake_home, pythonpath=fakehost)
    assert result.returncode == 0 and result.stdout.strip() == "ok True" and "Traceback" not in result.stderr
    assert "[boomer] early() failed" in (data_home / "early.log").read_text(encoding="utf-8")

    # kill switch and no-kiro_crew are no-ops; uninstall removes both files
    result = run(interpreter, {**fake_home, "FLOOFY_EARLY_DISABLE": "1"}, code="print('ok')", pythonpath=fakehost)
    assert result.returncode == 0
    result = run(interpreter, fake_home, code="import sys; print('floofy_early' in sys.modules, [m for m in sys.meta_path if 'Early' in type(m).__name__])")
    assert result.stdout.strip() == "True []", "without kiro_crew importable the shim arms nothing"
    removed = uninstall_user_site(interpreter, env=fake_home) if kind == "user-site" else uninstall_venv_site(interpreter, env=fake_home)
    assert len(removed) == 2 and not (site_dir / PTH_NAME).exists() and not (site_dir / "floofy_early").exists()
    assert not status(interpreter, kind, env=fake_home).installed


def test_status_reports_user_site_disabled_inside_a_venv(fake_home: dict[str, str]):
    """The running .venv interpreter: user site off, venv-site purelib available."""
    report = status(sys.executable, "user-site", env=fake_home)
    dirs = site_dirs(sys.executable, env=fake_home)
    if dirs.in_venv:
        assert report.user_site_enabled is False and any("ENABLE_USER_SITE" in n for n in report.notes)
    venv_report = status(sys.executable, "venv-site", env=fake_home)
    assert venv_report.site_dir == sysconfig.get_paths()["purelib"] and venv_report.reach.startswith("venv site")
    assert venv_report.to_dict()["installed"] is False
    assert status("/nonexistent/python", "user-site").notes[0].startswith("interpreter unreachable")


def test_installer_source_is_the_shipped_package():
    assert early_install.SOURCE_PACKAGE == LOADER_APP_DIR / "floofy_early" and (early_install.SOURCE_PACKAGE / "__init__.py").is_file()


def test_the_real_user_site_was_never_created():
    # The suite must only ever touch its fake HOME/PYTHONUSERBASE. The real user site MAY exist —
    # a live floofy install on the developer's own machine puts the early shim there — so the
    # guard is "the suite did not create it", not "it does not exist".
    assert REAL_USER_SITE.exists() == _real_user_site_existed, f"the test-suite created or removed {REAL_USER_SITE} (fake HOME/PYTHONUSERBASE only)"




def test_loader_reuses_the_shim_module_across_a_symlinked_home(tmp_path: Path):
    """The shim records the path early.json spells; the Loader resolves symlinks — same file, one module object (owner-verified gap)."""
    import importlib.util

    from floofy_loader.activation import load_part_module, module_key

    real = tmp_path / "real"
    (real / "mods" / "earlybird" / "hook").mkdir(parents=True)
    (real / "mods" / "earlybird" / "hook" / "__init__.py").write_text("COUNTER = []\n\ndef early(ctx):\n    COUNTER.append('early')\n\ndef activate(ctx):\n    COUNTER.append('activate')\n\ndef deactivate(ctx):\n    pass\n", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    # what floofy_early does with an early.json path spelled through the symlink
    mod_dir_as_written = link / "mods" / "earlybird"
    key = module_key("earlybird", "hook")
    for name in ("floofy_mods", "floofy_mods.earlybird"):
        sys.modules.setdefault(name, importlib.util.module_from_spec(importlib.machinery.ModuleSpec(name, None, is_package=True)))
    file = mod_dir_as_written / "hook" / "__init__.py"
    spec = importlib.util.spec_from_file_location(key, str(file), submodule_search_locations=[str(file.parent)])
    shim_module = importlib.util.module_from_spec(spec)
    sys.modules[key] = shim_module
    spec.loader.exec_module(shim_module)
    shim_module.early(None)
    try:
        # the Loader, handed the same mod through the resolved (real) path
        handle = load_part_module("earlybird", real / "mods" / "earlybird", {"kind": "python-hook", "path": "hook/", "module": "hook"}, 0)
        assert handle.module is shim_module, "a fresh import would lose everything early() recorded"
        assert shim_module.COUNTER == ["early"]
    finally:
        for name in [k for k in sys.modules if k.startswith("floofy_mods")]:
            sys.modules.pop(name, None)
