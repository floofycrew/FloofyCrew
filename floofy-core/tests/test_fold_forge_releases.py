"""Property 12 — folding the Forge's releases into ``CHANGELOG.md`` (Requirement 7.5, task 10.2).

``scripts/fold_forge_releases.py`` is a pure function over strings, so the test
builds whole changelogs and tag lists at random and checks the fold against an
oracle derived from how it built them, never from the script's own helpers:
which tags are pending (Forge-tagged ``vA.B.C``, strictly newer than the newest
heading, one per version), where the block goes (the first ``## `` line after
``## Unreleased``, else the end) and what every new heading reads.

Left out of the generator because the design leaves it undefined: a changelog
without ``## Unreleased`` (tested separately: ``ValueError``), CRLF changelogs,
two tags of one name with different taggers or dates, fenced code in the
release notes that holds an ``## `` line, and release notes that are only a
title line (the fold drops the title and leaves that section without a body).
"""
from __future__ import annotations

import re
import string
from dataclasses import dataclass
from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from fold_forge_releases import FORGE_EMAIL, Tag, fold_forge_releases, insertion_point, newest_heading, pending_tags, version_headings

Version = tuple[int, int, int]

H2_LINE = re.compile(r"^## .*$", re.MULTILINE)
ALT_FORGE_EMAIL = "release-train@example.org"
HUMAN_EMAILS = ("owner@example.com", "someone@floofycrew.invalid", "<maintainer@example.org>", "forge@example.net")
TITLE_MARK = "§"  # only a release title carries it, so its absence from a section proves the title was dropped
TABLE_HEADER = "| Edition | Channel | Host versions |"
EDITION_LABEL = {"internal": "internal", "external": "external (public)"}

# --- text that can never be mistaken for the document's structure ---------------------------------------------------

_PROSE_CHARS = string.ascii_letters + string.digits + " _`*.,;:()[]/|'\"!?+=-#—…é"
prose = st.tuples(st.text(string.ascii_letters, min_size=1, max_size=2), st.text(_PROSE_CHARS, max_size=40)).map("".join)
bullets = st.builds(lambda head, more: "- " + head + "".join("\n  " + line for line in more), prose, st.lists(prose, max_size=2))
versions = st.tuples(st.integers(0, 3), st.integers(0, 4), st.integers(0, 12))
dates = st.dates(min_value=date(2024, 1, 1), max_value=date(2032, 12, 31)).map(date.isoformat)
host_versions = st.lists(st.sampled_from(["0.7.0.5", "0.8.0.6", "0.10.0.2", "0.7.0rc5", "0.9.0", "1.0.0rc1"]), max_size=3).map(lambda xs: ", ".join(xs) or "-")
supports_rows = st.tuples(st.sampled_from(["internal", "external"]), st.sampled_from(["beta", "stable", "insider"]), host_versions)
_TABLE = "### Supports\n\n" + TABLE_HEADER + "\n|---|---|---|\n| internal | beta | 0.7.0.5 |\n| external (public) | stable | 0.9.0 |"
section_bodies = st.lists(st.one_of(prose, bullets, st.just(_TABLE)), max_size=4).map("\n\n".join)


def _tag_name(version: Version) -> str:
    return "v{}.{}.{}".format(*version)


def _heading(version: Version, day: str) -> str:
    return "## {}.{}.{} — {}".format(*version, day)


def _newer_than(version: Version) -> st.SearchStrategy[Version]:
    a, b, c = version
    return st.one_of(
        st.tuples(st.just(a), st.just(b), st.integers(c + 1, c + 6)),
        st.tuples(st.just(a), st.integers(b + 1, b + 3), st.integers(0, 6)),
        st.tuples(st.integers(a + 1, a + 2), st.integers(0, 3), st.integers(0, 6)),
    )


def _older_than(version: Version) -> st.SearchStrategy[Version] | None:
    a, b, c = version
    options = []
    if c > 0:
        options.append(st.tuples(st.just(a), st.just(b), st.integers(0, c - 1)))
    if b > 0:
        options.append(st.tuples(st.just(a), st.integers(0, b - 1), st.integers(0, 12)))
    if a > 0:
        options.append(st.tuples(st.integers(0, a - 1), st.integers(0, 4), st.integers(0, 12)))
    return st.one_of(*options) if options else None


