"""Detached-signature verification for registry documents — pure Python Ed25519 (Requirement 8.3, 11.6).

The manager and the Loader run on the host's own interpreter with the standard
library only (Requirement 7.2), and ``hashlib``/``hmac`` offer no public-key
verification, so Ed25519 (RFC 8032 §5.1) is implemented here: ~120 lines of
field arithmetic on Python integers, exercised by the RFC's own §7.1 test
vectors in the test suite. Performance is irrelevant — one signature per
refresh, a few milliseconds each.

Key material is 32-byte Ed25519 public keys; a **key id** is the first 16 hex
digits of ``sha256(public key bytes)`` (:func:`key_id`). The adapters pin the
registry keys per edition (``floofy_edition_*.registry_keys()``), a user may pin
one per source (``floofy registry add --public-key``), and :func:`verify_detached`
only ever accepts the keys it is handed.

The signature document (``index.json.sig`` / ``compat.json.sig``)::

    {"schema": 1, "alg": "ed25519", "keyId": "<16 hex>", "signature": "<base64, 64 bytes>",
     "signedAt": "2026-09-19T08:00:00Z", "canonical": "json-c14n-1"}

is verified over the :mod:`floofy_core.canonical` bytes of the *parsed* payload,
so whitespace and key order of the served file never matter. Verdicts are the
:mod:`floofy_core.registry_sources` statuses: ``verified``, ``invalid`` (any
defect — wrong key, unknown key, bad encoding, tampered bytes — is one verdict
with the reason in ``detail``, never an exception) and ``unsigned``.

Signing lives in :mod:`floofy_core.signing` (the maintainer's and the Forge's
side); it reuses the curve arithmetic below.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .canonical import CANONICAL_FORM, CanonicalError, canonical_bytes, canonical_bytes_of_json

__all__ = [
    "ALG",
    "KEY_ID_HEX",
    "SIGNATURE_SCHEMA",
    "SignatureDocument",
    "SignatureError",
    "VerifyResult",
    "ed25519_verify",
    "key_id",
    "parse_signature_document",
    "verify_detached",
]

ALG = "ed25519"
SIGNATURE_SCHEMA = 1
KEY_ID_HEX = 16
PUBLIC_KEY_BYTES = 32
SIGNATURE_BYTES = 64

STATUS_VERIFIED = "verified"
STATUS_UNSIGNED = "unsigned"
STATUS_INVALID = "invalid"


# --- Ed25519 field and group arithmetic (RFC 8032 §5.1) ---------------------------------------------------

_P = 2**255 - 19
_Q = 2**252 + 27742317777372353535851937790883648493  # the group order L
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)  # a square root of -1
# the base point B, RFC 8032 §5.1
_B = (
    15112221349535400772501151409588531511454012693041857206046113283949847762202,
    46316835694926478169428394003475163141307993866256225615783033603165251855960,
    1,
    (15112221349535400772501151409588531511454012693041857206046113283949847762202 * 46316835694926478169428394003475163141307993866256225615783033603165251855960) % _P,
)
_NEUTRAL = (0, 1, 1, 0)

Point = tuple[int, int, int, int]  # extended coordinates (X, Y, Z, T): x = X/Z, y = Y/Z, x*y = T/Z


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _point_add(a: Point, b: Point) -> Point:
    """The unified addition of RFC 8032 appendix A (also doubles)."""
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    aa = (y1 - x1) * (y2 - x2) % _P
    bb = (y1 + x1) * (y2 + x2) % _P
    cc = 2 * t1 * t2 * _D % _P
    dd = 2 * z1 * z2 % _P
    e, f, g, h = bb - aa, dd - cc, dd + cc, bb + aa
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _point_mul(scalar: int, point: Point) -> Point:
    result = _NEUTRAL
    while scalar > 0:
        if scalar & 1:
            result = _point_add(result, point)
        point = _point_add(point, point)
        scalar >>= 1
    return result


def _point_equal(a: Point, b: Point) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _point_encode(point: Point) -> bytes:
    z_inv = _inv(point[2])
    x = point[0] * z_inv % _P
    y = point[1] * z_inv % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _recover_x(y: int, sign: int) -> int | None:
    """RFC 8032 §5.1.3 steps 2–4: the x with the requested parity for this y, or ``None``."""
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _point_decode(raw: bytes) -> Point | None:
    if len(raw) != 32:
        return None
    y = int.from_bytes(raw, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _sha512_int(*parts: bytes) -> int:
    digest = hashlib.sha512()
    for part in parts:
        digest.update(part)
    return int.from_bytes(digest.digest(), "little")


def ed25519_verify(public_key: bytes, signature: bytes, message: bytes) -> bool:
    """RFC 8032 §5.1.7 with the strict checks: canonical ``S`` (``S < L``), decodable ``A`` and ``R``.

    Never raises on malformed input; every defect is ``False``.
    """
    if len(public_key) != PUBLIC_KEY_BYTES or len(signature) != SIGNATURE_BYTES:
        return False
    a_point = _point_decode(public_key)
    r_point = _point_decode(signature[:32])
    if a_point is None or r_point is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _Q:
        return False
    k = _sha512_int(signature[:32], public_key, message) % _Q
    return _point_equal(_point_mul(s, _B), _point_add(r_point, _point_mul(k, a_point)))


# --- keys and signature documents -----------------------------------------------------------------------------


def key_id(public_key: bytes) -> str:
    """The first 16 hex digits of ``sha256(public key)`` — the id pinned in adapters and written into signatures."""
    if len(public_key) != PUBLIC_KEY_BYTES:
        raise SignatureError(f"an Ed25519 public key is {PUBLIC_KEY_BYTES} bytes, got {len(public_key)}")
    return hashlib.sha256(public_key).hexdigest()[:KEY_ID_HEX]


class SignatureError(ValueError):
    """A signature document (or key) that cannot be read."""


@dataclass(frozen=True)
class SignatureDocument:
    """The parsed ``*.sig`` file."""

    key_id: str
    signature: bytes
    signed_at: str | None
    alg: str = ALG
    canonical: str = CANONICAL_FORM
    schema: int = SIGNATURE_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "alg": self.alg,
            "keyId": self.key_id,
            "signature": base64.b64encode(self.signature).decode("ascii"),
            "signedAt": self.signed_at,
            "canonical": self.canonical,
        }


def parse_signature_document(raw: bytes | str | dict[str, Any]) -> SignatureDocument:
    """Read a signature document; a ``SignatureError`` names the first defect."""
    if isinstance(raw, (bytes, str)):
        try:
            document = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        except (UnicodeDecodeError, ValueError) as exc:
            raise SignatureError(f"signature file is not JSON: {exc}") from exc
    else:
        document = raw
    if not isinstance(document, dict):
        raise SignatureError("signature document is not a JSON object")
    if document.get("schema") != SIGNATURE_SCHEMA:
        raise SignatureError(f"signature schema {document.get('schema')!r} is not {SIGNATURE_SCHEMA}")
    if document.get("alg") != ALG:
        raise SignatureError(f"signature algorithm {document.get('alg')!r} is not {ALG!r}")
    canonical = document.get("canonical", CANONICAL_FORM)
    if canonical != CANONICAL_FORM:
        raise SignatureError(f"canonical form {canonical!r} is not {CANONICAL_FORM!r}")
    kid = document.get("keyId")
    if not isinstance(kid, str) or len(kid) != KEY_ID_HEX or any(c not in "0123456789abcdef" for c in kid):
        raise SignatureError(f"keyId {kid!r} is not {KEY_ID_HEX} lower-case hex digits")
    encoded = document.get("signature")
    if not isinstance(encoded, str):
        raise SignatureError("signature is missing")
    try:
        signature = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SignatureError(f"signature is not base64: {exc}") from exc
    if len(signature) != SIGNATURE_BYTES:
        raise SignatureError(f"an Ed25519 signature is {SIGNATURE_BYTES} bytes, got {len(signature)}")
    signed_at = document.get("signedAt")
    return SignatureDocument(kid, signature, signed_at if isinstance(signed_at, str) else None)


@dataclass
class VerifyResult:
    """The verdict of :func:`verify_detached` — the shape :mod:`floofy_core.registry_sources` records in ``meta.json``."""

    status: str
    detail: str = ""
    key_id: str | None = None

    @property
    def verified(self) -> bool:
        return self.status == STATUS_VERIFIED

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "detail": self.detail, "keyId": self.key_id}


def verify_detached(payload: bytes | Any, signature: bytes | str | dict[str, Any] | None, allowed_keys: dict[str, bytes], *, expected_key_id: str | None = None) -> VerifyResult:
    """Verify a detached signature over ``payload`` (serialized JSON bytes, or an already parsed document).

    ``allowed_keys`` maps key id → 32-byte public key: the edition's pinned keys
    plus whatever the user pinned for the source. ``expected_key_id`` (the
    source's ``--key-id``) narrows that to one key: a signature by any other key
    is ``invalid`` even when that key is otherwise allowed. ``signature is None``
    is ``unsigned``. Nothing here raises for hostile input.
    """
    if signature is None:
        return VerifyResult(STATUS_UNSIGNED, "no detached signature published by the source")
    try:
        document = parse_signature_document(signature)
    except SignatureError as exc:
        return VerifyResult(STATUS_INVALID, f"unreadable signature: {exc}")
    if expected_key_id and document.key_id != expected_key_id:
        return VerifyResult(STATUS_INVALID, f"signed by key {document.key_id}, but this source is pinned to key {expected_key_id}", document.key_id)
    public_key = allowed_keys.get(document.key_id)
    if public_key is None:
        known = ", ".join(sorted(allowed_keys)) or "none"
        return VerifyResult(STATUS_INVALID, f"signed by an unknown key {document.key_id} (trusted keys: {known}); pin it with `floofy registry add … --public-key` if you trust it", document.key_id)
    if len(public_key) != PUBLIC_KEY_BYTES or key_id(public_key) != document.key_id:
        return VerifyResult(STATUS_INVALID, f"the pinned key for {document.key_id} does not hash to that id", document.key_id)
    try:
        message = canonical_bytes_of_json(payload) if isinstance(payload, (bytes, str)) else canonical_bytes(payload)
    except CanonicalError as exc:
        return VerifyResult(STATUS_INVALID, f"the signed document is not canonicalisable JSON: {exc}", document.key_id)
    if not ed25519_verify(public_key, document.signature, message):
        return VerifyResult(STATUS_INVALID, f"signature by key {document.key_id} does not match the document (tampered, or signed over different bytes)", document.key_id)
    return VerifyResult(STATUS_VERIFIED, f"Ed25519 signature by key {document.key_id} verified over {CANONICAL_FORM}" + (f", signed {document.signed_at}" if document.signed_at else ""), document.key_id)
