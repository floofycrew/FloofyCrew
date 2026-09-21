"""``floofy init`` / ``floofy deinit`` (Requirement 6.1, 7.1, 11.1, 2.3).

``init`` is the one place FloofyCrew asks for the user's consent: it shows the
prominent one-time warning (:data:`floofy_core.consent.WARNING_TEXT`) — on a
terminal as the full-screen ``[ I AGREE ]`` control of
:mod:`floofy_core.cli.consent_screen` (Requirement 15.3; recorded ``screen``),
otherwise printed, requiring the typed phrase ``I ACCEPT`` (``typed``) — or
``--i-accept-the-risk`` for automation, recorded as ``flag`` — before anything is
set up. ``--yes`` never answers this question. Then, each step explicit and reported:

1. the data home layout under ``<host home>/floofy/`` and ``consent.json``;
2. the Loader app: built (from a checkout) or taken from ``--loader-app``, then
   ``kirocrew app install`` through the host launcher (or the manual steps);
3. the host's App Kit execution gate, read for information: when it would refuse
   ``floofycrew`` the per-app ``agent.apps_trusted`` grant is **offered** and
   written only on confirmation (Requirement 2.3, DR-5), then ``kirocrew app enable``;
4. the re-apply trigger(s) of the edition (Requirement 6.1; ``--no-trigger`` skips,
   ``--trigger KIND`` keeps only the named kinds — the install scripts' menu answer);
5. optionally the early shim (``--early``; Requirement 3.2), location chosen by
   the edition adapter (user site on the bundle interpreter, venv site on a venv).

``deinit`` reverses the machine-level pieces (triggers, early shim, Loader app,
``floofy restore --all``) and keeps ``consent.json`` and ``audit.jsonl`` unless
``--purge`` removes the whole data home. Both write audit rows.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from ..consent import ACCEPT_PHRASE, WARNING_TEXT, read_consent, write_consent
from ..governance import LOADER_APP_NAME, warnings_for
from ..hostcli import grant_app_trust, run_host_cli
from ..loaderapp import early_install_module, installed_meta, obtain_build
from ..triggers import loader_startup_status, trigger_managers
from .context import CliContext, CliError

__all__ = ["register", "init", "deinit"]

#: The trigger kinds ``--trigger`` can select (the edition decides which of them it offers; Requirement 6.1, 15.2).
TRIGGER_KINDS = ("user-timer", "path-wrapper")

MANUAL_STEPS = (
    "Install the Loader app by hand:",
    "  1. build it:   python scripts/build_loader_app.py --out /tmp/floofycrew   (or unpack the release archive)",
    "  2. install it: kirocrew app install /tmp/floofycrew",
    "  3. if the host refuses `kirocrew app enable floofycrew` (\"blocked by execution policy\"), re-run `floofy init`",
    "     to record the agent.apps_trusted grant, or add \"floofycrew\" to agent.apps_trusted in the host's config.json yourself.",
)


def register(sub: argparse._SubParsersAction) -> None:
    init_parser = sub.add_parser("init", help="acknowledge the one-time warning, install the Loader app and the re-apply trigger")
    init_parser.add_argument("--i-accept-the-risk", action="store_true", dest="accept_flag", help=f"acknowledge the warning without typing '{ACCEPT_PHRASE}' (automation; recorded as such)")
    init_parser.add_argument("--reaccept", action="store_true", help="show and re-acknowledge the warning even when a current consent record exists")
    init_parser.add_argument("--loader-app", metavar="PATH", default=None, help="a built Loader app directory or archive (default: build from the source checkout)")
    init_parser.add_argument("--no-loader-app", action="store_true", help="skip installing the Loader app")
    init_parser.add_argument("--reinstall-loader", action="store_true", help="uninstall and reinstall the Loader app even when it is already installed")
    init_parser.add_argument("--no-grant", action="store_true", help="never offer the agent.apps_trusted grant")
    init_parser.add_argument("--no-trigger", action="store_true", help="skip installing the re-apply trigger(s)")
    init_parser.add_argument("--trigger", action="append", default=None, choices=TRIGGER_KINDS, metavar="KIND", help=f"install only this trigger kind (repeatable; one of {', '.join(TRIGGER_KINDS)}; default: every trigger the edition offers)")
    init_parser.add_argument("--early", action="store_true", help="also install the early shim (.pth) for hooks that run before platform bootstrap")
    init_parser.set_defaults(handler=init)

    deinit_parser = sub.add_parser("deinit", help="remove the trigger(s), the early shim and the Loader app; restore every payload to vanilla")
    deinit_parser.add_argument("--purge", action="store_true", help="also delete the whole data home (mods, consent record, audit log)")
    deinit_parser.add_argument("--keep-loader-app", action="store_true", help="leave the Loader app installed")
    deinit_parser.set_defaults(handler=deinit)


# --- init -------------------------------------------------------------------------------------


def _consent_step(ctx: CliContext, args: argparse.Namespace) -> dict[str, Any]:
    current = read_consent(ctx.home.consent)
    if current.ok and not args.reaccept:
        ctx.say(f"consent: already recorded on {current.acknowledged_at} by {current.by or '?'} (warning v{current.warning_version}); --reaccept to see the warning again")
        return {**current.to_dict(), "status": "existing"}
    how: str | None = None
    if not args.accept_flag:
        # Requirement 15.3: on a terminal, the full-screen [ I AGREE ] control; None means "not available here".
        # A host control (the interactive floofy's own frame, the manager App's modal) answers the same way and
        # says what it is through Console.consent_how (screen | app) — the record differs in that one field.
        accepted = ctx.console.ask_consent_screen()
        if accepted is True:
            how = ctx.console.consent_how if ctx.console.consent_fn is not None else "screen"
            ctx.say(f"consent: acknowledged on the {'full-screen control' if how == 'screen' else how + ' control'} (recorded as such)")
        elif accepted is False:
            raise CliError("consent not given: declined on the full-screen control (Esc/q). Run `floofy init` again to reconsider, or pass --i-accept-the-risk for automation (--yes never accepts the warning)", exit_code=3)
    if how is None:
        ctx.say("")
        ctx.say("=" * 72, tone="danger")
        ctx.say("  READ THIS ONCE — FloofyCrew consent", tone="heading")
        ctx.say("=" * 72, tone="danger")
        for line in WARNING_TEXT.rstrip().splitlines():
            ctx.say("  " + line)
        ctx.say("=" * 72, tone="danger")
        if args.accept_flag:
            how = "flag"
            ctx.say("consent: acknowledged with --i-accept-the-risk (recorded as such)")
        else:
            prompt = f"Type {ACCEPT_PHRASE} to acknowledge (anything else aborts): "
            if not ctx.console.typed(prompt, ACCEPT_PHRASE, what="consent"):
                raise CliError(f"consent not given: type {ACCEPT_PHRASE} at the prompt, or pass --i-accept-the-risk for automation (--yes never accepts the warning)", exit_code=3)
            how = "typed"
    record = write_consent(ctx.home.consent, how=how)
    ctx.audit.record("consent", result="ok", detail=f"warning v{record['warningVersion']} acknowledged ({how})", how=how)
    ctx.say(f"consent: recorded in {ctx.home.consent}")
    return {**record, "status": "recorded"}


def _loader_app_step(ctx: CliContext, args: argparse.Namespace) -> dict[str, Any]:
    outcome: dict[str, Any] = {"skipped": False}
    meta = installed_meta(ctx.host_home)
    outcome["before"] = meta
    if args.no_loader_app:
        outcome["skipped"] = True
        ctx.say("loader app: skipped (--no-loader-app)")
        return outcome
    launcher = ctx.launcher()
    outcome["launcher"] = str(launcher) if launcher else None
    if meta["installed"] and not args.reinstall_loader:
        ctx.say(f"loader app: already installed (v{meta['version'] or '?'}, enabled={meta['enabled']}) at {meta['appDir']}; --reinstall-loader to replace it")
    else:
        build, how, scratch = obtain_build(args.loader_app)
        outcome["build"] = str(build) if build else None
        outcome["buildSource"] = how
        try:
            if build is None or launcher is None:
                why = how if build is None else "no kirocrew launcher found (PATH or --kirocrew)"
                ctx.warn(f"loader app: cannot install automatically — {why}")
                ctx.console.lines(MANUAL_STEPS)
                outcome["manual"] = True
                return outcome
            granted_before = LOADER_APP_NAME in ctx.governance().apps_trusted
            if meta["installed"]:
                removed = run_host_cli(launcher, ["app", "uninstall", LOADER_APP_NAME], host_home=ctx.host_home, extra_env=ctx.host_cli_env())
                outcome["uninstall"] = removed.to_dict()
                ctx.say(f"loader app: uninstalled the previous copy ({'ok' if removed.ok else removed.output[-200:]})")
                # `kirocrew app uninstall` also drops the app from agent.apps_trusted (seen on a live 0.7.0.5:
                # every reinstall came back ungranted, and the self-update path, which never offers the grant,
                # left the new Loader installed but disabled). Restoring a grant the user already gave is not a
                # new decision, so it is re-recorded without asking and audited as a restore.
                ctx._governance = None  # noqa: SLF001 - re-read after the host CLI edited config.json
                if granted_before and LOADER_APP_NAME not in ctx.governance().apps_trusted:
                    restored = grant_app_trust(ctx.host_home / "config.json", LOADER_APP_NAME)
                    outcome["grantRestored"] = restored.to_dict()
                    ctx.audit.record("grant-apps-trusted", result="restored" if restored.written else "unchanged", files=[str(restored.path)], detail=f"re-recorded after `kirocrew app uninstall` dropped it: {restored.detail}", governanceFlags=["ThirdPartyAppsDisabled"])
                    ctx.say(f"grant: restored — `kirocrew app uninstall` had dropped 'floofycrew' from agent.apps_trusted; {restored.detail}")
                    ctx._governance = None  # noqa: SLF001
            installed = run_host_cli(launcher, ["app", "install", str(build)], host_home=ctx.host_home, extra_env=ctx.host_cli_env())
            outcome["install"] = installed.to_dict()
            if not installed.ok:
                ctx.warn(f"loader app: `kirocrew app install` failed: {installed.output[-400:]}")
                ctx.console.lines(MANUAL_STEPS)
                outcome["manual"] = True
                return outcome
        finally:
            if scratch is not None:
                shutil.rmtree(scratch, ignore_errors=True)
        ctx.say(f"loader app: installed from {how}")
        ctx.audit.record("loader-app-install", result="ok", files=[str(build)], detail=how)
    outcome.update(_grant_and_enable(ctx, args, launcher))
    outcome["after"] = installed_meta(ctx.host_home)
    return outcome


def _grant_and_enable(ctx: CliContext, args: argparse.Namespace, launcher: Path | None) -> dict[str, Any]:
    """Requirement 2.3: show the host's verdict, offer the operator-writable grant, then enable."""
    outcome: dict[str, Any] = {}
    snapshot = ctx.governance()
    allowed = snapshot.app_execution_allowed(LOADER_APP_NAME)
    # ``None`` = no config says anything; the host's default refuses third-party app code (execution.py L623–L686)
    outcome["hostVerdict"] = {"appExecutionAllowed": bool(allowed), "configKnown": allowed is not None, "appsAllowThirdParty": snapshot.apps_allow_third_party, "appsTrusted": list(snapshot.apps_trusted)}
    for warning in warnings_for(snapshot, app_targets=[LOADER_APP_NAME]):
        ctx.warn(warning.format())
    if allowed is not True:
        ctx.say(f"host verdict: the App Kit execution gate would refuse to run '{LOADER_APP_NAME}' (agent.apps_allow_third_party is off and it is not in agent.apps_trusted).")
        if args.no_grant:
            ctx.say("grant: not offered (--no-grant); the Loader stays installed but disabled until you grant it")
            outcome["grant"] = "not-offered"
        elif ctx.console.confirm(f"Record the per-app grant agent.apps_trusted += [\"{LOADER_APP_NAME}\"] in {ctx.host_home / 'config.json'}?", default=False):
            edit = grant_app_trust(ctx.host_home / "config.json", LOADER_APP_NAME)
            outcome["grant"] = edit.to_dict()
            ctx.audit.record("grant-apps-trusted", result="ok" if edit.written else "unchanged", files=[str(edit.path)], detail=edit.detail, governanceFlags=["ThirdPartyAppsDisabled"])
            ctx.say(f"grant: {edit.detail}")
            ctx._governance = None  # re-read below
        else:
            ctx.say("grant: declined; the Loader stays installed but disabled until you grant it (`floofy init` again, or edit agent.apps_trusted)")
            outcome["grant"] = "declined"
            ctx.audit.record("grant-apps-trusted", result="declined", detail="user declined the agent.apps_trusted grant")
    if launcher is None:
        outcome["enable"] = None
        return outcome
    enabled = run_host_cli(launcher, ["app", "enable", LOADER_APP_NAME], host_home=ctx.host_home, extra_env=ctx.host_cli_env())
    outcome["enable"] = enabled.to_dict()
    if enabled.ok:
        ctx.say(f"loader app: enabled ({LOADER_APP_NAME})")
    else:
        ctx.warn(f"loader app: `kirocrew app enable {LOADER_APP_NAME}` refused: {enabled.output[-300:]}")
        ctx.say("  This is the host's verdict, shown as a warning; FloofyCrew does not override it by force. Grant the app (agent.apps_trusted) and run `floofy init` again.")
    return outcome


