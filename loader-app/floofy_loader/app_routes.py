"""The manager App's route surface (Requirement 16.2, 16.3, 16.5, 8.8, 7.6) — one typed route per CLI action.

Every route below is mounted with the Loader's other routes under
``/api/apps/floofycrew/`` (the design writes it ``/apps/floofycrew/api/*``) and
does exactly one thing: it spells the ``floofy`` sub-command the request stands
for and runs it in-process through :func:`floofy_core.cli.main.execute` with
``actor="app"`` — the **same parser, handler and audit row** as the CLI and the
interactive ``floofy`` (``actor: cli`` / ``tui``), so there is one
implementation of every action (the owner's rule for every surface). The route
never invents flags: what the request may set is the typed body below, and the
automation switches that answer a question by themselves (``--yes``,
``--i-accept-the-risk``, ``--accept-unlisted-source``, ``--accept-flags``,
``--confirm-governance-target``) are not reachable from here.

| Method | Path | Body | Runs |
|---|---|---|---|
| POST | ``/mods/install`` | ``{source, now?, enable?, ref?}`` | ``install <source> [--now] [--enable] [--ref REF]`` |
| POST | ``/mods/update-check`` | | ``update --all --check`` |
| POST | ``/mods/update-all`` | ``{now?}`` | ``update --all [--now]`` |
| POST | ``/mods/{id}/enable`` · ``/disable`` | ``{reload?=true}`` | ``enable|disable <id> [--no-reload]`` |
| POST | ``/mods/{id}/uninstall`` | ``{now?, keepConfig?}`` | ``uninstall <id> [--now] [--keep-config]`` |
| POST | ``/mods/{id}/update`` | ``{now?}`` | ``update <id> [--now]`` |
| POST | ``/mods/{id}/yeet`` | ``{reason?}`` | ``yeet <id> --reason REASON`` |
| GET | ``/mods/{id}/info`` | | ``info <id>`` |
| POST | ``/yeet`` | ``{ids[], reason?}`` | ``yeet <ids…> --reason REASON`` |
| POST | ``/restore`` | ``{version}`` | ``yeet --restore <version>`` |
| GET | ``/quarantine`` | | ``yeet --list`` |
| GET | ``/status`` · POST/GET ``/doctor`` | | ``status`` · ``doctor`` |
| GET | ``/search?q=`` | | ``search <q>`` |
| GET | ``/audit?tail=N&op=OP…`` | | ``audit [--tail N] [--op OP]…`` |
| GET | ``/registries`` | | ``registry list`` |
| POST | ``/registries`` | ``{url, trust?, allowUnsigned?, name?, keyId?, publicKey?, refresh?=true}`` | ``registry add <url> --trust T [--allow-unsigned] [--name N] [--key-id K] [--public-key P] [--no-refresh]`` |
| DELETE | ``/registries/{key}`` | | ``registry remove <key>`` |
| POST | ``/registries/refresh`` · ``/registries/{key}/refresh`` | | ``registry refresh [<key>]`` |
| POST | ``/registries/{key}/trust`` | ``{trust, allowUnsigned?}`` | ``registry add <url of key> --trust T [--allow-unsigned] --no-refresh`` |
| POST | ``/registries/defaults`` | ``{refresh?=true, hostRegistry?=true}`` | ``registry defaults [--no-refresh] [--no-host-registry]`` |
| GET | ``/profiles`` | | ``profile list`` |
| POST | ``/profiles`` | ``{name}`` | ``profile save <name>`` |
| POST | ``/profiles/{name}/use`` | ``{now?, check?}`` | ``profile use <name> [--now] [--check]`` |
| POST | ``/profiles/{name}/export`` | ``{file}`` | ``profile export <name> <file>`` |
| POST | ``/profiles/import`` | ``{file, name?}`` | ``profile import <file> [--name N]`` |
| POST | ``/vanilla`` | ``{cancel?}`` | ``vanilla [--cancel]`` |
| POST | ``/self-update`` | ``{now?}`` | ``self-update [--now]`` — the "Update FloofyCrew" button; the CLI's own "Install FloofyCrew X over Y?" question arrives as the ``yes-no`` 409 (the App never runs ``--force``, ``--check`` or ``--target``) |
| POST | ``/consent`` | ``{agree: true, reaccept?}`` (or ``confirmations.consent: true``) | ``init --no-loader-app --no-trigger [--reaccept]`` with the agreement as the ``[ I AGREE ]`` answer (``how: "app"``) |

**Confirmation protocol** (Requirement 16.3; design "Manager App"). A request may
carry ``confirmations``::

    {"consent": true,                      # the one-time warning was agreed to on the [ I AGREE ] modal
     "yes": true | ["<exact prompt>", …],  # ordinary yes/no questions (all of this request's, or the listed prompts)
     "governanceTargets": ["<typed path>", …],   # typed, compared byte for byte with each governance-altering target
     "unlistedSource": "I ACCEPT",          # the unlisted-source line of a git reference
     "unsignedIndex": true}                 # --allow-unsigned on a registry source (the loosening warning)

The CLI's confirmation hooks (:class:`floofy_core.cli.console.Console`
``confirm_fn`` / ``consent_fn`` / ``typed_fn``) are bound to that object. When a
handler asks a question the object does not answer, the hook raises
:class:`floofy_core.cli.console.ConfirmationNeeded`; the command unwinds
**before it mutates anything** (every handler asks first — install after
validate + disclose, before ``place``) and the route answers::

    409 {"ok": false, "confirmation": {"kind": "consent" | "governance-target" | "unlisted-source" | "unsigned-index" | "yes-no",
                                        "text": "<the prompt or warning as the CLI prints it>",
                                        "expects": "agree" | "typed-path" | "accept" | "confirm",
                                        "targets": ["<governance path>", …], "detail": "missing" | "mismatch"},
         "disclosure": {…the install disclosure when one was shown…}, "transcript": [...], "command": "install", "argv": [...]}

The client presents it — the consent modal with the same text and an
``[ I AGREE ]`` control, a **text field** per governance target (never a
button; the server re-compares the typed path exactly), the network-host and
unlisted-source disclosures, the loosening warning — and re-posts with the
answers. A typed path that does not equal the target byte for byte is a 409
again (``detail: mismatch``), and nothing was changed in between. The one-time
consent outranks everything: a mutating command on a home without a consent
record is a 409 of kind ``consent`` (the CLI's exit 3); ``confirmations.consent``
(or ``POST /consent``) records it through the same ``init`` step the CLI runs,
with ``how: "app"``, before the command proceeds.

Staging (Requirement 7.6, 16.5): ``install`` / ``update`` / ``profile use`` land in
``pending/`` while the gateway runs unless ``now: true``, which places the mod
and reloads the Loader in-process (the same ``--now`` as the CLI); ``POST
/reload`` is the "apply now" for what is already staged. ``enable``/``disable``
reload by default, exactly like the CLI (``reload: false`` = ``--no-reload``).

The pure :func:`dispatch` returns ``(status, headers, body)`` so the contract
tests run without aiohttp; :func:`build_app_routes` adapts it for the host and
runs the command off the event loop.
"""
from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from floofy_core.cli.console import EXIT_CONFIRMATION_NEEDED, ConfirmationNeeded, Console
from floofy_core.cli.style import Style
from floofy_core.consent import ACCEPT_PHRASE, WARNING_TEXT, read_consent
from floofy_core.gitsource import UNLISTED_SOURCE_LINE

