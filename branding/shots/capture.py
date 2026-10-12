#!/usr/bin/env python
"""Capture the README screenshots of the FloofyCrew manager App and the KiroCrew IDE mod.

Everything runs against a SCRATCH KiroCrew gateway: a COPY of a host payload
under ``.scratch/`` with scratch ``KIROCREW_HOME`` / ``KIRO_HOME`` / ``HOME``,
exactly the recipe ``spa-host/tests/test_playwright.py`` uses. The live gateway
and ``~/.kiro/crew`` are never touched (the only thing read from there is a
public openvscode-server engine already downloaded on this desk, copied into the
scratch home). Only demo/first-party mods are installed, and the IDE opens a
throwaway folder this script creates.

Every shot is taken in the dashboard's stock LIGHT palette (the host's
``theme_mode`` and the renderer's ``mc-theme`` are both set to light before the
first frame; a frame that still paints dark is flipped in-page and logged), with
the manager's unofficial banner already dismissed the way a user dismisses it
("Don't show this again" = ``localStorage`` ``floofycrew.banner.hidden``). The
IDE's workbench is asked for the matching stock light theme and, when a build
boots dark anyway, switched through its own Color Theme picker.

    uv run --with pillow python branding/shots/capture.py              # everything
    uv run --with pillow python branding/shots/capture.py --only floofycrew
    uv run --with pillow python branding/shots/capture.py --only ide

Raw frames land in ``.scratch/shots/raw/``; each one is then run through
``macshadow.py`` into ``branding/shots/``. The gateway is stopped in a
``finally`` and the payload copy's shell is restored byte-identically
(``ShellGuard``), so the copy stays reusable by the test suite.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for relative in ("floofy-core", "floofy-core/tests", "loader-app", "loader-app/tests", "spa-host/tests", "scripts"):
    sys.path.insert(0, str(REPO_ROOT / relative))

LIVE_HOME = Path("~/.kiro/crew").expanduser()
if Path(os.environ.get("KIROCREW_HOME", "/nonexistent")).expanduser().resolve() == LIVE_HOME.resolve():
    raise SystemExit("refusing to run with KIROCREW_HOME pointing at the live home")

from floofy_loader.paths import FloofyPaths  # noqa: E402
from loader_testing import ScratchGateway, ShellGuard, build_loader_app, bundle_interpreter, fresh_scratch, scratch_payload  # noqa: E402
from spa_testing import consent, dashboard_url, install_loader_app, install_mod, write_host_config  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from macshadow import save_within, shadowed  # noqa: E402

#: Demo / first-party mods the screenshots may show. Deliberately no
#: rimuru-branding, no mochi-pet-zoom-fix, no agent-backend-router.
DEMO_MODS = (
    "custom-themes",
    "settings-demo",
    "display-comfort",
    "model-retry-policy",
    "agent-backend-doctor",
    "agent-orchestrator",
    "kiro-spec-plan",
    "os-notify-bridge",
)
#: Mod pages to try for the "a mod page" shot, in order of preference.
MOD_PAGE_CANDIDATES = ("model-retry-policy", "agent-backend-doctor", "agent-orchestrator", "settings-demo")

#: The KiroCrew IDE mod checkout: FLOOFY_IDE_SRC, else a sibling of this repository.
IDE_SRC = Path(os.environ.get("FLOOFY_IDE_SRC", str(REPO_ROOT.parent / "KirocrewIdeMod")))
IDE_ID = "kirocrew-ide"
#: A public openvscode-server release already unpacked on this desk; copied, never used in place.
LIVE_ENGINES = LIVE_HOME / "floofy" / "mods" / IDE_ID / ".floofy" / "engines"

SHOTS_RAW = REPO_ROOT / ".scratch" / "shots" / "raw"
SHOTS_OUT = REPO_ROOT / "branding" / "shots"
VIEWPORT = {"width": 1280, "height": 800}
SCALE = 2

#: The throwaway workspace the IDE opens: nothing real, nothing internal.
DEMO_WORKSPACE_FILES = {
    "README.md": (
        "# Demo workspace\n\n"
        "A throwaway folder for the KiroCrew IDE screenshots.\n\n"
        "- `main.py` - a few lines of Python\n"
        "- `settings.json` - a small config file\n"
    ),
    "main.py": (
        '"""A tiny demo module for the KiroCrew IDE screenshot."""\n\n\n'
        "def greet(name: str) -> str:\n"
        '    """Return a greeting for ``name``."""\n'
        '    return f"Hello, {name}!"\n\n\n'
        'if __name__ == "__main__":\n'
        '    print(greet("FloofyCrew"))\n'
    ),
    "settings.json": json.dumps({"theme": "light", "fontSize": 13, "tabSize": 4, "demo": True}, indent=2) + "\n",
}

results: list[dict] = []
skipped: list[tuple[str, str]] = []


def log(message: str) -> None:
    print(f"[shots] {message}", flush=True)


def finish(name: str, raw: Path) -> None:
    """Post-process one raw capture into ``branding/shots/<name>.png`` with the macOS shadow."""
    from PIL import Image

    out = SHOTS_OUT / f"{name}.png"
    with Image.open(raw) as handle:
        source_size = handle.size
        framed = shadowed(handle, radius=24, blur=34, offset=24, margin=80, alpha=0.58)
    path, size, dimensions = save_within(framed, out, max_bytes=1_500_000, max_width=2720)
    results.append({"name": name, "path": str(path), "pixels": dimensions, "bytes": size, "raw": str(raw), "raw_pixels": source_size})
    log(f"finished {path.name}  {dimensions[0]}x{dimensions[1]}  {size:,} B")


def write_demo_workspace(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, text in DEMO_WORKSPACE_FILES.items():
        (root / name).write_text(text, encoding="utf-8")
    return root


def grant_app(gw: ScratchGateway, name: str) -> None:
    """Add ``name`` to the host config's operator-writable ``agent.apps_trusted`` (after the install, as the host binds it)."""
    config_path = gw.home / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    agent = config.setdefault("agent", {})
    trusted = agent.setdefault("apps_trusted", [])
    if name not in trusted:
        trusted.append(name)
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def install_early_shim(gw: ScratchGateway, payload: Path, app_dir: Path) -> str:
    """Install the Loader's early shim for this scratch run (``floofy init --early`` does it on a real host).

    It lands in the USER site of the payload's own interpreter which, with
    ``HOME`` pointed at this run's scratch directory, is inside the scratch tree:
    nothing outside it is touched. Without the shim the IDE mod's early hook
    never runs, so its same-origin workbench proxy is not registered, the App
    falls back to the engine's own port (a "forward that port too" banner) and
    the bridge cannot reach the dashboard API with the user's own session.
    """
    from floofy_loader.early_install import install_user_site

    interpreter = bundle_interpreter(payload)
    if interpreter is None:
        return "no bundled interpreter in the payload copy"
    try:
        shim = install_user_site(interpreter, env=gw.env(), source=app_dir / "floofy_early")
    except Exception as exc:  # noqa: BLE001 - the shots still work without it
        return f"not installed ({type(exc).__name__}: {exc})"
    return f"installed ({getattr(shim, 'site_dir', '') or 'user site'})"


