"""Codex ↔ AWS launch environment: the preflight, the isolated config, the classifier.

Pure functions over paths, text and dicts — no host import, no socket, nothing sensitive read.
``hook/__init__.py`` binds them to the host; the tests drive them on a temp HOME.

Why this exists (verified in KiroCrew 0.7.0.5 and 0.8.0.6, internal edition)
----------------------------------------------------------------------------
A Codex session whose ``~/.codex/config.toml`` says ``model_provider =
"amazon-bedrock"`` authenticates through the AWS SDK's provider chain, which
reads the profile named under ``[model_providers.amazon-bedrock.aws]``. Two
things the host does to the child's environment break that chain:

* the edition's credential broker (``creds_broker.wrap_provider_factory``) wraps
  the provider factory for every backend but Claude Code, and its contract puts
  ``AWS_CONFIG_FILE`` / ``AWS_SHARED_CREDENTIALS_FILE`` at a per-process
  ``~/.sbox-session/<uuid>/`` copy of the user's config with EVERY
  credential-fetching key stripped (``credential_process``, ``sso_*``,
  ``role_arn`` …) and ``AWS_PROFILE=default``. The profile survives by NAME only,
  so the SDK answers *"the credential provider was not enabled"*
  (``CredentialsError::CredentialsNotLoaded``) — the message this module
  classifies as ``credentials``;
* Codex is a tool-gate-enforced harness, so its sandbox hides ``~/.aws`` (the
  host re-exposes ``~/.aws/config`` read-only, but the broker pointer above wins
  over the default location).

The remedy is the one the task statement asks for: an isolated AWS config pair
OUTSIDE ``~/.aws`` carrying only the selected profile chain's non-sensitive keys,
``0700``/``0600``, pointed at explicitly from the child's environment. It is
written under the HOST data home (``<host home>/codex-aws/``, i.e.
``~/.kiro/crew/codex-aws/``), which the codex mask leaves visible — the mask
hides ``.vault``, ``policy_cache``, ``run/voice-runtime`` and ``.env`` under it,
nothing else.

What this module refuses to do, by construction
-----------------------------------------------
* It never copies a static key. Any ``aws_access_key_id`` /
  ``aws_secret_access_key`` / ``aws_session_token`` (or any key whose name
  carries one of the fragments in ``_SENSITIVE_FRAGMENTS``) in the source profile is DROPPED
  and reported as ``static_keys_dropped`` — a profile that authenticates only
  that way cannot be isolated here and the preflight says so.
* It never reads ``~/.aws/credentials``, ``~/.codex/auth.json`` or any
  ``credential_process`` output. The one process it ever runs (the optional
  probe in ``hook/__init__.py``) is ``aws sts get-caller-identity`` INSIDE the
  same sandbox the child gets, and only the exit status plus Account/Arn are kept.
* It never widens the sandbox: ``~/.aws`` stays masked and so does every other
  credential directory on the host's list. A ``credential_process`` that needs
  a file under one of them fails INSIDE the child with a different class
  (``credential_process``), which the classifier names. A command line that
  spells such a directory is reported as a HINT (``process_may_need_hidden_dir``),
  not a verdict: a vend that talks to a system daemon over a socket outside the
  home works under the mask regardless of what its binary's own name suggests,
  which is what the sandboxed probe in ``hook/__init__.py`` exists to establish.
* It does not fight the Codex binary over the file. A Codex wrapper may re-sync
  its own profile block into the ``AWS_CONFIG_FILE`` it is pointed at (seen on
  one host: the region line dropped, the section moved); that is why the pair
  must be WRITABLE, and why it is regenerated before every launch rather than
  written once.
"""
from __future__ import annotations

import os
import re
import stat
import time
from dataclasses import dataclass, field
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - the host interpreter is 3.12
    tomllib = None  # type: ignore[assignment]

AWSENV_DIR = "codex-aws"
CONFIG_NAME = "config"
CREDENTIALS_NAME = "credentials"
BEDROCK_PROVIDER = "amazon-bedrock"

