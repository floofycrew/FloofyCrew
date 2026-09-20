"""Ed25519 key generation and detached signing — the maintainer's and the Forge's side (Requirement 8.3).

Pure Python (RFC 8032 §5.1.5–5.1.6) on top of the curve arithmetic in
:mod:`floofy_core.sigverify`, so ``registry-tools`` and the Forge sign with the
same code the manager verifies with and need no third-party package.

Key files are JSON. A **private key file** (mode ``0600``)::

    {"schema": 1, "alg": "ed25519", "keyId": "<16 hex>", "publicKey": "<base64, 32 bytes>",
     "privateKey": "<base64, 32-byte seed>", "createdAt": "…", "comment": "…"}

and the **public key record** pinned in an edition adapter is the same document
without ``privateKey``. The key id is ``sha256(public key)[:16]``
(:func:`floofy_core.sigverify.key_id`).

``python -m floofy_core.signing keygen --out KEY [--comment TEXT]`` and
``sign PAYLOAD --key KEY [--out SIG]`` are the minimal commands; ``registry-tools``
wraps them with the registry-repo workflow.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .canonical import CANONICAL_FORM, canonical_bytes, canonical_bytes_of_json
from .sigverify import _B, _Q, _point_encode, _point_mul, _sha512_int, key_id, SignatureDocument, SignatureError

__all__ = ["KeyPair", "generate_keypair", "load_private_key", "load_public_key_record", "main", "sign_detached", "sign_document"]

SEED_BYTES = 32


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: Any, what: str, length: int) -> bytes:
    if not isinstance(text, str):
        raise SignatureError(f"{what} is missing")
    try:
        raw = base64.b64decode(text, validate=True)
    except ValueError as exc:
        raise SignatureError(f"{what} is not base64: {exc}") from exc
    if len(raw) != length:
        raise SignatureError(f"{what} must be {length} bytes, got {len(raw)}")
    return raw


def _expand(seed: bytes) -> tuple[int, bytes]:
    """RFC 8032 §5.1.5: the clamped scalar and the prefix derived from the seed."""
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, digest[32:]


def _public_key(seed: bytes) -> bytes:
    scalar, _prefix = _expand(seed)
    return _point_encode(_point_mul(scalar, _B))


def ed25519_sign(seed: bytes, message: bytes) -> bytes:
    """RFC 8032 §5.1.6 — a 64-byte signature over ``message`` by the key with this 32-byte seed."""
    if len(seed) != SEED_BYTES:
        raise SignatureError(f"an Ed25519 seed is {SEED_BYTES} bytes, got {len(seed)}")
    scalar, prefix = _expand(seed)
    public = _point_encode(_point_mul(scalar, _B))
    r = _sha512_int(prefix, message) % _Q
    r_encoded = _point_encode(_point_mul(r, _B))
    k = _sha512_int(r_encoded, public, message) % _Q
    s = (r + k * scalar) % _Q
    return r_encoded + s.to_bytes(32, "little")


@dataclass(frozen=True)
class KeyPair:
    seed: bytes
    public_key: bytes
    created_at: str
    comment: str = ""

    @property
    def key_id(self) -> str:
        return key_id(self.public_key)

    def public_record(self) -> dict[str, Any]:
        """What an edition adapter pins (no secret material)."""
        return {"schema": 1, "alg": "ed25519", "keyId": self.key_id, "publicKey": _b64(self.public_key), "createdAt": self.created_at, "comment": self.comment}

    def private_record(self) -> dict[str, Any]:
        return {**self.public_record(), "privateKey": _b64(self.seed)}

    def save_private(self, path: Path) -> Path:
        """Write the private key file with mode 0600 (never into a repository — see ``.gitignore``/``KEYS.md``)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(self.private_record(), handle, indent=2)
            handle.write("\n")
        os.chmod(path, 0o600)
        return path

    def save_public(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.public_record(), indent=2) + "\n", encoding="utf-8")
        return path


def generate_keypair(comment: str = "") -> KeyPair:
    seed = secrets.token_bytes(SEED_BYTES)
    return KeyPair(seed, _public_key(seed), _now(), comment)


def keypair_from_seed(seed: bytes, *, comment: str = "", created_at: str | None = None) -> KeyPair:
    return KeyPair(bytes(seed), _public_key(bytes(seed)), created_at or _now(), comment)


