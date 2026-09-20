"""Audit log and registry trust controls (task 6.8; Requirement 8.3, 8.4, 8.5, 11.3, 11.5, 11.6).

A loopback HTTP server stands in for one or two registries (plaintext to loopback
is the one allowed exception); nothing here leaves the machine. Homes are scratch
directories: ``--home`` for the host home and ``KIRO_HOME`` for the agent seam.
"""
from __future__ import annotations

import hashlib
import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from floofy_core import registry_sources
from floofy_core.audit import AuditLog, read_audit
from floofy_core.cli.main import run
from floofy_core.consent import consent_reference, write_consent
from floofy_core.datahome import DataHome
from floofy_core.registry import IndexCache
from floofy_core.registry_sources import Source, SourceStore, VerifyResult, source_key

from floofy_testing import fake_payload
from test_cli_mods import write_mod


class _Registry(BaseHTTPRequestHandler):
    files: dict[str, bytes] = {}
    hits: list[str] = []

    def log_message(self, *_args) -> None:
        return None

    def do_GET(self) -> None:  # noqa: N802
        self.hits.append(self.path)
        data = self.files.get(self.path)
        if data is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FLOOFY_NO_ADAPTERS", "1")
    monkeypatch.delenv("KIROCREW_HOME", raising=False)
    monkeypatch.setenv("KIRO_HOME", str(tmp_path / "kiro"))  # the agent seam lands in KIRO_HOME; never the live ~/.kiro
    payload = tmp_path / "payload"
    fake_payload(payload, "0.7.0", build="0.7.0.5")
    home = tmp_path / "home"
    data = DataHome.for_host_home(home).ensure()
    write_consent(data.consent, by="tests", how="test")
    _Registry.files = {}
    _Registry.hits = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Registry)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    class Env:
        paths = data
        port = server.server_address[1]
        base = f"http://127.0.0.1:{server.server_address[1]}/reg/"
        work = tmp_path / "work"

        def run(self, *args: str, **kw):
            return run(["--home", str(home), "--root", str(payload), *args], non_interactive=True, actor="test", **kw)

        def publish(self, index: dict, *, signed: bool = False, compat: dict | None = None, at: str = "/reg/") -> None:
            _Registry.files[f"{at}index.json"] = json.dumps(index).encode("utf-8")
            if signed:
                _Registry.files[f"{at}index.json.sig"] = b"fake-signature"
            else:
                _Registry.files.pop(f"{at}index.json.sig", None)
            if compat is not None:
                _Registry.files[f"{at}compat.json"] = json.dumps(compat).encode("utf-8")

    try:
        yield Env()
    finally:
        server.shutdown()
        server.server_close()