def _forge_spellings(email: str) -> st.SearchStrategy[str]:
    """The Forge address as git may report it: bare, in angle brackets, any case, padded."""
    local, _, domain = email.partition("@")
    return st.sampled_from([email, f"<{email}>", email.upper(), f" <{local.capitalize()}@{domain.upper()}> "])


def _decoy_names(version: Version) -> st.SearchStrategy[str]:
    """Forge-tagged names that are not exactly ``vA.B.C``: pre-releases, build metadata, mod and label tags."""
    a, b, c = version
    return st.sampled_from([f"v{a}.{b}.{c}-rc1", f"v{a}.{b}.{c}rc2", f"v{a}.{b}.{c}+internal.1", f"V{a}.{b}.{c}", f"v{a}.{b}", f"v{a}.{b}.{c}.1", f"kirocrew-ide-{a}.{b}.{c}", f"agent-backend-router-{a}.{b}.{c}", f"floofycrew-v{a}.{b}.{c}", f"v {a}.{b}.{c}"])


# --- release notes (what `gh release view --json body` returns) -----------------------------------------------------


@dataclass(frozen=True)
class Note:
    kind: str  # missing (no key) | none (key -> None) | blank | notes
    text: str | None
    supports: tuple[tuple[str, str, str], ...]  # the `- edition/channel: versions` bullets of a Supports section


@st.composite
def release_notes(draw: st.DrawFn) -> Note:
    kind = draw(st.sampled_from(["missing", "none", "blank", "notes", "notes", "notes"]))
    if kind in ("missing", "none"):
        return Note(kind, None, ())
    if kind == "blank":
        return Note(kind, draw(st.sampled_from(["", "   ", "\n\n", "\r\n"])), ())
    lines: list[str] = []
    if draw(st.booleans()):
        lines += ["# floofycrew v9.9.9 (nightly) " + TITLE_MARK, ""]
    paragraphs = draw(st.lists(st.one_of(prose, bullets, st.sampled_from(["## Highlights", "### Details", "#### Notes"])), max_size=3))
    has_supports = draw(st.booleans())  # a `## Supports` heading, with or without bullets under it
    supports: tuple[tuple[str, str, str], ...] = tuple(draw(st.lists(supports_rows, max_size=4))) if has_supports else ()
    trailing = draw(st.lists(st.one_of(prose, st.sampled_from(["## Artifacts", "### Checksums"])), max_size=2))
    if not paragraphs and not has_supports and not trailing:
        paragraphs = [draw(prose)]  # notes that are only a title line are undefined by the design (see the module docstring)
    for paragraph in paragraphs:
        lines += [paragraph, ""]
    if has_supports:
        lines += ["## Supports (host versions with `loader: ok` in the matrix)", "", *(f"- {edition}/{channel}: {hosts}" for edition, channel, hosts in supports), ""]
    lines += trailing
    text = "\n".join(lines)
    if draw(st.booleans()):
        text = text.replace("\n", "\r\n")
    return Note(kind, text, supports)


# --- the scenario ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    changelog: str
    pos: int  # where the block must go, from the construction
    existing: tuple[Version, ...]  # document order (descending, as a real changelog is)
    existing_headings: tuple[str, ...]
    tags: tuple[Tag | tuple[str, str, str], ...]
    bodies: dict[str, str | None]
    notes: dict[str, Note]
    pending: tuple[Tag, ...]  # expected: Forge-tagged `vA.B.C` newer than the newest heading, deduped, oldest first
    forge_email: str


