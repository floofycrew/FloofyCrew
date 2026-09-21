"""Patcher orchestration tests (Requirement 5.4, 5.7, 5.8, 5.10, 6.4, 6.5, 11.4; task 3.5).

Fake payloads under a temp home: revert-then-patch across every payload (current
and dormant), idempotent double apply, a mod dropping out, drift actions, target
policy (engineering-rule refused, governance confirmed per file), the alias-graph
path, manifest-driven and lost-manifest restore, status, gc, the verify protocol
against a stand-in gateway over loopback TCP and a unix socket, and the CLI.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from floofy_core import cli_patch
from floofy_core.deploy import BACKUP_SUFFIX, DeployManifest, DriftClass, is_floofy_added
from floofy_core.gateway import GatewayEndpoint, find_endpoints, port_from_socket_name, probe_version, served_version
from floofy_core.patcher import Patcher, PlannedPatch, VerifyStatus, resolve_target, url_for
from floofy_core.patches import PatchDescriptor
from floofy_core.payloads import make_payload

from floofy_testing import FakeGateway, fake_payload

BOOT = {
    "schema": 1,
    "target": "kiro_crew/static/dist/index.html",
    "appliesTo": ">=0.6.0 <0.9.0",
    "ops": [
        {"op": "insert-before", "fingerprint": '<script type="module" crossorigin src="/assets/main-', "content": '<script id="floofy-boot">1</script>\n    ', "marker": 'id="floofy-boot"'},
        {"op": "append-head", "content": '<style id="floofy-css">:root{}</style>\n  ', "marker": 'id="floofy-css"'},
    ],
}
CHUNK = {
    "schema": 1,
    "target": "kiro_crew/static/dist/assets/chunk-a-AAAA1111.js",
    "cacheBust": True,
    "ops": [{"op": "replace", "fingerprint": "export const a = 1;", "content": "export const a = 41;/*floofy*/", "marker": "/*floofy*/"}],
}
GOVERNANCE = {"schema": 1, "target": "kiro_crew/security_policy.json", "ops": [{"op": "append-head", "content": "x"}]}
ENGINEERING = {"schema": 1, "target": "kiro_crew/dashboard/server.py", "ops": [{"op": "append-head", "content": "x"}]}


def planned(document: dict, mod: str = "m", part: str = "0") -> PlannedPatch:
    return PlannedPatch(mod, part, PatchDescriptor.from_dict(document, source=f"{mod}#{part}"))


def snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def two_payloads(tmp_path: Path):
    """Payload A (current, 0.7.0) and B (dormant, 0.6.0) plus a data home."""
    a = fake_payload(tmp_path / "a", "0.7.0", layout="flat")
    b = fake_payload(tmp_path / "b", "0.6.0", layout="flat")
    for package_dir in (a, b):
        (package_dir / "static" / "dist" / "assets" / "chunk-a-AAAA1111.js.br").write_bytes(b"br")
        (package_dir / "security_policy.json").write_text("<head></head>", encoding="utf-8")
    pa = make_payload(kind="fake", root=tmp_path / "a", package_dir=a, source="t", current=True)
    pb = make_payload(kind="fake", root=tmp_path / "b", package_dir=b, source="t", current=False)
    home = tmp_path / "home" / "floofy"
    return pa, pb, home


def make_patcher(home: Path, payloads, **kw) -> Patcher:
    return Patcher(home, payloads, endpoints=kw.pop("endpoints", []), **kw)


# --- apply -------------------------------------------------------------------------------


def test_apply_every_payload_and_manifest_shape(two_payloads) -> None:
    pa, pb, home = two_payloads
    before = {p.id: snapshot(p.root) for p in (pa, pb)}
    patcher = make_patcher(home, [pa, pb])
    report = patcher.apply([planned(BOOT), planned(CHUNK, part="1")], verify=False)
    assert report.ok and [r.payload for r in report.payloads] == [pa.id, pb.id]
    assert [r.dormant for r in report.payloads] == [False, True]  # no gateway: dormant = not current
    for payload in (pa, pb):
        html = payload.index_html.read_text()
        assert 'id="floofy-boot"' in html and 'id="floofy-css"' in html
        assert (payload.index_html.parent / ("index.html" + BACKUP_SUFFIX)).read_bytes() == before[payload.id]["static/dist/index.html".replace("static", "kiro_crew/static")]
        chunk = payload.assets_dir / "chunk-a-AAAA1111.js"
        assert "/*floofy*/" in chunk.read_text() and (chunk.parent / (chunk.name + BACKUP_SUFFIX)).exists()
        assert not (chunk.parent / (chunk.name + ".br")).exists() and (chunk.parent / (chunk.name + ".br" + BACKUP_SUFFIX)).exists()
        aliases = sorted(p.name for p in payload.assets_dir.iterdir() if is_floofy_added(p))
        assert len(aliases) == 2 and any(a.startswith("main-MMMM0000-") for a in aliases)
        assert "main-MMMM0000.js" not in html and any(a in html for a in aliases)
        manifest = DeployManifest.load(patcher.manifest_path(payload))
        assert manifest.payload == payload.id and manifest.host_version == payload.host_version.text
        assert sorted(Path(f.path).name for f in manifest.files) == ["chunk-a-AAAA1111.js", "index.html"]
        assert [Path(s.path).name for s in manifest.sidelined] == ["chunk-a-AAAA1111.js.br"]
        assert len(manifest.added) == 2 and manifest.files[0].mod == "m"
    audit = [json.loads(line) for line in (home / "audit.jsonl").read_text().splitlines()]
    assert [a["op"] for a in audit] == ["apply", "apply"] and audit[0]["payload"] == pa.id


def test_double_apply_is_byte_identical_and_writes_nothing(two_payloads) -> None:
    pa, _, home = two_payloads
    patcher = make_patcher(home, [pa])
    patches = [planned(BOOT), planned(CHUNK, part="1")]
    patcher.apply(patches, verify=False)
    once = snapshot(pa.root)
    manifest_once = patcher.manifest_path(pa).read_text()
    second = patcher.apply(patches, verify=False).payloads[0]
    assert second.written == [] and second.restored == [] and second.removed == []
    assert snapshot(pa.root) == once
    assert set(second.drift.values()) == {"clean"}
    kept = json.loads(patcher.manifest_path(pa).read_text())
    assert {k: v for k, v in kept.items() if k != "updatedAt"} == {k: v for k, v in json.loads(manifest_once).items() if k != "updatedAt"}


def test_mod_dropping_out_restores_to_vanilla(two_payloads) -> None:
    pa, _, home = two_payloads
    before = snapshot(pa.root)
    patcher = make_patcher(home, [pa])
    patcher.apply([planned(BOOT), planned(CHUNK, part="1")], verify=False)
    assert snapshot(pa.root) != before
    result = patcher.apply([planned(BOOT)], verify=False).payloads[0]  # the chunk patch is gone
    assert result.ok and "chunk-a-AAAA1111.js" in " ".join(result.restored)
    assert not any(is_floofy_added(p) for p in pa.assets_dir.iterdir())
    assert (pa.assets_dir / "chunk-a-AAAA1111.js.br").exists()
    result = patcher.apply([], verify=False).payloads[0]  # everything gone
    assert snapshot(pa.root) == before and not patcher.manifest_path(pa).exists()


def test_apply_reports_skips_and_partial_descriptor(two_payloads) -> None:
    pa, _, home = two_payloads
    bad = {"schema": 1, "target": "kiro_crew/static/dist/index.html", "ops": [{"op": "replace", "fingerprint": "nowhere", "content": "x"}, {"op": "append-head", "content": "<!--ok-->", "marker": "<!--ok-->"}]}
    result = make_patcher(home, [pa]).apply([planned(bad)], verify=False).payloads[0]
    assert result.ok and [s["code"] for s in result.skipped] == ["FingerprintMiss"]
    assert result.applied == ["m#0 -> kiro_crew/static/dist/index.html#ops/1"]
    assert "<!--ok-->" in pa.index_html.read_text()


def test_not_applicable_descriptor_leaves_payload_alone(two_payloads) -> None:
    pa, _, home = two_payloads
    before = snapshot(pa.root)
    gated = dict(BOOT, appliesTo="<0.1.0")
    result = make_patcher(home, [pa]).apply([planned(gated)], verify=False).payloads[0]
    assert result.skipped[0]["code"] == "NotApplicable" and snapshot(pa.root) == before


# --- target policy (5.10, 11.4) ------------------------------------------------------------


def test_engineering_rule_target_is_refused(two_payloads) -> None:
    pa, _, home = two_payloads
    before = snapshot(pa.root)
    result = make_patcher(home, [pa]).apply([planned(ENGINEERING)], verify=False).payloads[0]
    assert not result.ok and "Requirement 5.10" in result.errors[0] and snapshot(pa.root) == before


def test_governance_target_needs_per_file_confirmation(two_payloads) -> None:
    pa, _, home = two_payloads
    before = snapshot(pa.root)
    result = make_patcher(home, [pa]).apply([planned(GOVERNANCE)], verify=False).payloads[0]
    assert result.ok and result.skipped[0]["code"] == "GovernanceUnconfirmed" and snapshot(pa.root) == before
    patcher = make_patcher(home, [pa], confirmed_governance_targets={"kiro_crew/security_policy.json"})
    result = patcher.apply([planned(GOVERNANCE)], verify=False).payloads[0]
    assert result.ok and result.governance_altering == ["kiro_crew/security_policy.json"]
    assert (pa.package_dir / "security_policy.json").read_text() == "<head>x</head>"
    audit = [json.loads(line) for line in (home / "audit.jsonl").read_text().splitlines()]
    assert audit[-1]["governanceFlags"] == ["kiro_crew/security_policy.json"]


# --- drift actions (5.3) -------------------------------------------------------------------


def test_drift_actions_during_apply(two_payloads) -> None:
    pa, _, home = two_payloads
    patcher = make_patcher(home, [pa])
    patches = [planned(BOOT), planned(CHUNK, part="1")]
    patcher.apply(patches, verify=False)
    chunk = pa.assets_dir / "chunk-a-AAAA1111.js"
    # user-edited: hand edit the patched shell → skipped, left untouched, still tracked
    pa.index_html.write_text(pa.index_html.read_text() + "<!--hand-->", encoding="utf-8")
    # host-updated: the host re-laid the chunk with new original bytes
    (chunk.parent / (chunk.name + BACKUP_SUFFIX)).unlink()
    chunk.write_text("export const a = 1;\n// new host build\n", encoding="utf-8")
    result = patcher.apply(patches, verify=False).payloads[0]
    assert result.drift[str(pa.index_html)] == DriftClass.USER_EDITED.value
    assert any(s["code"] == "UserEdited" for s in result.skipped)
    assert pa.index_html.read_text().endswith("<!--hand-->")
    assert result.drift[str(chunk)] == DriftClass.HOST_UPDATED.value
    assert "/*floofy*/" in chunk.read_text() and "// new host build" in chunk.read_text()
    assert (chunk.parent / (chunk.name + BACKUP_SUFFIX)).read_text() == "export const a = 1;\n// new host build\n"
    manifest = DeployManifest.load(patcher.manifest_path(pa))
    assert {Path(f.path).name for f in manifest.files} == {"chunk-a-AAAA1111.js", "index.html"}
    # mod-deleted: the patched chunk vanished → re-added from the backup
    chunk.unlink()
    result = patcher.apply(patches, verify=False).payloads[0]
    assert result.drift[str(chunk)] == DriftClass.MOD_DELETED.value and chunk.is_file() and "/*floofy*/" in chunk.read_text()


# --- restore (5.8) -------------------------------------------------------------------------


def test_restore_manifest_driven_and_lost_manifest_sweep(two_payloads) -> None:
    pa, pb, home = two_payloads
    before = {p.id: snapshot(p.root) for p in (pa, pb)}
    patcher = make_patcher(home, [pa, pb])
    patcher.apply([planned(BOOT), planned(CHUNK, part="1")], verify=False)
    report = patcher.restore([pa.id])
    assert snapshot(pa.root) == before[pa.id] and not patcher.manifest_path(pa).exists()
    assert report.payloads[pa.id]["restored"] and snapshot(pb.root) != before[pb.id]
    patcher.manifest_path(pb).unlink()  # the manifest is lost
    patcher.restore()
    assert snapshot(pb.root) == before[pb.id]
    assert patcher.restore().payloads[pb.id] == {"restored": [], "removed": []}


# --- status / gc (6.4, 6.5) ----------------------------------------------------------------


def test_status_and_gc(two_payloads, tmp_path: Path) -> None:
    pa, pb, home = two_payloads
    patcher = make_patcher(home, [pa, pb])
    patcher.apply([planned(BOOT)], verify=False)
    status = patcher.status()
    assert status.gateway is None and [s.dormant for s in status.payloads] == [False, True]
    assert status.payloads[0].patched == 1 and status.payloads[0].backups_present == 1 and status.payloads[0].mods == ["m"]
    assert status.payloads[0].drift["clean"] == 1
    # a manifest for a payload that vanished
    ghost = DeployManifest("fake:ghost", "0.1.0", "external", str(tmp_path / "gone"))
    ghost.record_patch("/x", "a", "b", "m", "0")
    ghost.save(DeployManifest.path_for(home, "fake:ghost"))
    gc = patcher.gc()
    assert [Path(p).name for p in gc.removed] == ["fake_ghost.json"] and len(gc.kept) == 2


# --- verify (5.7) --------------------------------------------------------------------------


def test_verify_over_tcp_and_unix_socket(two_payloads, tmp_path: Path) -> None:
    pa, pb, home = two_payloads
    with FakeGateway(pa.dist_dir, "0.7.0") as tcp:
        endpoint = GatewayEndpoint(port=tcp.port)
        assert served_version(endpoint) == "0.7.0" and endpoint.reachable()
        probe = probe_version(endpoint)
        assert probe.version == "0.7.0" and probe.via == "loopback-tcp" and probe.reachable
        assert tcp.probes("tcp")[0]["host"] == f"127.0.0.1:{tcp.port}", "a TCP probe sends the served Host with its port, like a browser"
        patcher = Patcher(home, [pa, pb], endpoints=[endpoint])
        report = patcher.apply([planned(BOOT), planned(CHUNK, part="1")])
        a, b = report.payloads
        assert not a.dormant and b.dormant  # the gateway serves 0.7.0
        assert a.verify is not None and a.verify.status == VerifyStatus.VERIFIED and a.verify.served_version == "0.7.0"
        assert {c["kind"] for c in a.verify.checks} == {"patched", "added"} and all(c["ok"] for c in a.verify.checks)
        assert b.verify is None  # dormant payloads are not verified during apply
        assert patcher.verify(pb, endpoint=endpoint).status == VerifyStatus.DORMANT
        # tamper with a served file → failed
        pa.index_html.write_text("vanilla again", encoding="utf-8")
        failed = patcher.verify(pa, endpoint=endpoint)
        assert failed.status == VerifyStatus.FAILED and "differ" in failed.detail
    host_home = tmp_path / "hosthome"
    with FakeGateway(pa.dist_dir, "0.7.0", socket_dir=host_home) as gw:
        # the real host names the socket after the loopback port it also listens on (dashboard/urls.py)
        assert gw.socket_path == host_home / f"dashboard-{gw.port}.sock" and gw.port > 0
        endpoints = find_endpoints(host_home)
        assert [e.port for e in endpoints] == [gw.port] and endpoints[0].socket_path == gw.socket_path
        # over the socket alone the host answers the bare liveness bit: no version (task 10.8)
        bare = endpoints[0].get("/api/health").json()
        assert bare == {"ok": True}, bare
        # the probe learns the version only through the loopback retry with Host: 127.0.0.1:<port>
        probe = probe_version(endpoints[0])
        assert probe.version == "0.7.0" and probe.via == "loopback-tcp" and probe.reachable
        assert [p["transport"] for p in gw.probes()][-2:] == ["unix", "tcp"], "socket first, then the loopback retry"
        retry = gw.probes("tcp")[-1]
        assert retry["host"] == f"127.0.0.1:{gw.port}" and retry["identity"] is True and not retry["forwarded"]
        patcher = Patcher(home, [pa], host_home=host_home)
        patcher.restore()  # undo the tamper above (the backup goes back over it)
        patcher.apply([planned(BOOT), planned(CHUNK, part="1")], verify=False)
        report = patcher.verify(pa)
        assert report.status == VerifyStatus.VERIFIED and report.endpoint == str(gw.socket_path) and report.served_version == "0.7.0"
        assert all(e["transport"] == "unix" for e in gw.log if e["path"] != "/api/health"), "every other call stays on the socket"
        assert patcher.status().served_version == "0.7.0"
        assert not patcher.status().payloads[0].dormant
    assert Patcher(home, [pa], endpoints=[]).verify(pa).status == VerifyStatus.NO_GATEWAY
    Patcher(home, [pa], endpoints=[]).restore()
    assert Patcher(home, [pa], endpoints=[]).verify(pa).status == VerifyStatus.NOTHING


def test_bare_socket_answer_without_a_derivable_port_yields_no_version(two_payloads, tmp_path: Path) -> None:
    """A socket not named ``dashboard-<port>.sock`` (or ``dashboard-0.sock`` in --test-mode) has nothing to retry on: reachable, version unknown."""
    pa, _pb, home = two_payloads
    host_home = tmp_path / "hosthome"
    assert port_from_socket_name("dashboard-5476.sock") == 5476
    assert port_from_socket_name("dashboard-0.sock") is None and port_from_socket_name("gateway.sock") is None and port_from_socket_name(None) is None
    with FakeGateway(pa.dist_dir, "0.7.0", socket_path=host_home / "dashboard-0.sock") as gw:
        endpoints = find_endpoints(host_home)
        assert [e.port for e in endpoints] == [0]
        probe = probe_version(endpoints[0])
        assert probe.version is None and probe.via is None and probe.reachable and "no loopback port" in probe.detail
        assert served_version(endpoints[0]) is None
        assert gw.probes("tcp") == [], "no port to retry on: the TCP listener was never asked"
        # the gateway still counts as running: the verdict falls back to `current`
        patcher = Patcher(home, [pa], host_home=host_home)
        status = patcher.status()
        assert status.gateway == str(gw.socket_path) and status.served_version is None and status.payloads[0].dormant is (not pa.current)
    with FakeGateway(pa.dist_dir, "0.7.0", socket_path=host_home / "gateway.sock") as gw:
        odd = GatewayEndpoint(socket_path=gw.socket_path)
        assert find_endpoints(host_home) == [], "an unrecognised name is not a dashboard socket"
        assert probe_version(odd).reachable and served_version(odd) is None and gw.probes("tcp") == []
    # a TCP probe carrying a forwarding header, or a foreign Host, gets the bare payload too — no version either
    with FakeGateway(pa.dist_dir, "0.7.0") as gw:
        endpoint = GatewayEndpoint(port=gw.port)
        assert endpoint.get("/api/health", headers={"X-Forwarded-For": "10.0.0.9"}).json() == {"ok": True}
        assert endpoint.get("/api/health", headers={"Forwarded": "for=10.0.0.9"}).json() == {"ok": True}
        assert endpoint.get("/api/health", headers={"Host": f"evil.example:{gw.port}"}).json() == {"ok": True}
        assert endpoint.get("/api/health", headers={"Host": "localhost"}).json()["version"] == "0.7.0", "check_host compares the hostname only"
        assert probe_version(endpoint).version == "0.7.0"


def test_verify_without_a_served_version_distinguishes_404_everywhere_from_mismatches(two_payloads, tmp_path: Path) -> None:
    pa, pb, home = two_payloads
    patcher = make_patcher(home, [pa, pb])
    patcher.apply([planned(CHUNK)], verify=False)
    # a gateway serving a vanilla dist of another build and withholding its version (``""`` = the fake never
    # discloses identity, like a host behind a forwarding proxy): A's aliases are 404, A's repointed shell differs
    other = fake_payload(tmp_path / "other", "0.5.0")
    (other / "static" / "dist" / "assets" / "chunk-a-AAAA1111.js").write_text("export const a = 2;\n", encoding="utf-8")
    with FakeGateway(other / "static" / "dist", "") as gw:
        endpoint = GatewayEndpoint(port=gw.port)
        assert probe_version(endpoint).reachable and served_version(endpoint) is None
        report = Patcher(home, [pa], endpoints=[endpoint]).verify(pa)
        assert report.status == VerifyStatus.FAILED and report.served_version is None
        assert all(c["status"] == 404 for c in report.checks if c["kind"] == "added") and any(c["kind"] == "patched" and c["status"] == 200 and not c["match"] for c in report.checks)
        # a manifest of added files only (no shell): 404 for every one of them is the dormant verdict
        manifest = DeployManifest(pa.id, pa.host_version.text, pa.edition, str(pa.root))
        manifest.record_added(str(pa.assets_dir / "ghost-00000000-floofy.js"), "0" * 64, "m")
        manifest.save(DeployManifest.path_for(home, pa.id))
        report = Patcher(home, [pa], endpoints=[endpoint]).verify(pa)
        assert report.status == VerifyStatus.DORMANT and "404" in report.detail


def test_helpers(two_payloads) -> None:
    pa, _, _ = two_payloads
    assert resolve_target(pa, "kiro_crew/static/dist/index.html") == pa.index_html.resolve()
    assert url_for(pa, pa.index_html) == "/" and url_for(pa, pa.assets_dir / "x.js") == "/assets/x.js"
    assert url_for(pa, pa.package_dir / "security_policy.json") is None


# --- CLI ---------------------------------------------------------------------------------


def test_cli_round_trip(two_payloads, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pa, pb, home = two_payloads
    descriptor = tmp_path / "boot.json"
    descriptor.write_text(json.dumps(BOOT), encoding="utf-8")
    common = ["--data-home", str(home), "--host-home", str(tmp_path / "nohost"), "--no-adapters", "--root", str(pa.root), "--root", str(pb.root)]
    assert cli_patch.main([*common, "apply", "--patch", str(descriptor), "--mod", "boot", "--no-verify"]) == 0
    out = capsys.readouterr().out
    assert "OK" in out and "written 1" in out
    assert cli_patch.main([*common, "--json", "status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert len(status["payloads"]) == 2 and status["payloads"][0]["patched"] == 1 and status["payloads"][0]["mods"] == ["boot"]
    assert cli_patch.main([*common, "verify"]) == 0
    assert "no-gateway" in capsys.readouterr().out
    assert cli_patch.main([*common, "--payload", "nope", "status"]) == 2
    capsys.readouterr()
    assert cli_patch.main([*common, "gc"]) == 0
    assert cli_patch.main([*common, "restore"]) == 0
    assert "restored 1" in capsys.readouterr().out
    assert 'id="floofy-boot"' not in pa.index_html.read_text()
    assert cli_patch.main(["--data-home", str(home), "--no-adapters", "--root", str(tmp_path / "empty"), "status"]) == 1
    with pytest.raises(SystemExit):
        cli_patch.main([*common, "apply", "--patch", str(tmp_path / "missing.json")])
