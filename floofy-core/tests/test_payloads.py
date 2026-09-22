"""Payload discovery tests (Requirement 5.1, 10.1; task 3.1).

Temp-dir fakes for every layout the core walk must resolve (flat, venv
``site-packages``, deeply nested desktop bundle), the host's version and channel
rules, de-duplication across providers and the miss diagnostic that lists every
searched root. Edition-specific providers are tested in their adapters.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from floofy_core.payloads import (
    DiscoveryResult,
    ExtraRootProvider,
    FindSpecProvider,
    Payload,
    channel_for_version,
    default_edition_probe,
    discover_payloads,
    find_package_dirs,
    make_payload,
    path_token,
    read_host_version,
    venv_site_packages,
)
from floofy_core.semver import HostVersion, InvalidVersion

from floofy_testing import fake_payload


# --- the walk ------------------------------------------------------------------------


def test_walk_finds_flat_venv_and_nested_layouts(tmp_path: Path) -> None:
    flat = fake_payload(tmp_path / "flat", "0.7.0", layout="flat")
    venv = fake_payload(tmp_path / "venv", "0.7.0", layout="venv")
    nested = fake_payload(tmp_path / "bundle", "0.7.0", layout="nested")
    for root, package_dir in ((tmp_path / "flat", flat), (tmp_path / "venv", venv), (tmp_path / "bundle", nested)):
        assert find_package_dirs(root) == [package_dir]


def test_walk_ignores_non_package_dirs_and_pruned_trees(tmp_path: Path) -> None:
    (tmp_path / "kiro_crew").mkdir()  # no __init__.py: a leftover, not a payload
    (tmp_path / "node_modules" / "kiro_crew").mkdir(parents=True)
    (tmp_path / "node_modules" / "kiro_crew" / "__init__.py").write_text("__version__ = '9.9.9'\n")
    (tmp_path / ".hidden" / "kiro_crew").mkdir(parents=True)
    (tmp_path / ".hidden" / "kiro_crew" / "__init__.py").write_text("__version__ = '9.9.9'\n")
    assert find_package_dirs(tmp_path) == []
    assert find_package_dirs(tmp_path / "missing") == []


def test_walk_respects_max_depth(tmp_path: Path) -> None:
    deep = tmp_path.joinpath(*[f"d{i}" for i in range(6)])
    fake_payload(deep, "0.7.0", layout="flat")
    assert find_package_dirs(tmp_path, max_depth=3) == []
    assert len(find_package_dirs(tmp_path)) == 1


def test_venv_site_packages_needs_pyvenv_cfg(tmp_path: Path) -> None:
    fake_payload(tmp_path / "venv", "0.7.0", layout="venv")
    assert venv_site_packages(tmp_path / "venv") == tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
    (tmp_path / "venv" / "pyvenv.cfg").unlink()
    assert venv_site_packages(tmp_path / "venv") is None


# --- version and channel -------------------------------------------------------------


def test_read_host_version_from_literal(tmp_path: Path) -> None:
    package_dir = fake_payload(tmp_path, "0.7.0")
    assert read_host_version(package_dir) == HostVersion.parse("0.7.0")


@pytest.mark.parametrize(
    "stamp,expected",
    [
        ("0.7.0.5", "0.7.0.5"),  # base + one numeric segment: honoured
        ("0.7.0.5\n", "0.7.0.5"),  # whitespace stripped
        ("0.7.0", "0.7.0"),  # the base itself
        ("0.7.1.2", "0.7.0"),  # a different base: ignored
        ("0.7.0.5.1", "0.7.0"),  # a second segment: ignored
        ("0.7.0rc1", "0.7.0"),  # a prerelease stamp: ignored
        ("", "0.7.0"),  # empty: ignored
        ("x" * 70, "0.7.0"),  # oversized: ignored
    ],
)
def test_build_version_stamp_follows_host_rule(tmp_path: Path, stamp: str, expected: str) -> None:
    package_dir = fake_payload(tmp_path, "0.7.0", build=stamp)
    assert str(read_host_version(package_dir)) == expected


def test_stamp_over_prerelease_base_is_ignored(tmp_path: Path) -> None:
    package_dir = fake_payload(tmp_path, "0.7.0rc1", build="0.7.0rc1.3")
    assert str(read_host_version(package_dir)) == "0.7.0rc1"


def test_read_host_version_errors(tmp_path: Path) -> None:
    package_dir = tmp_path / "kiro_crew"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("x = 1\n")
    with pytest.raises(InvalidVersion):
        read_host_version(package_dir)
    with pytest.raises(InvalidVersion):
        read_host_version(tmp_path / "nowhere")


@pytest.mark.parametrize(
    "version,channel",
    [
        ("0.7.0", "stable"),
        ("0.7.0.5", "stable"),
        ("0.7.0rc4", "insider"),
        ("0.7.0-insider.4", "insider"),
        ("0.7.0-rc.2", "insider"),
        ("0.8.0.dev20260919", "nightly"),
        ("0.8.0-nightly.20260919", "nightly"),
    ],
)
def test_channel_for_version_matches_host_rule(version: str, channel: str) -> None:
    assert channel_for_version(HostVersion.parse(version)) == channel


def test_default_edition_probe(tmp_path: Path) -> None:
    assert default_edition_probe(fake_payload(tmp_path / "a", "0.7.0")) == "external"
    assert default_edition_probe(fake_payload(tmp_path / "b", "0.7.0", build="0.7.0.5")) == "unknown"


# --- make_payload and providers ------------------------------------------------------


def test_make_payload_derives_everything(tmp_path: Path) -> None:
    package_dir = fake_payload(tmp_path / "venv", "0.7.0rc1", layout="venv")
    payload = make_payload(kind="venv", root=tmp_path / "venv", package_dir=package_dir, source="t", discriminator="venv")
    assert payload.id == "venv:venv:0.7.0rc1"
    assert payload.dist_dir == package_dir / "static" / "dist"
    assert payload.has_frontend and payload.assets_dir.is_dir()
    assert payload.channel == "insider" and payload.edition == "external"
    assert payload.interpreter == tmp_path / "venv" / "bin" / "python"
    assert not payload.current
    assert "dormant" in payload.describe()


def test_extra_root_provider_lists_roots_and_finds_payloads(tmp_path: Path) -> None:
    one = fake_payload(tmp_path / "one", "0.7.0", layout="flat")
    two = fake_payload(tmp_path / "two", "0.6.0", layout="nested", build="0.6.0.16")
    provider = ExtraRootProvider([tmp_path / "one", tmp_path / "two", tmp_path / "empty"])
    assert provider.roots() == [tmp_path / "one", tmp_path / "two", tmp_path / "empty"]
    payloads = provider.discover()
    assert [p.id for p in payloads] == [f"root:{path_token(one)}:0.7.0", f"root:{path_token(two)}:0.6.0.16"]
    assert payloads[1].edition == "unknown"  # stamped build, no adapter probe


def test_extra_root_with_two_package_trees_gets_two_ids(tmp_path: Path) -> None:
    """A bundle root holding two site-packages farms yields two distinct payload ids."""
    root = tmp_path / "bundle"
    for minor in ("3.10", "3.12"):
        package_dir = root / "lib" / f"python{minor}" / "site-packages" / "kiro_crew"
        (package_dir / "static" / "dist").mkdir(parents=True)
        (package_dir / "__init__.py").write_text('__version__ = "0.7.0"\n', encoding="utf-8")
    payloads = ExtraRootProvider([root]).discover()
    assert len(payloads) == 2 and len({p.id for p in payloads}) == 2


def test_edition_probe_is_consulted(tmp_path: Path) -> None:
    fake_payload(tmp_path / "one", "0.7.0", build="0.7.0.5")
    provider = ExtraRootProvider([tmp_path / "one"], edition_probe=lambda p: "internal")
    assert provider.discover()[0].edition == "internal"


def test_discover_payloads_merges_dedups_and_reports_roots(tmp_path: Path) -> None:
    package_dir = fake_payload(tmp_path / "one", "0.7.0", layout="flat")

    class Fake:
        name = "fake"

        def roots(self) -> list[Path]:
            return [tmp_path / "one", tmp_path / "elsewhere"]

        def discover(self) -> list[Payload]:
            return [make_payload(kind="fake", root=tmp_path / "one", package_dir=package_dir, source="fake", current=True)]

    result = discover_payloads([Fake()], extra_roots=[tmp_path / "one"], include_find_spec=False)
    assert [p.id for p in result.payloads] == ["fake:0.7.0"]  # the extra-root duplicate was merged
    assert result.payloads[0].current and result.current is result.payloads[0]
    assert result.searched_roots == [tmp_path / "one", tmp_path / "elsewhere"]
    assert result.by_id("fake:0.7.0") is not None and result.by_id("nope") is None


def test_current_flag_is_or_ed_across_duplicates(tmp_path: Path) -> None:
    package_dir = fake_payload(tmp_path / "one", "0.7.0")

    class First:
        name = "first"

        def roots(self) -> list[Path]:
            return []

        def discover(self) -> list[Payload]:
            return [make_payload(kind="a", root=tmp_path / "one", package_dir=package_dir, source="first")]

    class Second(First):
        name = "second"

        def discover(self) -> list[Payload]:
            return [make_payload(kind="b", root=tmp_path / "one", package_dir=package_dir, source="second", current=True)]

    result = discover_payloads([First(), Second()], include_find_spec=False)
    assert [p.id for p in result.payloads] == ["a:0.7.0"] and result.payloads[0].current


def test_miss_lists_every_searched_root(tmp_path: Path) -> None:
    result = discover_payloads([], extra_roots=[tmp_path / "a", tmp_path / "b"], include_find_spec=False)
    assert result.payloads == []
    text = result.format_miss()
    assert str(tmp_path / "a") in text and str(tmp_path / "b") in text and "--root" in text
    assert "no payload roots" in DiscoveryResult().format_miss()


def test_provider_errors_become_notes(tmp_path: Path) -> None:
    class Broken:
        name = "broken"

        def roots(self) -> list[Path]:
            raise OSError("boom")

        def discover(self) -> list[Payload]:
            raise OSError("bang")

    result = discover_payloads([Broken()], include_find_spec=False)
    assert result.payloads == [] and len(result.notes) == 2


def test_find_spec_provider_in_this_interpreter_without_host() -> None:
    """The test interpreter has no ``kiro_crew``: the provider must answer cleanly, not raise."""
    provider = FindSpecProvider()
    if any((Path(p) / "kiro_crew" / "__init__.py").is_file() for p in sys.path if p):
        pytest.skip("kiro_crew importable here; the miss behaviour cannot be observed")
    assert provider.roots() == [] and provider.discover() == []


def test_find_spec_provider_with_another_interpreter(tmp_path: Path) -> None:
    """A stand-in interpreter whose site holds the package: the provider asks it with a subprocess."""
    package_dir = fake_payload(tmp_path / "venv", "0.7.0", layout="venv")
    shim = tmp_path / "python-shim"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import os, subprocess, sys\n"
        "args = [a for a in sys.argv[1:] if a != '-I']  # this stand-in has the package on its site\n"
        f"env = dict(os.environ, PYTHONPATH={str(package_dir.parent)!r})\n"
        "sys.exit(subprocess.call([sys.executable, *args], env=env))\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    provider = FindSpecProvider(interpreter=shim, name="spec")
    assert provider.roots() == [package_dir.parent]
    payloads = provider.discover()
    assert [p.id for p in payloads] == ["spec:0.7.0"]
    assert payloads[0].interpreter == shim and payloads[0].source == "spec"


# --- properties ----------------------------------------------------------------------


@settings(max_examples=60, deadline=None)
@given(
    major=st.integers(0, 20),
    minor=st.integers(0, 20),
    patch=st.integers(0, 20),
    build=st.integers(0, 999),
)
def test_stamp_with_one_segment_is_always_honoured(tmp_path_factory: pytest.TempPathFactory, major: int, minor: int, patch: int, build: int) -> None:
    root = tmp_path_factory.mktemp("stamp")
    base = f"{major}.{minor}.{patch}"
    package_dir = fake_payload(root, base, build=f"{base}.{build}")
    version = read_host_version(package_dir)
    assert version.build == build and str(version.base) == base


@given(st.text(min_size=1, max_size=40))
def test_path_token_is_short_and_deterministic(name: str) -> None:
    token = path_token(Path("/x") / name)
    assert len(token) == 8 and token == path_token(Path("/x") / name)



def test_make_payload_canonicalizes_symlinked_spellings(tmp_path: Path) -> None:
    """Two processes reaching one install through different $HOME spellings derive identical paths.

    The gateway sees ``/home/x/…`` (a symlink) while a shell spells ``/local/home/x/…``;
    without one canonical spelling the deployment manifest split per spelling and an
    apply committed index.html as two work items (the later dropping the boot script).
    """
    real = tmp_path / "real"
    package_dir = fake_payload(real, "0.7.0")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    via_link = make_payload(kind="test", root=link, package_dir=link / "kiro_crew", source="t")
    via_real = make_payload(kind="test", root=real, package_dir=package_dir, source="t")
    assert via_link.root == via_real.root == real.resolve()
    assert via_link.package_dir == via_real.package_dir
    assert via_link.index_html == via_real.index_html
    assert via_link.id == via_real.id