def _trigger_step(ctx: CliContext, args: argparse.Namespace) -> dict[str, Any]:
    outcome: dict[str, Any] = {"installed": [], "loaderStartup": loader_startup_status(ctx.host_home).to_dict()}
    if args.no_trigger:
        outcome["skipped"] = True
        ctx.say("re-apply trigger: skipped (--no-trigger); patches will not survive a host update until you run `floofy apply`")
        return outcome
    managers = trigger_managers(ctx.host_home, ctx.adapters(), edition=ctx.edition(), payload=ctx.current_payload())
    wanted = list(getattr(args, "trigger", None) or [])
    if wanted:
        # the install scripts' menu answer (Requirement 15.2): only the named kinds; the rest of the edition's set stays uninstalled
        offered = {m.kind for m in managers}
        for kind in wanted:
            if kind not in offered:
                ctx.warn(f"re-apply trigger [{kind}]: this edition offers no such trigger here (offered: {', '.join(sorted(offered)) or 'none'})")
        managers = [m for m in managers if m.kind in wanted]
        outcome["selected"] = wanted
    if not managers:
        ctx.warn("re-apply trigger: no edition adapter offers a trigger for this machine; the Loader's on_startup re-apply is the only trigger")
        outcome["none"] = True
        return outcome
    for manager in managers:
        report = manager.install()
        outcome["installed"].append(report.to_dict())
        ctx.say(f"re-apply trigger [{manager.kind}]: {report.detail}")
        ctx.audit.record("trigger-install", result="ok" if report.ok else "error", files=report.paths, detail=f"{manager.kind}: {report.detail}")
    return outcome


