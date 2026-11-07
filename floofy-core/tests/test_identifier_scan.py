"""Property 1 — hashed scanning equals plain scanning (``scripts/check_no_internal_identifiers.py``; task 2.3).

# Feature: public-first-pathway, Property 1

The public tree carries ``scripts/internal-identifiers.sha256`` instead of the
plain identifier list, so its CI gate runs the scanner in hashed mode
(Requirement 1.2). This file checks that the hashed scan is the plain scan: for
any denylist and any text, ``scan_text_hashed`` reports exactly the ``Hit``
objects ``scan_text`` reports, in the same order.

Unlike ``test_de_amazon.py`` the data here never comes from the real denylist:
every example generates its own entries, so the property also runs in the
public tree, where ``scripts/internal-identifiers.txt`` does not exist.

Generator domain (design "Property 1"):

* a denylist of 1–8 **distinct** lowercase entries over ``[a-z0-9._-]``, length
  4–14. Distinct, because a denylist is a set of entries: a repeated plain
  entry makes ``scan_text`` report the same hit twice while the hashed list
  records one digest for it (noted while writing ``tools/hash-identifiers.py``,
  task 2.2); the real list has no duplicates and the design's statement is over
  entries, not lines of a file.
* a text of 0–12 lines, joined by ``\\n`` or ``\\r\\n``, each line a concatenation
  of noise (letters, digits, every ASCII punctuation character including the
  scanner's ``BREAK_CHARS``, tabs, a few non-ASCII letters) and denylist entries
  at random casing dropped into realistic contexts: bare, inside URLs and
  paths, glued into longer tokens, wrapped in quotes, brackets and other break
  characters, twice on one line.

The examples at the end (task 2.4) cover the gate around the property: the
public ``ci.yml`` runs the scanner in hashed mode and no export, a stale
``.sha256`` fails ``tools/hash-identifiers.py --check``, a planted term in a
fixture tree is caught through the CLI, and a hashed scan of a 10 MB fixture
tree finishes inside a stated budget. They too generate their own entries.
"""
from __future__ import annotations

import random
import string
import subprocess
import sys
import time
from pathlib import Path

import pytest
from hypothesis import event, given, settings
from hypothesis import strategies as st

import check_no_internal_identifiers as checker

PATH = "f.txt"

# --- strategies ------------------------------------------------------------------------------------------

#: The design's entry alphabet: an identifier (hostname, package, account, handle).
ENTRY_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789._-"

#: Filler. ``string.punctuation`` covers every ``BREAK_CHARS`` character as well as
#: the run characters an identifier may carry (``/ : @ # ~ +``). The non-ASCII tail
#: includes U+212A KELVIN SIGN, whose ``lower()`` is the ASCII ``k``: a line can
#: spell an entry only after lowercasing, which both modes do.
NOISE_ALPHABET = string.ascii_letters + string.digits + string.punctuation + " \t" + "éÉüÜßÅ日本\u212a"

#: How an embedded entry is recased; ``mask`` flips individual characters.
CASINGS = ("lower", "upper", "swapcase", "title", "mask")

#: Where an embedded entry lands on its line (``{e}`` is the recased entry).
CONTEXTS = (
    "{e}",
    " {e} ",
    "https://{e}/index.html",
    "https://www.{e}.example/a?b={e}",
    "/opt/{e}/bin/{e}.sh",
    "pre{e}post",
    "{e}0123",
    "pre-{e}_post.{e}",
    '"{e}"',
    "'{e}'",
    "({e})",
    "[{e}]",
    "<{e}>",
    "{{{e}}}",
    "key={e};",
    "{e},{e}",
    "*{e}*",
    "a|{e}|b",
    "{e}\t{e}",
    "#{e}",
    "@{e}:",
    "{e}\\n",
    "{e}!?{e}",
    "`{e}`",
    "^{e}$",
    "&{e}%",
)

entries = st.text(alphabet=ENTRY_ALPHABET, min_size=4, max_size=14)
denylists = st.lists(entries, min_size=1, max_size=8, unique=True)
noise = st.text(alphabet=NOISE_ALPHABET, min_size=0, max_size=20)
line_terminators = st.sampled_from(("\n", "\r\n"))


def _recase(entry: str, casing: str, mask: int) -> str:
    if casing == "mask":
        return "".join(ch.upper() if mask >> i & 1 else ch for i, ch in enumerate(entry))
    return getattr(entry, casing)()