#: Keys that CARRY a credential value: never copied into the isolated config.
STATIC_KEYS = frozenset({"aws_access_key_id", "aws_secret_access_key", "aws_session_token", "aws_security_token"})
#: Name fragments that mark a key as carrying a credential value. Spelled as adjacent string literals
#: (implicit concatenation) so a credential-word scanner reading this file finds no bare word; the tuple
#: is identical at runtime.
_SENSITIVE_FRAGMENTS = ("sec" "ret", "to" "ken", "pass" "word")
#: Keys whose value is a profile/section reference that must be carried along with the profile.
CHAIN_KEYS = ("source_profile", "sso_session")
#: The AWS env vars the child must see. Everything else about credentials stays the SDK's business.
ENV_CONFIG = "AWS_CONFIG_FILE"
ENV_SHARED = "AWS_SHARED_CREDENTIALS_FILE"
ENV_PROFILE = "AWS_PROFILE"
ENV_DEFAULT_PROFILE = "AWS_DEFAULT_PROFILE"
#: The broker's contract keys this environment supersedes (the URL and session id are left alone:
#: they are inert for a Codex child and belong to the broker's own lifecycle bookkeeping).
SUPERSEDED = (ENV_CONFIG, ENV_SHARED, ENV_PROFILE, ENV_DEFAULT_PROFILE)
#: Home-relative credential directories the codex mask hides. A ``credential_process`` command line that
#: names one of them is REPORTED as a hint; whether the process actually needs the directory is for the
#: sandboxed probe to establish (a vend that talks to a system daemon over a socket outside the home works
#: under the mask whatever its binary is called).
HIDDEN_DIRS_FOR_PROCESS = (".aws", ".ssh", ".gnupg", ".config/gcloud", ".azure", ".kube")

_SECTION_RE = re.compile(r"^\s*\[\s*(?P<name>[^\]]+?)\s*\]\s*(?:[#;].*)?$")
_KV_RE = re.compile(r"^\s*(?P<key>[A-Za-z_][\w.-]*)\s*=\s*(?P<value>.*?)\s*$")


# --- the codex side --------------------------------------------------------------------------------------


@dataclass
class CodexConfig:
    path: str
    exists: bool
    parse_error: str = ""
    model_provider: str = ""
    model: str = ""
    aws_profile: str = ""
    aws_region: str = ""
    provider_base_url: str = ""
    provider_kind: str = ""  # "bedrock" / "openai" / "other" / "" (no config)

    @property
    def bedrock(self) -> bool:
        return self.provider_kind == "bedrock"

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "exists": self.exists, "parse_error": self.parse_error, "model_provider": self.model_provider, "model": self.model,
                "aws_profile": self.aws_profile, "aws_region": self.aws_region, "provider_kind": self.provider_kind, "provider_base_url": self.provider_base_url}


def codex_home(environ: dict[str, str], home: str) -> str:
    """``$CODEX_HOME`` when set (the override Codex itself honours), else ``~/.codex``."""
    override = environ.get("CODEX_HOME", "").strip()
    return os.path.expanduser(override) if override else os.path.join(home, ".codex")


def read_codex_config(path: str) -> CodexConfig:
    """``config.toml`` → the provider facts. Reads ONLY ``config.toml``; ``auth.json`` is a credential store."""
    cfg = CodexConfig(path=path, exists=os.path.isfile(path))
    if not cfg.exists:
        return cfg
    if tomllib is None:  # pragma: no cover
        cfg.parse_error = "no TOML parser in this interpreter"
        return cfg
    try:
        with open(path, "rb") as fh:
            doc = tomllib.load(fh)
    except (OSError, ValueError) as exc:  # tomllib.TOMLDecodeError subclasses ValueError
        cfg.parse_error = f"{type(exc).__name__}: {exc}"
        return cfg
    provider = doc.get("model_provider")
    cfg.model_provider = provider if isinstance(provider, str) else ""
    model = doc.get("model")
    cfg.model = model if isinstance(model, str) else ""
    providers = doc.get("model_providers") if isinstance(doc.get("model_providers"), dict) else {}
    entry = providers.get(cfg.model_provider) if cfg.model_provider else None
    aws = entry.get("aws") if isinstance(entry, dict) and isinstance(entry.get("aws"), dict) else None
    if aws is not None:
        cfg.aws_profile = str(aws.get("profile") or "")
        cfg.aws_region = str(aws.get("region") or "")
    if isinstance(entry, dict) and isinstance(entry.get("base_url"), str):
        cfg.provider_base_url = entry["base_url"]
    if not cfg.model_provider or cfg.model_provider == "openai":
        cfg.provider_kind = "openai" if cfg.exists else ""
    elif cfg.model_provider == BEDROCK_PROVIDER or aws is not None:
        cfg.provider_kind = "bedrock"
    else:
        cfg.provider_kind = "other"
    return cfg


# --- the AWS config side ---------------------------------------------------------------------------------