def _early_step(ctx: CliContext, args: argparse.Namespace) -> dict[str, Any]:
    if not args.early:
        return {"skipped": True}
    module = early_install_module(ctx.host_home)
    if module is None:
        ctx.warn("early shim: the Loader app is not installed (its floofy_early package is the shim source); run `floofy init` without --early first")
        return {"skipped": True, "reason": "loader app missing"}
    payload = ctx.current_payload()
    if payload is None or payload.interpreter is None:
        ctx.warn("early shim: no payload interpreter found to install into")
        return {"skipped": True, "reason": "no payload interpreter"}
    adapter = ctx.adapter_for(payload.edition)
    kind = "user-site"
    chooser = getattr(adapter, "early_shim_kind", None)
    if callable(chooser):
        try:
            kind = str(chooser(payload) or kind)
        except Exception:  # noqa: BLE001
            kind = "user-site"
    elif (payload.root / "pyvenv.cfg").is_file():
        kind = "venv-site"
    installer = module.install_venv_site if kind == "venv-site" else module.install_user_site
    try:
        status = installer(payload.interpreter)
    except Exception as exc:  # noqa: BLE001 - reported, never fatal for init
        ctx.warn(f"early shim: install failed: {exc}")
        return {"skipped": False, "ok": False, "error": str(exc), "kind": kind}
    ctx.say(f"early shim [{kind}]: installed into {status.site_dir} for {payload.interpreter}")
    ctx.audit.record("early-shim-install", result="ok", files=[status.site_dir or ""], detail=f"{kind} for {payload.interpreter}")
    return {"skipped": False, "ok": True, **status.to_dict()}


