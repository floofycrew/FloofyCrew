#!/usr/bin/env python3
"""Fold the Forge's releases into ``CHANGELOG.md`` from the tags.

The Forge's release commit never opens ``CHANGELOG.md`` (Requirement 7.5): a
Forge release's notes live in ``RELEASE.md`` on its GitHub release, so the one
file every feature branch edits never conflicts with a Forge commit. The next
hand-cut release (``scripts/cut-release.sh``) runs this script first, so every
Forge release since the newest ``## A.B.C — <date>`` heading gets its own
section before the human release's heading goes on top.

The rules, all in :func:`fold_forge_releases` (a pure function over strings, so
a test can drive it without ``git`` or ``gh``):

* a tag counts when its name is ``vA.B.C`` (three integers, nothing else), its
  tagger is the Forge identity (``forge@floofycrew.invalid``; ``FORGE_GIT_EMAIL``
  overrides it, as it does for the Forge itself) and ``A.B.C`` is newer than
  the newest version heading of the changelog — a version that already has a
  heading is never folded twice;
* one ``## A.B.C — <tag date>`` section per such tag, oldest first, inserted
  after the ``## Unreleased`` section's own content and before the first
  existing version heading, so the unreleased bullets stay where they are;
* the section's body is the GitHub release's notes (its ``RELEASE.md``) with the
  title line dropped, the headings nested one level deeper and the ``Supports``
  bullets rendered as the changelog's ``### Supports`` table; a tag without a
  GitHub release gets a one-line note saying so;
* every pre-existing byte of the changelog stays in place — the result is the
  input with one contiguous insertion — and a second run inserts nothing.

Usage:
    python scripts/fold_forge_releases.py              # fold into CHANGELOG.md
    python scripts/fold_forge_releases.py --dry-run    # print the sections that would be inserted, write nothing
    python scripts/fold_forge_releases.py --list       # the tags that would be folded, one per line (no gh calls)

Standard library plus ``git`` (the tags and their taggers: ``git for-each-ref``)
and ``gh`` (the notes: ``gh release view <tag> --json body``). Tags are read
from the local repository: ``git fetch --tags`` first.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "FORGE_EMAIL",
    "Tag",
    "fold_forge_releases",
    "forge_identity",
    "git_tags",
    "insertion_point",
    "newest_heading",
    "pending_tags",
    "release_body",
    "render_section",
    "section_body",
    "supports_table",
    "version_headings",
]

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The release train's identity, as the Forge's own ``identity.py`` has it. The reserved
#: ``.invalid`` TLD (RFC 2606) can never receive mail and can never be mistaken for a person.
FORGE_EMAIL = "forge@floofycrew.invalid"

Version = tuple[int, int, int]

UNRELEASED = re.compile(r"^## Unreleased[ \t]*$", re.MULTILINE)
VERSION_HEADING = re.compile(r"^## (\d+)\.(\d+)\.(\d+)(?=\s|$)", re.MULTILINE)
ANY_H2 = re.compile(r"^## ", re.MULTILINE)
VERSION_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
HEADING_LINE = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*$")
SUPPORTS_HEADING = re.compile(r"^(#{1,6})[ \t]+Supports\b", re.IGNORECASE)
SUPPORTS_BULLET = re.compile(r"^- ([^/\s]+)/([^:\s]+):[ \t]*(.*?)[ \t]*$")
#: The changelog's labels for the editions (the hand-cut sections use the same ones).
EDITION_LABELS = {"internal": "internal", "external": "external (public)"}
EDITION_ORDER = {"internal": 0, "external": 1}


@dataclass(frozen=True)
class Tag:
    """A git tag as the fold sees it: ``(name, tagger email, tag date)``.

    ``tagger`` is the annotated tag's tagger address (the tagged commit's committer
    for a lightweight tag); ``date`` is the matching date as ``YYYY-MM-DD``.
    """

    name: str
    tagger: str
    date: str

    @property
    def version(self) -> Version | None:
        match = VERSION_TAG.match(self.name)
        return (int(match[1]), int(match[2]), int(match[3])) if match else None

    @property
    def email(self) -> str:
        return self.tagger.strip().strip("<>").lower()


def forge_identity() -> str:
    """The Forge's tagger address: ``$FORGE_GIT_EMAIL`` when set, else :data:`FORGE_EMAIL`."""
    return os.environ.get("FORGE_GIT_EMAIL") or FORGE_EMAIL


# --- the changelog ---------------------------------------------------------------------------------------------------


def version_headings(changelog: str) -> list[Version]:
    """Every ``## A.B.C`` heading of the document, in document order."""
    return [(int(m[1]), int(m[2]), int(m[3])) for m in VERSION_HEADING.finditer(changelog)]


def newest_heading(changelog: str) -> Version | None:
    """The highest version that already has a heading, or ``None`` for a changelog without one."""
    headings = version_headings(changelog)
    return max(headings) if headings else None


