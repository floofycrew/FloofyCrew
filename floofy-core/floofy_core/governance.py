"""Governance awareness: read the host's effective policy, attach **warnings**, never gate.

DR-5 / Requirement 11.2: FloofyCrew reads the host's governance for information
only. Where the host would close a seam, FloofyCrew proceeds on the user's consent
through its own path and surfaces a ``governance`` warning per affected mod in
``doctor``, ``status`` and the UI. Nothing in this module returns a refusal; the
one question it asks (Requirement 5.9, "this patch will not survive the next
host update") goes through a :class:`Confirmer` and a *no* is recorded as the
user's decision (``UserDeclined``), not as governance.

What is read, and where the host reads it (0.7.0.5 sources):

* **Policy files** — ``security_policy.json``: the standalone operator's home
  tier at ``<host home>/security_policy.json`` (``platform/governance.py``
  ``_POLICY_HOME_LEAF``/``TIER_HOME``), the ``KIROCREW_SECURITY_POLICY`` env tier
  (``_POLICY_ENV``), and an edition's bundled ceiling (``TIER_BUNDLED``) whose
  location only the edition adapter knows (:class:`GovernanceLocations`).
  Read for ``capabilities.<name>.enabled`` (``capabilities.theme_install`` is a
  default-allow capability gate, ``SCOPE_CATALOG`` row at L1337) and the
  policy-only ``updates`` pins (``UpdatePins`` L1519–1560: ``source``,
  ``min_version``, ``check_command``, ``apply_command``, ``platform_commands``).
* **Config** — ``<host home>/config.json``: the App Kit execution gate
  ``agent.apps_allow_third_party`` (bool) and the grants ``agent.apps_trusted``,
  ``agent.apps_trusted_repositories``, ``agent.apps_trusted_local``
  (``apps/execution.py`` L22–24, L367, L397, L443–467).
* **Admission** — ``<host home>/app_admission.json`` (``apps/admission.py``
  ``_POLICY_FILENAME`` L52; modes ``open``/``enforce`` L55–56; an absent file
  admits) and the capability ceiling ``admission_policy.json`` named at L7.
* **The gateway** — ``GET /api/status`` carries ``governance`` (a health string,
  ``dashboard/state.py`` L6178) when the gateway is reachable over the unix
  socket; the theme route answers 403 when ``theme_install`` is denied
  (``dashboard/handlers/themes.py`` L586–605).

All of it is optional input: a missing file or an unreachable gateway leaves the
corresponding field ``None`` and produces no warning.
"""
from __future__ import annotations

import http.client
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from .gateway import GatewayEndpoint

__all__ = [
    "AlwaysConfirm",
    "CallableConfirmer",
    "Confirmer",
    "GovernanceLocations",
    "GovernanceSnapshot",
    "GovernanceWarning",
    "NeverConfirm",
    "UpdatePins",
    "WARNING_CODES",
    "default_locations",
    "survival_check",
    "survival_prompt",
    "warnings_for",
]

WARNING_CODES = (
    "ThemeInstallClosed",
    "CapabilityDisabled",
    "ThirdPartyAppsDisabled",
    "AppAdmissionEnforced",
    "AppAdmissionBanned",
    "UpdatePinWillWipePatches",
    "UpdateSourcePinned",
    "NoReapplyTrigger",
)

#: The Loader app's name as the host knows it (grants and admission are keyed by it).
LOADER_APP_NAME = "floofycrew"


# --- locations -----------------------------------------------------------------------


@dataclass(frozen=True)
class GovernanceLocations:
    """Where to read governance from; adapters add their edition's bundled ceiling."""

    host_home: Path
    policy_files: tuple[Path, ...] = ()
    config_file: Path | None = None
    admission_files: tuple[Path, ...] = ()

    def with_bundled_ceiling(self, path: Path | None) -> "GovernanceLocations":
        """An edition adapter prepends its packaged ceiling (the highest local tier)."""
        if path is None:
            return self
        return GovernanceLocations(self.host_home, (Path(path), *self.policy_files), self.config_file, self.admission_files)