#: Engine-side settings written before the first start. The mod's own
#: ``_seed_machine_settings`` only ADDS missing keys, so these survive it. All three
#: exist to keep modal chrome out of a screenshot: VS Code's "do you trust the authors
#: of this folder" dialog, its Welcome tab, and the "install the recommended Python
#: extension" toast. Nothing here loosens anything on the real desk — the file lives in
#: the scratch HOME this run creates and throws away.
ENGINE_MACHINE_SETTINGS = {
    "security.workspace.trust.enabled": False,
    "workbench.startupEditor": "none",
    "extensions.ignoreRecommendations": True,
}
#: The mod's own scheme -> VS Code theme mapping (``hook`` SCHEME_THEMES).
SCHEME_THEMES = {"dark": "Default Dark Modern", "light": "Default Light Modern"}


def seed_engine_settings(fake_home: Path, scheme: str = "light") -> Path:
    """Write the engine's Machine settings (chrome) and User settings (colour theme).

    ``workbench.colorTheme`` is an APPLICATION-scoped setting, so VS Code ignores
    it in Machine settings — it only takes effect from User settings, which is
    why the two files are written separately.
    """
    data = fake_home / ".openvscode-server" / "data"
    machine = data / "Machine" / "settings.json"
    machine.parent.mkdir(parents=True, exist_ok=True)
    machine.write_text(json.dumps(ENGINE_MACHINE_SETTINGS, indent=2) + "\n", encoding="utf-8")
    user = data / "User" / "settings.json"
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text(json.dumps({"workbench.colorTheme": SCHEME_THEMES[scheme]}, indent=2) + "\n", encoding="utf-8")
    return machine


