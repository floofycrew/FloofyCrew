"""``floofy self-update`` and ``floofy config`` (task 10.7; Requirement 7.7, 11.6).

* :func:`update_check` — the daily check every surface shares (``doctor``,
  ``status``, the interactive ``floofy``, ``self-update``): the edition adapter's
  release feed, the cache under ``cache/self-update.json``, the 24 h rule,
  ``updates.check`` and ``--offline`` (:mod:`floofy_core.selfupdate`). It never
  raises and never blocks a command for more than the feed's short timeout.
* ``self-update [--check] [--now] [--force] [--target PYZ]`` — a fresh check;
  when a newer release supports the running host: the disclosure (version,
  release notes, what is replaced, what is staged), one confirmation (``--yes``),
  the downloads verified against ``SHA256SUMS`` (and the notes' table), the
  atomic swap of the installed ``floofy.pyz`` (the file the ``floofy`` wrapper
  runs), the Loader app archive staged under ``pending/self-update/`` and
  installed through the host App Kit when no gateway runs (or with ``--now``),
  ``op: self-update`` audit rows. The CLI is not the gateway, so the zipapp
  swap is safe at any time; the Loader app waits for an idle gateway.
* ``config get [KEY]`` / ``config set KEY VALUE`` — FloofyCrew's own settings
  (:mod:`floofy_core.settings`): today ``updates.check``.
* :func:`apply_staged` — the trigger path (``floofy apply``) installs a staged
  Loader app the next time it runs without a gateway.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

from .. import __version__ as FRAMEWORK_VERSION
from ..deploy import utc_now
from ..loaderapp import installed_meta
from ..selfupdate import CACHE_NAME, FeedError, Release, ReleaseFeed, SelfUpdateError, StagePlan, UpdateCheck, check, download_release, is_newer, read_stage, swap, write_stage
from ..settings import KNOWN_SETTINGS, SettingsError
from . import cmd_init
from .context import CliContext, CliError, _running_from_zipapp

__all__ = ["apply_staged", "config_cmd", "default_target", "register", "self_update", "update_check"]

#: The installed zipapp on both editions when this process is not the zipapp itself (the install scripts' layout).
INSTALLED_PYZ = Path.home() / ".local" / "lib" / "floofycrew" / "floofy.pyz"


def register(sub: argparse._SubParsersAction) -> None:
    su = sub.add_parser("self-update", help="update FloofyCrew itself to the newest release that supports this host (verified download, atomic swap, Loader app staged)")
    su.add_argument("--check", action="store_true", help="only check (a fresh check, ignoring the daily cache) and report")
    su.add_argument("--now", action="store_true", help="also install the Loader app update right away, even while a gateway runs (default: staged until no gateway runs)")
    su.add_argument("--install-staged", action="store_true", help=argparse.SUPPRESS)  # the swapped-in release installs the stage on behalf of the process that swapped it
    su.add_argument("--force", action="store_true", help="install the newest release even when it is not newer or does not list this host version as supported")
    su.add_argument("--target", metavar="PYZ", default=None, help="the installed floofy.pyz to replace (default: the zipapp this floofy runs from, else ~/.local/lib/floofycrew/floofy.pyz)")
    su.set_defaults(handler=self_update)
    cfg = sub.add_parser("config", help="FloofyCrew's own settings (config.json in the data home): updates.check")
    actions = cfg.add_subparsers(dest="config_action", metavar="ACTION")
    get = actions.add_parser("get", help="print one setting, or every setting with its effective value")
    get.add_argument("key", nargs="?", default=None)
    setter = actions.add_parser("set", help="store a setting (e.g. `updates.check false`)")
    setter.add_argument("key")
    setter.add_argument("value")
    cfg.set_defaults(handler=config_cmd)


# --- the shared check -----------------------------------------------------------------------------------------------


def update_check(ctx: CliContext, *, force: bool = False, explicit: bool = False) -> UpdateCheck:
    """The daily FloofyCrew release check for this host (never raises; see :func:`floofy_core.selfupdate.check`).

    ``explicit`` (``floofy self-update``) bypasses ``updates.check`` — the user is
    asking right now — but still honours ``--offline``.
    """
    current = ctx.current_payload()
    feed: ReleaseFeed | None = ctx.release_feed()
    try:
        enabled = bool(ctx.settings().get("updates.check"))
    except SettingsError:
        enabled = True
    opener = None
    if feed is not None and not ctx.offline:
        try:
            opener = ctx.url_opener(feed.url)
        except Exception:  # noqa: BLE001 - a broken identity means anonymous HTTPS, never a failed command
            opener = None
    return check(
        feed,
        ctx.home.cache / CACHE_NAME,
        running=FRAMEWORK_VERSION,
        edition=ctx.edition(),
        channel=current.channel if current else None,
        host_version=current.host_version.text if current else None,
        opener=opener,
        enabled=enabled or explicit,
        offline=ctx.offline,
        force=force,
    )


def summary_for(ctx: CliContext, *, force: bool = False, explicit: bool = False) -> dict[str, Any]:
    """The check plus the staged Loader app update, for ``--json`` documents and the Loader's ``/state``."""
    outcome = update_check(ctx, force=force, explicit=explicit)
    return {**outcome.summary(), "staged": read_stage(ctx.home.pending)}


