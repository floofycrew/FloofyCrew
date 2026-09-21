"""``floofy.host`` facts and the ``floofy`` facade (Requirement 3.3, 10.1) — with a fake ``kiro_crew``."""
from __future__ import annotations

import sys
import types
import warnings
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from floofy_core import API_VERSION
from floofy_core.payloads import Payload
from floofy_core.semver import HostVersion, Version
from floofy_loader import api
from floofy_loader.host import HostFacts, default_host_home, read_host_facts


def fake_host(tmp_path: Path, version: str, *, layout: str = "venv") -> types.ModuleType:
    """A stand-in for the imported ``kiro_crew`` package: ``__version__`` (stamp already applied) and ``__file__``."""
    if layout == "venv":
        package_dir = tmp_path / "venv" / "lib" / "python3.12" / "site-packages" / "kiro_crew"
        (tmp_path / "venv").mkdir(parents=True, exist_ok=True)
        (tmp_path / "venv" / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    elif layout == "bundle":
        package_dir = tmp_path / "0.7.0.5" / "lib" / "python3.12" / "site-packages" / "kiro_crew"
    else:
        package_dir = tmp_path / "flat" / "kiro_crew"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    module = types.ModuleType("kiro_crew")
    module.__version__ = version
    module.__file__ = str(package_dir / "__init__.py")
    return module


def test_stamped_internal_build(tmp_path: Path):
    facts = read_host_facts(host=fake_host(tmp_path, "0.7.0.5", layout="bundle"), env={"KIROCREW_HOME": str(tmp_path / "home")}, adapters=[])
    assert facts.version == "0.7.0.5" and facts.build_version == "0.7.0.5"
    assert facts.base_version == Version(0, 7, 0) and facts.host_version == HostVersion.parse("0.7.0.5")
    assert facts.edition == "internal" and facts.profile == "enterprise"
    assert facts.channel is None, "beta/stable is bookkeeping, not bytes: unknown without the adapter"
    assert facts.payload_root == tmp_path / "0.7.0.5" and facts.package_dir == tmp_path / "0.7.0.5" / "lib" / "python3.12" / "site-packages" / "kiro_crew"
    assert facts.host_home == tmp_path / "home" and facts.data_home == tmp_path / "home" / "floofy"
    assert facts.interpreter == Path(sys.executable) and facts.source == "package"
    assert facts.api_version == API_VERSION and facts.to_dict()["base_version"] == "0.7.0"


@pytest.mark.parametrize(
    ("version", "channel"),
    [("0.7.0", "stable"), ("0.7.0rc4", "insider"), ("0.7.0.dev20260918", "nightly"), ("0.7.0-insider.3", "insider"), ("0.7.0-nightly.20260918", "nightly")],
)
def test_public_build_channel_follows_the_host_rule(tmp_path: Path, version: str, channel: str):
    facts = read_host_facts(host=fake_host(tmp_path, version), env={}, adapters=[])
    assert facts.build_version is None and facts.channel == channel
    assert facts.edition == "external" and facts.profile == "standalone"
    assert facts.payload_root == tmp_path / "venv"  # pyvenv.cfg wins


def test_profile_env_override_and_flat_layout(tmp_path: Path):
    facts = read_host_facts(host=fake_host(tmp_path, "0.7.0", layout="flat"), env={"KIROCREW_PROFILE": "enterprise"}, adapters=[])
    assert facts.profile == "enterprise" and facts.payload_root == tmp_path / "flat"


def test_adapter_recognising_the_payload_supplies_channel_and_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("FLOOFY_NO_ADAPTERS", raising=False)
    host = fake_host(tmp_path, "0.7.0.5", layout="bundle")
    package_dir = Path(host.__file__).parent

    class Provider:
        name = "fake-bookkeeping"

        def roots(self):
            return [package_dir.parent]

        def discover(self):
            return [Payload("fake:0.7.0.5", tmp_path / "0.7.0.5", package_dir, package_dir / "static" / "dist", HostVersion.parse("0.7.0.5"), "internal", "beta", None, True, self.name)]

    adapter = types.ModuleType("floofy_edition_fake")
    adapter.EDITION = "internal"
    adapter.providers = lambda: [Provider()]
    adapter.probe_edition = lambda p: "internal" if p == package_dir else None
    facts = read_host_facts(host=host, env={}, adapters=[adapter])
    assert facts.channel == "beta" and facts.payload_id == "fake:0.7.0.5" and facts.source == "adapter"
    assert facts.edition == "internal"


def test_without_a_host_the_facts_are_placeholders(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "kiro_crew", None)  # import fails cleanly
    facts = read_host_facts(env={}, adapters=[])
    assert facts.version == "0.0.0" and facts.package_dir is None and facts.payload_root is None
    assert any("not importable" in note for note in facts.notes)


def test_default_host_home():
    assert default_host_home({}) == Path.home() / ".kiro" / "crew"
    assert default_host_home({"KIROCREW_HOME": "/tmp/x"}) == Path("/tmp/x")


@settings(max_examples=60, deadline=None)
@given(
    major=st.integers(0, 20),
    minor=st.integers(0, 40),
    patch=st.integers(0, 40),
    suffix=st.sampled_from(["", ".{n}", "rc{n}", ".dev{n}", "-insider.{n}", "-nightly.{n}"]),
    n=st.integers(0, 99),
)
def test_facts_never_raise_and_agree_with_the_version_grammar(tmp_path_factory, major, minor, patch, suffix, n):
    version = f"{major}.{minor}.{patch}" + suffix.format(n=n)
    module = types.ModuleType("kiro_crew")
    module.__version__ = version
    module.__file__ = None
    facts = read_host_facts(host=module, env={}, adapters=[])
    parsed = HostVersion.parse(version)
    assert facts.base_version == parsed.base
    assert (facts.build_version is not None) == (parsed.build is not None)
    if parsed.build is None:
        assert facts.channel in {"stable", "insider", "nightly"}


# --- the facade ------------------------------------------------------------------------


def test_facade_exposes_the_api_surface_and_the_alias():
    api.install_alias()
    import floofy  # noqa: PLC0415

    assert floofy is api and floofy.api_version == API_VERSION and floofy.unofficial is True
    assert isinstance(floofy.host, HostFacts)
    with pytest.raises(RuntimeError, match="once the Loader has booted"):
        floofy.fetch("https://example.com/")


def test_bind_publishes_live_facts(tmp_path: Path):
    facts = read_host_facts(host=fake_host(tmp_path, "0.7.0"), env={}, adapters=[])
    previous = api.host
    try:
        api.bind(host_facts=facts, fetch_impl=lambda url, **kw: ("fetched", url), event_bus="bus")
        assert api.host is facts and api.fetch("https://a.example/") == ("fetched", "https://a.example/") and api.events == "bus"
    finally:
        api.bind(host_facts=previous, fetch_impl=api._not_bound, event_bus=None)
        api.events = None


def test_deprecated_names_warn_naming_the_caller(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(api.DEPRECATED_NAMES, "version_of_host", ("host", "1.2.0"))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        value = api.version_of_host
    assert value is api.host
    messages = [str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)]
    assert messages and "floofy.version_of_host is deprecated, use floofy.host (removed in FloofyCrew 1.2.0)" in messages[0]
    assert __name__ in messages[0], "the warning names the calling module"
    with pytest.raises(AttributeError):
        api.no_such_name  # noqa: B018