@st.composite
def scenarios(draw: st.DrawFn) -> Scenario:
    forge_email = draw(st.sampled_from([FORGE_EMAIL, ALT_FORGE_EMAIL, f"<{FORGE_EMAIL}>"]))
    canonical = forge_email.strip().strip("<>").lower()
    existing = tuple(sorted(draw(st.sets(versions, max_size=5)), reverse=True))
    newest = existing[0] if existing else None

    # The document: title, preamble, `## Unreleased` (bullets or empty), then the sections newest first.
    head = "# Changelog\n\n" + "".join(line + "\n\n" for line in draw(st.lists(prose, max_size=2))) + "## Unreleased"
    unreleased = draw(st.lists(bullets, max_size=3))
    if unreleased:
        head += "\n\n" + "\n".join(unreleased)
    headings = tuple(_heading(version, draw(dates)) for version in existing)
    if existing:
        head += draw(st.sampled_from(["\n", "\n\n", "\n\n\n"]))
        sections = []
        for heading in headings:
            body = draw(section_bodies)
            sections.append(heading + ("\n\n" + body if body else ""))
        tail = "\n\n".join(sections) + "\n"
    else:
        head += draw(st.sampled_from(["", "\n", "\n\n"]))
        tail = ""

    # The tags. Forge-tagged `vA.B.C` newer than the newest heading fold (once per version); nothing else does.
    tags: list[Tag] = []
    pending: dict[Version, Tag] = {}
    names: set[str] = set()

    def add(tag: Tag) -> None:
        if tag.name not in names:
            names.add(tag.name)
            tags.append(tag)

    newer = _newer_than(newest) if newest is not None else versions
    for _ in range(draw(st.integers(0, 4))):
        version = draw(newer)
        if version in pending:
            tags.append(pending[version])  # an exact duplicate: folded once
        else:
            pending[version] = Tag(_tag_name(version), draw(_forge_spellings(canonical)), draw(dates))
            add(pending[version])
    if newest is not None:
        for _ in range(draw(st.integers(0, 2))):  # Forge-tagged, but the version already has a heading
            add(Tag(_tag_name(draw(st.sampled_from(existing))), draw(_forge_spellings(canonical)), draw(dates)))
        older = _older_than(newest)
        for _ in range(draw(st.integers(0, 2)) if older is not None else 0):  # Forge-tagged, but older than the newest heading
            add(Tag(_tag_name(draw(older)), draw(_forge_spellings(canonical)), draw(dates)))
    for _ in range(draw(st.integers(0, 2))):  # a human's `vA.B.C`, even a newer one
        version = draw(newer)
        if version not in pending:
            add(Tag(_tag_name(version), draw(st.sampled_from(HUMAN_EMAILS)), draw(dates)))
    for _ in range(draw(st.integers(0, 3))):  # the Forge's other tags: rc, metadata, mods, labels
        add(Tag(draw(_decoy_names(draw(newer))), draw(_forge_spellings(canonical)), draw(dates)))
    if tags and draw(st.booleans()):
        tags.append(draw(st.sampled_from(tags)))
    ordered = draw(st.permutations(tags))
    as_tuples = draw(st.booleans())

    bodies: dict[str, str | None] = {}
    notes: dict[str, Note] = {}
    for name in sorted(names):
        notes[name] = draw(release_notes())
        if notes[name].kind != "missing":
            bodies[name] = notes[name].text

    return Scenario(
        changelog=head + tail,
        pos=len(head),
        existing=existing,
        existing_headings=headings,
        tags=tuple((tag.name, tag.tagger, tag.date) if as_tuples else tag for tag in ordered),
        bodies=bodies,
        notes=notes,
        pending=tuple(pending[version] for version in sorted(pending)),
        forge_email=forge_email,
    )


# --- Property 12 ------------------------------------------------------------------------------------------------------


