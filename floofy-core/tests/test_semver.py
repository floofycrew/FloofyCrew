"""SemVer, host version and range tests (Requirement 1.2, 1.3, 1.4; task 2.4)."""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from floofy_core.semver import HostVersion, InvalidRange, InvalidVersion, Range, Version, max_satisfying

# --- strategies -------------------------------------------------------------------

_numeric = st.integers(min_value=0, max_value=999).map(str)
_alnum = st.from_regex(r"[0-9]{0,2}[a-zA-Z-][0-9a-zA-Z-]{0,4}", fullmatch=True)
_identifier = st.one_of(_numeric, _alnum)
_component = st.integers(min_value=0, max_value=50)

versions = st.builds(
    Version,
    _component,
    _component,
    _component,
    st.lists(_identifier, max_size=3).map(tuple),
    st.lists(st.from_regex(r"[0-9a-zA-Z-]{1,5}", fullmatch=True), max_size=2).map(tuple),
)
release_versions = st.builds(Version, _component, _component, _component)


def _reference_key(v: Version) -> tuple:
    """An independent transcription of SemVer §11 used to cross-check ``Version`` ordering."""
    def ident(part: str):
        return (0, int(part)) if part.isdigit() else (1, part)

    return (v.major, v.minor, v.patch, 1 if not v.prerelease else 0, tuple(ident(p) for p in v.prerelease))


# --- Version ------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(versions)
def test_parse_str_round_trip(v: Version) -> None:
    """**Validates: Requirements 1.2** — ``parse(str(v))`` reproduces ``v`` and its text."""
    text = str(v)
    parsed = Version.parse(text)
    assert parsed == v and str(parsed) == text
    assert parsed.prerelease == v.prerelease and parsed.build == v.build


@settings(max_examples=200, deadline=None)
@given(versions, versions, versions)
def test_ordering_is_a_strict_total_order(a: Version, b: Version, c: Version) -> None:
    """**Validates: Requirements 1.2** — trichotomy, antisymmetry and transitivity hold and
    agree with an independent transcription of SemVer §11; build metadata never matters."""
    assert sum([a < b, a == b, a > b]) == 1
    if a < b and b < c:
        assert a < c
    assert (a < b) == (_reference_key(a) < _reference_key(b))
    assert (a == b) == (_reference_key(a) == _reference_key(b))
    stripped = Version(a.major, a.minor, a.patch, a.prerelease, ())
    assert stripped == a and hash(stripped) == hash(a)


def test_semver_spec_precedence_chain() -> None:
    chain = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0", "2.0.0", "2.1.0", "2.1.1"]
    parsed = [Version.parse(t) for t in chain]
    assert parsed == sorted(parsed)
    assert all(x < y for x, y in zip(parsed, parsed[1:]))
    assert Version.parse("1.0.0+build.1") == Version.parse("1.0.0+other")


@pytest.mark.parametrize("bad", ["1", "1.2", "01.2.3", "1.2.3.4", "v1.2.3", "1.2.3-", "1.2.3-01", "1.2.3+", "1.2 .3", "a.b.c", ""])
def test_invalid_versions(bad: str) -> None:
    with pytest.raises(InvalidVersion):
        Version.parse(bad)


def test_surrounding_whitespace_is_tolerated() -> None:
    assert Version.parse(" 1.2.3 ") == Version(1, 2, 3)
    assert HostVersion.parse(" 0.7.0.5\n").build == 5


def test_version_helpers() -> None:
    v = Version.parse("1.2.3-rc.1+sha.abc")
    assert v.is_prerelease and v.base == Version(1, 2, 3) and v.core == (1, 2, 3)
    assert repr(v) == "Version('1.2.3-rc.1+sha.abc')"
    assert Version.coerce(v) is v and Version.coerce("1.2.3") == Version(1, 2, 3)
    assert v.satisfies(">=1.2.3-rc.0 <1.3.0") and not v.satisfies("^1.2.3")


# --- HostVersion --------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, base, build, pre, dev, tag",
    [
        ("0.7.0", "0.7.0", None, None, None, None),
        ("0.7.0.5", "0.7.0", 5, None, None, None),
        ("0.7.0rc1", "0.7.0", None, ("rc", 1), None, None),
        ("0.7.0.dev3", "0.7.0", None, None, 3, None),
        ("0.7.0-insider.3", "0.7.0", None, None, None, "insider.3"),
        ("12.3.4.56", "12.3.4", 56, None, None, None),
    ],
)
def test_host_version_shapes(text, base, build, pre, dev, tag) -> None:
    """Requirement 1.3: every host shape yields the X.Y.Z base the range is matched against."""
    host = HostVersion.parse(text)
    assert str(host.base) == base and host.build == build and host.pre == pre and host.dev == dev and host.tag == tag
    assert str(host) == text and repr(host) == f"HostVersion({text!r})"
    assert host.is_final == (pre is None and dev is None and tag is None)