def default_locations(host_home: Path) -> GovernanceLocations:
    """The edition-neutral locations under a host data home (env tier + home tier + config + admission)."""
    home = Path(host_home)
    policies: list[Path] = []
    env_policy = os.environ.get("KIROCREW_SECURITY_POLICY")
    if env_policy:
        policies.append(Path(env_policy))
    policies.append(home / "security_policy.json")
    return GovernanceLocations(
        host_home=home,
        policy_files=tuple(policies),
        config_file=home / "config.json",
        admission_files=(home / "app_admission.json", home / "admission_policy.json"),
    )


# --- the snapshot ----------------------------------------------------------------------


@dataclass(frozen=True)
class UpdatePins:
    """The policy-only ``updates`` block (``platform/governance.py`` ``UpdatePins``)."""

    source: str = ""
    min_version: str = ""
    check_command: str = ""
    apply_command: str = ""
    platform_commands: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def has_commands(self) -> bool:
        if self.apply_command or self.check_command:
            return True
        return any(isinstance(v, dict) and (v.get("apply_command") or v.get("check_command")) for v in self.platform_commands.values())

    @property
    def pinned(self) -> bool:
        return bool(self.source or self.min_version or self.has_commands)


def _read_json(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"{path}: {exc}")
        return None
    if not isinstance(document, dict):
        errors.append(f"{path}: not a JSON object")
        return None
    return document