# Feature: public-first-pathway, Property 12
@settings(max_examples=150, deadline=None)
@given(scenarios())
def test_fold_inserts_each_pending_forge_tag_once_under_unreleased(scenario: Scenario) -> None:
    """**Validates: Requirements 7.5** — for any changelog with existing ``## A.B.C — date``
    headings and any tag list, the fold inserts exactly one ``## A.B.C — <tag date>`` section per
    Forge-tagged ``vA.B.C`` newer than the newest heading (one per version), oldest first, as one
    contiguous block between ``## Unreleased``'s content and the first existing heading; every
    pre-existing byte stays in place; human, mod, rc and already-present versions insert nothing;
    folding the result again inserts nothing."""
    changelog, pos, pending = scenario.changelog, scenario.pos, scenario.pending
    head, tail = changelog[:pos], changelog[pos:]
    result = fold_forge_releases(changelog, scenario.tags, scenario.bodies, forge_email=scenario.forge_email)

    # (a) one contiguous insertion at the first `## ` line after `## Unreleased`: the original is the result minus the block
    assert insertion_point(changelog) == pos
    assert len(result) >= len(changelog)
    assert result.startswith(head) and result.endswith(tail)
    inserted = result[pos : len(result) - len(tail)]
    assert result == head + inserted + tail

    # (b), (e) exactly one new heading per pending tag, oldest first, carrying the tag date, all of them
    # between `## Unreleased`'s content and the first pre-existing heading
    expected_new = ["## {}.{}.{} — {}".format(*tag.version, tag.date) for tag in pending]
    assert H2_LINE.findall(result) == ["## Unreleased", *expected_new, *scenario.existing_headings]
    assert H2_LINE.findall(inserted) == expected_new
    assert version_headings(result) == [tag.version for tag in pending] + list(scenario.existing)
    assert newest_heading(result) == max([*scenario.existing, *(tag.version for tag in pending)], default=None)

    # (c) human tags, mod tags, rc tags, already-present and older versions insert nothing
    assert pending_tags(changelog, scenario.tags, forge_email=scenario.forge_email) == list(pending)
    if not pending:
        assert result == changelog
        return
    assert len(result) > len(changelog)

    # (d) idempotence: the same inputs against the folded document change nothing
    assert fold_forge_releases(result, scenario.tags, scenario.bodies, forge_email=scenario.forge_email) == result
    assert pending_tags(result, scenario.tags, forge_email=scenario.forge_email) == []

    # the block: a blank line before its first heading and (when something follows) after its last one
    parts = re.split(r"(?m)^(?=## )", inserted)
    assert parts[0].strip() == "" and result[: pos + len(parts[0])].endswith("\n\n")
    assert len(parts) == len(pending) + 1
    if tail:
        assert inserted.endswith("\n\n")
    else:
        assert result.endswith("\n") and not result.endswith("\n\n")
    assert "\r" not in result  # CRLF release notes come out LF like the rest of the document

    # each section: its heading, then the transformed notes (title dropped, headings nested at `###` or below,
    # `- edition/channel: hosts` bullets as the changelog's table) or the one-line note for a tag without notes
    for tag, section, heading in zip(pending, parts[1:], expected_new, strict=True):
        assert section.startswith(heading + "\n\n")
        body = section[len(heading) :]
        note = scenario.notes[tag.name]
        assert TITLE_MARK not in body
        for line in body.splitlines():
            if line.startswith("#"):
                assert re.match(r"^#{3,6} ", line), line  # nested at least one level below the `## A.B.C` heading
        if note.text is None or not note.text.strip():
            lines = [line for line in body.splitlines() if line.strip()]
            assert len(lines) == 1 and tag.name in lines[0], body
            continue
        table_lines = [line for line in body.splitlines() if line.startswith("|")]
        if note.supports:
            assert "\n### Supports\n" in body
            assert table_lines[:2] == [TABLE_HEADER, "|---|---|---|"]
            assert sorted(table_lines[2:]) == sorted(f"| {EDITION_LABEL[edition]} | {channel} | {hosts} |" for edition, channel, hosts in note.supports)
        else:
            assert table_lines == []


# --- examples ---------------------------------------------------------------------------------------------------------

CHANGELOG = """# Changelog

FloofyCrew follows SemVer.

## Unreleased

- **agent-backend-router 0.3.6: a backend that answers on another model is
  caught and excluded.**

## 1.3.11 — 2026-10-24

A Spec session no longer opens with a false note about its backend.

### Supports

| Edition | Channel | Host versions |
|---|---|---|
| internal | beta | 0.7.0.5, 0.7.0.7 |
| external (public) | stable | 0.6.0, 0.7.0 |

## 1.3.10 — 2026-10-20

Older notes.
"""

FORGE_NOTES = """# floofycrew v1.3.12 (nightly)

Commit `0123abc` on `main`, tag `v1.3.12`.

## Supports (host versions with `loader: ok` in the matrix)

- internal/beta: 0.7.0.5, 0.8.0.6
- internal/stable: 0.8.0.9
- external/stable: 0.9.0

## Artifacts

- `floofy.pyz` — 1 bytes, sha256 `aa`
- reproducible across two consecutive builds: True

FloofyCrew is unofficial and ships only its own artifacts; the KiroCrew host is never rebuilt or redistributed (DR-1).
"""