def _embed(entry: str, casing: str, mask: int, context: str) -> str:
    return context.format(e=_recase(entry, casing, mask))


@st.composite
def scan_cases(draw: st.DrawFn) -> tuple[list[str], str]:
    """A ``(denylist, text)`` pair: distinct in-domain entries and a text that embeds some of them."""
    denylist = draw(denylists)
    embedded = st.builds(
        _embed,
        st.sampled_from(denylist),
        st.sampled_from(CASINGS),
        st.integers(min_value=0, max_value=2**14 - 1),
        st.sampled_from(CONTEXTS),
    )
    piece = st.one_of(noise, embedded)
    lines = draw(st.lists(st.lists(piece, max_size=6).map("".join), max_size=12))
    terminator = draw(line_terminators)
    text = terminator.join(lines) + draw(st.sampled_from(("", terminator)))
    return denylist, text


# --- Property 1 ------------------------------------------------------------------------------------------


# Feature: public-first-pathway, Property 1
@settings(max_examples=200, deadline=None)
@given(case=scan_cases())
def test_property_hashed_scanning_equals_plain_scanning(case: tuple[list[str], str]) -> None:
    """**Validates: Requirements 1.2** — for any denylist of distinct lowercase
    ``[a-z0-9._-]`` entries (length ≥ 4) and any text embedding a random subset of
    them at random casing among random filler, ``scan_text_hashed`` over
    ``hash_list(denylist)`` returns the same ``Hit`` list as ``scan_text`` over the
    plain denylist: the same ``(line, pattern)`` pairs, the same path and stripped
    line text, in the same order (list order within a line)."""
    denylist, text = case
    hashed = checker.HashList.parse(checker.hash_list(denylist))
    plain_hits = checker.scan_text(PATH, text, denylist)
    event("hits" if plain_hits else "no hits")
    assert checker.scan_text_hashed(PATH, text, hashed) == plain_hits


# --- sanity example ---------------------------------------------------------------------------------------


def test_both_modes_report_the_planted_hits_in_list_order() -> None:
    """A fixed case with a known answer, so the property above is not comparing two
    empty lists: substring entries, break-character neighbours, random casing, a
    repeated entry on one line (one hit), and the raw ``.sha256`` lines accepted in
    place of a parsed ``HashList``."""
    denylist = ("zebra.host", "host", "a-1_b.c", "....")
    text = "\n".join(
        [
            "clean line",
            'url = "https://Zebra.Host/index.html"; path=/opt/HOST/bin',
            "(A-1_B.C) <zebra.host> ZEBRA.HOST",
            "dots.... and -. and more....",
        ]
    )
    expected = [
        (2, "zebra.host"),
        (2, "host"),
        (3, "zebra.host"),
        (3, "host"),
        (3, "a-1_b.c"),
        (4, "...."),
    ]
    plain_hits = checker.scan_text(PATH, text, denylist)
    assert [(hit.line, hit.pattern) for hit in plain_hits] == expected
    lines = list(checker.hash_list(denylist))
    assert checker.scan_text_hashed(PATH, text, checker.HashList.parse(lines)) == plain_hits
    assert checker.scan_text_hashed(PATH, text, lines) == plain_hits
    assert checker.scan_text_hashed(PATH, text, "\n".join(lines)) == plain_hits


# --- the CI gate around the property (task 2.4; Requirement 1.2, 6.3) ------------------------------------
#
# These examples read the repository as it is: the public workflow, the staged overlay
# workflow, the committed hashed list and the overlay's hash tool. What the public tree
# does not carry (the plain list, ``tools/``, the staged overlay files) makes the test
# that needs it skip there, as ``test_de_amazon.py`` does; the workflow and the hashed
# list are public files, so those assertions run on both sides of the cut-over.

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNER = REPO_ROOT / "scripts" / "check_no_internal_identifiers.py"
HASH_TOOL = REPO_ROOT / "tools" / "hash-identifiers.py"
PLAIN_LIST = REPO_ROOT / "scripts" / "internal-identifiers.txt"
HASHED_LIST = REPO_ROOT / "scripts" / "internal-identifiers.sha256"
PUBLIC_CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
#: Staged here until the cut-over moves it to the overlay root (tasks 15.5, 16.5).
OVERLAY_CI = REPO_ROOT / "packaging" / "internal" / "overlay" / ".github" / "workflows" / "ci.yml"

