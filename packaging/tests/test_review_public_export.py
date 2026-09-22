"""Tests for packaging/internal/review_public_export.py — the pre-push AI review.

The model is faked with ``--model-cmd`` (a command reading the prompt on stdin),
so these tests are offline and deterministic. The module is INTERNAL-ONLY: in
the public export ``packaging/internal`` does not exist, so the whole file
skips there instead of failing.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
REVIEWER = REPO_ROOT / "packaging" / "internal" / "review_public_export.py"
pytestmark = pytest.mark.skipif(not REVIEWER.exists(), reason="internal-only tooling; absent from the public export")


def _load():
    spec = importlib.util.spec_from_file_location("review_public_export", REVIEWER)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def reviewer():
    return _load()


def _clone(tmp_path: Path, *, committed: dict[str, str] = None, pending: dict[str, str] = None) -> Path:
    clone = tmp_path / "clone"
    clone.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(clone)], check=True)
    for rel, text in (committed or {}).items():
        (clone / rel).parent.mkdir(parents=True, exist_ok=True)
        (clone / rel).write_text(text, encoding="utf-8")
    if committed:
        env_id = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                  "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
        subprocess.run(["git", "-C", str(clone), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(clone), "-c", "commit.gpgsign=false", "commit", "-q", "-m", "base"],
                       check=True, env={**__import__("os").environ, **env_id})
    for rel, text in (pending or {}).items():
        (clone / rel).parent.mkdir(parents=True, exist_ok=True)
        (clone / rel).write_text(text, encoding="utf-8")
    return clone


def _fake_model(tmp_path: Path, findings: list[dict], *, capture: Path | None = None) -> str:
    """A --model-cmd: optionally captures the prompt, then prints a findings block."""
    script = tmp_path / "fake_model.py"
    body = ["import sys", "prompt = sys.stdin.read()"]
    if capture:
        body.append(f"open({str(capture)!r}, 'a', encoding='utf-8').write(prompt + chr(0))")
    body += [f"print('FINDINGS_JSON_BEGIN')", f"print({json.dumps(findings)!r})", f"print('FINDINGS_JSON_END')"]
    script.write_text("\n".join(body) + "\n", encoding="utf-8")
    return f"{sys.executable} {script}"


def test_empty_delta_short_circuits_without_calling_the_model(reviewer, tmp_path: Path):
    clone = _clone(tmp_path, committed={"a.md": "published\n"})
    marker = tmp_path / "called"
    cmd = _fake_model(tmp_path, [], capture=marker)
    record = reviewer.review(clone, model="m", effort="low", max_bytes=90_000, timeout=60,
                             model_cmd=cmd, skip_scan=True)
    assert record["verdict"] == "empty-delta" and not marker.exists()


def test_clean_review_writes_the_record_and_reports_the_paths(reviewer, tmp_path: Path):
    clone = _clone(tmp_path, committed={"a.md": "one\n"}, pending={"b.md": "new file\n"})
    (clone / "a.md").write_text("one\nchanged\n", encoding="utf-8")
    record = reviewer.review(clone, model="m", effort="low", max_bytes=90_000, timeout=60,
                             model_cmd=_fake_model(tmp_path, []), skip_scan=True)
    assert record["verdict"] == "clean" and record["chunks"] >= 1
    assert set(record["reviewedPaths"]) == {"a.md", "b.md"}
    saved = json.loads((clone / ".git" / "floofycrew-ai-review.json").read_text(encoding="utf-8"))
    assert saved["verdict"] == "clean" and saved["findings"] == []


def test_findings_surface_and_fail_the_cli(reviewer, tmp_path: Path):
    finding = {"file": "b.md", "excerpt": "internal-thing", "category": "host", "reason": "looks internal"}
    clone = _clone(tmp_path, committed={"a.md": "one\n"}, pending={"b.md": "mentions internal-thing\n"})
    record = reviewer.review(clone, model="m", effort="low", max_bytes=90_000, timeout=60,
                             model_cmd=_fake_model(tmp_path, [finding]), skip_scan=True)
    assert record["verdict"] == "findings" and record["findings"] == [finding]
    result = subprocess.run([sys.executable, str(REVIEWER), "--clone", str(clone), "--skip-scan",
                             "--model-cmd", _fake_model(tmp_path, [finding])],
                            capture_output=True, text=True)
    assert result.returncode == 1 and "internal-thing" in result.stdout


def test_the_delta_reaches_the_model_and_big_deltas_are_chunked(reviewer, tmp_path: Path):
    capture = tmp_path / "prompts"
    pending = {"new.md": "brand new content marker-alpha\n" * 400}
    clone = _clone(tmp_path, committed={"a.md": "x\n"}, pending=pending)
    record = reviewer.review(clone, model="m", effort="low", max_bytes=4_000, timeout=60,
                             model_cmd=_fake_model(tmp_path, [], capture=capture), skip_scan=True)
    prompts = capture.read_text(encoding="utf-8").split(chr(0))[:-1]
    assert record["chunks"] == len(prompts) > 1, "the delta is split across model calls"
    assert all(p.startswith(reviewer.RUBRIC[:40]) for p in prompts), "every chunk carries the rubric"
    assert "marker-alpha" in "".join(prompts)


def test_a_marker_less_model_answer_is_retried_then_an_error_not_a_pass(reviewer, tmp_path: Path):
    clone = _clone(tmp_path, committed={"a.md": "x\n"}, pending={"b.md": "y\n"})
    counter = tmp_path / "calls"
    script = tmp_path / "chatty.py"
    script.write_text(
        "import pathlib\n"
        f"c = pathlib.Path({str(counter)!r})\n"
        "c.write_text(c.read_text() + 'x' if c.exists() else 'x')\n"
        "print('all good, nothing to see')\n",
        encoding="utf-8")
    with pytest.raises(reviewer.ReviewError, match="no FINDINGS_JSON_BEGIN"):
        reviewer.review(clone, model="m", effort="low", max_bytes=90_000, timeout=60,
                        model_cmd=f"{sys.executable} {script}", skip_scan=True)
    assert counter.read_text() == "xx", "a contract drift is retried exactly once before failing closed"


def test_a_drifted_chunk_recovers_on_the_retry(reviewer, tmp_path: Path):
    clone = _clone(tmp_path, committed={"a.md": "x\n"}, pending={"b.md": "y\n"})
    state = tmp_path / "state"
    script = tmp_path / "flaky.py"
    script.write_text(
        "import pathlib\n"
        f"s = pathlib.Path({str(state)!r})\n"
        "if s.exists():\n"
        "    print('FINDINGS_JSON_BEGIN'); print('[]'); print('FINDINGS_JSON_END')\n"
        "else:\n"
        "    s.write_text('1'); print('let me analyze this diff for you...')\n",
        encoding="utf-8")
    record = reviewer.review(clone, model="m", effort="low", max_bytes=90_000, timeout=60,
                             model_cmd=f"{sys.executable} {script}", skip_scan=True)
    assert record["verdict"] == "clean"


def test_committed_but_unpushed_changes_are_reviewed_against_the_upstream(reviewer, tmp_path: Path):
    capture = tmp_path / "prompt"
    upstream = tmp_path / "upstream.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(upstream)], check=True)
    clone = _clone(tmp_path, committed={"a.md": "published\n"})
    # a relative remote URL: an absolute one would carry the runner's home
    # directory (an account name), which the internal-remote guard rightly refuses
    subprocess.run(["git", "-C", str(clone), "remote", "add", "origin", "../upstream.git"], check=True)
    subprocess.run(["git", "-C", str(clone), "push", "-q", "-u", "origin", "main"], check=True)
    (clone / "a.md").write_text("published\nplus a committed-but-unpushed line marker-beta\n", encoding="utf-8")
    env_id = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
              "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    subprocess.run(["git", "-C", str(clone), "-c", "commit.gpgsign=false", "commit", "-q", "-am", "local"],
                   check=True, env={**__import__("os").environ, **env_id})
    record = reviewer.review(clone, model="m", effort="low", max_bytes=90_000, timeout=60,
                             model_cmd=_fake_model(tmp_path, [], capture=capture), skip_scan=True)
    assert record["verdict"] == "clean" and record["base"] == "origin/main"
    assert "marker-beta" in capture.read_text(encoding="utf-8"), "the unpushed COMMIT is part of the reviewed delta"


def test_base_full_reviews_every_tracked_file(reviewer, tmp_path: Path):
    clone = _clone(tmp_path, committed={"a.md": "one marker-gamma\n", "sub/b.md": "two\n"})
    capture = tmp_path / "prompt"
    record = reviewer.review(clone, model="m", effort="low", max_bytes=90_000, timeout=60,
                             model_cmd=_fake_model(tmp_path, [], capture=capture), skip_scan=True, base="full")
    assert record["base"] is None and set(record["reviewedPaths"]) == {"a.md", "sub/b.md"}
    assert "marker-gamma" in capture.read_text(encoding="utf-8")


def test_refuses_a_clone_with_an_internal_remote(reviewer, tmp_path: Path):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import check_no_internal_identifiers as checker  # noqa: PLC0415

    clone = _clone(tmp_path, committed={"a.md": "x\n"})
    host = checker.DENYLIST[0].strip(".")
    subprocess.run(["git", "-C", str(clone), "remote", "add", "origin", f"ssh://{host}/pkg/W"], check=True)
    with pytest.raises(Exception, match="internal remote"):
        reviewer.review(clone, model="m", effort="low", max_bytes=90_000, timeout=60,
                        model_cmd=_fake_model(tmp_path, []), skip_scan=True)
