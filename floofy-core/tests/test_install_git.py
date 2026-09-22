"""``floofy install <git reference>`` — install straight from a repository (task 7.7).

Requirement 8.8 (grammar, shallow clone with the user's credentials, ``floofy
validate`` on the checkout, ``files[]`` verification, the extra "unlisted source"
consent line, the audit row with source and commit), 11.1 / 11.3 (the line is
consent, never satisfied by ``--yes``; the source is trusted no further than that
consent), 11.6 (encrypted transports only) and 13.3. A local **bare repository**
stands in for the forge, reached over ``file://`` under the test-only switch
``FLOOFY_ALLOW_LOCAL_GIT=1``; nothing touches a live install or the network.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from floofy_core.audit import read_audit
from floofy_core.cli.console import Console
from floofy_core.cli.main import execute, run
from floofy_core.consent import ACCEPT_PHRASE, write_consent
from floofy_core.datahome import DataHome
from floofy_core.gitsource import UNLISTED_SOURCE_LINE, GitRef, GitRefError, checkout_key, local_git_allowed, manifest_sha256, verify_files
from floofy_core.modstore import read_source

from floofy_testing import EXAMPLES_DIR, fake_payload


def _git(*args: str, cwd: Path) -> str:
    done = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True, env={"GIT_AUTHOR_NAME": "tests", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "tests", "GIT_COMMITTER_EMAIL": "t@example.invalid", "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(cwd)})
    return done.stdout.strip()


def make_bare_repo(tmp_path: Path, *, mod_kind: str = "theme", tag: str = "1.0.0", subdirectory: str = "") -> tuple[str, str]:
    """A bare repository holding the example mod at ``tag`` (optionally under ``subdirectory``); returns ``(file:// url, commit)``."""
    work = tmp_path / "work"
    target = work / subdirectory if subdirectory else work
    target.parent.mkdir(parents=True, exist_ok=True)
    import shutil  # noqa: PLC0415

    shutil.copytree(EXAMPLES_DIR / mod_kind, target)
    _git("init", "-q", "-b", "main", cwd=work)
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "release", cwd=work)
    _git("tag", tag, cwd=work)
    commit = _git("rev-parse", "HEAD", cwd=work)
    bare = tmp_path / "forge.git"
    _git("clone", "-q", "--bare", str(work), str(bare), cwd=tmp_path)
    return bare.as_uri(), commit


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FLOOFY_NO_ADAPTERS", "1")
    monkeypatch.setenv("FLOOFY_ALLOW_LOCAL_GIT", "1")
    monkeypatch.delenv("KIROCREW_HOME", raising=False)
    monkeypatch.setenv("KIRO_HOME", str(tmp_path / "kiro"))
    payload_root = tmp_path / "payload"
    fake_payload(payload_root, "0.7.0", build="0.7.0.5")
    home = tmp_path / "home"
    data = DataHome.for_host_home(home).ensure()
    write_consent(data.consent, by="tests", how="test")

    class Env:
        root = payload_root
        host_home = home
        paths = data
        scratch = tmp_path

        def argv(self, *args: str) -> list[str]:
            return ["--home", str(home), "--root", str(payload_root), *args]

        def run(self, *args: str, **kw):
            return run(self.argv(*args), non_interactive=True, actor="test", **kw)

    return Env()


# --- the grammar -----------------------------------------------------------------------------------------


def test_reference_grammar_accepts_the_two_encrypted_spellings_only():
    ssh = GitRef.parse("ssh://git@forge.example/pkg/Widget@2.3.4#mods/widget")
    assert (ssh.url, ssh.tag, ssh.subdirectory, ssh.text) == ("ssh://git@forge.example/pkg/Widget", "2.3.4", "mods/widget", "ssh://git@forge.example/pkg/Widget@2.3.4#mods/widget")
    https = GitRef.parse("https://github.example/owner/repo.git@v1.0.0")
    assert (https.url, https.tag, https.stem, https.pinned) == ("https://github.example/owner/repo.git", "v1.0.0", "repo", True)
    for text, code in (
        ("git://forge.example/pkg/Widget@1.0.0", "PlaintextTransport"),
        ("http://forge.example/owner/repo.git@1.0.0", "PlaintextTransport"),
        ("forge.example/owner/repo@1.0.0", "NotAGitReference"),
        ("https://forge.example/owner/repo?raw=1", "NotAGitReference"),
        ("https://forge.example/owner/repo@1.0.0#../escape", "BadSubdirectory"),
        ("file:///srv/repo.git@1.0.0", "LocalTransport"),
    ):
        with pytest.raises(GitRefError) as caught:
            GitRef.parse(text, allow_local=False)
        assert caught.value.code == code, text
    assert not local_git_allowed({})
    assert GitRef.parse("file:///srv/repo.git@1.0.0", allow_local=True).scheme == "file"
    # a bare reference is not refused: it takes the repository's default branch (HEAD)
    bare = GitRef.parse("https://forge.example/owner/repo", allow_local=False)
    assert bare.is_default_branch and not bare.pinned and bare.commitish == ""