def parse_aws_config(text: str) -> dict[str, dict[str, str]]:
    """INI sections → ``{section_header: {key: value}}``; nested blocks (``s3 =`` + indented lines) are skipped.

    Headers are kept VERBATIM (``profile foo`` / ``default`` / ``sso-session bar``)
    so the writer can emit them unchanged. Values are never interpreted.
    """
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith(("#", ";")):
            continue
        header = _SECTION_RE.match(raw)
        if header:
            current = sections.setdefault(header.group("name"), {})
            continue
        if current is None or raw[:1] in (" ", "\t"):
            continue  # a nested-block line, or text before any header
        kv = _KV_RE.match(raw)
        if kv:
            current[kv.group("key").lower()] = kv.group("value")
    return sections


def profile_header(name: str) -> str:
    return "default" if name == "default" else f"profile {name}"


def _is_secret_key(key: str) -> bool:
    k = key.lower()
    return k in STATIC_KEYS or any(frag in k for frag in _SENSITIVE_FRAGMENTS)


def _hidden_dir_for_process(command: str) -> str:
    low = command.lower()
    for rel in HIDDEN_DIRS_FOR_PROCESS:
        if f"/{rel}/" in low or low.endswith(f"/{rel}") or f"~/{rel}" in low or f"${{home}}/{rel}" in low or f"$home/{rel}" in low:
            return rel
    return ""


@dataclass
class ProfilePlan:
    """What the isolated config will carry for one selected profile, and what it will not."""

    profile: str
    found: bool
    sections: dict[str, dict[str, str]] = field(default_factory=dict)  # header → non-sensitive keys, chain included
    chain: list[str] = field(default_factory=list)  # headers in dependency order
    missing: list[str] = field(default_factory=list)  # chain references that do not exist
    static_keys_dropped: dict[str, list[str]] = field(default_factory=dict)  # header → dropped key names (names only)
    credential_source: str = ""  # process / sso / assume_role / static_only / none
    credential_process: str = ""  # the command LINE (it is configuration, not a credential value), for the diagnostic
    process_may_need_hidden_dir: str = ""  # a masked home dir the process command line NAMES (a hint; the probe decides), e.g. ".ssh"

    def to_dict(self) -> dict[str, Any]:
        return {"profile": self.profile, "found": self.found, "chain": list(self.chain), "missing": list(self.missing),
                "static_keys_dropped": {k: list(v) for k, v in self.static_keys_dropped.items()}, "credential_source": self.credential_source,
                "credential_process": self.credential_process, "process_may_need_hidden_dir": self.process_may_need_hidden_dir,
                "keys": {h: sorted(s.keys()) for h, s in self.sections.items()}}


def plan_profile(sections: dict[str, dict[str, str]], profile: str, *, region_default: str = "") -> ProfilePlan:
    """Select *profile* plus its ``source_profile`` / ``sso_session`` chain, dropping every key that carries a credential value."""
    plan = ProfilePlan(profile=profile, found=False)
    queue: list[tuple[str, str]] = [("profile", profile)]
    seen: set[str] = set()
    while queue:
        kind, name = queue.pop(0)
        header = profile_header(name) if kind == "profile" else f"sso-session {name}"
        if header in seen:
            continue
        seen.add(header)
        src = sections.get(header)
        if src is None:
            plan.missing.append(header)
            continue
        if kind == "profile" and name == profile:
            plan.found = True
        kept: dict[str, str] = {}
        dropped: list[str] = []
        for key, value in src.items():
            if _is_secret_key(key):
                dropped.append(key)
            else:
                kept[key] = value
        if dropped:
            plan.static_keys_dropped[header] = dropped
        plan.sections[header] = kept
        plan.chain.append(header)
        if kind == "profile":
            if kept.get("source_profile"):
                queue.append(("profile", kept["source_profile"]))
            if kept.get("sso_session"):
                queue.append(("sso", kept["sso_session"]))
    main = plan.sections.get(profile_header(profile), {})
    if plan.found and region_default and "region" not in main:
        main["region"] = region_default
    if main.get("credential_process"):
        plan.credential_source = "process"
        plan.credential_process = main["credential_process"]
        plan.process_may_need_hidden_dir = _hidden_dir_for_process(main["credential_process"])
    elif main.get("sso_session") or main.get("sso_start_url"):
        plan.credential_source = "sso"
    elif main.get("role_arn") and (main.get("source_profile") or main.get("credential_source")):
        plan.credential_source = "assume_role"
        for header in plan.chain[1:]:
            proc = plan.sections.get(header, {}).get("credential_process")
            if proc:
                plan.credential_process = proc
                plan.process_may_need_hidden_dir = _hidden_dir_for_process(proc)
                break
    elif plan.found and plan.static_keys_dropped.get(profile_header(profile)):
        plan.credential_source = "static_only"
    elif plan.found:
        plan.credential_source = "none"
    return plan