def test_forge_release_notes_become_a_changelog_section() -> None:
    """The Forge's ``RELEASE.md`` (title, ``## Supports`` bullets, ``## Artifacts``) folds into the hand-cut
    sections' shape: ``## A.B.C — <tag date>``, ``### Supports`` as the table, headings one level deeper."""
    tags = [Tag("v1.3.12", f"<{FORGE_EMAIL}>", "2026-10-30"), Tag("v1.3.13", FORGE_EMAIL, "2026-11-02")]
    result = fold_forge_releases(CHANGELOG, tags, {"v1.3.12": FORGE_NOTES}, forge_email=FORGE_EMAIL)
    unreleased_end = CHANGELOG.index("## 1.3.11")
    expected_block = (
        "## 1.3.12 — 2026-10-30\n\n"
        "Commit `0123abc` on `main`, tag `v1.3.12`.\n\n"
        "### Supports\n\n"
        "| Edition | Channel | Host versions |\n|---|---|---|\n"
        "| internal | beta | 0.7.0.5, 0.8.0.6 |\n| internal | stable | 0.8.0.9 |\n| external (public) | stable | 0.9.0 |\n\n"
        "### Artifacts\n\n"
        "- `floofy.pyz` — 1 bytes, sha256 `aa`\n- reproducible across two consecutive builds: True\n\n"
        "FloofyCrew is unofficial and ships only its own artifacts; the KiroCrew host is never rebuilt or redistributed (DR-1).\n\n"
        "## 1.3.13 — 2026-11-02\n\n"
        "No GitHub release carries `v1.3.13`; this section was folded from the tag alone.\n\n"
    )
    assert result == CHANGELOG[:unreleased_end] + expected_block + CHANGELOG[unreleased_end:]
    assert fold_forge_releases(result, tags, {"v1.3.12": FORGE_NOTES}, forge_email=FORGE_EMAIL) == result


def test_nothing_pending_returns_the_document_unchanged() -> None:
    tags = [
        ("v1.3.11", FORGE_EMAIL, "2026-10-24"),  # already has a heading
        ("v1.3.9", FORGE_EMAIL, "2026-10-01"),  # older than the newest heading
        ("v1.3.12", "owner@example.com", "2026-10-30"),  # a human's tag
        ("kirocrew-ide-1.3.12", FORGE_EMAIL, "2026-10-30"),  # a mod tag
        ("v1.3.12-rc1", FORGE_EMAIL, "2026-10-30"),  # a pre-release
    ]
    assert fold_forge_releases(CHANGELOG, tags, {}, forge_email=FORGE_EMAIL) == CHANGELOG
    assert pending_tags(CHANGELOG, tags, forge_email=FORGE_EMAIL) == []


def test_a_changelog_without_unreleased_cannot_be_folded_into() -> None:
    headless = CHANGELOG.replace("## Unreleased\n", "## Pending\n")
    with pytest.raises(ValueError, match="Unreleased"):
        insertion_point(headless)
    with pytest.raises(ValueError, match="Unreleased"):
        fold_forge_releases(headless, [("v1.3.12", FORGE_EMAIL, "2026-10-30")], {}, forge_email=FORGE_EMAIL)


def test_forge_identity_defaults_to_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    tags = [("v1.3.12", FORGE_EMAIL, "2026-10-30"), ("v1.3.13", ALT_FORGE_EMAIL, "2026-11-02")]
    monkeypatch.delenv("FORGE_GIT_EMAIL", raising=False)
    assert [tag.name for tag in pending_tags(CHANGELOG, tags)] == ["v1.3.12"]
    monkeypatch.setenv("FORGE_GIT_EMAIL", ALT_FORGE_EMAIL)
    assert [tag.name for tag in pending_tags(CHANGELOG, tags)] == ["v1.3.13"]
    assert H2_LINE.findall(fold_forge_releases(CHANGELOG, tags, {}))[1] == "## 1.3.13 — 2026-11-02"