def test_explicit_ref_forms_and_never_both():
    branch = GitRef.parse("https://forge.example/o/r.git", ref="main")
    assert branch.tag is None and branch.ref == "main" and branch.commitish == "main" and not branch.pinned and not branch.is_commit
    commit = GitRef.parse("ssh://forge.example/pkg/R", ref="a" * 40)
    assert commit.is_commit
    with pytest.raises(GitRefError) as both:
        GitRef.parse("ssh://forge.example/pkg/R@1.0.0", ref="main")
    assert both.value.code == "AmbiguousReference"
    with pytest.raises(GitRefError) as bad:
        GitRef.parse("ssh://forge.example/pkg/R", ref="not a ref")
    assert bad.value.code == "BadRef"


def test_looks_like_keeps_archive_downloads_and_registry_ids_apart():
    assert GitRef.looks_like("ssh://forge.example/pkg/R")
    assert GitRef.looks_like("https://forge.example/o/r.git")
    assert GitRef.looks_like("https://forge.example/o/r@1.0.0")
    assert GitRef.looks_like("https://forge.example/o/r", explicit_ref="main")
    assert not GitRef.looks_like("https://forge.example/o/r")  # an archive URL keeps its meaning
    assert not GitRef.looks_like("https://forge.example/o/r-1.0.0.zip")
    assert not GitRef.looks_like("rimuru-branding@1.0.0")
    assert GitRef.looks_like("git://forge.example/o/r"), "recognised so the refusal is the typed one"
    key = checkout_key(GitRef.parse("ssh://forge.example/pkg/Widget@2.3.4"))
    assert key.startswith("Widget-") and key.endswith("@2.3.4") and "/" not in key


# --- the install ------------------------------------------------------------------------------------------


def test_install_from_a_tagged_reference_records_source_commit_and_tier(env):
    url, commit = make_bare_repo(env.scratch)
    typed = Console(input_fn=lambda prompt: ACCEPT_PHRASE if "UNLISTED SOURCE" in prompt else "y", non_interactive=False)
    code, result = execute(env.argv("install", f"{url}@1.0.0"), typed)
    assert code == 0, typed.transcript
    lines = "\n".join(typed.transcript)
    assert UNLISTED_SOURCE_LINE in lines and f"commit {commit[:12]}" in lines, "the extra consent line names the source and the commit"
    assert "unlisted source: typed" in lines
    disclosure = result["disclosure"]
    assert disclosure["tier"] == "unlisted" and disclosure["unlistedSource"]["commit"] == commit and disclosure["unlistedSource"]["ref"] == f"{url}@1.0.0"
    assert result["source"]["kind"] == "git" and result["source"]["commit"] == commit and result["source"]["git"]["tag"] == "1.0.0"
    installed = env.paths.mods / "example-theme"
    assert (installed / "floofy.json").is_file() and not (installed / ".git").exists(), "the checkout is copied without its .git"
    record = read_source(installed)
    assert record["source"] == "git" and record["tier"] == "unlisted" and record["commit"] == commit
    assert record["git"] == {"url": url, "tag": "1.0.0", "ref": None, "subdirectory": None, "commit": commit}
    assert record["sha256"] == manifest_sha256(installed), "sha256 is the canonical-manifest hash a link record pins"
    row = read_audit(env.paths.audit)[-1]
    assert row["op"] == "install" and row["result"] == "ok" and row["tier"] == "unlisted" and row["commit"] == commit
    assert row["source"]["kind"] == "git" and row["source"]["git"]["url"] == url and row["unlistedSource"]["how"] == "typed"
    assert (env.paths.cache_git / checkout_key(GitRef.parse(f"{url}@1.0.0"))).is_dir(), "the shallow clone stays in the cache"