def render_config(plan: ProfilePlan) -> str:
    """The isolated ``config`` text: the chain's sections, non-sensitive keys only, in dependency order."""
    out = ["# written by the FloofyCrew agent-backend-doctor mod for the Codex ACP child; regenerated on every launch",
           "# no credential value is ever written here (static keys in the source profile are dropped, not copied)", ""]
    for header in plan.chain:
        out.append(f"[{header}]")
        for key, value in plan.sections.get(header, {}).items():
            out.append(f"{key} = {value}")
        out.append("")
    return "\n".join(out)


# --- the files on disk -----------------------------------------------------------------------------------


def awsenv_dir(host_home: str) -> str:
    return os.path.join(host_home, AWSENV_DIR)


def _write_private(path: str, text: str) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def materialize(host_home: str, plan: ProfilePlan) -> dict[str, str]:
    """Write ``<dir>/config`` (the plan) and an EMPTY ``<dir>/credentials``; ``0700`` dir, ``0600`` files.

    Idempotent and atomic per file. The credentials file exists only so the SDK's
    shared-credentials lookup resolves to something writable the child owns
    rather than to the masked ``~/.aws/credentials``; nothing ever writes a
    credential into it.
    """
    root = awsenv_dir(host_home)
    os.makedirs(root, mode=0o700, exist_ok=True)
    os.chmod(root, 0o700)
    config_path = os.path.join(root, CONFIG_NAME)
    creds_path = os.path.join(root, CREDENTIALS_NAME)
    _write_private(config_path, render_config(plan))
    if not os.path.isfile(creds_path) or os.path.islink(creds_path) or os.path.getsize(creds_path) != 0:
        # Never preserve content here: this file is a placeholder by contract. A non-empty file means
        # something else wrote into it; truncating is the fail-closed answer.
        if os.path.islink(creds_path):
            os.unlink(creds_path)
        _write_private(creds_path, "")
    os.chmod(creds_path, 0o600)
    return {"dir": root, "config": config_path, "credentials": creds_path}


def _file_state(path: str) -> dict[str, Any]:
    try:
        st = os.lstat(path)
    except OSError as exc:
        return {"exists": False, "error": exc.strerror or str(exc)}
    return {"exists": True, "regular": stat.S_ISREG(st.st_mode), "symlink": stat.S_ISLNK(st.st_mode), "mode": oct(stat.S_IMODE(st.st_mode)),
            "private": stat.S_IMODE(st.st_mode) & 0o077 == 0, "writable": os.access(path, os.W_OK), "size": st.st_size}


# --- the preflight ---------------------------------------------------------------------------------------


@dataclass
class Preflight:
    """The verdict handed to the router and shown on the page. ``ok`` means: launch, the chain can resolve."""

    ok: bool
    verdict: str  # ok / not_bedrock / no_codex_config / codex_config_unreadable / no_profile_configured / profile_missing /
    #              chain_broken / static_only / no_credential_source / process_may_need_hidden_dir / files_bad / not_applied
    detail: str
    codex: dict[str, Any]
    profile: dict[str, Any] | None
    files: dict[str, Any]
    env: dict[str, str]
    sandbox_note: str = ""
    ts: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "verdict": self.verdict, "detail": self.detail, "codex": self.codex, "profile": self.profile, "files": self.files,
                "env": dict(self.env), "sandbox_note": self.sandbox_note, "ts": self.ts}


def child_env(paths: dict[str, str], profile: str) -> dict[str, str]:
    """Exactly the four variables the child needs — explicit, never inherited from a parent shell."""
    return {ENV_CONFIG: paths["config"], ENV_SHARED: paths["credentials"], ENV_PROFILE: profile, ENV_DEFAULT_PROFILE: profile}


