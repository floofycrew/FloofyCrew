"""{{PACKAGE_NAME}}: the build gate of a FloofyCrew mod registry (see ``validate.py``). Data lives at the repository root, not in this module."""
from .validate import GateReport, ReservedNames, find_repository_root, load_reserved_names, reserved_name_hit, validate_registry

__all__ = ["GateReport", "ReservedNames", "find_repository_root", "load_reserved_names", "reserved_name_hit", "validate_registry"]