def test_the_unlisted_source_line_is_never_accepted_by_yes_but_by_the_named_flag(env):
    url, commit = make_bare_repo(env.scratch)
    refused = env.run("--yes", "install", f"{url}@1.0.0")
    assert refused.exit == 1 and "unlisted-source line was not confirmed" in refused.stderr and "--yes never accepts it" in refused.stderr
    assert not (env.paths.mods / "example-theme").exists()
    declined = read_audit(env.paths.audit)[-1]
    assert declined["op"] == "install" and declined["result"] == "declined" and declined["source"]["kind"] == "git" and declined["commit"] == commit
    wrong = Console(input_fn=lambda prompt: "yes", non_interactive=False, assume_yes=True)
    code, _ = execute(env.argv("--yes", "install", f"{url}@1.0.0"), wrong)
    assert code == 1 and "unlisted source: not confirmed" in "\n".join(wrong.transcript)
    accepted = env.run("--yes", "install", f"{url}@1.0.0", "--accept-unlisted-source")
    assert accepted.exit == 0, accepted.stderr
    row = read_audit(env.paths.audit)[-1]
    assert row["result"] == "ok" and row["unlistedSource"]["how"] == "flag" and "unlisted source accepted (flag)" in row["detail"]


def test_bare_reference_installs_the_default_branch_and_records_the_commit(env):
    url, commit = make_bare_repo(env.scratch)
    bare = env.run("install", url, "--accept-unlisted-source")
    assert bare.exit == 0, bare.stderr
    record = read_source(env.paths.mods / "example-theme")
    assert record["git"]["tag"] is None and record["git"]["ref"] is None and record["commit"] == commit, "default branch (HEAD), recorded with the commit it resolved to"
    result = env.run("install", url, "--ref", "main", "--accept-unlisted-source")
    assert result.exit == 0, result.stderr
    record = read_source(env.paths.mods / "example-theme")
    assert record["git"]["tag"] is None and record["git"]["ref"] == "main" and record["commit"] == commit
    by_commit = env.run("install", url, "--ref", commit, "--accept-unlisted-source")
    assert by_commit.exit == 0, by_commit.stderr
    assert read_source(env.paths.mods / "example-theme")["commit"] == commit


def test_subdirectory_fragment_and_missing_manifest(env):
    url, commit = make_bare_repo(env.scratch, subdirectory="mods/example-theme")
    missing = env.run("install", f"{url}@1.0.0", "--accept-unlisted-source")
    assert missing.exit == 1 and "no floofy.json" in missing.stderr and "#<subdirectory>" in missing.stderr
    result = env.run("install", f"{url}@1.0.0#mods/example-theme", "--accept-unlisted-source")
    assert result.exit == 0, result.stderr
    record = read_source(env.paths.mods / "example-theme")
    assert record["git"]["subdirectory"] == "mods/example-theme" and record["ref"] == f"{url}@1.0.0#mods/example-theme"


def test_tampered_checkout_is_refused_before_disclosure(env, tmp_path: Path):
    work = env.scratch / "work"
    url, _ = make_bare_repo(env.scratch)
    # a second commit that changes a listed file without updating files[]: the tag moves with it
    (work / "theme" / "theme.json").write_text(json.dumps({"tampered": True}), encoding="utf-8")
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "tamper", cwd=work)
    _git("tag", "-f", "1.0.0", cwd=work)
    _git("push", "-q", "-f", str(env.scratch / "forge.git"), "main", "1.0.0", cwd=work)
    refused = env.run("install", f"{url}@1.0.0", "--accept-unlisted-source")
    assert refused.exit == 1 and "differs from the manifest's files[]" in refused.stderr and "theme/theme.json" in refused.stderr
    assert not (env.paths.mods / "example-theme").exists()