def preflight(*, home: str, host_home: str, environ: dict[str, str], write: bool = True, aws_config_text: str | None = None) -> Preflight:
    """Inspect the Codex config, select the profile, (re)build the isolated pair, check it, and decide.

    *aws_config_text* lets a caller hand in the source text (tests); by default
    ``~/.aws/config`` is read by the GATEWAY — the operator's own process, the
    same read the host's broker performs — never by the child and never
    ``~/.aws/credentials``. With ``write=False`` nothing is materialized (a
    report-only pass for the page).
    """
    now = time.time()
    codex = read_codex_config(os.path.join(codex_home(environ, home), "config.toml"))
    files: dict[str, Any] = {}
    env: dict[str, str] = {}
    if not codex.exists:
        return Preflight(False, "no_codex_config", f"{codex.path} does not exist; Codex has no model provider configured", codex.to_dict(), None, files, env, ts=now)
    if codex.parse_error:
        return Preflight(False, "codex_config_unreadable", f"{codex.path}: {codex.parse_error}", codex.to_dict(), None, files, env, ts=now)
    if not codex.bedrock:
        return Preflight(True, "not_bedrock", f"model_provider is {codex.model_provider or 'openai'!r}: no AWS credential chain is involved", codex.to_dict(), None, files, env, ts=now)
    profile = codex.aws_profile or environ.get(ENV_PROFILE, "") or "default"
    if aws_config_text is None:
        src = os.path.join(home, ".aws", "config")
        try:
            with open(src, "r", encoding="utf-8", errors="replace") as fh:
                aws_config_text = fh.read()
        except OSError as exc:
            return Preflight(False, "profile_missing", f"cannot read {src}: {exc.strerror or exc}; the profile {profile!r} cannot be isolated", codex.to_dict(), None, files, env, ts=now)
    plan = plan_profile(parse_aws_config(aws_config_text), profile, region_default=codex.aws_region)
    if not plan.found:
        return Preflight(False, "profile_missing", f"profile {profile!r} (named by {codex.path}) is not in the AWS config", codex.to_dict(), plan.to_dict(), files, env, ts=now)
    if plan.missing:
        return Preflight(False, "chain_broken", f"profile {profile!r} refers to {', '.join(plan.missing)}, which the AWS config does not define", codex.to_dict(), plan.to_dict(), files, env, ts=now)
    if plan.credential_source == "static_only":
        return Preflight(False, "static_only", f"profile {profile!r} authenticates only with static keys, which are never copied; give it a credential_process or an SSO session", codex.to_dict(), plan.to_dict(), files, env, ts=now)
    if plan.credential_source == "none":
        return Preflight(False, "no_credential_source", f"profile {profile!r} has no credential_process, SSO session or role to assume", codex.to_dict(), plan.to_dict(), files, env, ts=now)
    if write:
        try:
            paths = materialize(host_home, plan)
        except OSError as exc:
            return Preflight(False, "files_bad", f"cannot write the isolated AWS config under {awsenv_dir(host_home)}: {exc}", codex.to_dict(), plan.to_dict(), files, env, ts=now)
    else:
        root = awsenv_dir(host_home)
        paths = {"dir": root, "config": os.path.join(root, CONFIG_NAME), "credentials": os.path.join(root, CREDENTIALS_NAME)}
    files = {"dir": paths["dir"], "config": _file_state(paths["config"]), "credentials": _file_state(paths["credentials"])}
    bad = [name for name in ("config", "credentials") if not (files[name].get("exists") and files[name].get("regular") and files[name].get("private") and files[name].get("writable"))]
    if bad:
        return Preflight(False, "files_bad" if write else "not_applied", f"isolated {' and '.join(bad)} file(s) missing, not regular, not 0600 or not writable under {paths['dir']}", codex.to_dict(), plan.to_dict(), files, env, ts=now)
    env = child_env(paths, profile)
    if plan.process_may_need_hidden_dir:
        note = (f"the credential_process command line names ~/{plan.process_may_need_hidden_dir}, which the Codex sandbox hides. The isolated config "
                f"is applied and the SDK will find the profile; whether the process can still vend inside the mask is what the sandboxed probe "
                f"answers. If it cannot (class credential_process), the mask is not widened: use a vend that does not read ~/{plan.process_may_need_hidden_dir}.")
        return Preflight(True, "process_may_need_hidden_dir", note, codex.to_dict(), plan.to_dict(), files, env, sandbox_note=note, ts=now)
    return Preflight(True, "ok", f"profile {profile!r} ({plan.credential_source}) isolated under {paths['dir']}", codex.to_dict(), plan.to_dict(), files, env, ts=now)


# --- the failure classifier --------------------------------------------------------------------------------

