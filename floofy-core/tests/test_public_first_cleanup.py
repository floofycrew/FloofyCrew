"""The public-first pathway's removals stay removed (public-first spec, tasks 15.1, 15.2 and 17.1).

Seeded by task 15.1 with the README edit of Requirement 1.4: the marked
internal-only regions of ``README.md`` and ``docs/README.md`` are gone, one
"Internal edition" section stands in their place, and both files pass the
identifier scanner as they are — no marker stripping in between. Task 15.2 adds
the public ``CONTRIBUTING.md`` of Requirements 1.5 and 7.6: the maintainer's
identity, the working convention, the required checks and the Forge's release
shape, all stated there. Task 17.1 adds the Requirement 1.3 and 6.3 removals: the
export tooling is deleted from the tree and the index, nothing but the cut-over
tooling still names it, the public CI runs the hashed scan and no export step,
the release workflow writes no registry pull request, and the scanner's allowlist
stays behind as the one piece of the export pathway that is still a gate's input.

The marker text is assembled at runtime, never spelled: ``floofy-core/`` is part
of the public tree, and a literal marker here would trip the test below that says
no tracked file spells one. Internal identifiers are likewise never spelled, so the
file stays clean under the scan it exercises.
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

#: The working convention's two statements (Requirement 7.6), as ``CONTRIBUTING.md`` leads with them.
CONVENTION_STATEMENTS: tuple[str, ...] = ("Feature work lives on branches", "`main` is always releasable")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True)


def _tracked_files_quoting(needle: str, *, excluding: tuple[str, ...] = MAY_QUOTE_THE_MARKER) -> list[str]:
    """Tracked text files (working-tree content) containing ``needle``, outside the
    paths allowed to quote it. ``git grep`` exits 1 for "no match" and >1 on error."""
    excludes = [f":(exclude){path}" for path in excluding]
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
    decision records spells a marker. The code that used to strip them is gone
    (task 17.1); this test assembles the text at runtime, as that code did."""
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
    """Seeded by task 15.2; task 17.1 added the convention's two statements and the
    export's absence. The public contributor guide exists and states: the maintainer's
    identity, as the two values the clone's own ``.git/config`` pins; the convention
    (feature work on branches, ``main`` always releasable); the hold (removing
    ``publish-enabled``); the two required checks, named as ``ci.yml`` names them; the
    notes' two homes; and that nothing is exported, filtered or reviewed between a
    commit and GitHub (Requirement 1.3), which is what makes the convention the whole
    story."""
    assert CONTRIBUTING.is_file(), "CONTRIBUTING.md is missing from the public tree"
    text = CONTRIBUTING.read_text(encoding="utf-8")
    for statement in CONVENTION_STATEMENTS:
        assert statement in text, f"CONTRIBUTING.md does not state the convention {statement!r}"
    assert re.search(r"nothing is exported", text, flags=re.I), "CONTRIBUTING.md says nothing is exported between a commit and GitHub"
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


# --- Requirement 1.3 / 6.3: the export pathway is gone (task 17.1) --------------------

#: Requirement 1.3's removal list — the exporter, its AI review and their two test
#: modules — plus the one-piece release script the public and overlay halves replaced
#: (task 10.4). None may be in the working tree or in the index.
REMOVED_PATHS: tuple[str, ...] = (
    "scripts/export_public.py",
    "packaging/internal/review_public_export.py",
    "floofy-core/tests/test_public_export.py",
    "packaging/tests/test_review_public_export.py",
    "tools/cut-release.sh",
)

#: The two module names the removal takes away. Tracked files outside the specs, the
#: decision records and the changelog's history may still spell them only where they
#: read the exporter *as committed at the cut-over commit* or assert its absence.
REMOVED_MODULE_NAMES: tuple[str, ...] = ("export_public", "review_public_export")
MAY_NAME_THE_REMOVED_MODULES: tuple[str, ...] = (
    "tools/cutover/",  # keep_list.py, replacements.py, prove.py read the exporter with `git show C0:`
    "packaging/tests-internal/test_cutover_",  # their fixture tests commit a stand-in exporter into fixture histories
    "floofy-core/tests/test_identifier_scan.py",  # asserts the string is absent from both CI workflows
    "floofy-core/tests/test_public_first_cleanup.py",  # this module's own path strings
)

#: The scanner's allowlist for the full-tree public scan. It outlives the export whose
#: name it carries: the CI's de-amazon job and the tests name it by this path.
PUBLIC_ALLOWLIST = "scripts/public-export-allow.txt"
PUBLIC_CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"

#: What the registry pull-request step of ``release.yml`` spelled before task 6.5
#: removed it (DR-9, D-2: the Forge's direct signed commit is the registry's one writer).
REGISTRY_PR_TRACES: tuple[str, ...] = (
    "Stamp the release into the public registry index",
    "REGISTRY_TOKEN",
    "FLOOFYCREW_SIGNING_KEY",
    "pr create",
    "registry_tools build",
)


def _workflow_lines(path: Path) -> list[str]:
    """Every non-blank, non-comment line of a workflow file, stripped — what the runner acts on."""
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _step_lines(path: Path) -> list[str]:
    """The step-level lines of a workflow: names, ``run:`` commands and ``uses:`` actions."""
    return [line.removeprefix("- ") for line in _workflow_lines(path) if line.removeprefix("- ").startswith(("name:", "run:", "uses:"))]