def init(ctx: CliContext, args: argparse.Namespace) -> int:
    ctx.say(f"{ctx.paint('floofy init', 'heading')} — {ctx.paint('FloofyCrew is unofficial and not affiliated with Kiro or KiroCrew.', 'italic')}")
    consent = _consent_step(ctx, args)
    ctx.home.ensure()
    ctx.home.profiles.mkdir(parents=True, exist_ok=True)
    if not ctx.home.registries.is_file():
        ctx.home.registries.write_text(json.dumps({"schema": 1, "sources": []}, indent=2) + "\n", encoding="utf-8")
    ctx.say(f"data home: {ctx.data_home} (edition: {ctx.edition()}, payloads found: {len(ctx.payloads())})")
    loader = _loader_app_step(ctx, args)
    triggers = _trigger_step(ctx, args)
    early = _early_step(ctx, args)
    ctx.audit.record("init", result="ok", detail=f"edition={ctx.edition()} loader={'installed' if installed_meta(ctx.host_home)['installed'] else 'missing'}")
    ctx.set_result(consent=consent, dataHome=str(ctx.data_home), edition=ctx.edition(), loaderApp=loader, triggers=triggers, earlyShim=early)
    ctx.say("done. Next: `floofy install <mod>` — `floofy doctor` shows the state of everything above.")
    return 0