def load_private_key(path: Path) -> KeyPair:
    """Read a private key file; the public key is re-derived and checked against the file."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SignatureError(f"{path}: {exc}") from exc
    if not isinstance(document, dict) or document.get("alg") != "ed25519":
        raise SignatureError(f"{path}: not an ed25519 key file")
    seed = _unb64(document.get("privateKey"), "privateKey", SEED_BYTES)
    pair = keypair_from_seed(seed, comment=str(document.get("comment") or ""), created_at=document.get("createdAt") if isinstance(document.get("createdAt"), str) else None)
    if "publicKey" in document and _unb64(document["publicKey"], "publicKey", 32) != pair.public_key:
        raise SignatureError(f"{path}: publicKey does not belong to privateKey")
    if "keyId" in document and document["keyId"] != pair.key_id:
        raise SignatureError(f"{path}: keyId {document['keyId']!r} does not match the public key ({pair.key_id})")
    return pair


def load_public_key_record(record: dict[str, Any] | str | Path) -> tuple[str, bytes]:
    """``(key_id, public_key)`` from a pinned public key record (a dict, or a JSON file path); the id is re-derived."""
    if not isinstance(record, dict):
        try:
            record = json.loads(Path(record).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SignatureError(f"{record}: {exc}") from exc
        if not isinstance(record, dict):
            raise SignatureError("public key record is not a JSON object")
    if record.get("alg", "ed25519") != "ed25519":
        raise SignatureError(f"unsupported key algorithm {record.get('alg')!r}")
    public = _unb64(record.get("publicKey"), "publicKey", 32)
    kid = key_id(public)
    if "keyId" in record and record["keyId"] != kid:
        raise SignatureError(f"keyId {record['keyId']!r} does not match the public key ({kid})")
    return kid, public


def sign_detached(payload: bytes | Any, pair: KeyPair, *, signed_at: str | None = None) -> SignatureDocument:
    """Sign the canonical bytes of ``payload`` (serialized JSON bytes/str, or a parsed document)."""
    message = canonical_bytes_of_json(payload) if isinstance(payload, (bytes, str)) else canonical_bytes(payload)
    return SignatureDocument(pair.key_id, ed25519_sign(pair.seed, message), signed_at or _now(), canonical=CANONICAL_FORM)


def sign_document(payload_path: Path, pair: KeyPair, *, out: Path | None = None) -> Path:
    """Sign the JSON file at ``payload_path`` and write ``<payload>.sig`` (or ``out``); returns the signature path."""
    payload_path = Path(payload_path)
    document = sign_detached(payload_path.read_bytes(), pair)
    target = Path(out) if out else payload_path.with_name(payload_path.name + ".sig")
    target.write_text(json.dumps(document.to_dict(), indent=2) + "\n", encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m floofy_core.signing", description="Ed25519 keys and detached signatures for FloofyCrew registry documents")
    sub = parser.add_subparsers(dest="command", required=True)
    keygen = sub.add_parser("keygen", help="generate a key pair: <out> (private, mode 0600) and <out>.pub.json (public record)")
    keygen.add_argument("--out", required=True, help="private key file to write")
    keygen.add_argument("--comment", default="", help="free text stored with both records")
    keygen.add_argument("--force", action="store_true", help="overwrite an existing private key file")
    sign = sub.add_parser("sign", help="write the detached signature of a JSON document")
    sign.add_argument("payload", help="the JSON file to sign (index.json, compat.json)")
    sign.add_argument("--key", required=True, help="private key file")
    sign.add_argument("--out", default=None, help="signature file (default <payload>.sig)")
    show = sub.add_parser("key-id", help="print the key id of a key file (private or public record)")
    show.add_argument("key")
    args = parser.parse_args(argv)
    try:
        if args.command == "keygen":
            out = Path(args.out)
            if out.exists() and not args.force:
                print(f"error: {out} exists (use --force to overwrite)", file=sys.stderr)
                return 1
            pair = generate_keypair(args.comment)
            pair.save_private(out)
            public_path = pair.save_public(out.with_name(out.name + ".pub.json"))
            print(f"key id {pair.key_id}\nprivate key: {out} (mode 0600 — keep it out of every repository)\npublic record: {public_path}")
            return 0
        if args.command == "sign":
            pair = load_private_key(Path(args.key))
            target = sign_document(Path(args.payload), pair, out=Path(args.out) if args.out else None)
            print(f"signed {args.payload} with key {pair.key_id} -> {target}")
            return 0
        if args.command == "key-id":
            document = json.loads(Path(args.key).read_text(encoding="utf-8"))
            kid, _public = load_public_key_record(document)
            print(kid)
            return 0
    except (SignatureError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