def insertion_point(changelog: str) -> int:
    """The offset where folded sections go: the first ``## `` line after ``## Unreleased`` (else the end).

    Raises :class:`ValueError` when the document has no ``## Unreleased`` section.
    """
    unreleased = UNRELEASED.search(changelog)
    if unreleased is None:
        raise ValueError("the changelog has no '## Unreleased' section to fold under")
    following = ANY_H2.search(changelog, unreleased.end())
    return following.start() if following else len(changelog)


def pending_tags(changelog: str, tags: Iterable[Tag | tuple[str, str, str]], *, forge_email: str | None = None) -> list[Tag]:
    """The Forge's ``vA.B.C`` tags newer than the newest heading, oldest first (one per version)."""
    wanted = (forge_email or forge_identity()).strip().strip("<>").lower()
    newest = newest_heading(changelog)
    present = set(version_headings(changelog))
    chosen: dict[Version, Tag] = {}
    for item in tags:
        tag = item if isinstance(item, Tag) else Tag(*item)
        version = tag.version
        if version is None or tag.email != wanted:
            continue
        if version in present or (newest is not None and version <= newest) or version in chosen:
            continue
        chosen[version] = tag
    return [chosen[version] for version in sorted(chosen)]


# --- the section ---------------------------------------------------------------------------------------------------


def _is_fence(line: str) -> bool:
    return line.lstrip().startswith(("```", "~~~"))


def _demote(lines: list[str]) -> list[str]:
    """Drop the leading title line and nest every other heading at least one level deeper (``###`` or below)."""
    out: list[str] = []
    fenced = False
    for line in lines:
        if _is_fence(line):
            fenced = not fenced
            out.append(line)
            continue
        match = None if fenced else HEADING_LINE.match(line)
        if match is None:
            out.append(line)
            continue
        if len(match[1]) == 1 and not any(previous.strip() for previous in out):
            continue  # the release title; the `## A.B.C — <date>` heading replaces it
        out.append("#" * min(max(len(match[1]) + 1, 3), 6) + " " + match[2])
    return out


def supports_table(rows: Iterable[tuple[str, str, str]]) -> list[str]:
    """The changelog's ``| Edition | Channel | Host versions |`` table for ``(edition, channel, versions)`` rows."""
    ordered = sorted(rows, key=lambda row: (EDITION_ORDER.get(row[0], len(EDITION_ORDER)), row[0], row[1]))
    out = ["| Edition | Channel | Host versions |", "|---|---|---|"]
    out.extend(f"| {EDITION_LABELS.get(edition, edition)} | {channel} | {versions} |" for edition, channel, versions in ordered)
    return out


def _tabulate_supports(lines: list[str]) -> list[str]:
    """Rewrite a ``Supports`` section's ``- edition/channel: versions`` bullets as the table."""
    out: list[str] = []
    fenced = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if _is_fence(line):
            fenced = not fenced
        heading = None if fenced else SUPPORTS_HEADING.match(line)
        if heading is not None:
            rows: list[tuple[str, str, str]] = []
            rest: list[str] = []
            end = index + 1
            while end < len(lines) and not HEADING_LINE.match(lines[end]) and not _is_fence(lines[end]):
                bullet = SUPPORTS_BULLET.match(lines[end])
                if bullet is not None:
                    rows.append((bullet[1], bullet[2], bullet[3]))
                elif lines[end].strip():
                    rest.append(lines[end])
                end += 1
            if rows:
                out.extend([f"{heading[1]} Supports", "", *supports_table(rows)])
                if rest:
                    out.extend(["", *rest])
                out.append("")
                index = end
                continue
        out.append(line)
        index += 1
    return out


def section_body(body: str | None, tag: Tag) -> str:
    """The release notes as a changelog section body (no trailing newline)."""
    if body is None:
        return f"No GitHub release carries `{tag.name}`; this section was folded from the tag alone."
    if not body.strip():
        return f"The GitHub release for `{tag.name}` carries no notes."
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = _tabulate_supports(_demote(lines))
    return "\n".join(lines).strip("\n")


def render_section(tag: Tag, body: str | None) -> str:
    """``## A.B.C — <tag date>`` plus the body, ending in a blank line so the next heading stands apart."""
    version = tag.version
    if version is None:
        raise ValueError(f"{tag.name!r} is not a vA.B.C tag")
    return f"## {version[0]}.{version[1]}.{version[2]} — {tag.date}\n\n{section_body(body, tag)}\n\n"