# --- deinit -----------------------------------------------------------------------------------


def deinit(ctx: CliContext, args: argparse.Namespace) -> int:
    outcome: dict[str, Any] = {"triggers": [], "earlyShim": [], "loaderApp": None, "restore": None, "purged": False}
    if not ctx.console.confirm(f"Remove FloofyCrew's triggers, early shim and Loader app for {ctx.host_home} and restore every payload to vanilla?", default=False):
        raise CliError("deinit cancelled", exit_code=1)
    for manager in trigger_managers(ctx.host_home, ctx.adapters(), edition=ctx.edition(), payload=ctx.current_payload()):
        report = manager.uninstall()
        outcome["triggers"].append(report.to_dict())
        ctx.say(f"re-apply trigger [{manager.kind}]: {report.detail}")
    module = early_install_module(ctx.host_home)
    payload = ctx.current_payload()
    if module is not None and payload is not None and payload.interpreter is not None:
        for name in ("uninstall_user_site", "uninstall_venv_site"):
            try:
                removed = getattr(module, name)(payload.interpreter)
            except Exception as exc:  # noqa: BLE001
                removed = [f"error: {exc}"]
            outcome["earlyShim"].append({name: removed})
            if removed:
                ctx.say(f"early shim: {name}: {removed}")
    # payloads back to vanilla first (the Loader app's ui/patched copies are swept by the Patcher)
    from ..patcher import Patcher  # noqa: PLC0415

    payloads = ctx.payloads()
    if payloads:
        report = Patcher(ctx.data_home, payloads, host_home=ctx.host_home).restore(None, sweep=True)
        outcome["restore"] = report.to_dict()
        ctx.say(f"restore: {sum(len(v['restored']) for v in report.payloads.values())} file(s) restored, {sum(len(v['removed']) for v in report.payloads.values())} removed")
    else:
        ctx.say("restore: no payload found (nothing to restore)")
    if not args.keep_loader_app and installed_meta(ctx.host_home)["installed"]:
        launcher = ctx.launcher()
        if launcher is None:
            ctx.warn("loader app: no kirocrew launcher found; run `kirocrew app uninstall floofycrew` yourself")
        else:
            removed = run_host_cli(launcher, ["app", "uninstall", LOADER_APP_NAME], host_home=ctx.host_home, extra_env=ctx.host_cli_env())
            outcome["loaderApp"] = removed.to_dict()
            ctx.say(f"loader app: {'uninstalled' if removed.ok else 'uninstall failed: ' + removed.output[-200:]}")
    ctx.audit.record("deinit", result="ok", detail="purge" if args.purge else "kept consent and audit")
    if args.purge:
        shutil.rmtree(ctx.data_home, ignore_errors=True)
        outcome["purged"] = True
        ctx.say(f"purged {ctx.data_home}")
    else:
        ctx.say(f"kept {ctx.home.consent.name} and {ctx.home.audit.name} in {ctx.data_home} (--purge removes everything)")
    ctx.set_result(**outcome)
    return 0
