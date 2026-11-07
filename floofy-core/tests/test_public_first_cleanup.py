"""The public-first pathway's removals stay removed (public-first spec, tasks 15.1, 15.2 and 17.1).

Seeded by task 15.1 with the README edit of Requirement 1.4: the marked
internal-only regions of ``README.md`` and ``docs/README.md`` are gone, one
"Internal edition" section stands in their place, and both files pass the
identifier scanner as they are — no marker stripping in between. Task 15.2 adds
the public ``CONTRIBUTING.md`` of Requirements 1.5 and 7.6: the maintainer's
identity, the working convention, the required checks and the Forge's release
shape, all stated there. Task 17.1 extends the module with the Requirement 1.3
removals (the marked spot at the end).

The marker text is assembled at runtime, never spelled: ``floofy-core/`` is part
of the public tree, and while the exporter still exists it strips every exported
text file — a literal marker here would be eaten by, or trip, the very stripper
this module says is no longer needed. Internal identifiers are likewise never
spelled, so the file stays clean under the scan it exercises.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_no_internal_identifiers as checker  # noqa: E402

#: The files whose marked internal-only regions the "Internal edition" section replaced.
EDITED_READMES: tuple[str, ...] = ("README.md", "docs/README.md")

#: The stem shared by the three marker forms (``:start`` / ``:end`` / ``:line``),
#: built at runtime — see the module docstring.
MARKER_STEM = ":".join(("floofy", "internal-only"))

#: Tracked paths that may quote the marker when they describe the old pathway:
#: the specs and the decision records. Both live in the overlay, never in the
#: public tree, so the public tree is checked whole.
MAY_QUOTE_THE_MARKER: tuple[str, ...] = (".kiro", "research")

#: The one docs page written for the internal audience alone. It leaves with the
#: overlay (the public history never carries it), so the public docs index must not
#: link it — its index row was the marker line of ``docs/README.md``.
INTERNAL_ONLY_DOC = "migrating-from-the-standalone-patcher.md"

HASHED_LIST = REPO_ROOT / "scripts" / "internal-identifiers.sha256"

CONTRIBUTING = REPO_ROOT / "CONTRIBUTING.md"

#: The maintainer's public git identity (Requirement 1.5): the values the exporter
#: used to write into every public clone as ``PUBLIC_GIT_NAME`` / ``PUBLIC_GIT_EMAIL``.
#: With the exporter gone (task 17.1) the clone's own ``.git/config`` holds them and
#: ``CONTRIBUTING.md`` is where they are spelled, so the test pins them literally.
MAINTAINER_NAME = "Oscar Tseng"
MAINTAINER_EMAIL = "oscar217b@gmail.com"

#: What ``CONTRIBUTING.md`` must name (Requirements 1.5, 7.6): the owner switch whose
#: removal holds every Forge push, the two required status checks, and the two files
#: a Forge release's notes travel through (``RELEASE.md`` on the GitHub release, folded
#: into ``CHANGELOG.md`` by the next hand-cut release).
CONTRIBUTING_NAMES: tuple[str, ...] = ("publish-enabled", "de-amazon", "pytest", "RELEASE.md", "CHANGELOG.md")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True)


def _tracked_files_quoting(needle: str) -> list[str]:
    """Tracked text files (working-tree content) containing ``needle``, outside the
    paths allowed to quote it. ``git grep`` exits 1 for "no match" and >1 on error."""
    excludes = [f":(exclude){path}" for path in MAY_QUOTE_THE_MARKER]
    result = _git("grep", "-I", "-l", "-F", "-e", needle, "--", ".", *excludes)
    if result.returncode > 1:
        raise RuntimeError(f"git grep failed: {result.stderr.strip()}")
    return sorted(line for line in result.stdout.splitlines() if line)


def _internal_edition_section(readme: str) -> str:
    """The body of the README's ``## Internal edition`` section (up to the next ``## ``)."""
    match = re.search(r"^## Internal edition\s*$(.*?)(?=^## |\Z)", readme, flags=re.M | re.S)
    assert match is not None, "README.md has no '## Internal edition' section"
    return match.group(1)