def test_plaintext_and_unknown_repositories_are_refused_with_typed_messages(env):
    plaintext = env.run("install", "git://forge.example/pkg/Widget@1.0.0")
    assert plaintext.exit == 1 and "git:// is not encrypted" in plaintext.stderr
    http = env.run("install", "http://forge.example/o/r.git@1.0.0")
    assert http.exit == 1 and "http:// is not encrypted" in http.stderr
    missing = env.run("install", (env.scratch / "nowhere.git").as_uri() + "@1.0.0", "--accept-unlisted-source")
    assert missing.exit == 1 and "cannot clone" in missing.stderr
    not_git = env.run("install", "rimuru-branding", "--ref", "main")
    assert not_git.exit == 1 and "--ref only applies to a git reference" in not_git.stderr


def test_file_references_need_the_test_switch(env, monkeypatch: pytest.MonkeyPatch):
    url, _ = make_bare_repo(env.scratch)
    monkeypatch.delenv("FLOOFY_ALLOW_LOCAL_GIT")
    refused = env.run("install", f"{url}@1.0.0", "--accept-unlisted-source")
    assert refused.exit == 1 and "file:// references are for the test-suite" in refused.stderr


def test_verify_files_checks_hash_and_size(tmp_path: Path):
    root = tmp_path / "mod"
    root.mkdir()
    (root / "a.txt").write_bytes(b"hello")
    import hashlib  # noqa: PLC0415

    digest = hashlib.sha256(b"hello").hexdigest()
    verify_files(root, [{"path": "a.txt", "sha256": digest, "size": 5}])
    with pytest.raises(GitRefError) as size:
        verify_files(root, [{"path": "a.txt", "sha256": digest, "size": 4}], what="the record")
    assert size.value.code == "FilesMismatch" and "5 bytes ≠ 4" in str(size.value) and "the record" in str(size.value)
    with pytest.raises(GitRefError):
        verify_files(root, [{"path": "b.txt", "sha256": digest}])



# --- link records: the registry path through the same clone (Requirement 8.9) -------------------------------


def _merged_cache(env, entry: dict) -> None:
    """Write a merged ``cache/index.json`` (what ``floofy registry refresh`` produces from one usable source) holding one link-listed mod."""
    document = {"schema": 1, "source": "test:forge", "generatedAt": "2026-09-20T00:00:00Z", "mods": [{"id": "example-theme", "name": "Example theme", "description": "", "authors": ["tests"], "tags": [], "repo": entry["link"]["repo"], "versions": [entry]}]}
    env.paths.cache.mkdir(parents=True, exist_ok=True)
    env.paths.index_cache.write_text(json.dumps({"schema": 1, "merged": True, "generatedAt": "2026-09-20T00:00:00Z", "documents": [{"source": "forge", "key": "forge", "url": "https://forge.example/registry/", "trust": "index", "signature": "verified", "document": document}]}), encoding="utf-8")


