"""``floofy new <kind>`` — scaffold a valid mod (Requirement 14.1).

Every scaffold is a directory with a schema-valid ``floofy.json`` (``files[]``
hashed), a ``README.md``, an MIT ``LICENSE`` stub, a ``tests/smoke_test.py``
that runs ``floofy validate`` on the mod, and a GitHub Actions workflow
(``.github/workflows/floofy-validate.yml``) for the public registry's submission
checks (Requirement 8.7). The kind-specific part skeletons mirror
``floofy-core/examples/<kind>``; they are generated here rather than copied so
the zipapp can scaffold too. ``floofy validate`` passes on every scaffold.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

__all__ = ["KINDS", "refresh_files", "scaffold"]

KINDS = ("theme", "agent", "skill", "appearance", "config", "app", "python-hook", "spa", "patch", "ui")
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")

LICENSE = """MIT License

Copyright (c) {year} {author}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

README = """# {name}

A FloofyCrew mod for KiroCrew (kind: `{kind}`). FloofyCrew is unofficial and not
affiliated with Kiro or KiroCrew.

## What it does

Describe the change this mod makes and which host seam it uses
(`{seam}`).

## Develop

```sh
floofy validate .            # schema, hashes, parts, targets, network scan
floofy dev . --follow        # link into the data home, stream the mod's log
python tests/smoke_test.py   # what CI runs
```

Keep `files[]` in `floofy.json` current after editing files: `floofy new` hashed
them once; `python tests/smoke_test.py --refresh` rewrites the hashes.

## Publish

Tag the release with the manifest `version` and open a pull request against the
registry's `index.json` (see the FloofyCrew docs, "publishing to a registry").
"""

SMOKE_TEST = '''"""Smoke test: the mod validates cleanly (run by CI and by hand).

``--refresh`` rewrites the ``files[]`` hashes after you edited files.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def refresh() -> None:
    manifest_path = ROOT / "floofy.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = []
    for path in sorted(ROOT.rglob("*")):
        rel = path.relative_to(ROOT).as_posix()
        if not path.is_file() or rel == "floofy.json" or any(part.startswith(".") for part in path.relative_to(ROOT).parts):
            continue
        files.append({"path": rel, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest["files"] = files
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\\n", encoding="utf-8")


def main() -> int:
    if "--refresh" in sys.argv:
        refresh()
        print("files[] refreshed")
        return 0
    result = subprocess.run([sys.executable, "-m", "floofy_core.cli", "validate", str(ROOT)], text=True)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
'''

WORKFLOW = """name: floofy validate
on: [push, pull_request]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install the floofy CLI
        run: pip install floofycrew
      - name: Validate the mod (schema, hashes, parts, targets, network scan)
        run: floofy validate .
      - name: Smoke test
        run: python tests/smoke_test.py
"""