def _zip(mod_dir: Path, top: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in mod_dir.rglob("*"):
            if path.is_file():
                archive.write(path, f"{top}/{path.relative_to(mod_dir).as_posix()}")
    return buffer.getvalue()


INDEX = {"schema": 1, "source": "test-registry", "generatedAt": "2026-09-19T00:00:00Z", "mods": [{"id": "reggy", "name": "Reggy", "description": "d", "authors": [], "tags": [], "versions": [{"version": "1.0.0", "kirocrew": ">=0.7.0 <0.9.0", "compat": {}, "files": [{"url": "https://example.invalid/reggy.zip", "sha256": "ab" * 32, "size": 1}]}]}]}


# --- the audit log (Requirement 11.5) ------------------------------------------------------------------


def test_audit_rows_carry_actor_and_consent_reference_for_every_mutation(env):
    mod = write_mod(env.work / "agenty", "agenty", parts=[{"kind": "agent", "side": "gateway", "path": "agents/a.json"}], files={"agents/a.json": "{}"})
    assert env.run("install", str(mod)).exit == 0
    assert env.run("disable", "agenty").exit == 0 and env.run("enable", "agenty").exit == 0
    assert env.run("--yes", "uninstall", "agenty").exit == 0
    rows = read_audit(env.paths.audit)
    ops = [r["op"] for r in rows]
    assert ops == ["agent-install", "install", "disable", "enable", "agent-uninstall", "uninstall"], "the seam handler's row precedes its command's"
    reference = consent_reference(env.paths.consent)
    assert set(reference) == {"warningVersion", "sha256"} and reference["warningVersion"] == 1 and len(reference["sha256"]) == 64
    for row in rows:
        assert set(row) >= {"ts", "op", "actor", "by", "consentRef", "mod", "version", "payload", "result", "detail", "files", "governanceFlags"}
        assert row["actor"] == "test" and row["consentRef"] == reference and row["mod"] == "agenty"
    install_row = next(r for r in rows if r["op"] == "install")
    assert install_row["version"] == "1.0.0" and install_row["payload"] and install_row["result"] == "ok" and install_row["files"]
    listed = env.run("--json", "audit", "--tail", "2")
    assert listed.exit == 0 and [r["op"] for r in listed.json["rows"]] == ["agent-uninstall", "uninstall"] and listed.json["count"] == 2
    text = env.run("audit", "--op", "install")
    assert "install agenty@1.0.0 [test/" in text.stdout and "agent-install" not in text.stdout
    assert env.run("audit", "--tail", "-1").exit == 2
    # any surface can write through the one writer; the actor names it and extra fields are kept verbatim
    log = AuditLog(env.paths.data_home, actor="ui")
    row = log.record("custom-op", mod="x", extra="y")
    assert row["actor"] == "ui" and row["extra"] == "y" and read_audit(env.paths.audit)[-1] == row
    env.paths.consent.unlink()
    assert AuditLog(env.paths.data_home).record("orphan")["consentRef"] is None, "no consent record -> null reference, never a crash"


def test_ui_route_rows_are_attributed_to_the_ui_actor(env):
    mod = write_mod(env.work / "agenty", "agenty", parts=[{"kind": "agent", "side": "gateway", "path": "agents/a.json"}], files={"agents/a.json": "{}"})
    assert env.run("install", str(mod)).exit == 0
    result = run(["--home", str(env.paths.host_home), "disable", "agenty"], non_interactive=True, actor="ui", in_gateway=True)
    assert result.exit == 0
    row = read_audit(env.paths.audit)[-1]
    assert row["op"] == "disable" and row["actor"] == "ui" and row["consentRef"] == consent_reference(env.paths.consent)


# --- registry trust controls (Requirement 8.3, 8.4, 11.3, 11.6) ----------------------------------------


def test_registry_add_records_trust_and_refuses_an_unsigned_index_by_default(env):
    env.publish(INDEX, signed=False)
    added = env.run("--json", "registry", "add", env.base, "--name", "test")
    assert added.exit == 1, "unsigned + not allowed: the refresh refuses the index (exit 1) but the source is recorded"
    store = SourceStore.load(env.paths)
    assert [s.url for s in store.sources] == [env.base] and store.sources[0].trust == "index" and store.sources[0].allow_unsigned is False
    document = json.loads(env.paths.registries.read_text(encoding="utf-8"))
    assert document["schema"] == 1 and set(document["sources"][0]) == {"url", "name", "trust", "allowUnsigned", "keyId", "addedAt"} and document["sources"][0]["allowUnsigned"] is False
    assert "refused by default" in added.stderr and added.json["refused"] == [source_key(env.base)]
    meta = json.loads((env.paths.cache_sources / source_key(env.base) / "meta.json").read_text(encoding="utf-8"))
    assert meta["usable"] is False and meta["signature"]["status"] == "unsigned" and meta["label"] == "test"
    assert IndexCache.load(env.paths).mods == [], "the merged cache holds nothing from a refused source"
    assert env.run("--json", "search", "reggy").json["mods"] == []
    rows = read_audit(env.paths.audit)
    ops = [r["op"] for r in rows]
    assert "registry-add" in ops and "registry-trust-loosened" not in ops and ops[-1] == "registry-refresh"
    assert rows[-1]["result"] == "refused" and rows[-1]["sources"] == [{"label": "test", "url": env.base, "status": "unsigned", "usable": False, "allowUnsigned": False}], "the refresh row records the decision"
    listed = env.run("registry", "list")
    assert "refused: index not verified (unsigned)" in listed.stdout
    # a present-but-bogus signature is `invalid` and refused the same way
    env.publish(INDEX, signed=True)
    again = env.run("--json", "registry", "refresh")
    assert again.exit == 1 and again.json["sources"][0]["signature"]["status"] == "invalid"
    assert "unreadable signature" in again.json["sources"][0]["signature"]["detail"]
    assert env.run("registry", "refresh", "nope").exit == 1


def test_allow_unsigned_is_the_users_explicit_audited_loosening(env):
    env.publish(INDEX, signed=False, compat={"schema": 1, "rows": [{"edition": "internal", "channel": "beta", "hostVersion": "0.7.0.5", "framework": {"loader": "ok"}, "mods": {"reggy@1.0.0": "tested"}}]})
    loosened = env.run("--json", "registry", "add", env.base, "--allow-unsigned", "--trust", "owner", "--name", "test")
    assert loosened.exit == 0, loosened.stderr
    assert "TRUST LOOSENED" in loosened.stderr and loosened.json["loosened"] is True
    rows = read_audit(env.paths.audit)
    loosen = next(r for r in rows if r["op"] == "registry-trust-loosened")
    assert "allowUnsigned=true" in loosen["detail"] and loosen["governanceFlags"] == ["UnsignedIndexAccepted"] and loosen["consentRef"]
    assert rows[-1]["op"] == "registry-refresh" and rows[-1]["result"] == "ok" and rows[-1]["sources"][0]["usable"] is True and rows[-1]["sources"][0]["allowUnsigned"] is True
    assert loosened.json["usable"] == [source_key(env.base)] and loosened.json["mergedMods"] == 1 and loosened.json["mergedCompatRows"] == 1
    cache = IndexCache.load(env.paths)
    assert [m.key for m in cache.mods] == ["reggy"] and cache.mods[0].source == "test", "the configured name is the source label, not the index's own claim"
    found = env.run("--json", "search", "reggy")
    assert found.json["mods"][0]["worksHere"] == "1.0.0" and found.json["mods"][0]["verdict"] is None, "the index's own compat cell is empty; the matrix row lives in compat.json"
    doctor = env.run("--json", "doctor")
    assert doctor.json["compat"]["row"]["mods"] == {"reggy@1.0.0": "tested"}, "doctor shows the compat verdict for this host version"
    # adding the same source again without --allow-unsigned tightens it back (not another loosening) — even without a refetch
    tightened = env.run("--json", "registry", "add", env.base, "--no-refresh")
    assert tightened.exit == 0 and tightened.json["loosened"] is False and SourceStore.load(env.paths).sources[0].allow_unsigned is False
    assert tightened.json["mergedMods"] == 0 and IndexCache.load(env.paths).mods == [], "the merged cache follows the current trust settings"
    assert [r["op"] for r in read_audit(env.paths.audit)].count("registry-trust-loosened") == 1
    # loosening again is a second audited loosening
    assert env.run("--json", "registry", "add", env.base, "--allow-unsigned", "--no-refresh").json["mergedMods"] == 1
    assert [r["op"] for r in read_audit(env.paths.audit)].count("registry-trust-loosened") == 2
    removed = env.run("--json", "registry", "remove", "test")
    assert removed.exit == 0 and SourceStore.load(env.paths).sources == [] and IndexCache.load(env.paths).mods == []
    assert read_audit(env.paths.audit)[-1]["op"] == "registry-remove"


def test_verified_signature_makes_the_source_usable_without_loosening(env, monkeypatch: pytest.MonkeyPatch):
    env.publish(INDEX, signed=True)
    seen: list[tuple[bytes, bytes | None, str | None]] = []

    def verifier(index: bytes, signature: bytes | None, source: Source) -> VerifyResult:
        seen.append((index, signature, source.key_id))
        return VerifyResult("verified", "test verifier", "key-1")

    monkeypatch.setattr(registry_sources, "VERIFIER", verifier)
    added = env.run("--json", "registry", "add", env.base, "--key-id", "key-1")
    assert added.exit == 0 and added.json["usable"] and added.json["sources"][0]["signature"] == {"status": "verified", "detail": "test verifier", "keyId": "key-1"}
    assert seen == [(json.dumps(INDEX).encode("utf-8"), b"fake-signature", "key-1")], "the hook gets the raw index bytes, the detached signature and the source"
    assert [m.id for m in IndexCache.load(env.paths).mods] == ["reggy"]
    assert not any(r["op"] == "registry-trust-loosened" for r in read_audit(env.paths.audit))
    assert "TRUST LOOSENED" not in added.stderr


def test_colliding_ids_are_namespaced_by_the_configured_source_label(env):
    other = {**INDEX, "source": "test-registry", "mods": [{**INDEX["mods"][0], "versions": [{**INDEX["mods"][0]["versions"][0], "version": "2.0.0", "files": [{"url": "https://example.invalid/reggy2.zip", "sha256": "cd" * 32, "size": 1}]}]}, {"id": "solo", "name": "Solo", "versions": []}]}
    env.publish(INDEX, signed=False, at="/reg/")
    env.publish(other, signed=False, at="/reg2/")
    assert env.run("registry", "add", env.base, "--allow-unsigned", "--name", "alpha").exit == 0
    assert env.run("registry", "add", f"http://127.0.0.1:{env.port}/reg2/", "--allow-unsigned", "--name", "beta").exit == 0
    cache = IndexCache.load(env.paths)
    assert sorted(m.key for m in cache.mods) == ["alpha/reggy", "beta/reggy", "solo"], "both indexes claim the same `source`; the user's labels disambiguate"
    assert cache.find("reggy") is None and cache.find("alpha/reggy").versions[0].version == "1.0.0" and cache.find("beta/reggy").versions[0].version == "2.0.0"
    assert any("namespaced as alpha/reggy and beta/reggy" in note for note in cache.notes)
    found = env.run("--json", "search", "reggy")
    assert sorted(m["key"] for m in found.json["mods"]) == ["alpha/reggy", "beta/reggy"]
    assert env.run("--json", "which", "cd" * 32).json["hits"][0]["mod"] == "beta/reggy"
    # the same source listing an id twice keeps the first, no namespacing
    dup = IndexCache.from_documents([("one", {"mods": [{"id": "x", "versions": []}, {"id": "x", "versions": []}]})], prefer_label=True)
    assert [m.key for m in dup.mods] == ["x"]
    with pytest.raises(Exception, match="already named"):
        SourceStore.load(env.paths).add(Source("https://elsewhere.example/", name="alpha"))


def test_which_resolves_hashes_from_cache_and_installed_mods(env):
    env.publish(INDEX, signed=False)
    assert env.run("registry", "add", env.base, "--allow-unsigned", "--name", "test").exit == 0
    hit = env.run("--json", "which", "AB" * 32)
    assert hit.exit == 0 and hit.json["hits"][0] == {"where": "registry", "mod": "reggy", "version": "1.0.0", "file": "https://example.invalid/reggy.zip", "source": "test"}
    mod = write_mod(env.work / "local", "local", parts=[{"kind": "agent", "side": "gateway", "path": "agents/a.json"}], files={"agents/a.json": '{"n": 1}'})
    assert env.run("install", str(mod)).exit == 0
    digest = hashlib.sha256(b'{"n": 1}').hexdigest()
    local = env.run("--json", "which", digest)
    assert local.exit == 0 and local.json["hits"][0] == {"where": "installed", "mod": "local", "version": "1.0.0", "file": "agents/a.json", "source": str(env.paths.mods / "local")}
    assert env.run("which", "0" * 64).exit == 1 and env.run("which", "zz").exit == 2


def test_plaintext_remote_source_is_refused(env):
    refused = env.run("registry", "add", "http://registry.example.com/")
    assert refused.exit == 1 and "https" in refused.stderr and SourceStore.load(env.paths).sources == []
    assert not any(r["op"].startswith("registry") for r in read_audit(env.paths.audit)), "nothing was recorded, so nothing is audited"
    assert env.run("registry", "add", "ftp://registry.example.com/").exit == 1


def test_update_writes_a_summary_row_next_to_each_install_row(env):
    mod = write_mod(env.work / "reggy", "reggy", parts=[{"kind": "agent", "side": "gateway", "path": "agents/r.json"}], files={"agents/r.json": "{}"})
    archive = _zip(mod, "reggy")
    _Registry.files["/files/reggy-1.0.0.zip"] = archive
    newer = write_mod(env.work / "reggy2", "reggy", parts=[{"kind": "agent", "side": "gateway", "path": "agents/r.json"}], files={"agents/r.json": '{"v": 2}'}, extra={"version": "1.1.0"})
    archive2 = _zip(newer, "reggy")
    _Registry.files["/files/reggy-1.1.0.zip"] = archive2
    files_base = f"http://127.0.0.1:{env.port}/files/"
    index = {**INDEX, "mods": [{**INDEX["mods"][0], "versions": [
        {"version": "1.0.0", "kirocrew": ">=0.7.0 <0.9.0", "compat": {"0.7.0.5": "tested"}, "files": [{"url": files_base + "reggy-1.0.0.zip", "sha256": hashlib.sha256(archive).hexdigest(), "size": len(archive)}]},
        {"version": "1.1.0", "kirocrew": ">=0.7.0 <0.9.0", "compat": {"0.7.0.5": "tested"}, "files": [{"url": files_base + "reggy-1.1.0.zip", "sha256": hashlib.sha256(archive2).hexdigest(), "size": len(archive2)}]},
    ]}]}
    env.publish(index, signed=False)
    assert env.run("registry", "add", env.base, "--allow-unsigned", "--name", "test").exit == 0
    assert env.run("--yes", "install", "reggy@1.0.0").exit == 0
    updated = env.run("--json", "--yes", "update", "--all")
    assert updated.exit == 0, updated.stderr
    assert [a["version"] for a in updated.json["applied"]] == ["1.1.0"]
    rows = read_audit(env.paths.audit)
    assert [r["op"] for r in rows[-3:]] == ["agent-install", "install", "update"]
    assert rows[-1]["result"] == "ok" and rows[-1]["mods"] == [{"id": "reggy", "version": "1.1.0", "ok": True}] and rows[-1]["payload"] and rows[-1]["consentRef"]
    assert rows[-2]["mod"] == "reggy" and rows[-2]["version"] == "1.1.0"


def test_source_store_round_trip_and_keys(tmp_path: Path):
    home = DataHome.for_host_home(tmp_path).ensure()
    store = SourceStore.load(home)
    source, loosened = store.add(Source("https://example.com/reg/", "owner", True, "k", "ex"))
    assert loosened is True and source.base == "https://example.com/reg/" and Source("https://example.com/reg/index.json").base == "https://example.com/reg/"
    assert source.label == "ex" and Source("https://example.com/reg/").label == source_key("https://example.com/reg/")
    store.save()
    reloaded = SourceStore.load(home)
    assert reloaded.sources[0].to_dict()["allowUnsigned"] is True and reloaded.find("ex") is not None and reloaded.find(source.key) is not None
    assert source_key("https://example.com/reg/") == source_key("https://example.com/reg/") and source_key("https://a/") != source_key("https://b/")
    with pytest.raises(Exception, match="trust must be one of"):
        store.add(Source("https://x/", "friend"))
    with pytest.raises(Exception, match="https"):
        store.add(Source("http://x.example/"))
    assert registry_sources.is_usable("verified", Source("https://x/")) and not registry_sources.is_usable("unsigned", Source("https://x/"))
    assert registry_sources.is_usable("invalid", Source("https://x/", allow_unsigned=True)) and not registry_sources.is_usable(None, Source("https://x/", allow_unsigned=True))
