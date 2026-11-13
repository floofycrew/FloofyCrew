"""The Python anchor check against both host shapes (Requirement 6.2, 9.1): a plain module and a facade.

Until KiroCrew 0.10 every anchored name was defined at the top level of the module
``anchors.json`` names, and the AST walk found it there. KiroCrew 0.11+ split
``apps/manager.py``, ``dashboard/handlers/core.py`` and ``config/loader.py`` into owner
modules and kept each original path as a *facade* (``kiro_crew._facade_mirror``): the owners
are imported at import time, a read of ``kiro_crew.apps.manager.apps_dir`` is served by the
owner that binds it, and the only static trace left in the facade is a
``from <owner> import <name>`` under ``if TYPE_CHECKING:``. The anchors keep naming the
runtime path FloofyCrew uses, so the checker follows that trace into the owner's source and
requires the definition there — never taking the import on faith, and never changing what a
plain module matches.
"""
from __future__ import annotations

from pathlib import Path

from floofy_core.anchors import check_anchors


def _write_module(root: Path, module: str, text: str, *, as_package: bool = False) -> None:
    """Write ``module`` under ``root`` as ``<path>.py`` (or ``<path>/__init__.py``), with every parent made a package."""
    base = root.joinpath(*module.split("."))
    path = base / "__init__.py" if as_package else base.with_suffix(".py")
    path.parent.mkdir(parents=True, exist_ok=True)
    for parent in path.parents:
        if parent == root:
            break
        (parent / "__init__.py").touch()
    path.write_text(text, encoding="utf-8")


PLAIN_MODULE = (
    "import json\n"
    "from pathlib import Path\n"
    "\n"
    "APPS = 'apps'\n"
    "\n"
    "\n"
    "def apps_dir() -> Path:\n"
    "    return Path(APPS)\n"
    "\n"
    "\n"
    "class Records:\n"
    "    kind = 'record'\n"
    "\n"
    "    def load(self) -> None:\n"
    "        pass\n"
)


def test_a_plain_module_matches_at_its_top_level_as_before(tmp_path: Path):
    """The unsplit shape (0.7–0.10 hosts): top-level def/assign/import, ``Class.method`` in the class body, old reason text."""
    root = tmp_path / "site-packages"
    _write_module(root, "kiro_crew.apps.manager", PLAIN_MODULE)
    anchors = [
        {"module": "kiro_crew.apps.manager", "attr": "apps_dir"},  # top-level def
        {"module": "kiro_crew.apps.manager", "attr": "APPS"},  # top-level assignment
        {"module": "kiro_crew.apps.manager", "attr": "json"},  # top-level import
        {"module": "kiro_crew.apps.manager", "attr": "Records.load"},  # method in the class body
        {"module": "kiro_crew.apps.manager", "attr": "Records.kind"},  # attribute in the class body
        {"module": "kiro_crew.apps", "attr": None},  # the module's presence alone
        {"module": "kiro_crew.apps.manager", "attr": "gone"},  # absent, and nothing to follow
        {"module": "kiro_crew.apps.manager", "attr": "Records.gone"},  # absent from the class body
    ]
    report = check_anchors(root, anchors, host_version="0.10.0")
    assert report.matched == [
        "kiro_crew.apps.manager:apps_dir",
        "kiro_crew.apps.manager:APPS",
        "kiro_crew.apps.manager:json",
        "kiro_crew.apps.manager:Records.load",
        "kiro_crew.apps.manager:Records.kind",
        "kiro_crew.apps",
    ]
    assert [m["name"] for m in report.missed] == ["kiro_crew.apps.manager:gone", "kiro_crew.apps.manager:Records.gone"]
    assert report.missed[0]["reason"] == "'gone' is not defined at the top level of manager.py", "a plain module keeps the old reason"
    assert report.to_dict()["pythonAnchors"] == {"matched": 6, "total": 8, "missed": ["kiro_crew.apps.manager:gone", "kiro_crew.apps.manager:Records.gone"]}


