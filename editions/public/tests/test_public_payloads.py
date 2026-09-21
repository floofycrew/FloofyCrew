"""Public-edition payload discovery (Requirement 5.1, 10.1; task 3.1).

Fake homes for pipx (both layouts, ``PIPX_HOME``), the managed ``crew-venv`` with
shadow siblings (promoted one current), desktop bundles (``.app`` ``backend-dist``
and ``/opt``), and the real public 0.6.0 wheel venv left by spike 1.5 when present.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from floofy_core.payloads import discover_payloads
from floofy_edition_public.payloads import (
    DesktopBundleProvider,
    ManagedVenvProvider,
    PipxProvider,
    default_providers,
    probe_edition,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
from floofy_testing import fake_payload

SPIKE_VENV = REPO_ROOT / ".scratch" / "spike-5" / "venv"


def test_pipx_both_layouts_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    fake_payload(home / ".local" / "pipx" / "venvs" / "kirocrew", "0.6.0", layout="venv")
    fake_payload(home / ".local" / "share" / "pipx" / "venvs" / "kirocrew", "0.7.0", layout="venv")
    fake_payload(tmp_path / "pipxhome" / "venvs" / "kirocrew", "0.7.0rc2", layout="venv")
    monkeypatch.delenv("PIPX_HOME", raising=False)
    provider = PipxProvider(home=home)
    assert [p.id for p in provider.discover()] == ["venv:kirocrew:0.6.0", "venv:kirocrew:0.7.0"]
    monkeypatch.setenv("PIPX_HOME", str(tmp_path / "pipxhome"))
    payloads = provider.discover()
    assert [p.id for p in payloads][0] == "venv:kirocrew:0.7.0rc2"
    assert payloads[0].channel == "insider" and payloads[0].edition == "external"


def test_managed_venv_promoted_is_current(tmp_path: Path) -> None:
    kiro = tmp_path / ".kiro"
    fake_payload(kiro / "crew-venv", "0.6.0", layout="venv")
    fake_payload(kiro / "crew-venv-0.7.0", "0.7.0", layout="venv")
    (kiro / "crew-venv-0.8.0").mkdir()  # no pyvenv.cfg: a root, not a payload
    (kiro / "crew-venv-broken").mkdir()  # not a version-shaped sibling: ignored
    provider = ManagedVenvProvider(kiro_home=kiro)
    assert [r.name for r in provider.roots()] == ["crew-venv", "crew-venv-0.7.0", "crew-venv-0.8.0"]
    payloads = {p.id: p for p in provider.discover()}
    assert set(payloads) == {"venv:crew-venv:0.6.0", "venv:crew-venv-0.7.0:0.7.0"}
    assert payloads["venv:crew-venv:0.6.0"].current and not payloads["venv:crew-venv-0.7.0:0.7.0"].current


def test_desktop_bundles(tmp_path: Path) -> None:
    apps = tmp_path / "Applications"
    fake_payload(apps / "KiroCrew.app", "0.7.0", layout="nested")
    home = tmp_path / "home"
    fake_payload(home / "Applications" / "KiroCrew Insider.app", "0.7.0rc1", layout="nested")
    opt = tmp_path / "opt"
    fake_payload(opt / "kirocrew", "0.6.0", layout="flat")
    provider = DesktopBundleProvider(home=home, app_dirs=(apps,), opt_dirs=(opt,))
    roots = provider.roots()
    assert roots[0] == apps / "KiroCrew.app" / "Contents" / "Resources" / "backend-dist"
    assert len(roots) == 3
    payloads = provider.discover()
    assert sorted(str(p.host_version) for p in payloads) == ["0.6.0", "0.7.0", "0.7.0rc1"]
    assert all(p.id.startswith("app:") for p in payloads)


def test_default_providers_over_empty_home(tmp_path: Path) -> None:
    result = discover_payloads(default_providers(tmp_path), include_find_spec=False)
    assert result.payloads == []


def test_probe_edition(tmp_path: Path) -> None:
    assert probe_edition(fake_payload(tmp_path / "a", "0.7.0")) == "external"
    assert probe_edition(fake_payload(tmp_path / "b", "0.7.0", build="0.7.0.5")) is None


@pytest.mark.skipif(not (SPIKE_VENV / "pyvenv.cfg").is_file(), reason="spike-5 public venv not present")
def test_real_public_wheel_venv() -> None:
    """The 0.6.0 stable wheel installed by spike 1.5 into .scratch/spike-5/venv."""
    result = discover_payloads([], extra_roots=[SPIKE_VENV], include_find_spec=False, edition_probe=probe_edition)
    assert len(result.payloads) == 1
    payload = result.payloads[0]
    assert str(payload.host_version) == "0.6.0" and payload.channel == "stable"
    assert payload.edition == "external" and payload.has_frontend