def fold_forge_releases(changelog: str, tags: Iterable[Tag | tuple[str, str, str]], bodies: Mapping[str, str | None], *, forge_email: str | None = None) -> str:
    """The changelog with one section per pending Forge tag inserted under ``## Unreleased``.

    ``tags`` are :class:`Tag` values or ``(name, tagger email, date)`` tuples;
    ``bodies`` maps a tag name to its GitHub release notes (``None`` or a
    missing key: no release). The input comes back unchanged when no tag is
    pending; otherwise the result is ``changelog[:pos] + block + changelog[pos:]``
    for ``pos = insertion_point(changelog)``, so every pre-existing byte stays in
    place, and folding the result again inserts nothing.
    """
    pending = pending_tags(changelog, tags, forge_email=forge_email)
    if not pending:
        return changelog
    pos = insertion_point(changelog)
    head, tail = changelog[:pos], changelog[pos:]
    block = "".join(render_section(tag, bodies.get(tag.name)) for tag in pending)
    if head and not head.endswith("\n\n"):
        block = ("\n" if head.endswith("\n") else "\n\n") + block
    if not tail:
        block = block.rstrip("\n") + "\n"
    return head + block + tail


# --- git and gh (the CLI's inputs; the fold itself never runs a command) ----------------------------------------------

_REF_FORMAT = "%00".join(("%(refname:strip=2)", "%(taggeremail)", "%(taggerdate:short)", "%(*committeremail)", "%(*committerdate:short)", "%(committeremail)", "%(committerdate:short)"))


def git_tags(repo: Path) -> list[Tag]:
    """Every ``vA.B.C`` tag of the repository at ``repo`` with its tagger and date.

    An annotated tag reports its tagger; a lightweight tag falls back to the
    tagged commit's committer, which is what the Forge's own commits carry.
    """
    completed = subprocess.run(["git", "-C", str(repo), "for-each-ref", "refs/tags/", f"--format={_REF_FORMAT}"], check=True, capture_output=True, text=True, timeout=120)
    tags: list[Tag] = []
    for line in completed.stdout.splitlines():
        fields = (line.split("\0") + [""] * 7)[:7]
        name, tagger_email, tagger_date, deref_email, deref_date, commit_email, commit_date = fields
        if not VERSION_TAG.match(name):
            continue
        email = (tagger_email or deref_email or commit_email).strip().strip("<>")  # git writes `<address>`
        tags.append(Tag(name, email, tagger_date or deref_date or commit_date))
    return tags


def release_body(tag: str, *, repo: str | None = None, cwd: Path | None = None) -> str | None:
    """The GitHub release notes of ``tag`` through ``gh release view --json body``; ``None`` when no release carries it."""
    command = ["gh", "release", "view", tag, "--json", "body"]
    if repo:
        command += ["--repo", repo]
    env = dict(os.environ, GH_NO_UPDATE_NOTIFIER="1", GH_PROMPT_DISABLED="1")
    try:
        completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=300, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError("gh is not installed; the release notes come from `gh release view`") from exc
    if completed.returncode == 0:
        body = json.loads(completed.stdout or "{}").get("body")
        return body if isinstance(body, str) else ""
    detail = completed.stderr.strip() or f"exit {completed.returncode}"
    if "not found" in detail.lower() or "404" in detail:
        return None
    raise RuntimeError(f"gh release view {tag}: {detail}")


# --- CLI -------------------------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fold the Forge's releases since the changelog's newest heading into CHANGELOG.md, one `## A.B.C — <tag date>` section per Forge tag, under `## Unreleased`.")
    parser.add_argument("--changelog", type=Path, default=REPO_ROOT / "CHANGELOG.md", help="the changelog to fold into (default: CHANGELOG.md at the repository root)")
    parser.add_argument("--repo", help="GitHub repository for gh as OWNER/NAME (default: gh infers it from the git remote)")
    parser.add_argument("--forge-email", help=f"the Forge's tagger address (default: $FORGE_GIT_EMAIL, else {FORGE_EMAIL})")
    parser.add_argument("--dry-run", action="store_true", help="print the sections that would be inserted and write nothing")
    parser.add_argument("--list", action="store_true", help="print the tags that would be folded, one per line, and exit without calling gh")
    args = parser.parse_args(argv)
    changelog_path: Path = args.changelog.resolve()
    try:
        text = changelog_path.read_bytes().decode("utf-8")
        tags = git_tags(changelog_path.parent)
        pending = pending_tags(text, tags, forge_email=args.forge_email)
        if args.list:
            for tag in pending:
                print(tag.name)
            return 0
        if not pending:
            print(f"nothing to fold: no Forge tag is newer than the newest heading of {changelog_path.name}")
            return 0
        pos = insertion_point(text)  # a malformed changelog fails here, before any gh call
        bodies = {tag.name: release_body(tag.name, repo=args.repo, cwd=changelog_path.parent) for tag in pending}
        folded = fold_forge_releases(text, pending, bodies, forge_email=args.forge_email)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)
        print(f"fold_forge_releases: {detail}", file=sys.stderr)
        return 2
    verb = "would fold" if args.dry_run else "folded"
    for tag in pending:
        note = "" if bodies[tag.name] is not None else " (no GitHub release found)"
        print(f"{verb} {tag.name} — {tag.date}{note}")
    if args.dry_run:
        print()
        print(folded[pos : len(folded) - (len(text) - pos)], end="")
        return 0
    changelog_path.write_bytes(folded.encode("utf-8"))
    print(f"wrote {changelog_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
