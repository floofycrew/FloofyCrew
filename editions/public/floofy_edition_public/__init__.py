"""FloofyCrew edition adapter for the public open-source distribution.

Owns payload discovery (pipx / managed venvs / desktop bundles), host-version
derivation (``kiro_crew.__version__`` + channel marker), the venv-site early
shim location, the re-apply triggers (user timer, ``kirocrew`` PATH wrapper) and
the public registry endpoint.
"""

EDITION = "external"
