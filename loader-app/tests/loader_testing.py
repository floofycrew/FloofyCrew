"""Shared helpers for the Loader tests (plain module on the pytest pythonpath).

Importing this module arms the same live-install guard as the core tests
(``floofy_testing`` sets ``FLOOFY_NO_ADAPTERS=1``): no Loader test may reach a
real host install. The gateway-backed tests run against a **copy** of a host
payload under ``.scratch/`` with scratch ``KIROCREW_HOME`` / ``KIRO_HOME`` and
skip cleanly when no copy exists (design "Spike outcomes" 1.3/1.5 recipe).
"""
from __future__ import annotations

import os
from pathlib import Path

from floofy_testing import REPO_ROOT, live_fingerprint  # noqa: F401 (arms FLOOFY_NO_ADAPTERS)

LOADER_APP_DIR = REPO_ROOT / "loader-app"
EXAMPLES_DIR = REPO_ROOT / "floofy-core" / "examples"

#: Where the gateway-backed tests expect a payload copy; ``FLOOFY_SCRATCH_PAYLOAD`` overrides.
SCRATCH_PAYLOAD_ENV = "FLOOFY_SCRATCH_PAYLOAD"
DEFAULT_SCRATCH_PAYLOAD = REPO_ROOT / ".scratch" / "payload-0.7.0.5"


def scratch_payload() -> Path | None:
    """The payload copy to test against, or ``None`` when there is none (tests skip)."""
    candidate = Path(os.environ.get(SCRATCH_PAYLOAD_ENV) or DEFAULT_SCRATCH_PAYLOAD).resolve()
    if (candidate / "bin" / "kirocrew").is_file() and site_packages(candidate) is not None:
        return candidate
    return None


def site_packages(payload: Path) -> Path | None:
    """The ``site-packages`` holding ``kiro_crew`` for the interpreter the launcher picks."""
    interpreter = bundle_interpreter(payload)
    if interpreter is None:
        return None
    minor = interpreter.name.removeprefix("python")
    for candidate in sorted(payload.glob(f"**/python{minor}/site-packages")):
        if (candidate / "kiro_crew" / "__init__.py").is_file():
            return candidate
    return None


def bundle_interpreter(payload: Path) -> Path | None:
    """The highest-minor real (non-symlink) ``python3.X`` in the payload, as ``bin/kirocrew`` selects it."""
    best: tuple[int, Path] | None = None
    for candidate in [*payload.glob("bin/python3.*"), *payload.glob("*/bin/python3.*")]:
        if candidate.is_symlink() or not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue
        suffix = candidate.name.removeprefix("python3.")
        if not suffix.isdigit():
            continue
        if best is None or int(suffix) > best[0]:
            best = (int(suffix), candidate)
    return best[1] if best else None
