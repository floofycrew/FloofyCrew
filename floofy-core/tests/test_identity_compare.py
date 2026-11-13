"""The identity report's two-tree mode (task 18.3; Requirement 10.4).

``scripts/artifact_identity_report.py --tree A B`` compares two directories that
are already built, so the cut-over can show that the tree the old export path
produced and the tree the public repository now carries are the same bytes for
the same source. These tests drive it on scratch trees: the editions mode needs
both adapters and is covered by ``test_two_track.py``; nothing here builds a
release tree, so the module runs on the public tree too.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import artifact_identity_report as report_module
from artifact_identity_report import Expectation, compare_trees, main


def _tree(root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


SHARED = {
    "README.md": "shared\n",
    "pkg/core.py": "print(1)\n",
    "pkg/resolver.py": "print(2)\n",
    "pkg/registry.py": "print(3)\n",
    "pkg/data/one.json": '{"a": 1}\n',
    "pkg/data/two.json": '{"b": 2}\n',
    "docs/guide.md": "# guide\n",
    "docs/design.md": "# design\n",
    "ui/app.js": "export const x = 1;\n",
}


def test_identical_trees_report_100_percent(tmp_path: Path):
    left = _tree(tmp_path / "a", SHARED)
    right = _tree(tmp_path / "b", SHARED)
    report = compare_trees(left, right)
    assert report.ok and report.differing == {}
    assert report.identical_files == report.total_files == len(SHARED)
    assert report.file_percent == 100.0 and report.byte_percent == 100.0
    assert sorted(report.files) == ["a", "b"], "the labels default to the directory names"
    assert report.identical_bytes == report.total_bytes > 0


def test_a_one_sided_file_is_expected_when_a_pattern_names_it(tmp_path: Path):
    left = _tree(tmp_path / "a", {**SHARED, ".github/workflows/ci.yml": "jobs:\n"})
    right = _tree(tmp_path / "b", SHARED)
    unexpected = compare_trees(left, right)
    assert unexpected.differing == {"only in a": [".github/workflows/ci.yml"]}, "a one-sided file is reported on its side"
    assert unexpected.unexpected == [], "a one-sided file alone is not an unexpected difference"

    expected = compare_trees(left, right, expect=[Expectation.parse(".github/workflows/ci.yml")])
    assert expected.differing == {"expected": [".github/workflows/ci.yml"]}
    assert expected.ok, expected.render()
    assert expected.identical_files == len(SHARED) and expected.total_files == len(SHARED) + 1


def test_a_regex_confined_difference_is_expected(tmp_path: Path):
    left = _tree(tmp_path / "a", {**SHARED, "RELEASE.md": "# 1.3.12\n\nVendors public tag v1.3.12 (aaaa111).\n\nbody\n"})
    right = _tree(tmp_path / "b", {**SHARED, "RELEASE.md": "# 1.3.12\n\nVendors public tag v1.3.12 (bbbb222).\n\nbody\n"})
    report = compare_trees(left, right, expect=[Expectation.parse("RELEASE.md=^Vendors public tag ")])
    assert report.ok, report.render()
    assert report.differing == {"expected": ["RELEASE.md"]}
    assert report.identical_files == len(SHARED) and report.total_files == len(SHARED) + 1



def test_a_difference_outside_the_regex_still_fails(tmp_path: Path):
    left = _tree(tmp_path / "a", {**SHARED, "RELEASE.md": "# 1.3.12\n\nVendors public tag v1.3.12 (aaaa111).\n\nbody\n"})
    right = _tree(tmp_path / "b", {**SHARED, "RELEASE.md": "# 1.3.12\n\nVendors public tag v1.3.12 (bbbb222).\n\nedited body\n"})
    report = compare_trees(left, right, expect=[Expectation.parse("RELEASE.md=^Vendors public tag ")])
    assert not report.ok and report.unexpected == ["RELEASE.md"], report.render()
    assert report.differing == {"other": ["RELEASE.md"]}
    assert "FAIL: unexpected differences: RELEASE.md" in report.render()


def test_a_regex_expectation_fails_on_a_one_sided_file(tmp_path: Path):
    """``=REGEX`` says *these lines* may differ, which presupposes the file is in both trees."""
    left = _tree(tmp_path / "a", {**SHARED, "RELEASE.md": "Vendors public tag v1.3.12\n"})
    right = _tree(tmp_path / "b", SHARED)
    report = compare_trees(left, right, expect=[Expectation.parse("RELEASE.md=^Vendors public tag ")])
    assert report.differing == {"other": ["RELEASE.md"]} and not report.ok


def test_an_unexpected_difference_fails(tmp_path: Path):
    left = _tree(tmp_path / "a", SHARED)
    right = _tree(tmp_path / "b", {**SHARED, "pkg/core.py": "print(2)\n"})
    report = compare_trees(left, right)
    assert not report.ok and report.unexpected == ["pkg/core.py"]
    assert report.identical_files == len(SHARED) - 1 and report.total_files == len(SHARED)


def test_min_percent_fails_a_tree_that_drifted_too_far(tmp_path: Path):
    left = _tree(tmp_path / "a", SHARED)
    right = _tree(tmp_path / "b", {"README.md": "shared\n"})
    report = compare_trees(left, right, expect=[Expectation.parse("pkg/*"), Expectation.parse("docs/*"), Expectation.parse("ui/*")])
    assert report.unexpected == [], "the patterns cover every one-sided file"
    assert not report.ok and report.file_percent == pytest.approx(11.1, abs=0.1), "but one identical file in nine is below the 80 % target"
    assert "below the 80 % target" in report.render()
    assert compare_trees(left, right, expect=[Expectation.parse("*")], min_percent=10.0).ok


def test_the_cli_reports_json_and_the_exit_status(tmp_path: Path, capsys: pytest.CaptureFixture):
    left = _tree(tmp_path / "a", {**SHARED, "RELEASE.md": "tag v1\nbody\n"})
    right = _tree(tmp_path / "b", {**SHARED, "RELEASE.md": "tag v2\nbody\n"})
    total = len(SHARED) + 1
    argv = ["--tree", str(left), str(right), "--names", "export,public-first", "--json"]
    assert main(argv) == 1
    failed = json.loads(capsys.readouterr().out)
    assert failed["trees"] == ["export", "public-first"] and failed["differing"] == {"other": ["RELEASE.md"]}
    assert failed["ok"] is False and failed["totalFiles"] == total and failed["identicalFiles"] == len(SHARED)
    assert failed["perTree"]["export"]["files"] == total and failed["perTree"]["public-first"]["files"] == total

    assert main([*argv, "--expect", "RELEASE.md=^tag "]) == 0
    passed = json.loads(capsys.readouterr().out)
    assert passed["ok"] is True and passed["differing"] == {"expected": ["RELEASE.md"]}
    assert main(["--tree", str(left), str(right), "--names", "export,public-first"]) == 1
    assert "export vs public-first" in capsys.readouterr().out



@pytest.mark.parametrize(
    "argv",
    [
        ["--expect", "RELEASE.md"],  # the expectations only mean something against two trees
        ["--names", "a,b"],
        ["--tree", "x", "y", "--names", "only-one"],
        ["--tree", "x", "y", "--keep", "z"],
        ["--tree", "x", "y", "--expect", "RELEASE.md="],  # an empty regex would excuse every line
        ["--tree", "x", "y", "--expect", "=^tag "],
    ],
)
def test_the_cli_rejects_a_mode_mix_or_a_broken_expectation(argv: list[str]):
    with pytest.raises(SystemExit) as exit_info:
        main(argv)
    assert exit_info.value.code == 2


def test_a_missing_tree_is_an_error_not_a_zero_percent_measurement(tmp_path: Path):
    _tree(tmp_path / "a", SHARED)
    with pytest.raises(ValueError, match="not a directory"):
        compare_trees(tmp_path / "a", tmp_path / "nope")
    with pytest.raises(ValueError, match="--names"):
        compare_trees(tmp_path / "a", tmp_path / "a")


def test_the_editions_mode_is_untouched():
    """The two-tree mode is additive: the editions mode keeps its groups and its contract."""
    assert sorted(report_module.EDITIONS) == ["external", "internal"] and report_module.EDITIONS["external"] == "public"
    assert list(report_module.ALLOWED_DIFFERENCES) == ["adapter modules", "edition stamps"]
    assert report_module._group("zipapp/floofy_edition_public/registry.json") == "adapter modules"
    assert report_module._group("loader-app/EDITION") == "edition stamps"
    assert report_module._group("loader-app/floofy_loader/boot.py") == "other"
    assert report_module.DEFAULT_MIN_PERCENT == 80.0
    editions = report_module.IdentityReport({"internal": {}, "external": {}})
    assert editions.title == report_module.EDITIONS_TITLE and editions.min_percent == 80.0
    assert editions.render().startswith("Artifact byte-identity across editions")
