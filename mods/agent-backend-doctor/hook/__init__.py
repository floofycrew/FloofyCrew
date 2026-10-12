"""Agent Backend Doctor — find, un-cache and repair KiroCrew's agent-backend harnesses.

Two gaps in the host, both verified in the installed 0.7.0.5 and 0.8.0.6 builds:

1. **Discovery.** ``kiro_crew.acp.client._resolve_node_adapter_argv`` finds the Node
   ACP adapters (``claude-agent-acp``, ``codex-acp``, ``pi-acp``) through an env
   override, a vendored ``node_modules``, mise, and ``env.augmented_path()`` — a
   fixed list of bin directories. An npm global prefix outside that list
   (``~/.local/node``, ``~/.npm-global``, ``prefix=`` in ``~/.npmrc``,
   ``$NPM_CONFIG_PREFIX``) is invisible, so ``npm i -g`` "succeeds" and the backend
   still reads *missing, run npm i -g …*. This hook sits ``after`` that resolver
   (``docs/python-api.md``): when the host answers ``(None, searched)`` it looks in
   exactly those prefixes for ``<package>/dist/index.js`` with the adapter's
   hoisted dependency beside it — the host's own completeness rule — and answers
   ``[<that tree's node>, entry]`` instead. The plain-binary ladders
   (``opencode`` / ``pi`` / the 0.8 self-served table) get the same treatment for a
   ``bin/<name>`` launcher under those prefixes. In-process only; nothing is
   written and the host install is never touched.
2. **The cached negative.** The spawn path resolves each adapter once per process
   behind an ``_UNRESOLVED`` sentinel and never invalidates it, and the host's own
   probe deliberately only *reports* ``restart_required`` ("a dashboard GET must not
   mutate a global on the spawn path"). This mod is not a GET: on activation and on
   the page's explicit *Re-resolve* it resets a cached negative to ``_UNRESOLVED``
   (through ``agent_sdk.forget_for_recheck`` where the host has it — 0.8 — else by
   the same sentinel write, fenced with ``bump_resolution_generation`` when present)
   and drops the probe cache, so the next session resolves fresh through (1) and
   the backend switch flips to *installed* without a gateway restart.

Plus two opt-in repairs the page offers: ``CODEX_PATH`` (the only channel
``codex-acp`` has for running a *different* Codex binary, e.g. one installed by
your own CLI tooling;
exported into this gateway process, which every adapter child inherits) and
symlinking the found launchers into ``~/.local/bin`` so the fix outlives the mod.

3. **Codex's AWS credentials inside the sandbox** (``awsenv.py``). A Codex whose
   ``model_provider`` is ``amazon-bedrock`` authenticates through the AWS SDK
   chain, and the internal edition's credential broker hands every non-Claude
   child ``AWS_CONFIG_FILE`` / ``AWS_SHARED_CREDENTIALS_FILE`` pointers at a
   sanitized per-process copy (profile names kept, every credential-fetching key
   stripped) plus ``AWS_PROFILE=default`` — so the SDK reports *failed to load
   AWS credentials: the credential provider was not enabled*. Before each Codex
   launch this mod reads ``~/.codex/config.toml`` (never ``auth.json``), selects
   the named profile and its ``source_profile`` / ``sso_session`` chain from
   ``~/.aws/config`` with every static key dropped, writes an isolated
   ``<host home>/codex-aws/{config,credentials}`` pair (``0700``/``0600``,
   outside ``~/.aws``, visible through the codex mask), and hands exactly those
   four variables to the child through ``extra_env`` (``after``
   ``AcpClient.__init__``) and again on the final spawn environment (``after``
   ``CodexHarness.apply_spawn_env``). The mask itself is never widened; a
   ``credential_process`` that needs a hidden directory is predicted and named.

Every private host name is reached with ``getattr`` and feature-detected: a seam
the running host lacks degrades that feature to *report only*, and the page says
so. No credential is read, no socket is opened; the mod's own state lives in
``ctx.config`` / ``ctx.data_dir``.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from typing import Any

from . import awsenv as AWS
from . import discovery as D

_ctx = None
_report_only: dict[str, str] = {}      # feature → why it is report-only on this host
_findings: dict[str, dict[str, Any]] = {}   # bin_name → what the after-hook answered, for the page
_codex_prev: tuple[bool, str] | None = None  # (was set, previous value) of CODEX_PATH before we touched it
_awsenv_last: dict[str, Any] = {}       # the last preflight, for the page and the router
_awsenv_sessions: dict[str, dict[str, Any]] = {}  # session_key → launch record (what the child was handed)
_AWSENV_MAX_SESSIONS = 200

RESOLVER = "kiro_crew.acp.client:_resolve_node_adapter_argv"
CLIENT_INIT = "kiro_crew.acp.client:AcpClient.__init__"
CODEX_SPAWN_ENV = "kiro_crew.acp.harness.codex:CodexHarness.apply_spawn_env"

#: backend id → the launcher name the host's ladders look for (0.8's ``ACP_BACKEND_PROCESS_NAMES`` wins when present).
FALLBACK_PROCESS_NAMES = {"claude": "claude-agent-acp", "codex": "codex-acp", "pi": "pi-acp", "opencode": "opencode", "": "kiro-cli", "kas": "kiro-cli"}
#: backend id → module-level caches the spawn path keeps for it (``_self_served_bin_caches`` is a dict keyed by id on 0.8).
CACHE_GLOBALS = {"claude": ("_claude_acp_argv_cache",), "codex": ("_codex_acp_argv_cache",), "pi": ("_pi_acp_argv_cache", "_pi_bin_cache"), "opencode": ("_opencode_bin_cache",)}
NODE_ADAPTER_BACKENDS = ("claude", "codex", "pi")


def default_codex_path() -> str | None:
    """The ``codex`` the host's own search path reaches (the one the user installed), else ``None``."""
    return D.on_search_path("codex", _search_path())