# --- Requirement 1.4: the markers are gone, the section stands in their place -------


def test_no_internal_only_marker_remains_in_the_edited_readmes() -> None:
    """The 2026-10-24 count was 7 marker lines (8 in README.md and 1 in docs/README.md
    by the time of the edit); none may survive, in any of the three forms."""
    for rel in EDITED_READMES:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert MARKER_STEM not in text, f"{rel} still carries an internal-only marker"


def test_no_internal_only_marker_remains_anywhere_in_the_tree() -> None:
    """Stronger than the two READMEs: no tracked file outside the specs and the
    decision records spells a marker. The code that used to strip them (while it
    still exists, until task 17.1) assembles the text at runtime, as this test does."""
    assert _tracked_files_quoting(MARKER_STEM) == []


def test_the_readme_has_one_internal_edition_section_that_names_the_overlay_by_role() -> None:
    """One short section replaces the marked regions: it names the overlay by its
    role (an internal repository vendoring a pinned public release) and sends the
    reader to the overlay's own README for everything else; it names no internal
    identifier (that is the scanner's job, next test)."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    headings = re.findall(r"^#{2,3} Internal edition\s*$", readme, flags=re.M)
    assert len(headings) == 1, f"expected exactly one 'Internal edition' heading, found {len(headings)}"
    section = _internal_edition_section(readme)
    assert "overlay" in section, "the section names the overlay by role"
    assert "pinned public release" in section or "public tag" in section, "the section says the overlay vendors a public release"
    assert "README" in section, "the section points at the overlay's own README for everything else"
    assert len(section.splitlines()) <= 20, "the section is meant to be short"
    assert "#internal-edition" in readme.split("## What is it?", 1)[0], "the table of contents lists the section"
    # the install block it replaced is gone with it: no internal clone step, no link into the overlay's packaging
    assert "git clone <the FloofyCrew package>" not in readme
    assert "packaging/internal/" not in readme, "the public README links no overlay-only path"


def test_the_docs_index_no_longer_links_the_internal_only_page() -> None:
    """The marker line of docs/README.md was the index row of the one internal-audience
    page; the row went with the marker (the overlay's README indexes that page) and
    every remaining relative link in the index resolves inside docs/."""
    index = (REPO_ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert INTERNAL_ONLY_DOC not in index
    links = re.findall(r"\]\(([^)#:]+\.md)\)", index)
    assert links, "the docs index links its pages"
    missing = [link for link in links if not (REPO_ROOT / "docs" / link).is_file()]
    assert missing == [], f"docs/README.md links pages that do not exist: {missing}"


def test_the_edited_readmes_pass_the_scanner_without_marker_stripping() -> None:
    """What CI runs, as a module call: the plain scan where the plain list lives (the
    overlay) and the hashed scan where the hashed list lives (the public tree, and
    here), each over the two files as they are. At least one of the two lists must
    be present, or this test would be vacuous."""
    ran = 0
    if checker.DENYLIST:
        hits = checker.scan(REPO_ROOT, EDITED_READMES)  # default allowlist: none for these files
        assert hits == [], "plain scan: " + "; ".join(hit.format() for hit in hits)
        ran += 1
    if HASHED_LIST.is_file():
        hash_list = checker.HashList.parse(HASHED_LIST.read_text(encoding="utf-8"))
        hits = checker.scan(REPO_ROOT, EDITED_READMES, hash_list=hash_list)
        assert hits == [], "hashed scan: " + "; ".join(hit.format() for hit in hits)
        ran += 1
    assert ran, "neither scripts/internal-identifiers.txt nor scripts/internal-identifiers.sha256 is present"


@pytest.mark.skipif(not HASHED_LIST.is_file(), reason="the hashed identifier list is not in this tree")
def test_the_public_ci_invocation_passes_over_the_edited_readmes() -> None:
    """The exact command shape of the public de-amazon job, narrowed to the two files:
    hashed list plus the public allowlist, exit 0."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_no_internal_identifiers.py"),
         "--paths", *EDITED_READMES, "--allowlist", "scripts/public-export-allow.txt", "--hashed", str(HASHED_LIST)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("OK:"), result.stdout