__all__ = ["APP_ACTOR", "AppRouteSpec", "ROUTES", "RouteError", "app_console", "build_app_routes", "dispatch", "route_table"]

#: The audit ``actor`` of every mutation the App performs.
APP_ACTOR = "app"
#: Exit code of a handler that refused because no consent record exists (``CliContext.require_consent``, ``init`` declined).
EXIT_CONSENT_REQUIRED = 3

_MOD_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_PROFILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SOURCE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HOSTVER = re.compile(r"^[0-9][0-9A-Za-z.+-]{0,63}$")
_TRUST = ("index", "owner")

Responder = tuple[int, dict[str, str], bytes]
ArgvBuilder = Callable[[dict[str, str], dict[str, Any], dict[str, list[str]]], list[str]]


class RouteError(Exception):
    """A malformed request: ``status`` and the message the client gets."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _json(status: int, payload: Any) -> Responder:
    body = json.dumps(payload, default=str).encode("utf-8")
    return status, {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}, body


# --- body readers -----------------------------------------------------------------------------------------


def _flag(body: dict[str, Any], key: str, default: bool = False) -> bool:
    value = body.get(key, default)
    if not isinstance(value, bool):
        raise RouteError(f"{key} must be a boolean")
    return value


def _text(body: dict[str, Any], key: str, *, required: bool = False, pattern: re.Pattern[str] | None = None, what: str | None = None) -> str | None:
    value = body.get(key)
    if value is None or value == "":
        if required:
            raise RouteError(f"{key} is required")
        return None
    if not isinstance(value, str) or value.startswith("-") or "\x00" in value or "\n" in value:
        raise RouteError(f"{key} must be a string that does not start with '-'")
    if pattern is not None and not pattern.match(value):
        raise RouteError(f"{key} is not a valid {what or key}")
    return value


def _param(params: dict[str, str], key: str, pattern: re.Pattern[str], what: str) -> str:
    value = params.get(key, "")
    if not pattern.match(value):
        raise RouteError(f"{what} {value!r} is not valid", 404)
    return value


def _mod_id(params: dict[str, str]) -> str:
    return _param(params, "id", _MOD_ID, "mod id")


# --- argv builders ----------------------------------------------------------------------------------------


def _install(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
    source = _text(body, "source", required=True)
    argv = ["install", str(source)]
    if _flag(body, "now"):
        argv.append("--now")
    if _flag(body, "enable"):
        argv.append("--enable")
    ref = _text(body, "ref")
    if ref:
        argv += ["--ref", ref]
    return argv


def _flip(command: str) -> ArgvBuilder:
    def build(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
        argv = [command, _mod_id(params)]
        if not _flag(body, "reload", True):
            argv.append("--no-reload")
        return argv

    return build


def _uninstall(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
    argv = ["uninstall", _mod_id(params)]
    if _flag(body, "now"):
        argv.append("--now")
    if _flag(body, "keepConfig"):
        argv.append("--keep-config")
    return argv


def _update_one(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
    argv = ["update", _mod_id(params)]
    if _flag(body, "now"):
        argv.append("--now")
    return argv


def _update_all(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
    argv = ["update", "--all"]
    if _flag(body, "now"):
        argv.append("--now")
    return argv


def _yeet_one(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
    return ["yeet", _mod_id(params), "--reason", _text(body, "reason") or "manual yeet (app)"]


def _yeet_many(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and _MOD_ID.match(i) for i in ids):
        raise RouteError("ids must be a non-empty list of mod ids")
    return ["yeet", *ids, "--reason", _text(body, "reason") or "manual yeet (app)"]


def _restore(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
    return ["yeet", "--restore", str(_text(body, "version", required=True, pattern=_HOSTVER, what="host version"))]


def _search(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
    q = " ".join(query.get("q", [])).strip()
    if q.startswith("-"):
        raise RouteError("q must not start with '-'")
    return ["search", q] if q else ["search"]


def _audit(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
    argv = ["audit"]
    tail = (query.get("tail") or [""])[0]
    if tail:
        if not tail.isdigit():
            raise RouteError("tail must be a non-negative integer")
        argv += ["--tail", tail]
    for op in query.get("op", []):
        if not re.match(r"^[a-z][a-z0-9-]{0,63}$", op):
            raise RouteError(f"op {op!r} is not an operation name")
        argv += ["--op", op]
    return argv


def _registry_add(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
    url = str(_text(body, "url", required=True))
    trust = _text(body, "trust") or "index"
    if trust not in _TRUST:
        raise RouteError(f"trust must be one of {', '.join(_TRUST)}")
    argv = ["registry", "add", url, "--trust", trust]
    if _flag(body, "allowUnsigned"):
        argv.append("--allow-unsigned")
    for key, option in (("name", "--name"), ("keyId", "--key-id"), ("publicKey", "--public-key")):
        value = _text(body, key)
        if value:
            argv += [option, value]
    if not _flag(body, "refresh", True):
        argv.append("--no-refresh")
    return argv


def _registry_key(params: dict[str, str]) -> str:
    return _param(params, "key", _SOURCE_KEY, "registry source key")


def _registry_remove(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
    return ["registry", "remove", _registry_key(params)]


def _registry_refresh_one(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
    return ["registry", "refresh", _registry_key(params)]


def _registry_trust(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
    # the trust change re-adds the source by URL with the new settings, exactly what the interactive floofy runs;
    # the URL is looked up from the key at dispatch time (see ``_resolve_source_url``)
    trust = _text(body, "trust") or "index"
    if trust not in _TRUST:
        raise RouteError(f"trust must be one of {', '.join(_TRUST)}")
    argv = ["registry", "add", f"@key:{_registry_key(params)}", "--trust", trust]
    if _flag(body, "allowUnsigned"):
        argv.append("--allow-unsigned")
    argv.append("--no-refresh")
    return argv


def _registry_defaults(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
    argv = ["registry", "defaults"]
    if not _flag(body, "refresh", True):
        argv.append("--no-refresh")
    if not _flag(body, "hostRegistry", True):
        argv.append("--no-host-registry")
    return argv


def _profile_name(params: dict[str, str]) -> str:
    return _param(params, "name", _PROFILE, "profile name")


def _profile_save(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
    return ["profile", "save", str(_text(body, "name", required=True, pattern=_PROFILE, what="profile name"))]


def _profile_use(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
    argv = ["profile", "use", _profile_name(params)]
    if _flag(body, "now"):
        argv.append("--now")
    if _flag(body, "check"):
        argv.append("--check")
    return argv


def _profile_export(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
    return ["profile", "export", _profile_name(params), str(_text(body, "file", required=True))]


def _profile_import(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
    argv = ["profile", "import", str(_text(body, "file", required=True))]
    name = _text(body, "name", pattern=_PROFILE, what="profile name")
    if name:
        argv += ["--name", name]
    return argv


def _vanilla(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
    return ["vanilla", "--cancel"] if _flag(body, "cancel") else ["vanilla"]


def _self_update(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
    # `--now` installs the staged Loader app through the host App Kit while this gateway runs (it takes effect at
    # the next start — the App's "Update & restart" follows it with POST /host/restart); without it the zipapp is
    # swapped and the Loader app archive stays staged for `floofy apply`, exactly like the CLI.
    argv = ["self-update"]
    if _flag(body, "now"):
        argv.append("--now")
    return argv


def _consent(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
    argv = ["init", "--no-loader-app", "--no-trigger"]
    if _flag(body, "reaccept"):
        argv.append("--reaccept")
    return argv


def _static(*argv: str) -> ArgvBuilder:
    return lambda params, body, query: list(argv)


def _info(params: dict[str, str], body: dict[str, Any], query: dict[str, list[str]]) -> list[str]:
    return ["info", _mod_id(params)]


# --- the table -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AppRouteSpec:
    """One route: the method and path the host sees, the ``floofy`` argv it stands for, and how it behaves."""

    method: str
    path: str
    name: str
    argv: ArgvBuilder
    #: The command may change the machine (it writes an audit row); read-only routes never do.
    mutating: bool = False
    #: A ``--allow-unsigned`` request needs ``confirmations.unsignedIndex`` (the loosening warning) before it runs.
    loosens_trust: bool = False

    @property
    def is_consent(self) -> bool:
        return self.name == "consent"


ROUTES: tuple[AppRouteSpec, ...] = (
    AppRouteSpec("POST", "/mods/install", "mods.install", _install, mutating=True),
    AppRouteSpec("POST", "/mods/update-check", "updates.check", _static("update", "--all", "--check")),
    AppRouteSpec("POST", "/mods/update-all", "updates.apply", _update_all, mutating=True),
    AppRouteSpec("POST", "/mods/{id}/enable", "mods.enable", _flip("enable"), mutating=True),
    AppRouteSpec("POST", "/mods/{id}/disable", "mods.disable", _flip("disable"), mutating=True),
    AppRouteSpec("POST", "/mods/{id}/uninstall", "mods.uninstall", _uninstall, mutating=True),
    AppRouteSpec("POST", "/mods/{id}/update", "mods.update", _update_one, mutating=True),
    AppRouteSpec("POST", "/mods/{id}/yeet", "mods.yeet", _yeet_one, mutating=True),
    AppRouteSpec("GET", "/mods/{id}/info", "mods.info", _info),
    AppRouteSpec("POST", "/yeet", "mods.yeet_many", _yeet_many, mutating=True),
    AppRouteSpec("POST", "/restore", "mods.yeet_restore", _restore, mutating=True),
    AppRouteSpec("GET", "/quarantine", "mods.yeet_list", _static("yeet", "--list")),
    AppRouteSpec("GET", "/status", "status", _static("status")),
    AppRouteSpec("POST", "/doctor", "doctor", _static("doctor")),
    AppRouteSpec("GET", "/doctor", "doctor", _static("doctor")),
    AppRouteSpec("GET", "/search", "mods.search", _search),
    AppRouteSpec("GET", "/audit", "audit", _audit),
    AppRouteSpec("GET", "/registries", "registries.list", _static("registry", "list")),
    AppRouteSpec("POST", "/registries", "registries.add", _registry_add, mutating=True, loosens_trust=True),
    AppRouteSpec("POST", "/registries/refresh", "registries.refresh", _static("registry", "refresh"), mutating=True),
    AppRouteSpec("POST", "/registries/defaults", "registries.defaults", _registry_defaults, mutating=True),
    AppRouteSpec("DELETE", "/registries/{key}", "registries.remove", _registry_remove, mutating=True),
    AppRouteSpec("POST", "/registries/{key}/refresh", "registries.refresh_one", _registry_refresh_one, mutating=True),
    AppRouteSpec("POST", "/registries/{key}/trust", "registries.trust", _registry_trust, mutating=True, loosens_trust=True),
    AppRouteSpec("GET", "/profiles", "profiles.list", _static("profile", "list")),
    AppRouteSpec("POST", "/profiles", "profiles.save", _profile_save, mutating=True),
    AppRouteSpec("POST", "/profiles/import", "profiles.import", _profile_import, mutating=True),
    AppRouteSpec("POST", "/profiles/{name}/use", "profiles.use", _profile_use, mutating=True),
    AppRouteSpec("POST", "/profiles/{name}/export", "profiles.export", _profile_export),
    AppRouteSpec("POST", "/vanilla", "vanilla", _vanilla, mutating=True),
    AppRouteSpec("POST", "/self-update", "selfupdate", _self_update, mutating=True),
    AppRouteSpec("POST", "/consent", "consent", _consent, mutating=True),
)

_BY_NAME: dict[str, AppRouteSpec] = {}
for _spec in ROUTES:
    _BY_NAME.setdefault(_spec.name, _spec)


def spec(name: str) -> AppRouteSpec:
    return _BY_NAME[name]


def route_table() -> tuple[tuple[str, str], ...]:
    return tuple((s.method, s.path) for s in ROUTES)


# --- the App's console: the CLI's hooks bound to the request's confirmations ------------------------------------


def _wanted(confirmations: dict[str, Any], key: str) -> Any:
    return confirmations.get(key) if isinstance(confirmations, dict) else None


def app_console(confirmations: dict[str, Any] | None) -> Console:
    """A captured, plain console whose confirmation hooks answer from ``confirmations`` or raise :class:`ConfirmationNeeded`.

    Nothing is answered by default: an ordinary yes/no needs ``yes`` (``true``
    for every yes/no question of the request, or a list of the exact prompts),
    the one-time consent needs ``consent: true``, a governance-altering target
    needs its exact path in ``governanceTargets`` (compared with ``==``, no
    trimming — what the user typed is what the server checks), the unlisted-source
    line needs ``unlistedSource == "I ACCEPT"``. ``assume_yes`` is never set: the
    App has no ``--yes``.
    """
    answers = confirmations if isinstance(confirmations, dict) else {}

    def confirm_fn(prompt: str, default: bool) -> bool:
        yes = _wanted(answers, "yes")
        if yes is True or (isinstance(yes, list) and prompt in yes):
            return True
        raise ConfirmationNeeded("yes-no", prompt, detail="missing" if not yes else "prompt-not-listed")

    def consent_fn() -> bool:
        if _wanted(answers, "consent") is True:
            return True
        raise ConfirmationNeeded("consent", WARNING_TEXT, detail="missing")

    def typed_fn(prompt: str, what: str) -> str | None:
        if what == "unlisted source":
            given = _wanted(answers, "unlistedSource")
            if given == ACCEPT_PHRASE:
                return ACCEPT_PHRASE
            raise ConfirmationNeeded("unlisted-source", prompt, detail=("missing" if given in (None, "") else "mismatch") + f"; the line: {UNLISTED_SOURCE_LINE}")
        if what.startswith("governance target "):
            expected = what[len("governance target "):]
            typed = _wanted(answers, "governanceTargets")
            typed_list = [t for t in typed if isinstance(t, str)] if isinstance(typed, list) else []
            if any(t == expected for t in typed_list):  # byte-equal, never trimmed or normalised
                return expected
            raise ConfirmationNeeded("governance-target", prompt, targets=[expected], detail="mismatch" if typed_list else "missing")
        if what == "consent":
            return consent_fn() and ACCEPT_PHRASE
        raise ConfirmationNeeded("yes-no", prompt, detail=f"typed answer for {what!r} not available to the App")

    return Console(out=io.StringIO(), err=io.StringIO(), non_interactive=False, assume_yes=False, style=Style.off(), consent_fn=consent_fn, confirm_fn=confirm_fn, typed_fn=typed_fn, consent_how=APP_ACTOR)


# --- dispatch --------------------------------------------------------------------------------------------


def _resolve_source_url(runtime: Any, argv: list[str]) -> list[str]:
    """``@key:<key>`` placeholders (the trust route) become the source's URL; an unknown key is a 404."""
    out: list[str] = []
    for part in argv:
        if part.startswith("@key:"):
            from floofy_core.registry_sources import SourceStore  # noqa: PLC0415

            store = SourceStore.load(runtime.paths)
            source = store.find(part[len("@key:"):])
            if source is None:
                raise RouteError(f"no registry source matches {part[len('@key:'):]!r}", 404)
            out.append(source.url)
        else:
            out.append(part)
    return out