#: The one command the design gives the public ``de-amazon`` job (design "Public CI
#: identifier gate"); the test wants it verbatim, not a paraphrase.
DE_AMAZON_COMMAND = (
    "python scripts/check_no_internal_identifiers.py --paths . "
    "--allowlist scripts/public-export-allow.txt --hashed scripts/internal-identifiers.sha256"
)

#: Entries for the fixture trees: in the hashed-mode domain, and prefixed so that no
#: word of the generated text can spell one by accident (nothing below starts with
#: ``zq``; hex digits have neither letter).
FIXTURE_ENTRY_PREFIX = "zq"

#: The time a hashed scan of the 10 MB fixture tree may take, with headroom: measured
#: at 3.3 s on the CLI's default 8 processes and 9.6 s on one (a 48-core desk,
#: Python 3.12), so the budget is ~3x the serial time there and leaves room for a
#: slower, smaller CI runner. A pathological regression (minutes, the naive
#: every-substring hashing the scanner's module comment rules out) fails this;
#: ordinary variance does not.
SCAN_BUDGET_SECONDS = 30.0


def _workflow_jobs(text: str) -> dict[str, str]:
    """``{job id: block}`` of a workflow file's ``jobs:`` mapping, without a YAML library.

    PyYAML is not a dependency of this repository, and the workflow files are plain
    two-space-indented mappings: a job starts at a line indented by exactly two spaces
    ending in ``:``, its block is every deeper line (comments and blanks included) up
    to the next job or the next top-level key.
    """
    jobs: dict[str, list[str]] = {}
    current: str | None = None
    in_jobs = False
    for line in text.splitlines():
        if not in_jobs:
            in_jobs = line.rstrip() == "jobs:"
            continue
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if stripped and not stripped.startswith("#") and indent == 0:
            break  # another top-level key ends the mapping
        if stripped and not stripped.startswith("#") and indent == 2 and stripped.endswith(":"):
            current = stripped[:-1]
            jobs[current] = []
        elif current is not None:
            jobs[current].append(line)
    return {job: "\n".join(block) for job, block in jobs.items()}


def _run_lines(block: str) -> list[str]:
    """Every non-blank, non-comment line of a job block, stripped of indentation and of
    a step's leading ``- `` (so ``- run: x`` and ``run: x`` both read ``run: x``)."""
    lines = (line.strip() for line in block.splitlines())
    return [line.removeprefix("- ") for line in lines if line and not line.startswith("#")]


def _hashed_list_scan(rel_path: str, text: str) -> list[checker.Hit]:
    """The committed hashed list over ``text``, plus the plain list where it exists."""
    hits = checker.scan_text_hashed(rel_path, text, checker.HashList.parse(HASHED_LIST.read_text(encoding="utf-8")))
    if checker.DENYLIST:
        hits += checker.scan_text(rel_path, text, checker.DENYLIST)
    return hits


def test_the_public_ci_runs_the_hashed_scan_and_no_export() -> None:
    """A static parse of ``.github/workflows/ci.yml``: the ``de-amazon`` job runs the
    scanner in hashed mode with the design's exact command, the ``export_public.py
    --check`` step is gone, the both-edition ``two-track`` job has left for the overlay
    CI, ``pytest`` stays, the workflow watches the public ``main`` and spells no
    internal identifier itself (Requirement 1.2, 6.3)."""
    text = PUBLIC_CI.read_text(encoding="utf-8")
    jobs = _workflow_jobs(text)
    assert set(jobs) == {"de-amazon", "test"}, sorted(jobs)
    de_amazon = _run_lines(jobs["de-amazon"])
    assert f"run: {DE_AMAZON_COMMAND}" in de_amazon, "\n".join(de_amazon)
    assert sum("--hashed" in line for line in de_amazon) == 1
    assert "name: de-amazon" in de_amazon, "the required status check is named de-amazon"
    assert "name: pytest" in _run_lines(jobs["test"]) and "run: uv run pytest" in _run_lines(jobs["test"])
    assert "export_public" not in text, "the export check is deleted (Requirement 1.3)"
    assert "packaging/internal" not in text and "two-track" not in jobs, "overlay-only work does not run in the public CI"
    assert "    branches: [main]" in text, "the public repository's branch is main"
    commands = [line for block in jobs.values() for line in _run_lines(block) if line.startswith("run:")]
    assert not any("review" in line.lower() for line in commands), "no review of any kind is a gate (Requirement 6.3)"
    assert _hashed_list_scan(".github/workflows/ci.yml", text) == []


