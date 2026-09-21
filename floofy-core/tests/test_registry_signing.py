"""The registry test suite of the core (tasks 7.1–7.6; Requirement 8.1, 8.3, 8.4, 8.5, 9.1, 9.2, 13.3).

Design "Testing Strategy → Registry" and where each item lives (``pytest -m registry``
runs them all; the whole registry set adds well under 30 s to the suite):

| Item | Where |
|---|---|
| sign/verify round trip (fresh keys), RFC 8032 §7.1 vectors, openssl cross-check | here: ``test_rfc8032_vectors_sign_and_verify``, ``test_sign_verify_round_trip_tamper_and_key_pinning``, ``test_openssl_cross_check`` |
| tampered index refused by default, admitted with the audited ``--allow-unsigned`` | ``test_cli_registry_audit.py::test_real_signature_flow_verified_tampered_and_audited_override`` (end to end through the CLI, real keys), the verifier alone here in ``test_verify_index_uses_edition_and_source_pinned_keys`` |
| hash lookup (Requirement 8.5) | here: ``test_hash_lookup_resolves_mod_at_version``; ``test_cli_registry_audit.py::test_which_resolves_hashes_from_cache_and_installed_mods`` |
| federated collision namespacing (Requirement 8.4) | here: ``test_colliding_ids_are_namespaced_by_the_callers_label``; ``test_cli_registry_audit.py::test_colliding_ids_are_namespaced_by_the_configured_source_label`` |
| ``best_version`` selection (Requirement 9.2) | here: ``test_best_version_grades_tested_over_expected_over_unknown_and_excludes_broken``, ``test_matrix_row_outranks_the_inline_cell_and_falls_back_across_channels`` |
| download hash + size checks | here: ``test_download_checks_sha256_and_size_and_discards_the_file`` |
| schemas (index, compat, signature) accept the design examples | here: ``test_index_and_compat_schemas_accept_the_design_examples_and_reject_defects`` |
| ``app-registry.json`` accepted by the host's own reader | ``registry-tools/tests/test_app_registry.py::test_emitted_rows_are_accepted_by_the_hosts_reader`` (the payload copy's interpreter runs ``kiro_crew.apps.registry``) and the operator row's ``PUT`` round trip on a scratch gateway in ``loader-app/tests/test_gateway.py::test_9b_…`` |
| registry-tools: build, sign, validate-submission, compat-merge, bootstrap | ``registry-tools/tests/`` |
| the adapters' pinned keys and default sources | ``editions/*/tests/test_*_registry.py`` |

Pure functions and fixtures only — no network beyond loopback, no live install.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import shutil
import stat
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from floofy_core import registry_sources
from floofy_core.canonical import CANONICAL_FORM, CanonicalError, canonical_bytes, canonical_bytes_of_json
from floofy_core.compat import CompatCache
from floofy_core.installer import InstallError, download
from floofy_core.registry import IndexCache
from floofy_core.schema import SCHEMA_IDS, load_schema
from floofy_core.schema.check import validate_instance
from floofy_core.sigverify import SignatureError, ed25519_verify, key_id, parse_signature_document, verify_detached
from floofy_core.signing import ed25519_sign, generate_keypair, keypair_from_seed, load_private_key, load_public_key_record, main as signing_main, sign_detached, sign_document
from floofy_core.registry_sources import Source, verify_index

pytestmark = pytest.mark.registry

# RFC 8032 §7.1: (secret key, public key, message, signature), all hex.
RFC8032_VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60", "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "", "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb", "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72", "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7", "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82", "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
    (
        "833fe62409237b9d62ec77587520911e9a759cec1d19755b7da901b96dca3d42",
        "ec172b93ad5e563bf4932c70e1245034c35467ef2efd4d64ebf819683467e2bf",
        "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f",
        "dc2a4459e7369633a52b1bf277839a00201009a3efbf3ecb69bea2186c26b58909351fc9ac90b3ecfdfbc7c66431e0303dca179c138ac17ad9bef1177331a704",
    ),
]

INDEX_EXAMPLE = {
    "schema": 1,
    "source": "github:floofycrew/floofycrew-registry",
    "generatedAt": "2026-09-19T08:00:00Z",
    "mods": [
        {
            "id": "rimuru-branding",
            "name": "Rimuru branding",
            "description": "First-frame Rimuru theme, favicon and logo.",
            "authors": ["maintainer"],
            "tags": ["theme", "branding"],
            "repo": "https://github.com/floofycrew/FloofyCrew",
            "license": "MIT",
            "versions": [
                {
                    "version": "1.2.0",
                    "kirocrew": ">=0.7.0 <0.9.0",
                    "editions": ["internal", "external"],
                    "compat": {"0.7.0.5": "tested", "0.7.0": "expected"},
                    "files": [{"url": "https://github.com/floofycrew/FloofyCrew/releases/download/rimuru-branding-1.2.0/rimuru-branding-1.2.0.zip", "sha256": "ab" * 32, "size": 12345}],
                    "dependencies": {"floofycrew": "^1.0"},
                    "channel": "stable",
                    "publishedAt": "2026-09-19T07:00:00Z",
                    "kinds": ["theme", "spa", "patch"],
                }
            ],
        }
    ],
}

COMPAT_EXAMPLE = {
    "schema": 1,
    "rows": [
        {
            "edition": "internal",
            "channel": "beta",
            "hostVersion": "0.7.0.5",
            "framework": {"loader": "ok", "spaFingerprints": {"matched": 41, "total": 42, "missed": ["topbar-root"]}, "pythonAnchors": {"matched": 12, "total": 12}, "shimVersion": "1.0.3"},
            "mods": {"rimuru-branding@1.2.0": {"verdict": "tested", "run": "https://example.com/forge/run/1"}, "other@0.1.0": "expected"},
            "overrides": [{"cell": "other@0.1.0", "verdict": "broken", "reason": "boot script races the host theme on 0.7.0.5", "by": "maintainer"}],
        }
    ],
}


def _unhex(text: str) -> bytes:
    return binascii.unhexlify(text)


# --- the curve (RFC 8032) ------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed, public, message, signature", RFC8032_VECTORS, ids=["empty", "1-byte", "2-bytes", "sha-abc"])
def test_rfc8032_vectors_sign_and_verify(seed: str, public: str, message: str, signature: str):
    seed_b, public_b, message_b, signature_b = (_unhex(x) for x in (seed, public, message, signature))
    assert keypair_from_seed(seed_b).public_key == public_b
    assert ed25519_sign(seed_b, message_b) == signature_b, "signing is deterministic and matches the RFC"
    assert ed25519_verify(public_b, signature_b, message_b)
    assert not ed25519_verify(public_b, signature_b, message_b + b"\x00")
    flipped = bytearray(signature_b)
    flipped[5] ^= 0x01
    assert not ed25519_verify(public_b, bytes(flipped), message_b)


def test_verify_rejects_malformed_and_non_canonical_inputs():
    seed, public, _message, signature = (_unhex(x) for x in RFC8032_VECTORS[1])
    message = b"\x72"
    q = 2**252 + 27742317777372353535851937790883648493
    s = int.from_bytes(signature[32:], "little")
    non_canonical = signature[:32] + (s + q).to_bytes(32, "little")
    assert not ed25519_verify(public, non_canonical, message), "S >= L is refused (malleability)"
    assert not ed25519_verify(public[:-1], signature, message) and not ed25519_verify(public, signature[:-1], message)
    assert not ed25519_verify(b"\xff" * 32, signature, message), "a non-point public key is refused"
    assert not ed25519_verify(public, b"\xff" * 32 + signature[32:], message), "a non-point R is refused"
    other = generate_keypair()
    assert not ed25519_verify(other.public_key, signature, message)
    with pytest.raises(SignatureError):
        ed25519_sign(seed[:-1], message)


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not installed")
def test_openssl_cross_check(tmp_path: Path):
    pem = tmp_path / "k.pem"
    generated = subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(pem)], capture_output=True)
    if generated.returncode != 0:
        pytest.skip("this openssl has no Ed25519")
    seed = subprocess.run(["openssl", "pkey", "-in", str(pem), "-outform", "DER"], check=True, capture_output=True).stdout[-32:]
    public = subprocess.run(["openssl", "pkey", "-in", str(pem), "-pubout", "-outform", "DER"], check=True, capture_output=True).stdout[-32:]
    assert keypair_from_seed(seed).public_key == public
    message = tmp_path / "m.bin"
    message.write_bytes(b"floofy " * 100)
    theirs = subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(pem), "-rawin", "-in", str(message)], check=True, capture_output=True).stdout
    assert ed25519_verify(public, theirs, message.read_bytes()) and ed25519_sign(seed, message.read_bytes()) == theirs
    ours = tmp_path / "s.bin"
    ours.write_bytes(ed25519_sign(seed, b"other"))
    (tmp_path / "m2.bin").write_bytes(b"other")
    public_pem = subprocess.run(["openssl", "pkey", "-in", str(pem), "-pubout"], check=True, capture_output=True).stdout
    (tmp_path / "pub.pem").write_bytes(public_pem)
    verified = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(tmp_path / "pub.pem"), "-rawin", "-in", str(tmp_path / "m2.bin"), "-sigfile", str(ours)], capture_output=True)
    assert verified.returncode == 0, verified.stderr


# --- canonical JSON -----------------------------------------------------------------------------------------


def test_canonical_bytes_are_order_and_whitespace_independent():
    a = canonical_bytes({"b": [1, 2, {"z": None, "a": "ü"}], "a": True})
    b = canonical_bytes_of_json(b'{\n  "a": true,\n  "b": [1, 2, {"a": "\\u00fc", "z": null}]\n}')
    assert a == b == '{"a":true,"b":[1,2,{"a":"ü","z":null}]}'.encode("utf-8")
    assert CANONICAL_FORM == "json-c14n-1"
    with pytest.raises(CanonicalError):
        canonical_bytes({"x": float("nan")})
    with pytest.raises(CanonicalError):
        canonical_bytes({1: "non-string key"})
    with pytest.raises(CanonicalError):
        canonical_bytes({"x": object()})
    with pytest.raises(CanonicalError):
        canonical_bytes_of_json(b"not json")


# --- detached signatures ------------------------------------------------------------------------------------


def test_sign_verify_round_trip_tamper_and_key_pinning():
    pair = generate_keypair("test key")
    other = generate_keypair("another key")
    assert len(pair.key_id) == 16 and pair.key_id == key_id(pair.public_key)
    document = sign_detached(INDEX_EXAMPLE, pair)
    raw = json.dumps(document.to_dict()).encode("utf-8")
    assert validate_instance(document.to_dict(), load_schema("signature")) == []
    keys = {pair.key_id: pair.public_key}
    pretty = json.dumps(INDEX_EXAMPLE, indent=2).encode("utf-8")
    compact = canonical_bytes(INDEX_EXAMPLE)
    assert verify_detached(pretty, raw, keys).verified and verify_detached(compact, raw, keys).verified, "whitespace and key order never matter"
    assert verify_detached(INDEX_EXAMPLE, document.to_dict(), keys).key_id == pair.key_id
    tampered = json.loads(pretty)
    tampered["mods"][0]["versions"][0]["files"][0]["sha256"] = "cd" * 32
    verdict = verify_detached(json.dumps(tampered).encode("utf-8"), raw, keys)
    assert verdict.status == "invalid" and "does not match" in verdict.detail
    assert verify_detached(pretty, None, keys).status == "unsigned"
    assert verify_detached(pretty, raw, {}).status == "invalid" and "unknown key" in verify_detached(pretty, raw, {}).detail
    assert verify_detached(pretty, raw, {other.key_id: other.public_key}).status == "invalid"
    pinned_elsewhere = verify_detached(pretty, raw, {**keys, other.key_id: other.public_key}, expected_key_id=other.key_id)
    assert pinned_elsewhere.status == "invalid" and "pinned to key" in pinned_elsewhere.detail
    assert verify_detached(pretty, raw, keys, expected_key_id=pair.key_id).verified
    wrong_bytes = verify_detached(pretty, raw, {pair.key_id: other.public_key})
    assert wrong_bytes.status == "invalid" and "does not hash" in wrong_bytes.detail
    assert verify_detached(b"{not json", raw, keys).status == "invalid"
    # unreadable documents
    for broken in (b"nope", json.dumps({**document.to_dict(), "alg": "rsa"}), json.dumps({**document.to_dict(), "keyId": "XYZ"}), json.dumps({**document.to_dict(), "signature": "AAAA"}), json.dumps({**document.to_dict(), "canonical": "json-c14n-9"}), json.dumps([1])):
        assert verify_detached(pretty, broken, keys).status == "invalid", broken
        with pytest.raises(SignatureError):
            parse_signature_document(broken)


def test_key_files_round_trip_and_cli(tmp_path: Path, capsys):
    private = tmp_path / "keys" / "reg.ed25519.json"
    assert signing_main(["keygen", "--out", str(private), "--comment", "unit test"]) == 0
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    pair = load_private_key(private)
    public_record = json.loads(private.with_name(private.name + ".pub.json").read_text(encoding="utf-8"))
    assert "privateKey" not in public_record and public_record["keyId"] == pair.key_id and public_record["comment"] == "unit test"
    assert load_public_key_record(public_record) == (pair.key_id, pair.public_key)
    assert signing_main(["keygen", "--out", str(private)]) == 1, "never overwrite a key by accident"
    index = tmp_path / "index.json"
    index.write_text(json.dumps(INDEX_EXAMPLE, indent=2), encoding="utf-8")
    assert signing_main(["sign", str(index), "--key", str(private)]) == 0
    signature = index.with_name("index.json.sig")
    assert verify_detached(index.read_bytes(), signature.read_bytes(), {pair.key_id: pair.public_key}).verified
    assert signing_main(["key-id", str(private.with_name(private.name + ".pub.json"))]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == pair.key_id
    # a corrupted key file is refused
    corrupt = json.loads(private.read_text(encoding="utf-8"))
    corrupt["publicKey"] = base64.b64encode(generate_keypair().public_key).decode("ascii")
    (tmp_path / "corrupt.json").write_text(json.dumps(corrupt), encoding="utf-8")
    with pytest.raises(SignatureError, match="does not belong"):
        load_private_key(tmp_path / "corrupt.json")
    assert signing_main(["sign", str(index), "--key", str(tmp_path / "corrupt.json")]) == 1
    assert sign_document(index, pair, out=tmp_path / "elsewhere.sig").name == "elsewhere.sig"


# --- the verifier hook in registry_sources -------------------------------------------------------------------


def test_verify_index_uses_edition_and_source_pinned_keys(monkeypatch: pytest.MonkeyPatch):
    edition_key = generate_keypair("edition")
    source_key = generate_keypair("third party")
    monkeypatch.setattr(registry_sources, "KEY_PROVIDER", lambda: {edition_key.key_id: edition_key.public_key})
    index_bytes = json.dumps(INDEX_EXAMPLE).encode("utf-8")
    by_edition = json.dumps(sign_detached(INDEX_EXAMPLE, edition_key).to_dict()).encode("utf-8")
    by_third_party = json.dumps(sign_detached(INDEX_EXAMPLE, source_key).to_dict()).encode("utf-8")
    plain = Source("https://registry.example/")
    assert verify_index(index_bytes, by_edition, plain).verified
    assert verify_index(index_bytes, None, plain).status == "unsigned"
    assert verify_index(index_bytes, by_third_party, plain).status == "invalid", "an unknown key is not trusted by default"
    pinned = Source("https://registry.example/", public_key=base64.b64encode(source_key.public_key).decode("ascii"))
    assert verify_index(index_bytes, by_third_party, pinned).verified, "the user pinned the third party's key for this source"
    assert verify_index(index_bytes, by_edition, pinned).verified, "the edition's keys still apply"
    restricted = Source("https://registry.example/", key_id=source_key.key_id, public_key=pinned.public_key)
    assert verify_index(index_bytes, by_edition, restricted).status == "invalid", "--key-id restricts the source to one key"
    assert verify_index(index_bytes, by_third_party, restricted).verified
    tampered = json.dumps({**INDEX_EXAMPLE, "generatedAt": "2027-01-01T00:00:00Z"}).encode("utf-8")
    assert verify_index(tampered, by_edition, plain).status == "invalid"
    # a malformed pinned key never crashes verification
    assert verify_index(index_bytes, by_edition, Source("https://registry.example/", public_key="not base64!")).verified, "an unreadable pin is ignored; the edition keys still decide"


def test_source_store_pins_and_derives_the_key_id(tmp_path: Path):
    from floofy_core.datahome import DataHome
    from floofy_core.registry_sources import SourceStore

    pair = generate_keypair()
    encoded = base64.b64encode(pair.public_key).decode("ascii")
    store = SourceStore.load(DataHome.for_host_home(tmp_path).ensure())
    source, _ = store.add(Source("https://registry.example/", public_key=encoded))
    assert source.key_id == pair.key_id and source.public_key_bytes == pair.public_key
    store.save()
    reloaded = SourceStore.load(DataHome.for_host_home(tmp_path))
    assert reloaded.sources[0].public_key == encoded and reloaded.sources[0].to_record()["publicKey"] == encoded
    with pytest.raises(Exception, match="does not match"):
        store.add(Source("https://other.example/", key_id="0" * 16, public_key=encoded))
    with pytest.raises(Exception, match="base64"):
        store.add(Source("https://other.example/", public_key="***"))
    hexed, _ = store.add(Source("https://hex.example/", public_key=pair.public_key.hex()))
    assert hexed.key_id == pair.key_id
    templated = Source("https://code.example/pkg/blobs/main/--/{file}?raw=1")
    assert templated.file_url("index.json") == "https://code.example/pkg/blobs/main/--/index.json?raw=1" and templated.base == "https://code.example/pkg/blobs/main/--/"
    assert Source("https://r.example/reg").file_url("compat.json.sig") == "https://r.example/reg/compat.json.sig"


# --- schemas (Requirement 8.1, 9.1) ---------------------------------------------------------------------------


def test_index_and_compat_schemas_accept_the_design_examples_and_reject_defects():
    index_schema, compat_schema = load_schema("index"), load_schema("compat")
    assert index_schema["$id"] == SCHEMA_IDS["index"] and compat_schema["$id"] == SCHEMA_IDS["compat"] and load_schema("signature")["$id"] == SCHEMA_IDS["signature"]
    assert validate_instance(INDEX_EXAMPLE, index_schema) == []
    assert validate_instance(COMPAT_EXAMPLE, compat_schema) == []
    version = INDEX_EXAMPLE["mods"][0]["versions"][0]
    assert set(version) >= {"version", "kirocrew", "editions", "compat", "files", "dependencies", "channel", "publishedAt"}, "Requirement 8.1's field list"
    assert set(INDEX_EXAMPLE["mods"][0]) >= {"id", "name", "description", "authors", "tags", "repo", "versions"}
    bad_index = json.loads(json.dumps(INDEX_EXAMPLE))
    bad_index["mods"][0]["versions"][0]["compat"]["0.7.0.5"] = "maybe"
    bad_index["mods"][0]["versions"][0]["files"][0]["url"] = "http://plaintext.example/x.zip"
    bad_index["mods"][0]["versions"][0]["files"][0]["sha256"] = "XYZ"
    del bad_index["mods"][0]["repo"]
    bad_index["signature"] = {"x": 1}
    messages = [str(e) for e in validate_instance(bad_index, index_schema)]
    assert any("compat/0.7.0.5" in m for m in messages) and any("files/0/url" in m for m in messages) and any("files/0/sha256" in m for m in messages)
    assert any("'repo'" in m for m in messages) and any("'signature'" in m for m in messages), "the index never embeds a signature"
    bad_compat = json.loads(json.dumps(COMPAT_EXAMPLE))
    bad_compat["rows"][0]["framework"]["loader"] = "fine"
    bad_compat["rows"][0]["mods"]["not a cell"] = "tested"
    bad_compat["rows"][0]["overrides"][0].pop("reason")
    messages = [str(e) for e in validate_instance(bad_compat, compat_schema)]
    assert any("framework/loader" in m for m in messages) and any("not a cell" in m for m in messages) and any("'reason'" in m for m in messages)
    assert validate_instance({"schema": 1, "rows": []}, compat_schema) == []
    assert validate_instance({"schema": 1, "source": "x", "generatedAt": "2026-01-01T00:00:00Z", "mods": []}, index_schema) == []



@pytest.mark.parametrize(
    ("host_version", "accepted"),
    [
        ("0.6.0", True),  # public stable
        ("0.7.0.5", True),  # internal build number
        ("0.7.0rc5", True),  # public insider: a PEP 440 pre-release, no separator
        ("0.8.0.dev20260920060902", True),  # public nightly
        ("0.7.0-insider.3", True),  # the design's hyphenated pre-release form
        ("0.7.0a1", True),
        ("0.7", False),
        ("v0.7.0", False),
        ("0.7.0 rc5", False),
        ("latest", False),
    ],
)
def test_both_schemas_accept_every_host_version_shape_the_host_prints(host_version: str, accepted: bool):
    """Requirement 8.1 and 9.1: an index compat cell and a matrix row must accept the same host
    version strings — the Forge's public insider row (``0.7.0rc5``) was refused by the index
    schema while ``compat.schema.json`` took it, so ``registry_tools build`` could not fold the
    measured cell into ``index.json``."""
    index_schema, compat_schema = load_schema("index"), load_schema("compat")
    assert index_schema["$defs"]["hostVersion"]["pattern"] == compat_schema["$defs"]["hostVersion"]["pattern"]
    index = json.loads(json.dumps(INDEX_EXAMPLE))
    index["mods"][0]["versions"][0]["compat"] = {host_version: "tested"}
    row = json.loads(json.dumps(COMPAT_EXAMPLE))
    row["rows"][0]["hostVersion"] = host_version
    index_ok = validate_instance(index, index_schema) == []
    compat_ok = validate_instance(row, compat_schema) == []
    assert index_ok is accepted and compat_ok is accepted, (host_version, index_ok, compat_ok)



# --- the client: grading, hash lookup, namespacing, downloads (Requirement 8.4, 8.5, 9.2) ------------------------


def _index(versions: list[dict]) -> IndexCache:
    return IndexCache.from_documents([("src", {"schema": 1, "source": "src", "mods": [{"id": "m", "name": "M", "versions": [{"kirocrew": ">=0.7.0 <0.9.0", "compat": {}, "files": [{"url": "https://x.example/m.zip", "sha256": "0" * 64, "size": 1}], **v} for v in versions]}]})])


def _compat(rows: list[dict]) -> CompatCache:
    return CompatCache.from_dict({"schema": 1, "rows": [{"framework": {"loader": "ok"}, **r} for r in rows]})


HOST = {"base_version": "0.7.0", "edition": "internal", "host_version": "0.7.0.5", "channel": "beta"}


def test_best_version_grades_tested_over_expected_over_unknown_and_excludes_broken():
    cache = _index([
        {"version": "0.9.0", "compat": {"0.7.0.5": "tested"}},
        {"version": "1.0.0", "compat": {"0.7.0.5": "expected"}},
        {"version": "1.1.0"},
        {"version": "2.0.0", "compat": {"0.7.0.5": "broken"}},
        {"version": "3.0.0", "kirocrew": ">=0.9.0"},
        {"version": "3.1.0", "editions": ["external"]},
        {"version": "4.0.0", "compat": {"0.7.0.5": "tested"}, "yanked": "pulled: data loss"},
        {"version": "not-semver"},
    ])
    best = cache.best_version("m", **HOST)
    assert (best.version.version, best.verdict) == ("0.9.0", "tested"), "tested beats newer expected and unknown; broken, out-of-range, other-edition, yanked never"
    assert cache.pick("m", **HOST)[1].version == "0.9.0" and cache.pick("m", requested="1.1.0", **HOST)[1].version == "1.1.0"
    plain = _index([{"version": "1.0.0"}, {"version": "1.2.0"}, {"version": "1.1.0"}])
    assert plain.best_version("m", **HOST).version.version == "1.2.0" and plain.best_version("m", **HOST).verdict is None, "without any verdict the newest in range wins"
    none = _index([{"version": "2.0.0", "compat": {"0.7.0.5": "broken"}}, {"version": "3.0.0", "kirocrew": ">=0.9.0"}])
    answer = none.best_version("m", **HOST)
    assert answer.version is None and "2.0.0 (broken on 0.7.0.5)" in answer.why and "3.0.0 (declares kirocrew >=0.9.0)" in answer.why, "every exclusion is named"
    assert cache.best_version("nope", **HOST).why == "'nope' is not in the registry cache"
    assert answer.to_dict() == {"key": "m", "version": None, "verdict": None, "why": answer.why}


def test_matrix_row_outranks_the_inline_cell_and_falls_back_across_channels():
    cache = _index([{"version": "1.0.0", "compat": {"0.7.0.5": "tested"}}, {"version": "1.1.0", "compat": {"0.7.0.5": "expected"}}])
    compat = _compat([{"edition": "internal", "channel": "beta", "hostVersion": "0.7.0.5", "mods": {"m@1.1.0": "tested", "m@1.0.0": "broken"}}])
    best = cache.best_version("m", compat=compat, **HOST)
    assert (best.version.version, best.verdict) == ("1.1.0", "tested"), "the matrix row wins over the index's inline cells"
    all_broken = _compat([{"edition": "internal", "channel": "beta", "hostVersion": "0.7.0.5", "mods": {"m@1.0.0": "broken", "m@1.1.0": "broken"}}])
    assert "1.0.0 (broken on 0.7.0.5)" in cache.best_version("m", compat=all_broken, **HOST).why
    stable_only = _compat([{"edition": "internal", "channel": "stable", "hostVersion": "0.7.0.5", "mods": {"m@1.1.0": "tested"}}])
    assert cache.best_version("m", compat=stable_only, **HOST).version.version == "1.1.0", "a build whose channel has no row takes the same edition's row for that version"
    assert cache.best_version("m", compat=stable_only, **{**HOST, "channel": None}).version.version == "1.1.0"
    other = _compat([{"edition": "external", "channel": "stable", "hostVersion": "0.7.0", "mods": {"m@1.1.0": "tested"}}])
    assert cache.best_version("m", compat=other, **HOST).version.version == "1.0.0", "another edition's row does not apply"
    partial = _compat([{"edition": "internal", "channel": "beta", "hostVersion": "0.7.0.5", "mods": {"m@1.0.0": "expected"}}])
    assert cache.best_version("m", compat=partial, **HOST).version.version == "1.1.0", "a cell for one version only leaves the others to their inline verdicts"
    overridden = CompatCache.from_dict({"schema": 1, "rows": [{"edition": "internal", "channel": "beta", "hostVersion": "0.7.0.5", "framework": {"loader": "ok"}, "mods": {"m@1.1.0": "tested"}, "overrides": [{"cell": "m@1.1.0", "verdict": "broken", "reason": "r", "by": "b"}]}]})
    assert cache.best_version("m", compat=overridden, **HOST).version.version == "1.0.0", "a human override is applied by the reader"


def test_hash_lookup_resolves_mod_at_version():
    """Requirement 8.5: any file's sha256 → mod@version (and the file) from the index."""
    cache = IndexCache.from_documents([("src", {"schema": 1, "source": "src", "mods": [
        {"id": "a", "name": "A", "versions": [{"version": "1.0.0", "files": [{"url": "https://x/a-1.zip", "sha256": "aa" * 32, "size": 1}]}, {"version": "1.1.0", "files": [{"url": "https://x/a-1.1.zip", "sha256": "ab" * 32, "size": 1}, {"url": "https://x/a-1.1.tar.gz", "sha256": "ac" * 32, "size": 1}]}]},
        {"id": "b", "name": "B", "versions": [{"version": "2.0.0", "files": [{"url": "https://x/b.zip", "sha256": "AB" * 32, "size": 1}]}]},
    ]})])
    hits = cache.by_hash("ab" * 32)
    assert [(e.key, v.version, f["url"]) for e, v, f in hits] == [("a", "1.1.0", "https://x/a-1.1.zip"), ("b", "2.0.0", "https://x/b.zip")], "case-insensitive; every match reported"
    assert [(e.key, v.version) for e, v, _ in cache.by_hash("AC" * 32)] == [("a", "1.1.0")]
    assert cache.by_hash("00" * 32) == [] and cache.by_hash("") == []