def install_ide(gw: ScratchGateway, ide_src: Path) -> str | None:
    """Install the IDE mod plus its App part and a COPY of a local engine; returns a reason when it cannot be done."""
    if not (ide_src / "floofy.json").is_file():
        return f"no IDE mod checkout at {ide_src}"
    install_mod(gw, ide_src)
    engine = None
    if LIVE_ENGINES.is_dir():
        for entry in sorted(LIVE_ENGINES.iterdir(), reverse=True):
            if (entry / "bin" / "openvscode-server").is_file():
                engine = entry
                break
    if engine is None:
        return "no openvscode-server engine found on this desk (the App's Install engine flow needs GitHub)"
    target = FloofyPaths(gw.data_home).mod_dir(IDE_ID) / ".floofy" / "engines" / engine.name
    if not (target / "bin" / "openvscode-server").is_file():
        log(f"copying the engine {engine.name} into the scratch home (~240 MB)")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(engine, target, symlinks=True, dirs_exist_ok=True)
    return None


# --- browser helpers --------------------------------------------------------------------------

#: The App's "Don't show this again" preference (``BANNER_PREFERENCE_KEY`` in loader-app/ui/index.mjs).
BANNER_PREFERENCE_KEY = "floofycrew.banner.hidden"


def new_page(browser, *, color_scheme: str = "light", height: int | None = None):
    context = browser.new_context(
        viewport={"width": VIEWPORT["width"], "height": height or VIEWPORT["height"]},
        device_scale_factor=SCALE,
        color_scheme=color_scheme,
    )
    # the dashboard keeps its light/dark choice in localStorage (`mc-theme`); seed it so a
    # fresh profile paints the mode we want from the first frame. The manager's unofficial
    # banner is dismissed the way a user dismisses it: the same localStorage preference.
    context.add_init_script(
        "try { localStorage.setItem('mc-theme', %s); localStorage.setItem(%s, '1'); } catch (e) {}"
        % (json.dumps(color_scheme), json.dumps(BANNER_PREFERENCE_KEY))
    )
    return context, context.new_page()


def open_manager(page, gw) -> None:
    page.goto(dashboard_url(gw, "/apps/floofycrew"), wait_until="load", timeout=120000)
    page.wait_for_selector('[data-testid="floofycrew-nav"]', timeout=120000)
    page.wait_for_selector('[data-testid="floofycrew-footer"]', timeout=60000)
    if page.locator('[data-testid="floofycrew-banner"]').count():
        log("the unofficial banner is showing despite the seeded preference; dismissing it by its button")
        page.click('[data-testid="floofycrew-banner-dismiss"]')
        page.wait_for_selector('[data-testid="floofycrew-banner"]', state="detached", timeout=10000)
    if not is_light(page):
        log("the dashboard is still painting dark; flipping the palette in the page")
        set_light(page)


def is_light(page) -> bool:
    """True when the dashboard is actually painting a light palette (its ``--bg`` token is bright)."""
    return bool(page.evaluate(
        """() => {
          const probe = document.createElement('div');
          probe.style.color = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim() || getComputedStyle(document.body).backgroundColor;
          document.body.appendChild(probe);
          const rgb = (getComputedStyle(probe).color.match(/\\d+/g) || [0, 0, 0]).map(Number);
          probe.remove();
          return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) > 128;
        }"""
    ))


