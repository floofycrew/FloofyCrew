"""SemVer 2.0 versions, KiroCrew host version shapes and npm-style ranges.

Standard library only (Requirement 7.2): the CLI and the Loader run on the
host's own interpreter, so no ``packaging`` or ``semver`` distribution is
available. Three pure value types:

* :class:`Version` — a SemVer 2.0.0 version (Requirement 1.2) with the
  precedence rules of SemVer §11 (build metadata ignored).
* :class:`HostVersion` — the shapes KiroCrew hosts print (``X.Y.Z``,
  ``X.Y.Z.N`` internal build number, ``X.Y.ZrcN``, ``X.Y.Z.devN``,
  ``X.Y.Z-insider.N``) with the ``X.Y.Z`` :attr:`HostVersion.base` that
  compatibility ranges are matched against (Requirement 1.3).
* :class:`Range` — an npm-style range: comparators ``>= > < <= =``, hyphen
  ranges, ``^``, ``~``, ``*``/``x`` wildcards, ``||`` unions and whitespace
  as AND (Requirement 1.3, 1.4).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering
from typing import ClassVar, Iterable

__all__ = [
    "Comparator",
    "ComparatorSet",
    "HostVersion",
    "InvalidRange",
    "InvalidVersion",
    "Range",
    "SemVerError",
    "Version",
]


class SemVerError(ValueError):
    """Base class for version and range parse errors."""


class InvalidVersion(SemVerError):
    """The text is not a SemVer 2.0 version (or not a known host version shape)."""


class InvalidRange(SemVerError):
    """The text is not a range this module understands."""


# The official SemVer 2.0.0 grammar (https://semver.org, "Is there a suggested regular expression").
_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+(?P<build>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

_NUMERIC_RE = re.compile(r"^(0|[1-9]\d*)$")


def _identifier_key(identifier: str) -> tuple[int, int, str]:
    """SemVer §11.4: numeric identifiers sort numerically and below alphanumeric ones."""
    if _NUMERIC_RE.match(identifier):
        return (0, int(identifier), "")
    return (1, 0, identifier)


@total_ordering
@dataclass(frozen=True, eq=False)
class Version:
    """A SemVer 2.0.0 version. Ordering follows §11; build metadata is ignored."""

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text: str) -> "Version":
        """Parse a strict SemVer 2.0.0 string; raise :class:`InvalidVersion` otherwise."""
        if not isinstance(text, str):
            raise InvalidVersion(f"version must be a string, got {type(text).__name__}")
        match = _SEMVER_RE.match(text.strip())
        if not match:
            raise InvalidVersion(f"not a SemVer 2.0 version: {text!r}")
        prerelease = match.group("prerelease")
        build = match.group("build")
        return cls(
            int(match.group("major")),
            int(match.group("minor")),
            int(match.group("patch")),
            tuple(prerelease.split(".")) if prerelease else (),
            tuple(build.split(".")) if build else (),
        )

    @classmethod
    def coerce(cls, value: "Version | str") -> "Version":
        """Accept a :class:`Version` or a string."""
        return value if isinstance(value, Version) else cls.parse(value)

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)

    @property
    def base(self) -> "Version":
        """The ``X.Y.Z`` core without prerelease or build."""
        return Version(self.major, self.minor, self.patch)

    @property
    def core(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def precedence_key(self) -> tuple:
        """A sort key implementing SemVer §11 (build metadata excluded)."""
        pre = tuple(_identifier_key(part) for part in self.prerelease)
        # A version without prerelease has higher precedence than one with (§11.3).
        return (self.major, self.minor, self.patch, 0 if self.prerelease else 1, pre)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self.precedence_key() == other.precedence_key()

    def __lt__(self, other: "Version") -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self.precedence_key() < other.precedence_key()

    def __hash__(self) -> int:
        return hash(self.precedence_key())

    def __str__(self) -> str:
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            text += "-" + ".".join(self.prerelease)
        if self.build:
            text += "+" + ".".join(self.build)
        return text

    def __repr__(self) -> str:
        return f"Version({str(self)!r})"

    def satisfies(self, range_text: "Range | str", *, include_prerelease: bool = False) -> bool:
        """``True`` when this version is inside ``range_text`` (see :meth:`Range.contains`)."""
        return Range.coerce(range_text).contains(self, include_prerelease=include_prerelease)


# --- host versions -----------------------------------------------------------------

_HOST_RE = re.compile(
    r"""^
    (?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)
    (?:(?P<pre_tag>a|b|rc)(?P<pre_num>\d+))?      # 0.7.0rc1 (PEP 440 style pre-release)
    (?:\.(?P<build>\d+))?                          # 0.7.0.5  (internal build number)
    (?:\.dev(?P<dev>\d+))?                         # 0.7.0.dev3
    (?:-(?P<tag>[0-9A-Za-z][0-9A-Za-z.-]*))?       # 0.7.0-insider.3
    (?:\+(?P<meta>[0-9A-Za-z][0-9A-Za-z.-]*))?     # +local metadata, ignored
    $""",
    re.VERBOSE,
)

#: Pre-final phases in ascending order; a plain ``X.Y.Z`` (or ``X.Y.Z.N``) is final.
_PHASE_RANK: dict[str, int] = {"dev": 0, "a": 1, "b": 2, "rc": 3, "tag": 4, "final": 5}


@total_ordering
@dataclass(frozen=True, eq=False)
class HostVersion:
    """A KiroCrew host version as printed by either edition (Requirement 1.3).

    ``base`` is the ``X.Y.Z`` :class:`Version` that ``kirocrew.version`` ranges
    are matched against. ``build`` is the internal four-component build number
    (``0.7.0.5`` → 5), ``pre`` a PEP 440 style pre-release (``rc``, 1), ``dev`` a
    dev number and ``tag`` a SemVer-style suffix such as ``insider.3``. Ordering:
    dev < a < b < rc < tagged < final, then the build number.
    """

    major: int
    minor: int
    patch: int
    build: int | None = None
    pre: tuple[str, int] | None = None
    dev: int | None = None
    tag: str | None = None
    text: str = ""

    @classmethod
    def parse(cls, text: str) -> "HostVersion":
        if not isinstance(text, str):
            raise InvalidVersion(f"host version must be a string, got {type(text).__name__}")
        cleaned = text.strip()
        match = _HOST_RE.match(cleaned)
        if not match:
            raise InvalidVersion(f"not a recognised host version: {text!r}")
        pre = (match.group("pre_tag"), int(match.group("pre_num"))) if match.group("pre_tag") else None
        return cls(
            int(match.group("major")),
            int(match.group("minor")),
            int(match.group("patch")),
            int(match.group("build")) if match.group("build") is not None else None,
            pre,
            int(match.group("dev")) if match.group("dev") is not None else None,
            match.group("tag"),
            cleaned,
        )

    @classmethod
    def coerce(cls, value: "HostVersion | str") -> "HostVersion":
        return value if isinstance(value, HostVersion) else cls.parse(value)

    @property
    def base(self) -> Version:
        """The ``X.Y.Z`` core as a :class:`Version` (Requirement 1.3)."""
        return Version(self.major, self.minor, self.patch)

    @property
    def is_final(self) -> bool:
        return self.pre is None and self.dev is None and self.tag is None

    @property
    def tag_name(self) -> str | None:
        """The first label of ``tag`` (``insider`` for ``0.7.0-insider.3``), else ``None``."""
        return self.tag.split(".", 1)[0] if self.tag else None

    def sort_key(self) -> tuple:
        if self.dev is not None:
            phase, number = _PHASE_RANK["dev"], self.dev
        elif self.pre is not None:
            phase, number = _PHASE_RANK[self.pre[0]], self.pre[1]
        elif self.tag is not None:
            phase, number = _PHASE_RANK["tag"], 0
        else:
            phase, number = _PHASE_RANK["final"], 0
        tag_key = tuple(_identifier_key(part) for part in self.tag.split(".")) if self.tag else ()
        return (self.major, self.minor, self.patch, phase, number, tag_key, self.build or 0)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HostVersion):
            return NotImplemented
        return self.sort_key() == other.sort_key()

    def __lt__(self, other: "HostVersion") -> bool:
        if not isinstance(other, HostVersion):
            return NotImplemented
        return self.sort_key() < other.sort_key()

    def __hash__(self) -> int:
        return hash(self.sort_key())

    def __str__(self) -> str:
        if self.text:
            return self.text
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.pre:
            text += f"{self.pre[0]}{self.pre[1]}"
        if self.build is not None:
            text += f".{self.build}"
        if self.dev is not None:
            text += f".dev{self.dev}"
        if self.tag:
            text += f"-{self.tag}"
        return text

    def __repr__(self) -> str:
        return f"HostVersion({str(self)!r})"


# --- ranges ------------------------------------------------------------------------

_OPERATORS = ("<=", ">=", "<", ">", "=", "^", "~")

_PARTIAL = (
    r"v?(?P<major>\d+|[xX*])"
    r"(?:\.(?P<minor>\d+|[xX*])"
    r"(?:\.(?P<patch>\d+|[xX*]))?)?"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)
_COMPARATOR_RE = re.compile(r"(?P<op><=|>=|<|>|=|\^|~)?\s*" + _PARTIAL)
_HYPHEN_RE = re.compile(r"(?P<lower>\S+)\s+-\s+(?P<upper>\S+)")


def _component(text: str | None) -> int | None:
    """A partial-version component: ``None`` when missing or a wildcard."""
    if text is None or text in ("x", "X", "*"):
        return None
    return int(text)


@dataclass(frozen=True)
class _Partial:
    major: int | None
    minor: int | None
    patch: int | None
    prerelease: tuple[str, ...]
    build: tuple[str, ...]

    @classmethod
    def from_match(cls, match: re.Match) -> "_Partial":
        major = _component(match.group("major"))
        minor = _component(match.group("minor")) if major is not None else None
        patch = _component(match.group("patch")) if minor is not None else None
        prerelease = tuple(match.group("prerelease").split(".")) if match.group("prerelease") else ()
        build = tuple(match.group("build").split(".")) if match.group("build") else ()
        if prerelease and patch is None:
            raise InvalidRange(f"a prerelease needs a full X.Y.Z version: {match.group(0)!r}")
        return cls(major, minor, patch, prerelease, build)

    @property
    def is_any(self) -> bool:
        return self.major is None

    def floor(self) -> Version:
        """The lowest version described (missing components as 0)."""
        return Version(self.major or 0, self.minor or 0, self.patch or 0, self.prerelease, self.build)

    def exact(self) -> Version:
        assert self.major is not None and self.minor is not None and self.patch is not None
        return Version(self.major, self.minor, self.patch, self.prerelease, self.build)


@dataclass(frozen=True)
class Comparator:
    """One ``op version`` clause."""

    op: str
    version: Version

    def matches(self, version: Version) -> bool:
        if self.op == "<":
            return version < self.version
        if self.op == "<=":
            return version <= self.version
        if self.op == ">":
            return version > self.version
        if self.op == ">=":
            return version >= self.version
        return version == self.version  # "="

    def __str__(self) -> str:
        return f"{self.op}{self.version}"


@dataclass(frozen=True)
class ComparatorSet:
    """Comparators joined by AND; the empty set matches every version."""

    comparators: tuple[Comparator, ...]

    def contains(self, version: Version, *, include_prerelease: bool = False) -> bool:
        if not all(comparator.matches(version) for comparator in self.comparators):
            return False
        if version.is_prerelease and not include_prerelease:
            # npm rule: a prerelease only satisfies a set that names a prerelease on the same X.Y.Z tuple.
            return any(c.version.is_prerelease and c.version.core == version.core for c in self.comparators)
        return True

    def __str__(self) -> str:
        return " ".join(str(comparator) for comparator in self.comparators) if self.comparators else "*"


@dataclass(frozen=True)
class Range:
    """An npm-style version range; ``sets`` are joined by OR (Requirement 1.3, 1.4)."""

    sets: tuple[ComparatorSet, ...]
    text: str = ""

    ANY: ClassVar["Range"]

    @classmethod
    def parse(cls, text: str) -> "Range":
        """Parse ``text``; raise :class:`InvalidRange` on anything not understood."""
        if not isinstance(text, str):
            raise InvalidRange(f"range must be a string, got {type(text).__name__}")
        sets = tuple(_parse_set(part) for part in text.split("||"))
        return cls(sets, text.strip())

    @classmethod
    def coerce(cls, value: "Range | str") -> "Range":
        return value if isinstance(value, Range) else cls.parse(value)

    def contains(self, version: Version | str, *, include_prerelease: bool = False) -> bool:
        """``True`` when ``version`` satisfies at least one comparator set."""
        candidate = Version.coerce(version)
        return any(s.contains(candidate, include_prerelease=include_prerelease) for s in self.sets)

    def __contains__(self, version: object) -> bool:
        if not isinstance(version, (Version, str)):
            return False
        try:
            return self.contains(version)
        except InvalidVersion:
            return False

    def __str__(self) -> str:
        return " || ".join(str(s) for s in self.sets)

    def __repr__(self) -> str:
        return f"Range({self.text or str(self)!r})"


def _parse_set(text: str) -> ComparatorSet:
    text = text.strip()
    if not text or text in ("*", "x", "X"):
        return ComparatorSet(())
    text = _HYPHEN_RE.sub(_expand_hyphen, text)
    comparators: list[Comparator] = []
    position = 0
    clauses = 0
    for match in _COMPARATOR_RE.finditer(text):
        gap = text[position : match.start()]
        if gap.strip():
            raise InvalidRange(f"unexpected {gap.strip()!r} in range {text!r}")
        comparators.extend(_expand(match.group("op") or "", _Partial.from_match(match)))
        clauses += 1
        position = match.end()
    if text[position:].strip() or clauses == 0:
        raise InvalidRange(f"cannot parse range {text!r}")
    return ComparatorSet(tuple(comparators))


def _expand_hyphen(match: re.Match) -> str:
    """``A - B`` → ``>=A <=B`` (upper partials round up: ``1.2.3 - 2.3`` → ``<2.4.0``)."""
    lower_match = _COMPARATOR_RE.fullmatch(match.group("lower"))
    upper_match = _COMPARATOR_RE.fullmatch(match.group("upper"))
    if not lower_match or not upper_match or lower_match.group("op") or upper_match.group("op"):
        raise InvalidRange(f"bad hyphen range {match.group(0)!r}")
    lower = _Partial.from_match(lower_match)
    upper = _Partial.from_match(upper_match)
    clauses: list[str] = []
    if not lower.is_any:
        clauses.append(f">={lower.floor()}")
    if not upper.is_any:
        if upper.minor is None:
            clauses.append(f"<{upper.major + 1}.0.0")
        elif upper.patch is None:
            clauses.append(f"<{upper.major}.{upper.minor + 1}.0")
        else:
            clauses.append(f"<={upper.exact()}")
    return " ".join(clauses) or "*"


def _expand(op: str, partial: _Partial) -> list[Comparator]:
    """Turn one ``op partial`` clause into primitive comparators (node-semver semantics)."""
    if partial.is_any:
        if op in ("<", ">"):
            return [Comparator("<", Version(0, 0, 0))]  # ``<*`` / ``>*``: nothing can match
        return []  # ``*``, ``>=*``, ``<=*``, ``^*``, ``~*``: every version
    major, minor, patch = partial.major, partial.minor, partial.patch
    assert major is not None
    if op in ("", "="):
        if minor is None:
            return _between(Version(major, 0, 0), Version(major + 1, 0, 0))
        if patch is None:
            return _between(Version(major, minor, 0), Version(major, minor + 1, 0))
        return [Comparator("=", partial.exact())]
    if op == ">":
        if minor is None:
            return [Comparator(">=", Version(major + 1, 0, 0))]
        if patch is None:
            return [Comparator(">=", Version(major, minor + 1, 0))]
        return [Comparator(">", partial.exact())]
    if op == ">=":
        return [Comparator(">=", partial.floor())]
    if op == "<":
        return [Comparator("<", partial.floor())]
    if op == "<=":
        if minor is None:
            return [Comparator("<", Version(major + 1, 0, 0))]
        if patch is None:
            return [Comparator("<", Version(major, minor + 1, 0))]
        return [Comparator("<=", partial.exact())]
    if op == "~":
        if minor is None:
            return _between(Version(major, 0, 0), Version(major + 1, 0, 0))
        return _between(partial.floor(), Version(major, minor + 1, 0))
    if op == "^":
        if minor is None:
            return _between(Version(major, 0, 0), Version(major + 1, 0, 0))
        if patch is None:
            upper = Version(major + 1, 0, 0) if major > 0 else Version(0, minor + 1, 0)
            return _between(Version(major, minor, 0), upper)
        assert patch is not None
        if major > 0:
            upper = Version(major + 1, 0, 0)
        elif minor > 0:
            upper = Version(0, minor + 1, 0)
        else:
            upper = Version(0, 0, patch + 1)
        return _between(partial.exact(), upper)
    raise InvalidRange(f"unknown operator {op!r}")


def _between(lower: Version, upper: Version) -> list[Comparator]:
    return [Comparator(">=", lower), Comparator("<", upper)]


Range.ANY = Range((ComparatorSet(()),), "*")


def max_satisfying(versions: Iterable[Version], range_: Range | str) -> Version | None:
    """The highest version in ``versions`` inside ``range_``, or ``None``."""
    target = Range.coerce(range_)
    matching = [v for v in versions if target.contains(v)]
    return max(matching) if matching else None