def _global_argv(runtime: Any) -> list[str]:
    prefix: list[str] = []
    if runtime.facts is not None and runtime.facts.package_dir is not None:
        prefix = ["--root", str(runtime.facts.package_dir.parent)]  # the running payload, whatever else discovery finds
    return prefix


def _run(runtime: Any, argv: list[str], console: Console, mutated: list[str]) -> tuple[int, dict[str, Any]]:
    from floofy_core.cli.main import execute  # noqa: PLC0415

    full = [*_global_argv(runtime), "--home", str(runtime.facts.host_home), *argv]
    return execute(full, console, actor=APP_ACTOR, in_gateway=True, on_mutation=mutated.append, live_state=runtime.state_dict)


def _consent_question() -> dict[str, Any]:
    return ConfirmationNeeded("consent", WARNING_TEXT, detail="no consent record: the one-time warning must be agreed to first (POST /consent, or confirmations.consent)").to_dict()


def _document(console: Console, code: int, result: dict[str, Any], argv: list[str], command: str, mutated: list[str], reloaded: bool) -> dict[str, Any]:
    return {"ok": code == 0, "exit": code, "command": command, "argv": argv, "stdout": console.out.getvalue() if isinstance(console.out, io.StringIO) else "", "stderr": console.err.getvalue() if isinstance(console.err, io.StringIO) else "", "transcript": list(console.transcript), "json": result, "mutated": list(mutated), "reloaded": reloaded}