def test_the_export_pathway_files_are_absent_from_the_tree_and_the_index() -> None:
    """Requirement 1.3: the four files are deleted, and ``tools/cut-release.sh`` with them
    (task 10.4). Both the working tree and ``git ls-files`` are asked, so a file that is
    merely unstaged or merely untracked fails too."""
    present = [rel for rel in REMOVED_PATHS if (REPO_ROOT / rel).exists()]
    assert present == [], f"still in the working tree: {present}"
    tracked = _git("ls-files", "--", *REMOVED_PATHS)
    assert tracked.returncode == 0, tracked.stderr
    assert tracked.stdout.split() == [], f"still tracked: {tracked.stdout.split()}"


def test_nothing_but_the_cut_over_tooling_names_the_removed_modules() -> None:
    """Nothing imports, runs or documents the exporter or its review as a live tool. The
    cut-over tooling reads the exporter at the cut-over commit through ``git show`` (so
    the rewrite stays re-runnable from that commit), its fixture tests commit a stand-in
    into fixture histories, and the scanner test asserts the name's absence from the
    CI files: those, and this module, are the only tracked files that may spell the
    names. The changelog is history and is left alone."""
    naming: set[str] = set()
    for name in REMOVED_MODULE_NAMES:
        naming.update(_tracked_files_quoting(name, excluding=(*MAY_QUOTE_THE_MARKER, "CHANGELOG.md")))
    unexpected = sorted(path for path in naming if not path.startswith(MAY_NAME_THE_REMOVED_MODULES))
    assert unexpected == [], f"tracked files that still name the export tooling: {unexpected}"


def test_the_public_ci_runs_the_hashed_scan_with_the_kept_allowlist_and_no_export_step() -> None:
    """Requirements 1.2, 1.3, 6.3 as the public CI shows them: one scanner invocation, in
    hashed mode, against the kept allowlist; no step names, runs or uses an export or a
    review of any kind (``test_identifier_scan.py`` parses the same file job by job; this
    is the removal's own, narrower pin)."""
    steps = _step_lines(PUBLIC_CI)
    scans = [line for line in steps if line.startswith("run:") and "check_no_internal_identifiers.py" in line]
    assert len(scans) == 1, f"expected one scanner step, found {scans}"
    assert "--hashed scripts/internal-identifiers.sha256" in scans[0], scans[0]
    assert f"--allowlist {PUBLIC_ALLOWLIST}" in scans[0], scans[0]
    for line in steps:
        assert not any(name in line for name in REMOVED_MODULE_NAMES), f"ci.yml still runs the export pathway: {line}"
        assert "review" not in line.lower(), f"a review is a gate in ci.yml: {line}"


def test_the_release_workflow_has_no_registry_pull_request_step() -> None:
    """Task 6.5 removed the step that cloned the public registry with ``REGISTRY_TOKEN``,
    rebuilt its index and opened a pull request: the registry has one writer, the Forge's
    direct signed commit (DR-9, D-2). None of the step's traces survive outside comments,
    the only secret the workflow reads is ``GITHUB_TOKEN``, and no step name speaks of the
    registry index or a pull request."""
    lines = _workflow_lines(RELEASE_WORKFLOW)
    text = "\n".join(lines)
    for trace in REGISTRY_PR_TRACES:
        assert trace not in text, f"release.yml still carries the registry pull-request step: {trace!r}"
    assert set(re.findall(r"secrets\.([A-Za-z_]+)", text)) == {"GITHUB_TOKEN"}
    names = [line for line in _step_lines(RELEASE_WORKFLOW) if line.startswith("name:")]
    assert names, "release.yml has no named steps"
    assert not any(re.search(r"registry index|pull request|\bstamp", name, flags=re.I) for name in names), names


def test_the_scanners_allowlist_stays_and_parses_against_the_tree() -> None:
    """``scripts/public-export-allow.txt`` is the one survivor of the export pathway: it is
    the allowlist of the scanner's full-tree public scan, so it stays, tracked, under the
    name the CI and the tests use, and it parses with the scanner's own ``parse_allowlist``
    against this tree (every excused path exists, every pattern is a denylist entry, every
    rule has a reason) — and excuses none of the removed paths."""
    allowlist = REPO_ROOT / PUBLIC_ALLOWLIST
    assert allowlist.is_file(), f"{PUBLIC_ALLOWLIST} is gone"
    assert _git("ls-files", "--error-unmatch", "--", PUBLIC_ALLOWLIST).returncode == 0, f"{PUBLIC_ALLOWLIST} is not tracked"
    denylist = checker.HashList.parse(HASHED_LIST.read_text(encoding="utf-8")) if HASHED_LIST.is_file() else checker.DENYLIST
    rules = checker.parse_allowlist(allowlist.read_text(encoding="utf-8"), root=REPO_ROOT, denylist=denylist)
    assert rules, "the allowlist excuses nothing, so it would be dead weight"
    assert all(rule.reason for rule in rules)
    assert not any(rule.path.rstrip("/") in REMOVED_PATHS for rule in rules), "a rule excuses a removed path"
