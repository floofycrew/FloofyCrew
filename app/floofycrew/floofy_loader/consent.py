"""The one-time consent record — re-exported from the shared core (Requirement 11.1).

The warning text, the version rule and the reader/writer live in
:mod:`floofy_core.consent` so the ``floofy`` CLI (``floofy init``) and the Loader
share one definition without the CLI importing the Loader package. This module
keeps the historical import path for the Loader's own code and tests.
"""
from __future__ import annotations

from floofy_core.consent import (
    ACCEPT_PHRASE,
    REASON_CONSENT_REQUIRED,
    WARNING_TEXT,
    WARNING_VERSION,
    ConsentStatus,
    consent_reference,
    read_consent,
    write_consent,
)

__all__ = [
    "ACCEPT_PHRASE",
    "ConsentStatus",
    "REASON_CONSENT_REQUIRED",
    "WARNING_TEXT",
    "WARNING_VERSION",
    "consent_reference",
    "read_consent",
    "write_consent",
]