# --- host access, all feature-detected -----------------------------------------------------------------


def _client():
    from kiro_crew.acp import client  # noqa: PLC0415

    return client


def _home() -> str:
    return os.path.expanduser("~")


def _search_path() -> str:
    try:
        from kiro_crew.env import augmented_path  # noqa: PLC0415

        return str(augmented_path(os.environ.get("PATH", "")))
    except Exception:  # noqa: BLE001
        return os.environ.get("PATH", "")


def _known_backends() -> list[str]:
    try:
        from kiro_crew.acp_backends import ACP_BACKENDS_KNOWN  # noqa: PLC0415

        return sorted(ACP_BACKENDS_KNOWN, key=lambda b: (b != "", b))
    except Exception:  # noqa: BLE001
        return ["", "kas", "claude", "codex", "opencode", "pi"]


def _policy_name(backend: str) -> str:
    for module in ("kiro_crew.acp_backends", "kiro_crew.agent_sdk.backends"):
        try:
            mod = __import__(module, fromlist=["POLICY_ID_BY_BACKEND"])
            table = getattr(mod, "POLICY_ID_BY_BACKEND", None)
            if isinstance(table, dict):
                return str(table.get(backend, backend or "kiro"))
        except Exception:  # noqa: BLE001
            continue
    return backend or "kiro"


def _selectable() -> set[str]:
    try:
        from kiro_crew.dashboard.handlers.core import _selectable_acp_backends  # noqa: PLC0415

        return set(_selectable_acp_backends())
    except Exception:  # noqa: BLE001
        try:
            from kiro_crew.acp_backends import selectable_backend_values  # noqa: PLC0415

            return set(selectable_backend_values())
        except Exception:  # noqa: BLE001
            return set()


def _process_names() -> dict[str, str]:
    names = dict(FALLBACK_PROCESS_NAMES)
    try:
        from kiro_crew.agent_sdk.backends import ACP_BACKEND_PROCESS_NAMES  # noqa: PLC0415

        names.update({str(k): str(v) for k, v in dict(ACP_BACKEND_PROCESS_NAMES).items()})
    except Exception:  # noqa: BLE001
        pass
    return names


def _adapter_packages() -> dict[str, str]:
    packages = {"claude": D.ADAPTER_PACKAGES["claude-agent-acp"], "codex": D.ADAPTER_PACKAGES["codex-acp"], "pi": D.ADAPTER_PACKAGES["pi-acp"]}
    try:
        from kiro_crew.agent_sdk.backends import ACP_BACKEND_NODE_ADAPTER_PACKAGES  # noqa: PLC0415

        packages.update({str(k): str(v) for k, v in dict(ACP_BACKEND_NODE_ADAPTER_PACKAGES).items()})
    except Exception:  # noqa: BLE001
        pass
    return packages


def _namespace(backend: str) -> str:
    try:
        from kiro_crew.acp_backends import model_registry_namespace  # noqa: PLC0415

        return str(model_registry_namespace(backend))
    except Exception:  # noqa: BLE001
        return "acp" if backend in ("", "kas") else backend


def _advertised(namespace: str) -> list[str]:
    try:
        from kiro_crew import model_registry  # noqa: PLC0415

        return [str(m) for m in model_registry.advertised_models(namespace) if isinstance(m, str) and m]
    except Exception:  # noqa: BLE001
        return []


# --- seam 1: discovery after-hooks ------------------------------------------------------------------------


def _after_node_adapter(result, *args, **kwargs):
    """``after`` ``_resolve_node_adapter_argv``: fill a miss from the npm prefixes the host does not search."""
    bin_name = str(kwargs.get("bin_name") or (args[0] if args else "") or "")
    try:
        argv, searched = result
    except Exception:  # noqa: BLE001 - an unexpected shape passes through untouched
        return result
    if argv or not bin_name:
        _findings[bin_name or "?"] = {"host": "found" if argv else "no bin_name", "argv": list(argv or []), "ts": time.time()}
        return result
    found = D.find_adapter(bin_name, home=_home(), search_path=str(searched or ""), environ=dict(os.environ), package=_adapter_packages().get(_backend_of(bin_name)))
    if found is None:
        _findings[bin_name] = {"host": "missing", "doctor": None, "ts": time.time()}
        if _ctx is not None:
            prefixes = [p for _r, p in D.candidate_prefixes(_home(), dict(os.environ), node_on_path=D.find_node(dict(os.environ), str(searched or ""), _home()))]
            _ctx.log.info("%s: the host searched %d dirs and missed it; the doctor checked %d npm prefix(es) (%s) and found no complete install either",
                          bin_name, len([p for p in str(searched).split(os.pathsep) if p]), len(prefixes), ", ".join(prefixes) or "none")
        return result
    _findings[bin_name] = {"host": "missing", "doctor": {"entry": found.entry, "node": found.node, "prefix": found.prefix, "rung": found.rung}, "ts": time.time()}
    if _ctx is not None:
        _ctx.log.info("%s: the host searched %d dirs and missed it; found under %s (%s) — spawning %s", bin_name, len([p for p in str(searched).split(os.pathsep) if p]), found.prefix, found.rung, found.argv)
    return found.argv, searched