@dataclass
class GovernanceSnapshot:
    """What the host's governance currently says — information, never a gate."""

    capabilities: dict[str, bool] = field(default_factory=dict)
    apps_allow_third_party: bool | None = None
    apps_trusted: list[str] = field(default_factory=list)
    apps_trusted_repositories: dict[str, Any] = field(default_factory=dict)
    apps_trusted_local: list[str] = field(default_factory=list)
    admission_mode: str | None = None
    admission_banned: list[str] = field(default_factory=list)
    update_pins: UpdatePins = field(default_factory=UpdatePins)
    gateway_governance: str | None = None
    sources: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def theme_install_enabled(self) -> bool | None:
        """``capabilities.theme_install`` — ``None`` when no policy says (host default: allowed)."""
        return self.capabilities.get("theme_install")

    def app_execution_allowed(self, app_name: str = LOADER_APP_NAME) -> bool | None:
        """Would the App Kit execution gate run ``app_name``? ``None`` when config is unknown."""
        if self.apps_allow_third_party is None and not self.apps_trusted and not self.apps_trusted_local:
            return None
        if self.apps_allow_third_party:
            return True
        return app_name in self.apps_trusted or app_name in self.apps_trusted_local

    @classmethod
    def read(cls, locations: GovernanceLocations, *, endpoint: GatewayEndpoint | None = None) -> "GovernanceSnapshot":
        """Read every location that exists; later policy tiers only narrow (a ``false`` wins)."""
        snapshot = cls()
        for policy_path in locations.policy_files:
            document = _read_json(policy_path, snapshot.errors)
            if document is None:
                continue
            snapshot.sources.append(str(policy_path))
            snapshot._merge_policy(document)
        if locations.config_file is not None:
            config = _read_json(locations.config_file, snapshot.errors)
            if config is not None:
                snapshot.sources.append(str(locations.config_file))
                snapshot._merge_config(config)
        for admission_path in locations.admission_files:
            document = _read_json(admission_path, snapshot.errors)
            if document is None:
                continue
            snapshot.sources.append(str(admission_path))
            snapshot._merge_admission(document)
        if endpoint is not None:
            snapshot._read_gateway(endpoint)
        return snapshot

    def _merge_policy(self, document: dict[str, Any]) -> None:
        raw_caps = document.get("capabilities")
        if isinstance(raw_caps, dict):
            for name, row in raw_caps.items():
                if isinstance(row, dict) and isinstance(row.get("enabled"), bool):
                    enabled = row["enabled"]
                    # a ceiling can only be narrowed by a lower tier: once false, stays false
                    self.capabilities[name] = self.capabilities.get(name, True) and enabled
        raw_updates = document.get("updates")
        if isinstance(raw_updates, dict):
            platform_commands = raw_updates.get("platform_commands")
            self.update_pins = UpdatePins(
                source=str(raw_updates.get("source") or self.update_pins.source),
                min_version=str(raw_updates.get("min_version") or self.update_pins.min_version),
                check_command=str(raw_updates.get("check_command") or self.update_pins.check_command),
                apply_command=str(raw_updates.get("apply_command") or self.update_pins.apply_command),
                platform_commands={k: dict(v) for k, v in platform_commands.items() if isinstance(v, dict)} if isinstance(platform_commands, dict) else dict(self.update_pins.platform_commands),
            )

    def _merge_config(self, config: dict[str, Any]) -> None:
        agent = config.get("agent")
        if not isinstance(agent, dict):
            return
        if isinstance(agent.get("apps_allow_third_party"), bool):
            self.apps_allow_third_party = agent["apps_allow_third_party"]
        if isinstance(agent.get("apps_trusted"), list):
            self.apps_trusted = [str(n) for n in agent["apps_trusted"]]
        if isinstance(agent.get("apps_trusted_repositories"), dict):
            self.apps_trusted_repositories = dict(agent["apps_trusted_repositories"])
        if isinstance(agent.get("apps_trusted_local"), list):
            self.apps_trusted_local = [str(n) for n in agent["apps_trusted_local"]]

    def _merge_admission(self, document: dict[str, Any]) -> None:
        mode = document.get("mode")
        if isinstance(mode, str):
            self.admission_mode = mode
        banned = document.get("banned") or document.get("deny") or []
        if isinstance(banned, list):
            self.admission_banned = sorted({*self.admission_banned, *(str(b) for b in banned)})

    def _read_gateway(self, endpoint: GatewayEndpoint) -> None:
        try:
            response = endpoint.get("/api/status")
        except (OSError, http.client.HTTPException) as exc:
            self.errors.append(f"{endpoint.label}: /api/status unreachable: {exc}")
            return
        if response.status != 200:
            self.errors.append(f"{endpoint.label}: /api/status answered {response.status} (needs the dashboard credential over TCP)")
            return
        try:
            payload = response.json()
        except ValueError:
            return
        if isinstance(payload, dict):
            self.sources.append(f"{endpoint.label}/api/status")
            governance = payload.get("governance")
            if isinstance(governance, str):
                self.gateway_governance = governance

    def to_dict(self) -> dict[str, Any]:
        return {
            "capabilities": dict(self.capabilities),
            "appsAllowThirdParty": self.apps_allow_third_party,
            "appsTrusted": list(self.apps_trusted),
            "appsTrustedLocal": list(self.apps_trusted_local),
            "admissionMode": self.admission_mode,
            "admissionBanned": list(self.admission_banned),
            "updatePins": {
                "source": self.update_pins.source,
                "minVersion": self.update_pins.min_version,
                "hasCommands": self.update_pins.has_commands,
            },
            "gatewayGovernance": self.gateway_governance,
            "sources": list(self.sources),
            "errors": list(self.errors),
        }


# --- warnings --------------------------------------------------------------------------


@dataclass(frozen=True)
class GovernanceWarning:
    """One informational finding; ``affected_targets`` names mods, parts or seams."""

    code: str
    message: str
    affected_targets: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "affectedTargets": list(self.affected_targets)}

    def format(self) -> str:
        who = f" [{', '.join(self.affected_targets)}]" if self.affected_targets else ""
        return f"governance {self.code}{who}: {self.message}"