def describe(ctx: CliContext, outcome: UpdateCheck) -> str:
    """One line for ``self-update``: the notice, else why there is nothing to install (the reason ``doctor`` prints too), else the check's state."""
    if outcome.notice:
        return outcome.notice
    if outcome.status == "ok" and outcome.latest is not None:
        return f"FloofyCrew {FRAMEWORK_VERSION}: up to date for this host ({outcome.reason}; checked {outcome.checked_at})"
    return f"FloofyCrew {FRAMEWORK_VERSION}: update check {outcome.status} — {outcome.detail}"


def say_stage(ctx: CliContext) -> None:
    stage = read_stage(ctx.home.pending)
    if stage and not stage.get("applied") and stage.get("present"):
        ctx.say(f"Loader app update to FloofyCrew {stage.get('version')} staged ({Path(str(stage.get('archive'))).name}): installed by `floofy apply` the next time no gateway runs, or now with `floofy self-update --now`", tone="warn")


# --- the swap -------------------------------------------------------------------------------------------------------


def default_target() -> Path | None:
    """The zipapp this process runs from, else the install scripts' path when it exists, else ``None`` (a checkout / package install)."""
    running = _running_from_zipapp()
    if running:
        return Path(running)
    return INSTALLED_PYZ if INSTALLED_PYZ.is_file() else None