def _plain_binary_hook(default_name: str | None):
    """An ``after`` hook for a plain-binary ladder (``opencode`` / ``pi`` / 0.8's ``_resolve_self_served_bin(backend)``).

    The 0.8 ladder takes the backend id as its argument, so the launcher name comes
    from the process-name table; the 0.7 per-harness ladders take none, so the name
    is the one bound here. A miss is filled with a ``bin/<name>`` launcher under one
    of the npm prefixes the host does not search.
    """

    def hook(result, *args, **kwargs):
        try:
            binary, searched = result
        except Exception:  # noqa: BLE001 - an unexpected shape passes through untouched
            return result
        if binary:
            return result
        backend = args[0] if args and isinstance(args[0], str) else kwargs.get("backend")
        name = _process_names().get(backend) if isinstance(backend, str) else None
        name = name or default_name
        if not name:
            return result
        for launcher in D.launcher_candidates(name, home=_home(), environ=dict(os.environ)):
            _findings[name] = {"host": "missing", "doctor": {"entry": launcher, "node": None, "prefix": os.path.dirname(os.path.dirname(launcher)), "rung": "bin/ under an npm prefix"}, "ts": time.time()}
            if _ctx is not None:
                _ctx.log.info("%s: not on the host's search path; using the launcher %s", name, launcher)
            return launcher, searched
        _findings[name] = {"host": "missing", "doctor": None, "ts": time.time()}
        return result

    return hook


def _backend_of(bin_name: str) -> str:
    for backend, name in _process_names().items():
        if name == bin_name and backend not in ("", "kas"):
            return backend
    return ""


# --- seam 2: the spawn path's caches -------------------------------------------------------------------------


def cache_state(backend: str) -> dict[str, Any]:
    """What the running gateway remembers about *backend*'s resolution: ``unresolved`` / ``ok`` / ``negative`` per cache."""
    out: dict[str, Any] = {"state": "unresolved", "caches": {}}
    try:
        client = _client()
    except Exception as exc:  # noqa: BLE001
        return {"state": "unknown", "caches": {}, "error": f"{type(exc).__name__}: {exc}"}
    unresolved = getattr(client, "_UNRESOLVED", object())
    values: dict[str, Any] = {}
    for name in CACHE_GLOBALS.get(backend, ()):
        if hasattr(client, name):
            values[name] = getattr(client, name)
    shared = getattr(client, "_self_served_bin_caches", None)
    if isinstance(shared, dict) and backend in shared:
        values["_self_served_bin_caches[%s]" % backend] = shared[backend]
    for name, value in values.items():
        if value is unresolved:
            out["caches"][name] = {"state": "unresolved"}
            continue
        try:
            found, _searched = value
        except Exception:  # noqa: BLE001
            out["caches"][name] = {"state": "unknown"}
            continue
        out["caches"][name] = {"state": "ok" if found else "negative", "value": list(found) if isinstance(found, (list, tuple)) else found}
    states = {c["state"] for c in out["caches"].values()}
    out["state"] = "negative" if "negative" in states else "ok" if "ok" in states else "unresolved" if values else "none"
    return out


def forget(backend: str) -> dict[str, Any]:
    """Drop the spawn path's cached resolution and the probe cache for *backend*. Call ON THE EVENT LOOP."""
    done: dict[str, Any] = {"backend": backend, "via": [], "before": cache_state(backend).get("state")}
    try:
        from kiro_crew import agent_sdk  # noqa: PLC0415

        forget_for_recheck = getattr(agent_sdk, "forget_for_recheck", None)
        if callable(forget_for_recheck):
            forget_for_recheck(backend)
            done["via"].append("agent_sdk.forget_for_recheck")
    except Exception as exc:  # noqa: BLE001
        done["error"] = f"{type(exc).__name__}: {exc}"
    if "agent_sdk.forget_for_recheck" not in done["via"]:
        try:
            client = _client()
            unresolved = getattr(client, "_UNRESOLVED", None)
            bump = getattr(client, "bump_resolution_generation", None)
            if callable(bump):
                bump(backend)
                done["via"].append("bump_resolution_generation")
            if unresolved is not None:
                for name in CACHE_GLOBALS.get(backend, ()):
                    if hasattr(client, name):
                        setattr(client, name, unresolved)
                        done["via"].append(name)
            shared = getattr(client, "_self_served_bin_caches", None)
            if isinstance(shared, dict) and shared.pop(backend, None) is not None:
                done["via"].append("_self_served_bin_caches")
        except Exception as exc:  # noqa: BLE001
            done["error"] = f"{type(exc).__name__}: {exc}"
        try:
            from kiro_crew.agent_sdk import clear_probe_cache  # noqa: PLC0415

            clear_probe_cache()
            done["via"].append("clear_probe_cache")
        except Exception as exc:  # noqa: BLE001
            done.setdefault("error", f"{type(exc).__name__}: {exc}")
    done["after"] = cache_state(backend).get("state")
    return done


def _reset_negatives() -> list[dict[str, Any]]:
    """On activation: every backend whose cache holds a miss is reset so the next spawn resolves through the after-hook."""
    out = []
    for backend in _known_backends():
        if cache_state(backend).get("state") == "negative":
            out.append(forget(backend))
    return out


# --- seam 3: Codex's AWS launch environment ------------------------------------------------------------------


def _host_home() -> str:
    hh = getattr(getattr(_ctx, "host", None), "host_home", None) if _ctx else None
    return str(hh) if hh else os.path.join(_home(), ".kiro", "crew")


def awsenv_enabled() -> bool:
    cfg = _ctx.config.to_dict() if _ctx is not None else {}
    return cfg.get("codex_awsenv_enabled", True) is not False


def run_preflight(*, write: bool = True, reason: str = "launch") -> AWS.Preflight:
    """The preflight over the live HOME / host home; the result is remembered for the page and the router.

    Blocking (reads two small files, writes two): the launch hooks call it on the
    event loop deliberately — it is the same size of work as the host's own broker
    ``start_broker`` performs on that loop (three file writes), and running it
    off-loop would hand the child an environment the preflight has not finished
    building.
    """
    global _awsenv_last
    pf = AWS.preflight(home=_home(), host_home=_host_home(), environ=dict(os.environ), write=write)
    _awsenv_last = {**pf.to_dict(), "reason": reason}
    if _ctx is not None:
        _ctx.state["codex_awsenv"] = {"ok": pf.ok, "verdict": pf.verdict, "profile": (pf.profile or {}).get("profile"), "ts": pf.ts}
    return pf