# --- Requirement 1.5 / 7.6: CONTRIBUTING.md states the identity and the convention ----


def _required_check_names() -> list[str]:
    """The display names of the public CI's jobs — the required status checks on ``main``.
    A job's ``name:`` sits at the four-space indent of ``jobs.<id>``; the workflow's own
    ``name:`` is unindented and a step's is a list item, so neither matches."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    names = re.findall(r"^    name:\s*(\S+)\s*$", workflow, flags=re.M)
    assert names, "ci.yml declares no job display names"
    return names


def test_contributing_names_the_identity_the_convention_and_the_release_shape() -> None:
    """Seeded by task 15.2 (task 17.1 extends it once the exporter is gone). The public
    contributor guide exists and states: the maintainer's identity, as the two values the
    clone's own ``.git/config`` pins; the hold (removing ``publish-enabled``); the two
    required checks, named as ``ci.yml`` names them; the notes' two homes."""
    assert CONTRIBUTING.is_file(), "CONTRIBUTING.md is missing from the public tree"
    text = CONTRIBUTING.read_text(encoding="utf-8")
    for value in (MAINTAINER_NAME, MAINTAINER_EMAIL):
        assert value in text, f"CONTRIBUTING.md does not state the maintainer's identity value {value!r}"
    assert re.search(r"git config user\.name\s+\"" + re.escape(MAINTAINER_NAME) + '"', text), \
        "CONTRIBUTING.md shows the `git config user.name` command with the maintainer's name"
    assert re.search(r"git config user\.email\s+\"" + re.escape(MAINTAINER_EMAIL) + '"', text), \
        "CONTRIBUTING.md shows the `git config user.email` command with the maintainer's address"
    assert ".git/config" in text, "CONTRIBUTING.md says the identity lives in the clone's own .git/config"
    for word in CONTRIBUTING_NAMES:
        assert word in text, f"CONTRIBUTING.md does not mention {word!r}"
    missing = [name for name in _required_check_names() if f"`{name}`" not in text]
    assert missing == [], f"CONTRIBUTING.md does not name the required check(s) {missing} as ci.yml names them"
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "](CONTRIBUTING.md)" in readme, "README.md links CONTRIBUTING.md"


def test_contributing_passes_the_scanner_as_it_is() -> None:
    """``CONTRIBUTING.md`` ships in the public tree, so it is scanned the way CI scans it:
    the plain list where it lives (the overlay), the hashed list where it lives (the
    public tree). At least one list must be present, or the test would be vacuous."""
    ran = 0
    if checker.DENYLIST:
        hits = checker.scan(REPO_ROOT, ("CONTRIBUTING.md",))
        assert hits == [], "plain scan: " + "; ".join(hit.format() for hit in hits)
        ran += 1
    if HASHED_LIST.is_file():
        hash_list = checker.HashList.parse(HASHED_LIST.read_text(encoding="utf-8"))
        hits = checker.scan(REPO_ROOT, ("CONTRIBUTING.md",), hash_list=hash_list)
        assert hits == [], "hashed scan: " + "; ".join(hit.format() for hit in hits)
        ran += 1
    assert ran, "neither scripts/internal-identifiers.txt nor scripts/internal-identifiers.sha256 is present"


# --- Requirement 1.3 / 6.3: the export pathway is gone (task 17.1 adds these) ---------
#
# Task 17.1 fills this in once the cut-over proof (16.3) has used export_public.py
# for the last time:
#   * the Requirement 1.3 paths are absent: scripts/export_public.py,
#     packaging/internal/review_public_export.py, floofy-core/tests/test_public_export.py,
#     packaging/tests/test_review_public_export.py (and tools/cut-release.sh);
#   * CONTRIBUTING.md names the maintainer's identity values and the convention
#     (branches, main always releasable, the publish-enabled hold) — seeded above by
#     task 15.2; 17.1 extends that test;
#   * .github/workflows/ci.yml invokes the scanner with --hashed and runs no export;
#   * .github/workflows/release.yml has no registry pull-request step;
#   * scripts/public-export-allow.txt stays, as the scanner's allowlist.