def self_update(ctx: CliContext, args: argparse.Namespace) -> int:
    if getattr(args, "install_staged", False):
        # Invoked by the process that just swapped this release in: no feed, no download — only the
        # staged Loader app, installed with THIS release's code (install_stage_with_new_release).
        installed = apply_staged(ctx, force=True)
        ctx.set_result(selfUpdate={"installed": {"loaderApp": {"install": installed}}, "staged": read_stage(ctx.home.pending)})
        return 0
    if ctx.offline:
        raise CliError("self-update needs the network; drop --offline")
    outcome = update_check(ctx, force=True, explicit=True)
    document = {**outcome.summary(), "staged": read_stage(ctx.home.pending)}
    ctx.set_result(selfUpdate=document, checked=True)
    ctx.say(f"{ctx.paint('floofy self-update', 'heading')} — {describe(ctx, outcome)}")
    if outcome.status in ("unreachable", "no-feed", "offline"):
        raise CliError(f"cannot check for a FloofyCrew release: {outcome.detail}")
    release = outcome.latest
    if release is None:
        raise CliError("the release feed named no release")
    if args.check:
        say_stage(ctx)
        return 0
    if not outcome.available and not args.force:
        if not is_newer(release.version, FRAMEWORK_VERSION):
            # The running zipapp is the newest release, or ahead of the feed: nothing to swap
            # (describe said which). A Loader app archive staged by an earlier run is still
            # pending, though — the swap happens in the first run, the stage waits for a quiet
            # gateway — so `--now` (or no gateway) installs it here too, exactly as it would
            # have right after the swap. Before, this branch only *described* the stage and
            # `floofy self-update --now` after the swap did nothing (found on the test box).
            installed = install_stage_now(ctx, args)
            if installed is not None:
                ctx.set_result(selfUpdate={**document, "installed": {"loaderApp": {"install": installed}}})
            return 0
        raise CliError(f"FloofyCrew {release.version} does not list your host {outcome.host_version} ({outcome.edition}/{outcome.channel}) as supported; `floofy self-update --force` installs it anyway, at your own risk")
    target = Path(args.target).expanduser() if args.target else default_target()
    if target is None:
        raise CliError("this floofy does not run from an installed zipapp (a checkout or a package install): update it the way you installed it, or name the zipapp to replace with --target")
    plan = StagePlan(target, ctx.home.pending)
    meta = installed_meta(ctx.host_home)
    ctx.say(f"  release      {release.version} ({release.tag}){' — ' + release.url if release.url else ''}")
    ctx.say(f"  supports     {json.dumps(release.supports.get(str(outcome.edition), {}), sort_keys=True)}")
    ctx.say(f"  zipapp       {plan.target}  ← {release.assets.get('floofy.pyz')}  (verified against SHA256SUMS{'; notes say ' + release.sha256[:12] + '…' if release.sha256 else ''}, swapped atomically)")
    ctx.say(f"  loader app   {'staged under ' + str(plan.stage_dir) + (' and installed now' if args.now or not ctx.gateway_running() else ' — installed by `floofy apply` when no gateway runs (or --now)') if meta['installed'] else 'not installed here (floofy init installs it); the archive is staged only'}")
    if not ctx.console.confirm(f"Install FloofyCrew {release.version} over {plan.target}?", default=False):
        ctx.audit.record("self-update", result="declined", version=release.version, detail="user declined")
        ctx.say("declined: nothing changed")
        return 1
    opener = None
    try:
        opener = ctx.url_opener(release.assets.get("floofy.pyz") or "")
    except Exception:  # noqa: BLE001
        opener = None
    try:
        downloaded = download_release(release, plan, opener=opener)
    except SelfUpdateError as exc:
        ctx.audit.record("self-update", result="refused", version=release.version, detail=str(exc))
        raise CliError(f"self-update refused: {exc}") from exc
    except FeedError as exc:
        # The feed named the release but an asset could not be fetched: nothing was swapped or staged
        # (SHA256SUMS is read first). On the internal edition the feed is RELEASE.md on mainline while
        # the assets are raw blobs at the tag `v<version>`, so a release whose package commit is
        # pushed but not yet tagged shows up exactly like this (seen on the test box for 1.1.4):
        # a 404 at the tag URL. Say so instead of a traceback.
        ctx.audit.record("self-update", result="unavailable", version=release.version, detail=str(exc))
        tag_missing = "HTTP 404" in str(exc) and bool(release.tag) and f"/{release.tag}/" in str(exc)
        hint = f" — the feed already names {release.version} but the assets are read at the tag {release.tag}, which does not exist yet: the publisher still has to push that tag; try again later" if tag_missing else ""
        raise CliError(f"self-update could not fetch the release assets: {exc}{hint}; nothing was changed") from exc
    # everything below runs on modules imported before the swap: after os.replace the zipapp on disk is the new release
    swapped = swap(plan)
    stage = write_stage(plan, version=release.version, archive=Path(downloaded["loaderApp"]["path"]), sha256=downloaded["loaderApp"]["sha256"], by=ctx.actor)
    ctx.audit.record("self-update", result="ok", version=release.version, phase="swap", files=[str(swapped), downloaded["loaderApp"]["path"]], sha256=downloaded["pyz"]["sha256"], detail=f"{release.assets.get('floofy.pyz')} -> {swapped}; loader app staged")
    ctx.say(f"swapped {swapped} to FloofyCrew {release.version} (sha256 {downloaded['pyz']['sha256'][:12]}…); the next `floofy` runs it", tone="ok")
    result: dict[str, Any] = {"version": release.version, "target": str(swapped), "pyzSha256": downloaded["pyz"]["sha256"], "loaderApp": {**downloaded["loaderApp"], "stage": stage}}
    if meta["installed"]:
        result["loaderApp"]["install"] = install_stage_with_new_release(ctx, args, swapped)
    else:
        say_stage(ctx)
    ctx.set_result(selfUpdate={**document, "installed": result})
    return 0