def _part_files(kind: str, mod_id: str, name: str) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, Any]]:
    """``(parts, files, manifest extras)`` for the kind."""
    if kind == "theme":
        return (
            [{"kind": "theme", "side": "gateway", "path": "theme/theme.json", "description": "a Level-0 theme pack"}],
            {
                "theme/theme.json": json.dumps({"formatVersion": 1, "name": name, "slug": mod_id, "emoji": "🎨", "level": 0}, indent=2) + "\n",
                "theme/variables.json": json.dumps({"dark": {"--bg": "#101418", "--text": "#e6edf3", "--accent": "#7aa2f7"}, "light": {"--bg": "#ffffff", "--text": "#1f2328", "--accent": "#2f6feb"}}, indent=2) + "\n",
            },
            {},
        )
    if kind == "agent":
        return (
            [{"kind": "agent", "side": "gateway", "path": f"agents/{mod_id}.json"}],
            {f"agents/{mod_id}.json": json.dumps({"name": mod_id, "description": f"{name} agent", "prompt": "You are a helpful agent.", "tools": ["*"]}, indent=2) + "\n"},
            {},
        )
    if kind == "skill":
        return (
            [{"kind": "skill", "side": "gateway", "path": f"skills/{mod_id}/SKILL.md"}],
            {f"skills/{mod_id}/SKILL.md": f"---\nname: {mod_id}\ndescription: {name}\n---\n\n# {name}\n\nWhen to use this skill and how.\n"},
            {},
        )
    if kind == "appearance":
        return (
            [{"kind": "appearance", "side": "gateway", "path": "appearance/manifest.json"}],
            {"appearance/manifest.json": json.dumps({"id": mod_id, "name": name, "author": "you", "description": f"{name} appearance pack", "format": "svg", "animations": {}}, indent=2) + "\n"},
            {},
        )
    if kind == "config":
        return (
            [{"kind": "config", "side": "gateway", "path": "config/values.json", "values": {"dashboard.compact": True}}],
            {"config/values.json": json.dumps({"dashboard.compact": True}, indent=2) + "\n"},
            {},
        )
    if kind == "app":
        return (
            [{"kind": "app", "side": "gateway", "path": "app/app.json"}],
            {
                "app/app.json": json.dumps({"name": mod_id, "version": "0.1.0", "description": f"{name} (a KiroCrew App shipped by a FloofyCrew mod)", "ui": {"entry": "ui/panel.mjs"}, "permissions": []}, indent=2) + "\n",
                "app/ui/panel.mjs": 'import React from "react";\n\nexport default function Panel() {\n  return React.createElement("div", { className: "p-4" }, "Hello from ' + name + '");\n}\n',
            },
            {},
        )
    if kind == "python-hook":
        return (
            [{"kind": "python-hook", "side": "gateway", "path": "hook/", "module": "hook"}],
            {"hook/__init__.py": f'"""{name}: a FloofyCrew python-hook part."""\n\n\ndef activate(ctx):\n    ctx.log.info("{mod_id} active on host %s", ctx.host.version)\n    ctx.state["active"] = True\n\n\ndef deactivate(ctx):\n    ctx.state["active"] = False\n'},
            {},
        )
    if kind == "spa":
        return (
            [{"kind": "spa", "side": "spa", "path": "spa/main.js"}],
            {"spa/main.js": f'// {name}: a FloofyCrew spa part, loaded by the SPA host inside an error boundary.\nexport default function activate(ctx) {{\n  ctx.log.info("{mod_id} active on host " + ctx.host.version);\n}}\n\nexport function deactivate() {{}}\n'},
            {},
        )
    if kind == "ui":
        page = (
            f"// {name}: the mod's own page inside the FloofyCrew App (Requirement 16.4).\n"
            "// The App imports this module same-origin and calls the default export with the page's\n"
            "// container and `api = floofy.mod(id)`: config.get()/set(patch) (persisted in the mod's\n"
            "// .floofy/config.json), routes.list()/fetch(path) (the mod's python-hook routes), theme.tokens(),\n"
            "// state(). Return a cleanup function; a throw is caught by the App's error boundary.\n"
            "export default async function mount(container, api) {\n"
            "  const config = await api.config.get();\n"
            '  const label = document.createElement("label");\n'
            f'  label.textContent = "Greeting for {name}: ";\n'
            '  const input = document.createElement("input");\n'
            '  input.value = config.greeting || "";\n'
            '  input.addEventListener("change", () => api.config.set({ greeting: input.value }));\n'
            "  label.appendChild(input);\n"
            "  container.appendChild(label);\n"
            '  return () => { container.textContent = ""; };\n'
            "}\n"
        )
        return (
            [{"kind": "ui", "side": "spa", "path": "ui/", "entry": "ui/page.mjs", "title": name}],
            {"ui/page.mjs": page},
            {},
        )
    if kind == "patch":
        descriptor = {
            "schema": 1,
            "target": "kiro_crew/static/dist/index.html",
            "appliesTo": ">=0.7.0 <0.9.0",
            "description": f"{name}: an example index.html patch (edit the fingerprint and content)",
            "ops": [{"op": "append-head", "content": f'<meta name="floofy-{mod_id}" content="1">', "marker": f'name="floofy-{mod_id}"'}],
        }
        return (
            [{"kind": "patch", "side": "spa", "path": "patches/index.json"}],
            {"patches/index.json": json.dumps(descriptor, indent=2) + "\n"},
            {},
        )
    raise ValueError(f"unknown kind {kind!r}")


def refresh_files(root: Path) -> list[dict[str, str]]:
    """Rewrite ``files[]`` from every non-dot file except ``floofy.json``; returns the list."""
    manifest_path = Path(root) / "floofy.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = []
    for path in sorted(Path(root).rglob("*")):
        rel = path.relative_to(root)
        if not path.is_file() or rel.as_posix() == "floofy.json" or any(part.startswith(".") for part in rel.parts):
            continue
        files.append({"path": rel.as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest["files"] = files
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return files


def scaffold(kind: str, directory: Path, *, mod_id: str | None = None, name: str | None = None, author: str = "you", host_range: str = ">=0.7.0 <0.9.0", framework_range: str = "^1.0", year: int = 2026) -> Path:
    """Write the scaffold for ``kind`` under ``directory/<id>/`` and return the mod root."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    mod_id = mod_id or f"my-{kind}"
    if not _ID_RE.match(mod_id):
        raise ValueError(f"{mod_id!r} is not a valid mod id (^[a-z][a-z0-9_-]{{1,63}}$)")
    name = name or mod_id.replace("-", " ").replace("_", " ").title()
    root = Path(directory) / mod_id
    if root.exists():
        raise FileExistsError(f"{root} already exists")
    parts, files, extras = _part_files(kind, mod_id, name)
    from .kinds import SEAMS  # noqa: PLC0415

    files["README.md"] = README.format(name=name, kind=kind, seam=SEAMS.get(kind, kind))
    files["LICENSE"] = LICENSE.format(year=year, author=author)
    files["tests/smoke_test.py"] = SMOKE_TEST
    files[".github/workflows/floofy-validate.yml"] = WORKFLOW
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    manifest: dict[str, Any] = {
        "schema": 1,
        "id": mod_id,
        "name": name,
        "version": "0.1.0",
        "description": f"{name}: a {kind} mod scaffolded by `floofy new` (edit me).",
        "authors": [author],
        "license": "MIT",
        "tags": [kind],
        "kirocrew": {"version": host_range, "editions": ["internal", "external"], "strict": False},
        "dependsOn": {"floofycrew": framework_range},
        "parts": parts,
        "files": [],
        **extras,
    }
    (root / "floofy.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    refresh_files(root)
    return root
