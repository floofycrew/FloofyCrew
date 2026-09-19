"""Target classification for patch targets and shipped-file destinations.

Two classes of path get special treatment when a mod names them (design "Target
classes", DR-2 rung 6, DR-5):

* **Engineering-rule targets** (Requirement 5.10) — the host's Python sources
  and launcher. Rewriting them in place is never allowed; Python-level behaviour
  goes through the Loader hooks (Requirement 3). ``floofy validate`` reports an
  *error* ``EngineeringRuleTarget``.
* **Governance files** (Requirement 11.4) — the host's policy and trust files.
  FloofyCrew itself never edits them, and a mod that does is *flagged*
  governance-altering (a warning the user may accept per file, audited), never
  refused. ``floofy validate`` reports a *warning* ``GovernanceAltering``.

The core knows only edition-neutral patterns. Edition adapters extend the policy
with their own (the internal package's Python sources, bundle bookkeeping files)
through :meth:`TargetPolicy.extended`, so no internal identifier lives here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache

__all__ = [
    "DEFAULT_TARGET_POLICY",
    "ENGINEERING_RULE_TARGETS",
    "GOVERNANCE_TARGETS",
    "TargetClass",
    "TargetPolicy",
    "glob_to_regex",
    "matches_target",
    "normalize_target",
]

#: Requirement 5.10 — host Python sources and the launcher. ``**`` spans directories;
#: every pattern also matches when preceded by any directory prefix.
ENGINEERING_RULE_TARGETS: tuple[str, ...] = (
    "kiro_crew/**/*.py",
    "kiro_crew/**/*.pyc",
    "bin/kirocrew",
)

#: Requirement 11.4 — governance and trust files FloofyCrew routes around.
GOVERNANCE_TARGETS: tuple[str, ...] = (
    "security_policy.json",
    "admission_policy.json",
    "app_admission.json",
    "denied_commands.json",
    "trust/**",
)


class TargetClass(str, Enum):
    ALLOWED = "allowed"
    ENGINEERING_RULE = "engineering-rule"
    GOVERNANCE = "governance"


@lru_cache(maxsize=512)
def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a glob to a regex anchored at a directory boundary.

    ``**/`` matches zero or more directories, a trailing ``/**`` matches the
    directory itself and everything below it, ``*`` matches within one segment
    and ``?`` one character. Unless the glob starts with ``/`` it also matches
    after any directory prefix, so ``bin/kirocrew`` matches
    ``venv/bin/kirocrew``.
    """
    parts: list[str] = []
    i = 0
    anchored = pattern.startswith("/")
    if anchored:
        pattern = pattern[1:]
    while i < len(pattern):
        if pattern.startswith("/**", i) and i + 3 == len(pattern):
            parts.append("(?:/.*)?")
            i += 3
        elif pattern.startswith("**/", i):
            parts.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            parts.append(".*")
            i += 2
        elif pattern[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(pattern[i]))
            i += 1
    prefix = "^" if anchored else "^(?:.*/)?"
    return re.compile(prefix + "".join(parts) + "$")


def normalize_target(path: str) -> str:
    """POSIX form without ``./`` prefixes, duplicate or trailing slashes."""
    text = path.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    text = re.sub(r"/{2,}", "/", text)
    return text.rstrip("/") if len(text) > 1 else text


def matches_target(path: str, pattern: str) -> bool:
    return glob_to_regex(pattern).match(normalize_target(path)) is not None


@dataclass(frozen=True)
class TargetPolicy:
    """The patterns that classify targets; adapters extend, the core supplies defaults."""

    engineering_rule: tuple[str, ...] = field(default=ENGINEERING_RULE_TARGETS)
    governance: tuple[str, ...] = field(default=GOVERNANCE_TARGETS)

    def extended(self, *, engineering_rule: tuple[str, ...] | list[str] = (), governance: tuple[str, ...] | list[str] = ()) -> "TargetPolicy":
        """A new policy with extra patterns appended (duplicates dropped)."""
        return TargetPolicy(
            tuple(dict.fromkeys((*self.engineering_rule, *engineering_rule))),
            tuple(dict.fromkeys((*self.governance, *governance))),
        )

    def classify(self, path: str) -> TargetClass:
        """Engineering-rule wins over governance; anything else is allowed."""
        if any(matches_target(path, pattern) for pattern in self.engineering_rule):
            return TargetClass.ENGINEERING_RULE
        if any(matches_target(path, pattern) for pattern in self.governance):
            return TargetClass.GOVERNANCE
        return TargetClass.ALLOWED

    def matching_pattern(self, path: str) -> str | None:
        """The first pattern that classifies ``path``, for diagnostics."""
        for pattern in (*self.engineering_rule, *self.governance):
            if matches_target(path, pattern):
                return pattern
        return None


DEFAULT_TARGET_POLICY = TargetPolicy()