def install_stage_with_new_release(ctx: CliContext, args: argparse.Namespace, swapped: Path) -> dict[str, Any] | None:
    """Run the Loader app step on the release that was just swapped in, not on this (old) process.

    Everything after :func:`swap` still executes the modules of the release being
    replaced. For the Loader step that means an update from a release with a bug in
    that very step re-runs the bug once more (the 1.1.3 → 1.1.4 and 1.1.4 → 1.1.5
    updates on the test box each tripped over the grant handling of the *previous*
    release). So when the stage is to be installed now, the new zipapp is invoked
    as a subprocess — ``<interpreter> <new pyz> [global flags] self-update
    --install-staged --json`` — with the same host home, launcher override and
    payload roots, and its ``--json`` result is relayed. If it cannot be run (the
    swap target is not a runnable zipapp, e.g. a test stand-in, or it exits without
    a result) the step falls back to this process, as before.
    """
    stage = read_stage(ctx.home.pending)
    if not stage or stage.get("applied") or not stage.get("present"):
        return None
    if not (args.now or not ctx.gateway_running()):
        say_stage(ctx)
        return None
    if not _looks_like_zipapp(swapped):
        return apply_staged(ctx, force=True)
    command = [sys.executable, str(swapped), "--home", str(ctx.host_home), "--json", "--yes"]
    if ctx.kirocrew is not None:
        command += ["--kirocrew", str(ctx.kirocrew)]
    for root in ctx.roots:
        command += ["--root", str(root)]
    if ctx.no_adapters:
        command.append("--no-adapters")
    if getattr(args, "no_color", False):
        command.append("--no-color")
    command += ["self-update", "--install-staged"]
    env = {**os.environ, "FLOOFY_ACTOR": ctx.actor}
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=600, env=env, check=False)
        document = json.loads(completed.stdout) if completed.stdout.strip() else {}
        install = document.get("selfUpdate", {}).get("installed", {}).get("loaderApp", {}).get("install")
    except (OSError, subprocess.SubprocessError, ValueError):
        install = None
    if not isinstance(install, dict):
        ctx.say("the new release could not run the Loader app step itself; running it here", tone="muted")
        return apply_staged(ctx, force=True)
    version = stage.get("version")
    if install.get("ok") and install.get("enabled"):
        ctx.say(f"Loader app updated to FloofyCrew {version} through the host App Kit (by the new release itself); it runs at the next gateway start", tone="ok")
    elif install.get("ok"):
        ctx.say(f"Loader app {version} is installed but DISABLED: the host refused to enable it. Run `floofy init` to record the grant and enable it, then restart the gateway", tone="warn")
    else:
        ctx.say("Loader app update: the host App Kit install did not complete; the archive stays staged (details in the audit log)", tone="warn")
    return {**install, "via": "new-release"}


def _looks_like_zipapp(path: Path) -> bool:
    """A zipapp ends in a zip central directory; a stand-in or a truncated download does not."""
    try:
        return zipfile.is_zipfile(path)
    except OSError:
        return False


def install_stage_now(ctx: CliContext, args: argparse.Namespace) -> dict[str, Any] | None:
    """Install the staged Loader app when ``--now`` asks for it or no gateway runs; otherwise say it is staged.

    Shared by both ends of ``self-update``: right after the swap and on a later run that
    finds the zipapp already current. ``--now`` overrides the running-gateway hold (the
    App Kit reinstall while a gateway runs takes effect at the next gateway start, which
    the message says); without it a running gateway leaves the stage for ``floofy apply``.
    """
    stage = read_stage(ctx.home.pending)
    if not stage or stage.get("applied") or not stage.get("present"):
        return None
    if args.now or not ctx.gateway_running():
        return apply_staged(ctx, force=True)
    say_stage(ctx)
    return None