def _record_session(session_key: str, record: dict[str, Any]) -> None:
    if not session_key:
        return
    if len(_awsenv_sessions) >= _AWSENV_MAX_SESSIONS:
        oldest = sorted(_awsenv_sessions.items(), key=lambda kv: kv[1].get("ts", 0))[: len(_awsenv_sessions) - _AWSENV_MAX_SESSIONS + 1]
        for k, _v in oldest:
            _awsenv_sessions.pop(k, None)
    _awsenv_sessions[session_key] = record


def codex_launch_record(session_key: str) -> dict[str, Any] | None:
    """What the doctor handed the Codex child of *session_key* (for the router's diagnostics). ``None`` if nothing."""
    return _awsenv_sessions.get(session_key or "")


def apply_awsenv(extra_env: dict[str, str], *, session_key: str, reason: str) -> dict[str, Any]:
    """Run the preflight and, when it passes, put the isolated pointers into *extra_env* (in place).

    The four variables REPLACE whatever the broker or the parent shell put there:
    that is the point. Nothing else in *extra_env* is touched (the broker's URL and
    session id stay — they are inert for a Codex child and belong to its own
    lifecycle bookkeeping). Returns the launch record stored for the session.
    """
    pf = run_preflight(write=True, reason=reason)
    superseded = {k: ("set" if k in extra_env else "unset") for k in AWS.SUPERSEDED}
    applied = False
    if pf.ok and pf.env:
        for k in AWS.SUPERSEDED:
            extra_env.pop(k, None)
        extra_env.update(pf.env)
        applied = True
    record = {"session": session_key, "ts": time.time(), "reason": reason, "applied": applied, "preflight": pf.verdict, "preflight_ok": pf.ok,
              "detail": pf.detail, "profile": (pf.profile or {}).get("profile") if pf.profile else None,
              "credential_source": (pf.profile or {}).get("credential_source") if pf.profile else None,
              "process_may_need_hidden_dir": (pf.profile or {}).get("process_may_need_hidden_dir") if pf.profile else None,
              "superseded": superseded, "config_file": pf.env.get(AWS.ENV_CONFIG) if applied else None,
              "model_provider": pf.codex.get("model_provider"), "codex_model": pf.codex.get("model"), "region": pf.codex.get("aws_region")}
    _record_session(session_key, record)
    if _ctx is not None:
        if applied:
            _ctx.log.info("codex awsenv %s session=%s: profile=%s (%s) → isolated config applied%s", reason, session_key or "-", record["profile"],
                          record["credential_source"], f"; NOTE {pf.sandbox_note}" if pf.sandbox_note else "")
        elif pf.verdict == "not_bedrock":
            _ctx.log.debug("codex awsenv %s session=%s: %s", reason, session_key or "-", pf.detail)
        else:
            _ctx.log.warning("codex awsenv %s session=%s: NOT applied (%s): %s", reason, session_key or "-", pf.verdict, pf.detail)
    return record


def _after_client_init(result, self, *args, **kwargs):
    """``after`` ``AcpClient.__init__``: a Codex client gets the isolated AWS pointers in its ``_extra_env``.

    Both transports read the child's environment from this dict (``AcpClient._spawn``
    directly; ``AcpRuntime`` through the provider's ``getattr(client, "_extra_env")``),
    so mutating it here covers 0.7's per-session adapter and 0.8's runtime path alike.
    """
    try:
        if not awsenv_enabled() or getattr(self, "backend", None) != "codex":
            return result
        extra_env = getattr(self, "_extra_env", None)
        if not isinstance(extra_env, dict):
            extra_env = {}
            self._extra_env = extra_env
        session_key = kwargs.get("session_key") or (args[4] if len(args) > 4 and isinstance(args[4], str) else "")
        apply_awsenv(extra_env, session_key=str(session_key or ""), reason="launch")
    except Exception as exc:  # noqa: BLE001 - never let a diagnostic break a spawn
        if _ctx is not None:
            _ctx.log.warning("codex awsenv at client init failed open: %s", exc)
    return result


def _after_codex_spawn_env(result, self, env, *args, **kwargs):
    """``after`` ``CodexHarness.apply_spawn_env``: the FINAL environment of a runtime-path Codex spawn.

    ``extra_env`` was already merged by the time this runs, so the pointers are
    normally present; this re-asserts them against anything that merged later and
    records which spelling the child actually received. The runtime does not pass
    the session key here, so the record is keyed by the config path it applied.
    """
    try:
        if not awsenv_enabled() or not isinstance(env, dict):
            return result
        pf_env = (_awsenv_last.get("env") or {}) if _awsenv_last.get("ok") else {}
        if not pf_env:
            pf = run_preflight(write=True, reason="spawn-env")
            pf_env = pf.env if pf.ok else {}
        if pf_env:
            for k in AWS.SUPERSEDED:
                env.pop(k, None)
            env.update(pf_env)
            _awsenv_last["spawn_env_applied"] = time.time()
    except Exception as exc:  # noqa: BLE001
        if _ctx is not None:
            _ctx.log.warning("codex awsenv at spawn env failed open: %s", exc)
    return result