def warnings_for(
    snapshot: GovernanceSnapshot,
    *,
    theme_targets: Iterable[str] = (),
    app_targets: Iterable[str] = (),
    capabilities_used: Iterable[str] = (),
) -> list[GovernanceWarning]:
    """Translate a snapshot into warnings for the seams a mod set uses (Requirement 11.2).

    ``theme_targets`` are mods with theme parts, ``app_targets`` mods with app
    parts (the Loader itself counts), ``capabilities_used`` other host
    capabilities a mod relies on. Every warning tells the user what the host's
    policy would have said and which path FloofyCrew takes instead.
    """
    warnings: list[GovernanceWarning] = []
    themes = tuple(theme_targets)
    apps = tuple(app_targets)
    if snapshot.theme_install_enabled is False:
        warnings.append(
            GovernanceWarning(
                "ThemeInstallClosed",
                "the host's policy disables capabilities.theme_install, so POST /api/themes/install answers 403; "
                "theme parts are written directly into the themes directory with the same validation rules applied locally (Requirement 2.8)",
                themes,
            )
        )
    for name in capabilities_used:
        if snapshot.capabilities.get(name) is False:
            warnings.append(GovernanceWarning("CapabilityDisabled", f"the host's policy disables capabilities.{name}; a mod relying on it runs on your consent but the host will refuse that route", (name,)))
    allowed = snapshot.app_execution_allowed()
    if allowed is False:
        warnings.append(
            GovernanceWarning(
                "ThirdPartyAppsDisabled",
                f"agent.apps_allow_third_party is off and '{LOADER_APP_NAME}' is not in agent.apps_trusted, so the host refuses to run third-party apps; "
                "the manager offers to record the per-app agent.apps_trusted grant with your confirmation (Requirement 2.3)",
                (LOADER_APP_NAME, *apps),
            )
        )
    if snapshot.admission_mode == "enforce":
        warnings.append(GovernanceWarning("AppAdmissionEnforced", "app_admission.json is in enforce mode: the host admits only apps that pass every active check; FloofyCrew shows the host's verdict, it does not enforce it", (LOADER_APP_NAME, *apps)))
    banned = [name for name in (LOADER_APP_NAME, *apps) if name in snapshot.admission_banned]
    if banned:
        warnings.append(GovernanceWarning("AppAdmissionBanned", f"the host's app admission policy bans {', '.join(banned)}; install proceeds on your consent and the host's verdict is shown as a warning", tuple(banned)))
    if snapshot.update_pins.source or snapshot.update_pins.min_version:
        warnings.append(GovernanceWarning("UpdateSourcePinned", f"the host's policy pins updates (source {snapshot.update_pins.source or '-'}, minimum version {snapshot.update_pins.min_version or '-'}); FloofyCrew never pauses updates, it re-applies after them", ()))
    return warnings


def survival_check(triggers_installed: bool, snapshot: GovernanceSnapshot | None) -> list[GovernanceWarning]:
    """Requirement 5.9: will an overlay patch survive the next host update?"""
    warnings: list[GovernanceWarning] = []
    if not triggers_installed:
        warnings.append(
            GovernanceWarning(
                "NoReapplyTrigger",
                "no re-apply trigger is installed (floofy init installs the hourly user timer and the Loader on_startup hook); "
                "the next host update will land a fresh payload and these patches will not be re-applied until you run floofy apply",
            )
        )
    if snapshot is not None and snapshot.update_pins.has_commands:
        warnings.append(
            GovernanceWarning(
                "UpdatePinWillWipePatches",
                "the host's policy pins operator update commands (updates.apply_command); an update replaces payload files outside FloofyCrew's control, "
                "so patches are wiped until the re-apply trigger runs again",
            )
        )
    return warnings


# --- confirmation ----------------------------------------------------------------------


class Confirmer(Protocol):
    """Asks the user; injected by the CLI (interactive) or the Loader (pre-consented)."""

    def confirm(self, prompt: str, default: bool = False) -> bool: ...


@dataclass(frozen=True)
class AlwaysConfirm:
    """Non-interactive consent already given (the Loader's re-apply on startup)."""

    def confirm(self, prompt: str, default: bool = False) -> bool:  # noqa: ARG002
        return True


@dataclass(frozen=True)
class NeverConfirm:
    def confirm(self, prompt: str, default: bool = False) -> bool:  # noqa: ARG002
        return False


@dataclass(frozen=True)
class CallableConfirmer:
    """Wraps any ``(prompt, default) -> bool`` (the CLI's ``input()`` loop, a test stub)."""

    ask: Callable[[str, bool], bool]

    def confirm(self, prompt: str, default: bool = False) -> bool:
        return bool(self.ask(prompt, default))


def survival_prompt(warnings: Iterable[GovernanceWarning]) -> str:
    lines = [w.message for w in warnings]
    return "These patches may not survive the next host update:\n  - " + "\n  - ".join(lines) + "\nApply anyway?"
