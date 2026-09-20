"""The registry client's grading and download checks (task 7.3; Requirement 8.3, 8.5, 9.2)."""
from __future__ import annotations

import hashlib
import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from floofy_core.compat import CompatCache
from floofy_core.installer import InstallError, download
from floofy_core.registry import IndexCache


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
    # without any verdict the newest in range wins
    plain = _index([{"version": "1.0.0"}, {"version": "1.2.0"}, {"version": "1.1.0"}])
    assert plain.best_version("m", **HOST).version.version == "1.2.0" and plain.best_version("m", **HOST).verdict is None
    # every candidate excluded: the reason names each one
    none = _index([{"version": "2.0.0", "compat": {"0.7.0.5": "broken"}}, {"version": "3.0.0", "kirocrew": ">=0.9.0"}])
    answer = none.best_version("m", **HOST)
    assert answer.version is None and "2.0.0 (broken on 0.7.0.5)" in answer.why and "3.0.0 (declares kirocrew >=0.9.0)" in answer.why
    assert cache.best_version("nope", **HOST).why == "'nope' is not in the registry cache"
    assert answer.to_dict() == {"key": "m", "version": None, "verdict": None, "why": answer.why}


def test_matrix_row_outranks_the_inline_cell_and_falls_back_across_channels():
    cache = _index([{"version": "1.0.0", "compat": {"0.7.0.5": "tested"}}, {"version": "1.1.0", "compat": {"0.7.0.5": "expected"}}])
    # the beta row says 1.1.0 is tested and 1.0.0 broken: the matrix wins over the index's inline cells
    compat = _compat([{"edition": "internal", "channel": "beta", "hostVersion": "0.7.0.5", "mods": {"m@1.1.0": "tested", "m@1.0.0": "broken"}}])
    best = cache.best_version("m", compat=compat, **HOST)
    assert (best.version.version, best.verdict) == ("1.1.0", "tested") and "1.0.0 (broken on 0.7.0.5)" in cache.best_version("m", compat=_compat([{"edition": "internal", "channel": "beta", "hostVersion": "0.7.0.5", "mods": {"m@1.0.0": "broken", "m@1.1.0": "broken"}}]), **HOST).why
    # a build whose channel has no row takes the same edition's row for that version (the channels are the same bytes)
    stable_only = _compat([{"edition": "internal", "channel": "stable", "hostVersion": "0.7.0.5", "mods": {"m@1.1.0": "tested"}}])
    assert cache.best_version("m", compat=stable_only, **HOST).version.version == "1.1.0"
    assert cache.best_version("m", compat=stable_only, **{**HOST, "channel": None}).version.version == "1.1.0"
    # a row for another edition or version does not apply; the inline cells decide again
    other = _compat([{"edition": "external", "channel": "stable", "hostVersion": "0.7.0", "mods": {"m@1.1.0": "tested"}}])
    assert cache.best_version("m", compat=other, **HOST).version.version == "1.0.0"
    # a cell the matrix has for one version only leaves the others to their inline verdicts
    partial = _compat([{"edition": "internal", "channel": "beta", "hostVersion": "0.7.0.5", "mods": {"m@1.0.0": "expected"}}])
    assert cache.best_version("m", compat=partial, **HOST).version.version == "1.1.0", "1.0.0 downgraded to expected by the matrix, 1.1.0 expected inline: newest expected wins"
    # an override in the row is applied by the reader
    overridden = CompatCache.from_dict({"schema": 1, "rows": [{"edition": "internal", "channel": "beta", "hostVersion": "0.7.0.5", "framework": {"loader": "ok"}, "mods": {"m@1.1.0": "tested"}, "overrides": [{"cell": "m@1.1.0", "verdict": "broken", "reason": "r", "by": "b"}]}]})
    assert cache.best_version("m", compat=overridden, **HOST).version.version == "1.0.0"


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