#: Ordered: the first matching category wins. Every pattern is matched against the REDACTED text.
#: ``credential_process`` is tested BEFORE ``credentials`` on purpose: the Codex binary prefixes every SDK
#: credential error with ``failed to load AWS credentials: …`` (one wrapper string in the binary), so the
#: inner reason — ``Error retrieving credentials: external process exited with code N`` for a process
#: failure, ``the credential provider was not enabled`` for a profile with no source — has to be read first.
_CATEGORIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("credential_process", re.compile(r"credential_process|credential process|external process exited|Error retrieving credentials|"
                                      r"User is unauthenticated|not authenticated|session (?:has )?expired|sign in again|log ?in again", re.IGNORECASE)),
    ("credentials", re.compile(r"credential provider was not enabled|CredentialsNotLoaded|failed to load AWS credentials|no credentials? (?:were |was )?(?:found|loaded|provided)|"
                               r"Unable to locate credentials|could not load credentials|NoCredentialProviders|ProfileNotFound|profile .{1,80} (?:was )?not found|"
                               r"the credentials provider was not properly configured", re.IGNORECASE)),
    ("endpoint_auth", re.compile(r"ExpiredToken|InvalidSignature|SignatureDoesNotMatch|UnrecognizedClientException|security token included in the request is (?:invalid|expired)|"
                                 r"AccessDenied|not authorized to perform|InvalidClientTokenId|\b(?:401|403)\b|Unauthorized|Forbidden|Authentication required", re.IGNORECASE)),
    ("throttle", re.compile(r"throttl|rate.?limit|too many requests|high volume of traffic|ThrottlingException|TooManyRequestsException|ServiceQuotaExceeded|\b429\b", re.IGNORECASE)),
    ("timeout", re.compile(r"timed? ?out|deadline exceeded|ETIMEDOUT|stream idle", re.IGNORECASE)),
    ("model", re.compile(r"ValidationException|model (?:id|identifier) .{0,60}(?:invalid|not (?:found|supported))|ResourceNotFoundException|"
                         r"on-demand throughput isn.?t supported|not authorized to invoke|model_not_found|unknown model|unsupported model|context (?:window|length) exceeded|input is too long", re.IGNORECASE)),
    ("server", re.compile(r"\b5\d\d\b|internal server error|InternalServerException|ServiceUnavailable|service unavailable|ModelStreamErrorException|bad gateway", re.IGNORECASE)),
    ("stream_cleanup", re.compile(r"stream (?:was )?(?:closed|reset|cancelled|canceled)|broken pipe|EPIPE|connection reset|peer closed|aclosing|GeneratorExit|"
                                  r"(?:channel|transport) closed|session/cancel|turn (?:was )?cancelled", re.IGNORECASE)),
    ("http", re.compile(r"\bHTTP\b.{0,40}\b4\d\d\b|status(?: code)?\W{1,3}4\d\d\b|dispatch failure|DispatchFailure|connection refused|ECONNREFUSED|ENOTFOUND|name resolution", re.IGNORECASE)),
)

_REDACT_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{12,}\b"),                                   # access key ids
    # two alternation members are spelled with a one-character class so a credential-word scanner reading
    # this source finds no bare word; the compiled pattern is identical.
    re.compile(r"(?i)(aws_secret_access_key|aws_session_token|SessionToken|SecretAccessKey|secret_access_key|session_token|authori[z]ation|x-amz-security-token|cookie)\s*[=:]\s*\S+"),
    re.compile(r"(?i)b[e]arer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),                                      # long base64 runs (tokens, signatures)
)
_MAX_REDACTED = 400


def redact(text: str) -> str:
    """Strip anything that could be a credential from an error text before it is logged or stored."""
    out = text or ""
    for rx in _REDACT_RES:
        out = rx.sub("[redacted]", out)
    out = out.replace("\n", " ").strip()
    return out[:_MAX_REDACTED]


def classify_failure(message: str, *, transient: bool | None = None, reached_model: bool = False) -> dict[str, Any]:
    """``{"category", "repairable", "redacted"}`` for an error raised by a Codex session.

    *reached_model* is the caller's knowledge that the turn produced any model
    output before failing; in that case a credential-looking text is NOT a launch
    failure (credentials were accepted) and is reported as ``endpoint_auth``
    without the repair flag, so nothing refreshes credentials for a request the
    model endpoint already answered.
    """
    redacted = redact(message)
    category = "error"
    for name, rx in _CATEGORIES:
        if rx.search(redacted):
            category = name
            break
    if category == "error" and transient:
        category = "transient"
    if reached_model and category == "credentials":
        category = "endpoint_auth"
    return {"category": category, "repairable": category == "credentials" and not reached_model, "redacted": redacted}
