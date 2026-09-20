#!/usr/bin/env python3
"""Bake a theme mod's first-frame CSS (``boot.css``) from its pack's ``variables.json``.

A ``theme`` part gets its colours from the host only after auth and hydration
(the dashboard generates ``<style id="mc-custom-theme-<slug>">`` at runtime).
A ``spa`` part with ``activation: boot`` bakes the same CSS into ``index.html``
so the first frame already paints the pack (Requirement 4.3). This tool keeps
the baked file honest: it regenerates it with :func:`floofy_core.themes.theme_css`
— the ported dashboard generator — and refreshes the manifest's ``files[]``
hashes.

Usage:
    python scripts/bake_theme_css.py mods/rimuru-branding            # rewrite spa/boot.css
    python scripts/bake_theme_css.py mods/rimuru-branding --check    # exit 1 when stale

The pack is the mod's first ``theme`` part; the output is the ``css`` of its
first boot part (default ``spa/boot.css``). Standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "floofy-core"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from floofy_core.themes import theme_css_for_pack  # noqa: E402

import hash_example_files  # noqa: E402


def bake(mod_dir: Path, *, check: bool) -> int:
    manifest = json.loads((mod_dir / "floofy.json").read_text(encoding="utf-8"))
    parts = manifest.get("parts") or []
    theme = next((p for p in parts if p.get("kind") == "theme"), None)
    boot = next((p for p in parts if p.get("kind") == "spa" and p.get("activation") == "boot"), None)
    if theme is None or boot is None:
        print(f"{mod_dir}: needs a theme part and a boot spa part", file=sys.stderr)
        return 2
    pack_dir = (mod_dir / theme["path"]).parent
    css_rel = (boot.get("boot") or {}).get("css", "spa/boot.css")
    target = mod_dir / css_rel
    css = theme_css_for_pack(pack_dir) + "\n"
    current = target.read_text(encoding="utf-8") if target.is_file() else None
    if check:
        if current != css:
            print(f"{target} is stale; run scripts/bake_theme_css.py {mod_dir}", file=sys.stderr)
            return 1
        print(f"OK: {target} is current")
        return 0
    if current != css:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(css, encoding="utf-8")
        print(f"wrote {target} ({len(css)} bytes)")
    else:
        print(f"{target} unchanged")
    hash_example_files.refresh_manifest(mod_dir, check=False, add_missing=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mod_dir", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    return bake(args.mod_dir.resolve(), check=args.check)


if __name__ == "__main__":
    sys.exit(main())