def test_anchors_follow_a_facade_reexport_to_its_owner(tmp_path: Path):
    """KiroCrew 0.11+ split manager.py, handlers/core.py and config/loader.py into owners and kept the path as a facade.

    The facade lists the names under ``if TYPE_CHECKING: from <owner> import (…)``; the checker must follow that
    to the owner and require the definition there, never take the TYPE_CHECKING import on faith.
    """
    root = tmp_path / "site-packages"
    _write_module(root, "kiro_crew.apps.manager_records", "from pathlib import Path\n\ndef apps_dir() -> Path:\n    return Path('x')\n")
    _write_module(root, "kiro_crew.apps.manager_lookup", "def list_apps():\n    return []\n")
    _write_module(
        root,
        "kiro_crew.apps.manager",
        "from typing import TYPE_CHECKING\n\n_OWNERS = ('kiro_crew.apps.manager_records',)\n\n"
        "if TYPE_CHECKING:  # every owner-defined name, for type checkers\n"
        "    from kiro_crew.apps.manager_records import (  # noqa: F401\n        app_dir,\n        apps_dir,\n    )\n"
        "    from .manager_lookup import list_apps  # noqa: F401\n"
        "    from kiro_crew.apps.manager_gone import vanished  # noqa: F401\n"
        "    from somewhere_else import outsider  # noqa: F401\n"
        "try:\n    from kiro_crew.apps.manager_records import Path as HostPath  # noqa: F401\nexcept ImportError:\n    HostPath = None\n",
    )
    anchors = [
        {"module": "kiro_crew.apps.manager", "attr": "apps_dir"},  # absolute re-export → owner defines it
        {"module": "kiro_crew.apps.manager", "attr": "list_apps"},  # relative re-export → owner defines it
        {"module": "kiro_crew.apps.manager", "attr": "app_dir"},  # re-exported, but the owner does not define it
        {"module": "kiro_crew.apps.manager", "attr": "vanished"},  # re-exported from a module the payload lacks
        {"module": "kiro_crew.apps.manager", "attr": "outsider"},  # re-exported from outside the payload
        {"module": "kiro_crew.apps.manager", "attr": "HostPath"},  # try-block import whose owner only imports the name itself
        {"module": "kiro_crew.apps.manager", "attr": "_OWNERS"},  # the facade's own top-level name still matches
    ]
    report = check_anchors(root, anchors, host_version="0.12.2")
    assert report.matched == ["kiro_crew.apps.manager:apps_dir", "kiro_crew.apps.manager:list_apps", "kiro_crew.apps.manager:HostPath", "kiro_crew.apps.manager:_OWNERS"]
    assert [m["name"] for m in report.missed] == ["kiro_crew.apps.manager:app_dir", "kiro_crew.apps.manager:vanished", "kiro_crew.apps.manager:outsider"]
    assert report.to_dict()["pythonAnchors"] == {"matched": 4, "total": 7, "missed": ["kiro_crew.apps.manager:app_dir", "kiro_crew.apps.manager:vanished", "kiro_crew.apps.manager:outsider"]}


def test_a_package_facade_resolves_a_relative_reexport_against_itself(tmp_path: Path):
    """A facade that is a package's ``__init__.py``: ``from .owner import x`` is its own child, not its parent's."""
    root = tmp_path / "site-packages"
    _write_module(root, "kiro_crew.apps.manager_records", "def apps_dir():\n    return None\n")  # the parent's sibling trap
    _write_module(root, "kiro_crew.apps.handlers.manager_records", "def apps_dir():\n    return 'child'\n")
    _write_module(
        root,
        "kiro_crew.apps.handlers",
        "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from .manager_records import apps_dir  # noqa: F401\n    from ..manager_records import apps_dir as parent_apps_dir  # noqa: F401\n",
        as_package=True,
    )
    report = check_anchors(
        root,
        [{"module": "kiro_crew.apps.handlers", "attr": "apps_dir"}, {"module": "kiro_crew.apps.handlers", "attr": "parent_apps_dir"}],
        host_version="0.12.2",
    )
    assert report.ok, report.missed
    assert report.matched == ["kiro_crew.apps.handlers:apps_dir", "kiro_crew.apps.handlers:parent_apps_dir"]


def test_a_facade_chain_terminates(tmp_path: Path):
    """Facades that re-export each other: the hop limit stops the walk instead of recursing forever."""
    root = tmp_path / "site-packages"
    loop = "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from kiro_crew.{other} import ghost  # noqa: F401\n"
    _write_module(root, "kiro_crew.a", loop.format(other="b"))
    _write_module(root, "kiro_crew.b", loop.format(other="a"))
    report = check_anchors(root, [{"module": "kiro_crew.a", "attr": "ghost"}], host_version="0.12.2")
    assert [m["name"] for m in report.missed] == ["kiro_crew.a:ghost"]