def test_host_version_ordering_and_tag_name() -> None:
    chain = ["0.7.0.dev3", "0.7.0a1", "0.7.0b2", "0.7.0rc1", "0.7.0-insider.3", "0.7.0", "0.7.0.5", "0.7.0.6", "0.7.1"]
    parsed = [HostVersion.parse(t) for t in chain]
    assert parsed == sorted(parsed) and all(x < y for x, y in zip(parsed, parsed[1:]))
    assert HostVersion.parse("0.7.0-insider.3").tag_name == "insider"
    assert HostVersion.parse("0.7.0.5").tag_name is None
    assert HostVersion.parse("0.7.0") == HostVersion.parse("0.7.0+local")


@pytest.mark.parametrize("bad", ["0.7", "v0.7.0", "0.7.0.x", "", "0.7.0-", "0.7 .0"])
def test_invalid_host_versions(bad: str) -> None:
    with pytest.raises(InvalidVersion):
        HostVersion.parse(bad)


# --- Range --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, normalized",
    [
        ("^1.2.3", ">=1.2.3 <2.0.0"), ("^0.2.3", ">=0.2.3 <0.3.0"), ("^0.0.3", ">=0.0.3 <0.0.4"),
        ("^1.2", ">=1.2.0 <2.0.0"), ("^0.2", ">=0.2.0 <0.3.0"), ("^0.0", ">=0.0.0 <0.1.0"), ("^1", ">=1.0.0 <2.0.0"), ("^0", ">=0.0.0 <1.0.0"),
        ("~1.2.3", ">=1.2.3 <1.3.0"), ("~1.2", ">=1.2.0 <1.3.0"), ("~1", ">=1.0.0 <2.0.0"), ("~0.2.3", ">=0.2.3 <0.3.0"),
        ("1.2.3 - 2.3.4", ">=1.2.3 <=2.3.4"), ("1.2 - 2.3.4", ">=1.2.0 <=2.3.4"), ("1.2.3 - 2.3", ">=1.2.3 <2.4.0"), ("1.2.3 - 2", ">=1.2.3 <3.0.0"),
        ("*", "*"), ("", "*"), ("x", "*"), ("1.x", ">=1.0.0 <2.0.0"), ("1.2.x", ">=1.2.0 <1.3.0"), ("1", ">=1.0.0 <2.0.0"), ("1.2", ">=1.2.0 <1.3.0"),
        (">=1.2", ">=1.2.0"), (">1.2", ">=1.3.0"), ("<1.2", "<1.2.0"), ("<=1.2", "<1.3.0"), ("=1.2", ">=1.2.0 <1.3.0"), (">1", ">=2.0.0"), ("<=1", "<2.0.0"),
        (">=0.7.0 <0.9.0", ">=0.7.0 <0.9.0"), ("1.2.7 || >=1.2.9 <2.0.0", "=1.2.7 || >=1.2.9 <2.0.0"), (">= 1.2.3", ">=1.2.3"), ("v1.2.3", "=1.2.3"),
        ("^1.2.3-beta.2", ">=1.2.3-beta.2 <2.0.0"), ("~1.2.3-beta.2", ">=1.2.3-beta.2 <1.3.0"),
    ],
)
def test_range_normalisation(text: str, normalized: str) -> None:
    """Requirement 1.3, 1.4: npm-style syntax expands to the documented primitive comparators."""
    assert str(Range.parse(text)) == normalized


@pytest.mark.parametrize("bad", ["1.2.3 -2.0.0", ">=1.0.0 foo", "1.2.3.4", "^", "abc", ">>1.0.0", "1.x-beta", ">=1.0.0 - 2.0.0", "1.0.0 |"])
def test_invalid_ranges(bad: str) -> None:
    with pytest.raises(InvalidRange):
        Range.parse(bad)


def test_empty_union_members_mean_any() -> None:
    """npm semantics: an empty comparator set is ``*``, so ``1.0.0 ||`` admits everything."""
    assert Range.parse("1.0.0 ||").contains("9.9.9")