def test_colliding_ids_are_namespaced_by_the_callers_label():
    """Requirement 8.4: the same id from two sources gets ``<label>/<id>`` keys under the labels the caller (the user's config) chose."""
    alpha = {"schema": 1, "source": "claims-to-be-official", "mods": [{"id": "reggy", "name": "R", "versions": [{"version": "1.0.0"}]}, {"id": "solo", "name": "S", "versions": []}]}
    beta = {"schema": 1, "source": "claims-to-be-official", "mods": [{"id": "reggy", "name": "R", "versions": [{"version": "2.0.0"}]}]}
    cache = IndexCache.from_documents([("alpha", alpha), ("beta", beta)], prefer_label=True)
    assert sorted(m.key for m in cache.mods) == ["alpha/reggy", "beta/reggy", "solo"]
    assert cache.find("reggy") is None, "ambiguous bare id resolves to nothing"
    assert cache.find("alpha/reggy").versions[0].version == "1.0.0" and cache.find("beta/reggy").versions[0].version == "2.0.0" and cache.find("solo").key == "solo"
    assert {m.source for m in cache.mods} == {"alpha", "beta"}, "the index's own `source` claim never becomes the namespace"
    assert any("namespaced as alpha/reggy and beta/reggy" in n for n in cache.notes)
    claimed = IndexCache.from_documents([("alpha", alpha), ("beta", beta)])
    assert sorted(m.key for m in claimed.mods) == ["reggy", "solo"], "without prefer_label both documents claim one source, so the second `reggy` is dropped as a duplicate listing — which is why the merged cache namespaces by the user's labels instead"