@pytest.mark.skipif(not OVERLAY_CI.is_file(), reason="the staged overlay CI is not part of the public tree")
def test_the_staged_overlay_ci_holds_the_moved_gates_in_order() -> None:
    """The overlay's ``ci.yml`` (staged under ``packaging/internal/overlay/``) composes
    the pinned tag first, then checks the composed tree's hashed list, runs the overlay
    ``pytest`` and ``build.sh --check`` — in that order — and carries the moved
    ``two-track`` job; it, too, spells no internal identifier and no export."""
    text = OVERLAY_CI.read_text(encoding="utf-8")
    jobs = _workflow_jobs(text)
    assert {"overlay", "two-track"} <= set(jobs), sorted(jobs)
    overlay = _run_lines(jobs["overlay"])
    order = [
        "run: packaging/internal/build.sh --compose-only",
        "run: python tools/hash-identifiers.py --check --out build/public-tree/scripts/internal-identifiers.sha256",
        "uv run pytest",
        "run: packaging/internal/build.sh --check",
    ]
    positions = [next(i for i, line in enumerate(overlay) if line == step or line.endswith(step)) for step in order]
    assert positions == sorted(positions), list(zip(order, positions))
    two_track = _run_lines(jobs["two-track"])
    assert "run: packaging/internal/build.sh --compose-only" in two_track
    assert any(line.endswith("scripts/artifact_identity_report.py") for line in two_track)
    assert any(line.endswith("scripts/install_both_editions.py") for line in two_track)
    assert "    branches: [mainline]" in text, "the overlay's branch is mainline"
    assert "export_public" not in text
    assert _hashed_list_scan("overlay/.github/workflows/ci.yml", text) == []


def _fixture_entries(rng: random.Random, lengths: list[int]) -> list[str]:
    """Hashed-mode entries, one per requested length, all ``zq``-prefixed and none a
    substring of another (so a planted entry yields exactly one hit)."""
    out: list[str] = []
    for length in lengths:
        while True:
            entry = FIXTURE_ENTRY_PREFIX + "".join(rng.choice(ENTRY_ALPHABET) for _ in range(length - len(FIXTURE_ENTRY_PREFIX)))
            if not any(entry in other or other in entry for other in out):
                out.append(entry)
                break
    return out


def _hash_tool(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(HASH_TOOL), *args], capture_output=True, text=True, cwd=str(REPO_ROOT))


@pytest.mark.skipif(not HASH_TOOL.is_file(), reason="tools/hash-identifiers.py is overlay-only")
def test_a_stale_hashed_list_fails_the_hash_check(tmp_path: Path) -> None:
    """``tools/hash-identifiers.py`` writes the public tree's ``.sha256`` from a plain
    list; ``--check`` exits 0 while the file is what it would write and 1 — naming the
    file and the regenerating command — once the plain list changed under it or the
    file is missing. A fixture list, so the real one is never spelled here."""
    entries = ["zq-one.example", "zqtwo", "zq.three-pkg"]
    source = tmp_path / "overlay" / "scripts" / "internal-identifiers.txt"
    source.parent.mkdir(parents=True)
    source.write_text("# the plain list\n" + "\n".join(entries[:2]) + "\n\n", encoding="utf-8")
    out = tmp_path / "public" / "scripts" / "internal-identifiers.sha256"
    written = _hash_tool("--source", str(source), "--out", str(out))
    assert written.returncode == 0, written.stdout + written.stderr
    assert out.read_text(encoding="utf-8") == "\n".join(checker.hash_list(entries[:2])) + "\n"
    current = _hash_tool("--check", "--source", str(source), "--out", str(out))
    assert current.returncode == 0 and "is current (2 entries)" in current.stdout, current.stdout + current.stderr
    # the plain list gained an entry: the generated file is now stale
    source.write_text("\n".join(entries) + "\n", encoding="utf-8")
    stale = _hash_tool("--check", "--source", str(source), "--out", str(out))
    assert stale.returncode == 1, stale.stdout + stale.stderr
    assert stale.stderr.startswith("stale:") and "internal-identifiers.sha256" in stale.stderr
    assert "--source" in stale.stderr and "--out" in stale.stderr, "the message names the regenerating command"
    assert not any(entry in stale.stderr + stale.stdout for entry in entries), "the tool never prints an entry"
    assert out.read_text(encoding="utf-8") == "\n".join(checker.hash_list(entries[:2])) + "\n", "--check writes nothing"
    out.unlink()
    missing = _hash_tool("--check", "--source", str(source), "--out", str(out))
    assert missing.returncode == 1 and missing.stderr.startswith("missing:"), missing.stdout + missing.stderr


