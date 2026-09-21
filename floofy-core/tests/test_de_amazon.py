"""Tests for scripts/check_no_internal_identifiers.py (Requirement 10.2, task 1.7).

The test data never spells an internal identifier literally: every denylisted
token is taken from the checker's own ``DENYLIST`` at runtime, so this file
stays clean under the very scan it exercises.
"""
from __future__ import annotations

import string
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_no_internal_identifiers.py"
sys.path.insert(0, str(SCRIPT.parent))

import check_no_internal_identifiers as checker  # noqa: E402

TOKEN = checker.DENYLIST[0]  # a hostname fragment
OTHER_TOKEN = checker.DENYLIST[-1]  # an account name


def _tree(tmp_path: Path, files: dict[str, str | bytes]) -> Path:
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    return tmp_path


def test_clean_tree_has_no_hits(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"core/a.py": "x = 1\n", "docs/guide.md": "# Guide\nplain text\n"})
    assert checker.scan(root, ("core", "docs")) == []


def test_hit_reports_path_line_and_pattern(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"core/a.py": f"x = 1\nurl = 'https://foo.{TOKEN}/x'\n"})
    hits = checker.scan(root, ("core",))
    assert [(h.path, h.line, h.pattern) for h in hits] == [("core/a.py", 2, TOKEN)]


def test_match_is_case_insensitive(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"core/a.txt": TOKEN.upper() + "\n"})
    assert [h.pattern for h in checker.scan(root, ("core",))] == [TOKEN]


def test_unscanned_paths_are_ignored(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"editions/x.py": f"# {TOKEN}\n", "core/ok.py": "pass\n"})
    assert checker.scan(root, ("core",)) == []


def test_binary_and_skip_dirs_are_ignored(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "core/img.png": b"\x89PNG" + TOKEN.encode(),
            "core/__pycache__/m.cpython-312.pyc": TOKEN.encode(),
            "core/blob.bin": b"\xff\xfe" + TOKEN.encode() + b"\xff",
        },
    )
    assert checker.scan(root, ("core",)) == []


def test_legal_notice_files_are_not_scanned(tmp_path: Path) -> None:
    """A LICENSE/NOTICE file (or anything under licenses/) reproduces upstream attribution
    verbatim, as the upstream license requires — public attribution, not an internal
    identifier. Everything else still is scanned, including a name that merely contains
    the words."""
    root = _tree(
        tmp_path,
        {
            "core/NOTICE.md": TOKEN.encode(),
            "core/LICENSE": TOKEN.encode(),
            "core/LICENSES/Apache-2.0.txt": TOKEN.encode(),
            "core/dist-info/licenses/NOTICE.md": TOKEN.encode(),
            "core/licensing.py": TOKEN.encode(),
        },
    )
    hits = checker.scan(root, ("core",))
    assert [hit.path for hit in hits] == ["core/licensing.py"]


def test_allowlist_covers_exact_file_and_subtree(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "core/vendored/x.md": f"see {TOKEN}\n",
            "core/y.md": f"see {TOKEN} and {OTHER_TOKEN}\n",
        },
    )
    rules = checker.parse_allowlist(
        f"core/vendored/:*:vendored upstream text\ncore/y.md:{TOKEN}:documented example\n", root
    )
    hits = checker.scan(root, ("core",), rules)
    assert [(h.path, h.pattern) for h in hits] == [("core/y.md", OTHER_TOKEN)]


def test_allowlist_rejects_malformed_unknown_and_stale_lines(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"core/a.py": "pass\n"})
    with pytest.raises(checker.AllowlistError):
        checker.parse_allowlist("core/a.py:no-reason-field\n", root)
    with pytest.raises(checker.AllowlistError):
        checker.parse_allowlist("core/a.py:not-a-denylist-entry:reason\n", root)
    with pytest.raises(checker.AllowlistError):
        checker.parse_allowlist(f"core/missing.py:{TOKEN}:stale\n", root)
    assert checker.parse_allowlist("# comment only\n\n", root) == []


def test_cli_exit_codes(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"core/a.py": "pass\n", "core/b.py": f"# {TOKEN}\n"})
    clean = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--paths", "core/a.py"], capture_output=True, text=True
    )
    assert clean.returncode == 0, clean.stdout + clean.stderr
    dirty = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--paths", "core"], capture_output=True, text=True
    )
    assert dirty.returncode == 1
    assert "core/b.py:1" in dirty.stdout


def test_repository_default_paths_are_clean() -> None:
    """The real tree must pass with the committed allowlist (what CI runs)."""
    allow_file = REPO_ROOT / "scripts" / "de-amazon-allow.txt"
    rules = checker.parse_allowlist(allow_file.read_text(encoding="utf-8"), REPO_ROOT)
    hits = checker.scan(REPO_ROOT, checker.DEFAULT_SCAN_PATHS, rules)
    assert hits == [], "\n".join(h.format() for h in hits)


# --- property: the scanner flags exactly the lines a denylist token was planted on.

_clean_alphabet = string.ascii_letters + string.digits + " _-/:.()[]{}=\"'\n"


def _has_no_token(text: str) -> bool:
    lowered = text.lower()
    return not any(p in lowered for p in checker.DENYLIST)


clean_text = st.text(alphabet=_clean_alphabet, min_size=0, max_size=400).filter(_has_no_token)


@settings(max_examples=150, deadline=None)
@given(clean_text, st.sampled_from(checker.DENYLIST), st.integers(min_value=0, max_value=50))
def test_planted_token_is_the_only_hit(text: str, token: str, position: int) -> None:
    """**Validates: Requirements 10.2** — a public-clean text yields no hits; planting
    one denylisted token on one line yields hits only for that line, and the planted
    token is always among them (other entries may be substrings of it)."""
    assert checker.scan_text("f.txt", text) == []
    lines = text.split("\n")
    idx = position % len(lines)
    lines[idx] = lines[idx] + " " + token.swapcase()
    hits = checker.scan_text("f.txt", "\n".join(lines))
    assert hits, "planted token not detected"
    assert {h.line for h in hits} == {idx + 1}
    assert token.lower() in {h.pattern for h in hits}