def test_range_membership_examples() -> None:
    r = Range.parse("^1.0")
    assert r.contains("1.0.0") and r.contains("1.9.9") and not r.contains("2.0.0") and not r.contains("0.9.9")
    assert Version.parse("1.5.0") in r and "1.5.0" in r and "x" not in r
    assert not r.contains("1.5.0-beta") and r.contains("1.5.0-beta", include_prerelease=True)
    assert Range.parse(">=1.5.0-alpha").contains("1.5.0-beta")  # same tuple names a prerelease
    assert not Range.parse("<2.0.0").contains("2.0.0-rc.1")  # npm: prereleases of the excluded tuple do not sneak in
    assert Range.parse("1.2.7 || >=1.2.9 <2.0.0").contains("1.2.7") and not Range.parse("1.2.7 || >=1.2.9 <2.0.0").contains("1.2.8")
    assert Range.ANY.contains("0.0.0") and Range.ANY.contains("99.0.0-alpha", include_prerelease=True)
    assert not Range.parse("<*").contains("0.0.0")
    assert repr(Range.parse("^1.0")) == "Range('^1.0')"
    assert max_satisfying([Version.parse(t) for t in ("1.0.0", "1.4.0", "2.0.0")], "^1.0") == Version.parse("1.4.0")
    assert max_satisfying([], "*") is None


@settings(max_examples=200, deadline=None)
@given(release_versions)
def test_caret_tilde_and_comparators_contain_their_anchor(v: Version) -> None:
    """**Validates: Requirements 1.3, 1.4** — ``^v``, ``~v``, ``>=v``, ``=v`` and ``v - v``
    all contain ``v``; ``<v`` and ``>v`` never do."""
    for op in ("^", "~", ">=", "=", "<=", ""):
        assert Range.parse(f"{op}{v}").contains(v), op
    assert Range.parse(f"{v} - {v}").contains(v)
    assert not Range.parse(f"<{v}").contains(v) and not Range.parse(f">{v}").contains(v)


def _caret_upper(v: Version) -> Version:
    if v.major > 0:
        return Version(v.major + 1, 0, 0)
    if v.minor > 0:
        return Version(0, v.minor + 1, 0)
    return Version(0, 0, v.patch + 1)


@settings(max_examples=200, deadline=None)
@given(release_versions, release_versions)
def test_caret_and_tilde_semantics(anchor: Version, probe: Version) -> None:
    """**Validates: Requirements 1.3** — ``^`` allows changes that do not touch the left-most
    non-zero component; ``~`` allows patch-level changes only."""
    assert Range.parse(f"^{anchor}").contains(probe) == (anchor <= probe < _caret_upper(anchor))
    assert Range.parse(f"~{anchor}").contains(probe) == (anchor <= probe < Version(anchor.major, anchor.minor + 1, 0))


@settings(max_examples=150, deadline=None)
@given(release_versions, release_versions, release_versions)
def test_hyphen_and_union_semantics(lo: Version, hi: Version, probe: Version) -> None:
    """**Validates: Requirements 1.3** — ``lo - hi`` is the inclusive interval; ``a || b`` is the union."""
    lo, hi = min(lo, hi), max(lo, hi)
    assert Range.parse(f"{lo} - {hi}").contains(probe) == (lo <= probe <= hi)
    union = Range.parse(f"{lo} || {hi}")
    assert union.contains(probe) == (probe == lo or probe == hi)


@settings(max_examples=150, deadline=None)
@given(st.integers(0, 40), st.integers(0, 40), st.integers(0, 40), st.integers(0, 40), st.sampled_from(["", ".dev", "a", "b", "rc", "-insider."]))
def test_host_base_is_the_range_anchor(major: int, minor: int, patch: int, n: int, shape: str) -> None:
    """**Validates: Requirements 1.3** — whatever suffix the host prints, ranges see only X.Y.Z."""
    if shape == "":
        text = f"{major}.{minor}.{patch}" + (f".{n}" if n % 2 else "")
    elif shape == ".dev":
        text = f"{major}.{minor}.{patch}.dev{n}"
    elif shape == "-insider.":
        text = f"{major}.{minor}.{patch}-insider.{n}"
    else:
        text = f"{major}.{minor}.{patch}{shape}{n}"
    host = HostVersion.parse(text)
    assert host.base == Version(major, minor, patch)
    assert Range.parse(f"={major}.{minor}.{patch}").contains(host.base)
    assert HostVersion.parse(str(host)) == host