class _Files(BaseHTTPRequestHandler):
    payload = b""

    def log_message(self, *_args) -> None:
        return None

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)


def test_download_checks_sha256_and_size_and_discards_the_file(tmp_path: Path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("m/floofy.json", "{}")
    _Files.payload = buffer.getvalue()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Files)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/m.zip"
        digest = hashlib.sha256(_Files.payload).hexdigest()
        target = tmp_path / "m.zip"
        assert download(url, target, expected_sha256=digest, expected_size=len(_Files.payload)) == digest and target.is_file()
        with pytest.raises(InstallError, match="sha256 mismatch"):
            download(url, tmp_path / "bad.zip", expected_sha256="ab" * 32)
        assert not (tmp_path / "bad.zip").exists(), "a mismatching download is discarded"
        with pytest.raises(InstallError, match="the index says"):
            download(url, tmp_path / "small.zip", expected_size=len(_Files.payload) - 1)
        assert not (tmp_path / "small.zip").exists()
        with pytest.raises(InstallError, match="size mismatch"):
            download(url, tmp_path / "big.zip", expected_size=len(_Files.payload) + 10)
        assert not (tmp_path / "big.zip").exists()
        with pytest.raises(InstallError, match="https"):
            download("http://registry.example/m.zip", tmp_path / "plain.zip")
    finally:
        server.shutdown()
        server.server_close()