def set_light(page) -> None:
    """Last resort: flip ``html[data-theme]`` to the light side of the palette in the page itself."""
    page.evaluate(
        """() => {
          const root = document.documentElement;
          const current = root.getAttribute('data-theme') || 'kiro-dark';
          root.setAttribute('data-theme', current.endsWith('-dark') ? current.slice(0, -5) + '-light' : 'kiro-light');
        }"""
    )
    page.wait_for_timeout(500)


def shot(page, name: str, *, settle: float = 0.9) -> None:
    SHOTS_RAW.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(int(settle * 1000))
    raw = SHOTS_RAW / f"{name}.png"
    page.screenshot(path=str(raw))
    finish(name, raw)


def nav(page, key: str, marker: str) -> None:
    page.click(f'[data-testid="floofycrew-nav-{key}"]')
    page.wait_for_selector(f'[data-testid="floofycrew-page-{key}"] [data-testid="{marker}"]', timeout=60000)


# --- the FloofyCrew manager App ---------------------------------------------------------------

#: The Mods page is the README's hero: header, tabs, the mod-pages list and the install form at
#: the standard viewport. (A full-page capture of it is 1800 px tall and renders as a strip.)


def capture_manager(browser, gw) -> None:
    context, page = new_page(browser)
    try:
        open_manager(page, gw)
        page.wait_for_selector('[data-testid="floofycrew-mod-row-model-retry-policy"]', timeout=60000)
        shot(page, "floofycrew-mods")
    finally:
        context.close()

    context, page = new_page(browser)
    try:
        open_manager(page, gw)
        nav(page, "registry", "floofycrew-sources")
        shot(page, "floofycrew-registry-sources")

        nav(page, "doctor", "floofycrew-doctor")
        try:
            page.wait_for_function(
                "() => document.querySelector('[data-testid=\"floofycrew-doctor-consent\"]')?.textContent.includes('ok')",
                timeout=60000,
            )
        except Exception as exc:  # noqa: BLE001 - the page is the shot; a slow check is not fatal
            log(f"doctor consent line did not settle ({type(exc).__name__}); capturing anyway")
        shot(page, "floofycrew-doctor")

        capture_mod_page(page)
    finally:
        context.close()


def capture_mod_page(page) -> None:
    """The first demo mod page that renders content; a page that faults is reported, not shipped."""
    nav(page, "mods", "floofycrew-mod-pages")
    for mod_id in MOD_PAGE_CANDIDATES:
        opener = page.locator(f'[data-testid="floofycrew-open-page-{mod_id}"]')
        if opener.count() == 0:
            skipped.append((f"mod page {mod_id}", "no page row in the Mods list"))
            continue
        opener.click()
        try:
            page.wait_for_selector('[data-testid="floofycrew-modpage-back"]', timeout=60000)
        except Exception as exc:  # noqa: BLE001
            skipped.append((f"mod page {mod_id}", f"the page never mounted ({type(exc).__name__})"))
            nav(page, "mods", "floofycrew-mod-pages")
            continue
        page.wait_for_timeout(2500)
        if page.locator('[data-testid="floofycrew-modpage-fault"]').count():
            reason = page.inner_text('[data-testid="floofycrew-modpage-fault"]').replace("\n", " ")[:300]
            skipped.append((f"mod page {mod_id}", f"error state: {reason}"))
            page.click('[data-testid="floofycrew-modpage-back"]')
            page.wait_for_selector('[data-testid="floofycrew-mod-pages"]', timeout=30000)
            continue
        shot(page, "floofycrew-mod-page")
        log(f"mod page shot taken from {mod_id}")
        return
    skipped.append(("floofycrew-mod-page", "no demo mod page rendered content"))


# --- the install confirmation modal (no network: a local mod directory) -----------------------

