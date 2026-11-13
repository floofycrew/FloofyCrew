"""Python anchors: the host names FloofyCrew relies on, checked against a payload without importing it (Requirement 6.2, 9.1).

``anchors.json`` lists ``module:attr`` pairs (``attr`` ``null`` = the module's
presence). :func:`check_anchors` locates each module's source file under the
payload's package directory (``kiro_crew/…/module.py`` or ``…/__init__.py``) and
parses it with :mod:`ast`, looking for a top-level ``def``/``async def``/
``class``/assignment of that name (``Class.method`` looks inside the class
body) — no import, no gateway, so the check runs on a dormant payload, on the
Forge's scratch provisions, and inside the host-version-change handler. The
result carries the matrix shape ``pythonAnchors {matched, total, missed[]}``.
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .payloads import Payload
from .resources import read_package_text

__all__ = ["AnchorReport", "check_anchors", "load_anchors"]

ANCHORS_FILE = Path(__file__).with_name("anchors.json")


def load_anchors(path: Path | None = None) -> list[dict[str, Any]]:
    if path is not None:
        text = Path(path).read_text(encoding="utf-8")
    else:  # the bundled anchors.json — readable from a directory and from inside the zipapp
        text = read_package_text(__package__, "anchors.json", fallback=ANCHORS_FILE)
    document = json.loads(text)
    anchors = document.get("anchors") if isinstance(document, dict) else document
    return [a for a in (anchors or []) if isinstance(a, dict) and isinstance(a.get("module"), str)]


@dataclass
class AnchorReport:
    host_version: str | None
    matched: list[str] = field(default_factory=list)
    missed: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.matched) + len(self.missed)

    @property
    def ok(self) -> bool:
        return not self.missed

    def to_dict(self) -> dict[str, Any]:
        return {"hostVersion": self.host_version, "matched": list(self.matched), "missed": list(self.missed), "pythonAnchors": {"matched": len(self.matched), "total": self.total, "missed": [m["name"] for m in self.missed]}}


def _module_file(package_root: Path, module: str) -> Path | None:
    relative = Path(*module.split("."))
    for candidate in (package_root / relative.with_suffix(".py"), package_root / relative / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _top_level_names(tree: ast.Module) -> dict[str, ast.AST]:
    names: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    names[target.id] = node
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names[alias.asname or alias.name.split(".")[0]] = node
    return names


def _has_attr(tree: ast.Module, attr: str) -> bool:
    head, _, rest = attr.partition(".")
    names = _top_level_names(tree)
    if head not in names:
        return False
    if not rest:
        return True
    node = names[head]
    if not isinstance(node, ast.ClassDef):
        return False
    class_names = {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    class_names |= {t.id for n in node.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    class_names |= {n.target.id for n in node.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
    return rest in class_names


def check_anchors(payload: Payload | Path, anchors: list[dict[str, Any]] | None = None, *, host_version: str | None = None) -> AnchorReport:
    """Check every anchor against the payload's sources; ``payload`` may be a :class:`Payload` or the directory holding ``kiro_crew``."""
    if isinstance(payload, Payload):
        package_root = payload.package_dir.parent
        host_version = host_version or payload.host_version.text
    else:
        package_root = Path(payload)
    report = AnchorReport(host_version)
    for anchor in anchors if anchors is not None else load_anchors():
        module = str(anchor["module"])
        attr = anchor.get("attr")
        name = f"{module}:{attr}" if attr else module
        source = _module_file(package_root, module)
        if source is None:
            report.missed.append({"name": name, "reason": f"module file not found under {package_root}"})
            continue
        if not attr:
            report.matched.append(name)
            continue
        try:
            tree = ast.parse(source.read_text(encoding="utf-8", errors="replace"), filename=str(source))
        except SyntaxError as exc:
            report.missed.append({"name": name, "reason": f"cannot parse {source}: {exc}"})
            continue
        if _has_attr(tree, str(attr)):
            report.matched.append(name)
        else:
            report.missed.append({"name": name, "reason": f"{attr!r} is not defined at the top level of {source.name}"})
    return report
