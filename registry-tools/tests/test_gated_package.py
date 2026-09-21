"""The gated registry package (task 7.9; Requirement 8.7, 8.10, 10.5).

``init_registry_repo`` renders, for an edition whose adapter config carries a
``registryPackage`` block, a self-contained data package whose build is the
merge gate: the gate module and its pytest suite (``templates/registry-gate/``,
edition-neutral), the adapter's own build files, CR template and reserved names,
the pinned signing key record, the vendored FloofyCrew tooling and ``RENDERED.md``.
The rendered gate is exercised in a fresh interpreter (its own ``pytest`` and
``python -m <module>``) against a local repository standing in for the forge.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from registry_tools.bootstrap import init_registry_repo
from registry_tools.readme import MARK_BEGIN, MARK_END, readme_matches, render_table, update_readme
from registry_tools.repo import RegistryRepo

from registry_testing import REPO_ROOT

#: The tag the seeder expects for the first-party theme: ``<id>-<version>`` from its manifest.
RIMURU_TAG = "rimuru-branding-" + json.loads((REPO_ROOT / "mods" / "rimuru-branding" / "floofy.json").read_text(encoding="utf-8"))["version"]
from test_link_records import _git, point_forge_at

pytestmark = pytest.mark.registry

GATED_CONFIGS = {p: json.loads(p.read_text(encoding="utf-8")) for p in REPO_ROOT.glob("editions/*/floofy_edition_*/registry.json") if "registryPackage" in json.loads(p.read_text(encoding="utf-8"))}


def _gate_env(extra: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG")}
    env.update(extra)
    env["FLOOFY_ALLOW_LOCAL_GIT"] = "1"
    return env


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory):
    """The gated package rendered against a local stand-in for the forge; yields ``(target, config, git env)``."""
    assert GATED_CONFIGS, "an edition adapter declares a registryPackage block"
    config_path, config = sorted(GATED_CONFIGS.items())[0]
    tmp_path = tmp_path_factory.mktemp("gated")
    work = tmp_path / "package"
    shutil.copytree(REPO_ROOT / "mods" / "rimuru-branding", work / "mods" / "rimuru-branding")
    _git("init", "-q", "-b", "mainline", cwd=work)
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "ship the mod source", cwd=work)
    _git("tag", RIMURU_TAG, cwd=work)
    bare = tmp_path / "forge.git"
    _git("clone", "-q", "--bare", str(work), str(bare), cwd=tmp_path)
    git_env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"url.{bare.as_uri()}.insteadOf", "GIT_CONFIG_VALUE_0": str(config["floofycrewRepo"])}
    # the adapter's own config, but with the package files resolved to their real directory and a key we hold for signing
    from floofy_core.signing import generate_keypair  # noqa: PLC0415

    pair = generate_keypair()
    pair.save_private(tmp_path / "key.json")
    custom = json.loads(json.dumps(config))
    custom["sources"][0]["keyId"] = pair.key_id
    custom["keys"] = [pair.public_record()]
    custom["registryPackage"]["files"] = str((config_path.parent / config["registryPackage"]["files"]).resolve())
    custom_path = tmp_path / "registry.json"
    custom_path.write_text(json.dumps(custom), encoding="utf-8")
    target = tmp_path / "registry"
    saved = {k: os.environ.get(k) for k in git_env}
    os.environ.update(git_env)
    os.environ["FLOOFY_ALLOW_LOCAL_GIT"] = "1"
    try:
        # `contact` is passed explicitly: the manifest's `authors` carry a display
        # name, which deliberately does NOT fit the internal edition's alias shape
        summary = init_registry_repo(target, edition_config=custom_path, floofycrew_root=REPO_ROOT, seed_mods=["rimuru-branding"], host_version="0.7.0.5", channel="beta", verdict="tested", link_source=work, sign_key=tmp_path / "key.json", contact="modauthor")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return target, custom, git_env, summary


def test_the_rendered_package_has_the_documented_layout(rendered):
    target, config, _env, summary = rendered
    block = config["registryPackage"]
    gate = summary["gate"]
    assert gate["package"] == block["name"] and gate["module"] == block["gateModule"] and gate["vendored"] > 50
    module_dir = target / "src" / block["gateModule"]
    for relative in ("Config", "setup.py", "setup.cfg", "pyproject.toml", ".crux_template.md", "CONTRIBUTING.md", "README.md", "RENDERED.md", "schema/reserved-names.json", "schema/signing-key.pub.json", "test/test_registry_gate.py", "vendor/floofy_core/__init__.py", "vendor/registry_tools/__init__.py"):
        assert (target / relative).is_file(), relative
    assert (module_dir / "validate.py").is_file() and (module_dir / "__main__.py").is_file()
    for path in [*target.glob("*.md"), *target.glob("*.cfg"), *target.glob("*.toml"), target / "Config", *module_dir.glob("*.py"), *(target / "test").glob("*.py"), *(target / "schema").glob("*.json")]:
        assert "{{" not in path.read_text(encoding="utf-8"), f"unrendered placeholder in {path.name}"
    assert f"package.{block['name']} = " in (target / "Config").read_text(encoding="utf-8")
    assert block["gateCommand"] in (target / "README.md").read_text(encoding="utf-8")
    # the community bar (Requirement 8.10), item by item
    contributing = (target / "CONTRIBUTING.md").read_text(encoding="utf-8")
    for phrase in ("`floofy validate` clean", "`license` in `floofy.json`", "no\n   third-party artwork", "Not masquerade", "reserved-names.json", "`network.hosts[]`", "encrypted only", "Name a `contact`", "Bump `version`"):
        assert phrase in contributing, phrase
    assert config["repoUrlPattern"] in contributing
    # the seeded record names a contact; the key record is the pinned one; the vendored copies are byte copies
    curated = json.loads((target / "mods" / "rimuru-branding" / "mod.json").read_text(encoding="utf-8"))
    manifest = json.loads((REPO_ROOT / "mods" / "rimuru-branding" / "floofy.json").read_text(encoding="utf-8"))
    assert curated["contact"] == "modauthor", "the seeded contact is the explicit --contact (the manifest's display-name author does not fit the edition's contact shape)"
    assert not re.match(str(config["registryPackage"]["contactPattern"]), manifest["authors"][0]), "the manifest's author is a display name, not an alias — seeding needs --contact"
    assert json.loads((target / "schema" / "signing-key.pub.json").read_text(encoding="utf-8"))["keyId"] == config["sources"][0]["keyId"]
    rendered_md = (target / "RENDERED.md").read_text(encoding="utf-8")
    for package in ("floofy_core", "registry_tools"):
        source = REPO_ROOT / ("floofy-core" if package == "floofy_core" else "registry-tools") / package / "__init__.py"
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        assert (target / "vendor" / package / "__init__.py").read_bytes() == source.read_bytes()
        assert f"`vendor/{package}/__init__.py` | `{digest}`" in rendered_md
    assert "FloofyCrew source: commit `" in rendered_md
    reserved = json.loads((target / "schema" / "reserved-names.json").read_text(encoding="utf-8"))
    assert reserved["leadingToken"] == sorted(set(reserved["leadingToken"])) and "floofycrew" in reserved["leadingToken"] and reserved["exactOnly"] == sorted(set(reserved["exactOnly"]))
    # the README table is generated and in sync
    readme = (target / "README.md").read_text(encoding="utf-8")
    assert MARK_BEGIN in readme and MARK_END in readme and f"| `rimuru-branding` — Rimuru branding | {RIMURU_TAG.rsplit('-', 1)[1]} | link:" in readme
    assert readme_matches(RegistryRepo.load(target))[0]


def test_the_rendered_gate_passes_in_a_fresh_interpreter_and_rejects_a_bad_record(rendered, tmp_path: Path):
    target, config, git_env, _summary = rendered
    module = config["registryPackage"]["gateModule"]
    env = _gate_env({**git_env, "PYTHONPATH": "src"})
    gate = subprocess.run([sys.executable, "-m", module], cwd=str(target), env=env, capture_output=True, text=True, timeout=300)
    assert gate.returncode == 0, gate.stdout + gate.stderr
    assert "1 record(s), 1 cloned" in gate.stdout and gate.stdout.rstrip().endswith("OK")
    suite = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test"], cwd=str(target), env=env, capture_output=True, text=True, timeout=600)
    assert suite.returncode == 0, suite.stdout[-3000:] + suite.stderr[-2000:]
    assert "8 passed" in suite.stdout
    # offline: the clone step is skipped and says so; a broken record still fails without a clone
    offline = subprocess.run([sys.executable, "-m", module, "--no-clone"], cwd=str(target), env=env, capture_output=True, text=True, timeout=120)
    assert offline.returncode == 0 and "clone step skipped" in offline.stdout
    broken = tmp_path / "broken"
    shutil.copytree(target, broken, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    release_path = broken / "mods" / "rimuru-branding" / RIMURU_TAG.rsplit("-", 1)[1] / "release.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release["link"]["repo"] = "ssh://git.example.evil/pkg/FloofyCrew"
    release_path.write_text(json.dumps(release), encoding="utf-8")
    refused = subprocess.run([sys.executable, "-m", module, str(broken), "--no-clone"], cwd=str(broken), env=env, capture_output=True, text=True, timeout=120)
    assert refused.returncode == 1 and "outside this registry's forge pattern" in refused.stdout


def test_readme_table_rendering_and_check(tmp_path: Path):
    index = {"schema": 1, "source": "t", "generatedAt": "2026-09-20T00:00:00Z", "mods": [
        {"id": "b-mod", "name": "B | pipe", "description": "", "authors": [], "tags": [], "repo": "https://x", "versions": [{"version": "1.0.0", "kinds": ["theme"], "compat": {"0.7.0.5": "tested"}, "link": {"repo": "ssh://forge.example/pkg/B", "tag": "b-mod-1.0.0"}}, {"version": "0.9.0", "compat": {}, "files": [{"url": "https://x/a.zip"}]}]},
        {"id": "a-mod", "name": "A", "description": "", "authors": [], "tags": [], "repo": "https://x", "versions": []},
    ]}
    table = render_table(index)
    lines = table.splitlines()
    assert lines[0] == MARK_BEGIN and lines[-1] == MARK_END
    assert "| `a-mod` — A | — | — | — | — | no matrix cell yet |" in lines
    assert "| `b-mod` — B \\| pipe | 1.0.0 | link: `ssh://forge.example/pkg/B` at `b-mod-1.0.0` | theme | — | 1.0.0 on 0.7.0.5: tested |" in lines
    (tmp_path / "registry.json").write_text(json.dumps({"schema": 1, "source": "t"}), encoding="utf-8")
    (tmp_path / "index.json").write_text(json.dumps(index), encoding="utf-8")
    (tmp_path / "README.md").write_text("# T\n\nintro\n\n<!-- mods:begin -->\nold\n<!-- mods:end -->\n\ntail\n", encoding="utf-8")
    repo = RegistryRepo.load(tmp_path)
    assert readme_matches(repo)[0] is False
    changed, _ = update_readme(repo)
    assert changed and readme_matches(repo)[0] and (tmp_path / "README.md").read_text(encoding="utf-8").endswith("<!-- mods:end -->\n\ntail\n")
    assert update_readme(repo)[0] is False, "idempotent"
    (tmp_path / "README.md").write_text("# no markers\n", encoding="utf-8")
    ok, message = readme_matches(repo)
    assert not ok and "no <!-- mods:begin -->" in message
    update_readme(repo)
    assert readme_matches(repo)[0], "the block is appended when the README has no markers"