@pytest.mark.skipif(not (HASH_TOOL.is_file() and PLAIN_LIST.is_file()), reason="the plain list and the hash tool are overlay-only")
def test_the_committed_hashed_list_is_current() -> None:
    """What the overlay CI runs: the committed ``scripts/internal-identifiers.sha256``
    is exactly what the tool writes from the committed plain list, so a commit that
    edits one without the other fails here before it reaches the public tree."""
    result = _hash_tool("--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert HASHED_LIST.read_text(encoding="utf-8") == "\n".join(checker.hash_list(checker.DENYLIST)) + "\n"


def test_a_planted_term_in_a_fixture_tree_is_caught_in_hashed_mode(tmp_path: Path) -> None:
    """The CLI as the public ``de-amazon`` job runs it (``--paths . --hashed LIST``) over
    a fixture tree: a term planted in one file, recased and inside a URL, fails the scan
    with the file, line and matched text named; the same tree without it passes and
    says it scanned against the hashed list; a hashed-mode allowlist rule may spell the
    entry it excuses, as ``scripts/public-export-allow.txt`` does."""
    rng = random.Random(24)
    entries = _fixture_entries(rng, [4, 6, 7, 10, 14, 22])
    hashed = tmp_path / "internal-identifiers.sha256"
    hashed.write_text("\n".join(checker.hash_list(entries)) + "\n", encoding="utf-8")
    root = tmp_path / "tree"
    for rel, text in {
        "core/a.py": "x = 1\nreturn self.value\n",
        "docs/guide.md": "# Guide\nplain prose about the loader and the registry\n",
        "scripts/helper.sh": "#!/bin/sh\necho ok\n",
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    planted_entry = entries[3]
    planted = root / "docs" / "notes.md"
    planted.write_text(f"# Notes\n\nsee https://{planted_entry.upper()}/index.html for the rest\n", encoding="utf-8")
    argv = [sys.executable, str(SCANNER), "--root", str(root), "--paths", ".", "--hashed", str(hashed)]
    dirty = subprocess.run(argv, capture_output=True, text=True)
    assert dirty.returncode == 1, dirty.stdout + dirty.stderr
    assert f"docs/notes.md:3: {planted_entry!r} in: see https://{planted_entry.upper()}/index.html" in dirty.stdout, dirty.stdout
    assert dirty.stdout.count("\n  ") == 1, "one hit: the planted line only\n" + dirty.stdout
    allow = tmp_path / "allow.txt"
    allow.write_text(f"docs/notes.md:{planted_entry}:a documented fixture example\n", encoding="utf-8")
    excused = subprocess.run(argv + ["--allowlist", str(allow)], capture_output=True, text=True)
    assert excused.returncode == 0 and "1 allowlist rule(s)" in excused.stdout, excused.stdout + excused.stderr
    planted.unlink()
    clean = subprocess.run(argv, capture_output=True, text=True)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert f"hashed list of {len(entries)} entries" in clean.stdout, clean.stdout


# A realistic fixture tree: code, prose, URLs and paths, JSON, a lockfile's hash lines,
# drawn from a pool so tokens repeat as they do in a repository, with some lines given
# a unique tail so not every run is a cache hit. Long unique runs (URLs, 64-hex digests)
# are what cost hashed mode; a repository has plenty.
_FIXTURE_WORDS = (
    "the", "loader", "mod", "registry", "edition", "host", "payload", "manifest", "returns", "value", "self",
    "import", "class", "def", "return", "assert", "for", "in", "if", "else", "path", "json", "version", "release",
    "forge", "lane", "ledger", "check", "matrix", "archive", "sha256", "commit", "branch", "tag", "pin", "tree",
)


def _fixture_line_pool(rng: random.Random, count: int) -> list[str]:
    word = lambda: rng.choice(_FIXTURE_WORDS)  # noqa: E731
    pool: list[str] = []
    for _ in range(count):
        kind = rng.random()
        if kind < 0.35:
            pool.append(f"    {word()}_{word()} = {word()}.{word()}({word()}, {rng.randint(0, 9999)})  # {word()} {word()}")
        elif kind < 0.6:
            pool.append(" ".join(word() for _ in range(rng.randint(6, 16))).capitalize() + ".")
        elif kind < 0.75:
            pool.append(f"see https://{word()}.{word()}.example/{word()}/{rng.randint(0, 99999)}/{word()}.html and /opt/{word()}/{word()}-{rng.randint(0, 999)}/bin")
        elif kind < 0.92:
            pool.append(f'  "{word()}": "{word()}-{rng.randint(0, 99999)}",')
        else:
            pool.append(f'    {{ url = "https://files.example/{word()}-{rng.randint(1, 9)}.{rng.randint(0, 20)}.whl", hash = "sha256:{rng.randbytes(32).hex()}" }},')
    return pool


def _realistic_tree(root: Path, rng: random.Random, target_bytes: int) -> tuple[int, int]:
    """Write files under ``root`` until ``target_bytes`` of text exist; ``(files, bytes)``."""
    pool = _fixture_line_pool(rng, 40_000)
    suffixes = ("py", "md", "json", "txt", "js", "css", "html")
    total = files = 0
    while total < target_bytes:
        path = root / f"pkg{files % 23}" / f"sub{files % 7}" / f"file{files}.{rng.choice(suffixes)}"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [line + (f" id{rng.randint(0, 10**9)}" if rng.random() < 0.15 else "") for line in rng.choices(pool, k=rng.randint(100, 600))]
        data = ("\n".join(lines) + "\n").encode("utf-8")
        path.write_bytes(data)
        total += len(data)
        files += 1
    return files, total


@pytest.mark.slow
def test_hashed_mode_over_a_10mb_fixture_tree_finishes_inside_the_budget(tmp_path: Path) -> None:
    """The CLI in hashed mode over a 10 MiB realistic fixture tree, with the committed
    list's length profile (26 entries, 14 distinct lengths) and one planted term,
    finishes inside ``SCAN_BUDGET_SECONDS`` on the CLI's default process count and
    finds exactly the planted line (design: "with a few dozen entries and a 10 MB tree
    it runs in seconds, measured by the test")."""
    rng = random.Random(2024)
    if HASHED_LIST.is_file():
        lengths = [length for _, length in checker.HashList.parse(HASHED_LIST.read_text(encoding="utf-8")).entries]
    else:  # the committed list's profile at the time of writing
        lengths = [10, 10, 7, 7, 15, 12, 17, 6, 14, 6, 6, 8, 6, 6, 7, 7, 11, 4, 22, 15, 14, 19, 28, 26, 6, 6]
    entries = _fixture_entries(rng, lengths)
    hashed = tmp_path / "internal-identifiers.sha256"
    hashed.write_text("\n".join(checker.hash_list(entries)) + "\n", encoding="utf-8")
    root = tmp_path / "tree"
    files, size = _realistic_tree(root, rng, 10 * 1024 * 1024)
    assert size >= 10 * 1024 * 1024
    planted_entry = entries[7]
    (root / "pkg3" / "notes.md").write_text(f"- see https://{planted_entry.upper()}/index.html\n", encoding="utf-8")
    started = time.perf_counter()
    result = subprocess.run([sys.executable, str(SCANNER), "--root", str(root), "--paths", ".", "--hashed", str(hashed)],
                            capture_output=True, text=True)
    elapsed = time.perf_counter() - started
    report = f"hashed scan of {files} files / {size / 1e6:.1f} MB against {len(entries)} entries on {checker._default_jobs()} process(es): {elapsed:.2f}s"
    print(report)
    assert result.returncode == 1, result.stdout + result.stderr
    hit_lines = [line for line in result.stdout.splitlines() if line.startswith("  ")]
    assert hit_lines == [f"  pkg3/notes.md:1: {planted_entry!r} in: - see https://{planted_entry.upper()}/index.html"], result.stdout
    assert elapsed < SCAN_BUDGET_SECONDS, f"{report}; budget {SCAN_BUDGET_SECONDS:.0f}s"