def _link_entry(url: str, commit: str, root: Path) -> dict:
    import hashlib  # noqa: PLC0415

    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts and p.name != "floofy.json"):
        data = path.read_bytes()
        files.append({"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    return {"version": "1.0.0", "kirocrew": ">=0.7.0 <0.9.0", "editions": ["internal", "external"], "compat": {}, "dependencies": {"floofycrew": ">=0.0.0"}, "channel": "stable", "publishedAt": "2026-09-20T00:00:00Z", "link": {"repo": url, "commit": commit, "manifestSha256": manifest_sha256(root)}, "files": files}


def test_link_record_installs_through_the_clone_path_as_a_listed_source(env):
    url, commit = make_bare_repo(env.scratch)
    entry = _link_entry(url, commit, env.scratch / "work")
    _merged_cache(env, entry)
    result = env.run("--yes", "--json", "install", "example-theme")
    assert result.exit == 0, result.stderr
    source = result.json["source"]
    assert source["kind"] == "registry" and source["tier"] == "listed" and source["commit"] == commit and source["link"]["manifestSha256"] == entry["link"]["manifestSha256"]
    assert result.json["disclosure"]["tier"] == "listed" and result.json["disclosure"]["unlistedSource"] is None, "a registry record needs no unlisted-source consent"
    record = read_source(env.paths.mods / "example-theme")
    assert record["source"] == "registry" and record["tier"] == "listed" and record["registryKey"] == "example-theme" and record["link"]["commit"] == commit and record["git"]["url"] == url
    row = read_audit(env.paths.audit)[-1]
    assert row["tier"] == "listed" and row["commit"] == commit and row["source"]["link"]["repo"] == url
    theme = next(f for f in entry["files"] if f["path"] == "theme/theme.json")
    assert env.run("--json", "which", theme["sha256"]).json["hits"][0]["mod"] == "example-theme", "hash lookup covers a link record's files (Requirement 8.5)"


def test_link_record_refuses_an_unknown_commit_a_changed_manifest_and_changed_files(env):
    url, commit = make_bare_repo(env.scratch)
    work = env.scratch / "work"
    good = _link_entry(url, commit, work)
    # 1. the record pins a commit the repository does not have: the fetch fails, nothing lands
    _merged_cache(env, {**good, "link": {**good["link"], "commit": "0" * 40}})
    unknown = env.run("--yes", "install", "example-theme")
    assert unknown.exit == 1 and "cannot fetch" in unknown.stderr and not (env.paths.mods / "example-theme").exists()
    # 2. the record's manifest hash differs from the checkout's
    _merged_cache(env, {**good, "link": {**good["link"], "manifestSha256": "f" * 64}})
    manifest = env.run("--yes", "install", "example-theme")
    assert manifest.exit == 1 and "floofy.json" in manifest.stderr and "differs from the registry record" in manifest.stderr
    # 3. a file differs from the record (hash) or is missing
    files = [dict(f) for f in good["files"]]
    files[0]["sha256"] = "e" * 64
    _merged_cache(env, {**good, "files": files})
    hashed = env.run("--yes", "install", "example-theme")
    assert hashed.exit == 1 and "the registry record's files[]" in hashed.stderr
    _merged_cache(env, {**good, "files": [*good["files"], {"path": "extra.txt", "sha256": "a" * 64, "size": 1}]})
    missing = env.run("--yes", "install", "example-theme")
    assert missing.exit == 1 and "extra.txt: missing" in missing.stderr
    # 4. a record naming a plaintext transport is unusable, whatever the index claims
    _merged_cache(env, {**good, "link": {**good["link"], "repo": "git://forge.example/pkg/Example"}})
    plaintext = env.run("--yes", "install", "example-theme")
    assert plaintext.exit == 1 and "link record is unusable" in plaintext.stderr
    # and the intact record still installs
    _merged_cache(env, good)
    assert env.run("--yes", "install", "example-theme").exit == 0


def test_link_record_pins_a_commit_so_the_branch_may_move_on_and_no_tag_is_needed(env):
    """The record is located by its commit (Requirement 8.9): development continues on the branch, tags are not
    consulted, and a record made before commits were pinned (tag only) still resolves at its tag."""
    url, commit = make_bare_repo(env.scratch)
    work = env.scratch / "work"
    good = _link_entry(url, commit, work)
    assert "tag" not in good["link"]
    # the branch moves on after the record was made
    (work / "README.md").write_text("later\n", encoding="utf-8")
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "keep developing", cwd=work)
    _git("push", "-q", url, "HEAD:main", cwd=work)
    head = _git("rev-parse", "HEAD", cwd=work)
    assert head != commit
    _merged_cache(env, good)
    done = env.run("--yes", "install", "example-theme")
    assert done.exit == 0, done.stderr
    installed = env.paths.mods / "example-theme"
    assert not (installed / "README.md").exists(), "the pinned commit's tree landed, not the branch head that added README.md"
    source = read_source(installed)
    assert source["commit"] == commit and source["link"]["commit"] == commit and "tag" not in source["link"], "the install record carries the commit, no tag"
    env.run("--yes", "uninstall", "example-theme")
    # a legacy record: tag, no commit
    legacy = {**good, "link": {"repo": url, "tag": "1.0.0", "manifestSha256": good["link"]["manifestSha256"]}}
    _merged_cache(env, legacy)
    assert env.run("--yes", "install", "example-theme").exit == 0, "cloned at its tag"



def test_a_reference_to_a_tag_the_repository_lacks_names_the_missing_tag(env):
    """An unlisted install (`floofy install <url>@<tag>`) names a tag; git reports a missing one as
    "Remote branch X not found" and the manager says what is missing instead of relaying that."""
    url, _commit = make_bare_repo(env.scratch, tag="1.0.0")
    refused = env.run("install", f"{url}@rimuru-branding-1.0.0", "--accept-unlisted-source")
    assert refused.exit == 1
    assert "no tag or branch named 'rimuru-branding-1.0.0'" in refused.stderr