def needs_reload(route: AppRouteSpec, mutated: list[str]) -> bool:
    """Whether the Loader re-boots after a successful run: when the handler asked for it (``reload_gateway`` in-process —
    ``enable``/``disable``, an install or removal with ``--now``, a yeet), or after the consent was recorded (the Loader
    was inert). Never otherwise: a staged install stays in ``pending/`` until "apply now" (Requirement 7.6, 16.5) and the
    ``vanilla`` marker is consumed by the next start, not by a reload."""
    return bool(mutated) or route.is_consent


def dispatch(runtime: Any, route: AppRouteSpec, params: dict[str, str] | None = None, body: Any = None, query: dict[str, list[str]] | None = None, *, reload: bool = True) -> Responder:
    """Run one App route in-process and shape the reply (pure; ``reload`` re-boots the Loader inline when the run asks for it)."""
    params = params or {}
    query = query or {}
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return _json(400, {"ok": False, "error": "the body must be a JSON object"})
    confirmations = body.get("confirmations")
    if confirmations is not None and not isinstance(confirmations, dict):
        return _json(400, {"ok": False, "error": "confirmations must be an object"})
    confirmations = dict(confirmations or {})
    if runtime.facts is None or runtime.paths is None:
        return _json(503, {"ok": False, "error": "the Loader has not started"})
    if route.is_consent and body.get("agree") is not True and confirmations.get("consent") is not True:
        # the [ I AGREE ] answer arrives as {agree: true} or, through the protocol, as confirmations.consent; nothing else records it
        return _json(409, {"ok": False, "confirmation": _consent_question(), "error": "the consent route needs {\"agree\": true} (or confirmations.consent) — the [ I AGREE ] control; nothing else records the acknowledgement", "route": route.name})
    try:
        argv = route.argv(params, body, query)
        argv = _resolve_source_url(runtime, argv)
    except RouteError as exc:
        return _json(exc.status, {"ok": False, "error": str(exc), "route": route.name})
    command = argv[0] if argv[0] != "registry" and argv[0] != "profile" else f"{argv[0]} {argv[1]}"
    if route.loosens_trust and "--allow-unsigned" in argv and confirmations.get("unsignedIndex") is not True:
        # the CLI's flag is the user's explicit loosening; the App shows the same warning and asks first (Requirement 11.3)
        from floofy_core.cli.cmd_registry import LOOSENING_WARNING  # noqa: PLC0415

        url = argv[2]
        question = ConfirmationNeeded("unsigned-index", LOOSENING_WARNING.format(url=url), detail="missing")
        return _json(409, {"ok": False, "confirmation": question.to_dict(), "command": command, "argv": argv, "transcript": []})
    if route.is_consent:
        confirmations["consent"] = True  # {"agree": true} IS the [ I AGREE ] answer
    mutated: list[str] = []
    console = app_console(confirmations)
    # the one-time consent outranks everything else: record it first when the request agreed, else stop here
    consent = read_consent(runtime.paths.consent)
    if not consent.ok and not route.is_consent and confirmations.get("consent") is True:
        code, result = _run(runtime, ["init", "--no-loader-app", "--no-trigger"], console, mutated)
        if code != 0:
            return _json(422, {**_document(console, code, result, ["init", "--no-loader-app", "--no-trigger"], "init", mutated, False), "error": result.get("error") or "consent could not be recorded"})
        mutated.append("consent recorded")
        console = app_console(confirmations)
    code, result = _run(runtime, argv, console, mutated)
    if code == EXIT_CONFIRMATION_NEEDED:
        question = result.get("confirmation") or {}
        return _json(409, {"ok": False, "confirmation": question, "disclosure": result.get("disclosure"), "source": result.get("source"), "command": command, "argv": argv, "transcript": list(console.transcript)})
    if code == EXIT_CONSENT_REQUIRED:
        return _json(409, {"ok": False, "confirmation": _consent_question(), "command": command, "argv": argv, "transcript": list(console.transcript), "error": result.get("error")})
    reloaded = False
    if code == 0 and reload and needs_reload(route, mutated):
        try:
            runtime.reload()
            reloaded = True
        except Exception as exc:  # noqa: BLE001 - the command ran; the reload failure is reported
            console.transcript.append(f"reload failed: {type(exc).__name__}: {exc}")
    document = _document(console, code, result, argv, command, mutated, reloaded)
    if code == 0:
        return _json(200, document)
    document["error"] = result.get("error") or f"floofy {command} exited {code}"
    return _json(400 if code == 2 else 422, document)