def repair_codex_awsenv(session_key: str, reason: str, client=None) -> dict[str, Any]:
    """Re-create the isolated config and re-point *client*'s ``_extra_env`` (the router's one repair before its one retry)."""
    try:
        root = AWS.awsenv_dir(_host_home())
        for name in (AWS.CONFIG_NAME, AWS.CREDENTIALS_NAME):
            path = os.path.join(root, name)
            if os.path.lexists(path):
                os.unlink(path)
    except OSError as exc:
        if _ctx is not None:
            _ctx.log.warning("codex awsenv repair: could not remove the old files: %s", exc)
    extra_env = getattr(client, "_extra_env", None) if client is not None else None
    if not isinstance(extra_env, dict):
        extra_env = {}
        if client is not None:
            try:
                client._extra_env = extra_env
            except Exception:  # noqa: BLE001
                pass
    record = apply_awsenv(extra_env, session_key=session_key, reason=f"repair:{reason}")
    record["repaired"] = True
    return record


def classify_codex_failure(message: str, *, transient: bool | None = None, reached_model: bool = False) -> dict[str, Any]:
    return AWS.classify_failure(message, transient=transient, reached_model=reached_model)


PROBE_TIMEOUT_S = 25.0


def probe_awsenv(sandboxed: bool = True) -> dict[str, Any]:
    """``aws sts get-caller-identity`` with exactly the child's AWS environment, inside the child's own mask.

    The ONLY process this mod ever runs for credentials, and it is the provider
    chain itself, not a reader of any file. Sandboxed through the host's
    ``sandboxed_spawn_argv`` with the same hidden dirs the codex child gets
    (``adapter_hidden_credential_dirs("codex")``), so the answer is the child's
    truth, not the gateway's. ``AWS_EC2_METADATA_DISABLED`` stops a fall-through
    to IMDS. Only the exit status and the Account / Arn fields are kept; stdout
    is parsed for those two keys and otherwise discarded, stderr is redacted and
    truncated. Blocking — run off-loop.
    """
    import shutil  # noqa: PLC0415

    pf = run_preflight(write=True, reason="probe")
    out: dict[str, Any] = {"preflight": pf.verdict, "ok": False, "sandboxed": False, "ts": time.time()}
    if not pf.ok or not pf.env:
        out["error"] = pf.detail
        return out
    aws_cli = shutil.which("aws", path=_search_path())
    if not aws_cli:
        out["error"] = "no `aws` CLI on the host's search path to run the provider chain with"
        return out
    argv = [aws_cli, "sts", "get-caller-identity", "--output", "json", "--no-cli-pager"]
    env = {**os.environ, **pf.env, "AWS_EC2_METADATA_DISABLED": "true", "AWS_PAGER": "", "PATH": _search_path()}
    cleanup = None
    if sandboxed:
        try:
            from kiro_crew.acp_tool_gate import adapter_hidden_credential_dirs, adapter_expose_files  # noqa: PLC0415
            from kiro_crew.sandbox import sandboxed_spawn_argv  # noqa: PLC0415

            mode = str(_host_sandbox_mode() or "auto")
            hidden = tuple(adapter_hidden_credential_dirs("codex"))
            try:
                expose = tuple(adapter_expose_files("codex", hidden))
            except Exception:  # noqa: BLE001
                expose = ()
            try:
                argv, env, cleanup = sandboxed_spawn_argv(argv, mode, env=env, strip_python_env=True, extra_hidden_dirs=hidden, extra_expose_files=expose)
            except TypeError:
                argv, env, cleanup = sandboxed_spawn_argv(argv, mode, env=env, strip_python_env=True, extra_hidden_dirs=hidden)
            out["sandboxed"] = True
            out["hidden_dirs"] = len(hidden)
        except Exception as exc:  # noqa: BLE001 - no sandbox: say so, run nothing unmasked
            out["error"] = f"cannot build the child's sandbox for the probe ({type(exc).__name__}: {exc}); refusing to probe unmasked"
            return out
    try:
        completed = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=PROBE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        out["error"] = f"the provider chain did not answer within {PROBE_TIMEOUT_S:.0f}s"
        return out
    except OSError as exc:
        out["error"] = f"could not run the probe: {exc}"
        return out
    finally:
        if cleanup:
            try:
                os.unlink(cleanup)
            except OSError:
                pass
    out["exit_code"] = completed.returncode
    if completed.returncode == 0:
        try:
            doc = json.loads(completed.stdout or "{}")
            out.update({"ok": True, "account": str(doc.get("Account", "")), "arn": str(doc.get("Arn", ""))})
        except ValueError:
            out.update({"ok": True, "account": "", "arn": ""})
    else:
        cls = AWS.classify_failure(completed.stderr or "")
        out.update({"category": cls["category"], "stderr": cls["redacted"]})
    return out