def capture_install_confirm(browser, gw) -> None:
    """Install a throwaway mod from a local directory and capture the question the App asks first.

    The source is typed RELATIVE to the gateway's working directory so no home
    path ends up in a public README; the mod carries an ``agent`` part, which is
    what makes the manager ask the governance question. Answered "No" — nothing
    is installed — and the directory is removed again.
    """
    import hashlib

    relative = "floofy-demo-mod"
    root = gw.payload / relative
    files = {
        "agents/demo.json": json.dumps({"name": "demo-agent", "description": "a throwaway agent"}, indent=2) + "\n",
        # a governance-altering file: this is what makes the manager stop and ask (and then
        # ask for the path to be typed out) instead of installing straight away
        "security_policy.json": "{}\n",
        "README.md": "# demo mod\n\nA throwaway mod used for the FloofyCrew screenshots.\n",
    }
    shutil.rmtree(root, ignore_errors=True)
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    manifest = {
        "schema": 1,
        "id": "demo-agent-pack",
        "name": "Demo agent pack",
        "version": "1.0.0",
        "description": "A throwaway mod that adds one agent - here to show what the manager asks before it changes anything.",
        "authors": ["FloofyCrew"],
        "license": "MIT",
        "kirocrew": {"version": ">=0.7.0 <0.9.0"},
        "dependsOn": {"floofycrew": ">=0.0.0"},
        "parts": [{"kind": "agent", "side": "gateway", "path": "agents/demo.json"}],
        "files": [{"path": name, "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()} for name in sorted(files)],
    }
    (root / "floofy.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    context, page = new_page(browser)
    try:
        open_manager(page, gw)
        page.wait_for_selector('[data-testid="floofycrew-install-form"]', timeout=60000)
        page.fill('[data-testid="floofycrew-install-source"]', relative)
        page.click('[data-testid="floofycrew-install-submit"]')
        page.wait_for_selector('[data-testid="floofycrew-modal"]', timeout=60000)
        page.wait_for_timeout(700)
        shot(page, "floofycrew-install-confirm")
        for decline in ("floofycrew-yesno-no", "floofycrew-unlisted-cancel", "floofycrew-typed-cancel", "floofycrew-unsigned-keep"):
            button = page.locator(f'[data-testid="{decline}"]')
            if button.count():
                button.click()
                break
        page.wait_for_selector('[data-testid="floofycrew-modal"]', state="detached", timeout=30000)
    except Exception as exc:  # noqa: BLE001 - optional shot
        said = ""
        try:
            if page.locator('[data-testid="floofycrew-output"]').count():
                said = " | App output: " + page.inner_text('[data-testid="floofycrew-output"]').replace("\n", " ")[:400]
        except Exception:  # noqa: BLE001
            pass
        skipped.append(("floofycrew-install-confirm", f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}{said}"))
    finally:
        context.close()
        shutil.rmtree(root, ignore_errors=True)


# --- the KiroCrew IDE mod ----------------------------------------------------------------------

def open_ide_app(page, gw):
    page.goto(dashboard_url(gw, "/apps/kirocrew-ide"), wait_until="load", timeout=120000)
    page.wait_for_selector('[data-testid="dashboard-shell"]', timeout=120000)
    if not is_light(page):
        log("the dashboard is still painting dark on the IDE page; flipping the palette in the page")
        set_light(page)
    page.wait_for_selector('[data-testid="kirocrew-ide-status"], [data-testid="kirocrew-ide-dock"]', timeout=120000)


def capture_ide_app(browser, gw) -> None:
    """The App's own page, before an engine runs: status, folder, Start."""
    context, page = new_page(browser)
    try:
        open_ide_app(page, gw)
        shot(page, "kirocrew-ide-app", settle=1.5)
    except Exception as exc:  # noqa: BLE001
        skipped.append(("kirocrew-ide-app", f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"))
    finally:
        context.close()


def start_ide(gw, workspace: Path, *, scheme: str = "light") -> str | None:
    """Ask the mod to start its engine on the throwaway folder; returns a reason when it would not.

    The scheme is reported first, the way the App reports the dashboard's
    resolved light/dark: the mod splices the matching stock theme into the served
    workbench as a ``configurationDefaults`` default, so a fresh profile boots in it.
    """
    client = gw.client()
    client.post(f"/api/apps/floofycrew/mods/{IDE_ID}/api/theme", {"scheme": scheme})
    reply = client.post(f"/api/apps/floofycrew/mods/{IDE_ID}/api/start", {"folder": str(workspace)}, timeout=300)
    if reply.status != 200:
        return f"engine start returned HTTP {reply.status}: {reply.body[:400].decode('utf-8', 'replace')}"
    log(f"engine started: {reply.body[:200].decode('utf-8', 'replace')}")
    return None


def workbench_is_light(frame) -> bool:
    """True when the workbench paints a light editor (its ``--vscode-editor-background`` is bright).

    Read off ``.monaco-workbench`` — VS Code emits its theme as one CSS rule on
    the workbench root, and custom properties do not inherit upward.
    """
    return bool(frame.locator(".monaco-workbench").first.evaluate(
        """(el) => {
          const value = getComputedStyle(el).getPropertyValue('--vscode-editor-background').trim() || getComputedStyle(el).backgroundColor;
          const probe = document.createElement('div');
          probe.style.color = value;
          document.body.appendChild(probe);
          const rgb = (getComputedStyle(probe).color.match(/\\d+/g) || [0, 0, 0]).map(Number);
          probe.remove();
          return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) > 128;
        }"""
    ))


def ensure_workbench_light(page, frame) -> None:
    """A workbench that booted dark anyway is switched through its own Color Theme picker.

    That is what a user does, and it is the one mechanism that repaints a booted
    window: the mod's ``configurationDefaults`` and the engine's settings files
    only decide the first frame of a FRESH profile. Logged either way.
    """
    if workbench_is_light(frame):
        log("workbench booted light")
        return
    log("workbench booted dark; selecting the stock light theme through its Color Theme picker")
    frame.locator(".monaco-editor .view-lines").first.click(timeout=10000)  # focus the open editor, not the chrome
    page.keyboard.press("F1")
    picker = frame.locator(".quick-input-widget input").first
    picker.wait_for(timeout=15000)
    picker.fill("Preferences: Color Theme")
    page.wait_for_timeout(600)
    page.keyboard.press("Enter")
    page.wait_for_timeout(800)
    picker.fill(SCHEME_THEMES["light"])
    page.wait_for_timeout(600)
    page.keyboard.press("Enter")
    page.wait_for_timeout(1500)
    if workbench_is_light(frame):
        log("workbench repainted light")
    else:
        skipped.append(("kirocrew-ide (editor theme)", "the workbench stayed dark after the Color Theme picker; the shot shows a dark editor"))


def capture_ide_workbench(browser, gw, name: str) -> None:
    context, page = new_page(browser)
    try:
        open_ide_app(page, gw)
        frame = page.frame_locator('#kirocrew-ide-layer iframe[data-active="true"]')
        frame.locator(".monaco-workbench").first.wait_for(timeout=240000)
        open_demo_file(page, frame)
        ensure_workbench_light(page, frame)
        shot(page, name, settle=3.0)
    except Exception as exc:  # noqa: BLE001
        detail = workbench_diagnostics(page)
        try:
            SHOTS_RAW.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(SHOTS_RAW / f"{name}-failed.png"))
        except Exception:  # noqa: BLE001
            pass
        skipped.append((name, f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]} | workbench: {detail}"))
    finally:
        context.close()


def workbench_diagnostics(page) -> str:
    """What the workbench DOM actually holds, for a skip reason that can be acted on.

    Read through frame locators, not ``page.evaluate``: with the same-origin proxy
    unavailable the workbench iframe is a different origin and the page cannot
    reach into it.
    """
    frame = page.frame_locator('#kirocrew-ide-layer iframe[data-active="true"]')
    try:
        return json.dumps({
            "iframes": page.locator("#kirocrew-ide-layer iframe").count(),
            "workbench": frame.locator(".monaco-workbench").count(),
            "dialog": frame.locator(".monaco-dialog-box").count(),
            "activitybar": frame.locator(".activitybar [aria-label]").evaluate_all("els => els.map((e) => e.getAttribute('aria-label')).slice(0, 12)"),
            "explorerView": frame.locator(".explorer-folders-view").count(),
            "explorerRows": frame.locator(".explorer-folders-view .monaco-list-row").count(),
            "paneTitles": frame.locator(".pane-header .title").evaluate_all("els => els.map((e) => e.textContent).slice(0, 8)"),
        })
    except Exception as exc:  # noqa: BLE001
        return f"(diagnostics unavailable: {type(exc).__name__}: {str(exc).splitlines()[0][:120]})"


def show_explorer(page, frame) -> None:
    """Bring the FILE explorer forward, whichever way this engine build exposes it."""
    rows = frame.locator(".explorer-folders-view .monaco-list-row")
    for selector in ('.activitybar [aria-label*="Explorer"]', ".activitybar .codicon-files"):
        item = frame.locator(selector).first
        if not item.count():
            continue
        try:
            item.click(timeout=10000)
            rows.first.wait_for(timeout=15000)
            return
        except Exception:  # noqa: BLE001 - try the next handle
            continue
    # the keybinding: focus the workbench (the editor watermark is inert) and ask for the view
    try:
        frame.locator(".monaco-workbench").first.click(position={"x": 8, "y": 8}, timeout=10000)
    except Exception:  # noqa: BLE001
        pass
    page.keyboard.press("Control+Shift+E")
    rows.first.wait_for(timeout=40000)


def open_demo_file(page, frame) -> None:
    """Settle the workbench for a screenshot: trust the folder, show the FILE explorer, open ``main.py``.

    The explorer matters beyond looks: the bridge extension's own Steering /
    Skills views derive ``$HOME`` from the workspace path (``extension.js``
    "Derive $HOME from the workspace folder's path"), so with the scratch
    workspace under a real home they list that desk's global ``~/.kiro`` entries.
    The file explorer shows the throwaway folder and nothing else.
    """
    # the trust dialog should not appear at all (ENGINE_MACHINE_SETTINGS turns it off);
    # dismiss it anyway when an engine build ignores the setting
    trust = frame.locator('.monaco-dialog-box :text("I trust the authors")').first
    try:
        trust.wait_for(timeout=8000)
        trust.click()
        # granting trust reloads the workbench window: wait for it to come back
        frame.locator(".monaco-workbench").first.wait_for(timeout=120000)
    except Exception:  # noqa: BLE001 - no dialog, which is the expected case
        pass

    show_explorer(page, frame)
    rows = frame.locator(".explorer-folders-view .monaco-list-row")
    rows.filter(has_text="main.py").first.click(timeout=30000)
    frame.locator(".monaco-editor").first.wait_for(timeout=60000)

    # the engine's own toasts (extension recommendations) are noise in a screenshot
    for _ in range(4):
        toast = frame.locator(".notification-toast .codicon-notifications-clear").first
        if not toast.count():
            break
        try:
            toast.click(timeout=5000)
        except Exception:  # noqa: BLE001
            break


# --- main --------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", choices=("all", "floofycrew", "ide"), default="all")
    parser.add_argument("--ide-src", type=Path, default=IDE_SRC, help=f"the KiroCrew IDE mod checkout (default {IDE_SRC})")
    parser.add_argument("--no-install-confirm", action="store_true", help="skip the optional install-confirmation shot")
    parser.add_argument("--workspace", type=Path, default=REPO_ROOT / ".scratch" / "shots" / "workspace",
                        help="where the throwaway demo workspace is written; the IDE's bar shows this path, so for a "
                             "public shot pick one that carries no user name (default .scratch/shots/workspace)")
    args = parser.parse_args(argv)

    payload = scratch_payload()
    if payload is None:
        raise SystemExit("no payload copy under .scratch/ (set FLOOFY_SCRATCH_PAYLOAD)")
    want_floofycrew = args.only in ("all", "floofycrew")
    want_ide = args.only in ("all", "ide")

    scratch = fresh_scratch("shots")
    fake_home = scratch / "fake-home"
    fake_home.mkdir(parents=True, exist_ok=True)
    log(f"payload copy: {payload}")
    log(f"scratch: {scratch}")

    app_dir = build_loader_app(scratch / "app" / "floofycrew")
    # FLOOFY_ALLOW_LOCAL_GIT: local sources are allowed in this throwaway gateway.
    # HOME: the IDE engine keeps its user data under ~, so the scratch run gets its own.
    gw = ScratchGateway(payload, scratch, extra_env={"FLOOFY_ALLOW_LOCAL_GIT": "1", "HOME": str(fake_home)})
    guard = ShellGuard(payload, gw.data_home)
    FloofyPaths(gw.data_home).ensure()
    for mod_id in DEMO_MODS:
        install_mod(gw, REPO_ROOT / "mods" / mod_id)
    ide_reason = None if want_ide else "not requested (--only)"
    if want_ide:
        ide_reason = install_ide(gw, args.ide_src)
        if ide_reason:
            skipped.append(("kirocrew-ide-*", ide_reason))
        else:
            seed_engine_settings(fake_home, "light")
    consent(gw)
    install_loader_app(gw, app_dir)
    # the stock LIGHT palette: no third-party theme pack is selected for these shots
    write_host_config(gw, dashboard={"theme_mode": "light", "onboarded": True, "import_onboarded": True, "privacy_acked": True, "language": "en"})
    if want_ide and not ide_reason:
        installed = gw.cli("app", "install", str(args.ide_src / "app"))
        if installed.returncode != 0:
            ide_reason = f"kirocrew app install failed: {(installed.stdout + installed.stderr).strip()[:300]}"
            skipped.append(("kirocrew-ide-*", ide_reason))
        else:
            grant_app(gw, IDE_ID)
            enabled = gw.cli("app", "enable", IDE_ID)
            if enabled.returncode != 0:
                ide_reason = f"kirocrew app enable failed: {(enabled.stdout + enabled.stderr).strip()[:300]}"
                skipped.append(("kirocrew-ide-*", ide_reason))

    log(f"early shim: {install_early_shim(gw, payload, app_dir)}")
    workspace = write_demo_workspace(args.workspace.expanduser().resolve())
    log(f"demo workspace: {workspace}")
    SHOTS_OUT.mkdir(parents=True, exist_ok=True)
    started = time.time()
    gw.start()
    log(f"gateway ready on port {gw.ready['port']} ({time.time() - started:.1f} s)")
    if want_ide and not ide_reason:
        # the Loader writes early.json while it activates; the early hook only arms on the NEXT
        # start, which is the one `kirocrew restart` the IDE mod's install notes ask for
        deadline = time.time() + 30
        while time.time() < deadline and not (gw.data_home / "early.json").is_file():
            time.sleep(1)
        if (gw.data_home / "early.json").is_file():
            log("restarting once so the IDE's early hook arms its same-origin workbench proxy")
            gw.stop()
            gw.start()
            log(f"gateway ready on port {gw.ready['port']}")
        else:
            log("no early.json was written; the IDE will fall back to the engine's own port")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                if want_floofycrew:
                    capture_manager(browser, gw)
                if want_ide and not ide_reason:
                    capture_ide_app(browser, gw)
                    ide_reason = start_ide(gw, workspace)
                    if ide_reason:
                        skipped.append(("kirocrew-ide", ide_reason))
                    else:
                        capture_ide_workbench(browser, gw, "kirocrew-ide")

                # --- optional, and last: it drives a real install through to its question ---
                if want_floofycrew and not args.no_install_confirm:
                    capture_install_confirm(browser, gw)
            finally:
                browser.close()
    finally:
        gw.stop()
        log("gateway stopped")
        try:
            guard.check()
            log("payload copy restored byte-identically")
        except AssertionError as exc:
            log(f"WARNING: {exc}")

    report()
    return 0


def report() -> None:
    print("\n=== finished ===")
    for row in results:
        print(f"{row['path']}  {row['pixels'][0]}x{row['pixels'][1]}  {row['bytes']:,} B  (raw {row['raw_pixels'][0]}x{row['raw_pixels'][1]})")
    if skipped:
        print("\n=== skipped ===")
        for name, reason in skipped:
            print(f"{name}: {reason}")
    print("\nre-run: uv run --with pillow python branding/shots/capture.py")
    (REPO_ROOT / ".scratch" / "shots" / "report.json").write_text(
        json.dumps({"finished": results, "skipped": [{"shot": n, "reason": r} for n, r in skipped]}, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
