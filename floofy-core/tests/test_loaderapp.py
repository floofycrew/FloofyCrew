"""``floofy_core.loaderapp``: the installed Loader app's ``early_install`` module loaded from the app directory (task 10.9; Requirement 3.2, 7.4).

The CLI executes ``<host home>/apps/floofycrew/floofy_loader/early_install.py``
by file path so it never needs the Loader on its own import path. That file
declares ``@dataclass`` classes under ``from __future__ import annotations``, and
``dataclasses`` resolves such annotations through ``sys.modules[cls.__module__]``
— an ``exec_module`` without a ``sys.modules`` registration therefore raises
inside ``dataclasses._is_type``. The tests pin the registered load, the failure
cleanup and the fallback rules; the fallback import is blocked where the point
is that the installed copy must carry the day.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

from floofy_core.loaderapp import EARLY_INSTALL_MODULE_NAME, app_dir, early_install_module, load_module_from_file

from floofy_testing import REPO_ROOT

LOADER_APP = REPO_ROOT / "loader-app"

DATACLASS_UNDER_FUTURE_ANNOTATIONS = '''
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Record:
    """The shape early_install.py uses: a string annotation dataclasses must resolve through sys.modules."""

    name: str
    site_dir: str | None = None


def make() -> Record:
    return Record("x")
'''


def install_fake_loader_app(host_home: Path, *, early_install_source: str | None = None) -> Path:
    """The shipped shim files under ``<host home>/apps/floofycrew/`` (no host CLI involved); ``early_install_source`` replaces the module text."""
    directory = app_dir(host_home)
    (directory / "floofy_loader").mkdir(parents=True, exist_ok=True)
    shutil.copytree(LOADER_APP / "floofy_early", directory / "floofy_early", ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
    target = directory / "floofy_loader" / "early_install.py"
    if early_install_source is None:
        shutil.copy(LOADER_APP / "floofy_loader" / "early_install.py", target)
    else:
        target.write_text(early_install_source, encoding="utf-8")
    (directory / "app.json").write_text('{"name": "floofycrew"}\n', encoding="utf-8")
    (directory / "installed.json").write_text('{"name": "floofycrew", "version": "1.1.0", "enabled": true}\n', encoding="utf-8")
    return directory


@pytest.fixture
def no_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``import floofy_loader.early_install`` fail (a ``None`` entry in ``sys.modules`` raises ImportError): only the installed copy can answer."""
    monkeypatch.setitem(sys.modules, "floofy_loader.early_install", None)
    monkeypatch.delitem(sys.modules, EARLY_INSTALL_MODULE_NAME, raising=False)


def test_the_unregistered_pattern_fails_on_a_dataclass_and_the_registered_load_does_not(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "shape.py"
    source.write_text(DATACLASS_UNDER_FUTURE_ANNOTATIONS, encoding="utf-8")
    # the bug (task 10.9): module_from_spec + exec_module without sys.modules → dataclasses._is_type dereferences None
    spec = importlib.util.spec_from_file_location("_floofy_test_unregistered_shape", source)
    assert spec is not None and spec.loader is not None
    with pytest.raises(AttributeError, match="__dict__"):
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
    assert "_floofy_test_unregistered_shape" not in sys.modules
    # the fix: registered before it runs
    monkeypatch.delitem(sys.modules, "_floofy_test_shape", raising=False)
    module = load_module_from_file("_floofy_test_shape", source)
    assert sys.modules["_floofy_test_shape"] is module and module.__file__ == str(source)
    assert module.make().site_dir is None and module.Record.__module__ == "_floofy_test_shape"
    monkeypatch.delitem(sys.modules, "_floofy_test_shape", raising=False)


def test_a_failing_module_is_popped_again_and_a_previous_entry_restored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("raise ValueError('boom at import')\n", encoding="utf-8")
    monkeypatch.delitem(sys.modules, "_floofy_test_broken", raising=False)
    with pytest.raises(ValueError, match="boom at import"):
        load_module_from_file("_floofy_test_broken", broken)
    assert "_floofy_test_broken" not in sys.modules, "a failed load leaves no half-initialised module behind"
    sentinel = importlib.util.module_from_spec(importlib.machinery.ModuleSpec("_floofy_test_broken", None))
    monkeypatch.setitem(sys.modules, "_floofy_test_broken", sentinel)
    with pytest.raises(ValueError):
        load_module_from_file("_floofy_test_broken", broken)
    assert sys.modules["_floofy_test_broken"] is sentinel, "the previous module of that name is restored"


def test_early_install_module_loads_the_shipped_file_from_the_installed_app(tmp_path: Path, no_fallback: None) -> None:
    host_home = tmp_path / "home"
    directory = install_fake_loader_app(host_home)
    module = early_install_module(host_home)
    assert module is not None, "the installed copy must load (before task 10.9 this was None: 'early shim: Loader app not installed')"
    assert module.__file__ == str(directory / "floofy_loader" / "early_install.py")
    assert module.__name__ == EARLY_INSTALL_MODULE_NAME and sys.modules[EARLY_INSTALL_MODULE_NAME] is module
    assert Path(module.SOURCE_PACKAGE) == directory / "floofy_early", "the shim source is the package beside the installed module"
    # the dataclasses the load used to choke on work: status() for this interpreter (files only, nothing written)
    report = module.status(sys.executable, "venv-site")
    assert report.kind == "venv-site" and report.interpreter == sys.executable and isinstance(report.to_dict()["installed"], bool)
    assert module.ShimStatus("user-site", "python", None, False, False, None, None, "").to_dict()["installed"] is False
    # a second call reloads the installed copy under the same name (a reinstall of the app is picked up)
    again = early_install_module(host_home)
    assert again is not None and again.__file__ == module.__file__ and sys.modules[EARLY_INSTALL_MODULE_NAME] is again


def test_early_install_module_falls_back_or_gives_up_cleanly(tmp_path: Path, no_fallback: None) -> None:
    assert early_install_module(tmp_path / "nothing") is None, "no app, no import path: None"
    host_home = tmp_path / "home"
    install_fake_loader_app(host_home, early_install_source="def status(*a, **k):\n    return None\nraise RuntimeError('broken installed copy')\n")
    assert early_install_module(host_home) is None
    assert EARLY_INSTALL_MODULE_NAME not in sys.modules, "the failed installed copy is popped before the fallback is tried"
    # the shim package must be present beside the module, else the installed copy is not offered at all
    install_fake_loader_app(host_home)
    shutil.rmtree(app_dir(host_home) / "floofy_early")
    assert early_install_module(host_home) is None


def test_early_install_module_prefers_the_installed_copy_over_the_import_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With ``floofy_loader`` importable (this test session), the installed copy still wins: doctor talks about the shim the app ships."""
    monkeypatch.delitem(sys.modules, EARLY_INSTALL_MODULE_NAME, raising=False)
    host_home = tmp_path / "home"
    directory = install_fake_loader_app(host_home)
    module = early_install_module(host_home)
    assert module is not None and module.__file__ == str(directory / "floofy_loader" / "early_install.py")
    import floofy_loader.early_install as shipped  # noqa: PLC0415

    assert module is not shipped and module.PTH_NAME == shipped.PTH_NAME