def _host_sandbox_mode() -> str | None:
    try:
        with open(os.path.join(_host_home(), "config.json"), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        value = (doc.get("agent") or {}).get("sandbox")
        return value if isinstance(value, str) and value else None
    except (OSError, ValueError):
        return None


def awsenv_snapshot() -> dict[str, Any]:
    """The page's Codex-AWS panel: the last preflight (report-only refresh), the host facts, the recent launches."""
    pf = AWS.preflight(home=_home(), host_home=_host_home(), environ=dict(os.environ), write=False)
    report = pf.to_dict()
    if _awsenv_last.get("ok") and report["verdict"] == "not_applied":
        # Nothing on disk yet is a normal state before the first launch; say so instead of "bad".
        report["verdict"], report["detail"] = "pending", "the isolated config is written on the first Codex launch (or Rebuild now)"
    recent = sorted(_awsenv_sessions.values(), key=lambda r: r.get("ts", 0), reverse=True)[:20]
    return {"enabled": awsenv_enabled(), "hook": "report-only: " + _report_only["codex_awsenv"] if "codex_awsenv" in _report_only else "active",
            "spawn_env_hook": "report-only: " + _report_only["codex_spawn_env"] if "codex_spawn_env" in _report_only else "active",
            "dir": AWS.awsenv_dir(_host_home()), "sandbox_mode": _host_sandbox_mode(), "report": report, "last": dict(_awsenv_last), "recent": recent}


# --- the picture the page shows --------------------------------------------------------------------------------


def _probe(backend: str) -> dict[str, Any]:
    try:
        from kiro_crew.agent_sdk import probe_backend  # noqa: PLC0415

        state = probe_backend(backend)
        return {"installed": str(getattr(state, "installed", "unknown")), "missing_components": list(getattr(state, "missing_components", ()) or ()),
                "install_command": str(getattr(state, "install_command", "") or ""), "restart_required": bool(getattr(state, "restart_required", False))}
    except Exception as exc:  # noqa: BLE001
        return {"installed": "unknown", "missing_components": [], "install_command": "", "restart_required": False, "error": f"{type(exc).__name__}: {exc}"}


def _components(backend: str, name: str, search_path: str) -> dict[str, Any]:
    """The pieces a backend needs on disk, as this host sees them right now (blocking: filesystem only)."""
    home = _home()
    env = dict(os.environ)
    row: dict[str, Any] = {"launcher": name, "launcher_on_host_path": D.on_search_path(name, search_path) if name else None}
    if backend in NODE_ADAPTER_BACKENDS:
        found = D.find_adapter(name, home=home, search_path=search_path, environ=env, package=_adapter_packages().get(backend))
        row["adapter"] = {"entry": found.entry, "node": found.node, "prefix": found.prefix, "rung": found.rung} if found else None
        row["launchers"] = D.launcher_candidates(name, home=home, environ=env)
    elif backend not in ("", "kas"):
        row["launchers"] = D.launcher_candidates(name, home=home, environ=env) if name else []
    if backend == "claude":
        exe = None
        try:
            exe = getattr(_client(), "_resolve_claude_code_executable", lambda: None)()
        except Exception:  # noqa: BLE001
            exe = None
        row["cli"] = {"name": "claude", "path": exe, "env": "CLAUDE_CODE_EXECUTABLE", "forwarded": True}
    elif backend == "codex":
        row["cli"] = {"name": "codex", "path": os.environ.get("CODEX_PATH") or None, "env": "CODEX_PATH", "forwarded": False,
                      "installed_codex": D.on_search_path("codex", search_path),
                      "note": "codex-acp ships its own Codex and runs a different one only when CODEX_PATH names it"}
    elif backend == "pi":
        pi_bin = None
        try:
            pi_bin = getattr(_client(), "_resolve_pi_bin", lambda: (None, ""))()[0]
        except Exception:  # noqa: BLE001
            pi_bin = None
        row["cli"] = {"name": "pi", "path": pi_bin, "env": "PI_ACP_PI_COMMAND", "forwarded": True}
    return row


def snapshot() -> dict[str, Any]:
    """Everything the page renders. BLOCKING (probes shell out, discovery walks the filesystem) — run off-loop."""
    search_path = _search_path()
    names = _process_names()
    selectable = _selectable()
    env = dict(os.environ)
    home = _home()
    host_rows = {}
    try:
        from kiro_crew.dashboard.handlers import acp_backend_status as host_status  # noqa: PLC0415

        snap = getattr(host_status, "_snapshot", None)
        if callable(snap):
            host_rows = {str(r.get("id", "")): r for r in snap() if isinstance(r, dict)}
    except Exception as exc:  # noqa: BLE001
        host_rows = {"__error__": {"error": f"{type(exc).__name__}: {exc}"}}
    backends = []
    for backend in _known_backends():
        name = names.get(backend, backend)
        ns = _namespace(backend)
        row = {"id": backend, "name": _policy_name(backend), "namespace": ns, "selectable": backend in selectable, "probe": _probe(backend),
               "cache": cache_state(backend), "components": _components(backend, name, search_path), "advertised": _advertised(ns),
               "finding": _findings.get(name), "host_row": {k: host_rows[backend].get(k) for k in ("installed", "restart_required", "selectable", "missing_components", "install_command") if k in host_rows.get(backend, {})} if backend in host_rows else None}
        backends.append(row)
    node = D.find_node(env, search_path, home)
    prefixes = [{"rung": rung, "prefix": prefix, "node_modules": D.node_modules_roots(prefix)} for rung, prefix in D.candidate_prefixes(home, env, node_on_path=node)]
    return {
        "host_version": str(getattr(getattr(_ctx, "host", None), "version", "") or "") if _ctx else "",
        "hooks": {"node_adapters": "report-only: " + _report_only["node_adapters"] if "node_adapters" in _report_only else "active",
                  "plain_binaries": "report-only: " + _report_only["plain_binaries"] if "plain_binaries" in _report_only else "active"},
        "search_path": D.describe_dirs(search_path, 40), "search_path_count": len([p for p in search_path.split(os.pathsep) if p]),
        "node": node, "prefixes": prefixes, "codex_path": os.environ.get("CODEX_PATH"), "codex_path_default": default_codex_path(),
        "local_bin": os.path.join(home, ".local", "bin"), "config": dict(_ctx.config.to_dict()) if _ctx else {},
        "repairs": _repair_plan(backends, node, search_path), "host_rows_error": host_rows.get("__error__"), "backends": backends, "ts": time.time(),
    }


def _repair_plan(backends: list[dict[str, Any]], node: str | None, search_path: str) -> list[dict[str, Any]]:
    """Symlinks that would make the found launchers (and node) visible to the host's own ladder: ``~/.local/bin/<name>``."""
    plan: list[dict[str, Any]] = []
    local_bin = os.path.join(_home(), ".local", "bin")
    for row in backends:
        comp = row["components"]
        name = comp.get("launcher")
        if not name or comp.get("launcher_on_host_path") or row["id"] in ("", "kas"):
            continue
        for launcher in comp.get("launchers") or []:
            link = os.path.join(local_bin, name)
            plan.append({"name": name, "link": link, "target": launcher, "exists": os.path.lexists(link), "backend": row["id"]})
            break
    if node and not D.on_search_path("node", search_path):
        link = os.path.join(local_bin, "node")
        plan.append({"name": "node", "link": link, "target": node, "exists": os.path.lexists(link), "backend": None})
    return plan


# --- repairs ------------------------------------------------------------------------------------------------------


def apply_codex_path(enabled: bool, path: str | None) -> dict[str, Any]:
    """Export (or drop) ``CODEX_PATH`` in this gateway process; every adapter child inherits ``os.environ``."""
    global _codex_prev
    if _codex_prev is None:
        _codex_prev = ("CODEX_PATH" in os.environ, os.environ.get("CODEX_PATH", ""))
    if not enabled:
        was_set, prev = _codex_prev
        if was_set:
            os.environ["CODEX_PATH"] = prev
        else:
            os.environ.pop("CODEX_PATH", None)
        return {"ok": True, "codex_path": os.environ.get("CODEX_PATH"), "enabled": False}
    target = os.path.expanduser(path or default_codex_path() or "")
    if not target or not (os.path.isfile(target) and os.access(target, os.X_OK)):
        if not target:
            return {"ok": False, "error": "no codex executable on the host's search path; type its path", "codex_path": os.environ.get("CODEX_PATH"), "enabled": "CODEX_PATH" in os.environ}
        return {"ok": False, "error": f"{target} is not an executable file", "codex_path": os.environ.get("CODEX_PATH"), "enabled": "CODEX_PATH" in os.environ}
    os.environ["CODEX_PATH"] = target
    return {"ok": True, "codex_path": target, "enabled": True}


def repair_plan_now() -> list[dict[str, Any]]:
    """The symlink plan from the component rows alone (no probes, no host snapshot)."""
    search_path = _search_path()
    names = _process_names()
    rows = [{"id": b, "components": _components(b, names.get(b, b), search_path)} for b in _known_backends()]
    return _repair_plan(rows, D.find_node(dict(os.environ), search_path, _home()), search_path)


def link_launchers(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create ``~/.local/bin/<name>`` → launcher symlinks. Never overwrites: an existing path is reported, not replaced."""
    results = []
    local_bin = os.path.join(_home(), ".local", "bin")
    plan = {e["name"]: e for e in repair_plan_now()}
    for entry in entries:
        name = str(entry.get("name", ""))
        planned = plan.get(name)
        if planned is None:
            results.append({"name": name, "ok": False, "error": "nothing to link for this name"})
            continue
        link, target = planned["link"], planned["target"]
        if not os.path.isabs(link) or os.path.dirname(link) != local_bin or os.sep in name or name in (".", ".."):
            results.append({"name": name, "ok": False, "error": "refusing a link outside ~/.local/bin"})
            continue
        try:
            os.makedirs(local_bin, exist_ok=True)
            if os.path.lexists(link):
                current = os.readlink(link) if os.path.islink(link) else None
                results.append({"name": name, "ok": current == target, "link": link, "target": target, "error": None if current == target else f"{link} already exists" + (f" → {current}" if current else "")})
                continue
            os.symlink(target, link)
            results.append({"name": name, "ok": True, "link": link, "target": target})
        except OSError as exc:
            results.append({"name": name, "ok": False, "link": link, "target": target, "error": str(exc)})
    return results


# --- routes -----------------------------------------------------------------------------------------------------------


async def _status(request):
    return await asyncio.to_thread(snapshot)


async def _reresolve(request):
    body = request.json() if request.body else {}
    wanted = body.get("backend") if isinstance(body, dict) else None
    targets = [wanted] if isinstance(wanted, str) and wanted in _known_backends() else [b for b in _known_backends() if b not in ("", "kas")]
    results = [forget(b) for b in targets]            # on the loop: the handler runs inline until its first await
    probes = {}
    for b in targets:
        probes[b or "kiro"] = await asyncio.to_thread(_probe, b)
        probes[b or "kiro"]["cache"] = cache_state(b)
    if _ctx is not None:
        _ctx.log.info("re-resolved %s: %s", [b or "kiro" for b in targets], {b or "kiro": r.get("after") for b, r in zip(targets, results)})
    return {"ok": True, "reset": results, "probes": probes}


def _codex_path(request):
    body = request.json() if request.body else {}
    enabled = bool(body.get("enabled")) if isinstance(body, dict) else False
    path = body.get("path") if isinstance(body, dict) else None
    result = apply_codex_path(enabled, path if isinstance(path, str) and path.strip() else None)
    if result.get("ok") and _ctx is not None:
        _ctx.config.update({"codex_path_enabled": enabled, "codex_path": result.get("codex_path") or _ctx.config.get("codex_path", "")})
        _ctx.state["codex_path"] = result.get("codex_path")
        _ctx.log.info("CODEX_PATH %s", result.get("codex_path") or "unset")
    return (200 if result.get("ok") else 400, result)


def _symlink(request):
    body = request.json() if request.body else {}
    if not isinstance(body, dict) or body.get("confirm") is not True:
        return 400, {"ok": False, "error": "confirm: true is required"}
    entries = body.get("entries") or []
    results = link_launchers([e for e in entries if isinstance(e, dict)])
    if _ctx is not None:
        _ctx.log.info("symlink repair: %s", [(r.get("name"), r.get("ok")) for r in results])
    return {"ok": all(r.get("ok") for r in results), "results": results}


async def _awsenv_status(request):
    return await asyncio.to_thread(awsenv_snapshot)


def _awsenv_rebuild(request):
    record = repair_codex_awsenv("", "page", None)
    return {"ok": bool(record.get("applied")), "record": record, "last": dict(_awsenv_last)}


async def _awsenv_probe(request):
    body = request.json() if request.body else {}
    sandboxed = True if not isinstance(body, dict) else body.get("sandboxed", True) is not False
    result = await asyncio.to_thread(probe_awsenv, sandboxed)
    if _ctx is not None:
        _ctx.log.info("codex awsenv probe: ok=%s sandboxed=%s exit=%s category=%s", result.get("ok"), result.get("sandboxed"), result.get("exit_code"), result.get("category"))
    return result


def _awsenv_toggle(request):
    body = request.json() if request.body else {}
    enabled = bool(body.get("enabled", True)) if isinstance(body, dict) else True
    if _ctx is not None:
        _ctx.config.update({"codex_awsenv_enabled": enabled})
        _ctx.state["codex_awsenv_enabled"] = enabled
        _ctx.log.info("codex awsenv %s", "enabled" if enabled else "disabled (Codex children get the host's environment untouched)")
    return {"ok": True, "enabled": enabled}


# --- lifecycle --------------------------------------------------------------------------------------------------------


def activate(ctx):
    global _ctx
    _ctx = ctx
    _report_only.clear()
    try:
        ctx.hooks.after(RESOLVER, _after_node_adapter)
    except Exception as exc:  # noqa: BLE001 - a host without the seam: report only
        _report_only["node_adapters"] = f"{type(exc).__name__}: {exc}"
        ctx.log.warning("node-adapter discovery is report-only on this host: %s", exc)
    hooked_plain = 0
    for target, default_name in (("kiro_crew.acp.client:_resolve_self_served_bin", None), ("kiro_crew.acp.client:_resolve_opencode_bin", "opencode"), ("kiro_crew.acp.client:_resolve_pi_bin", "pi")):
        try:
            ctx.hooks.after(target, _plain_binary_hook(default_name))
            hooked_plain += 1
        except Exception:  # noqa: BLE001 - 0.7 has _resolve_opencode_bin, 0.8 has _resolve_self_served_bin; both have _resolve_pi_bin
            continue
    if not hooked_plain:
        _report_only["plain_binaries"] = "no plain-binary resolver found on this host"

    ctx.routes.add("GET", "status", _status)
    ctx.routes.add("POST", "reresolve", _reresolve)
    ctx.routes.add("POST", "codex-path", _codex_path)
    ctx.routes.add("POST", "symlink", _symlink)
    ctx.routes.add("GET", "codex-awsenv", _awsenv_status)
    ctx.routes.add("POST", "codex-awsenv/rebuild", _awsenv_rebuild)
    ctx.routes.add("POST", "codex-awsenv/probe", _awsenv_probe)
    ctx.routes.add("POST", "codex-awsenv/toggle", _awsenv_toggle)

    # Seam 3: Codex's AWS launch environment. The client-init hook is the one that matters (both transports read
    # the child's env from the client's _extra_env); the harness hook re-asserts on 0.8's runtime path.
    try:
        ctx.hooks.after(CLIENT_INIT, _after_client_init)
    except Exception as exc:  # noqa: BLE001
        _report_only["codex_awsenv"] = f"{type(exc).__name__}: {exc}"
        ctx.log.warning("codex AWS launch environment is report-only on this host: %s", exc)
    try:
        ctx.hooks.after(CODEX_SPAWN_ENV, _after_codex_spawn_env)
    except Exception as exc:  # noqa: BLE001 - a host without the codex harness module (older than 0.7): the client hook still covers the spawn
        _report_only["codex_spawn_env"] = f"{type(exc).__name__}: {exc}"

    cfg = ctx.config.to_dict()
    if cfg.get("codex_path_enabled"):
        outcome = apply_codex_path(True, cfg.get("codex_path") or None)
        ctx.state["codex_path"] = outcome.get("codex_path")
        if not outcome.get("ok"):
            ctx.log.warning("CODEX_PATH not applied: %s", outcome.get("error"))
    reset = _reset_negatives()
    awsenv_report = None
    if awsenv_enabled():
        try:
            awsenv_report = run_preflight(write=False, reason="activate")
        except Exception as exc:  # noqa: BLE001
            ctx.log.warning("codex awsenv preflight at activation failed: %s", exc)
    ctx.state.update({"hooks": {k: ("report-only" if k in _report_only else "active") for k in ("node_adapters", "plain_binaries", "codex_awsenv", "codex_spawn_env")},
                      "reset_on_activate": [r["backend"] or "kiro" for r in reset], "codex_path_enabled": bool(cfg.get("codex_path_enabled")),
                      "codex_awsenv_enabled": awsenv_enabled()})
    ctx.log.info("Agent Backend Doctor active on KiroCrew %s: discovery hooks %s; cached negatives reset: %s; CODEX_PATH=%s; codex awsenv: %s",
                 getattr(ctx.host, "version", "?"), "active" if not _report_only else _report_only, [r["backend"] or "kiro" for r in reset] or "none",
                 os.environ.get("CODEX_PATH") or "unset",
                 "disabled" if not awsenv_enabled() else (f"{awsenv_report.verdict} — {awsenv_report.detail}" if awsenv_report else "unknown"))


def deactivate(ctx):
    global _ctx, _codex_prev
    if _codex_prev is not None:
        was_set, prev = _codex_prev
        if was_set:
            os.environ["CODEX_PATH"] = prev
        else:
            os.environ.pop("CODEX_PATH", None)
        _codex_prev = None
    _findings.clear()
    _awsenv_sessions.clear()
    _awsenv_last.clear()
    _ctx = None