# --- the aiohttp adapter ----------------------------------------------------------------------------------


def build_app_routes(runtime: Any) -> list:
    """The host's ``AppRoute`` objects for :data:`ROUTES`; the command runs off the event loop, the reload on it."""
    import asyncio  # noqa: PLC0415

    from aiohttp import web  # noqa: PLC0415 - only inside the gateway
    from kiro_crew.apps.route_registry import AppRoute  # type: ignore[import-not-found]  # noqa: PLC0415

    def make(route: AppRouteSpec) -> Callable[..., Any]:
        async def handler(request: Any, ctx: Any) -> Any:
            body: Any = {}
            if route.method in ("POST", "PUT", "DELETE") and request.can_read_body:
                try:
                    body = await request.json()
                except Exception:  # noqa: BLE001 - a bad body is a 400, not a 500
                    return web.Response(status=400, headers={"Content-Type": "application/json; charset=utf-8"}, body=json.dumps({"ok": False, "error": "the body must be JSON"}).encode("utf-8"))
            params = {k: v for k, v in request.match_info.items()}
            query: dict[str, list[str]] = {}
            for key, value in request.query.items():
                query.setdefault(key, []).append(value)
            loop = asyncio.get_running_loop()
            # the CLI does file work (clones, downloads, the Patcher): off the event loop; the reload runs on it afterwards
            status, headers, raw = await loop.run_in_executor(None, lambda: dispatch(runtime, route, params, body, query, reload=False))
            if status == 200:
                document = json.loads(raw.decode("utf-8"))
                if document.get("ok") and needs_reload(route, list(document.get("mutated") or [])):
                    try:
                        await runtime.reload_async()  # the boot on the loop, the Patcher pass off it
                        document["reloaded"] = True
                    except Exception as exc:  # noqa: BLE001
                        document.setdefault("transcript", []).append(f"reload failed: {type(exc).__name__}: {exc}")
                    status, headers, raw = _json(200, document)
            return web.Response(status=status, headers=headers, body=raw)

        handler.__name__ = f"app_{route.name.replace('.', '_')}"
        return handler

    return [AppRoute(route.method, route.path, make(route)) for route in ROUTES]