def apply_staged(ctx: CliContext, *, force: bool = False) -> dict[str, Any] | None:
    """Install a staged Loader app update through the host App Kit — when no gateway runs, or ``force``.

    The same reinstall path as ``floofy init --reinstall-loader --loader-app
    <archive>`` (``kirocrew app uninstall`` / ``install`` / ``enable`` through the
    launcher); the marker is flipped to ``applied`` and the archive removed. A
    running gateway (and the in-gateway surface) leaves the stage untouched:
    ``None``.
    """
    stage = read_stage(ctx.home.pending)
    if stage is None or stage.get("applied") or not stage.get("present"):
        return None
    if not force and (ctx.in_gateway or ctx.gateway_running()):
        return None
    archive = Path(str(stage["archive"]))
    namespace = argparse.Namespace(no_loader_app=False, reinstall_loader=True, loader_app=str(archive), no_grant=True)
    outcome = cmd_init._loader_app_step(ctx, namespace)  # noqa: SLF001 - the edition's installer path, shared with init
    install = outcome.get("install")
    installed_ok = (install.get("returncode") == 0) if isinstance(install, dict) else (not outcome.get("manual") and bool(outcome.get("after", {}).get("installed")))
    # Installed is not enough: `kirocrew app enable` can be refused (the host drops the apps_trusted grant on
    # uninstall; a self-update from a release before the grant-restore fix runs the OLD code for this step, so
    # the first 1.1.3 → 1.1.4 update on the test box ended "installed but disabled" while this line still said
    # "updated"). The stage is consumed either way — the files are in place — but the message and the marker
    # say what is true and what to run.
    enabled_ok = bool(outcome.get("after", {}).get("enabled")) if installed_ok else False
    ok = installed_ok
    marker = ctx.home.pending / "self-update.json"
    record = {**{k: v for k, v in stage.items() if k != "present"}, "applied": ok, "appliedAt": utc_now() if ok else None, "outcome": "ok" if ok and enabled_ok else ("disabled" if ok else "manual"), "enabled": enabled_ok}
    try:
        marker.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        if ok:
            archive.unlink(missing_ok=True)
            stage_dir = archive.parent
            if stage_dir.is_dir() and not any(stage_dir.iterdir()):
                shutil.rmtree(stage_dir, ignore_errors=True)
    except OSError:
        pass
    if ok and enabled_ok:
        detail, line, tone, result = "Loader app installed and enabled through the host App Kit", f"Loader app updated to FloofyCrew {stage.get('version')} through the host App Kit; it runs at the next gateway start", "ok", "ok"
    elif ok:
        detail, line, tone, result = "Loader app installed through the host App Kit but `kirocrew app enable` was refused (agent.apps_trusted grant missing)", f"Loader app {stage.get('version')} is installed but DISABLED: the host refused to enable it (the grant is missing). Run `floofy init` to record the grant and enable it, then restart the gateway", "warn", "disabled"
    else:
        detail, line, tone, result = "Loader app install needs the manual steps (see above)", "Loader app update: the host App Kit install did not complete; the archive stays staged", "warn", "manual"
    ctx.audit.record("self-update", result=result, version=stage.get("version"), phase="loader-app", files=[str(archive)], detail=detail)
    ctx.say(line, tone=tone)
    return {"ok": ok, "enabled": enabled_ok, "version": stage.get("version"), "archive": str(archive), "outcome": outcome}


# --- config ----------------------------------------------------------------------------------------------------------


def config_cmd(ctx: CliContext, args: argparse.Namespace) -> int:
    settings = ctx.settings()
    action = getattr(args, "config_action", None)
    if action == "set":
        try:
            value = settings.set(args.key, args.value)
        except SettingsError as exc:
            raise CliError(str(exc), exit_code=2) from exc
        settings.save()
        ctx.audit.record("config-set", result="ok", key=args.key, value=value, files=[str(settings.path)])
        ctx.set_result(key=args.key, value=value, path=str(settings.path))
        ctx.say(f"{args.key} = {json.dumps(value)} ({settings.path})")
        return 0
    if action == "get" and args.key:
        try:
            value = settings.get(args.key)
        except SettingsError as exc:
            raise CliError(str(exc), exit_code=2) from exc
        ctx.set_result(key=args.key, value=value, default=KNOWN_SETTINGS[args.key][1], path=str(settings.path))
        ctx.say(json.dumps(value))
        return 0
    effective = settings.effective()
    ctx.set_result(settings=effective, path=str(settings.path), known={k: {"type": t.__name__, "default": d, "help": h} for k, (t, d, h) in KNOWN_SETTINGS.items()})
    for key, value in effective.items():
        kind, default, text = KNOWN_SETTINGS[key]
        ctx.say(f"{key} = {json.dumps(value)}" + (f"  {ctx.paint('(default)', 'muted')}" if value == default else "") + f"  — {text}")
    return 0
