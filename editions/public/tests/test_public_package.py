"""The public release tree built by ``packaging/public/build.py`` and the wheel (task 9.2; Requirement 10.2, 10.5).

The public track ships GitHub release assets — ``floofy.pyz``, the wheel, the Loader
app archive, ``SHA256SUMS`` — plus a generated ``loader-app`` tree, all built from the
shared core and the **public** adapter only. These tests build the tree into
temporary directories, prove two builds byte-identical, install the wheel into a
scratch venv and run ``floofy``, drive ``install.sh`` offline against the built
assets (verification, tamper refusal, the host-venv interpreter preference), and
scan every public artifact for internal identifiers. Nothing touches a live install.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGING = REPO_ROOT / "packaging" / "public"
ADAPTER_REGISTRY = REPO_ROOT / "editions" / "public" / "floofy_edition_public" / "registry.json"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module  # registered before it runs: dataclasses resolve annotations through sys.modules (task 10.9)
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


build_module = _load("public_release_build", PACKAGING / "build.py")

import build_wheel  # noqa: E402  (scripts/ is on the pytest pythonpath)
import check_no_internal_identifiers as checker  # noqa: E402


@pytest.fixture(scope="module")
def release(tmp_path_factory: pytest.TempPathFactory) -> dict:
    out = tmp_path_factory.mktemp("public") / "public"
    result = build_module.build_release(out, supports={"external": {"stable": ["0.6.0"], "insider": ["0.7.0rc5"]}}, tag="v-test")
    result["root"] = out
    return result


def test_layout_assets_and_the_public_only_adapter(release: dict):
    root: Path = release["root"]
    version = release["version"]
    assert {p.name for p in root.iterdir()} == {"README.md", "RELEASE.md", "install.sh", "app-registry.json", ".gitignore", "LICENSE", "NOTICE.md", "LICENSES", "app", "dist"}
    # the notices are the repository's, byte for byte, and ride inside the app tree too (it vendors the themes.py port)
    for name in ("LICENSE", "NOTICE.md", "LICENSES/Apache-2.0.txt"):
        assert (root / name).read_bytes() == (build_module.REPO_ROOT / name).read_bytes(), name
        assert (root / "app" / "floofycrew" / name).is_file(), name
    assert {p.name for p in (root / "dist").iterdir()} == {"floofy.pyz", f"floofycrew-{version}-py3-none-any.whl", f"floofycrew-loader-app-{version}.zip", "SHA256SUMS", "supports.json"}
    app = root / "app" / "floofycrew"
    assert json.loads((app / "app.json").read_text(encoding="utf-8"))["version"] == version
    assert (app / "floofy_edition_public").is_dir() and not (app / "floofy_edition_toolbox").exists()
    with zipfile.ZipFile(root / "dist" / "floofy.pyz") as archive:
        names = archive.namelist()
    assert any(n.startswith("floofy_edition_public/") for n in names) and not any(n.startswith("floofy_edition_toolbox/") for n in names)
    sums = dict(line.split("  ", 1)[::-1] for line in (root / "dist" / "SHA256SUMS").read_text(encoding="utf-8").splitlines())
    assert {"floofy.pyz", f"floofycrew-{version}-py3-none-any.whl", f"floofycrew-loader-app-{version}.zip"} <= set(sums), "asset names are relative to the release (what install.sh verifies)"
    assert sums["floofy.pyz"] == build_module.sha256_file(root / "dist" / "floofy.pyz")
    notes = (root / "RELEASE.md").read_text(encoding="utf-8")
    assert "- external/insider: 0.7.0rc5" in notes and "- external/stable: 0.6.0" in notes and "v-test" in notes and "DR-1" in notes


def test_two_builds_are_byte_identical(release: dict, tmp_path: Path):
    again = build_module.build_release(tmp_path / "public", supports={"external": {"stable": ["0.6.0"], "insider": ["0.7.0rc5"]}}, tag="v-test")
    assert build_module.tree_digest(Path(again["out"])) == build_module.tree_digest(release["root"])
    assert build_wheel.build_bytes(["public"]) == build_wheel.build_bytes(["public"])


def test_public_artifacts_carry_no_internal_identifier(release: dict, tmp_path: Path):
    root: Path = release["root"]
    text_paths = ["app", "install.sh", "README.md", "RELEASE.md", "app-registry.json"]
    hits = checker.scan(root, text_paths)
    assert hits == [], [h.format() for h in hits]
    unpacked = tmp_path / "wheel"
    with zipfile.ZipFile(root / "dist" / f"floofycrew-{release['version']}-py3-none-any.whl") as archive:
        archive.extractall(unpacked)
    hits = checker.scan(unpacked, [p.name for p in unpacked.iterdir()])
    assert hits == [], [h.format() for h in hits]
    assert (unpacked / f"floofycrew-{release['version']}.dist-info" / "entry_points.txt").read_text(encoding="utf-8") == "[console_scripts]\nfloofy = floofy_core.cli.main:main\n"
    assert (unpacked / f"floofycrew-{release['version']}.dist-info" / "top_level.txt").read_text(encoding="utf-8") == "floofy_core\nfloofy_edition_public\n"


def test_wheel_installs_into_a_scratch_venv_and_floofy_runs(release: dict, tmp_path: Path):
    venv = tmp_path / "venv"
    created = subprocess.run([sys.executable, "-m", "venv", str(venv)], capture_output=True, text=True, timeout=180, check=False)
    if created.returncode != 0 or not (venv / "bin" / "python").exists():
        pytest.skip(f"cannot create a venv here: {created.stderr[-300:]}")
    wheel = release["root"] / "dist" / f"floofycrew-{release['version']}-py3-none-any.whl"
    installed = subprocess.run([str(venv / "bin" / "python"), "-m", "pip", "install", "--quiet", "--no-index", "--no-deps", str(wheel)], capture_output=True, text=True, timeout=300, check=False)
    if installed.returncode != 0 and "No module named pip" in installed.stderr:
        pytest.skip("the venv has no pip")
    assert installed.returncode == 0, installed.stderr[-2000:]
    ran = subprocess.run([str(venv / "bin" / "floofy"), "--version"], capture_output=True, text=True, timeout=60, check=False)
    assert ran.returncode == 0 and f"floofy {release['version']}" in ran.stdout and "unofficial" in ran.stdout
    imported = subprocess.run([str(venv / "bin" / "python"), "-c", "import floofy_edition_public, floofy_core; print(floofy_core.__version__)"], capture_output=True, text=True, timeout=60, check=False)
    assert imported.returncode == 0 and imported.stdout.strip() == release["version"]


def _install(release: dict, home: Path, *, asset_dir: Path | None = None, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home), "FLOOFYCREW_ASSET_DIR": str(asset_dir or release["root"] / "dist"), "FLOOFYCREW_NO_INIT": "1", "FLOOFYCREW_PREFIX": str(home / ".local")}
    env.pop("FLOOFY_PYTHON", None)
    env.update(extra_env or {})
    return subprocess.run(["sh", str(release["root"] / "install.sh")], capture_output=True, text=True, env=env, timeout=180, check=False)


def test_install_sh_verifies_installs_and_prefers_the_host_venv_interpreter(release: dict, tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    first = _install(release, home)
    assert first.returncode == 0, first.stderr
    assert "verified floofy.pyz" in first.stdout and f"verified floofycrew-loader-app-{release['version']}.zip" in first.stdout
    wrapper = home / ".local" / "bin" / "floofy"
    lib = home / ".local" / "lib" / "floofycrew"
    assert wrapper.is_file() and os.access(wrapper, os.X_OK) and (lib / "floofy.pyz").is_file() and (lib / f"floofycrew-loader-app-{release['version']}.zip").is_file()
    assert "skipped floofy init" in first.stdout and f"--loader-app {lib}" in first.stdout
    assert not (home / ".kiro").exists(), "nothing is initialised without the acknowledgement"
    ran = subprocess.run([str(wrapper), "--version"], capture_output=True, text=True, env={**os.environ, "HOME": str(home)}, timeout=60, check=False)
    assert ran.returncode == 0 and f"floofy {release['version']}" in ran.stdout
    # a KiroCrew managed venv wins over python3.12 on PATH (the wrapper re-resolves every run)
    fake_venv = home / ".kiro" / "crew-venv" / "bin"
    fake_venv.mkdir(parents=True)
    (fake_venv / "python3.12").symlink_to(Path(sys.executable).resolve())
    second = _install(release, home)
    assert second.returncode == 0 and f"interpreter: {fake_venv / 'python3.12'}" in second.stdout, second.stdout


def test_install_sh_refuses_a_tampered_asset(release: dict, tmp_path: Path):
    tampered = tmp_path / "assets"
    shutil.copytree(release["root"] / "dist", tampered)
    with (tampered / "floofy.pyz").open("ab") as handle:
        handle.write(b"\n# tampered\n")
    home = tmp_path / "home"
    home.mkdir()
    result = _install(release, home, asset_dir=tampered)
    assert result.returncode == 1 and "sha256 mismatch for floofy.pyz" in result.stderr
    assert not (home / ".local").exists(), "nothing is installed after a failed verification"


def test_install_sh_is_https_only_posix_sh():
    text = (PACKAGING / "install.sh").read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n")
    assert "http://" not in text and "--proto '=https'" in text and "--https-only" in text
    assert "--i-accept-the-risk" not in text, "the installer never acknowledges the warning for the user"
    assert subprocess.run(["sh", "-n", str(PACKAGING / "install.sh")], capture_output=True, text=True, check=False).returncode == 0


def test_registry_row_matches_the_public_adapter(release: dict):
    row_file = json.loads((PACKAGING / "registry-row.json").read_text(encoding="utf-8"))
    adapter = json.loads(ADAPTER_REGISTRY.read_text(encoding="utf-8"))
    source = adapter["sources"][0]
    app_row = row_file["appRegistryRow"]
    assert app_row["gitUrl"] == app_row["repo"] == adapter["floofycrewRepo"] and app_row["name"] == "floofycrew" and app_row["subdirectory"] == "app/floofycrew"
    assert app_row["branch"] == build_module.LOADER_APP_BRANCH
    registries_source = row_file["registriesSource"]
    assert registries_source["url"] == source["url"] and registries_source["keyId"] == source["keyId"] == "31ddb18e6ffafef2"
    assert registries_source["publicKey"] == next(k["publicKey"] for k in adapter["keys"] if k["keyId"] == source["keyId"])
    assert registries_source["hostRegistry"] == {"repo": source["repo"], "branch": source["branch"], "name": source["name"]}
    assert registries_source["trust"] == "index" and registries_source["allowUnsigned"] is False
    from floofy_core.registry_sources import Source

    parsed = Source.from_dict(registries_source)
    assert parsed.key_id == "31ddb18e6ffafef2" and parsed.public_key_bytes is not None and parsed.host_registry["branch"] == "main"
    built = json.loads((release["root"] / "app-registry.json").read_text(encoding="utf-8"))
    assert built == [{**app_row, "branch": f"{build_module.LOADER_APP_BRANCH}-v-test"}], "the built tree pins the row to the release's copy of the branch"
    from registry_tools.app_registry import BRANCH_RE, is_git_url

    assert is_git_url(built[0]["gitUrl"]) and BRANCH_RE.match(built[0]["branch"])


def test_release_workflow_builds_verifies_and_publishes():
    text = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    for needle in ('- "v*"', "packaging/public/build.py --check", "check_no_internal_identifiers.py", "pip install --quiet --no-index --no-deps build/public/dist/floofycrew-*.whl", "gh release create", "loader-app", "registry_tools build"):
        assert needle in text, needle
